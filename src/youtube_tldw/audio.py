"""Audio rendering: extract a recut video's audio to mp3, or synthesize the text
TL;DW to natural speech with Piper (local neural TTS, no API keys).
"""

from __future__ import annotations

import importlib.util
import re
import sys
import threading
import time
from pathlib import Path

from . import TldrError
from .proc import run, stream_filter

_voice_cache = {}            # model path -> loaded PiperVoice (load is ~1-2s)
_voice_lock = threading.Lock()
_SENT = re.compile(r"[.!?]+(?:\s|$)")


def _load_voice(model_path: Path):
    key = str(model_path)
    with _voice_lock:
        voice = _voice_cache.get(key)
        if voice is None:
            from piper import PiperVoice  # lazy: avoid onnxruntime import until needed
            voice = PiperVoice.load(key)
            _voice_cache[key] = voice
        return voice

# Curated natural Piper voices (all verified in piper's voices.json), downloaded on
# first use. id -> (model, label). The model is a server-side constant — the caller's
# voice string is only ever an allowlist key, never interpolated into a path/argv.
VOICES = {
    "amy":      ("en_US-amy-medium",                   "Amy — female (US)"),
    "lessac":   ("en_US-lessac-medium",                "Lessac — female (US)"),
    "kristin":  ("en_US-kristin-medium",               "Kristin — female (US)"),
    "ljspeech": ("en_US-ljspeech-high",                "LJSpeech — female (US)"),
    "ryan":     ("en_US-ryan-high",                    "Ryan — male (US)"),
    "joe":      ("en_US-joe-medium",                   "Joe — male (US)"),
    "john":     ("en_US-john-medium",                  "John — male (US)"),
    "norman":   ("en_US-norman-medium",                "Norman — male (US)"),
    "cori":     ("en_GB-cori-high",                    "Cori — female (UK)"),
    "jenny":    ("en_GB-jenny_dioco-medium",           "Jenny — female (UK)"),
    "alba":     ("en_GB-alba-medium",                  "Alba — female (UK, Scottish)"),
    "alan":     ("en_GB-alan-medium",                  "Alan — male (UK)"),
    "northern": ("en_GB-northern_english_male-medium", "Northern — male (UK)"),
}
DEFAULT_VOICE = "amy"
VOICE_ALIASES = {"female": "amy", "male": "ryan"}  # CLI back-compat
VOICE_DIR = Path.home() / ".cache" / "youtube-tldw" / "voices"


def resolve_voice(voice_id: str) -> str:
    """Allowlist a voice id (or female/male alias) to its Piper model name."""
    vid = VOICE_ALIASES.get(voice_id, voice_id)
    entry = VOICES.get(vid)
    if entry is None:
        raise TldrError(f"Unknown voice: {voice_id!r}")
    return entry[0]


def voice_list() -> list[dict]:
    return [{"id": vid, "label": label} for vid, (_m, label) in VOICES.items()]

_MD = re.compile(r"[*_`#>]+")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")

# Safety net for unambiguous abbreviations/symbols TTS mangles, in case one slips
# past the prompt. Context-ambiguous ones (Dr., St., No.) are left to the model.
_SPEAK_FIXES = [
    (re.compile(r"\bWWII\b"), "World War Two"),
    (re.compile(r"\bWWI\b"), "World War One"),
    (re.compile(r"\bvs\.?\b", re.I), "versus"),
    (re.compile(r"\betc\.?\b", re.I), "et cetera"),
    (re.compile(r"\be\.g\.", re.I), "for example"),
    (re.compile(r"\bi\.e\.", re.I), "that is"),
    (re.compile(r"&"), " and "),
    (re.compile(r"%"), " percent"),
]


def _speakify(text: str) -> str:
    for pat, rep in _SPEAK_FIXES:
        text = pat.sub(rep, text)
    return re.sub(r"\s+", " ", text).strip()


def require_piper() -> None:
    if importlib.util.find_spec("piper") is None:
        raise TldrError(
            "Text-mode --render-audio needs Piper TTS. Install it with "
            "`pipx inject youtube-tldw piper-tts` (or `pip install piper-tts`)."
        )


def extract_audio(video: Path, out_mp3: Path, *, timeout: float = 1800) -> None:
    """Pull the audio track out of a video into an mp3."""
    run(
        ["ffmpeg", "-y", "-i", str(video), "-vn",
         "-c:a", "libmp3lame", "-q:a", "2", str(out_mp3)],
        timeout=timeout,
    )
    if not out_mp3.exists():
        raise TldrError("ffmpeg did not produce the audio file.")


def ensure_voice(voice_id: str, *, timeout: float = 180, attempts: int = 3,
                 on_progress=None) -> str:
    """Return the Piper model for `voice_id` (allowlisted), downloading if absent.

    Voice-model downloads from HuggingFace occasionally drop mid-stream (SSL EOF),
    so retry a few times and clean any partial file between tries.
    """
    log = on_progress or (lambda _m: None)
    model = resolve_voice(voice_id)
    onnx = VOICE_DIR / f"{model}.onnx"
    if onnx.exists():
        return model
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    last = ""
    for i in range(attempts):
        try:
            log(f"downloading voice {model} (first use, this can take a bit)…")
            print(f"Downloading voice {model}… (attempt {i + 1}/{attempts})", flush=True)
            run([sys.executable, "-m", "piper.download_voices", model,
                 "--data-dir", str(VOICE_DIR)], timeout=timeout)
            if onnx.exists():
                return model
            last = "download produced no model file"
        except TldrError as exc:
            last = str(exc)
        for partial in VOICE_DIR.glob(f"{model}.onnx*"):  # drop partials before retry
            partial.unlink(missing_ok=True)
        if i + 1 < attempts:
            log("download dropped; retrying…")
            time.sleep(1.5)
    raise TldrError(
        f"Couldn't download the voice “{model}” after {attempts} tries "
        f"(network/SSL error: {last.splitlines()[-1] if last else 'unknown'}). "
        "Check your connection and try again."
    )


# mp3 arrives from ffmpeg one ~200-byte frame at a time; coalesce before handing it
# to the caller (each block becomes a base64 NDJSON line). The first block is small so
# playback can start as early as possible; later ones amortize the framing overhead.
FIRST_BLOCK_BYTES = 4 * 1024
BLOCK_BYTES = 32 * 1024


def _mp3_argv(sample_rate: int) -> list[str]:
    """ffmpeg as a raw-PCM -> mp3 filter. All argv items are server-side constants."""
    return ["ffmpeg", "-loglevel", "error",
            "-f", "s16le", "-ar", str(int(sample_rate)), "-ac", "1", "-i", "pipe:0",
            "-c:a", "libmp3lame", "-q:a", "2", "-f", "mp3", "pipe:1"]


def stream_speech(
    text: str, voice_id: str, *, timeout: float = 120, on_progress=None,
):
    """Yield mp3 blocks as Piper synthesizes `text` — first audio in well under a
    second instead of after the whole script.

    Piper emits one PCM chunk per sentence; those are piped straight into a
    long-lived ffmpeg encoder, so the result is a single continuous mp3 bitstream
    (no per-chunk encoder gaps) that concatenates back into a normal file.
    on_progress is called (message, percent|None) — from the synthesis thread, so a
    caller that also writes from the consuming thread must serialize the two.
    """
    log = on_progress or (lambda _m, _p=None: None)
    require_piper()
    log("preparing voice…", None)
    model = ensure_voice(voice_id, on_progress=lambda m: log(m, None))
    log("loading voice…", None)
    voice = _load_voice(VOICE_DIR / f"{model}.onnx")

    n = max(1, len([s for s in _SENT.split(text) if s.strip()]))  # sentence count
    log("synthesizing speech…", 0)

    def feed(write) -> None:
        for i, chunk in enumerate(voice.synthesize(text)):   # one chunk per sentence
            write(chunk.audio_int16_bytes)
            done = min(1.0, (i + 1) / n)
            log(f"synthesizing speech… {round(done * 100)}%", min(99, round(done * 99)))

    buf = bytearray()
    limit = FIRST_BLOCK_BYTES
    for block in stream_filter(_mp3_argv(voice.config.sample_rate), feed,
                               timeout=timeout):
        buf += block
        if len(buf) >= limit:
            yield bytes(buf)
            buf.clear()
            limit = BLOCK_BYTES
    if buf:
        yield bytes(buf)


def synthesize_speech(
    text: str, out_mp3: Path, voice_id: str, *, timeout: float = 120, on_progress=None,
) -> None:
    """Synthesize `text` to an mp3 file (the batch form of stream_speech)."""
    with out_mp3.open("wb") as f:
        for block in stream_speech(text, voice_id, timeout=timeout,
                                   on_progress=on_progress):
            f.write(block)
    if not out_mp3.exists() or out_mp3.stat().st_size == 0:
        raise TldrError("Failed to produce speech audio.")


def _strip_markdown(text: str) -> str:
    text = _MD_LINK.sub(r"\1", text)
    text = _MD.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def build_spoken_script(title: str, channel: str, key_points, summary: str) -> str:
    """A clean, speakable script from the summary fields (no markdown, no URLs)."""
    parts = [f"T L D W summary of {_strip_markdown(title)}, "
             f"from {_strip_markdown(channel)}."]
    if key_points:
        parts.append("Key points.")
        parts += [f"{_strip_markdown(p)}." for p in key_points]
    parts.append("Summary.")
    parts.append(_strip_markdown(summary))
    return _speakify(" ".join(p for p in parts if p.strip()))

"""Audio rendering: extract a recut video's audio to mp3, or synthesize the text
TL;DW to natural speech with Piper (local neural TTS, no API keys).
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

from . import TldrError
from .proc import run

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


def ensure_voice(voice_id: str, *, timeout: float = 180) -> str:
    """Return the Piper model for `voice_id` (allowlisted), downloading if absent."""
    model = resolve_voice(voice_id)
    if not (VOICE_DIR / f"{model}.onnx").exists():
        VOICE_DIR.mkdir(parents=True, exist_ok=True)
        print(f"Downloading voice {model}… (first use only)")
        run([sys.executable, "-m", "piper.download_voices", model,
             "--data-dir", str(VOICE_DIR)], timeout=timeout)
    return model


def synthesize_speech(
    text: str, out_mp3: Path, voice_id: str, workdir: Path, *, timeout: float = 120
) -> None:
    """Synthesize `text` to speech with Piper, then encode to mp3."""
    require_piper()
    model = ensure_voice(voice_id)
    wav = workdir / "speech.wav"
    run([sys.executable, "-m", "piper", "-m", model, "--data-dir", str(VOICE_DIR),
         "-f", str(wav)], stdin=text, timeout=timeout)
    run(["ffmpeg", "-y", "-i", str(wav), "-c:a", "libmp3lame", "-q:a", "2",
         str(out_mp3)], timeout=timeout)
    if not out_mp3.exists():
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

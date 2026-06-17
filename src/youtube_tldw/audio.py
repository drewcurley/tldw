"""Audio rendering: extract a recut video's audio to mp3, or synthesize the text
TL;DW to natural speech with Piper (local neural TTS, no API keys).
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

from . import TldrError
from .metadata import VideoMeta
from .proc import run
from .summarize import TextResult

# Natural en_US Piper voices, downloaded on first use into a user cache dir.
PIPER_VOICES = {"female": "en_US-amy-medium", "male": "en_US-ryan-medium"}
VOICE_DIR = Path.home() / ".cache" / "youtube-tldw" / "voices"

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


def ensure_voice(gender: str, *, timeout: float = 900) -> str:
    """Return the Piper voice name for `gender`, downloading the model if absent."""
    name = PIPER_VOICES[gender]
    if not (VOICE_DIR / f"{name}.onnx").exists():
        VOICE_DIR.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {gender} voice ({name})… (first use only)")
        run([sys.executable, "-m", "piper.download_voices", name,
             "--data-dir", str(VOICE_DIR)], timeout=timeout)
    return name


def synthesize_speech(
    text: str, out_mp3: Path, gender: str, workdir: Path, *, timeout: float = 900
) -> None:
    """Synthesize `text` to speech with Piper, then encode to mp3."""
    require_piper()
    name = ensure_voice(gender)
    wav = workdir / "speech.wav"
    run([sys.executable, "-m", "piper", "-m", name, "--data-dir", str(VOICE_DIR),
         "-f", str(wav)], stdin=text, timeout=timeout)
    run(["ffmpeg", "-y", "-i", str(wav), "-c:a", "libmp3lame", "-q:a", "2",
         str(out_mp3)], timeout=timeout)
    if not out_mp3.exists():
        raise TldrError("Failed to produce speech audio.")


def _strip_markdown(text: str) -> str:
    text = _MD_LINK.sub(r"\1", text)
    text = _MD.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def build_spoken_script(meta: VideoMeta, result: TextResult) -> str:
    """A clean, speakable script from the summary (no markdown, no URLs)."""
    parts = [f"T L D W summary of {_strip_markdown(meta.title)}, "
             f"from {_strip_markdown(meta.channel)}."]
    if result.key_points:
        parts.append("Key points.")
        parts += [f"{_strip_markdown(p)}." for p in result.key_points]
    parts.append("Summary.")
    parts.append(_strip_markdown(result.summary))
    return _speakify(" ".join(p for p in parts if p.strip()))

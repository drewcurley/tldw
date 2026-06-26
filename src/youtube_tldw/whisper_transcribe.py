"""Whisper-based audio transcription fallback for videos without captions.

Uses faster-whisper (CTranslate2-based) — 4–8× faster than openai-whisper.
Install: pipx inject youtube-tldw faster-whisper
         pip install -e ".[whisper]"   # dev checkout

Models download to ~/.cache/youtube-tldw/whisper/ on first use.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Callable

from . import NoTranscriptError, TldrError
from . import config
from .proc import run
from .transcript import Cue

_CACHE_DIR = Path.home() / ".cache" / "youtube-tldw" / "whisper"

VALID_MODELS = frozenset({
    "tiny", "tiny.en", "base", "base.en", "small", "small.en",
    "medium", "medium.en", "large-v1", "large-v2", "large-v3", "large",
})
_MODEL_SIZES = {
    "tiny": "75MB", "tiny.en": "75MB",
    "base": "145MB", "base.en": "145MB",
    "small": "244MB", "small.en": "244MB",
    "medium": "769MB", "medium.en": "769MB",
    "large-v1": "1.5GB", "large-v2": "1.5GB", "large-v3": "1.6GB", "large": "1.6GB",
}
DEFAULT_MODEL = "small"


def is_available() -> bool:
    """True if faster-whisper is importable."""
    return importlib.util.find_spec("faster_whisper") is not None


def require_whisper() -> None:
    """Raise TldrError with install instructions if faster-whisper is missing."""
    if not is_available():
        raise TldrError(
            "faster-whisper is not installed. To enable audio transcription for "
            "captionless videos:\n"
            "  pipx inject youtube-tldw faster-whisper   # if using pipx\n"
            "  pip install faster-whisper                # if using a venv\n"
            "Then re-run with --whisper-fallback."
        )


def resolve_model() -> str:
    """Return the configured Whisper model size, falling back to 'small'."""
    saved = config.get("whisper_model")
    if saved and saved in VALID_MODELS:
        return saved
    return DEFAULT_MODEL


def download_audio(video_id: str, workdir: Path, *, timeout: float = 600) -> Path:
    """Download the best available audio stream for video_id into workdir."""
    from .metadata import watch_url
    run(
        [
            "yt-dlp", "-f", "bestaudio/best",
            "--no-playlist", "-o", "%(id)s.%(ext)s",
            watch_url(video_id),
        ],
        timeout=timeout,
        cwd=str(workdir),
    )
    candidates = sorted(workdir.glob(f"{video_id}.*"))
    if not candidates:
        raise TldrError("yt-dlp did not produce an audio file.")
    return candidates[0]


def transcribe(
    audio_path: Path,
    model_size: str,
    *,
    on_progress: Callable[[str, float | None], None] | None = None,
) -> list[Cue]:
    """Transcribe audio using faster-whisper. Returns a list[Cue].

    Streams progress via on_progress(message, pct) as segments arrive.
    """
    require_whisper()
    from faster_whisper import WhisperModel  # type: ignore[import]

    log = on_progress or (lambda _m, _p=None: None)
    size_label = _MODEL_SIZES.get(model_size, "?")
    log(
        f"loading Whisper '{model_size}' model ({size_label} — downloads on first use)…",
        None,
    )

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    model = WhisperModel(
        model_size,
        device="cpu",
        compute_type="int8",
        download_root=str(_CACHE_DIR),
    )

    log("transcribing audio — this takes roughly 0.5–2× the video length…", None)
    segments_iter, info = model.transcribe(str(audio_path), beam_size=5)
    duration = max(info.duration or 1.0, 1.0)

    cues: list[Cue] = []
    for seg in segments_iter:
        text = seg.text.strip()
        if not text:
            continue
        cues.append(Cue(
            start_ms=int(seg.start * 1000),
            end_ms=int(seg.end * 1000),
            text=text,
        ))
        pct = round(min(seg.end / duration * 95, 95), 1)
        log(f"transcribing… {seg.end:.0f}s / {duration:.0f}s", pct)

    if not cues:
        raise NoTranscriptError(
            "Whisper produced no text — the audio may be silent or inaudible."
        )
    log(f"transcription complete — {len(cues)} segments", 100)
    return cues

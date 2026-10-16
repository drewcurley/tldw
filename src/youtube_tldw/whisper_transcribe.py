"""Whisper-based audio transcription fallback for videos without captions.

Uses faster-whisper (CTranslate2-based) — 4–8× faster than openai-whisper.
Install: pipx inject youtube-tldw faster-whisper
         pip install -e ".[whisper]"   # dev checkout

Models download to ~/.cache/youtube-tldw/whisper/ on first use.
"""

from __future__ import annotations

import importlib.util
import os
import time
from pathlib import Path
from typing import Callable

from . import NoTranscriptError, TldrError, TldrTimeoutError
from . import config
from .proc import run
from .transcript import Cue, normalize

_CACHE_DIR = Path.home() / ".cache" / "youtube-tldw" / "whisper"

VALID_MODELS = frozenset({
    "tiny", "tiny.en", "base", "base.en", "small", "small.en",
    "medium", "medium.en", "large-v1", "large-v2", "large-v3", "large",
})
# What actually lands in the cache directory. The previous numbers here were the
# openai-whisper weights, not these: `small` was listed as 244MB against 464MB
# measured on disk. These feed the size we quote before a 464MB download.
_MODEL_SIZES = {
    "tiny": "75MB", "tiny.en": "75MB",
    "base": "145MB", "base.en": "145MB",
    "small": "484MB", "small.en": "484MB",
    "medium": "1.5GB", "medium.en": "1.5GB",
    "large-v1": "3.1GB", "large-v2": "3.1GB", "large-v3": "3.1GB", "large": "3.1GB",
}

# How much faster than realtime each model transcribes, used to tell someone how
# long they are about to wait. `small` is measured on an Apple Silicon laptop at
# 5.5x (int8, CPU); the rest are scaled from the relative model sizes and are
# therefore estimates. Being wrong here costs a bad estimate, not a bad transcript.
_REALTIME_FACTOR = {
    "tiny": 16.0, "tiny.en": 16.0,
    "base": 10.0, "base.en": 10.0,
    "small": 5.5, "small.en": 5.5,       # measured
    "medium": 2.0, "medium.en": 2.0,
    "large-v1": 1.0, "large-v2": 1.0, "large-v3": 1.0, "large": 1.0,
}

DEFAULT_MODEL = "small"


def is_available() -> bool:
    """True if faster-whisper is importable."""
    return importlib.util.find_spec("faster_whisper") is not None


# Shown verbatim in the panel when the dependency is missing, so it has to be the
# command someone can actually run rather than a description of one.
INSTALL_HINT = "pipx inject youtube-tldw faster-whisper"


def require_whisper() -> None:
    """Raise TldrError with install instructions if faster-whisper is missing."""
    if not is_available():
        raise TldrError(
            "faster-whisper is not installed. To transcribe captionless videos:\n"
            f"  {INSTALL_HINT}   # if tldw came from pipx\n"
            "  pip install faster-whisper                # if using a venv\n"
            "Then restart `tldw serve`."
        )


def resolve_model() -> str:
    """Return the configured Whisper model size, falling back to 'small'."""
    saved = config.get("whisper_model")
    if saved and saved in VALID_MODELS:
        return saved
    return DEFAULT_MODEL


MAX_AUDIO_BYTES = 300 * 1024 * 1024   # ~5 hours of opus; a muxed video blows past it
MAX_CUES = 50_000
MAX_CUE_CHARS = 1_000
_AUDIO_EXTS = {".m4a", ".webm", ".opus", ".mp3", ".ogg", ".oga", ".wav", ".aac", ".mp4"}


def estimate_seconds(duration_ms: int, model_size: str | None = None) -> int:
    """Roughly how long transcribing a video of this length will take.

    This is what someone is shown before agreeing to wait, so it is derived from a
    measured rate rather than the prose that used to live in this file ("roughly
    0.5-2x the video length", which was wrong by up to 11x).
    """
    factor = _REALTIME_FACTOR.get(model_size or resolve_model(), 5.5)
    return max(1, int((duration_ms / 1000.0) / factor))


def download_audio(video_id: str, workdir: Path, *, timeout: float = 600) -> Path:
    """Download video_id's audio into workdir and return the file.

    Bounded deliberately, because unlike every other yt-dlp call in this project
    this one can now be triggered by an HTTP request:

    - `bestaudio` with no `/best` fallback. `best` resolves to a *muxed* video
      stream when no audio-only format exists, which is gigabytes rather than the
      ~1MB per minute audio costs.
    - `--max-filesize`, so a pathological format can't fill the disk.
    - `!is_live`: canonical_video_id accepts /live/ URLs, and a live stream
      downloads from now until the stream ends -- i.e. until the timeout, writing
      the whole time.
    - `--ignore-config`, so a user's own yt-dlp config can't inject `--exec` or
      `--paths` into a download someone else triggered.

    video_id is always canonical_video_id output; nothing from the request body
    reaches here. That is what makes the glob below safe.
    """
    from .metadata import watch_url
    result = run(
        [
            "yt-dlp", "--ignore-config", "-f", "bestaudio",
            "--no-playlist", "--match-filter", "!is_live",
            "--max-filesize", str(MAX_AUDIO_BYTES),
            "--print", "after_move:filepath", "--no-simulate",
            "-o", "%(id)s.%(ext)s",
            watch_url(video_id),
        ],
        timeout=timeout,
        cwd=str(workdir),
    )
    # yt-dlp reports the file it actually settled on. Globbing and taking the first
    # match would happily pick a leftover `.part` or a `<id>.f251.webm` fragment and
    # hand Whisper a truncated file.
    for line in reversed((result.stdout or "").splitlines()):
        candidate = Path(line.strip())
        if candidate.name and candidate.suffix.lower() in _AUDIO_EXTS:
            resolved = candidate if candidate.is_absolute() else workdir / candidate
            if resolved.is_file():
                return resolved
    pool = [p for p in workdir.glob(f"{video_id}*")
            if p.is_file() and p.suffix.lower() in _AUDIO_EXTS]
    if not pool:
        raise TldrError(
            "yt-dlp produced no audio for this video. It may be live, private, "
            "age-restricted, or longer than the size limit."
        )
    return max(pool, key=lambda p: p.stat().st_size)


def _collapse_repeats(cues: list[Cue]) -> list[Cue]:
    """Drop a cue that merely repeats the one before it.

    Whisper loops: on a music bed or a long silence it will emit the same sentence
    over and over, for minutes. Every caption parser here already collapses rolling
    repeats; without this the loop is cached for two weeks and then summarized.
    """
    out: list[Cue] = []
    for cue in cues:
        if out and out[-1].text.casefold() == cue.text.casefold():
            out[-1].end_ms = max(out[-1].end_ms, cue.end_ms)
            continue
        out.append(cue)
    return out


def transcribe(
    audio_path: Path,
    model_size: str,
    *,
    on_progress: Callable[[str, float | None], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    deadline: float | None = None,
) -> list[Cue]:
    """Transcribe audio using faster-whisper. Returns normalized Cues.

    Streams progress via on_progress(message, pct) as segments arrive, and checks
    should_cancel() and the deadline between segments -- the only place this loop
    can be interrupted, since a single segment is indivisible.
    """
    require_whisper()
    from faster_whisper import WhisperModel  # type: ignore[import]

    log = on_progress or (lambda _m, _p=None: None)
    cancelled = should_cancel or (lambda: False)
    size_label = _MODEL_SIZES.get(model_size, "?")
    log(f"loading Whisper '{model_size}' model "
        f"({size_label} -- downloads on first use)...", None)

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    model = WhisperModel(
        model_size,
        device="cpu",
        # Leaving this at the default takes every core, so two runs oversubscribe
        # the machine and both finish slower than they would in series. This is a
        # laptop that is often doing something else.
        cpu_threads=max(1, (os.cpu_count() or 4) // 2),
        compute_type="int8",
        download_root=str(_CACHE_DIR),
    )
    if cancelled():
        raise TldrError("Transcription cancelled.")

    log("transcribing audio...", None)
    segments_iter, info = model.transcribe(
        str(audio_path),
        beam_size=5,
        # Silence and music are what captionless videos are made of, and both send
        # Whisper into repetition loops. VAD skips them; not carrying previous text
        # across segments stops a loop that starts anyway from feeding itself.
        vad_filter=True,
        condition_on_previous_text=False,
    )
    duration = max(info.duration or 1.0, 1.0)

    cues: list[Cue] = []
    for seg in segments_iter:
        if cancelled():
            raise TldrError("Transcription cancelled.")
        if deadline is not None and time.monotonic() > deadline:
            raise TldrTimeoutError(
                "Transcribing this video took longer than its time budget."
            )
        text = " ".join(seg.text.split())[:MAX_CUE_CHARS]
        if not text:
            continue
        cues.append(Cue(
            start_ms=int(seg.start * 1000),
            end_ms=int(seg.end * 1000),
            text=text,
        ))
        if len(cues) >= MAX_CUES:
            break
        pct = round(min(seg.end / duration * 95, 95), 1)
        log(f"transcribing... {seg.end:.0f}s / {duration:.0f}s", pct)

    cues = _collapse_repeats(cues)
    if not cues:
        raise NoTranscriptError(
            "Whisper produced no text -- the audio may be silent or inaudible."
        )
    # Every other cue producer ends here. Span maths and the numbered cue listing
    # assume sorted, non-overlapping cues; Whisper segments are usually already in
    # order, but "usually" is not the contract the rest of the code is written to.
    cues = normalize(cues)
    log(f"transcription complete -- {len(cues)} segments", 100)
    return cues


def transcribe_video(
    video_id: str,
    workdir: Path,
    *,
    on_progress: Callable[[str, float | None], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    deadline: float | None = None,
    model_size: str | None = None,
) -> list[Cue]:
    """Download this video's audio and transcribe it: the whole job, in one place.

    The CLI and the server both call this, so the sequence exists once. Checking for
    the dependency first matters -- discovering it is missing after downloading
    30MB of audio wastes the download and the wait.
    """
    require_whisper()
    log = on_progress or (lambda _m, _p=None: None)
    model = model_size or resolve_model()
    log(f"downloading audio for transcription ({_MODEL_SIZES.get(model, '?')} "
        f"model: {model})...", None)
    audio_path = download_audio(video_id, workdir)
    if should_cancel and should_cancel():
        raise TldrError("Transcription cancelled.")
    return transcribe(audio_path, model, on_progress=on_progress,
                      should_cancel=should_cancel, deadline=deadline)

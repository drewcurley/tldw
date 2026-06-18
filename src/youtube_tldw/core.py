"""Shared, presentation-free summarization core used by both the CLI and the
HTTP server. Imports no video/audio/CLI code, owns and cleans its own tempdir.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import TranscriptTooLongError
from . import metadata as md
from . import spans, summarize, transcript
from .summarize import TextResult
from .timing import format_clock
from .urls import canonical_video_id

# Span shaping shared with the video recut (no crossfade math here — playback is
# straight seeks, so the max-length cap uses 0 overlap).
MIN_CLIP_MS = 1200
BOUNDARY_PAD_MS = 600


@dataclass
class Summary:
    meta: md.VideoMeta
    result: TextResult
    cue_count: int


def select_segments(
    url: str,
    ratio: float | None = None,
    lang: str = "en",
    *,
    max_length_ms: int | None = None,
    timeout: float = 300.0,
    on_progress=None,
):
    """Pick the key source time-spans for in-player skip playback (no video work).

    Returns (meta, segments) where segments = [{start, end, label}] in SECONDS, the
    same cue-selection the recut uses (sentence-padded), minus intro/polish.
    """
    log = on_progress or (lambda _m, _p=None: None)
    video_id = canonical_video_id(url)            # BadUrlError
    log(f"video {video_id}: fetching metadata…", 6)
    import shutil
    import tempfile
    workdir = Path(tempfile.mkdtemp(prefix="youtube-tldw-seg-"))
    try:
        meta = md.fetch_metadata(video_id)
        lang_key, is_auto = md.choose_track(meta, lang)   # NoTranscriptError
        log(f"“{meta.title}” — using {'auto-captions' if is_auto else 'subtitles'} "
            f"({lang_key})", 14)
        cues = transcript.parse_subtitles(
            md.download_subtitle(video_id, lang_key, is_auto, workdir))  # NoTranscriptError
        log(f"parsed {len(cues)} cues, {sum(len(c.text.split()) for c in cues)} words", 22)
        log("selecting the key moments with Claude…", None)   # indeterminate spin
        sel = summarize.select_video_segments(
            cues, meta.channel, meta.title, ratio, max_length_ms, timeout=timeout)
        chosen = spans.spans_from_cue_ranges(sel.ranges, cues, min_clip_ms=MIN_CLIP_MS)
        chosen = spans.pad_spans(chosen, cues, BOUNDARY_PAD_MS, meta.duration_ms)
        caps = [c for c in (max_length_ms,
                            int(ratio * meta.duration_ms) if ratio else None) if c]
        chosen = spans.enforce_max_length(chosen, min(caps) if caps else None, 0)
        segments = [{"start": round(s.start_ms / 1000, 2),
                     "end": round(s.end_ms / 1000, 2),
                     "label": format_clock(s.display_start_ms)} for s in chosen]
        return meta, segments
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def summarize_url(
    url: str,
    ratio: float | None = None,
    lang: str = "en",
    *,
    timeout: float = 300.0,
    max_chars: int | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> Summary:
    """Fetch a video's transcript and summarize it. Raises the typed TldrError
    subclasses (BadUrl/NoTranscript/TranscriptTooLong/Claude/Timeout).

    `max_chars`: if set, reject transcripts that would trigger map-reduce (the
    browser flow uses this so a click never blocks for minutes).
    `on_progress`: optional callback for human-readable step messages.
    """
    log = on_progress or (lambda _m, _p=None, _c=False: None)
    video_id = canonical_video_id(url)  # BadUrlError
    log(f"video {video_id}: fetching metadata…", 3)
    workdir = Path(tempfile.mkdtemp(prefix="youtube-tldw-core-"))
    try:
        meta = md.fetch_metadata(video_id)
        lang_key, is_auto = md.choose_track(meta, lang)  # NoTranscriptError
        kind = "auto-captions" if is_auto else "subtitles"
        log(f"“{meta.title}” by {meta.channel} ({format_dur(meta.duration_ms)}) "
            f"— using {kind} ({lang_key})", 7)
        content = md.download_subtitle(video_id, lang_key, is_auto, workdir)
        cues = transcript.parse_subtitles(content)  # NoTranscriptError
        words = sum(len(c.text.split()) for c in cues)
        log(f"parsed {len(cues)} cues, {words} words", 13)
        if max_chars is not None:
            chars = sum(len(c.text) + 1 for c in cues)
            if chars > max_chars:
                raise TranscriptTooLongError(
                    "This transcript is too long for the browser flow; "
                    "use the `tldw` CLI for very long videos."
                )
        # The long step owns ~80% of the bar: starts at 15%, and creep=True tells the
        # client to ease forward toward ~96% from here until the result lands.
        log("summarizing with Claude (this can take 30-90s for a long video)…", 15,
            True)
        result = summarize.summarize_text(
            cues, meta.channel, meta.title, ratio, timeout=timeout
        )
        return Summary(meta, result, len(cues))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def format_dur(ms: int) -> str:
    from .timing import format_length
    return format_length(ms)

"""Shared, presentation-free summarization core used by both the CLI and the
HTTP server. Imports no video/audio/CLI code, owns and cleans its own tempdir.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import TranscriptTooLongError
from . import metadata as md
from . import summarize, transcript
from .summarize import TextResult
from .urls import canonical_video_id


@dataclass
class Summary:
    meta: md.VideoMeta
    result: TextResult
    cue_count: int


def summarize_url(
    url: str,
    ratio: float | None = None,
    lang: str = "en",
    *,
    timeout: float = 300.0,
    max_chars: int | None = None,
) -> Summary:
    """Fetch a video's transcript and summarize it. Raises the typed TldrError
    subclasses (BadUrl/NoTranscript/TranscriptTooLong/Claude/Timeout).

    `max_chars`: if set, reject transcripts that would trigger map-reduce (the
    browser flow uses this so a click never blocks for minutes).
    """
    video_id = canonical_video_id(url)  # BadUrlError
    workdir = Path(tempfile.mkdtemp(prefix="youtube-tldw-core-"))
    try:
        meta = md.fetch_metadata(video_id)
        lang_key, is_auto = md.choose_track(meta, lang)  # NoTranscriptError
        content = md.download_subtitle(video_id, lang_key, is_auto, workdir)
        cues = transcript.parse_subtitles(content)  # NoTranscriptError
        if max_chars is not None:
            chars = sum(len(c.text) + 1 for c in cues)
            if chars > max_chars:
                raise TranscriptTooLongError(
                    "This transcript is too long for the browser flow; "
                    "use the `tldw` CLI for very long videos."
                )
        result = summarize.summarize_text(
            cues, meta.channel, meta.title, ratio, timeout=timeout
        )
        return Summary(meta, result, len(cues))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

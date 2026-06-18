"""youtube-tldw: turn a YouTube video into a succinct text or video TL;DW."""

__version__ = "0.1.0"


class TldrError(Exception):
    """User-facing, expected error. cli.py prints the message and exits non-zero."""


# Specific subclasses so the HTTP server can map failures to status codes. The CLI
# still catches the TldrError base, so its behavior is unchanged.
class BadUrlError(TldrError):
    """The input wasn't a valid single YouTube video URL/id. (HTTP 400)"""


class NoTranscriptError(TldrError):
    """The video has no usable subtitles/transcript. (HTTP 422)"""


class TranscriptTooLongError(TldrError):
    """Transcript exceeds the single-pass limit for the browser flow. (HTTP 413)"""


class ClaudeError(TldrError):
    """The `claude` call failed or returned unusable output. (HTTP 502)"""


class TldrTimeoutError(TldrError):
    """A subprocess exceeded its timeout. (HTTP 504)"""

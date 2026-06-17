"""youtube-tldr: turn a YouTube video into a succinct text or video TL;DR."""

__version__ = "0.1.0"


class TldrError(Exception):
    """User-facing, expected error. cli.py prints the message and exits non-zero."""

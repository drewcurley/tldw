"""Time handling. One internal unit everywhere: integer milliseconds.

Convert to ffmpeg's HH:MM:SS.mmm only at command-construction time.
"""

from __future__ import annotations

import re

from . import TldrError

# VTT: HH:MM:SS.mmm or MM:SS.mmm ; SRT: HH:MM:SS,mmm
_CUE_TS = re.compile(
    r"(?:(?P<h>\d+):)?(?P<m>\d{1,2}):(?P<s>\d{2})[.,](?P<ms>\d{1,3})"
)
# CLI duration: 90, 90s, 5m, 1m30s, 1h2m, 1:30, 1:02:03
_DUR_COMPOUND = re.compile(
    r"^\s*(?:(?P<h>\d+)h)?(?:(?P<m>\d+)m)?(?:(?P<s>\d+)s)?\s*$", re.IGNORECASE
)


def parse_cue_ts(text: str) -> int:
    """Parse a VTT/SRT timestamp into milliseconds."""
    match = _CUE_TS.search(text)
    if not match:
        raise TldrError(f"Unparseable timestamp: {text!r}")
    h = int(match.group("h") or 0)
    m = int(match.group("m"))
    s = int(match.group("s"))
    ms = int((match.group("ms") or "0").ljust(3, "0"))
    return ((h * 60 + m) * 60 + s) * 1000 + ms


def parse_duration(text: str) -> int:
    """Parse a user-supplied duration like '90s', '5m', '1m30s', '1:30' -> ms."""
    text = text.strip()
    if not text:
        raise TldrError("Empty duration.")
    if text.isdigit():  # bare seconds
        return int(text) * 1000
    if ":" in text:  # mm:ss or hh:mm:ss
        parts = text.split(":")
        if not all(p.isdigit() for p in parts) or len(parts) > 3:
            raise TldrError(f"Invalid duration: {text!r}")
        total = 0
        for part in parts:
            total = total * 60 + int(part)
        return total * 1000
    match = _DUR_COMPOUND.match(text)
    if not match or not any(match.groups()):
        raise TldrError(f"Invalid duration: {text!r} (try e.g. 90s, 5m, 1m30s).")
    h = int(match.group("h") or 0)
    m = int(match.group("m") or 0)
    s = int(match.group("s") or 0)
    return ((h * 60 + m) * 60 + s) * 1000


def to_ffmpeg_ts(ms: int) -> str:
    """Milliseconds -> 'HH:MM:SS.mmm' for ffmpeg -ss/-to/-t."""
    if ms < 0:
        ms = 0
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{milli:03d}"


def format_length(ms: int) -> str:
    """Milliseconds -> compact human label like '3m42s', '45s', '1h2m3s'."""
    total_s = round(ms / 1000)
    h, rem = divmod(total_s, 3600)
    m, s = divmod(rem, 60)
    out = ""
    if h:
        out += f"{h}h"
    if m or h:
        out += f"{m}m"
    out += f"{s}s"
    return out


def read_time_label(word_count: int, wpm: int = 200) -> str:
    """Estimated reading time label for text mode, e.g. '3m' (min 1m)."""
    minutes = max(1, round(word_count / max(1, wpm)))
    return f"{minutes}m"

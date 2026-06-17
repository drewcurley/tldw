"""URL validation. Only accept canonical YouTube video URLs (no playlists, no
arbitrary yt-dlp extractors, no local paths). Returns the 11-char video id.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from . import TldrError

_ALLOWED_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def canonical_video_id(url: str) -> str:
    """Validate `url` and extract the YouTube video id, or raise TldrError."""
    if not isinstance(url, str) or not url.strip():
        raise TldrError("No URL provided.")
    parsed = urlparse(url.strip())

    if parsed.scheme != "https":
        raise TldrError("URL must start with https://")
    host = (parsed.hostname or "").lower()
    if host not in _ALLOWED_HOSTS:
        raise TldrError(
            f"Unsupported host {host!r}. Only youtube.com / youtu.be URLs are allowed."
        )

    if host == "youtu.be":
        vid = parsed.path.lstrip("/").split("/")[0]
    elif parsed.path == "/watch":
        vid = (parse_qs(parsed.query).get("v") or [""])[0]
    elif parsed.path.startswith(("/shorts/", "/embed/", "/live/", "/v/")):
        vid = parsed.path.split("/")[2]
    else:
        raise TldrError(
            "Could not find a video id in the URL. "
            "Use a normal watch / youtu.be / shorts link (not a playlist)."
        )

    if not _VIDEO_ID.match(vid):
        raise TldrError("That doesn't look like a single YouTube video URL.")
    return vid

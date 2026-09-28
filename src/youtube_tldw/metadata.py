"""yt-dlp interactions: metadata, subtitle track selection/download, video download.
All invoked through proc.run (argv list, shell=False). The video id is validated
upstream to [A-Za-z0-9_-]{11}, so the watch URL is safe to construct.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import urllib.error
import urllib.request
from urllib.parse import urlparse

from . import NoTranscriptError, TldrError
from . import config
from .proc import run

_OUTPUT_TMPL = "%(id)s.%(ext)s"  # static; never built from untrusted input
_MAX_HEIGHT = 720


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


@dataclass
class VideoMeta:
    video_id: str
    title: str
    channel: str
    duration_ms: int
    subtitles: dict          # manual: lang -> list[{ext,url,...}]
    auto_captions: dict      # auto:   lang -> list[...]


# YouTube rate-limits bursts from one address with HTTP 429, and without these a
# single 429 is an immediate hard failure. Back off and retry rather than making the
# user try again by hand — exponential from 2s, capped at 30s.
MAX_CAPTION_BYTES = 8 * 1024 * 1024   # a very long auto-caption track is ~1MB

# Opt-in: hand yt-dlp the browser's own YouTube session. Unauthenticated extraction
# is what YouTube throttles, so this is the standard remedy when even a first
# request of the day gets a 429. Allowlisted because it reaches argv, and never
# request-controlled — it is operator config like the model command.
COOKIE_BROWSERS = ("chrome", "chromium", "brave", "edge", "firefox", "safari",
                   "opera", "vivaldi")


def _cookie_args() -> list:
    choice = (os.environ.get("TLDW_YTDLP_COOKIES")
              or config.get("ytdlp_cookies") or "").strip().lower()
    return ["--cookies-from-browser", choice] if choice in COOKIE_BROWSERS else []
_RETRY = ["--retries", "3", "--extractor-retries", "3",
          "--retry-sleep", "extractor:exp=2:30", "--retry-sleep", "http:exp=2:30"]


def rate_limited(message: str) -> bool:
    """Is this yt-dlp failure YouTube throttling us, rather than a bad video?"""
    text = (message or "").lower()
    return "429" in text or "too many requests" in text


def _translate(exc: TldrError) -> TldrError:
    """Say what a 429 actually means. The raw yt-dlp text reads like a bug in tldw;
    it's YouTube throttling this machine, it passes, and it needs no action but
    waiting."""
    if rate_limited(str(exc)):
        return TldrError(
            "YouTube is rate-limiting this machine (HTTP 429) after too many "
            "requests in a short time. It clears on its own — wait a few minutes "
            "and try again. Videos summarized recently are cached and unaffected."
        )
    return exc


def fetch_metadata(video_id: str, *, timeout: float = 120) -> VideoMeta:
    try:
        res = run(
            ["yt-dlp", "-J", "--no-playlist", "--skip-download", *_RETRY,
             *_cookie_args(), watch_url(video_id)],
            timeout=timeout,
        )
    except TldrError as exc:
        raise _translate(exc) from exc
    try:
        info = json.loads(res.stdout)
    except json.JSONDecodeError as exc:
        raise TldrError("Could not read video metadata from yt-dlp.") from exc
    duration = info.get("duration") or 0
    return VideoMeta(
        video_id=video_id,
        title=info.get("title") or "untitled",
        channel=info.get("channel") or info.get("uploader") or "unknown",
        duration_ms=int(float(duration) * 1000),
        subtitles=info.get("subtitles") or {},
        auto_captions=info.get("automatic_captions") or {},
    )


def choose_track(meta: VideoMeta, lang: str) -> tuple[str, bool]:
    """Pick (lang_key, is_auto) by precedence. Raises if no captions exist.

    manual exact > manual prefix > manual any > auto exact > auto prefix > auto any
    """
    for tracks, is_auto in ((meta.subtitles, False), (meta.auto_captions, True)):
        if not tracks:
            continue
        if lang in tracks:
            return lang, is_auto
        prefix = next((k for k in tracks if k.split("-")[0] == lang), None)
        if prefix:
            return prefix, is_auto
    # No exact/prefix match in either; fall back to any manual, then any auto.
    if meta.subtitles:
        return next(iter(meta.subtitles)), False
    if meta.auto_captions:
        return next(iter(meta.auto_captions)), True
    raise NoTranscriptError(
        "This video has no subtitles or auto-captions, so there's nothing to "
        "summarize. (Try a different video.)"
    )


# A caption URL comes from yt-dlp's output or, in the extension flow, from the page.
# Either way it decides what we fetch, so it is checked before anything is requested.
_CAPTION_HOSTS = ("youtube.com", "googlevideo.com", "ytimg.com")


def is_safe_caption_url(url) -> bool:
    if not isinstance(url, str) or len(url) > 4096:
        return False
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in _CAPTION_HOSTS)


def caption_url(meta: VideoMeta, lang_key: str, is_auto: bool):
    """The caption track URL yt-dlp already handed us, preferring a format the
    transcript parser understands."""
    tracks = (meta.auto_captions if is_auto else meta.subtitles).get(lang_key) or []
    for ext in ("vtt", "srt"):
        for track in tracks:
            if isinstance(track, dict) and track.get("ext") == ext:
                if is_safe_caption_url(track.get("url")):
                    return track["url"]
    for track in tracks:
        if isinstance(track, dict) and is_safe_caption_url(track.get("url")):
            return track["url"]
    return None


def fetch_caption_text(url: str, *, timeout: float = 30) -> str:
    """GET a caption track directly.

    yt-dlp's second invocation re-ran its whole extraction just to save this file,
    and that extraction is what YouTube fingerprints. A plain request for a URL we
    already hold is both faster and far less likely to be rate-limited.
    """
    if not is_safe_caption_url(url):
        raise TldrError("Refusing to fetch a caption track from an unexpected host.")
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"),
        "Accept-Language": "en-us,en;q=0.5",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(MAX_CAPTION_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise _translate(TldrError("HTTP Error 429: Too Many Requests")) from exc
        raise TldrError(f"Caption download failed (HTTP {exc.code}).") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise TldrError(f"Caption download failed: {exc}") from exc
    if len(raw) > MAX_CAPTION_BYTES:
        raise TldrError("Caption track is implausibly large; refusing it.")
    return raw.decode("utf-8", "replace")


def subtitle_text(meta: VideoMeta, lang_key: str, is_auto: bool, workdir: Path,
                  *, timeout: float = 120, on_progress=None) -> str:
    """The chosen caption track's text, the cheapest way that works.

    Direct first: we already have the URL, and a second yt-dlp run would repeat an
    extraction that costs several requests and is what gets fingerprinted. Falls
    back to yt-dlp if that fails for any reason.
    """
    log = on_progress or (lambda _m: None)
    url = caption_url(meta, lang_key, is_auto)
    if url:
        try:
            text = fetch_caption_text(url, timeout=timeout)
            if text.strip():
                return text
        except TldrError as exc:
            log(f"direct caption fetch failed ({exc}); falling back to yt-dlp")
    return download_subtitle(meta.video_id, lang_key, is_auto, workdir, timeout=timeout)


def download_subtitle(
    video_id: str, lang_key: str, is_auto: bool, workdir: Path, *, timeout: float = 120
) -> str:
    """Write the chosen subtitle track to workdir and return its text content."""
    flag = "--write-auto-subs" if is_auto else "--write-subs"
    try:
        run(
            [
                "yt-dlp", "--skip-download", flag,
                "--sub-langs", lang_key, "--sub-format", "vtt/srt/best",
                "--no-playlist", *_RETRY, *_cookie_args(),
            "-o", _OUTPUT_TMPL, watch_url(video_id),
            ],
            timeout=timeout,
            cwd=str(workdir),
        )
    except TldrError as exc:
        raise _translate(exc) from exc
    # video_id is [A-Za-z0-9_-]{11}: no glob metacharacters. Prefer the exact
    # requested track + format, else fall back to any produced sub file.
    preferred = [
        workdir / f"{video_id}.{lang_key}.vtt",
        workdir / f"{video_id}.{lang_key}.srt",
    ]
    candidates = [p for p in preferred if p.exists()] or (
        sorted(workdir.glob(f"{video_id}*.vtt")) + sorted(workdir.glob(f"{video_id}*.srt"))
    )
    if not candidates:
        raise TldrError("yt-dlp did not produce a subtitle file.")
    return candidates[0].read_text(encoding="utf-8", errors="replace")


def download_video(
    video_id: str, workdir: Path, *, max_height: int = _MAX_HEIGHT, timeout: float = 1800
) -> Path:
    fmt = (
        f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b"
    )
    run(
        [
            "yt-dlp", "-f", fmt, "--merge-output-format", "mp4",
            "--no-playlist", "-o", _OUTPUT_TMPL, watch_url(video_id),
        ],
        timeout=timeout,
        cwd=str(workdir),
    )
    # Prefer the exact merged mp4; otherwise pick the largest video file so a
    # leftover video-only fragment (e.g. id.f399.mp4) is never chosen by accident.
    exact = workdir / f"{video_id}.mp4"
    if exact.exists():
        return exact
    produced = sorted(workdir.glob(f"{video_id}.*"))
    videos = [p for p in produced if p.suffix.lower() in {".mp4", ".mkv", ".webm"}]
    if not videos:
        raise TldrError("yt-dlp did not produce a video file.")
    return max(videos, key=lambda p: p.stat().st_size)

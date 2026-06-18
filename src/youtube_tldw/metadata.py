"""yt-dlp interactions: metadata, subtitle track selection/download, video download.
All invoked through proc.run (argv list, shell=False). The video id is validated
upstream to [A-Za-z0-9_-]{11}, so the watch URL is safe to construct.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import NoTranscriptError, TldrError
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


def fetch_metadata(video_id: str, *, timeout: float = 120) -> VideoMeta:
    res = run(
        ["yt-dlp", "-J", "--no-playlist", "--skip-download", watch_url(video_id)],
        timeout=timeout,
    )
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


def download_subtitle(
    video_id: str, lang_key: str, is_auto: bool, workdir: Path, *, timeout: float = 120
) -> str:
    """Write the chosen subtitle track to workdir and return its text content."""
    flag = "--write-auto-subs" if is_auto else "--write-subs"
    run(
        [
            "yt-dlp", "--skip-download", flag,
            "--sub-langs", lang_key, "--sub-format", "vtt/srt/best",
            "--no-playlist", "-o", _OUTPUT_TMPL, watch_url(video_id),
        ],
        timeout=timeout,
        cwd=str(workdir),
    )
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

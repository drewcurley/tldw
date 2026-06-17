"""ffmpeg recut pipeline: extract normalized clips, crossfade-concat, optional
burned captions, and measure the true rendered duration with ffprobe.
"""

from __future__ import annotations

from pathlib import Path

from . import TldrError
from .proc import run
from .spans import Span, build_xfade_filter, xfade_offsets_ms
from .timing import to_ffmpeg_ts
from .transcript import Cue

# Uniform normalization so xfade can blend clips (xfade needs identical streams).
_W, _H, _FPS, _SR = 1280, 720, 30, 48000
_VF = (
    f"scale={_W}:{_H}:force_original_aspect_ratio=decrease,"
    f"pad={_W}:{_H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={_FPS},format=yuv420p"
)
_CAPTIONS = "captions.srt"  # fixed basename; referenced via cwd so no metachars

# Caption styles: "none", "burn" (libass subtitles filter), "soft" (mov_text track).
_filter_cache: dict[str, bool] = {}


def has_filter(name: str) -> bool:
    """Whether this ffmpeg build exposes a given filter (e.g. 'subtitles')."""
    if name not in _filter_cache:
        res = run(["ffmpeg", "-hide_banner", "-filters"], timeout=30, check=False)
        _filter_cache[name] = any(
            line.split()[1:2] == [name]
            for line in res.stdout.splitlines()
            if len(line.split()) >= 2
        )
    return _filter_cache[name]


def caption_style_for(burn_captions: bool) -> str:
    """Decide how to render captions given this ffmpeg's capabilities."""
    if not burn_captions:
        return "none"
    return "burn" if has_filter("subtitles") else "soft"


def _extract_clip(source: Path, span: Span, workdir: Path, idx: int) -> Path:
    out = workdir / f"clip_{idx:04d}.mp4"
    run(
        [
            "ffmpeg", "-y", "-ss", to_ffmpeg_ts(span.start_ms), "-i", str(source),
            "-t", to_ffmpeg_ts(span.duration_ms),
            "-vf", _VF, "-af", f"aresample={_SR}",
            "-r", str(_FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-ar", str(_SR), "-ac", "2",
            "-video_track_timescale", "30000", str(out),
        ],
        timeout=1800,
    )
    if not out.exists():
        raise TldrError(f"Failed to extract clip {idx}.")
    return out


def probe_duration_ms(path: Path) -> int:
    res = run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        timeout=60,
    )
    try:
        return int(round(float(res.stdout.strip()) * 1000))
    except ValueError as exc:
        raise TldrError(f"Could not read duration of {path.name}.") from exc


def build_recut_srt(
    spans: list[Span], cues: list[Cue], clip_durations_ms: list[int], xfade_ms: int
) -> str:
    """Caption file aligned to the recut timeline (approx; ignores fade overlap)."""
    offsets = [0] + xfade_offsets_ms(clip_durations_ms, xfade_ms)
    blocks, n = [], 1
    for span, clip_start in zip(spans, offsets):
        for c in cues:
            if c.end_ms <= span.start_ms or c.start_ms >= span.end_ms:
                continue
            s = clip_start + max(0, c.start_ms - span.start_ms)
            e = clip_start + min(span.duration_ms, c.end_ms - span.start_ms)
            if e <= s:
                continue
            blocks.append(f"{n}\n{_srt_ts(s)} --> {_srt_ts(e)}\n{c.text}\n")
            n += 1
    return "\n".join(blocks)


def _srt_ts(ms: int) -> str:
    return to_ffmpeg_ts(ms).replace(".", ",")


def recut(
    source: Path,
    spans: list[Span],
    cues: list[Cue],
    out_path: Path,
    workdir: Path,
    *,
    xfade_ms: int,
    caption_style: str = "none",
) -> int:
    """Render the TL;DR cut to out_path. Returns the true rendered duration (ms).

    caption_style: "none" | "burn" (libass) | "soft" (muxed mov_text track).
    """
    if not spans:
        raise TldrError("No segments to cut.")
    clips = [_extract_clip(source, s, workdir, i) for i, s in enumerate(spans)]
    durations = [probe_duration_ms(c) for c in clips]

    if caption_style != "none":
        (workdir / _CAPTIONS).write_text(
            build_recut_srt(spans, cues, durations, xfade_ms), encoding="utf-8"
        )

    if len(clips) == 1:
        _encode_single(clips[0], out_path, caption_style, workdir)
    else:
        _encode_crossfade(clips, durations, out_path, caption_style, xfade_ms, workdir)

    if not out_path.exists():
        raise TldrError("ffmpeg did not produce the output video.")
    return probe_duration_ms(out_path)


def _encode_single(
    clip: Path, out_path: Path, caption_style: str, cwd: Path
) -> None:
    # cwd=workdir lets us reference the caption file by safe basename only.
    argv = ["ffmpeg", "-y", "-i", str(clip)]
    if caption_style == "soft":
        argv += ["-i", _CAPTIONS]
    if caption_style == "burn":
        argv += ["-vf", f"subtitles={_CAPTIONS}"]
    argv += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac"]
    if caption_style == "soft":
        argv += ["-map", "0:v", "-map", "0:a", "-map", "1", "-c:s", "mov_text"]
    argv += [str(out_path)]
    run(argv, timeout=1800, cwd=str(cwd))


def _encode_crossfade(
    clips: list[Path],
    durations: list[int],
    out_path: Path,
    caption_style: str,
    xfade_ms: int,
    cwd: Path,
) -> None:
    graph, vlabel, alabel = build_xfade_filter(durations, xfade_ms)
    if caption_style == "burn":
        graph = f"{graph};{vlabel}subtitles={_CAPTIONS}[vsub]"
        vlabel = "[vsub]"
    argv = ["ffmpeg", "-y"]
    for c in clips:
        argv += ["-i", str(c)]
    if caption_style == "soft":
        argv += ["-i", _CAPTIONS]
    argv += ["-filter_complex", graph, "-map", vlabel, "-map", alabel]
    if caption_style == "soft":
        argv += ["-map", f"{len(clips)}:0", "-c:s", "mov_text"]
    argv += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-c:a", "aac", str(out_path)]
    run(argv, timeout=3600, cwd=str(cwd))

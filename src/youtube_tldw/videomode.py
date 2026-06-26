"""ffmpeg recut pipeline: extract normalized clips, crossfade-concat, optional
burned captions, and measure the true rendered duration with ffprobe.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import TldrError, overlays
from .proc import run
from .spans import Span, build_xfade_chain, build_xfade_filter, xfade_offsets_ms

_MIN_CLIP_MS = 500  # clamp guard; xfade needs a clip longer than its own duration
from .timing import format_clock, to_ffmpeg_ts
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


# shared output flags so every clip (plain or timestamped) is xfade-compatible
_CLIP_OUT = [
    "-r", str(_FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
    "-c:a", "aac", "-ar", str(_SR), "-ac", "2", "-video_track_timescale", "30000",
]
_TS_SHOW_MAX = 1.8   # seconds the source timestamp is visible per clip
_TS_FADE = 0.35      # fade in/out duration


def _extract_clip(
    source: Path, span: Span, workdir: Path, idx: int, ts_text: str | None = None
) -> Path:
    out = workdir / f"clip_{idx:04d}.mp4"
    if ts_text is None:
        run(
            ["ffmpeg", "-y", "-ss", to_ffmpeg_ts(span.start_ms), "-i", str(source),
             "-t", to_ffmpeg_ts(span.duration_ms), "-vf", _VF, "-af", f"aresample={_SR}",
             *_CLIP_OUT, str(out)],
            timeout=1800,
        )
    else:
        # Overlay a source-timestamp that fades in/out at the clip start. Folded into
        # the normalization re-encode (no extra generation). SHOW shrinks for short
        # clips so the fade-out always completes; eof_action=pass drops it afterward.
        ts_png = workdir / f"ts_{idx:04d}.png"
        overlays.render_timestamp(ts_png, ts_text)
        dur_s = span.duration_ms / 1000.0
        show = max(2 * _TS_FADE, min(_TS_SHOW_MAX, dur_s))
        fade = min(_TS_FADE, show / 3)
        fade_out_st = show - fade
        graph = (
            f"[0:v]{_VF}[base];"
            f"[1:v]format=rgba,fade=t=in:st=0:d={fade:.3f}:alpha=1,"
            f"fade=t=out:st={fade_out_st:.3f}:d={fade:.3f}:alpha=1[ts];"
            f"[base][ts]overlay=24:24:eof_action=pass,setsar=1,format=yuv420p[v]"
        )
        run(
            ["ffmpeg", "-y", "-ss", to_ffmpeg_ts(span.start_ms),
             "-t", to_ffmpeg_ts(span.duration_ms), "-i", str(source),
             "-loop", "1", "-t", f"{show:.3f}", "-i", ts_png.name,
             "-filter_complex", graph, "-map", "[v]", "-map", "0:a",
             "-af", f"aresample={_SR}", *_CLIP_OUT, str(out)],
            timeout=1800, cwd=str(workdir),
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


@dataclass
class Polish:
    """Video-polish options. `banner_intro_s` is the kept-intro length in seconds
    (None => no intro banner). All text is drawn to PNGs, never into a filter."""
    badge: bool = True
    banner_intro_s: float | None = None
    end_card: bool = True
    timestamps: bool = True
    source_url: str | None = None
    badge_text: str = "TL;DW"
    banner_text: str = "TL;DW version"
    end_card_text: str = "Made with youtube-tldw"
    fadeblack_ms: int = 600
    fade_in_ms: int = 500
    fade_out_ms: int = 700
    end_card_s: float = 2.5

    @property
    def any_enabled(self) -> bool:
        return (self.badge or self.end_card or self.timestamps
                or self.banner_intro_s is not None)


def recut(
    source: Path,
    spans: list[Span],
    cues: list[Cue],
    out_path: Path,
    workdir: Path,
    *,
    xfade_ms: int,
    caption_style: str = "none",
    polish: "Polish | None" = None,
) -> int:
    """Render the TL;DW cut to out_path. Returns the true rendered duration (ms).

    caption_style: "none" | "burn" (libass) | "soft" (muxed mov_text track).
    polish: when set and enabled, runs the two-pass intro/badge/end-card pipeline.
    """
    if not spans:
        raise TldrError("No segments to cut.")

    # yt-dlp metadata duration can be off by a second or two from the actual
    # container.  Clamp every span to the probed source length so the last
    # ffmpeg extraction never asks for frames that don't exist.
    src_ms = probe_duration_ms(source)
    clamped: list[Span] = []
    for s in spans:
        end = min(s.end_ms, src_ms)
        if end - s.start_ms >= _MIN_CLIP_MS:
            clamped.append(Span(s.start_ms, end, label_ms=s.label_ms))
    if not clamped:
        raise TldrError(
            "No segments remain after clamping to the source video's actual length."
        )
    spans = clamped

    ts_on = polish is not None and polish.timestamps
    clips = [
        _extract_clip(source, s, workdir, i,
                      ts_text=format_clock(s.display_start_ms) if ts_on else None)
        for i, s in enumerate(spans)
    ]
    durations = [probe_duration_ms(c) for c in clips]

    if caption_style != "none":
        (workdir / _CAPTIONS).write_text(
            build_recut_srt(spans, cues, durations, xfade_ms), encoding="utf-8"
        )

    if polish is not None and polish.any_enabled:
        _recut_polished(clips, durations, out_path, workdir,
                        xfade_ms=xfade_ms, caption_style=caption_style, polish=polish)
    elif len(clips) == 1:
        _encode_single(clips[0], out_path, caption_style, workdir)
    else:
        _encode_crossfade(clips, durations, out_path, caption_style, xfade_ms, workdir)

    if not out_path.exists():
        raise TldrError("ffmpeg did not produce the output video.")
    return probe_duration_ms(out_path)


def _render_end_card_clip(
    workdir: Path, text: str, seconds: float, url: str | None = None
) -> Path:
    """Render the end-card PNG and encode it to a clip matching content-clip params."""
    overlays.render_end_card(workdir / "endcard.png", text, url)
    run(
        [
            "ffmpeg", "-y", "-loop", "1", "-t", f"{seconds:.3f}", "-i", "endcard.png",
            "-f", "lavfi", "-t", f"{seconds:.3f}",
            "-i", f"anullsrc=channel_layout=stereo:sample_rate={_SR}",
            "-vf", _VF, "-r", str(_FPS),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", str(_SR), "-ac", "2", "-shortest",
            "-video_track_timescale", "30000", "endcard.mp4",
        ],
        timeout=600, cwd=str(workdir),
    )
    return workdir / "endcard.mp4"


def _assemble(
    clips: list[Path], durations: list[int], workdir: Path,
    *, xfade_ms: int, polish: Polish,
) -> tuple[Path, int]:
    """Pass 1: content xfade chain + fade-to-black end card. Returns (file, probed_ms)."""
    chain = list(clips)
    chain_durs = list(durations)
    joins = [(xfade_ms, "fade")] * (len(clips) - 1)
    if polish.end_card:
        endcard = _render_end_card_clip(
            workdir, polish.end_card_text, polish.end_card_s, polish.source_url
        )
        chain.append(endcard)
        chain_durs.append(probe_duration_ms(endcard))
        joins.append((polish.fadeblack_ms, "fadeblack"))

    if len(chain) == 1:
        return chain[0], probe_duration_ms(chain[0])  # nothing to join; decorate as-is

    graph, vlabel, alabel = build_xfade_chain(chain_durs, joins)
    argv = ["ffmpeg", "-y"]
    for c in chain:
        argv += ["-i", c.name]
    argv += [
        "-filter_complex", graph, "-map", vlabel, "-map", alabel, "-shortest",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-ar", str(_SR), "-ac", "2",
        "-video_track_timescale", "30000", "assembled.mp4",
    ]
    run(argv, timeout=3600, cwd=str(workdir))
    assembled = workdir / "assembled.mp4"
    return assembled, probe_duration_ms(assembled)


def _decorate(
    assembled: Path, total_ms: int, out_path: Path, workdir: Path,
    *, caption_style: str, polish: Polish,
) -> None:
    """Pass 2: overlay badge/banner, fade in/out (keyed to probed length), captions."""
    if polish.badge:
        overlays.render_corner_badge(workdir / "badge.png", polish.badge_text)
    if polish.banner_intro_s is not None:
        overlays.render_intro_banner(
            workdir / "banner.png", polish.banner_text, polish.source_url
        )

    argv = ["ffmpeg", "-y", "-i", assembled.name]
    idx = 1
    badge_i = banner_i = cap_i = None
    if polish.badge:
        argv += ["-i", "badge.png"]; badge_i = idx; idx += 1
    if polish.banner_intro_s is not None:
        argv += ["-i", "banner.png"]; banner_i = idx; idx += 1
    if caption_style == "soft":
        argv += ["-i", _CAPTIONS]; cap_i = idx; idx += 1

    parts, cur = [], "[0:v]"
    if badge_i is not None:
        parts.append(f"{cur}[{badge_i}:v]overlay=W-w-24:24[vb]"); cur = "[vb]"
    if banner_i is not None:
        parts.append(
            f"{cur}[{banner_i}:v]overlay=0:0:"
            f"enable='between(t,0,{polish.banner_intro_s:.3f})'[vbn]"
        ); cur = "[vbn]"
    chain_filters = []
    if caption_style == "burn":
        chain_filters.append(f"subtitles={_CAPTIONS}")
    fade_out_st = max(0.0, (total_ms - polish.fade_out_ms) / 1000.0)
    chain_filters.append(f"fade=t=in:st=0:d={polish.fade_in_ms / 1000.0:.3f}")
    chain_filters.append(f"fade=t=out:st={fade_out_st:.3f}:d={polish.fade_out_ms / 1000.0:.3f}")
    chain_filters += ["format=yuv420p", "setsar=1"]
    parts.append(f"{cur}{','.join(chain_filters)}[vout]")

    argv += ["-filter_complex", ";".join(parts), "-map", "[vout]", "-map", "0:a"]
    if cap_i is not None:
        argv += ["-map", f"{cap_i}:0", "-c:s", "mov_text"]
    argv += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-c:a", "aac", str(out_path)]
    run(argv, timeout=3600, cwd=str(workdir))


def _recut_polished(
    clips: list[Path], durations: list[int], out_path: Path, workdir: Path,
    *, xfade_ms: int, caption_style: str, polish: Polish,
) -> None:
    assembled, total_ms = _assemble(
        clips, durations, workdir, xfade_ms=xfade_ms, polish=polish
    )
    _decorate(assembled, total_ms, out_path, workdir,
              caption_style=caption_style, polish=polish)


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

"""Command-line entrypoint and orchestration for youtube-tldr."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from . import TldrError, __version__
from . import metadata as md
from . import naming, proc, spans, summarize, textmode, transcript, videomode
from .timing import format_length, parse_duration
from .urls import canonical_video_id

DEFAULT_OUTPUT = Path.home() / "Downloads" / "youtube-tldr" / "tldrs"
XFADE_MS = 400
MIN_CLIP_MS = 1200
CLAUDE_TIMEOUT = 600.0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tldr",
        description="Summarize a YouTube video into a text or video TL;DR "
        "using your Claude Max subscription.",
    )
    p.add_argument("url", help="YouTube video URL (watch / youtu.be / shorts)")
    p.add_argument("--mode", required=True, choices=["text", "video"])
    p.add_argument("--ratio", type=float, default=None,
                   help="Target fraction of original length (0 < r <= 1). "
                        "Omit to let the AI choose.")
    p.add_argument("--max-length", default=None,
                   help="Hard cap on output length, e.g. 5m, 90s, 1m30s. "
                        "Wins over --ratio.")
    p.add_argument("--lang", default="en", help="Preferred subtitle language (default en)")
    p.add_argument("--burn-captions", action="store_true",
                   help="(video) burn recut-aligned captions into the video")
    p.add_argument("--keep-source", action="store_true",
                   help="(video) keep the downloaded source video too")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT,
                   help=f"Base output dir (default {DEFAULT_OUTPUT})")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _validate_args(args: argparse.Namespace) -> int | None:
    if args.ratio is not None and not (0 < args.ratio <= 1):
        raise TldrError("--ratio must be between 0 (exclusive) and 1.")
    max_ms = parse_duration(args.max_length) if args.max_length else None
    if max_ms is not None and max_ms <= 0:
        raise TldrError("--max-length must be positive.")
    return max_ms


def _ratio_cap(ratio: float | None, duration_ms: int) -> int | None:
    return int(ratio * duration_ms) if ratio else None


def _get_cues(video_id: str, lang: str, workdir: Path) -> tuple[md.VideoMeta, list]:
    meta = md.fetch_metadata(video_id)
    lang_key, is_auto = md.choose_track(meta, lang)
    kind = "auto-captions" if is_auto else "subtitles"
    print(f"Using {kind} ({lang_key}) for “{meta.title}” by {meta.channel}.")
    content = md.download_subtitle(video_id, lang_key, is_auto, workdir)
    cues = transcript.parse_subtitles(content)
    print(f"Parsed {len(cues)} transcript cues "
          f"({transcript.word_count(cues)} words).")
    return meta, cues


def _run_text(args, video_id: str, workdir: Path) -> Path:
    meta, cues = _get_cues(video_id, args.lang, workdir)
    print("Summarizing with Claude…")
    result = summarize.summarize_text(
        cues, meta.channel, meta.title, args.ratio, timeout=CLAUDE_TIMEOUT
    )
    markdown = textmode.render_markdown(meta, result)
    filename = naming.build_filename(
        meta.channel, meta.title, textmode.length_label(result), "md"
    )
    out_path = naming.avoid_overwrite(
        naming.resolve_output_path(args.output_dir, "text", filename)
    )
    textmode.write_markdown(out_path, markdown)
    print("\n" + "=" * 70 + "\n")
    print(markdown)
    print("=" * 70)
    return out_path


def _run_video(args, video_id: str, max_ms, workdir: Path) -> Path:
    meta, cues = _get_cues(video_id, args.lang, workdir)
    print("Asking Claude which segments to keep…")
    selection = summarize.select_video_segments(
        cues, meta.channel, meta.title, args.ratio, max_ms, timeout=CLAUDE_TIMEOUT
    )
    chosen = spans.spans_from_cue_ranges(selection.ranges, cues, min_clip_ms=MIN_CLIP_MS)
    # --max-length is a hard cap; an explicit --ratio also acts as a deterministic
    # cap (an "override"). When --ratio is omitted, only the AI's choice applies.
    caps = [c for c in (max_ms, _ratio_cap(args.ratio, meta.duration_ms)) if c]
    chosen = spans.enforce_max_length(chosen, min(caps) if caps else None, XFADE_MS)
    est = spans.rendered_duration_ms(chosen, XFADE_MS)
    print(f"Selected {len(chosen)} segment(s), ~{format_length(est)} before render.")

    caption_style = videomode.caption_style_for(args.burn_captions)
    if caption_style == "soft":
        print("Note: this ffmpeg lacks libass, so captions are embedded as a "
              "toggleable soft-subtitle track instead of being burned in. "
              "(Install a libass-enabled ffmpeg for true burn-in.)")

    print("Downloading video…")
    source = md.download_video(video_id, workdir)
    print("Recutting with crossfades…")
    tmp_out = workdir / "tldr_output.mp4"
    rendered_ms = videomode.recut(
        source, chosen, cues, tmp_out, workdir,
        xfade_ms=XFADE_MS, caption_style=caption_style,
    )

    filename = naming.build_filename(
        meta.channel, meta.title, format_length(rendered_ms), "mp4"
    )
    out_path = naming.avoid_overwrite(
        naming.resolve_output_path(args.output_dir, "video", filename)
    )
    shutil.move(str(tmp_out), str(out_path))
    if args.keep_source:
        kept = naming.avoid_overwrite(
            naming.resolve_output_path(
                args.output_dir, "video",
                naming.build_filename(meta.channel, meta.title, "source", source.suffix.lstrip(".")),
            )
        )
        shutil.move(str(source), str(kept))
        print(f"Kept source video: {kept}")
    print(f"Final length: {format_length(rendered_ms)}")
    return out_path


def run(args: argparse.Namespace) -> int:
    max_ms = _validate_args(args)
    proc.require("claude", "yt-dlp", "ffmpeg", "ffprobe")
    video_id = canonical_video_id(args.url)

    workdir = Path(tempfile.mkdtemp(prefix="youtube-tldr-"))
    try:
        if args.mode == "text":
            out_path = _run_text(args, video_id, workdir)
        else:
            out_path = _run_video(args, video_id, max_ms, workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"\nSaved: {out_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except TldrError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

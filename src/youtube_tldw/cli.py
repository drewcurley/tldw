"""Command-line entrypoint and orchestration for youtube-tldw."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

from . import TldrError, __version__
from . import metadata as md
from . import audio, naming, proc, spans, summarize, textmode, transcript, videomode
from .timing import format_length, parse_duration
from .urls import canonical_video_id

DEFAULT_OUTPUT = Path.home() / "Downloads" / "youtube-tldw" / "tldws"
XFADE_MS = 400
MIN_CLIP_MS = 1200
BOUNDARY_PAD_MS = 600  # pad cuts into real silence so crossfades don't clip words
DEFAULT_INTRO_S = 6
CLAUDE_TIMEOUT = 600.0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tldw",
        description="Summarize a YouTube video into a text or video TL;DW "
        "using the `claude` CLI (any Claude plan or API key) — or another model "
        "via --llm-cmd.",
    )
    p.add_argument("url", help="YouTube URL (watch / youtu.be / shorts) or bare video id")
    p.add_argument("--mode", choices=["text", "video"], default="video",
                   help="text | video (default: video)")
    p.add_argument("--render-audio", action="store_true",
                   help="also save an mp3: video → the recut audio; text → spoken "
                        "summary via local neural TTS")
    p.add_argument("--voice", default="female", metavar="VOICE",
                   choices=["female", "male", *audio.VOICES.keys()],
                   help="(text --render-audio) TTS voice: female|male or a named "
                        "voice (amy, ryan, cori, alan, …).")
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
    intro = p.add_mutually_exclusive_group()
    intro.add_argument("--keep-intro", type=int, metavar="SECONDS", default=None,
                       help=f"(video) keep the first SECONDS as an intro "
                            f"(default {DEFAULT_INTRO_S}s; polish is on by default)")
    intro.add_argument("--no-intro", action="store_true",
                       help="(video) don't preserve the intro")
    p.add_argument("--no-badge", action="store_true",
                   help="(video) no 'TL;DW' corner badge")
    p.add_argument("--no-banner", action="store_true",
                   help="(video) no 'TL;DW version' intro banner")
    p.add_argument("--no-end-card", action="store_true",
                   help="(video) no fade-to-black 'Made with youtube-tldw' end card")
    p.add_argument("--no-timestamps", action="store_true",
                   help="(video) don't fade in the source timestamp at each cut")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT,
                   help=f"Base output dir (default {DEFAULT_OUTPUT})")
    p.add_argument("--llm-cmd", default=None, metavar="CMD",
                   help="LLM backend command (reads prompt on stdin, prints the "
                        "model's text). Default: the `claude` CLI. Or set TLDW_LLM_CMD. "
                        "e.g. \"llm -m gpt-4o\" or \"ollama run llama3\".")
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


def _intro_seconds(args: argparse.Namespace) -> int:
    if args.no_intro:
        return 0
    if args.keep_intro is not None:
        return max(0, args.keep_intro)
    return DEFAULT_INTRO_S


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
    if args.render_audio:
        print(f"Synthesizing speech ({args.voice} voice)…")
        tmp_mp3 = workdir / "speech.mp3"
        audio.synthesize_speech(
            audio.build_spoken_script(meta.title, meta.channel, result.key_points,
                                      result.summary),
            tmp_mp3, args.voice, workdir,
        )
        _save_audio(args, meta, tmp_mp3)
    return out_path


def _save_audio(args, meta: md.VideoMeta, tmp_mp3: Path) -> None:
    length = format_length(videomode.probe_duration_ms(tmp_mp3))
    fname = naming.build_filename(meta.channel, meta.title, length, "mp3")
    dest = naming.avoid_overwrite(
        naming.resolve_output_path(args.output_dir, "audio", fname)
    )
    shutil.move(str(tmp_mp3), str(dest))
    print(f"Saved audio: {dest}")


def _run_video(args, video_id: str, max_ms, workdir: Path) -> Path:
    meta, cues = _get_cues(video_id, args.lang, workdir)
    print("Asking Claude which segments to keep…")
    selection = summarize.select_video_segments(
        cues, meta.channel, meta.title, args.ratio, max_ms, timeout=CLAUDE_TIMEOUT
    )
    chosen = spans.spans_from_cue_ranges(selection.ranges, cues, min_clip_ms=MIN_CLIP_MS)
    # Pad cut boundaries into real silence so the crossfade dissolves over the
    # natural pause around each sentence instead of clipping words.
    chosen = spans.pad_spans(chosen, cues, BOUNDARY_PAD_MS, meta.duration_ms)
    # --max-length is a hard cap; an explicit --ratio also acts as a deterministic
    # cap (an "override"). When --ratio is omitted, only the AI's choice applies.
    # Note: the cap bounds the KEY segments; intro + end card are additive.
    caps = [c for c in (max_ms, _ratio_cap(args.ratio, meta.duration_ms)) if c]
    chosen = spans.enforce_max_length(chosen, min(caps) if caps else None, XFADE_MS)

    intro_s = _intro_seconds(args)
    if intro_s > 0:
        intro = spans.Span(0, min(intro_s * 1000, meta.duration_ms))
        chosen = spans.merge_spans([intro] + chosen)

    polish = videomode.Polish(
        badge=not args.no_badge,
        banner_intro_s=float(intro_s) if (intro_s > 0 and not args.no_banner) else None,
        end_card=not args.no_end_card,
        timestamps=not args.no_timestamps,
        source_url=md.watch_url(video_id),
    )
    est = spans.rendered_duration_ms(chosen, XFADE_MS)
    print(f"Selected {len(chosen)} segment(s), ~{format_length(est)} of source "
          f"before polish.")
    if polish.any_enabled:
        bits = [b for b, on in (("intro", intro_s > 0), ("badge", polish.badge),
                                ("timestamps", polish.timestamps),
                                ("end-card", polish.end_card)) if on]
        print(f"Polish on ({', '.join(bits)}); disable with "
              f"--no-intro/--no-badge/--no-end-card.")

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
        xfade_ms=XFADE_MS, caption_style=caption_style, polish=polish,
    )
    if max_ms is not None and rendered_ms > max_ms:
        print(f"Note: final {format_length(rendered_ms)} exceeds --max-length "
              f"{format_length(max_ms)} because the intro/end card are additive "
              f"(the cap bounds the key segments).")

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
    if args.render_audio:
        print("Extracting audio…")
        tmp_mp3 = workdir / "audio.mp3"
        audio.extract_audio(out_path, tmp_mp3)
        _save_audio(args, meta, tmp_mp3)
    return out_path


def run(args: argparse.Namespace) -> int:
    max_ms = _validate_args(args)
    if args.llm_cmd:
        os.environ["TLDW_LLM_CMD"] = args.llm_cmd
    required = ["yt-dlp", "ffmpeg", "ffprobe"]
    if not (args.llm_cmd or os.environ.get("TLDW_LLM_CMD")):
        required.append("claude")  # only needed for the default backend
    proc.require(*required)
    if args.render_audio and args.mode == "text":
        audio.require_piper()  # fail fast before any Claude work
    video_id = canonical_video_id(args.url)

    workdir = Path(tempfile.mkdtemp(prefix="youtube-tldw-"))
    try:
        if args.mode == "text":
            out_path = _run_text(args, video_id, workdir)
        else:
            out_path = _run_video(args, video_id, max_ms, workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"\nSaved: {out_path}")
    return 0


def _serve_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tldw serve",
                                description="Run the local HTTP API for the browser extension.")
    p.add_argument("--host", default="127.0.0.1", help="loopback only (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--token", default=None, help="bearer token (or set TLDW_TOKEN)")
    p.add_argument("--allow-origin", default=None,
                   help="pin one extension origin; default allows any chrome-extension://")
    p.add_argument("--llm-cmd", default=None, metavar="CMD",
                   help="LLM backend command (default: claude CLI; or set TLDW_LLM_CMD)")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    # Dispatch `serve` before argparse so `tldw <url>` stays unchanged.
    if argv and argv[0] == "serve":
        from . import server
        sargs = _serve_parser().parse_args(argv[1:])
        if sargs.llm_cmd:
            os.environ["TLDW_LLM_CMD"] = sargs.llm_cmd
        try:
            server.serve(sargs.host, sargs.port,
                         sargs.token or os.environ.get("TLDW_TOKEN"),
                         sargs.allow_origin)
            return 0
        except TldrError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

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

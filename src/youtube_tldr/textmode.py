"""Render the text TL;DR to Markdown and write the .md file."""

from __future__ import annotations

from pathlib import Path

from .metadata import VideoMeta, watch_url
from .summarize import TextResult
from .timing import format_length, read_time_label


def render_markdown(meta: VideoMeta, result: TextResult) -> str:
    lines = [
        f"# TL;DR — {meta.title}",
        "",
        f"**Channel:** {meta.channel}  ",
        f"**Source:** {watch_url(meta.video_id)}  ",
        f"**Original length:** {format_length(meta.duration_ms)}",
        "",
        "## Key points",
        "",
    ]
    lines += [f"- {p}" for p in result.key_points] or ["- (none extracted)"]
    lines += ["", "## Summary", "", result.summary, ""]
    if result.rationale:
        lines += [f"> _{result.rationale}_", ""]
    return "\n".join(lines)


def summary_word_count(result: TextResult) -> int:
    return len(result.summary.split()) + sum(len(p.split()) for p in result.key_points)


def length_label(result: TextResult) -> str:
    return read_time_label(summary_word_count(result))


def write_markdown(path: Path, markdown: str) -> None:
    path.write_text(markdown, encoding="utf-8")

"""Parse VTT/SRT subtitles into clean, non-overlapping, de-duplicated cues.

Handles two shapes:
  * manual subtitles (SRT or clean VTT): multi-line cues joined with a space.
  * YouTube auto-captions (VTT with inline <c>/<timestamp> "paint-on" tags):
    rolling cues that repeat the previous line — de-duplicated line-by-line.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from . import TldrError
from .timing import parse_cue_ts

_ARROW = "-->"
_INLINE_TS = re.compile(r"<\d{2}:\d{2}:\d{2}[.,]\d{3}>")
_INLINE_TAG = re.compile(r"</?c[^>]*>|<[^>]+>")
_WS = re.compile(r"\s+")
_HAS_INLINE = re.compile(r"<\d{2}:\d{2}:\d{2}[.,]\d{3}>")


@dataclass
class Cue:
    start_ms: int
    end_ms: int
    text: str


def _clean_line(line: str) -> str:
    line = _INLINE_TS.sub("", line)
    line = _INLINE_TAG.sub("", line)
    line = html.unescape(line)
    return _WS.sub(" ", line).strip()


def _raw_blocks(content: str) -> list[tuple[int, int, list[str]]]:
    """Yield (start_ms, end_ms, payload_lines) for every timed block."""
    blocks: list[tuple[int, int, list[str]]] = []
    for block in re.split(r"\r?\n\r?\n", content):
        lines = block.splitlines()
        arrow_idx = next((i for i, ln in enumerate(lines) if _ARROW in ln), None)
        if arrow_idx is None:
            continue  # header / NOTE / STYLE / index-only
        try:
            left, right = lines[arrow_idx].split(_ARROW, 1)
            start = parse_cue_ts(left)
            end = parse_cue_ts(right)
        except TldrError:
            continue
        payload = lines[arrow_idx + 1 :]
        blocks.append((start, end, payload))
    return blocks


def parse_subtitles(content: str) -> list[Cue]:
    """Parse subtitle text into ordered, de-duplicated, non-overlapping cues."""
    blocks = _raw_blocks(content)
    if not blocks:
        raise TldrError("Subtitle file contained no readable cues.")

    is_auto = bool(_HAS_INLINE.search(content))
    cues: list[Cue] = []
    last_text: str | None = None

    for start, end, payload in blocks:
        cleaned = [_clean_line(ln) for ln in payload]
        cleaned = [ln for ln in cleaned if ln]
        if not cleaned:
            continue
        if is_auto:
            for line in cleaned:
                if line == last_text:
                    if cues:
                        cues[-1].end_ms = max(cues[-1].end_ms, end)
                    continue
                cues.append(Cue(start, end, line))
                last_text = line
        else:
            text = " ".join(cleaned)
            if text == last_text:
                cues[-1].end_ms = max(cues[-1].end_ms, end)
                continue
            cues.append(Cue(start, end, text))
            last_text = text

    return _normalize(cues)


def _normalize(cues: list[Cue]) -> list[Cue]:
    """Sort, fix inverted spans, and remove overlaps so cuts never collide."""
    cues = [c for c in cues if c.text and c.end_ms > c.start_ms]
    if not cues:
        raise TldrError("Subtitle file produced no usable text.")
    cues.sort(key=lambda c: (c.start_ms, c.end_ms))
    for i in range(1, len(cues)):
        if cues[i].start_ms < cues[i - 1].end_ms:
            cues[i].start_ms = cues[i - 1].end_ms
        if cues[i].end_ms <= cues[i].start_ms:
            cues[i].end_ms = cues[i].start_ms + 1
    return cues


def full_text(cues: list[Cue]) -> str:
    return " ".join(c.text for c in cues)


def word_count(cues: list[Cue]) -> int:
    return sum(len(c.text.split()) for c in cues)

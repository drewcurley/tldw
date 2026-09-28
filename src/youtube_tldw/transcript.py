"""Parse VTT/SRT subtitles into clean, non-overlapping, de-duplicated cues.

Handles two shapes:
  * manual subtitles (SRT or clean VTT): multi-line cues joined with a space.
  * YouTube auto-captions (VTT with inline <c>/<timestamp> "paint-on" tags):
    rolling cues that repeat the previous line — de-duplicated line-by-line.
"""

from __future__ import annotations

import json

import html
import re
from dataclasses import dataclass

from . import NoTranscriptError, TldrError
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


def parse_json3(content: str) -> list[Cue]:
    """Parse YouTube's json3 caption format into cues.

    It's what the player itself requests, so it's the format most reliably served
    when the page fetches its own captions. Shape:
    {"events":[{"tStartMs":0,"dDurationMs":1200,"segs":[{"utf8":"text"}]}, ...]}
    """
    try:
        data = json.loads(content)
        events = data["events"]
    except (ValueError, KeyError, TypeError) as exc:
        raise NoTranscriptError("Caption data was not valid json3.") from exc
    cues = []
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, dict):
            continue
        text = "".join(
            seg.get("utf8", "") for seg in event.get("segs") or []
            if isinstance(seg, dict)
        ).strip()
        start = event.get("tStartMs")
        if not text or not isinstance(start, (int, float)):
            continue                      # timing-only or empty events
        duration = event.get("dDurationMs")
        duration = int(duration) if isinstance(duration, (int, float)) else 0
        cues.append(Cue(int(start), int(start) + max(duration, 1), text))
    if not cues:
        raise NoTranscriptError("Caption data contained no readable cues.")
    # json3 repeats rolling text the same way auto-captions do in VTT.
    deduped, last = [], None
    for cue in cues:
        if cue.text == last and deduped:
            deduped[-1].end_ms = max(deduped[-1].end_ms, cue.end_ms)
            continue
        deduped.append(cue)
        last = cue.text
    return _normalize(deduped)


MAX_PANEL_ROWS = 20_000        # a 10-hour video is ~5,000 panel rows
MAX_CUE_CHARS = 1_000
_TAIL_MS = 5_000               # how long the closing line runs when nothing says


def parse_panel_rows(rows, duration_ms: int | None = None) -> list[Cue]:
    """Cues from YouTube's own transcript panel, as the extension read it.

    The panel gives each line a start and the words, but no end -- on screen, a line
    simply runs until the next one starts, so that is what we reconstruct. The last
    line runs to the end of the video when we know how long it is, since its start is
    where the closing sentence begins, not where it stops.

    Every value here came in over HTTP from the page, so nothing is trusted: rows
    that aren't a start plus some text are dropped rather than repaired.
    """
    clean: list[tuple[int, str]] = []
    for row in list(rows)[:MAX_PANEL_ROWS]:
        if not isinstance(row, dict):
            continue
        start, text = row.get("start_ms"), row.get("text")
        if isinstance(start, bool) or not isinstance(start, (int, float)):
            continue
        if start < 0 or start > 24 * 3600 * 1000:
            continue
        if not isinstance(text, str) or not text.strip():
            continue
        clean.append((int(start), _WS.sub(" ", text).strip()[:MAX_CUE_CHARS]))
    if not clean:
        return []
    clean.sort(key=lambda row: row[0])
    end_of_video = duration_ms if isinstance(duration_ms, int) else None
    cues = []
    for i, (start, text) in enumerate(clean):
        if i + 1 < len(clean):
            end = clean[i + 1][0]
        elif end_of_video and end_of_video > start:
            end = end_of_video
        else:
            end = start + _TAIL_MS
        cues.append(Cue(start, max(end, start + 1), text))
    return _normalize(cues)


def parse_captions(content: str, fmt: str | None = None) -> list[Cue]:
    """Parse a caption track in whichever format the page managed to fetch."""
    if fmt == "json3" or content.lstrip().startswith("{"):
        return parse_json3(content)
    return parse_subtitles(content)


def parse_subtitles(content: str) -> list[Cue]:
    """Parse subtitle text into ordered, de-duplicated, non-overlapping cues."""
    blocks = _raw_blocks(content)
    if not blocks:
        raise NoTranscriptError("Subtitle file contained no readable cues.")

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
        raise NoTranscriptError("Subtitle file produced no usable text.")
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

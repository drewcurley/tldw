"""Prompt construction, response validation, and map-reduce orchestration for
both text and video summarization. All prompts are STATIC; every piece of data
(metadata, transcript, cues) travels on stdin.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import TldrError
from .claude_client import ask_json
from .timing import format_length, to_ffmpeg_ts
from .transcript import Cue

# Defensive single-pass cap (chars). Opus has a 1M-token window, so this is
# generous; beyond it we map-reduce (text) / chunk-select (video).
SINGLE_PASS_CHARS = 350_000

_TEXT_PROMPT = """You are an expert at distilling video transcripts into a TL;DW \
that preserves every key point while cutting all filler.

Input (on stdin): video metadata then the full transcript.

{ratio_clause}

Return ONLY a JSON object, no prose, no markdown fences, with this schema:
{{
  "key_points": ["concise bullet", ...],   // the essential takeaways, in order
  "summary": "markdown body that reads naturally and keeps all key points",
  "chosen_ratio": 0.0,                      // fraction of original length you kept
  "rationale": "one sentence on why this length fits the content"
}}"""

_TEXT_REDUCE_PROMPT = """You are combining ordered partial summaries of one video \
into a single TL;DW. Input (on stdin): the partial summaries in order.

{ratio_clause}

Return ONLY a JSON object with this schema:
{{
  "key_points": ["concise bullet", ...],
  "summary": "unified markdown body, no repetition",
  "chosen_ratio": 0.0,
  "rationale": "one sentence"
}}"""

_VIDEO_PROMPT = """You are selecting the segments of a video to KEEP for a TL;DW \
recut. Input (on stdin): metadata then the transcript as numbered cues, one per \
line, formatted `[index] (timestamp) text`.

Pick the cue ranges that together preserve every key point as a much shorter cut.
Select whole ranges of consecutive cues (so the audio stays coherent). Keep them
in chronological order. Prefer fewer, longer ranges over many tiny ones.

{ratio_clause}
{maxlen_clause}

Return ONLY a JSON object, no prose, with this schema:
{{
  "segments": [
    {{"first_cue": 0, "last_cue": 12, "reason": "why this matters"}}
  ],
  "chosen_ratio": 0.0,
  "rationale": "one sentence on the overall length you chose"
}}"""


def _ratio_clause(ratio: float | None) -> str:
    if ratio is None:
        return (
            "Decide the ideal degree of compression YOURSELF based on how "
            "information-dense the content is. Be aggressive: keep only what "
            "genuinely matters. A dense talk may keep more; a rambling vlog much less."
        )
    return (
        f"Target roughly {round(ratio * 100)}% of the original length. Treat this "
        "as a strong guide, but never pad with filler to reach it."
    )


def _maxlen_clause(max_length_ms: int | None) -> str:
    if max_length_ms is None:
        return ""
    return (
        f"Hard limit: the kept segments must total no more than "
        f"{format_length(max_length_ms)} of source video."
    )


@dataclass
class TextResult:
    key_points: list[str]
    summary: str
    chosen_ratio: float | None
    rationale: str


@dataclass
class VideoSelection:
    ranges: list[tuple[int, int]]
    chosen_ratio: float | None
    rationale: str


def _validate_text(data: dict) -> TextResult:
    if not isinstance(data, dict):
        raise ValueError("not an object")
    kp = data.get("key_points")
    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("missing summary")
    if not isinstance(kp, list) or not all(isinstance(x, str) for x in kp):
        raise ValueError("bad key_points")
    return TextResult(
        key_points=[x.strip() for x in kp if x.strip()],
        summary=summary.strip(),
        chosen_ratio=_opt_float(data.get("chosen_ratio")),
        rationale=str(data.get("rationale", "")).strip(),
    )


def _make_video_validator(n_cues: int, lo: int = 0, hi: int | None = None):
    """Validate segments. Indices are clamped into [lo, hi] so a chunked window
    never yields a span pointing at an unrelated part of the timeline.
    """
    hi = (n_cues - 1) if hi is None else hi

    def _validate(data: dict) -> VideoSelection:
        segs = data.get("segments")
        if not isinstance(segs, list) or not segs:
            raise ValueError("no segments")
        ranges: list[tuple[int, int]] = []
        for s in segs:
            if not isinstance(s, dict) or "first_cue" not in s or "last_cue" not in s:
                raise ValueError("bad segment")
            first, last = s["first_cue"], s["last_cue"]
            if not isinstance(first, int) or not isinstance(last, int):
                raise ValueError("non-int cue index")
            first = max(lo, min(first, hi))
            last = max(lo, min(last, hi))
            ranges.append((first, last))
        return VideoSelection(
            ranges=ranges,
            chosen_ratio=_opt_float(data.get("chosen_ratio")),
            rationale=str(data.get("rationale", "")).strip(),
        )

    return _validate


def _opt_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _metadata_header(channel: str, title: str) -> str:
    return f"TITLE: {title}\nCHANNEL: {channel}\n\n"


def format_cues_for_selection(cues: list[Cue]) -> str:
    return "\n".join(
        f"[{i}] ({to_ffmpeg_ts(c.start_ms)}) {c.text}" for i, c in enumerate(cues)
    )


def _chunk(seq: list, size: int) -> list[list]:
    return [seq[i : i + size] for i in range(0, len(seq), size)]


def summarize_text(
    cues: list[Cue], channel: str, title: str, ratio: float | None, *, timeout: float
) -> TextResult:
    header = _metadata_header(channel, title)
    full = " ".join(c.text for c in cues)
    if len(full) <= SINGLE_PASS_CHARS:
        prompt = _TEXT_PROMPT.format(ratio_clause=_ratio_clause(ratio))
        return ask_json(prompt, header + full, validate=_validate_text, timeout=timeout)

    # Map-reduce: summarize chunks, then reduce.
    per_chunk = max(1, SINGLE_PASS_CHARS // 2)
    chunks, buf = [], ""
    for c in cues:
        if len(buf) + len(c.text) > per_chunk and buf:
            chunks.append(buf)
            buf = ""
        buf += c.text + " "
    if buf:
        chunks.append(buf)

    partials = []
    map_prompt = _TEXT_PROMPT.format(ratio_clause=_ratio_clause(None))
    for part in chunks:
        res = ask_json(map_prompt, header + part, validate=_validate_text, timeout=timeout)
        partials.append("- " + "\n- ".join(res.key_points) + "\n\n" + res.summary)

    reduce_prompt = _TEXT_REDUCE_PROMPT.format(ratio_clause=_ratio_clause(ratio))
    return ask_json(
        reduce_prompt, header + "\n\n---\n\n".join(partials),
        validate=_validate_text, timeout=timeout,
    )


def select_video_segments(
    cues: list[Cue],
    channel: str,
    title: str,
    ratio: float | None,
    max_length_ms: int | None,
    *,
    timeout: float,
) -> VideoSelection:
    header = _metadata_header(channel, title)
    prompt = _VIDEO_PROMPT.format(
        ratio_clause=_ratio_clause(ratio), maxlen_clause=_maxlen_clause(max_length_ms)
    )
    listing = format_cues_for_selection(cues)
    if len(listing) <= SINGLE_PASS_CHARS:
        return ask_json(
            prompt, header + listing,
            validate=_make_video_validator(len(cues)), timeout=timeout,
        )

    # Chunk cues but keep GLOBAL indices so spans stay on one timeline.
    approx_per = max(1, len(cues) * SINGLE_PASS_CHARS // max(1, len(listing)))
    all_ranges: list[tuple[int, int]] = []
    ratios: list[float] = []
    for group in _chunk(list(range(len(cues))), approx_per):
        sub_listing = "\n".join(
            f"[{i}] ({to_ffmpeg_ts(cues[i].start_ms)}) {cues[i].text}" for i in group
        )
        sel = ask_json(
            prompt, header + sub_listing,
            validate=_make_video_validator(len(cues), lo=group[0], hi=group[-1]),
            timeout=timeout,
        )
        all_ranges.extend(sel.ranges)
        if sel.chosen_ratio is not None:
            ratios.append(sel.chosen_ratio)
    if not all_ranges:
        raise TldrError("Claude selected no segments.")
    return VideoSelection(
        ranges=all_ranges,
        chosen_ratio=(sum(ratios) / len(ratios)) if ratios else None,
        rationale="combined from chunked selection",
    )

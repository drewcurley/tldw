"""Turn Claude's cue-index selections into validated, crossfade-aware ms spans
and build the ffmpeg xfade/acrossfade filtergraph.

Selecting by cue index inherently snaps cuts to speech boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import TldrError
from .transcript import Cue


@dataclass
class Span:
    start_ms: int
    end_ms: int
    # Original (pre-padding) source start, for the on-screen timestamp. Excluded
    # from equality so existing comparisons stay valid.
    label_ms: int | None = field(default=None, compare=False)

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    @property
    def display_start_ms(self) -> int:
        """Source start to show the viewer (pre-padding when padded)."""
        return self.label_ms if self.label_ms is not None else self.start_ms


def spans_from_cue_ranges(
    ranges: list[tuple[int, int]], cues: list[Cue], *, min_clip_ms: int
) -> list[Span]:
    """Map (first_cue, last_cue) index pairs to ms spans; validate + merge.

    - clamps indices into range and orders first<=last
    - converts to ms via cue boundaries (already snapped to speech)
    - sorts, merges overlapping/adjacent spans
    - drops spans shorter than min_clip_ms (too short to crossfade cleanly)
    """
    n = len(cues)
    spans: list[Span] = []
    for first, last in ranges:
        if first > last:
            first, last = last, first
        first = max(0, min(first, n - 1))
        last = max(0, min(last, n - 1))
        spans.append(Span(cues[first].start_ms, cues[last].end_ms))

    spans = [s for s in spans if s.duration_ms > 0]
    if not spans:
        raise TldrError("Claude selected no usable segments.")
    merged = merge_spans(spans)
    kept = [s for s in merged if s.duration_ms >= min_clip_ms]
    return kept or [max(merged, key=lambda s: s.duration_ms)]


def merge_spans(spans: list[Span]) -> list[Span]:
    """Sort by start and merge overlapping/adjacent spans into fresh Span objects.

    The merged span keeps the EARLIEST original display start of its group.
    """
    ordered = sorted((s for s in spans if s.duration_ms > 0), key=lambda s: s.start_ms)
    if not ordered:
        return []
    first = ordered[0]
    merged: list[Span] = [Span(first.start_ms, first.end_ms, label_ms=first.display_start_ms)]
    for s in ordered[1:]:
        if s.start_ms <= merged[-1].end_ms:  # overlap or adjacent
            merged[-1].end_ms = max(merged[-1].end_ms, s.end_ms)
            merged[-1].label_ms = min(merged[-1].label_ms, s.display_start_ms)
        else:
            merged.append(Span(s.start_ms, s.end_ms, label_ms=s.display_start_ms))
    return merged


def pad_spans(
    spans: list[Span], cues: list[Cue], pad_ms: int, duration_ms: int
) -> list[Span]:
    """Extend each span outward ONLY into real inter-cue silence (up to pad_ms).

    Protects the audio crossfade from clipping words: the dissolve lands on the
    natural pause around a sentence, never on adjacent speech. Records the original
    start as the display label, then merges. No-op where speech is continuous.
    """
    if pad_ms <= 0 or not cues:
        return merge_spans([Span(s.start_ms, s.end_ms, label_ms=s.display_start_ms)
                            for s in spans])
    ends = sorted(c.end_ms for c in cues)
    starts = sorted(c.start_ms for c in cues)
    padded: list[Span] = []
    for s in spans:
        prev_end = max((e for e in ends if e <= s.start_ms), default=0)
        head = min(pad_ms, max(0, s.start_ms - prev_end))
        next_start = min((st for st in starts if st >= s.end_ms), default=duration_ms)
        tail = min(pad_ms, max(0, next_start - s.end_ms))
        padded.append(Span(max(0, s.start_ms - head),
                           min(duration_ms, s.end_ms + tail),
                           label_ms=s.display_start_ms))
    return merge_spans(padded)


def enforce_max_length(
    spans: list[Span], max_total_ms: int | None, xfade_ms: int
) -> list[Span]:
    """Deterministically cap total *rendered* length (post-crossfade).

    Keeps chronological order, greedily dropping trailing spans until the
    crossfade-aware total fits. --max-length is a hard cap and wins over --ratio.
    """
    if max_total_ms is None:
        return spans
    kept: list[Span] = []
    for s in spans:
        trial = kept + [s]
        if rendered_duration_ms(trial, xfade_ms) <= max_total_ms or not kept:
            kept = trial
        else:
            break
    return kept


def rendered_duration_ms(spans: list[Span], xfade_ms: int) -> int:
    """Authoritative output length: sum(clips) - (N-1)*xfade. Never trust the LLM."""
    if not spans:
        return 0
    total = sum(s.duration_ms for s in spans)
    return total - (len(spans) - 1) * xfade_ms


def xfade_offsets_ms(clip_durations_ms: list[int], xfade_ms: int) -> list[int]:
    """Cumulative absolute offsets for chained xfade (one per transition, N-1)."""
    offsets: list[int] = []
    running = 0
    for i in range(1, len(clip_durations_ms)):
        running += clip_durations_ms[i - 1]
        offsets.append(running - i * xfade_ms)
    return offsets


def build_xfade_chain(
    clip_durations_ms: list[int], joins: list[tuple[int, str]]
) -> tuple[str, str, str]:
    """Chain xfade/acrossfade with per-join (duration_ms, transition).

    `joins` has length N-1. Offsets are cumulative:
    L0 = d0; L_{i+1} = L_i + d_{i+1} - t_i; offset_i = L_i - t_i.
    Returns (filtergraph, video_label, audio_label).
    """
    n = len(clip_durations_ms)
    if n < 2:
        raise ValueError("build_xfade_chain requires >= 2 clips")
    if len(joins) != n - 1:
        raise ValueError("joins must have length len(clips)-1")

    parts: list[str] = []
    vprev, aprev = "[0:v]", "[0:a]"
    running = clip_durations_ms[0]
    for i in range(1, n):
        dur_ms, transition = joins[i - 1]
        d = dur_ms / 1000.0
        offset = (running - dur_ms) / 1000.0
        vout = f"[v{i}]" if i < n - 1 else "[vout]"
        aout = f"[a{i}]" if i < n - 1 else "[aout]"
        parts.append(
            f"{vprev}[{i}:v]xfade=transition={transition}:"
            f"duration={d:.3f}:offset={offset:.3f}{vout}"
        )
        parts.append(f"{aprev}[{i}:a]acrossfade=d={d:.3f}:c1=tri:c2=tri{aout}")
        vprev, aprev = vout, aout
        running = running + clip_durations_ms[i] - dur_ms
    return ";".join(parts), "[vout]", "[aout]"


def build_xfade_filter(
    clip_durations_ms: list[int], xfade_ms: int, *, transition: str = "fade"
) -> tuple[str, str, str]:
    """Build a filter_complex chaining xfade (video) + acrossfade (audio).

    Returns (filtergraph, video_label, audio_label). Assumes each input i has
    streams [i:v] and [i:a] and all clips share codec params (callers normalize).
    """
    n = len(clip_durations_ms)
    if n < 2:
        raise ValueError("build_xfade_filter requires >= 2 clips")
    d = xfade_ms / 1000.0
    offsets = [o / 1000.0 for o in xfade_offsets_ms(clip_durations_ms, xfade_ms)]

    parts: list[str] = []
    vprev, aprev = "[0:v]", "[0:a]"
    for i in range(1, n):
        vout = f"[v{i}]" if i < n - 1 else "[vout]"
        aout = f"[a{i}]" if i < n - 1 else "[aout]"
        parts.append(
            f"{vprev}[{i}:v]xfade=transition={transition}:"
            f"duration={d:.3f}:offset={offsets[i - 1]:.3f}{vout}"
        )
        parts.append(f"{aprev}[{i}:a]acrossfade=d={d:.3f}:c1=tri:c2=tri{aout}")
        vprev, aprev = vout, aout
    return ";".join(parts), "[vout]", "[aout]"

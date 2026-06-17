import pytest

from youtube_tldw import TldrError
from youtube_tldw.spans import (
    Span,
    build_xfade_chain,
    build_xfade_filter,
    enforce_max_length,
    merge_spans,
    rendered_duration_ms,
    spans_from_cue_ranges,
    xfade_offsets_ms,
)
from youtube_tldw.transcript import Cue


def _cues(n, step=1000):
    return [Cue(i * step, i * step + step, f"c{i}") for i in range(n)]


def test_spans_from_ranges_clamp_and_order():
    cues = _cues(10)
    spans = spans_from_cue_ranges([(8, 3), (-5, 1), (100, 200)], cues, min_clip_ms=0)
    # (8,3)->(3000,9000); (-5,1)->(0,2000); (100,200)->(9000,10000)
    # after sort + adjacent-merge of the last two: (0,2000) and (3000,10000)
    assert spans == [Span(0, 2000), Span(3000, 10000)]


def test_spans_merge_adjacent():
    cues = _cues(10)
    spans = spans_from_cue_ranges([(0, 2), (3, 5), (2, 3)], cues, min_clip_ms=0)
    assert len(spans) == 1
    assert spans[0].start_ms == 0 and spans[0].end_ms == 6000


def test_min_clip_filter_keeps_longest_when_all_short():
    cues = _cues(4, step=200)
    spans = spans_from_cue_ranges([(0, 0), (2, 2)], cues, min_clip_ms=10_000)
    assert len(spans) == 1


def test_no_spans_raises():
    with pytest.raises(TldrError):
        spans_from_cue_ranges([], _cues(3), min_clip_ms=0)


def test_rendered_duration_subtracts_crossfade():
    spans = [Span(0, 5000), Span(10000, 13000), Span(20000, 22000)]
    # 5000+3000+2000 - 2*400 = 9200
    assert rendered_duration_ms(spans, 400) == 9200
    assert rendered_duration_ms([Span(0, 5000)], 400) == 5000
    assert rendered_duration_ms([], 400) == 0


def test_enforce_max_length_drops_trailing():
    spans = [Span(0, 5000), Span(10000, 13000), Span(20000, 25000)]
    kept = enforce_max_length(spans, 8000, 400)
    # 5000 + 3000 - 400 = 7600 <= 8000; adding the third blows the cap.
    assert kept == spans[:2]
    assert rendered_duration_ms(kept, 400) <= 8000


def test_enforce_keeps_oversized_first_span_over_budget():
    # Cap cannot be honored; the contract is to keep one span anyway.
    spans = [Span(0, 5000), Span(10000, 13000)]
    kept = enforce_max_length(spans, 3000, 400)
    assert kept == [Span(0, 5000)]
    assert rendered_duration_ms(kept, 400) == 5000  # exceeds the 3000 cap


def test_enforce_no_cap_returns_all():
    spans = [Span(0, 5000), Span(10000, 13000)]
    assert enforce_max_length(spans, None, 400) == spans


def test_xfade_offsets():
    # clips 5000, 3000, 2000 ; T=400
    offs = xfade_offsets_ms([5000, 3000, 2000], 400)
    assert offs == [5000 - 400, 5000 + 3000 - 800]


def test_build_xfade_filter_structure():
    graph, v, a = build_xfade_filter([5000, 3000, 2000], 400)
    assert v == "[vout]" and a == "[aout]"
    assert graph.count("xfade") == 2
    assert graph.count("acrossfade") == 2
    assert "offset=4.600" in graph  # first offset 4600ms
    assert "[vout]" in graph and "[aout]" in graph


def test_build_xfade_requires_two():
    with pytest.raises(ValueError):
        build_xfade_filter([5000], 400)


def test_build_xfade_chain_mixed_joins():
    # content xfade 400 then fade-to-black 600 to an end card.
    durs = [3000, 2000, 2500]
    joins = [(400, "fade"), (600, "fadeblack")]
    graph, v, a = build_xfade_chain(durs, joins)
    assert v == "[vout]" and a == "[aout]"
    assert "transition=fade:" in graph and "transition=fadeblack:" in graph
    # offset0 = 3000-400 = 2600ms; offset1 = (3000+2000-400)-600 = 4000ms
    assert "offset=2.600" in graph and "offset=4.000" in graph


def test_build_xfade_chain_validates_lengths():
    with pytest.raises(ValueError):
        build_xfade_chain([3000], [])
    with pytest.raises(ValueError):
        build_xfade_chain([3000, 2000], [(400, "fade"), (600, "fadeblack")])


def test_merge_spans_overlap_and_adjacent():
    merged = merge_spans([Span(6000, 10000), Span(0, 4000), Span(4000, 6000)])
    assert merged == [Span(0, 10000)]


def test_merge_spans_disjoint_preserved():
    merged = merge_spans([Span(0, 2000), Span(5000, 7000)])
    assert merged == [Span(0, 2000), Span(5000, 7000)]


def test_merge_spans_returns_fresh_objects():
    original = Span(0, 1000)
    merged = merge_spans([original])
    merged[0].end_ms = 9999
    assert original.end_ms == 1000  # not mutated

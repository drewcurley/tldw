import pytest

from youtube_tldw import summarize
from youtube_tldw.summarize import (
    TextResult,
    VideoSelection,
    _make_video_validator,
    _validate_text,
    format_cues_for_selection,
)
from youtube_tldw.transcript import Cue


def _cues(n):
    return [Cue(i * 1000, i * 1000 + 1000, f"word{i}") for i in range(n)]


def test_validate_text_ok():
    r = _validate_text(
        {"key_points": ["a", " "], "summary": "body", "chosen_ratio": "0.2",
         "rationale": "x"}
    )
    assert isinstance(r, TextResult)
    assert r.key_points == ["a"]
    assert r.chosen_ratio == 0.2


@pytest.mark.parametrize(
    "data",
    [
        {"key_points": ["a"]},                       # no summary
        {"summary": "x", "key_points": "nope"},      # bad key_points
        {"summary": "", "key_points": []},           # empty summary
    ],
)
def test_validate_text_bad(data):
    with pytest.raises(ValueError):
        _validate_text(data)


def test_video_validator_ok():
    v = _make_video_validator(10)(
        {"segments": [{"first_cue": 0, "last_cue": 3}], "chosen_ratio": 0.3}
    )
    assert isinstance(v, VideoSelection)
    assert v.ranges == [(0, 3)]


@pytest.mark.parametrize(
    "data",
    [
        {"segments": []},
        {"segments": [{"first_cue": 0}]},                   # missing last
        {"segments": [{"first_cue": "a", "last_cue": 1}]},  # non-int
    ],
)
def test_video_validator_bad(data):
    with pytest.raises(ValueError):
        _make_video_validator(10)(data)


def test_video_validator_clamps_out_of_range():
    v = _make_video_validator(10)(
        {"segments": [{"first_cue": -5, "last_cue": 99}]}
    )
    assert v.ranges == [(0, 9)]  # clamped into [0, n-1]


def test_video_prompt_demands_sentence_boundaries():
    from youtube_tldw.summarize import _VIDEO_PROMPT
    p = _VIDEO_PROMPT.lower()
    assert "complete sentence" in p
    assert "mid-sentence" in p
    assert "first word" in p and "final word" in p


def test_text_prompts_forbid_abbreviations():
    from youtube_tldw.summarize import _TEXT_PROMPT, _TEXT_REDUCE_PROMPT
    for p in (_TEXT_PROMPT, _TEXT_REDUCE_PROMPT):
        low = p.lower()
        assert "abbreviation" in low
        assert "world war two" in low  # the explicit example


def test_format_cues_listing():
    listing = format_cues_for_selection(_cues(2))
    assert listing.splitlines()[0].startswith("[0] (00:00:00.000) word0")


def test_summarize_text_single_pass(monkeypatch):
    seen = {}

    def fake_ask(prompt, payload, *, validate, timeout, step=None):
        seen["payload"] = payload
        return validate({"key_points": ["k"], "summary": "s", "chosen_ratio": 0.2})

    monkeypatch.setattr(summarize, "ask_json", fake_ask)
    r = summarize.summarize_text(_cues(3), "Chan", "Title", 0.25, timeout=1)
    assert r.summary == "s"
    assert "TITLE: Title" in seen["payload"]
    assert "CHANNEL: Chan" in seen["payload"]


def test_select_video_single_pass(monkeypatch):
    def fake_ask(prompt, payload, *, validate, timeout, step=None):
        return validate({"segments": [{"first_cue": 0, "last_cue": 1}]})

    monkeypatch.setattr(summarize, "ask_json", fake_ask)
    sel = summarize.select_video_segments(_cues(3), "C", "T", None, None, timeout=1)
    assert sel.ranges == [(0, 1)]


def test_select_video_on_segment_found_fires_streaming(monkeypatch):
    """on_segment_found is called for each segment in the streaming path."""
    found = []

    def fake_stream(prompt, stdin, *, on_segment, timeout, step=None):
        on_segment({"first_cue": 0, "last_cue": 1, "reason": "a"})
        on_segment({"first_cue": 2, "last_cue": 2, "reason": "b"})
        return {"chosen_ratio": 0.3, "rationale": "ok"}

    monkeypatch.setattr(summarize, "stream_ndjson_segments", fake_stream)
    monkeypatch.setattr(summarize, "is_claude_cli", lambda: True)

    summarize.select_video_segments(
        _cues(5), "C", "T", None, None, timeout=1,
        on_progress=lambda m, p=None: None,
        on_segment_found=lambda first, last: found.append((first, last)),
    )
    assert found == [(0, 1), (2, 2)]


def test_select_video_chunked_clamps_to_window(monkeypatch):
    # Force chunking; Claude echoes an out-of-window index that must be clamped
    # to the chunk's own range so spans never point at an unrelated timeline part.
    monkeypatch.setattr(summarize, "SINGLE_PASS_CHARS", 5)

    def fake_ask(prompt, payload, *, validate, timeout, step=None):
        return validate({"segments": [{"first_cue": 0, "last_cue": 999}]})

    monkeypatch.setattr(summarize, "ask_json", fake_ask)
    sel = summarize.select_video_segments(_cues(6), "C", "T", None, None, timeout=1)
    assert len(sel.ranges) > 1  # chunked into multiple windows
    # every index stays within the real cue range (no 999 leaked through)
    for first, last in sel.ranges:
        assert 0 <= first <= 5 and 0 <= last <= 5


def test_summarize_text_map_reduce(monkeypatch):
    # Force map-reduce by shrinking the single-pass threshold.
    monkeypatch.setattr(summarize, "SINGLE_PASS_CHARS", 5)
    calls = {"n": 0}

    def fake_ask(prompt, payload, *, validate, timeout, step=None):
        calls["n"] += 1
        return validate({"key_points": ["k"], "summary": "s"})

    monkeypatch.setattr(summarize, "ask_json", fake_ask)
    r = summarize.summarize_text(_cues(6), "C", "T", 0.2, timeout=1)
    assert r.summary == "s"
    assert calls["n"] >= 2  # at least one map + one reduce


# --- streaming summary --------------------------------------------------------------

def _stream_lines(monkeypatch, lines, seen=None):
    def fake(prompt, payload, *, on_obj, timeout, step=""):
        if seen is not None:
            seen["prompt"], seen["step"] = prompt, step
        for obj in lines:
            on_obj(obj)
    monkeypatch.setattr(summarize, "stream_ndjson", fake)


def _no_buffered(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("fell back to the buffered call")
    monkeypatch.setattr(summarize, "ask_json", boom)


def test_streaming_summary_reports_pieces_as_they_arrive(monkeypatch):
    seen = {}
    _stream_lines(monkeypatch, [
        {"key_point": "Neurons hold a number"},
        {"key_point": "Layers pass activations forward"},
        {"paragraph": "A network is layers of neurons."},
        {"paragraph": "It learns by adjusting weights."},
        {"chosen_ratio": 0.2, "rationale": "dense"},
    ], seen)
    _no_buffered(monkeypatch)
    partials = []
    res = summarize.summarize_text(_cues(5), "C", "T", None, timeout=1,
                                   on_partial=partials.append)
    assert [p["kind"] for p in partials] == ["key_point", "key_point",
                                             "paragraph", "paragraph"]
    assert res.key_points == ["Neurons hold a number", "Layers pass activations forward"]
    assert res.summary == "A network is layers of neurons.\n\nIt learns by adjusting weights."
    assert res.chosen_ratio == 0.2 and res.rationale == "dense"
    assert "WRITE FOR THE EAR" in seen["prompt"]          # same rules as batch
    assert '{"key_point"' in seen["prompt"] and seen["step"] == "summarize"


def test_streaming_that_never_produces_a_summary_falls_back(monkeypatch):
    """A model that ignores the line format costs one retry, not a broken result."""
    _stream_lines(monkeypatch, [{"key_point": "only a point"}])
    calls = []
    monkeypatch.setattr(summarize, "ask_json",
                        lambda *a, **k: calls.append(1) or summarize.TextResult(
                            ["k"], "buffered body", 0.3, "r"))
    progress = []
    res = summarize.summarize_text(_cues(5), "C", "T", None, timeout=1,
                                   on_partial=lambda _p: None,
                                   on_progress=lambda m, p=None: progress.append(m))
    assert calls == [1] and res.summary == "buffered body"
    assert any("reformatting" in m for m in progress)


def test_streaming_does_not_retry_a_real_failure(monkeypatch):
    """A logged-out CLI or a timeout would fail the retry identically."""
    def fail(*a, **k):
        raise summarize.TldrError("claude: Not logged in")
    monkeypatch.setattr(summarize, "stream_ndjson", fail)
    _no_buffered(monkeypatch)
    with pytest.raises(summarize.TldrError, match="Not logged in"):
        summarize.summarize_text(_cues(5), "C", "T", None, timeout=1,
                                 on_partial=lambda _p: None)


def test_custom_backends_get_the_buffered_summary(monkeypatch):
    def cannot(*a, **k):
        raise NotImplementedError
    monkeypatch.setattr(summarize, "stream_ndjson", cannot)
    monkeypatch.setattr(summarize, "ask_json",
                        lambda *a, **k: summarize.TextResult(["k"], "body", None, ""))
    res = summarize.summarize_text(_cues(5), "C", "T", None, timeout=1,
                                   on_partial=lambda _p: None)
    assert res.summary == "body"


def test_no_on_partial_means_the_original_buffered_path(monkeypatch):
    """The CLI and anything else that doesn't ask for streaming is unchanged."""
    def never(*a, **k):
        raise AssertionError("streamed without being asked to")
    monkeypatch.setattr(summarize, "stream_ndjson", never)
    monkeypatch.setattr(summarize, "ask_json",
                        lambda p, *a, **k: summarize.TextResult(["k"], "b", None, ""))
    assert summarize.summarize_text(_cues(5), "C", "T", None, timeout=1).summary == "b"


def test_streamed_headings_become_markdown_sections(monkeypatch):
    """The summary is written in titled sections. The streaming format asked for
    "one paragraph per line", so the model stopped emitting them entirely."""
    _stream_lines(monkeypatch, [
        {"key_point": "a point"},
        {"heading": "The problem"},
        {"paragraph": "Recognizing digits is hard."},
        {"heading": "## Neurons"},          # already hashed: not double-hashed
        {"paragraph": "A neuron holds a number."},
        {"chosen_ratio": 0.2, "rationale": "dense"},
    ])
    _no_buffered(monkeypatch)
    partials = []
    res = summarize.summarize_text(_cues(5), "C", "T", None, timeout=1,
                                   on_partial=partials.append)
    assert res.summary == ("## The problem\n\nRecognizing digits is hard.\n\n"
                           "## Neurons\n\nA neuron holds a number.")
    # The client renders headings from the same markdown, so they stream as blocks.
    assert [p["text"] for p in partials if p["kind"] == "paragraph"][0] == "## The problem"


def test_stream_prompt_asks_for_sections():
    p = summarize._TEXT_PROMPT_STREAM.format(ratio_clause="")
    assert '"heading"' in p and "sections" in p

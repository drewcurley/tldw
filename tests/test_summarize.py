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

    def fake_ask(prompt, payload, *, validate, timeout):
        seen["payload"] = payload
        return validate({"key_points": ["k"], "summary": "s", "chosen_ratio": 0.2})

    monkeypatch.setattr(summarize, "ask_json", fake_ask)
    r = summarize.summarize_text(_cues(3), "Chan", "Title", 0.25, timeout=1)
    assert r.summary == "s"
    assert "TITLE: Title" in seen["payload"]
    assert "CHANNEL: Chan" in seen["payload"]


def test_select_video_single_pass(monkeypatch):
    def fake_ask(prompt, payload, *, validate, timeout):
        return validate({"segments": [{"first_cue": 0, "last_cue": 1}]})

    monkeypatch.setattr(summarize, "ask_json", fake_ask)
    sel = summarize.select_video_segments(_cues(3), "C", "T", None, None, timeout=1)
    assert sel.ranges == [(0, 1)]


def test_select_video_chunked_clamps_to_window(monkeypatch):
    # Force chunking; Claude echoes an out-of-window index that must be clamped
    # to the chunk's own range so spans never point at an unrelated timeline part.
    monkeypatch.setattr(summarize, "SINGLE_PASS_CHARS", 5)

    def fake_ask(prompt, payload, *, validate, timeout):
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

    def fake_ask(prompt, payload, *, validate, timeout):
        calls["n"] += 1
        return validate({"key_points": ["k"], "summary": "s"})

    monkeypatch.setattr(summarize, "ask_json", fake_ask)
    r = summarize.summarize_text(_cues(6), "C", "T", 0.2, timeout=1)
    assert r.summary == "s"
    assert calls["n"] >= 2  # at least one map + one reduce

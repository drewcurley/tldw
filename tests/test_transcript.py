import json
from pathlib import Path

import pytest

from youtube_tldw import NoTranscriptError, TldrError
from youtube_tldw import transcript
from youtube_tldw.transcript import full_text, parse_subtitles, word_count

FIXTURES = Path(__file__).parent / "fixtures"


def test_auto_caption_dedup_and_tag_strip():
    cues = parse_subtitles((FIXTURES / "auto_captions.vtt").read_text())
    text = full_text(cues)
    # inline <c>/<timestamp> tags gone
    assert "<c>" not in text and "<00:" not in text
    # rolling duplicate "welcome back to the" should appear once
    assert text.count("welcome back to the") == 1
    # entities decoded
    assert "&amp;" not in text and "&" in text
    # cues are non-overlapping and ordered
    for a, b in zip(cues, cues[1:]):
        assert a.end_ms <= b.start_ms
        assert a.end_ms > a.start_ms


def test_manual_srt_joins_multiline_and_decodes():
    cues = parse_subtitles((FIXTURES / "manual.srt").read_text())
    assert len(cues) == 3
    # multi-line cue joined with a space, not a newline
    assert cues[0].text == "Hello and welcome to the show. Today we cover three ideas."
    assert "&amp;" not in cues[1].text
    assert "&" in cues[1].text
    assert word_count(cues) > 0


def test_empty_raises():
    with pytest.raises(TldrError):
        parse_subtitles("WEBVTT\n\n")


# --- json3, the format the player itself requests -------------------------------

JSON3 = json.dumps({"events": [
    {"tStartMs": 0, "dDurationMs": 1200, "segs": [{"utf8": "hello "}, {"utf8": "there"}]},
    {"tStartMs": 1200, "dDurationMs": 800, "segs": [{"utf8": "second line"}]},
]})


def test_parse_json3_joins_segments():
    cues = transcript.parse_json3(JSON3)
    assert [(c.start_ms, c.end_ms, c.text) for c in cues] == [
        (0, 1200, "hello there"), (1200, 2000, "second line")]


def test_parse_json3_skips_timing_only_events():
    raw = json.dumps({"events": [
        {"tStartMs": 0},                                   # no segs at all
        {"tStartMs": 10, "dDurationMs": 5, "segs": [{"utf8": "  "}]},   # blank
        {"segs": [{"utf8": "no start"}]},                  # no timestamp
        {"tStartMs": 100, "dDurationMs": 50, "segs": [{"utf8": "kept"}]},
    ]})
    assert [c.text for c in transcript.parse_json3(raw)] == ["kept"]


def test_parse_json3_collapses_rolling_repeats():
    raw = json.dumps({"events": [
        {"tStartMs": 0, "dDurationMs": 100, "segs": [{"utf8": "same"}]},
        {"tStartMs": 100, "dDurationMs": 100, "segs": [{"utf8": "same"}]},
        {"tStartMs": 200, "dDurationMs": 100, "segs": [{"utf8": "next"}]},
    ]})
    cues = transcript.parse_json3(raw)
    assert [c.text for c in cues] == ["same", "next"]
    assert cues[0].end_ms == 200                  # the repeat extended it


@pytest.mark.parametrize("bad", ["", "not json", "{}", '{"events": []}',
                                 '{"events": "nope"}', '{"events": [{"segs": []}]}'])
def test_parse_json3_rejects_unusable_input(bad):
    with pytest.raises(NoTranscriptError):
        transcript.parse_json3(bad)


def test_parse_captions_picks_the_parser():
    assert transcript.parse_captions(JSON3, "json3")[0].text == "hello there"
    assert transcript.parse_captions(JSON3)[0].text == "hello there"      # sniffed
    vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nfrom vtt\n"
    assert transcript.parse_captions(vtt, None)[0].text == "from vtt"


# --- the transcript panel, as the extension reads it off the page ---------------
#
# The panel is the only copy of the captions YouTube still serves: timedtext answers
# 429 to yt-dlp and 200-with-nothing to the page itself. Its rows carry a start and
# the words, and nothing else, so the ends are reconstructed here.

def _rows(*pairs):
    return [{"start_ms": ms, "text": text} for ms, text in pairs]


def test_panel_rows_run_each_cue_up_to_the_next():
    cues = transcript.parse_panel_rows(_rows((0, "one"), (5000, "two")))
    assert [(c.start_ms, c.end_ms, c.text) for c in cues] == [
        (0, 5000, "one"), (5000, 10000, "two")]


def test_the_last_cue_runs_to_the_end_of_the_video():
    """Its start is where the closing sentence begins, not where it stops."""
    cues = transcript.parse_panel_rows(_rows((0, "one"), (5000, "two")), 90_000)
    assert cues[-1].end_ms == 90_000


def test_a_duration_that_cannot_be_right_is_ignored():
    cues = transcript.parse_panel_rows(_rows((0, "one"), (60_000, "two")), 1_000)
    assert cues[-1].end_ms > cues[-1].start_ms


def test_panel_rows_are_sorted_before_they_are_paired():
    cues = transcript.parse_panel_rows(_rows((5000, "two"), (0, "one")))
    assert [c.text for c in cues] == ["one", "two"]
    assert cues[0].end_ms == 5000


def test_panel_whitespace_is_collapsed():
    assert transcript.parse_panel_rows(_rows((0, " one\n  two "))) [0].text == "one two"


@pytest.mark.parametrize("row", [
    "a string", 12345, None, [],
    {"text": "no start"},
    {"start_ms": "0", "text": "start is a string"},
    {"start_ms": True, "text": "a bool is not a time"},
    {"start_ms": -1, "text": "before the video"},
    {"start_ms": 25 * 3600 * 1000, "text": "after any video"},
    {"start_ms": 0, "text": ""},
    {"start_ms": 0, "text": "   "},
    {"start_ms": 0, "text": 5},
])
def test_unusable_panel_rows_are_dropped_not_repaired(row):
    """All of this arrives over HTTP from the page, so none of it is trusted."""
    cues = transcript.parse_panel_rows([row, {"start_ms": 1000, "text": "good"}])
    assert [c.text for c in cues] == ["good"]


def test_nothing_usable_returns_nothing_rather_than_raising():
    """The server reads this as 'fetch it properly instead'."""
    assert transcript.parse_panel_rows([]) == []
    assert transcript.parse_panel_rows([{"start_ms": 0, "text": ""}]) == []


def test_a_flood_of_rows_is_capped():
    rows = _rows(*[(i * 100, f"line {i}") for i in range(transcript.MAX_PANEL_ROWS + 50)])
    assert len(transcript.parse_panel_rows(rows)) <= transcript.MAX_PANEL_ROWS


def test_a_very_long_row_is_truncated():
    cues = transcript.parse_panel_rows(_rows((0, "x" * 5000)))
    assert len(cues[0].text) == transcript.MAX_CUE_CHARS


def test_two_rows_sharing_a_start_still_produce_usable_spans():
    """_normalize has to leave every cue with somewhere to cut."""
    cues = transcript.parse_panel_rows(_rows((0, "one"), (0, "two"), (5000, "three")))
    assert all(c.end_ms > c.start_ms for c in cues)

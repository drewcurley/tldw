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

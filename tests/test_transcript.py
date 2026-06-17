from pathlib import Path

import pytest

from youtube_tldr import TldrError
from youtube_tldr.transcript import full_text, parse_subtitles, word_count

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

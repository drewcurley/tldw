import pytest

from youtube_tldr import TldrError
from youtube_tldr.timing import (
    format_length,
    parse_cue_ts,
    parse_duration,
    read_time_label,
    to_ffmpeg_ts,
)


@pytest.mark.parametrize(
    "text,ms",
    [
        ("00:00:01.500", 1500),
        ("01:02:03.250", 3_723_250),
        ("02:05.000", 125_000),       # VTT optional hours
        ("00:00:01,250", 1250),       # SRT comma
        ("00:00:00.1", 100),          # short ms padded
    ],
)
def test_parse_cue_ts(text, ms):
    assert parse_cue_ts(text) == ms


@pytest.mark.parametrize(
    "text,ms",
    [
        ("90", 90_000),
        ("90s", 90_000),
        ("5m", 300_000),
        ("1m30s", 90_000),
        ("1h2m3s", 3_723_000),
        ("1:30", 90_000),
        ("1:02:03", 3_723_000),
    ],
)
def test_parse_duration(text, ms):
    assert parse_duration(text) == ms


@pytest.mark.parametrize("bad", ["", "abc", "5x", "1:2:3:4"])
def test_parse_duration_invalid(bad):
    with pytest.raises(TldrError):
        parse_duration(bad)


def test_roundtrip_ffmpeg_ts():
    assert to_ffmpeg_ts(3_723_250) == "01:02:03.250"
    assert to_ffmpeg_ts(-5) == "00:00:00.000"


@pytest.mark.parametrize(
    "ms,label",
    [(45_000, "45s"), (222_000, "3m42s"), (3_723_000, "1h2m3s")],
)
def test_format_length(ms, label):
    assert format_length(ms) == label


def test_read_time_label():
    assert read_time_label(400) == "2m"
    assert read_time_label(10) == "1m"  # floor of 1

from youtube_tldw import textmode
from youtube_tldw.metadata import VideoMeta
from youtube_tldw.summarize import TextResult


def _meta():
    return VideoMeta("dQw4w9WgXcQ", "My Video", "My Channel", 600_000, {}, {})


def test_render_full():
    r = TextResult(["point one", "point two"], "The body.", 0.2, "because dense")
    md = textmode.render_markdown(_meta(), r)
    assert "# TL;DW — My Video" in md
    assert "**Channel:** My Channel" in md
    assert "- point one" in md
    assert "The body." in md
    assert "_because dense_" in md
    assert "youtube.com/watch?v=dQw4w9WgXcQ" in md


def test_render_empty_key_points_and_no_rationale():
    r = TextResult([], "Body only.", None, "")
    md = textmode.render_markdown(_meta(), r)
    assert "- (none extracted)" in md
    assert "_" not in md.split("## Summary")[1]  # no rationale blockquote


def test_length_label():
    r = TextResult(["a b c"], " ".join(["word"] * 397), None, "")
    # 400 words / 200 wpm = 2m
    assert textmode.length_label(r) == "2m"

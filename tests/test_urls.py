import pytest

from youtube_tldw import TldrError
from youtube_tldw.urls import canonical_video_id

VID = "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VID}",
        f"https://youtube.com/watch?v={VID}&t=10s",
        f"https://m.youtube.com/watch?v={VID}",
        f"https://youtu.be/{VID}",
        f"https://www.youtube.com/shorts/{VID}",
        f"https://www.youtube.com/embed/{VID}",
        f"https://www.youtube.com/live/{VID}",
        VID,                 # bare 11-char video id
        f"  {VID}  ",        # bare id with surrounding whitespace
    ],
)
def test_accepts_valid(url):
    assert canonical_video_id(url) == VID


def test_real_example_inputs():
    assert canonical_video_id("https://www.youtube.com/watch?v=86QbFlOHuTs") == "86QbFlOHuTs"
    assert canonical_video_id("86QbFlOHuTs") == "86QbFlOHuTs"


@pytest.mark.parametrize(
    "url",
    [
        "http://www.youtube.com/watch?v=" + VID,        # not https
        "https://vimeo.com/12345",                       # wrong host
        "https://evil.com/watch?v=" + VID,               # wrong host
        "file:///etc/passwd",                            # local path
        "https://www.youtube.com/playlist?list=PL123",   # playlist, no video
        "https://www.youtube.com/watch?v=short",         # bad id
        "",                                              # empty
        "https://www.youtube.com/watch?v=" + VID + "extra",  # id too long
    ],
)
def test_rejects_invalid(url):
    with pytest.raises(TldrError):
        canonical_video_id(url)

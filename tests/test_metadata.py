import json

import pytest

from youtube_tldw import TldrError, metadata
from youtube_tldw.metadata import VideoMeta, choose_track
from youtube_tldw.proc import ProcResult

VID = "dQw4w9WgXcQ"


def _meta(subs=None, auto=None):
    return VideoMeta(VID, "t", "c", 1000, subs or {}, auto or {})


def test_choose_prefers_manual_exact():
    m = _meta(subs={"en": [{}], "fr": [{}]}, auto={"en": [{}]})
    assert choose_track(m, "en") == ("en", False)


def test_choose_manual_prefix():
    m = _meta(subs={"en-US": [{}]}, auto={"en": [{}]})
    assert choose_track(m, "en") == ("en-US", False)


def test_choose_auto_exact_when_no_manual_match():
    m = _meta(subs={"fr": [{}]}, auto={"en": [{}]})
    assert choose_track(m, "en") == ("en", True)


def test_choose_auto_prefix():
    m = _meta(auto={"en-orig": [{}]})
    assert choose_track(m, "en") == ("en-orig", True)


def test_choose_falls_back_to_any_manual():
    m = _meta(subs={"de": [{}]}, auto={"es": [{}]})
    assert choose_track(m, "en") == ("de", False)


def test_choose_raises_when_no_captions():
    with pytest.raises(TldrError):
        choose_track(_meta(), "en")


def test_fetch_metadata_fields_and_fallbacks(monkeypatch):
    payload = {"title": "Cool", "uploader": "Up", "duration": 12.5,
               "automatic_captions": {"en": [{}]}}
    monkeypatch.setattr(metadata, "run", lambda *a, **k: ProcResult(0, json.dumps(payload), ""))
    m = metadata.fetch_metadata(VID)
    assert m.title == "Cool" and m.channel == "Up"
    assert m.duration_ms == 12500
    assert m.auto_captions == {"en": [{}]}


def test_fetch_metadata_bad_json(monkeypatch):
    monkeypatch.setattr(metadata, "run", lambda *a, **k: ProcResult(0, "not json", ""))
    with pytest.raises(TldrError):
        metadata.fetch_metadata(VID)


def test_download_subtitle_prefers_exact_track(monkeypatch, tmp_path):
    monkeypatch.setattr(metadata, "run", lambda *a, **k: None)
    (tmp_path / f"{VID}.en.vtt").write_text("WEBVTT exact")
    (tmp_path / f"{VID}.en-US.vtt").write_text("WEBVTT other")
    content = metadata.download_subtitle(VID, "en", True, tmp_path)
    assert "exact" in content


def test_download_subtitle_missing_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(metadata, "run", lambda *a, **k: None)
    with pytest.raises(TldrError):
        metadata.download_subtitle(VID, "en", False, tmp_path)


def test_download_video_prefers_exact_mp4(monkeypatch, tmp_path):
    monkeypatch.setattr(metadata, "run", lambda *a, **k: None)
    (tmp_path / f"{VID}.f399.mp4").write_bytes(b"frag")
    (tmp_path / f"{VID}.mp4").write_bytes(b"merged-bigger")
    assert metadata.download_video(VID, tmp_path).name == f"{VID}.mp4"


def test_download_video_missing_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(metadata, "run", lambda *a, **k: None)
    with pytest.raises(TldrError):
        metadata.download_video(VID, tmp_path)


# --- rate limiting ------------------------------------------------------------

def test_rate_limited_detects_youtube_throttling():
    assert metadata.rate_limited("ERROR: Unable to download: HTTP Error 429: Too Many Requests")
    assert metadata.rate_limited("too many requests")
    assert not metadata.rate_limited("ERROR: Video unavailable")
    assert not metadata.rate_limited("")
    assert not metadata.rate_limited(None)


def test_a_429_is_explained_rather_than_dumped(monkeypatch):
    """The raw yt-dlp text reads like a bug in tldw. It isn't, and it passes."""
    def boom(argv, **kw):
        raise TldrError("`yt-dlp` failed (exit 1): ERROR: HTTP Error 429: Too Many Requests")

    monkeypatch.setattr(metadata, "run", boom)
    with pytest.raises(TldrError) as exc:
        metadata.fetch_metadata("dQw4w9WgXcQ")
    assert "rate-limiting" in str(exc.value) and "wait a few minutes" in str(exc.value).lower()
    assert "yt-dlp" not in str(exc.value)


def test_other_failures_are_left_alone(monkeypatch):
    def boom(argv, **kw):
        raise TldrError("`yt-dlp` failed (exit 1): ERROR: Video unavailable")

    monkeypatch.setattr(metadata, "run", boom)
    with pytest.raises(TldrError, match="Video unavailable"):
        metadata.fetch_metadata("dQw4w9WgXcQ")


def test_both_yt_dlp_calls_retry_with_backoff():
    """A single 429 used to be an immediate hard failure."""
    for flag in ("--retries", "--extractor-retries", "--retry-sleep"):
        assert flag in metadata._RETRY
    assert any("exp=" in a for a in metadata._RETRY)

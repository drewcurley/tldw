import pytest

from youtube_tldw import TranscriptTooLongError, core
from youtube_tldw.metadata import VideoMeta
from youtube_tldw.summarize import TextResult
from youtube_tldw.transcript import Cue


def _patch(monkeypatch, cues=None):
    monkeypatch.setattr(core, "canonical_video_id", lambda u: "dQw4w9WgXcQ")
    monkeypatch.setattr(core.md, "fetch_metadata",
                        lambda vid: VideoMeta(vid, "T", "C", 1000, {}, {}))
    monkeypatch.setattr(core.md, "choose_track", lambda m, l: ("en", True))
    monkeypatch.setattr(core.md, "download_subtitle", lambda *a, **k: "ignored")
    monkeypatch.setattr(core.transcript, "parse_subtitles",
                        lambda c: cues or [Cue(0, 1000, "hello world")])


def test_summarize_url_happy(monkeypatch):
    _patch(monkeypatch)
    monkeypatch.setattr(core.summarize, "summarize_text",
                        lambda *a, **k: TextResult(["k"], "body", 0.2, "x"))
    s = core.summarize_url("https://youtu.be/dQw4w9WgXcQ")
    assert s.result.summary == "body" and s.meta.title == "T" and s.cue_count == 1


def test_summarize_url_rejects_too_long(monkeypatch):
    _patch(monkeypatch, cues=[Cue(0, 1000, "x" * 100)])
    monkeypatch.setattr(core.summarize, "summarize_text",
                        lambda *a, **k: pytest.fail("should not summarize"))
    with pytest.raises(TranscriptTooLongError):
        core.summarize_url("u", max_chars=50)


def test_select_segments(monkeypatch):
    from youtube_tldw.summarize import VideoSelection
    _patch(monkeypatch, cues=[Cue(0, 2000, "a"), Cue(2000, 4000, "b"),
                              Cue(4000, 6000, "c")])
    monkeypatch.setattr(core.summarize, "select_video_segments",
                        lambda *a, **k: VideoSelection([(0, 1)], None, "r"))
    meta, segs = core.select_segments("https://youtu.be/dQw4w9WgXcQ")
    assert segs and set(segs[0]) == {"start", "end", "label"}
    assert segs[0]["start"] < segs[0]["end"]
    assert isinstance(segs[0]["label"], str)


def test_summarize_url_cleans_tempdir(monkeypatch, tmp_path):
    _patch(monkeypatch)
    monkeypatch.setattr(core.summarize, "summarize_text",
                        lambda *a, **k: TextResult([], "b", None, ""))
    d = tmp_path / "wd"

    def fake_mkdtemp(prefix=""):
        d.mkdir()
        return str(d)

    monkeypatch.setattr(core.tempfile, "mkdtemp", fake_mkdtemp)
    core.summarize_url("u")
    assert not d.exists()  # cleaned up in finally


# --- the handoff the whole captionless path hangs off --------------------------

def test_a_captionless_video_raises_with_its_metadata_attached(monkeypatch):
    """Offering to transcribe needs the title, channel and a trusted duration, and
    re-fetching them would be a second call on the rate-limited path.

    This exercises the real fetch_transcript. Every server-level test stubs
    summarize_url wholesale, so an exception handler that was itself broken --
    NoTranscriptError wasn't imported, so the clause raised NameError and turned
    every captionless video into a 500 -- passed the entire suite.
    """
    from youtube_tldw import NoTranscriptError, metadata as md
    meta = md.VideoMeta("dQw4w9WgXcQ", "A Title", "A Channel", 600_000, {}, {})
    monkeypatch.setattr(core.md, "fetch_metadata", lambda vid: meta)

    def no_track(_meta, _lang):
        raise NoTranscriptError("This video has no subtitles or auto-captions.")

    monkeypatch.setattr(core.md, "choose_track", no_track)
    with pytest.raises(NoTranscriptError) as caught:
        core.fetch_transcript("dQw4w9WgXcQ")
    assert caught.value.meta is meta
    assert caught.value.meta.duration_ms == 600_000


def test_a_captionless_video_is_still_a_NoTranscriptError_not_a_500(monkeypatch):
    """The handler must not replace the typed error the status mapping relies on."""
    from youtube_tldw import NoTranscriptError, TldrError, metadata as md
    monkeypatch.setattr(core.md, "fetch_metadata",
                        lambda vid: md.VideoMeta(vid, "T", "C", 1000, {}, {}))
    monkeypatch.setattr(core.md, "choose_track",
                        lambda m, l: (_ for _ in ()).throw(NoTranscriptError("none")))
    with pytest.raises(TldrError):            # not NameError, not a bare Exception
        core.fetch_transcript("dQw4w9WgXcQ")

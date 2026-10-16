"""Tests for the faster-whisper audio transcription fallback."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from youtube_tldw import NoTranscriptError, TldrError
from youtube_tldw import whisper_transcribe as wt
from youtube_tldw.transcript import Cue


# ---------------------------------------------------------------------------
# is_available / require_whisper
# ---------------------------------------------------------------------------

def test_is_available_true(monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    assert wt.is_available() is True


def test_is_available_false(monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert wt.is_available() is False


def test_require_whisper_raises_when_missing(monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    with pytest.raises(TldrError, match="faster-whisper is not installed"):
        wt.require_whisper()


def test_require_whisper_passes_when_present(monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    wt.require_whisper()  # should not raise


# ---------------------------------------------------------------------------
# resolve_model
# ---------------------------------------------------------------------------

def test_resolve_model_defaults_to_small(monkeypatch):
    monkeypatch.setattr(wt.config, "get", lambda k, d=None: None)
    assert wt.resolve_model() == "small"


def test_resolve_model_from_config(monkeypatch):
    monkeypatch.setattr(wt.config, "get",
                        lambda k, d=None: "medium" if k == "whisper_model" else d)
    assert wt.resolve_model() == "medium"


def test_resolve_model_ignores_invalid_config(monkeypatch):
    monkeypatch.setattr(wt.config, "get",
                        lambda k, d=None: "notamodel" if k == "whisper_model" else d)
    assert wt.resolve_model() == "small"


# ---------------------------------------------------------------------------
# download_audio
# ---------------------------------------------------------------------------

def test_download_audio_returns_first_match(monkeypatch, tmp_path):
    (tmp_path / "abc1234.m4a").write_bytes(b"audio")

    def fake_run(argv, **kw):
        from youtube_tldw.proc import ProcResult
        return ProcResult(0, "", "")

    monkeypatch.setattr(wt, "run", fake_run)
    result = wt.download_audio("abc1234", tmp_path)
    assert result == tmp_path / "abc1234.m4a"


def test_download_audio_raises_when_no_file(monkeypatch, tmp_path):
    def fake_run(argv, **kw):
        from youtube_tldw.proc import ProcResult
        return ProcResult(0, "", "")

    monkeypatch.setattr(wt, "run", fake_run)
    with pytest.raises(TldrError, match="produced no audio"):
        wt.download_audio("abc1234", tmp_path)


# ---------------------------------------------------------------------------
# transcribe
# ---------------------------------------------------------------------------

def _make_segment(start: float, end: float, text: str):
    seg = SimpleNamespace(start=start, end=end, text=text)
    return seg


def _patch_whisper(monkeypatch, segments, duration: float = 60.0):
    """Patch faster_whisper.WhisperModel so transcribe() uses fake segments."""
    info = SimpleNamespace(duration=duration)

    class FakeModel:
        def __init__(self, *a, **kw):
            pass

        def transcribe(self, path, **kw):
            return iter(segments), info

    fake_module = SimpleNamespace(WhisperModel=FakeModel)
    monkeypatch.setitem(
        __import__("sys").modules, "faster_whisper", fake_module
    )
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())


def test_transcribe_converts_segments_to_cues(monkeypatch, tmp_path):
    segs = [
        _make_segment(0.0, 2.5, " Hello world."),
        _make_segment(2.5, 5.0, " This is a test."),
    ]
    _patch_whisper(monkeypatch, segs, duration=10.0)

    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"fake")
    cues = wt.transcribe(audio, "small")

    assert len(cues) == 2
    assert cues[0] == Cue(start_ms=0, end_ms=2500, text="Hello world.")
    assert cues[1] == Cue(start_ms=2500, end_ms=5000, text="This is a test.")


def test_transcribe_skips_empty_segments(monkeypatch, tmp_path):
    segs = [
        _make_segment(0.0, 1.0, "   "),  # whitespace only
        _make_segment(1.0, 3.0, "Real text."),
    ]
    _patch_whisper(monkeypatch, segs, duration=10.0)

    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"fake")
    cues = wt.transcribe(audio, "small")
    assert len(cues) == 1
    assert cues[0].text == "Real text."


def test_transcribe_raises_on_empty_result(monkeypatch, tmp_path):
    _patch_whisper(monkeypatch, [], duration=5.0)

    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"fake")
    with pytest.raises(NoTranscriptError, match="Whisper produced no text"):
        wt.transcribe(audio, "small")


def test_transcribe_reports_progress(monkeypatch, tmp_path):
    segs = [
        _make_segment(0.0, 30.0, "Segment one."),
        _make_segment(30.0, 60.0, "Segment two."),
    ]
    _patch_whisper(monkeypatch, segs, duration=60.0)

    progress_calls = []

    def on_progress(m, pct=None):
        progress_calls.append((m, pct))

    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"fake")
    wt.transcribe(audio, "small", on_progress=on_progress)

    # First two calls are the setup messages (pct=None), then per-segment with pct
    pcts = [p for _, p in progress_calls if p is not None]
    assert len(pcts) >= 2
    assert pcts[0] < pcts[-1]   # pct increases
    assert all(0 <= p <= 100 for p in pcts)


def test_transcribe_raises_when_whisper_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"fake")
    with pytest.raises(TldrError, match="faster-whisper is not installed"):
        wt.transcribe(audio, "small")


# ---------------------------------------------------------------------------
# Bounds and cue quality
#
# Unlike every other yt-dlp call in this project, the audio download can now be
# triggered by an HTTP request, and the transcription that follows saturates the
# CPU for minutes. Both have to be bounded before they are reachable that way.
# ---------------------------------------------------------------------------

def _argv_of(monkeypatch, tmp_path):
    """Run download_audio against a stubbed yt-dlp and return the argv it built."""
    seen = {}

    def fake_run(argv, **kw):
        from youtube_tldw.proc import ProcResult
        seen["argv"] = argv
        (tmp_path / "abc12345678.m4a").write_bytes(b"audio")
        return ProcResult(0, str(tmp_path / "abc12345678.m4a"), "")

    monkeypatch.setattr(wt, "run", fake_run)
    wt.download_audio("abc12345678", tmp_path)
    return seen["argv"]


def test_audio_download_asks_for_audio_only(monkeypatch, tmp_path):
    """`bestaudio/best` falls back to a muxed video stream -- gigabytes, not ~1MB
    a minute."""
    argv = _argv_of(monkeypatch, tmp_path)
    assert "bestaudio" in argv
    assert "bestaudio/best" not in argv


def test_audio_download_is_bounded(monkeypatch, tmp_path):
    argv = _argv_of(monkeypatch, tmp_path)
    assert "--max-filesize" in argv
    assert str(wt.MAX_AUDIO_BYTES) in argv


def test_audio_download_refuses_live_streams(monkeypatch, tmp_path):
    """A live stream downloads until the stream ends, i.e. until the timeout."""
    argv = _argv_of(monkeypatch, tmp_path)
    assert "--match-filter" in argv
    assert "!is_live" in argv


def test_audio_download_ignores_the_users_ytdlp_config(monkeypatch, tmp_path):
    """A request-triggered download must not pick up --exec from a user config."""
    assert "--ignore-config" in _argv_of(monkeypatch, tmp_path)


def test_audio_download_takes_the_path_ytdlp_reports(monkeypatch, tmp_path):
    """Globbing and taking the first match picks up `.part` files and fragments."""
    real = tmp_path / "abc12345678.m4a"

    def fake_run(argv, **kw):
        from youtube_tldw.proc import ProcResult
        real.write_bytes(b"the real audio")
        (tmp_path / "abc12345678.f251.webm").write_bytes(b"fragment")
        (tmp_path / "abc12345678.aac.part").write_bytes(b"partial")
        return ProcResult(0, f"{real}\n", "")

    monkeypatch.setattr(wt, "run", fake_run)
    assert wt.download_audio("abc12345678", tmp_path) == real


def test_a_part_file_is_never_handed_to_whisper(monkeypatch, tmp_path):
    """Falling back to the glob still has to skip partial downloads."""
    def fake_run(argv, **kw):
        from youtube_tldw.proc import ProcResult
        (tmp_path / "abc12345678.aac.part").write_bytes(b"x" * 100)
        (tmp_path / "abc12345678.m4a").write_bytes(b"x" * 10)
        return ProcResult(0, "", "")        # yt-dlp printed nothing usable

    monkeypatch.setattr(wt, "run", fake_run)
    assert wt.download_audio("abc12345678", tmp_path).suffix == ".m4a"


# --- the estimate someone is asked to agree to ---

def test_estimate_matches_the_measured_rate():
    """5.5x realtime measured for `small`; a 45-minute video is about 8 minutes."""
    assert 7 * 60 <= wt.estimate_seconds(45 * 60 * 1000, "small") <= 9 * 60


def test_a_bigger_model_is_estimated_slower():
    assert wt.estimate_seconds(600_000, "large") > wt.estimate_seconds(600_000, "tiny")


def test_estimate_never_returns_zero():
    assert wt.estimate_seconds(0, "small") >= 1


# --- repetition, the failure mode of captionless material ---

def test_a_repeated_sentence_collapses_into_one_cue():
    """Whisper loops on music beds and silence, for minutes at a time."""
    cues = [Cue(0, 1000, "Thanks for watching"),
            Cue(1000, 2000, "Thanks for watching"),
            Cue(2000, 3000, "Thanks for watching"),
            Cue(3000, 4000, "Something else")]
    out = wt._collapse_repeats(cues)
    assert [c.text for c in out] == ["Thanks for watching", "Something else"]
    assert out[0].end_ms == 3000          # the span still covers the whole loop


def test_repeat_collapsing_ignores_case():
    out = wt._collapse_repeats([Cue(0, 1000, "Hello"), Cue(1000, 2000, "hello")])
    assert len(out) == 1


def test_different_text_is_never_collapsed():
    out = wt._collapse_repeats([Cue(0, 1000, "one"), Cue(1000, 2000, "two")])
    assert len(out) == 2


# --- interrupting an eight-minute job ---

def test_transcription_stops_when_cancelled(monkeypatch, tmp_path):
    """Between segments is the only place this loop can be interrupted."""
    segs = [_make_segment(i, i + 1.0, f"line {i}") for i in range(10)]
    _patch_whisper(monkeypatch, segs, duration=10.0)
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"fake")
    seen = {"n": 0}

    def cancel():
        seen["n"] += 1
        return seen["n"] > 3

    with pytest.raises(TldrError, match="cancelled"):
        wt.transcribe(audio, "small", should_cancel=cancel)


def test_a_run_that_overruns_its_budget_times_out(monkeypatch, tmp_path):
    import time as _time
    segs = [_make_segment(i, i + 1.0, f"line {i}") for i in range(10)]
    _patch_whisper(monkeypatch, segs, duration=10.0)
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"fake")
    from youtube_tldw import TldrTimeoutError
    with pytest.raises(TldrTimeoutError, match="time budget"):
        wt.transcribe(audio, "small", deadline=_time.monotonic() - 1)


def test_cancelling_before_the_first_segment_still_stops(monkeypatch, tmp_path):
    """Loading the model is slow enough that a stop can land during it."""
    _patch_whisper(monkeypatch, [_make_segment(0, 1, "x")], duration=10.0)
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"fake")
    with pytest.raises(TldrError, match="cancelled"):
        wt.transcribe(audio, "small", should_cancel=lambda: True)


# --- the contract every other cue producer satisfies ---

def test_whisper_cues_come_back_normalized(monkeypatch, tmp_path):
    """Span maths and the numbered cue listing assume sorted, non-overlapping
    cues. Whisper segments are usually in order; 'usually' is not a contract."""
    segs = [
        _make_segment(5.0, 7.0, "second"),
        _make_segment(0.0, 6.0, "first"),      # out of order AND overlapping
    ]
    _patch_whisper(monkeypatch, segs, duration=10.0)
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"fake")
    cues = wt.transcribe(audio, "small")
    assert [c.text for c in cues] == ["first", "second"]
    assert all(c.end_ms > c.start_ms for c in cues)
    assert cues[1].start_ms >= cues[0].end_ms      # no overlap left


def test_a_repetition_loop_is_collapsed_before_it_is_cached(monkeypatch, tmp_path):
    segs = [_make_segment(float(i), i + 1.0, "Thanks for watching") for i in range(40)]
    segs.append(_make_segment(40.0, 41.0, "A real sentence"))
    _patch_whisper(monkeypatch, segs, duration=41.0)
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"fake")
    cues = wt.transcribe(audio, "small")
    assert [c.text for c in cues] == ["Thanks for watching", "A real sentence"]


def test_silence_detection_and_loop_prevention_are_on(monkeypatch, tmp_path):
    """Both are what keep a music bed from producing minutes of one sentence."""
    seen = {}

    class FakeModel:
        def __init__(self, *a, **kw):
            seen["init"] = kw

        def transcribe(self, path, **kw):
            seen["kw"] = kw
            return iter([_make_segment(0, 1, "x")]), SimpleNamespace(duration=1.0)

    monkeypatch.setitem(__import__("sys").modules, "faster_whisper",
                        SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"fake")
    wt.transcribe(audio, "small")
    assert seen["kw"]["vad_filter"] is True
    assert seen["kw"]["condition_on_previous_text"] is False
    assert seen["init"]["cpu_threads"] >= 1    # not every core


def test_a_runaway_transcript_is_capped(monkeypatch, tmp_path):
    monkeypatch.setattr(wt, "MAX_CUES", 5)
    segs = [_make_segment(float(i), i + 1.0, f"line {i}") for i in range(50)]
    _patch_whisper(monkeypatch, segs, duration=50.0)
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"fake")
    assert len(wt.transcribe(audio, "small")) <= 5


def test_one_enormous_segment_is_truncated(monkeypatch, tmp_path):
    _patch_whisper(monkeypatch, [_make_segment(0, 1, "x" * 5000)], duration=1.0)
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"fake")
    assert len(wt.transcribe(audio, "small")[0].text) == wt.MAX_CUE_CHARS


# --- the whole job, in the one place both callers use ---

def test_transcribe_video_checks_the_dependency_before_downloading(monkeypatch,
                                                                   tmp_path):
    """Discovering faster-whisper is missing after 30MB of audio wastes both."""
    monkeypatch.setattr(wt, "is_available", lambda: False)
    monkeypatch.setattr(wt, "download_audio",
                        lambda *a, **k: pytest.fail("downloaded anyway"))
    with pytest.raises(TldrError, match="not installed"):
        wt.transcribe_video("abc12345678", tmp_path)

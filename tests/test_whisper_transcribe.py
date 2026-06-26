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
    with pytest.raises(TldrError, match="did not produce"):
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

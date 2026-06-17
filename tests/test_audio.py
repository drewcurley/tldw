from pathlib import Path

import pytest

from youtube_tldw import TldrError, audio
from youtube_tldw.metadata import VideoMeta
from youtube_tldw.summarize import TextResult


def test_build_spoken_script_strips_markdown():
    meta = VideoMeta("id12345abcd", "My **Great** Video", "The #1 Channel", 1000, {}, {})
    r = TextResult(["point *one*", "point two"], "The **summary** with `code` and a "
                   "[link](http://x).", 0.2, "why")
    s = audio.build_spoken_script(meta, r)
    assert "Key points" in s and "Summary" in s
    assert "point one" in s and "point two" in s
    assert "summary" in s and "link" in s
    for ch in "*`#[]()_":
        assert ch not in s
    assert "http" not in s  # URL stripped from link


def test_spoken_script_expands_unambiguous_abbreviations():
    meta = VideoMeta("id12345abcd", "History", "Chan", 1000, {}, {})
    r = TextResult(["Allies won WWII"], "Turnout was 60% vs. last year & rising.",
                   0.2, "x")
    s = audio.build_spoken_script(meta, r)
    assert "World War Two" in s and "WWII" not in s
    assert "versus" in s and "vs." not in s
    assert "percent" in s and "%" not in s
    assert "&" not in s and " and " in s


def test_extract_audio_command(monkeypatch, tmp_path):
    cap = {}
    monkeypatch.setattr(
        audio, "run",
        lambda argv, **kw: (cap.update(argv=argv), Path(argv[-1]).write_bytes(b"\x00")),
    )
    audio.extract_audio(tmp_path / "v.mp4", tmp_path / "a.mp3")
    assert "-vn" in cap["argv"] and "libmp3lame" in cap["argv"]


def test_extract_audio_missing_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(audio, "run", lambda argv, **kw: None)  # produces nothing
    with pytest.raises(TldrError):
        audio.extract_audio(tmp_path / "v.mp4", tmp_path / "a.mp3")


def test_synthesize_speech_uses_piper_then_ffmpeg(monkeypatch, tmp_path):
    calls = []

    def fake_run(argv, **kw):
        calls.append((argv, kw.get("stdin")))
        if "-f" in argv:  # piper writes the wav
            Path(argv[argv.index("-f") + 1]).write_bytes(b"\x00")
        if argv[0] == "ffmpeg":  # ffmpeg writes the mp3
            Path(argv[-1]).write_bytes(b"\x00")

    monkeypatch.setattr(audio, "run", fake_run)
    monkeypatch.setattr(audio, "require_piper", lambda: None)
    monkeypatch.setattr(audio, "ensure_voice", lambda gender, **k: "en_US-amy-medium")
    out = tmp_path / "out.mp3"
    audio.synthesize_speech("hello world", out, "female", tmp_path)

    piper_argv, stdin = calls[0]
    assert "piper" in piper_argv and "-m" in piper_argv
    assert stdin == "hello world"           # text piped, never argv
    assert calls[1][0][0] == "ffmpeg" and "libmp3lame" in calls[1][0]
    assert out.exists()


def test_ensure_voice_downloads_when_missing(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(audio, "VOICE_DIR", tmp_path / "voices")
    monkeypatch.setattr(audio, "run", lambda argv, **kw: captured.update(argv=argv))
    name = audio.ensure_voice("male")
    assert name == "en_US-ryan-medium"
    assert "piper.download_voices" in captured["argv"]
    assert "en_US-ryan-medium" in captured["argv"]


def test_ensure_voice_skips_download_when_present(monkeypatch, tmp_path):
    vdir = tmp_path / "voices"
    vdir.mkdir()
    (vdir / "en_US-amy-medium.onnx").write_bytes(b"\x00")
    monkeypatch.setattr(audio, "VOICE_DIR", vdir)
    monkeypatch.setattr(audio, "run", lambda *a, **k: pytest.fail("should not download"))
    assert audio.ensure_voice("female") == "en_US-amy-medium"


def test_require_piper_missing(monkeypatch):
    monkeypatch.setattr(audio.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(TldrError):
        audio.require_piper()

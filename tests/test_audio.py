from pathlib import Path

import pytest

from youtube_tldw import TldrError, audio


def test_build_spoken_script_strips_markdown():
    s = audio.build_spoken_script(
        "My **Great** Video", "The #1 Channel",
        ["point *one*", "point two"],
        "The **summary** with `code` and a [link](http://x).")
    assert "Key points" in s and "Summary" in s
    assert "point one" in s and "point two" in s
    assert "summary" in s and "link" in s
    for ch in "*`#[]()_":
        assert ch not in s
    assert "http" not in s  # URL stripped from link


def test_spoken_script_expands_unambiguous_abbreviations():
    s = audio.build_spoken_script("History", "Chan", ["Allies won WWII"],
                                  "Turnout was 60% vs. last year & rising.")
    assert "World War Two" in s and "WWII" not in s
    assert "versus" in s and "vs." not in s
    assert "percent" in s and "%" not in s
    assert "&" not in s and " and " in s


def test_resolve_voice_and_aliases():
    assert audio.resolve_voice("female") == "en_US-amy-medium"
    assert audio.resolve_voice("male") == "en_US-ryan-high"
    assert audio.resolve_voice("cori") == "en_GB-cori-high"
    assert audio.resolve_voice("alan") == "en_GB-alan-medium"
    with pytest.raises(TldrError):
        audio.resolve_voice("../etc/passwd")
    with pytest.raises(TldrError):
        audio.resolve_voice("nope")


def test_voice_list_shape():
    vs = audio.voice_list()
    ids = {v["id"] for v in vs}
    assert "amy" in ids and "cori" in ids and "alan" in ids
    assert all("label" in v and "id" in v for v in vs)


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


class _FakeChunk:
    audio_int16_bytes = b"\x00\x00"
    sample_rate = 22050
    sample_width = 2
    sample_channels = 1


class _FakeVoice:
    def synthesize(self, text):
        return [_FakeChunk(), _FakeChunk(), _FakeChunk()]


def test_synthesize_speech_inprocess_with_progress(monkeypatch, tmp_path):
    monkeypatch.setattr(audio, "require_piper", lambda: None)
    monkeypatch.setattr(audio, "ensure_voice", lambda v, **k: "en_US-amy-medium")
    monkeypatch.setattr(audio, "_load_voice", lambda p: _FakeVoice())
    ff = []
    monkeypatch.setattr(audio, "run",
                        lambda argv, **kw: (ff.append(argv), Path(argv[-1]).write_bytes(b"\x00")))
    prog = []
    out = tmp_path / "out.mp3"
    audio.synthesize_speech("One. Two. Three.", out, "amy", tmp_path,
                            on_progress=lambda m, p=None: prog.append((m, p)))

    assert (tmp_path / "speech.wav").exists()          # wav assembled from chunks
    assert ff and ff[0][0] == "ffmpeg" and "libmp3lame" in ff[0]
    assert out.exists()
    # per-sentence synth percentages reported, increasing, capped at 95
    synth = [p for m, p in prog if "synthesizing" in m and isinstance(p, int)]
    assert synth and synth == sorted(synth) and synth[-1] <= 95
    assert any(p is None for m, p in prog)             # preparing/loading are indeterminate


def test_ensure_voice_downloads_when_missing(monkeypatch, tmp_path):
    captured = {}
    vdir = tmp_path / "voices"
    monkeypatch.setattr(audio, "VOICE_DIR", vdir)

    def fake_run(argv, **kw):
        captured["argv"] = argv
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / "en_US-ryan-high.onnx").write_bytes(b"\x00")

    monkeypatch.setattr(audio, "run", fake_run)
    name = audio.ensure_voice("male")  # alias -> ryan
    assert name == "en_US-ryan-high"
    assert "piper.download_voices" in captured["argv"]
    assert "en_US-ryan-high" in captured["argv"]


def test_ensure_voice_retries_then_succeeds(monkeypatch, tmp_path):
    vdir = tmp_path / "voices"
    monkeypatch.setattr(audio, "VOICE_DIR", vdir)
    monkeypatch.setattr(audio.time, "sleep", lambda _s: None)
    calls = {"n": 0}

    def flaky(argv, **kw):
        calls["n"] += 1
        if calls["n"] < 2:
            raise TldrError("SSL: UNEXPECTED_EOF_WHILE_READING")  # transient
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / "en_US-amy-medium.onnx").write_bytes(b"\x00")

    monkeypatch.setattr(audio, "run", flaky)
    assert audio.ensure_voice("amy") == "en_US-amy-medium"
    assert calls["n"] == 2  # failed once, succeeded on retry


def test_ensure_voice_gives_up_with_clean_message(monkeypatch, tmp_path):
    monkeypatch.setattr(audio, "VOICE_DIR", tmp_path / "voices")
    monkeypatch.setattr(audio.time, "sleep", lambda _s: None)

    def always_fail(argv, **kw):
        raise TldrError("network down")

    monkeypatch.setattr(audio, "run", always_fail)
    with pytest.raises(TldrError, match="Couldn't download"):
        audio.ensure_voice("amy", attempts=2)


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

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


class _FakeConfig:
    sample_rate = 22050


class _FakeVoice:
    config = _FakeConfig()

    def synthesize(self, text):
        return [_FakeChunk(), _FakeChunk(), _FakeChunk()]


def _patch_voice(monkeypatch):
    monkeypatch.setattr(audio, "require_piper", lambda: None)
    monkeypatch.setattr(audio, "ensure_voice", lambda v, **k: "en_US-amy-medium")
    monkeypatch.setattr(audio, "_load_voice", lambda p: _FakeVoice())


def _fake_filter(out_blocks, captured=None):
    """Stand in for proc.stream_filter: drain the feed, then yield canned mp3 bytes."""
    def _filter(argv, feed, *, timeout=None, block=65536):
        if captured is not None:
            captured["argv"] = argv
            captured["pcm"] = bytearray()
            feed(captured["pcm"].extend)
        else:
            feed(lambda _b: None)
        yield from out_blocks
    return _filter


def test_stream_speech_yields_mp3_and_reports_progress(monkeypatch):
    _patch_voice(monkeypatch)
    cap = {}
    monkeypatch.setattr(audio, "stream_filter", _fake_filter([b"ID3", b"more"], cap))
    prog = []
    blocks = list(audio.stream_speech("One. Two. Three.", "amy",
                                      on_progress=lambda m, p=None: prog.append((m, p))))

    assert b"".join(blocks) == b"ID3more"                # nothing lost
    assert bytes(cap["pcm"]) == b"\x00\x00" * 3       # every sentence reached ffmpeg
    assert cap["argv"][0] == "ffmpeg" and "libmp3lame" in cap["argv"]
    assert "22050" in cap["argv"]                        # rate taken from the voice
    synth = [p for m, p in prog if "synthesizing" in m and isinstance(p, int)]
    assert synth and synth == sorted(synth) and synth[-1] <= 99
    assert any(p is None for m, p in prog)               # preparing/loading indeterminate


def test_stream_speech_coalesces_small_blocks(monkeypatch):
    """ffmpeg emits ~200-byte frames; the caller should get a few big blocks instead."""
    _patch_voice(monkeypatch)
    frames = [b"x" * 200] * 400                          # 80KB in 400 tiny writes
    monkeypatch.setattr(audio, "stream_filter", _fake_filter(frames))
    blocks = list(audio.stream_speech("One.", "amy"))

    assert b"".join(blocks) == b"".join(frames)          # nothing lost or reordered
    assert len(blocks) < 10                              # coalesced, not passed through
    assert len(blocks[0]) < audio.BLOCK_BYTES            # first block small: fast start
    assert len(blocks[0]) >= audio.FIRST_BLOCK_BYTES


def test_synthesize_speech_writes_mp3(monkeypatch, tmp_path):
    _patch_voice(monkeypatch)
    monkeypatch.setattr(audio, "stream_filter", _fake_filter([b"ID3", b"payload"]))
    out = tmp_path / "out.mp3"
    audio.synthesize_speech("One. Two.", out, "amy")
    assert out.read_bytes() == b"ID3payload"


def test_synthesize_speech_empty_output_raises(monkeypatch, tmp_path):
    _patch_voice(monkeypatch)
    monkeypatch.setattr(audio, "stream_filter", _fake_filter([]))
    with pytest.raises(TldrError):
        audio.synthesize_speech("One.", tmp_path / "out.mp3", "amy")


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

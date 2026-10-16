from pathlib import Path

import pytest

from youtube_tldw import TldrError, cli
from youtube_tldw.metadata import VideoMeta
from youtube_tldw.summarize import TextResult, VideoSelection
from youtube_tldw.transcript import Cue

VID = "dQw4w9WgXcQ"
URL = f"https://www.youtube.com/watch?v={VID}"


def _meta():
    return VideoMeta(VID, "Cool Title", "My Channel", 600_000, {}, {})


def _cues():
    return [Cue(i * 2000, i * 2000 + 2000, f"point {i}") for i in range(5)]


def _common_patches(monkeypatch):
    monkeypatch.setattr(cli.proc, "require", lambda *a: None)
    monkeypatch.setattr(cli.md, "fetch_metadata", lambda vid: _meta())
    monkeypatch.setattr(cli.md, "choose_track", lambda meta, lang: ("en", True))
    monkeypatch.setattr(cli.md, "download_subtitle", lambda *a, **k: "ignored")
    monkeypatch.setattr(cli.transcript, "parse_subtitles", lambda content: _cues())


def test_ratio_bounds():
    args = cli.build_parser().parse_args([URL, "--mode", "text", "--ratio", "1.5"])
    with pytest.raises(TldrError):
        cli._validate_args(args)


def test_max_length_parsed():
    args = cli.build_parser().parse_args(
        [URL, "--mode", "video", "--max-length", "2m"]
    )
    assert cli._validate_args(args) == 120_000


def test_text_mode_end_to_end(monkeypatch, tmp_path):
    _common_patches(monkeypatch)
    monkeypatch.setattr(
        cli.summarize, "summarize_text",
        lambda *a, **k: TextResult(["k1", "k2"], "the summary body", 0.2, "why"),
    )
    args = cli.build_parser().parse_args(
        [URL, "--mode", "text", "--output-dir", str(tmp_path)]
    )
    assert cli.run(args) == 0
    files = list((tmp_path / "text").glob("*.md"))
    assert len(files) == 1
    assert files[0].name == "My Channel - Cool Title - tl;dw - 1m.md"
    body = files[0].read_text()
    assert "the summary body" in body and "- k1" in body


def test_video_mode_end_to_end(monkeypatch, tmp_path):
    _common_patches(monkeypatch)
    monkeypatch.setattr(
        cli.summarize, "select_video_segments",
        lambda *a, **k: VideoSelection([(0, 1), (3, 4)], 0.4, "r"),
    )
    monkeypatch.setattr(cli.md, "download_video", lambda vid, wd: wd / "src.mp4")

    def fake_recut(source, chosen, cues, out_path, workdir, *, xfade_ms, caption_style, polish=None):
        Path(out_path).write_bytes(b"\x00\x00")  # pretend mp4
        return 95_000  # 1m35s

    monkeypatch.setattr(cli.videomode, "recut", fake_recut)
    args = cli.build_parser().parse_args(
        [URL, "--mode", "video", "--output-dir", str(tmp_path)]
    )
    assert cli.run(args) == 0
    files = list((tmp_path / "video").glob("*.mp4"))
    assert len(files) == 1
    assert files[0].name == "My Channel - Cool Title - tl;dw - 1m35s.mp4"


def test_video_mode_keep_source(monkeypatch, tmp_path):
    _common_patches(monkeypatch)
    monkeypatch.setattr(
        cli.summarize, "select_video_segments",
        lambda *a, **k: VideoSelection([(0, 1)], 0.4, "r"),
    )

    def fake_download(vid, wd):
        src = wd / "src.mp4"
        src.write_bytes(b"\x00source")
        return src

    monkeypatch.setattr(cli.md, "download_video", fake_download)

    def fake_recut(source, chosen, cues, out_path, workdir, *, xfade_ms, caption_style, polish=None):
        Path(out_path).write_bytes(b"\x00")
        return 60_000

    monkeypatch.setattr(cli.videomode, "recut", fake_recut)
    args = cli.build_parser().parse_args(
        [URL, "--mode", "video", "--keep-source", "--output-dir", str(tmp_path)]
    )
    assert cli.run(args) == 0
    vids = sorted(p.name for p in (tmp_path / "video").glob("*.mp4"))
    assert any("tl;dw - 1m" in n for n in vids)
    assert any("source" in n for n in vids)  # kept source written too


def test_ratio_acts_as_cap(monkeypatch, tmp_path):
    # duration 600s, ratio 0.1 => 60s cap; selection of 0..4 (10s) stays, but a
    # huge selection would be trimmed. Here we assert the cap is wired in.
    _common_patches(monkeypatch)
    captured = {}

    def fake_select(cues, channel, title, ratio, max_ms, *, timeout):
        captured["ratio"] = ratio
        return VideoSelection([(0, 4)], None, "r")

    monkeypatch.setattr(cli.summarize, "select_video_segments", fake_select)
    monkeypatch.setattr(cli.md, "download_video", lambda vid, wd: wd / "src.mp4")
    monkeypatch.setattr(
        cli.videomode, "recut",
        lambda *a, **k: (Path(a[3]).write_bytes(b"\x00"), 9000)[1],
    )
    args = cli.build_parser().parse_args(
        [URL, "--mode", "video", "--ratio", "0.1", "--output-dir", str(tmp_path)]
    )
    assert cli.run(args) == 0
    assert captured["ratio"] == 0.1


def test_intro_seconds_logic():
    parse = cli.build_parser().parse_args
    assert cli._intro_seconds(parse([URL, "--mode", "video"])) == cli.DEFAULT_INTRO_S
    assert cli._intro_seconds(parse([URL, "--mode", "video", "--no-intro"])) == 0
    assert cli._intro_seconds(parse([URL, "--mode", "video", "--keep-intro", "12"])) == 12


def test_no_intro_and_keep_intro_are_exclusive():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            [URL, "--mode", "video", "--no-intro", "--keep-intro", "5"]
        )


def test_video_polish_flags_wire_into_recut(monkeypatch, tmp_path):
    _common_patches(monkeypatch)
    monkeypatch.setattr(
        cli.summarize, "select_video_segments",
        lambda *a, **k: VideoSelection([(0, 1)], None, "r"),
    )
    monkeypatch.setattr(cli.md, "download_video", lambda vid, wd: wd / "src.mp4")
    captured = {}

    def fake_recut(source, chosen, cues, out_path, workdir, *, xfade_ms, caption_style, polish=None):
        captured["polish"] = polish
        captured["chosen"] = chosen
        Path(out_path).write_bytes(b"\x00")
        return 30_000

    monkeypatch.setattr(cli.videomode, "recut", fake_recut)

    # defaults: all polish on; intro prepended so first span starts at 0
    cli.run(cli.build_parser().parse_args(
        [URL, "--mode", "video", "--output-dir", str(tmp_path)]))
    p = captured["polish"]
    assert p.badge and p.end_card and p.banner_intro_s == float(cli.DEFAULT_INTRO_S)
    assert p.timestamps and p.source_url.endswith(VID)
    assert captured["chosen"][0].start_ms == 0

    # disable everything
    cli.run(cli.build_parser().parse_args(
        [URL, "--mode", "video", "--no-badge", "--no-banner", "--no-end-card",
         "--no-intro", "--no-timestamps", "--output-dir", str(tmp_path)]))
    p2 = captured["polish"]
    assert not p2.badge and not p2.end_card and not p2.timestamps
    assert p2.banner_intro_s is None


def test_max_length_exceedance_note(monkeypatch, tmp_path, capsys):
    _common_patches(monkeypatch)
    monkeypatch.setattr(
        cli.summarize, "select_video_segments",
        lambda *a, **k: VideoSelection([(0, 1)], None, "r"),
    )
    monkeypatch.setattr(cli.md, "download_video", lambda vid, wd: wd / "src.mp4")
    monkeypatch.setattr(
        cli.videomode, "recut",
        lambda *a, **k: (Path(a[3]).write_bytes(b"\x00"), 90_000)[1],  # 1m30s
    )
    cli.run(cli.build_parser().parse_args(
        [URL, "--mode", "video", "--max-length", "30s", "--output-dir", str(tmp_path)]))
    assert "exceeds --max-length" in capsys.readouterr().out


def test_default_mode_is_video():
    assert cli.build_parser().parse_args([URL]).mode == "video"


def test_video_render_audio_extracts_mp3(monkeypatch, tmp_path):
    _common_patches(monkeypatch)
    monkeypatch.setattr(cli.summarize, "select_video_segments",
                        lambda *a, **k: VideoSelection([(0, 1)], None, "r"))
    monkeypatch.setattr(cli.md, "download_video", lambda vid, wd: wd / "src.mp4")
    monkeypatch.setattr(cli.videomode, "recut",
                        lambda *a, **k: (Path(a[3]).write_bytes(b"\x00"), 40_000)[1])
    monkeypatch.setattr(cli.videomode, "probe_duration_ms", lambda p: 40_000)
    monkeypatch.setattr(cli.audio, "extract_audio",
                        lambda video, out_mp3, **k: Path(out_mp3).write_bytes(b"\x00"))
    cli.run(cli.build_parser().parse_args(
        [URL, "--mode", "video", "--render-audio", "--output-dir", str(tmp_path)]))
    assert list((tmp_path / "audio").glob("*.mp3"))


def test_text_render_audio_synthesizes(monkeypatch, tmp_path):
    _common_patches(monkeypatch)
    monkeypatch.setattr(cli.summarize, "summarize_text",
                        lambda *a, **k: TextResult(["k"], "body", 0.2, "x"))
    monkeypatch.setattr(cli.audio, "require_piper", lambda: None)
    monkeypatch.setattr(cli.videomode, "probe_duration_ms", lambda p: 30_000)
    monkeypatch.setattr(
        cli.audio, "synthesize_speech",
        lambda text, out_mp3, voice, **k: Path(out_mp3).write_bytes(b"\x00"),
    )
    cli.run(cli.build_parser().parse_args(
        [URL, "--mode", "text", "--render-audio", "--output-dir", str(tmp_path)]))
    assert list((tmp_path / "audio").glob("*.mp3"))
    assert list((tmp_path / "text").glob("*.md"))


def test_text_render_audio_requires_piper(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli.proc, "require", lambda *a: None)
    monkeypatch.setattr(cli.audio, "require_piper",
                        lambda: (_ for _ in ()).throw(TldrError("needs Piper TTS")))
    code = cli.main(
        [URL, "--mode", "text", "--render-audio", "--output-dir", str(tmp_path)])
    assert code == 1
    assert "Piper" in capsys.readouterr().err


def test_no_transcript_aborts_cleanly(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli.proc, "require", lambda *a: None)
    monkeypatch.setattr(cli.md, "fetch_metadata", lambda vid: _meta())

    def no_track(meta, lang):
        raise TldrError("This video has no subtitles or auto-captions")

    monkeypatch.setattr(cli.md, "choose_track", no_track)
    code = cli.main([URL, "--mode", "text", "--output-dir", str(tmp_path)])
    assert code == 1
    assert "no subtitles" in capsys.readouterr().err


# --- the CLI's captionless path ------------------------------------------------
#
# Criterion 16: CLI behaviour unchanged, with the orchestration shared with the
# server rather than written twice. Nothing covered this before, so "unchanged"
# was an assertion rather than a fact.

def test_cli_without_the_flag_still_refuses_a_captionless_video(monkeypatch):
    from youtube_tldw import NoTranscriptError, cli
    from youtube_tldw import metadata as md
    meta = md.VideoMeta("dQw4w9WgXcQ", "T", "C", 600_000, {}, {})
    monkeypatch.setattr(cli.md, "fetch_metadata", lambda vid: meta)
    monkeypatch.setattr(cli.md, "choose_track",
                        lambda m, l: (_ for _ in ()).throw(NoTranscriptError("none")))
    monkeypatch.setattr(cli.wt, "transcribe_video",
                        lambda *a, **k: pytest.fail("transcribed without the flag"))
    with pytest.raises(NoTranscriptError):
        cli._get_cues("dQw4w9WgXcQ", "en", Path("/tmp"), whisper_fallback=False)


def test_cli_uses_the_same_orchestration_as_the_server(monkeypatch, tmp_path):
    """Both callers go through transcribe_video, so the download-then-transcribe
    sequence exists in one place."""
    from youtube_tldw import NoTranscriptError, cli
    from youtube_tldw import metadata as md
    from youtube_tldw.transcript import Cue
    meta = md.VideoMeta("dQw4w9WgXcQ", "T", "C", 600_000, {}, {})
    monkeypatch.setattr(cli.md, "fetch_metadata", lambda vid: meta)
    monkeypatch.setattr(cli.md, "choose_track",
                        lambda m, l: (_ for _ in ()).throw(NoTranscriptError("none")))
    monkeypatch.setattr(cli.wt, "require_whisper", lambda: None)
    monkeypatch.setattr(cli.wt, "resolve_model", lambda: "small")
    calls = []

    def fake(video_id, workdir, **kw):
        calls.append((video_id, kw.get("model_size")))
        return [Cue(0, 900, "spoken words")]

    monkeypatch.setattr(cli.wt, "transcribe_video", fake)
    got_meta, cues = cli._get_cues("dQw4w9WgXcQ", "en", tmp_path,
                                   whisper_fallback=True)
    assert calls == [("dQw4w9WgXcQ", "small")]
    assert [c.text for c in cues] == ["spoken words"]
    assert got_meta is meta


def test_cli_quotes_an_estimate_from_the_measured_rate(monkeypatch, tmp_path, capsys):
    from youtube_tldw import NoTranscriptError, cli
    from youtube_tldw import metadata as md
    from youtube_tldw.transcript import Cue
    meta = md.VideoMeta("dQw4w9WgXcQ", "T", "C", 45 * 60 * 1000, {}, {})
    monkeypatch.setattr(cli.md, "fetch_metadata", lambda vid: meta)
    monkeypatch.setattr(cli.md, "choose_track",
                        lambda m, l: (_ for _ in ()).throw(NoTranscriptError("none")))
    monkeypatch.setattr(cli.wt, "require_whisper", lambda: None)
    monkeypatch.setattr(cli.wt, "resolve_model", lambda: "small")
    monkeypatch.setattr(cli.wt, "transcribe_video",
                        lambda *a, **k: [Cue(0, 900, "x")])
    cli._get_cues("dQw4w9WgXcQ", "en", tmp_path, whisper_fallback=True)
    assert "roughly 8 min" in capsys.readouterr().out


# --- config values the server would silently ignore ---------------------------

@pytest.mark.parametrize("args", [
    ["set", "whisper", "of"],
    ["set", "whisper", "yes"],
    ["set", "whisper-max-minutes", "zero"],
    ["set", "whisper-max-minutes", "0"],
    ["set", "whisper-max-minutes", "-5"],
    ["set", "whisper-model", "enormous"],
])
def test_a_setting_the_server_would_ignore_is_refused(args, monkeypatch, capsys):
    """A pin that reads as set and does nothing is worse than a rejected one."""
    from youtube_tldw import cli
    written = []
    monkeypatch.setattr(cli.config, "set_value",
                        lambda k, v: written.append((k, v)))
    assert cli._run_config(args) == 2
    assert written == []
    assert "error:" in capsys.readouterr().out


@pytest.mark.parametrize("args", [
    ["set", "whisper", "off"], ["set", "whisper", "ask"],
    ["set", "whisper", "always"], ["set", "whisper-max-minutes", "90"],
    ["set", "whisper-model", "medium"],
])
def test_valid_settings_are_written(args, monkeypatch):
    from youtube_tldw import cli
    written = []
    monkeypatch.setattr(cli.config, "set_value",
                        lambda k, v: written.append((k, v)))
    assert cli._run_config(args) == 0
    assert len(written) == 1

from pathlib import Path

import pytest

from youtube_tldr import TldrError, cli
from youtube_tldr.metadata import VideoMeta
from youtube_tldr.summarize import TextResult, VideoSelection
from youtube_tldr.transcript import Cue

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
    assert files[0].name == "My Channel - Cool Title - tl;dr - 1m.md"
    body = files[0].read_text()
    assert "the summary body" in body and "- k1" in body


def test_video_mode_end_to_end(monkeypatch, tmp_path):
    _common_patches(monkeypatch)
    monkeypatch.setattr(
        cli.summarize, "select_video_segments",
        lambda *a, **k: VideoSelection([(0, 1), (3, 4)], 0.4, "r"),
    )
    monkeypatch.setattr(cli.md, "download_video", lambda vid, wd: wd / "src.mp4")

    def fake_recut(source, chosen, cues, out_path, workdir, *, xfade_ms, caption_style):
        Path(out_path).write_bytes(b"\x00\x00")  # pretend mp4
        return 95_000  # 1m35s

    monkeypatch.setattr(cli.videomode, "recut", fake_recut)
    args = cli.build_parser().parse_args(
        [URL, "--mode", "video", "--output-dir", str(tmp_path)]
    )
    assert cli.run(args) == 0
    files = list((tmp_path / "video").glob("*.mp4"))
    assert len(files) == 1
    assert files[0].name == "My Channel - Cool Title - tl;dr - 1m35s.mp4"


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

    def fake_recut(source, chosen, cues, out_path, workdir, *, xfade_ms, caption_style):
        Path(out_path).write_bytes(b"\x00")
        return 60_000

    monkeypatch.setattr(cli.videomode, "recut", fake_recut)
    args = cli.build_parser().parse_args(
        [URL, "--mode", "video", "--keep-source", "--output-dir", str(tmp_path)]
    )
    assert cli.run(args) == 0
    vids = sorted(p.name for p in (tmp_path / "video").glob("*.mp4"))
    assert any("tl;dr - 1m" in n for n in vids)
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


def test_no_transcript_aborts_cleanly(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli.proc, "require", lambda *a: None)
    monkeypatch.setattr(cli.md, "fetch_metadata", lambda vid: _meta())

    def no_track(meta, lang):
        raise TldrError("This video has no subtitles or auto-captions")

    monkeypatch.setattr(cli.md, "choose_track", no_track)
    code = cli.main([URL, "--mode", "text", "--output-dir", str(tmp_path)])
    assert code == 1
    assert "no subtitles" in capsys.readouterr().err

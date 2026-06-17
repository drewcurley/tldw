from pathlib import Path

import pytest

from youtube_tldr import videomode
from youtube_tldr.proc import ProcResult
from youtube_tldr.spans import Span
from youtube_tldr.transcript import Cue


def test_has_filter_detects(monkeypatch):
    videomode._filter_cache.clear()
    listing = " T. acrossfade  A->A  x\n .S subtitles  S->V  y\n"
    monkeypatch.setattr(videomode, "run", lambda *a, **k: ProcResult(0, listing, ""))
    assert videomode.has_filter("subtitles") is True
    assert videomode.has_filter("nope") is False
    videomode._filter_cache.clear()


def test_caption_style_for(monkeypatch):
    assert videomode.caption_style_for(False) == "none"
    monkeypatch.setattr(videomode, "has_filter", lambda n: True)
    assert videomode.caption_style_for(True) == "burn"
    monkeypatch.setattr(videomode, "has_filter", lambda n: False)
    assert videomode.caption_style_for(True) == "soft"


def test_build_recut_srt_remaps_timeline():
    spans = [Span(0, 3000), Span(10000, 12000)]
    cues = [
        Cue(0, 1000, "first"),
        Cue(10000, 11000, "second"),
        Cue(50000, 51000, "outside"),
    ]
    srt = videomode.build_recut_srt(spans, cues, [3000, 2000], 400)
    assert "first" in srt and "second" in srt
    assert "outside" not in srt
    assert "00:00:02,600" in srt  # clip 2 starts at 3000-400=2600ms


def test_build_recut_srt_clamps_straddling_cue():
    spans = [Span(1000, 3000)]
    cues = [Cue(0, 5000, "straddle")]
    srt = videomode.build_recut_srt(spans, cues, [2000], 400)
    assert "00:00:00,000 --> 00:00:02,000" in srt


def test_encode_crossfade_no_captions(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(
        videomode, "run", lambda argv, **kw: captured.update(argv=argv, cwd=kw.get("cwd"))
    )
    videomode._encode_crossfade(
        [tmp_path / "a.mp4", tmp_path / "b.mp4"], [3000, 2000],
        tmp_path / "out.mp4", "none", 400, tmp_path,
    )
    argv = captured["argv"]
    assert "-filter_complex" in argv
    assert "[vout]" in argv and "[aout]" in argv
    assert argv.count("-i") == 2
    assert "mov_text" not in argv


def test_encode_crossfade_burn(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(videomode, "run", lambda argv, **kw: captured.update(argv=argv))
    videomode._encode_crossfade(
        [tmp_path / "a.mp4", tmp_path / "b.mp4"], [3000, 2000],
        tmp_path / "out.mp4", "burn", 400, tmp_path,
    )
    graph = captured["argv"][captured["argv"].index("-filter_complex") + 1]
    assert "subtitles=captions.srt" in graph and "[vsub]" in graph


def test_encode_crossfade_soft_muxes_track(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(videomode, "run", lambda argv, **kw: captured.update(argv=argv))
    videomode._encode_crossfade(
        [tmp_path / "a.mp4", tmp_path / "b.mp4"], [3000, 2000],
        tmp_path / "out.mp4", "soft", 400, tmp_path,
    )
    argv = captured["argv"]
    assert "mov_text" in argv
    assert "captions.srt" in argv          # added as an input
    assert "2:0" in argv                    # subtitle input mapped (index == n clips)
    assert "subtitles=captions.srt" not in " ".join(argv)  # NOT burned


def test_encode_single_soft(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(videomode, "run", lambda argv, **kw: captured.update(argv=argv))
    videomode._encode_single(tmp_path / "a.mp4", tmp_path / "out.mp4", "soft", tmp_path)
    argv = captured["argv"]
    assert "mov_text" in argv and "captions.srt" in argv


def _stub_pipeline(monkeypatch):
    monkeypatch.setattr(
        videomode, "_extract_clip",
        lambda source, span, workdir, idx: workdir / f"clip_{idx}.mp4",
    )
    monkeypatch.setattr(videomode, "probe_duration_ms", lambda path: 2000)


def test_recut_single_clip(monkeypatch, tmp_path):
    _stub_pipeline(monkeypatch)
    calls = {"single": 0, "cross": 0}
    monkeypatch.setattr(
        videomode, "_encode_single",
        lambda clip, out, style, cwd: (calls.__setitem__("single", 1),
                                       Path(out).write_bytes(b"\x00")),
    )
    monkeypatch.setattr(videomode, "_encode_crossfade",
                        lambda *a: calls.__setitem__("cross", 1))
    out = tmp_path / "out.mp4"
    ms = videomode.recut(
        tmp_path / "src.mp4", [Span(0, 2000)], [], out, tmp_path,
        xfade_ms=400, caption_style="none",
    )
    assert ms == 2000 and calls["single"] == 1 and calls["cross"] == 0


def test_recut_multi_clip_writes_captions(monkeypatch, tmp_path):
    _stub_pipeline(monkeypatch)
    monkeypatch.setattr(
        videomode, "_encode_crossfade",
        lambda clips, durs, out, style, xf, cwd: Path(out).write_bytes(b"\x00"),
    )
    cues = [Cue(0, 1000, "hello"), Cue(5000, 6000, "world")]
    spans = [Span(0, 2000), Span(5000, 7000)]
    out = tmp_path / "out.mp4"
    videomode.recut(
        tmp_path / "src.mp4", spans, cues, out, tmp_path,
        xfade_ms=400, caption_style="soft",
    )
    assert (tmp_path / "captions.srt").exists()
    assert out.exists()


def test_recut_no_spans_raises(tmp_path):
    with pytest.raises(Exception):
        videomode.recut(tmp_path / "s.mp4", [], [], tmp_path / "o.mp4", tmp_path,
                        xfade_ms=400, caption_style="none")

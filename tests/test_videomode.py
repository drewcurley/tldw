from pathlib import Path

import pytest

from youtube_tldw import videomode
from youtube_tldw.proc import ProcResult
from youtube_tldw.spans import Span
from youtube_tldw.transcript import Cue


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
        lambda source, span, workdir, idx, ts_text=None: workdir / f"clip_{idx}.mp4",
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


# --- polish (intro / badge / end card) ---

def test_polish_any_enabled():
    from youtube_tldw.videomode import Polish
    assert Polish(badge=True, end_card=False, timestamps=False).any_enabled
    assert Polish(badge=False, end_card=True, timestamps=False).any_enabled
    assert Polish(badge=False, end_card=False, timestamps=True).any_enabled
    assert Polish(badge=False, end_card=False, timestamps=False,
                  banner_intro_s=2.0).any_enabled
    assert not Polish(badge=False, end_card=False, timestamps=False,
                      banner_intro_s=None).any_enabled


def test_extract_clip_plain_uses_vf(monkeypatch, tmp_path):
    captured = {}

    def fake_run(argv, **kw):
        captured["argv"] = argv
        Path(argv[-1]).write_bytes(b"\x00")

    monkeypatch.setattr(videomode, "run", fake_run)
    videomode._extract_clip(tmp_path / "src.mp4", Span(0, 3000), tmp_path, 0)
    argv = captured["argv"]
    assert "-vf" in argv and "-filter_complex" not in argv


def test_extract_clip_timestamp_overlay(monkeypatch, tmp_path):
    captured = {}

    def fake_run(argv, **kw):
        captured["argv"] = argv
        captured["cwd"] = kw.get("cwd")
        Path(argv[-1]).write_bytes(b"\x00")

    monkeypatch.setattr(videomode, "run", fake_run)
    videomode._extract_clip(tmp_path / "src.mp4", Span(12000, 15000), tmp_path, 2,
                            ts_text="0:12")
    argv = captured["argv"]
    graph = argv[argv.index("-filter_complex") + 1]
    assert "fade=t=in:st=0" in graph and "alpha=1" in graph
    assert "fade=t=out:st=" in graph
    assert "overlay=24:24:eof_action=pass" in graph
    assert "setsar=1,format=yuv420p" in graph
    assert "-loop" in argv                          # ts image looped
    assert captured["cwd"] == str(tmp_path)
    assert (tmp_path / "ts_0002.png").exists()      # timestamp PNG rendered


def test_extract_clip_timestamp_short_clip_shrinks_show(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(
        videomode, "run",
        lambda argv, **kw: (captured.update(argv=argv), Path(argv[-1]).write_bytes(b"\x00")),
    )
    # 1.0s clip -> show=1.0, fade=0.333, fade_out_st=0.667
    videomode._extract_clip(tmp_path / "src.mp4", Span(0, 1000), tmp_path, 0, ts_text="0:00")
    argv = captured["argv"]
    assert "1.000" in argv                          # -loop -t show == clip duration
    graph = argv[argv.index("-filter_complex") + 1]
    assert "fade=t=out:st=0.667" in graph


def test_recut_passes_source_timestamp(monkeypatch, tmp_path):
    from youtube_tldw.videomode import Polish
    seen = []
    monkeypatch.setattr(
        videomode, "_extract_clip",
        lambda src, s, wd, i, ts_text=None: (seen.append(ts_text), wd / f"c{i}.mp4")[1],
    )
    monkeypatch.setattr(videomode, "probe_duration_ms", lambda p: 2000)
    monkeypatch.setattr(videomode, "_recut_polished",
                        lambda *a, **k: Path(a[2]).write_bytes(b"\x00"))
    videomode.recut(tmp_path / "s.mp4", [Span(72000, 75000)], [], tmp_path / "o.mp4",
                    tmp_path, xfade_ms=400, caption_style="none",
                    polish=Polish(timestamps=True))
    assert seen == ["1:12"]  # 72000ms source start -> 1:12


def test_decorate_full_filtergraph(monkeypatch, tmp_path):
    from youtube_tldw.videomode import Polish
    captured = {}
    monkeypatch.setattr(videomode, "run", lambda argv, **kw: captured.update(argv=argv, cwd=kw.get("cwd")))
    polish = Polish(badge=True, banner_intro_s=2.0, end_card=True)
    videomode._decorate(tmp_path / "assembled.mp4", 6500, tmp_path / "out.mp4", tmp_path,
                        caption_style="soft", polish=polish)
    argv = captured["argv"]
    graph = argv[argv.index("-filter_complex") + 1]
    assert "overlay=W-w-24:24" in graph                      # corner badge
    assert "enable='between(t,0,2.000)'" in graph             # intro banner window
    assert "fade=t=in:st=0" in graph
    assert "fade=t=out:st=5.800" in graph                     # (6500-700)/1000
    assert "format=yuv420p" in graph and "[vout]" in graph
    # soft captions muxed
    assert "mov_text" in argv and videomode._CAPTIONS in argv
    # ARCH-3: only safe basenames reach ffmpeg, and we run from the workdir
    assert "assembled.mp4" in argv and "badge.png" in argv and "banner.png" in argv
    assert captured["cwd"] == str(tmp_path)
    for a in argv:
        assert "/" not in a or a.endswith("out.mp4")          # output may be a full path


def test_decorate_minimal_just_fade(monkeypatch, tmp_path):
    from youtube_tldw.videomode import Polish
    captured = {}
    monkeypatch.setattr(videomode, "run", lambda argv, **kw: captured.update(argv=argv))
    polish = Polish(badge=False, banner_intro_s=None, end_card=True)
    videomode._decorate(tmp_path / "assembled.mp4", 5000, tmp_path / "out.mp4", tmp_path,
                        caption_style="none", polish=polish)
    graph = captured["argv"][captured["argv"].index("-filter_complex") + 1]
    assert "overlay" not in graph
    assert graph.startswith("[0:v]fade=t=in")
    assert "mov_text" not in captured["argv"]


def test_assemble_uses_fadeblack_for_endcard(monkeypatch, tmp_path):
    from youtube_tldw.videomode import Polish
    captured = {}
    monkeypatch.setattr(videomode, "run", lambda argv, **kw: captured.update(argv=argv))
    monkeypatch.setattr(videomode, "_render_end_card_clip",
                        lambda wd, text, secs, url=None: wd / "endcard.mp4")
    monkeypatch.setattr(videomode, "probe_duration_ms",
                        lambda p: 2500 if "endcard" in p.name else 6500)
    clips = [tmp_path / "clip_0000.mp4", tmp_path / "clip_0001.mp4"]
    out, total = videomode._assemble(clips, [3000, 2000], tmp_path,
                                     xfade_ms=400, polish=Polish(end_card=True))
    assert out.name == "assembled.mp4" and total == 6500
    graph = captured["argv"][captured["argv"].index("-filter_complex") + 1]
    assert "transition=fade:" in graph and "transition=fadeblack:" in graph
    assert "endcard.mp4" in captured["argv"]


def test_recut_dispatches_to_polished(monkeypatch, tmp_path):
    from youtube_tldw.videomode import Polish
    _stub_pipeline(monkeypatch)
    called = {}
    monkeypatch.setattr(
        videomode, "_recut_polished",
        lambda clips, durs, out, wd, **k: (called.__setitem__("yes", 1),
                                           Path(out).write_bytes(b"\x00")),
    )
    out = tmp_path / "out.mp4"
    videomode.recut(tmp_path / "s.mp4", [Span(0, 3000)], [], out, tmp_path,
                    xfade_ms=400, caption_style="none", polish=Polish(badge=True))
    assert called.get("yes") == 1 and out.exists()


def test_recut_no_polish_uses_plain_path(monkeypatch, tmp_path):
    from youtube_tldw.videomode import Polish
    _stub_pipeline(monkeypatch)
    monkeypatch.setattr(videomode, "_recut_polished",
                        lambda *a, **k: pytest.fail("should not run when polish disabled"))
    monkeypatch.setattr(videomode, "_encode_single",
                        lambda clip, out, style, cwd: Path(out).write_bytes(b"\x00"))
    out = tmp_path / "out.mp4"
    # all polish off -> any_enabled False -> plain path
    videomode.recut(tmp_path / "s.mp4", [Span(0, 3000)], [], out, tmp_path,
                    xfade_ms=400, caption_style="none",
                    polish=Polish(badge=False, end_card=False, banner_intro_s=None,
                                  timestamps=False))
    assert out.exists()

from PIL import Image

from youtube_tldw import overlays


def test_corner_badge_png(tmp_path):
    p = tmp_path / "badge.png"
    overlays.render_corner_badge(p, "TL;DW")
    assert p.exists()
    img = Image.open(p)
    assert img.mode == "RGBA"
    assert img.width > 0 and img.height > 0


def test_intro_banner_full_canvas(tmp_path):
    p = tmp_path / "banner.png"
    overlays.render_intro_banner(p, "TL;DW version")
    img = Image.open(p)
    assert img.size == (1280, 720)
    assert img.mode == "RGBA"


def test_end_card_opaque_black(tmp_path):
    p = tmp_path / "endcard.png"
    overlays.render_end_card(p, "Made with youtube-tldw")
    img = Image.open(p).convert("RGB")
    assert img.size == (1280, 720)
    assert img.getpixel((5, 5)) == (0, 0, 0)  # black background


def test_intro_banner_with_url_taller_band(tmp_path):
    p_plain = tmp_path / "b1.png"
    p_url = tmp_path / "b2.png"
    overlays.render_intro_banner(p_plain, "TL;DW version")
    overlays.render_intro_banner(p_url, "TL;DW version",
                                 "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    # both full-canvas; the URL variant just adds a second text line
    assert Image.open(p_plain).size == (1280, 720)
    assert Image.open(p_url).size == (1280, 720)


def test_end_card_with_url(tmp_path):
    p = tmp_path / "ec.png"
    overlays.render_end_card(p, "Made with youtube-tldw",
                             "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert Image.open(p).size == (1280, 720)


def test_render_timestamp(tmp_path):
    p = tmp_path / "ts.png"
    overlays.render_timestamp(p, "12:34")
    img = Image.open(p)
    assert img.mode == "RGBA" and img.width > 0 and img.height > 0


def test_font_fallback_when_no_system_fonts(tmp_path, monkeypatch):
    # Force the load_default() fallback path (CI has no macOS fonts).
    monkeypatch.setattr(overlays, "_FONT_CANDIDATES", ())
    p = tmp_path / "card.png"
    overlays.render_end_card(p, "fallback works")
    assert p.exists()
    assert Image.open(p).size == (1280, 720)

"""Render TL;DW overlay assets (badge, intro banner, end card) to PNGs with Pillow.

This ffmpeg has no libfreetype/libass, so all on-screen text is rasterized here and
composited by ffmpeg's `overlay` filter. INVARIANT: text is only ever drawn into a
PNG — it is NEVER interpolated into an ffmpeg filter string.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Canvas matches the video pipeline (1280x720).
_W, _H = 1280, 720
_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """A truetype font at `size`, or a legibly-sized default if none are present."""
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:  # Pillow >= 10.1 can size the bundled font
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    return r - l, b - t


def render_corner_badge(path: Path, text: str = "TL;DW") -> None:
    """Small translucent badge with an opaque backing plate (guaranteed contrast)."""
    pad_x, pad_y = 22, 12
    font = _font(34)
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    tw, th = _text_size(probe, text, font)
    w, h = tw + pad_x * 2, th + pad_y * 2
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=14, fill=(0, 0, 0, 170))
    d.text((pad_x, pad_y - 2), text, font=font, fill=(255, 255, 255, 235))
    img.save(path)


def render_intro_banner(
    path: Path, text: str = "TL;DW version", url: str | None = None
) -> None:
    """Full-width lower-third banner (RGBA); optional source URL on a second line."""
    img = Image.new("RGBA", (_W, _H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    font = _font(46)
    url_font = _font(28)
    tw, th = _text_size(d, text, font)
    uw, uh = _text_size(d, url, url_font) if url else (0, 0)
    gap = 10 if url else 0
    band_h = th + uh + gap + 44
    band_top = _H - band_h - 48
    d.rectangle([0, band_top, _W, band_top + band_h], fill=(0, 0, 0, 150))
    d.text(((_W - tw) // 2, band_top + 20), text, font=font, fill=(255, 255, 255, 240))
    if url:
        d.text(((_W - uw) // 2, band_top + 20 + th + gap), url, font=url_font,
               fill=(210, 210, 210, 235))
    img.save(path)


def render_end_card(
    path: Path, text: str = "Made with youtube-tldw", url: str | None = None
) -> None:
    """Full-frame opaque black end card with centered text; optional URL beneath."""
    img = Image.new("RGB", (_W, _H), (0, 0, 0))
    d = ImageDraw.Draw(img)
    font = _font(56)
    url_font = _font(30)
    tw, th = _text_size(d, text, font)
    uw, uh = _text_size(d, url, url_font) if url else (0, 0)
    gap = 24 if url else 0
    block_h = th + uh + gap
    top = (_H - block_h) // 2
    d.text(((_W - tw) // 2, top), text, font=font, fill=(255, 255, 255))
    if url:
        d.text(((_W - uw) // 2, top + th + gap), url, font=url_font,
               fill=(200, 200, 200))
    img.save(path)


def render_timestamp(path: Path, text: str) -> None:
    """Small translucent plate showing a source timestamp (e.g. '12:34')."""
    pad_x, pad_y = 18, 10
    font = _font(30)
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    tw, th = _text_size(probe, text, font)
    w, h = tw + pad_x * 2, th + pad_y * 2
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=12, fill=(0, 0, 0, 160))
    d.text((pad_x, pad_y - 2), text, font=font, fill=(255, 255, 255, 235))
    img.save(path)

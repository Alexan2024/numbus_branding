"""Движок рендера: запасной шрифт, слайды карусели, длинные заголовки, JPEG, поворот и размер фото."""
import io
import os

import numpy as np
import pytest
from PIL import Image, JpegImagePlugin

import render as R
import spec as S
from conftest import make_photo, make_logo, SVG_LOGO

HAVE_FONTS = os.path.exists(os.path.join(R.FONT_DIR, "Inter.ttf"))
needs_fonts = pytest.mark.skipif(not HAVE_FONTS, reason="шрифты скачиваются при сборке Docker")


def ctx(**fields):
    logo = Image.open(io.BytesIO(R.prepare_logo(make_logo())[0])).convert("RGBA")
    f = dict(title="Заголовок поста", subtitle="", hashtag="#новости", i=1, n=1)
    f.update(fields)
    return R.Ctx(S.sanitize_palette(None), {"logo": logo}, {}, f)


@needs_fonts
def test_fallback_for_missing_glyphs():
    face = R.Face("playfair", 600, 60)
    runs = face.runs("Цена 350 ₽")
    fonts = {id(f) for _, f in runs}
    if 0x20BD not in (R.font_cover("playfair") or {0x20BD}):
        assert len(fonts) == 2, "₽ должен рисоваться запасным шрифтом"
    assert "".join(t for t, _ in runs) == "Цена 350 ₽"


def test_carousel_cover_only_text():
    photo = R.open_photo(make_photo(1200, 1500, seed=1))
    layers = S.preset_spec("editorial")["feed"]["layers"]
    W, H = 1080, 1350
    cover = np.asarray(R.render_surface(photo, W, H, layers, ctx(i=1, n=3))).astype(int)
    rest = np.asarray(R.render_surface(photo, W, H, layers, ctx(i=2, n=3))).astype(int)
    bottom = slice(int(H * 0.6), H)
    assert np.abs(cover[bottom] - rest[bottom]).mean() > 5      # на обложке — заголовок и затемнение
    top_logo = (slice(0, int(H * 0.12)), slice(0, int(W * 0.3)))
    assert np.abs(cover[top_logo] - rest[top_logo]).mean() < 1  # логотип — на всех кадрах


def test_counter_hidden_on_single_photo():
    photo = R.open_photo(make_photo(1200, 1500, seed=2))
    layers = [S.sanitize_layer(dict(type="text", source="counter", text="{i} / {n}", anchor="br", x=0.06, y=0.06,
                                    size=0.05, color={"mode": "fixed", "value": "#FF0000"}))]
    one = np.asarray(R.render_surface(photo, 1080, 1350, layers, ctx(i=1, n=1))).astype(int)
    base = np.asarray(R.render_surface(photo, 1080, 1350, [], ctx())).astype(int)
    assert np.abs(one - base).max() == 0


def test_long_title_shrinks_then_cuts():
    photo = R.open_photo(make_photo(1200, 1500, seed=3))
    L = S.sanitize_layer(dict(type="text", source="title", anchor="bl", x=0.06, y=0.06, size=0.07, maxw=0.8, lines=2))
    c = ctx(title="Очень длинный заголовок, который никак не помещается в две строки на обложке поста " * 3)
    R.render_surface(photo, 1080, 1350, [L], c)
    assert "title_cut" in c.notes
    c2 = ctx(title="Заголовок чуть длиннее, чем две строки этого размера")
    R.render_surface(photo, 1080, 1350, [L], c2)
    assert c2.notes <= {"title_small", "title_cut"}


def test_jpeg_444_with_srgb_profile():
    img = R.render_surface(R.open_photo(make_photo(800, 1000)), 800, 1000, [], ctx())
    out = Image.open(io.BytesIO(R.to_jpeg(img)))
    assert JpegImagePlugin.get_sampling(out) == 0          # 4:4:4 — красные плашки без «грязи»
    assert out.info.get("icc_profile")


def test_exif_rotation_and_big_jpeg_draft():
    rotated = R.open_photo(make_photo(1200, 900, exif_orientation=6))
    assert rotated.size == (900, 1200)
    big = make_photo(5400, 6000, seed=4)
    im = R.open_photo(big)
    assert min(im.size) >= R.PHOTO_SHORT and im.size[0] < 5400    # декодирован уменьшенным
    with pytest.raises(ValueError):
        R.open_image(_png(11000, 10000))                            # огромный PNG не открываем


def _png(w, h):
    buf = io.BytesIO()
    Image.new("L", (w, h), 0).save(buf, "PNG", compress_level=1)
    return buf.getvalue()


def test_svg_logo_sanitized_and_colors():
    png, had_alpha = R.prepare_logo(SVG_LOGO)
    im = Image.open(io.BytesIO(png))
    assert im.mode == "RGBA" and had_alpha
    cols = R.logo_colors(png)
    assert cols and cols[0].upper().startswith("#1E7A5C"[:3])
    red = R.logo_colors(R.prepare_logo(make_logo(color=(200, 60, 40)))[0])
    r, g, b = R.hex_rgb(red[0])
    assert r > 150 and g < 110

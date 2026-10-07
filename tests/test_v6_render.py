"""v6 (пилот): безопасность SVG-логотипов, многоцветные логотипы в стартовых стилях,
доверие к прозрачности логотипа, память при декодировании, данные стартовых стилей."""
import io
import os
import time

import numpy as np
import pytest
from PIL import Image, ImageDraw

import db
import render as R
import spec as S
from conftest import make_logo, make_photo

NS = 'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'
RED = '<rect width="40" height="40" fill="#D7262D"/>'


# ============ SVG: внешние ресурсы, сущности, размеры ============
@pytest.fixture()
def secret(tmp_path):
    """Чужой файл на сервере (как DATA_DIR/samples/777.img): пёстрое фото."""
    p = tmp_path / "samples" / "777.img"
    p.parent.mkdir()
    rng = np.random.default_rng(3)
    Image.fromarray(rng.integers(0, 255, (300, 300, 3), dtype=np.uint8), "RGB").save(p, "JPEG")
    return str(p)


def _colours(png):
    a = np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))
    vis = a[a[:, :, 3] > 8][:, :3]
    return len(np.unique((vis // 16).reshape(-1, 3), axis=0))


def _payloads(path):
    nested = __import__("base64").b64encode(
        f'<svg {NS} width="100" height="100"><image href="{path}" width="100" height="100"/></svg>'.encode()).decode()
    return {
        "abs": f'<svg {NS} width="100" height="100"><image href="{path}" width="100" height="100"/>{RED}</svg>',
        "xlink_file": f'<svg {NS} width="100" height="100"><image xlink:href="file://{path}" width="100" height="100"/>{RED}</svg>',
        "data_in_class": f'<svg {NS} width="100" height="100"><image href="{path}" class="data:" width="100" height="100"/>{RED}</svg>',
        "ns_prefix": f'<svg {NS} xmlns:s="http://www.w3.org/2000/svg" width="100" height="100"><s:image href="{path}" width="100" height="100"/>{RED}</svg>',
        "feImage": f'<svg {NS} width="100" height="100"><filter id="f" x="0" y="0" width="1" height="1"><feImage href="{path}"/></filter><rect width="100" height="100" filter="url(#f)"/>{RED}</svg>',
        "traversal": f'<svg {NS} width="100" height="100"><image href="../../../../../../..{path}" width="100" height="100"/>{RED}</svg>',
        "nested_svg": f'<svg {NS} width="100" height="100"><image href="data:image/svg+xml;base64,{nested}" width="100" height="100"/>{RED}</svg>',
        "use_external": f'<svg {NS} width="100" height="100"><use href="{path}#x"/>{RED}</svg>',
        "pattern_style_url": f'<svg {NS} width="100" height="100"><rect width="100" height="100" style="fill:url(file://{path}#p)"/>{RED}</svg>',
    }


def test_svg_external_resources_never_loaded(secret):
    for name, svg in _payloads(secret).items():
        png, _ = R.prepare_logo(svg.encode())
        assert _colours(png) <= 2, name                     # только свой красный квадрат, не чужое фото
        text, _ = R.sanitize_svg(svg.encode())
        assert secret not in text, name


def test_svg_dev_zero_is_dropped_quickly():
    svg = f'<svg {NS} width="100" height="100"><image href="/dev/zero" class="data:" width="100" height="100"/>{RED}</svg>'
    t = time.time()
    png, _ = R.prepare_logo(svg.encode())
    assert time.time() - t < 15 and _colours(png) == 1


def test_svg_scripts_events_and_css_urls_removed():
    svg = (f'<svg {NS} width="100" height="100" onload="alert(1)"><script>alert(1)</script>'
           f'<style>@import url(http://x/a.css); .a{{fill:url(http://x/p)}} .b{{fill:url(#g)}}</style>'
           f'<foreignObject width="10" height="10"><div xmlns="http://www.w3.org/1999/xhtml">x</div></foreignObject>'
           f'<a href="javascript:alert(1)">{RED}</a><rect onclick="x()" fill="url(http://x/y) red" width="5" height="5"/></svg>')
    text, size = R.sanitize_svg(svg.encode())
    low = text.lower()
    for bad in ("script", "onload", "onclick", "foreignobject", "http://x", "javascript", "@import"):
        assert bad not in low, bad
    assert "url(#g)" in text and "#D7262D" in text           # своё содержимое и ссылки внутри файла на месте
    assert size == (100.0, 100.0)


def test_svg_entities():
    ent = '<!ENTITY a0 "lol">' + "".join(f'<!ENTITY a{i} "{("&a%d;" % (i - 1)) * 10}">' for i in range(1, 10))
    bombs = [
        f'<?xml version="1.0"?><!DOCTYPE svg [{ent}]><svg {NS}><text>&a9;</text></svg>',
        f'<?xml version="1.0"?><!DOCTYPE svg SYSTEM "x" [{ent}]><svg {NS}><text>&a9;</text></svg>',
        f'<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]><svg {NS}><text>&x;</text></svg>',
        f'<!DOCTYPE svg [<!ENTITY % p SYSTEM "http://127.0.0.1:9/x.dtd"> %p;]><svg {NS}>{RED}</svg>',
        f'<svg {NS}><text>&undefined;</text></svg>',
    ]
    for svg in bombs:
        with pytest.raises(R.BadVector):
            R.prepare_logo(svg.encode())
    assert not issubclass(R.BadVector, ValueError)       # бот и редактор: «не получилось открыть логотип»
    # Illustrator: DOCTYPE с простыми сущностями для пространств имён — открывается
    ai = ('<?xml version="1.0" encoding="utf-8"?>\n<!-- Generator: Adobe Illustrator 24.0.0 -->\n'
          '<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd" [\n'
          '\t<!ENTITY ns_extend "http://ns.adobe.com/Extensibility/1.0/">\n'
          '\t<!ENTITY ns_ai "http://ns.adobe.com/AdobeIllustrator/10.0/">\n]>\n'
          '<svg version="1.1" xmlns:x="&ns_extend;" xmlns:i="&ns_ai;" xmlns="http://www.w3.org/2000/svg" '
          'width="240px" height="80px" viewBox="0 0 240 80"><switch><foreignObject requiredExtensions="&ns_ai;" '
          'width="1" height="1"/><g i:extraneous="self"><rect width="240" height="80" rx="12" fill="#2B59C3"/>'
          '<circle cx="40" cy="40" r="20" fill="#FFFFFF"/></g></switch></svg>')
    png, had = R.prepare_logo(ai.encode())
    assert had and Image.open(io.BytesIO(png)).width >= 1900


def test_svg_size_units_and_limits():
    root = lambda attrs: __import__("xml.etree.ElementTree", fromlist=["x"]).fromstring(f"<svg {NS} {attrs}/>")
    assert R.svg_size(root('viewBox="0 0 +1 +60"')) == (1.0, 60.0)
    assert R.svg_size(root('width="20mm" height="10mm"')) == pytest.approx((75.59, 37.80), abs=0.01)
    assert R.svg_size(root('width="100%" height="100%" viewBox="0,0,500,100"')) == (500.0, 100.0)
    assert R.svg_size(root('width="300" viewBox="0 0 600 200"')) == (300.0, 100.0)
    assert R.svg_size(root('width="1in" height="72pt"')) == (96.0, 96.0)
    assert R.svg_size(root('')) is None
    with pytest.raises(R.BadVector):                         # 1:60 — не логотип (и раньше 120 000 px высотой)
        R.prepare_logo(f'<svg {NS} viewBox="0 0 +1 +60"><rect width="1" height="60" fill="red"/></svg>'.encode())
    big = R.rasterize_vector(f'<svg {NS} width="100000" height="40000"><rect x="10" y="10" width="99980" '
                             f'height="39980" fill="red"/></svg>'.encode())
    assert max(big.size) <= R.VECTOR_MAX_SIDE
    inkscape = f'<svg {NS} width="210mm" height="99mm" viewBox="0 0 210 99"><rect x="10mm" y="5" width="50" height="40" fill="#1E7A5C"/></svg>'
    png, _ = R.prepare_logo(inkscape.encode())                # раньше: «SVG has an invalid size»
    assert R.logo_colors(png)[0].startswith("#1")


def test_svg_embedded_raster():
    small = io.BytesIO()
    Image.new("RGB", (64, 32), (30, 120, 220)).save(small, "PNG")
    b64 = __import__("base64").b64encode(small.getvalue()).decode()
    svg = f'<svg {NS} viewBox="0 0 100 40"><image xlink:href="data:image/png;base64,{b64}" x="10" y="4" width="64" height="32"/></svg>'
    png, _ = R.prepare_logo(svg.encode())
    assert R.logo_colors(png) == ["#1E78DC"]
    bomb = io.BytesIO()
    Image.new("1", (8000, 8000)).save(bomb, "PNG")            # 64 Мп в 10 КБ
    b64 = __import__("base64").b64encode(bomb.getvalue()).decode()
    with pytest.raises(R.TooBig):
        R.prepare_logo(f'<svg {NS} width="10" height="10"><image href="data:image/png;base64,{b64}" width="10" height="10"/></svg>'.encode())


# ============ Логотипы: многоцветность и прозрачность ============
def _png(im):
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def badge_logo():
    """Красная скруглённая плашка с белыми «буквами» — уголки прозрачны (≈0,3% пикселей)."""
    im = Image.new("RGBA", (900, 300), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, 899, 299), radius=30, fill=(215, 38, 45, 255))
    for k in range(6):
        d.rectangle((90 + k * 125, 90, 160 + k * 125, 210), fill=(255, 255, 255, 255))
    return _png(im)


def avatar_logo():
    im = Image.new("RGBA", (640, 640), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((0, 0, 639, 639), fill=(40, 120, 220, 255))
    d.rectangle((200, 180, 280, 460), fill=(255, 255, 255, 255))
    d.rectangle((360, 180, 440, 460), fill=(255, 255, 255, 255))
    return _png(im)


def two_hue_logo():
    im = Image.new("RGBA", (1200, 300), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for k in range(4):
        d.rectangle((20 + k * 130, 60, 110 + k * 130, 240), fill=(215, 38, 45, 255))
        d.rectangle((640 + k * 130, 60, 730 + k * 130, 240), fill=(30, 70, 200, 255))
    return _png(im)


def outlined_logo():
    im = Image.new("RGBA", (900, 300), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for k in range(4):
        d.rectangle((20 + k * 220, 40, 200 + k * 220, 260), fill=(255, 255, 255, 255), outline=(0, 0, 0, 255), width=14)
    return _png(im)


def test_logo_is_multitone():
    prep = lambda b: R.prepare_logo(b)[0]
    assert R.logo_is_multitone(prep(badge_logo()))
    assert R.logo_is_multitone(prep(avatar_logo()))
    assert R.logo_is_multitone(prep(outlined_logo()))
    assert not R.logo_is_multitone(prep(make_logo()))                     # знак и надпись на прозрачном
    assert not R.logo_is_multitone(prep(make_logo(transparent=False)))    # то же на белом фоне
    assert not R.logo_is_multitone(prep(two_hue_logo()))                 # два цвета, но без рисунка внутри
    assert not R.logo_is_multitone(None) and not R.logo_is_multitone(b"junk")
    # плашка JPEG на белом: фон убран, «буквы» стали прозрачными — перекраска безопасна
    b = Image.open(io.BytesIO(badge_logo()))
    bg = Image.new("RGB", (b.width + 80, b.height + 80), (255, 255, 255))
    bg.paste(b, (40, 40), b)
    jpg = io.BytesIO()
    bg.save(jpg, "JPEG", quality=90)
    assert not R.logo_is_multitone(prep(jpg.getvalue()))


def test_badge_alpha_trusted_and_colour():
    png, had_alpha = R.prepare_logo(badge_logo())
    assert had_alpha
    a = np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))[:, :, 3]
    assert (a == 255).mean() > 0.99                          # раньше: 31% плашки становилось полупрозрачным
    assert R.logo_colors(png)[0] == "#D7262D"                 # раньше: #F7D8D9
    # непрозрачный логотип на белом по-прежнему очищается от фона
    png2, had2 = R.prepare_logo(make_logo(transparent=False))
    assert not had2


# ============ Стартовые стили под бренд ============
def _logo_layers(sp):
    return [L for s in ("feed", "story") for L in sp[s]["layers"] if L["type"] == "logo"]


def test_preset_spec_fits_brand():
    plain = S.preset_spec("editorial")
    assert all(L["color"]["mode"] == "contrast" for L in _logo_layers(plain) if L["color"]["mode"] != "fixed")
    badge = S.brand_traits(R.prepare_logo(badge_logo())[0], ["#FFFFFF", "#141414", "#D7262D"])
    assert badge["multitone"] and badge["accent_on_dark"]
    for key in S.ONBOARD_PRESETS + ("center", "mark", "carousel"):
        sp = S.preset_spec(key, badge)
        assert all(L["color"] == {"mode": "original"} for L in _logo_layers(sp)), key
    word = S.brand_traits(R.prepare_logo(make_logo())[0], None)
    sp = S.preset_spec("editorial", word)
    assert _logo_layers(sp)[0]["color"]["mode"] == "contrast"
    # ширина по пропорциям: длинная надпись шире, квадрат уже, в пределах 0,6–1,6 от базовой
    base = S.preset_spec("editorial")["feed"]["layers"][1]["w"]
    assert S.preset_spec("editorial", {"aspect": 10})["feed"]["layers"][1]["w"] == pytest.approx(1.6 * base)
    assert S.preset_spec("editorial", {"aspect": 1})["feed"]["layers"][1]["w"] == pytest.approx(0.6 * base)
    assert S.preset_spec("editorial", {"aspect": 3.5})["feed"]["layers"][1]["w"] == pytest.approx(base)
    # тёмный акцент на тёмном затемнении: выделение только весом
    dark = S.brand_traits(None, ["#FFFFFF", "#141414", "#1E3A8A"])
    assert dark["accent_on_dark"] is False
    em = [L for L in S.preset_spec("editorial", dark)["feed"]["layers"] if L.get("source") == "title"][0]["em"]
    assert "color" not in em and em["weight"] == 800
    assert S.brand_traits(b"junk", None)["accent_on_dark"]          # битый логотип — без падения


def test_seed_templates_use_brand_logo(fresh_db):
    bid = db.create_brand(5001, "pilot", 30)
    db.set_asset(bid, "logo", R.prepare_logo(avatar_logo())[0])
    db.seed_templates(bid, "ru")
    tpls = db.list_templates(bid)
    assert len(tpls) == len(S.SEED_PRESETS)
    for t in tpls:
        assert all(L["color"] == {"mode": "original"} for L in _logo_layers(t["spec"]))
    bid2 = db.create_brand(5002, "pilot", 30)
    db.set_asset(bid2, "logo", R.prepare_logo(make_logo())[0])
    db.seed_templates(bid2, "ru")
    assert all(L["color"]["mode"] == "contrast" for t in db.list_templates(bid2) for L in _logo_layers(t["spec"]))
    bid3 = db.create_brand(5003, "pilot", 30)                    # без логотипа — как раньше
    assert len(db.seed_templates(bid3, "ru")) == len(S.SEED_PRESETS)


def test_badge_renders_in_own_colours():
    """Плашка в стиле «Подпись» на светлом фото: красная с белым, а не чёрный прямоугольник."""
    png = R.prepare_logo(badge_logo())[0]
    logo = Image.open(io.BytesIO(png)).convert("RGBA")
    pal = S.sanitize_palette(["#FFFFFF", "#141414", "#D7262D"])
    photo = Image.new("RGB", (1080, 1350), (235, 235, 230))
    for key in S.ONBOARD_PRESETS:
        sp = S.preset_spec(key, S.brand_traits(png, pal))
        L = _logo_layers(sp)[0]
        ctx = R.Ctx(pal, {"logo": logo}, {}, dict(title="", hashtag="", i=1, n=1))
        im = R.render_surface(photo, 1080, 1350, [L], ctx)
        box = ctx.boxes[L["id"]]
        crop = np.asarray(im.crop((box[0] + 5, box[1] + 5, box[0] + box[2] - 5, box[1] + box[3] - 5))).reshape(-1, 3)
        red = ((crop[:, 0] > 180) & (crop[:, 1] < 80)).mean()
        white = (crop.min(1) > 230).mean()
        assert red > 0.3 and white > 0.05, key


# ============ Данные стартовых стилей ============
def test_starter_style_data():
    for p in S.PRESETS:
        sp = S.preset_spec(p["key"])
        for surface in ("feed", "story"):
            for L in sp[surface]["layers"]:
                if L["type"] == "text" and L["source"] == "title":
                    assert L.get("em") and L["em"].get("weight"), (p["key"], surface)
                    if L.get("plate"):
                        assert "color" not in L["em"] and L["em"]["weight"] > L["weight"]
                if L["type"] == "text" and L["source"] == "hashtag":
                    assert L.get("tag") == "clean" and 0 < L["maxw"] <= 0.6, (p["key"], surface)
                if L["type"] == "logo" and surface == "feed":
                    assert L["w"] >= 0.18, p["key"]
        if p["key"] not in ("blank",):
            assert sp["story"]["layers"], p["key"]               # сторис сверстаны для каждого стиля
            assert sp["story"]["enabled"] == S.STARTER_STORIES
    center = S.preset_spec("center")["feed"]["layers"]
    assert center[0]["type"] == "overlay" and center[0]["opacity"] >= 0.5
    assert center[1]["weight"] == 700 and center[1].get("shadow")


def test_long_rubric_cleaned_and_kept_off_logo():
    pal = S.sanitize_palette(["#FFFFFF", "#141414", "#D7262D"])
    logo = Image.open(io.BytesIO(R.prepare_logo(make_logo())[0])).convert("RGBA")
    photo = R.open_photo(make_photo(1080, 1350, seed=4))
    for key in ("editorial", "caption"):
        sp = S.preset_spec(key, {"aspect": logo.width / logo.height})
        ctx = R.Ctx(pal, {"logo": logo}, {}, dict(title="Заголовок", hashtag="#городские_новости_и_происшествия",
                                                   i=1, n=1))
        R.render_surface(photo, 1080, 1350, sp["feed"]["layers"], ctx)
        tag = next(L for L in sp["feed"]["layers"] if L.get("source") == "hashtag")
        lg = next(L for L in sp["feed"]["layers"] if L["type"] == "logo")
        (tx, ty, tw, th), (lx, ly, lw, lh) = ctx.boxes[tag["id"]], ctx.boxes[lg["id"]]
        assert tx + tw <= lx or lx + lw <= tx, key                # рубрика и логотип не пересекаются
        assert R.text_content(tag, ctx) == "ГОРОДСКИЕ НОВОСТИ И ПРОИСШЕСТВИЯ"


def test_bold_highlight_visible():
    pal = S.sanitize_palette(["#FFFFFF", "#141414", "#D7262D"])
    photo = Image.new("RGB", (1080, 1350), (90, 90, 90))
    L = [x for x in S.preset_spec("editorial")["feed"]["layers"] if x.get("source") == "title"][0]

    def red_px(title):
        im = R.render_surface(photo, 1080, 1350, [L], R.Ctx(pal, {}, {}, dict(title=title, i=1, n=1)))
        a = np.asarray(im).astype(int)
        return int(((a[:, :, 0] > 170) & (a[:, :, 1] < 90)).sum())

    assert red_px("Пробки 9 баллов") == 0
    assert red_px("*Пробки 9 баллов*") > 500


# ============ Память при декодировании ============
def _big_png(w, h, orient=None, mode="RGB"):
    a = np.zeros((h, w, 3), np.uint8)
    a[: h // 2, : w // 2] = (220, 30, 30)
    im = Image.fromarray(a, "RGB").convert(mode)
    b = io.BytesIO()
    ex = Image.Exif()
    if orient:
        ex[0x0112] = orient
    im.save(b, "PNG", exif=ex.tobytes(), icc_profile=R.SRGB_ICC)
    return b.getvalue()


def test_assets_capped_at_25mp_before_decoding():
    data = _big_png(6000, 5200, mode="L")                   # 31 Мп
    t = time.time()
    with pytest.raises(R.TooBig):
        R.open_image(data)                                  # логотип, графика, макет
    with pytest.raises(R.TooBig):
        R.prepare_logo(data)
    assert time.time() - t < 2                              # по заголовку, без декодирования
    assert R.open_photo(data).size == (3000, 2600)          # как фото — принимается и уменьшается сразу


def test_big_photo_reduced_early_keeps_orientation():
    out = R.open_photo(_big_png(6000, 5200, orient=6))
    assert out.size == (2600, 3000)                         # повернуто и уменьшено, короткая ≥ PHOTO_SHORT
    a = np.asarray(out)
    assert a[10, -10, 0] > 200 and a[-10, 10, 0] < 50       # красный угол после поворота — справа сверху
    tif = io.BytesIO()
    Image.fromarray(np.zeros((5200, 6000, 3), np.uint8)).save(tif, "TIFF", tiffinfo={274: 8}, compression="tiff_deflate")
    assert R.open_photo(tif.getvalue()).size == (2600, 3000)
    img = Image.open(io.BytesIO(_big_png(6000, 5200)))
    img.load()
    assert R._reduce(img, 2).info.get("icc_profile") == R.SRGB_ICC   # профиль доходит до перевода в sRGB

"""NUMBUS Branding — движок рендера.

Логика перенесена из ÖMANKÖ Post Creator, но все «зашитые» параметры
(логотип, шрифт, угол, размер, цвет) теперь приходят из бренд-кита клиента.
Геометрия задаётся в опорных единицах канваса шириной 1920px и масштабируется.
"""
import io
import os
import math
import hashlib
import logging

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter

try:
    import pillow_avif  # noqa: F401 — AVIF-декодер
except Exception:
    pass
try:
    import pillow_heif
    pillow_heif.register_heif_opener()  # HEIC с айфонов, присланные файлом
except Exception:
    pass

logger = logging.getLogger(__name__)
BASE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(BASE, "fonts")
REF_W = 1920

# ============ Шрифты ============
# tag — вес для хештега, head — вес заголовка обложки,
# ls — трекинг заголовка в ленте (в сторис вдвое плотнее), scale — поправка
# кегля для широких гарнитур.
FONTS = {
    "nunito":     dict(label="Nunito",     file="Nunito.ttf",          tag=600, head=900, ls=-0.03, scale=1.00),
    "inter":      dict(label="Inter",      file="Inter.ttf",           tag=500, head=800, ls=-0.04, scale=1.00),
    "manrope":    dict(label="Manrope",    file="Manrope.ttf",         tag=600, head=800, ls=-0.03, scale=1.00),
    "montserrat": dict(label="Montserrat", file="Montserrat.ttf",      tag=600, head=800, ls=-0.03, scale=0.95),
    "unbounded":  dict(label="Unbounded",  file="Unbounded.ttf",       tag=500, head=700, ls=-0.03, scale=0.80),
    "playfair":   dict(label="Playfair",   file="PlayfairDisplay.ttf", tag=500, head=800, ls=-0.01, scale=1.05),
}
CUSTOM_FONT_SPEC = dict(label="Custom", tag=600, head=900, ls=-0.02, scale=1.00)
_FALLBACK_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_font_cache = {}


def _set_weight(font, wght, display=False):
    """Выставляет вес на вариативном шрифте. Для статичных — тихо ничего."""
    try:
        axes = font.get_variation_axes()
    except Exception:
        return
    vals = []
    for a in axes:
        name = a.get("name", b"")
        if isinstance(name, bytes):
            name = name.decode("latin-1", "ignore")
        lo, hi = a.get("minimum", 0), a.get("maximum", 0)
        if "eight" in name:
            vals.append(max(lo, min(hi, wght)))
        elif "ptical" in name:
            vals.append(hi if display else lo)
        else:
            vals.append(a.get("default", lo))
    try:
        font.set_variation_by_axes(vals)
    except Exception as e:
        logger.warning("Не смог выставить вес шрифта: %s", e)


def font_spec(kit) -> dict:
    return FONTS.get(kit.get("font"), CUSTOM_FONT_SPEC if kit.get("font") == "custom" else FONTS["nunito"])


def get_font(kit, custom_font: bytes, role: str, size: float):
    """role: 'tag' | 'head'."""
    spec = font_spec(kit)
    wght = spec[role]
    size = max(1, round(size))
    if kit.get("font") == "custom" and custom_font:
        key = ("custom", hashlib.md5(custom_font).hexdigest(), role, size)
        if key not in _font_cache:
            try:
                f = ImageFont.truetype(io.BytesIO(custom_font), size)
                _set_weight(f, wght, display=(role == "head"))
                _font_cache[key] = f
            except Exception as e:
                logger.error("Свой шрифт не открылся (%s) — фолбэк Nunito", e)
                return get_font(dict(kit, font="nunito"), None, role, size)
        return _font_cache[key]
    fkey = kit.get("font") if kit.get("font") in FONTS else "nunito"
    key = (fkey, role, size)
    if key not in _font_cache:
        path = os.path.join(FONT_DIR, FONTS[fkey]["file"])
        try:
            f = ImageFont.truetype(path, size)
            _set_weight(f, wght, display=(role == "head"))
        except Exception as e:
            logger.error("Шрифт %s не найден (%s) — системный фолбэк", path, e)
            f = ImageFont.truetype(_FALLBACK_FONT, size) if os.path.exists(_FALLBACK_FONT) \
                else ImageFont.load_default()
        _font_cache[key] = f
    return _font_cache[key]


def validate_font(data: bytes) -> bool:
    try:
        f = ImageFont.truetype(io.BytesIO(data), 40)
        f.getbbox("Aa Жж #")
        return True
    except Exception:
        return False


# ============ Картинки ============
def open_image(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)  # фото с телефона не должны лечь боком
    return img


def open_photo(data: bytes) -> Image.Image:
    return open_image(data).convert("RGB")


def prepare_logo(data: bytes):
    """Готовит логотип клиента: RGBA, обрезан по видимой части.

    Если прозрачности нет (JPG или PNG на фоне), фон определяется по углам,
    а альфа строится из контраста с ним — так работают и «чёрный на белом»,
    и «белый на чёрном». Возвращает (png_bytes, had_alpha).
    """
    img = open_image(data).convert("RGBA")
    if max(img.size) > 3000:
        img.thumbnail((3000, 3000), Image.LANCZOS)
    arr = np.array(img)
    alpha = arr[:, :, 3]
    had_alpha = bool((alpha < 10).mean() > 0.01)
    if not had_alpha:
        rgb = arr[:, :, :3].astype(np.float32)
        gray = rgb @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
        h, w = gray.shape
        k = max(2, int(min(h, w) * 0.05))
        corners = np.concatenate([gray[:k, :k].ravel(), gray[:k, -k:].ravel(),
                                  gray[-k:, :k].ravel(), gray[-k:, -k:].ravel()])
        bg = float(np.median(corners))
        diff = np.abs(gray - bg)
        top = float(np.percentile(diff, 99.5)) or 1.0
        a = np.clip(diff / top * 255.0, 0, 255)
        a[a < 18] = 0
        alpha = a.astype(np.uint8)
        arr[:, :, 3] = alpha
    mask = alpha > 8
    if not mask.any():
        raise ValueError("empty logo")
    ys, xs = np.where(mask)
    arr = arr[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    out = Image.fromarray(arr, "RGBA")
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue(), had_alpha


def sample_image(w=1600, h=2000) -> Image.Image:
    """Нейтральный фон для превью, если клиент не прислал своё фото:
    светлое небо сверху, тёмная земля снизу — видно, как работает адаптивный цвет."""
    y = np.linspace(0, 1, h).reshape(-1, 1)
    x = np.linspace(0, 1, w).reshape(1, -1)
    top = np.array([214, 222, 230], dtype=np.float32)
    mid = np.array([198, 170, 150], dtype=np.float32)
    low = np.array([40, 42, 48], dtype=np.float32)
    t = np.clip(y * 1.4, 0, 1)[..., None]
    col = top * (1 - t) + mid * t
    t2 = np.clip((y - 0.62) * 3.2, 0, 1)[..., None]
    col = col * (1 - t2) + low * t2
    col = col + (x[..., None] - 0.5) * 18
    rng = np.random.default_rng(7)
    col = col + rng.normal(0, 3.5, (h, w, 1))
    img = Image.fromarray(np.clip(np.broadcast_to(col, (h, w, 3)), 0, 255).astype(np.uint8), "RGB")
    sun = Image.new("L", (w, h), 0)
    ImageDraw.Draw(sun).ellipse((w * 0.58, h * 0.18, w * 0.80, h * 0.36), fill=200)
    sun = sun.filter(ImageFilter.GaussianBlur(w * 0.03))
    img = Image.composite(Image.new("RGB", (w, h), (255, 240, 220)), img, sun)
    return img


# ============ Общие утилиты (из ÖMANKÖ) ============
BRIGHTNESS_OFFSET = 45
ALPHA = 0.95


def get_average_color(img, x, y, w, h):
    x, y = max(0, int(x)), max(0, int(y))
    x2, y2 = min(int(x + w), img.width), min(int(y + h), img.height)
    if x2 <= x or y2 <= y:
        return 0.0, 0.0, 0.0
    arr = np.array(img.crop((x, y, x2, y2)).convert("RGB")).reshape(-1, 3).mean(axis=0)
    return float(arr[0]), float(arr[1]), float(arr[2])


def brightness_of(r, g, b):
    return (r * 299 + g * 587 + b * 114) / 1000


def adjust_brightness(r, g, b, percent):
    if percent > 0:
        r, g, b = (c + (255 - c) * percent / 100 for c in (r, g, b))
    else:
        p = abs(percent)
        r, g, b = (c - c * p / 100 for c in (r, g, b))
    return int(min(255, max(0, r))), int(min(255, max(0, g))), int(min(255, max(0, b)))


def adaptive_color(canvas, box):
    x, y, w, h = box
    r, g, b = get_average_color(canvas, x, y, w, h)
    pct = BRIGHTNESS_OFFSET if brightness_of(r, g, b) < 128 else -BRIGHTNESS_OFFSET
    return adjust_brightness(r, g, b, pct)


def fit_image_to_canvas(img, cw, ch):
    """Заполнение канваса с центрированием и обрезкой (cover)."""
    ir, cr = img.width / img.height, cw / ch
    if ir > cr:
        dh = ch
        dw = int(round(dh * ir))
    else:
        dw = cw
        dh = int(round(dw / ir))
    resized = img.resize((dw, dh), Image.LANCZOS)
    return resized.crop(((dw - cw) // 2, (dh - ch) // 2, (dw - cw) // 2 + cw, (dh - ch) // 2 + ch))


def tint(logo: Image.Image, color, alpha=ALPHA):
    """Силуэт логотипа, залитый цветом color (альфа-края сохраняются)."""
    solid = Image.new("RGBA", logo.size, (int(color[0]), int(color[1]), int(color[2]), 0))
    solid.putalpha(logo.split()[3].point(lambda p: int(p * alpha)))
    return solid


def fit_box(ratio, max_w, max_h):
    """Размер лого с пропорцией ratio (w/h), вписанного в max_w×max_h."""
    w = max_w
    h = w / ratio
    if h > max_h:
        h = max_h
        w = h * ratio
    return max(1, round(w)), max(1, round(h))


def draw_tracked(draw, xy, text, font, fill, ls_px):
    """Строка с трекингом. Позиции считаем по префиксам — кернинг не теряется."""
    x0, y = xy
    for i, c in enumerate(text):
        x = x0 + draw.textlength(text[:i], font=font) + ls_px * i
        draw.text((x, y), c, font=font, fill=fill)


def tracked_width(draw, text, font, ls_px):
    if not text:
        return 0
    return draw.textlength(text, font=font) + ls_px * (len(text) - 1)


# ============ Бренд-контекст ============
class Brand:
    """Всё, что нужно рендеру: кит + открытые ассеты."""

    def __init__(self, kit: dict, logo_png: bytes, cover_logo_png: bytes = None, font: bytes = None):
        self.kit = kit
        self.logo = Image.open(io.BytesIO(logo_png)).convert("RGBA") if logo_png else None
        self.cover_logo = Image.open(io.BytesIO(cover_logo_png)).convert("RGBA") if cover_logo_png else self.logo
        self.font = font

    def f(self, role, size):
        return get_font(self.kit, self.font, role, size)


# ============ БРЕНДИНГ ============
FORMATS = {
    "4:5":  (1920, 2400),
    "3:4":  (1920, 2560),
    "1:1":  (1920, 1920),
    "9:16": (1080, 1920),
    "3:2":  (1920, 1280),
    "orig": None,
}
SIZE_AREA = {"s": 4000, "m": 7000, "l": 12000}    # «визуальный вес» лого, px² на 1920
SIZE_MAX_H = {"s": 80, "m": 105, "l": 140}
SIZE_MAX_W = {"s": 300, "m": 380, "l": 480}
TAG_SIZE = {"s": 44, "m": 51, "l": 60}
MARGIN_X = 90
MARGIN_Y = 72


def canvas_size(img, fmt_key):
    fmt = FORMATS.get(fmt_key)
    if fmt:
        return fmt
    w, h = img.size
    target = min(max(w, 1920), 2560)
    return target, int(round(h * target / w))


def logo_size(ratio, size_key, scale):
    area = SIZE_AREA[size_key]
    w = math.sqrt(area * ratio)
    h = math.sqrt(area / ratio)
    k = min(1.0, SIZE_MAX_H[size_key] / h, SIZE_MAX_W[size_key] / w)
    return max(1, round(w * k * scale)), max(1, round(h * k * scale))


def render_branding(img: Image.Image, fmt_key: str, hashtag: str, brand: Brand) -> Image.Image:
    kit = brand.kit
    cw, ch = canvas_size(img, fmt_key)
    s = cw / REF_W
    canvas = fit_image_to_canvas(img, cw, ch).convert("RGBA")
    pos, size = kit.get("pos", "bl"), kit.get("size", "m")
    mode = kit.get("color", "adaptive")
    mx, my = round(MARGIN_X * s), round(MARGIN_Y * s)

    # --- Лого ---
    lw = lh = 0
    lx = mx
    ly = ch - my
    if brand.logo is not None:
        lw, lh = logo_size(brand.logo.width / brand.logo.height, size, s)
        lx = mx if pos[1] == "l" else cw - mx - lw
        ly = ch - my - lh if pos[0] == "b" else my
        logo = brand.logo.resize((lw, lh), Image.LANCZOS)
        if mode == "original":
            canvas.alpha_composite(logo, (lx, ly))
        else:
            color = {"white": (255, 255, 255), "black": (0, 0, 0)}.get(mode) or \
                adaptive_color(canvas, (lx, ly, lw, lh))
            canvas.alpha_composite(tint(logo, color), (lx, ly))

    # --- Хештег: противоположный угол той же строки, центр по оси лого ---
    if hashtag:
        font = brand.f("tag", TAG_SIZE[size] * s)
        d = ImageDraw.Draw(canvas)
        bb = d.textbbox((0, 0), hashtag, font=font)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        axis = (ly + lh / 2) if lh else (ch - my - th / 2 if pos[0] == "b" else my + th / 2)
        tx = cw - mx - tw if pos[1] == "l" else mx
        ty = axis - th / 2
        if mode in ("white", "black"):
            color = (255, 255, 255) if mode == "white" else (0, 0, 0)
        else:
            color = adaptive_color(canvas, (tx, ty - 10 * s, tw, th + 20 * s))
        d.text((tx - bb[0], ty - bb[1]), hashtag, font=font,
               fill=(color[0], color[1], color[2], int(255 * ALPHA)))

    return canvas.convert("RGB")


# ============ ОБЛОЖКА ============
COVER_FORMATS = {
    "4:5":  dict(size=(1920, 2400), title=135, title_bottom=365),
    "3:4":  dict(size=(1920, 2560), title=135, title_bottom=385),
    "1:1":  dict(size=(1920, 1920), title=120, title_bottom=330),
    "3:2":  dict(size=(1920, 1280), title=110, title_bottom=250),
    "orig": dict(size=None, title=135, title_bottom=365),
}
BUBBLE_H = 126
BUBBLE_TOP = 68
BUBBLE_PAD_X = 48
BUBBLE_TEXT = 51
BUBBLE_ALPHA = 0.85
FEED_LOGO_BOX = (326, 90)       # лого внизу ленты вписывается в этот бокс
FEED_LOGO_BOTTOM = 65
LINE_SPACING = 1.08
TITLE_MAX_W = 0.88              # заголовок не шире 88% канваса

STORY_SIZE = (1080, 1920)
STORY_TITLE = 77
STORY_LOGO_BOX = (200, 81)
STORY_LOGO_TOP = 168
STORY_GRAD_RISE = 900
STORY_VARIANTS = {
    # плашка-пустышка под стикер-ссылку; цвет инвертный к фону
    "ig": dict(title_bottom=452, b_w=387, b_h=135, b_r=41, b_bottom=215),
    "tg": dict(title_bottom=382, b_w=430, b_h=115, b_r=17, b_bottom=161),
}
STORY_BUBBLE_ALPHA = 0.50

GRAD_ALPHA_DARK = 0.18
GRAD_ALPHA_LIGHT = 0.62
GRAD_ALPHA_CEIL = 0.99
DARK_LEVELS = [0.4, 0.7, 1.0, 1.4, 1.8]
DARK_DEFAULT_IDX = 2


def apply_bottom_gradient(canvas, brightness, rise, dark_level=1.0):
    """Чёрный градиент снизу: адаптивная база по яркости + ручной сдвиг."""
    cw, ch = canvas.size
    t = max(0.0, min(1.0, brightness / 255.0))
    base_alpha = GRAD_ALPHA_DARK + (GRAD_ALPHA_LIGHT - GRAD_ALPHA_DARK) * t
    alpha = max(0.0, min(GRAD_ALPHA_CEIL, base_alpha + (dark_level - 1.0)))
    rise = min(int(rise), ch)
    mask = np.zeros((ch, cw), dtype=np.uint8)
    mask[ch - rise:, :] = np.linspace(0, int(255 * alpha), rise).astype(np.uint8).reshape(-1, 1)
    black = Image.new("RGBA", (cw, ch), (0, 0, 0, 255))
    return Image.composite(black, canvas.convert("RGBA"), Image.fromarray(mask, "L"))


def _wrap_title(draw, text, font, ls_px, max_w):
    """Переносы пользователя сохраняем; слишком длинные строки дорезаем по словам."""
    out = []
    for raw in text.split("\n"):
        words = raw.split(" ")
        line = ""
        for w in words:
            cand = (line + " " + w).strip() if line else w
            if line and tracked_width(draw, cand, font, ls_px) > max_w:
                out.append(line)
                line = w
            else:
                line = cand
        out.append(line)
    return out


def draw_title(canvas, text, brand: Brand, size, ls_ratio, bottom_offset):
    cw, ch = canvas.size
    d = ImageDraw.Draw(canvas)
    max_w = cw * TITLE_MAX_W
    font = brand.f("head", size)
    lines = _wrap_title(d, text, font, round(size * ls_ratio), max_w)
    widest = max((tracked_width(d, ln, font, round(size * ls_ratio)) for ln in lines), default=0)
    if widest > max_w:  # одно слово шире канваса — уменьшаем кегль
        size = size * max_w / widest
        font = brand.f("head", size)
    ls_px = round(size * ls_ratio)
    ascent, descent = font.getmetrics()
    line_adv = int(size * LINE_SPACING)
    last_top = (ch - bottom_offset) - (ascent + descent)
    first_top = last_top - (len(lines) - 1) * line_adv
    for i, ln in enumerate(lines):
        w = tracked_width(d, ln, font, ls_px)
        draw_tracked(d, (cw / 2 - w / 2, first_top + i * line_adv), ln, font, (255, 255, 255, 255), ls_px)


def _paste_logo_centered(canvas, logo, box, cx, y_top, mode):
    if logo is None:
        return
    lw, lh = fit_box(logo.width / logo.height, *box)
    rs = logo.resize((lw, lh), Image.LANCZOS)
    if mode != "original":
        rs = tint(rs, (255, 255, 255), 1.0)  # на обложке поверх градиента — всегда белый
    canvas.alpha_composite(rs, (int(cx - lw / 2), int(y_top)))


def render_cover_feed(img, fmt_key, title, hashtag, brand: Brand, dark_level=1.0):
    spec = COVER_FORMATS.get(fmt_key, COVER_FORMATS["4:5"])
    if spec["size"]:
        cw, ch = spec["size"]
    else:
        cw, ch = canvas_size(img, "orig")
    s = cw / REF_W
    fs = font_spec(brand.kit)
    title_size = spec["title"] * fs["scale"] * s
    title_bottom = spec["title_bottom"] * s

    base = fit_image_to_canvas(img, cw, ch)
    ry = max(0, ch - title_bottom - title_size * 2)
    br = brightness_of(*get_average_color(base, 0, ry, cw, title_size * 2))
    canvas = apply_bottom_gradient(base, br, min(ch, title_bottom + title_size * 4), dark_level)

    draw_title(canvas, title, brand, title_size, fs["ls"], title_bottom)

    if hashtag:
        font = brand.f("tag", BUBBLE_TEXT * s)
        d = ImageDraw.Draw(canvas)
        label = "# " + hashtag.lstrip("#")
        tw = d.textlength(label, font=font)
        bh = round(BUBBLE_H * s)
        bw = int(tw + 2 * BUBBLE_PAD_X * s)
        left, top = int(cw / 2 - bw / 2), round(BUBBLE_TOP * s)
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle((left, top, left + bw, top + bh), radius=bh // 2,
                                                fill=(0, 0, 0, int(255 * BUBBLE_ALPHA)))
        canvas.alpha_composite(layer)
        d = ImageDraw.Draw(canvas)
        bb = d.textbbox((0, 0), label, font=font)
        d.text((cw / 2 - tw / 2, top + bh / 2 - (bb[3] - bb[1]) / 2 - bb[1]), label,
               font=font, fill=(255, 255, 255, 255))

    logo = brand.cover_logo
    if logo is not None:
        lw, lh = fit_box(logo.width / logo.height, FEED_LOGO_BOX[0] * s, FEED_LOGO_BOX[1] * s)
        _paste_logo_centered(canvas, logo, (FEED_LOGO_BOX[0] * s, FEED_LOGO_BOX[1] * s),
                             cw / 2, ch - FEED_LOGO_BOTTOM * s - lh, brand.kit.get("color"))
    return canvas.convert("RGB")


def render_cover_story(img, variant, title, brand: Brand, dark_level=1.0):
    v = STORY_VARIANTS[variant]
    cw, ch = STORY_SIZE
    fs = font_spec(brand.kit)
    tsize = STORY_TITLE * fs["scale"]
    base = fit_image_to_canvas(img, cw, ch)
    ry = max(0, ch - v["title_bottom"] - tsize * 2)
    br = brightness_of(*get_average_color(base, 0, ry, cw, tsize * 2))
    canvas = apply_bottom_gradient(base, br, STORY_GRAD_RISE, dark_level)

    _paste_logo_centered(canvas, brand.cover_logo, STORY_LOGO_BOX, cw / 2, STORY_LOGO_TOP,
                         brand.kit.get("color"))
    draw_title(canvas, title, brand, tsize, fs["ls"] * 2, v["title_bottom"])

    # Пустая плашка под стикер-ссылку: тёмный фон → светлая, светлый → тёмная
    b_w, b_h = v["b_w"], v["b_h"]
    left, top = int(cw / 2 - b_w / 2), ch - v["b_bottom"] - b_h
    dark_bg = brightness_of(*get_average_color(canvas, left, top, b_w, b_h)) < 128
    rgb = (255, 255, 255) if dark_bg else (0, 0, 0)
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle((left, top, left + b_w, top + b_h), radius=v["b_r"],
                                            fill=rgb + (int(255 * STORY_BUBBLE_ALPHA),))
    canvas.alpha_composite(layer)
    return canvas.convert("RGB")


# ============ Вывод ============
def to_jpeg(img, quality=92) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def to_preview(img, max_side=1200) -> bytes:
    im = img.copy()
    im.thumbnail((max_side, max_side), Image.LANCZOS)
    return to_jpeg(im, 85)

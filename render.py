"""NUMBUS Branding — движок рендера v3 (слои).

Шаблон клиента — это JSON со списком слоёв: логотип, текст, фигура, градиент,
затемнение, графика. Движок ничего не знает о конкретном стиле — стиль целиком
задаёт клиент в редакторе (Mini App). Редактор рисует превью на <canvas> по тем же
правилам (webapp.html → renderSurface), поэтому превью совпадает с итогом.

ЕДИНИЦЫ. Все координаты и размеры — доли ШИРИНЫ канваса (W). Так шаблон
одинаково ложится на 4:5, 1:1 и любой другой формат.

ЯКОРЬ. Двухбуквенный: вертикаль t/m/b + горизонталь l/c/r ("bl", "mc"…).
  l: левый край слоя = x·W      r: правый край = W − x·W     c: центр = W/2 + x·W
  t: верх = y·W                 b: низ = H − y·W             m: центр = H/2 + y·W

ТЕКСТ. Блок строк: высота = capH + (n−1)·leading·size. Верх блока — линия
высоты прописных первой строки, низ — базовая линия последней. То есть
«снизу» текст стоит на базовой линии, как в вёрстке. Заголовок и подзаголовок
проходят через типограф (typo.py). Если знака нет в шрифте, он рисуется
шрифтом Inter того же начертания — как это делает браузер в редакторе.
lines — сколько строк максимум: длинный текст сначала уменьшается (до 70%
кегля), потом обрезается многоточием.

СЛАЙДЫ. slides: all | first | rest — слой только на обложке карусели или
только на остальных кадрах. Счётчик «1 / 1» на одиночном фото не рисуется.

ЦВЕТ. {"mode":"fixed","value":"#RRGGBB"|"p0".."p4"} — фиксированный или из палитры;
{"mode":"adaptive"} — тон фона ±45% (светлее на тёмном, темнее на светлом);
{"mode":"contrast","light":..,"dark":..} — светлый на тёмном фоне, тёмный на светлом;
{"mode":"original"} — только для логотипа: цвета файла.

ФОТО В РАМКЕ. Слой photo: фото поста в прямоугольнике (как rect: box или inset), radius,
zoom ≥ 1, fx/fy — своя точка фокуса (иначе — из пульта). Если такой слой есть на кадре,
кадр заливается цветом bg этого слоя, а фото рисуется только в рамке.

ВЫДЕЛЕНИЕ. «*…*» в тексте → стиль L["em"] (color, weight, font). Обратная косая перед * — сама звёздочка.
ПОТОК. L["after"] = id слоя: верх слоя = низ того слоя + gap·W.

ФОТО переводится в sRGB (снимки iPhone — Display P3), JPEG сохраняется 4:4:4.
"""
import io
import os
import re
import hashlib
import logging
import tempfile
from collections import OrderedDict

import numpy as np
from PIL import Image, ImageCms, ImageDraw, ImageFont, ImageOps, ImageFilter

from typo import typograf

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

logger = logging.getLogger(__name__)
BASE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(BASE, "fonts")

# ============ Каталог шрифтов (все с кириллицей и осью веса) ============
FONTS = {
    "inter":      dict(label="Inter",              file="Inter.ttf",              min=100, max=900, group="sans"),
    "onest":      dict(label="Onest",              file="Onest.ttf",              min=100, max=900, group="sans"),
    "golos":      dict(label="Golos",              file="GolosText.ttf",          min=400, max=900, group="sans"),
    "manrope":    dict(label="Manrope",            file="Manrope.ttf",            min=200, max=800, group="sans"),
    "geologica":  dict(label="Geologica",          file="Geologica.ttf",          min=100, max=900, group="sans"),
    "montserrat": dict(label="Montserrat",         file="Montserrat.ttf",         min=100, max=900, group="sans"),
    "jost":       dict(label="Jost",               file="Jost.ttf",               min=100, max=900, group="sans"),
    "rubik":      dict(label="Rubik",              file="Rubik.ttf",              min=300, max=900, group="sans"),
    "nunito":     dict(label="Nunito",             file="Nunito.ttf",             min=200, max=1000, group="round"),
    "comfortaa":  dict(label="Comfortaa",          file="Comfortaa.ttf",          min=300, max=700, group="round"),
    "unbounded":  dict(label="Unbounded",          file="Unbounded.ttf",          min=200, max=900, group="display"),
    "oswald":     dict(label="Oswald",             file="Oswald.ttf",             min=200, max=700, group="display"),
    "playfair":   dict(label="Playfair Display",   file="PlayfairDisplay.ttf",    min=400, max=900, group="serif"),
    "cormorant":  dict(label="Cormorant Garamond", file="CormorantGaramond.ttf",  min=300, max=700, group="serif"),
    "lora":       dict(label="Lora",               file="Lora.ttf",               min=400, max=700, group="serif"),
    "jetbrains":  dict(label="JetBrains Mono",     file="JetBrainsMono.ttf",      min=100, max=800, group="mono"),
}
CUSTOM_FONT_SLOTS = ("font1", "font2", "font3")
DEFAULT_FONT = "inter"
FALLBACK_FONT = "inter"          # знаки, которых нет в шрифте, рисуются им (как в браузере)
_DEJAVU = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_DEJAVU_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_CACHE_MAX = 256
_font_cache = OrderedDict()      # (ключ, вес, кегль) → FreeTypeFont; старые вытесняются
_cover_cache = {}                # ключ шрифта → множество кодов символов

# Знаки, которые должны быть в шрифте бренда (проверка при загрузке своего шрифта)
CHECK_CHARS = ("АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя"
               "0123456789₽№«»„“—–…% ")


def _apply_axes(font, weight, size):
    try:
        axes = font.get_variation_axes()
    except Exception:
        return
    vals = []
    for a in axes:
        name = a.get("name", b"")
        name = name.decode("latin-1", "ignore") if isinstance(name, bytes) else str(name)
        lo, hi, df = a.get("minimum", 0), a.get("maximum", 0), a.get("default", a.get("minimum", 0))
        if "eight" in name:
            vals.append(max(lo, min(hi, weight)))
        elif "ptical" in name:
            vals.append(max(lo, min(hi, size)))  # как font-optical-sizing:auto в браузере
        else:
            vals.append(df)
    try:
        font.set_variation_by_axes(vals)
    except Exception as e:
        logger.warning("variation: %s", e)


def _cache_put(ck, f):
    _font_cache[ck] = f
    _font_cache.move_to_end(ck)
    while len(_font_cache) > FONT_CACHE_MAX:
        _font_cache.popitem(last=False)
    return f


def qsize(size):
    return max(1.0, round(float(size) * 2) / 2)


def _custom_key(data):
    return "c:" + hashlib.md5(data).hexdigest()


def get_font(key, weight, size, customs=None):
    """key — ключ каталога, слот своего шрифта (font1..font3) или «dejavu»."""
    size = qsize(size)
    weight = int(weight or 400)
    if key in CUSTOM_FONT_SLOTS and customs and customs.get(key):
        data = customs[key]
        ck = (_custom_key(data), weight, size)
        f = _font_cache.get(ck)
        if f is not None:
            _font_cache.move_to_end(ck)
            return f
        try:
            f = ImageFont.truetype(io.BytesIO(data), size)
            _apply_axes(f, weight, size)
        except Exception as e:
            logger.error("Свой шрифт не открылся: %s", e)
            return get_font(DEFAULT_FONT, weight, size)
        return _cache_put(ck, f)
    if key == "dejavu":
        path = _DEJAVU_BOLD if weight >= 600 else _DEJAVU
        ck = ("dejavu", weight >= 600, size)
        f = _font_cache.get(ck)
        if f is None:
            f = _cache_put(ck, ImageFont.truetype(path, size) if os.path.exists(path) else ImageFont.load_default(size))
        return f
    if key not in FONTS:
        key = DEFAULT_FONT
    ck = (key, weight, size)
    f = _font_cache.get(ck)
    if f is not None:
        _font_cache.move_to_end(ck)
        return f
    try:
        f = ImageFont.truetype(os.path.join(FONT_DIR, FONTS[key]["file"]), size)
        _apply_axes(f, weight, size)
    except Exception as e:
        logger.error("Шрифт %s: %s — фолбэк", key, e)
        f = ImageFont.truetype(_DEJAVU, size) if os.path.exists(_DEJAVU) else ImageFont.load_default(size)
    return _cache_put(ck, f)


def _cmap_of(src):
    try:
        from fontTools.ttLib import TTFont
        return frozenset(TTFont(src, lazy=True, fontNumber=0).getBestCmap() or {})
    except Exception as e:
        logger.warning("cmap: %s", e)
        return None


def font_cover(key, customs=None):
    """Множество символов шрифта (None — неизвестно, считаем, что есть всё)."""
    if key in CUSTOM_FONT_SLOTS and customs and customs.get(key):
        ck = _custom_key(customs[key])
        if ck not in _cover_cache:
            _cover_cache[ck] = _cmap_of(io.BytesIO(customs[key]))
        return _cover_cache[ck]
    if key == "dejavu":
        if "dejavu" not in _cover_cache:
            _cover_cache["dejavu"] = _cmap_of(_DEJAVU) if os.path.exists(_DEJAVU) else None
        return _cover_cache["dejavu"]
    key = key if key in FONTS else DEFAULT_FONT
    if key not in _cover_cache:
        _cover_cache[key] = _cmap_of(os.path.join(FONT_DIR, FONTS[key]["file"]))
    return _cover_cache[key]


def missing_chars(data: bytes, chars=CHECK_CHARS) -> str:
    """Каких знаков из списка нет в загруженном шрифте."""
    cov = _cmap_of(io.BytesIO(data))
    if cov is None:
        return ""
    return "".join(ch for ch in chars if ord(ch) not in cov)


class Face:
    """Шрифт слоя с запасными: основной → Inter того же веса → DejaVu.
    Строка режется на куски по тому, какой шрифт знает знак."""

    def __init__(self, key, weight, size, customs=None):
        self.key = key if (key in FONTS or (key in CUSTOM_FONT_SLOTS and customs and customs.get(key))) else DEFAULT_FONT
        self.weight, self.size, self.customs = weight, qsize(size), customs
        self.font = get_font(self.key, weight, size, customs)
        chain = [self.key] + ([FALLBACK_FONT] if self.key != FALLBACK_FONT else []) + ["dejavu"]
        self.chain = [(k, font_cover(k, customs)) for k in chain]
        self._fonts = {}

    def _font(self, k):
        if k not in self._fonts:
            self._fonts[k] = self.font if k == self.key else get_font(k, self.weight, self.size, self.customs)
        return self._fonts[k]

    def _pick(self, ch):
        cp = ord(ch)
        for k, cov in self.chain:
            if cov is None or cp in cov:
                return k
        return self.key

    def runs(self, s):
        out, cur, buf = [], None, []
        for ch in s:
            k = self._pick(ch)
            if k != cur and buf:
                out.append(("".join(buf), self._font(cur)))
                buf = []
            cur = k
            buf.append(ch)
        if buf:
            out.append(("".join(buf), self._font(cur)))
        return out

    def length(self, s):
        return sum(f.getlength(t) for t, f in self.runs(s)) if s else 0.0


def validate_font(data: bytes) -> bool:
    try:
        f = ImageFont.truetype(io.BytesIO(data), 40)
        f.getbbox("Aa Жж #")
        return True
    except Exception:
        return False


def font_name(data: bytes, fallback: str) -> str:
    try:
        fam, style = ImageFont.truetype(io.BytesIO(data), 20).getname()
        return (fam or fallback)[:40]
    except Exception:
        return fallback


# ============ Цветовой профиль ============
_SRGB = ImageCms.createProfile("sRGB")
SRGB_ICC = ImageCms.ImageCmsProfile(_SRGB).tobytes()


def to_srgb(img):
    """Картинка с профилем (Display P3, Adobe RGB, CMYK) → sRGB. Без профиля — как есть."""
    icc = img.info.get("icc_profile")
    if not icc:
        return img
    try:
        src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        desc = (ImageCms.getProfileDescription(src) or "").lower()
        if "srgb" in desc and img.mode in ("RGB", "RGBA"):
            return img
        if img.mode not in ("RGB", "RGBA", "CMYK", "L"):
            img = img.convert("RGBA" if "A" in img.mode or "transparency" in img.info else "RGB")
        out_mode = "RGBA" if img.mode == "RGBA" else "RGB"
        out = ImageCms.profileToProfile(img, src, _SRGB, renderingIntent=ImageCms.Intent.PERCEPTUAL,
                                        outputMode=out_mode)
        if out is None:
            return img
        out.info.pop("icc_profile", None)
        return out
    except Exception as e:
        logger.info("ICC: %s", e)
        return img


# ============ Картинки ============
# 200-Мп снимки с телефонов открываются: JPEG декодируется сразу уменьшенным (draft),
# а остальные форматы больше MAX_PIXELS не принимаются — иначе не хватит памяти.
Image.MAX_IMAGE_PIXELS = 260_000_000
MAX_PIXELS = 100_000_000
PHOTO_SHORT = 2560     # короткой стороны фото с запасом хватает на любой формат вывода


class TooBig(ValueError):
    """Картинка больше MAX_PIXELS: не открываем, чтобы не съесть память сервера."""


def open_image(data: bytes, short: int = None) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    W, H = img.size
    # MPO — тот же JPEG (так сохраняют Samsung и iPhone с HDR), draft работает и для него
    if short and img.format in ("JPEG", "MPO"):
        s = next((a for a in (8, 4, 2) if min(W, H) // a >= short), 1)
        if s > 1:
            img.draft(img.mode, (W // s, H // s))
    if img.size[0] * img.size[1] > MAX_PIXELS:
        raise TooBig(f"image too big: {W}x{H}")
    img = ImageOps.exif_transpose(img)
    return to_srgb(img)


def open_photo(data: bytes) -> Image.Image:
    return open_image(data, PHOTO_SHORT).convert("RGB")


_SVG_IMG = re.compile(rb"<image\b[^>]*>", re.I)
_SVG_DANGER = re.compile(rb"<!DOCTYPE[^>]*(\[[\s\S]*?\])?\s*>|<script\b[\s\S]*?</script>", re.I)


_RASTER_SIGS = (b"\x89PNG", b"\xff\xd8", b"GIF8", b"RIFF", b"BM", b"II*\x00", b"MM\x00*")


def is_svg(data: bytes) -> bool:
    if data.startswith(_RASTER_SIGS):
        return False
    head = data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    return head.startswith((b"<?xml", b"<svg", b"<!--", b"<!doctype")) and b"<svg" in data[:65536].lower()


def is_pdf(data: bytes) -> bool:
    """PDF и AI (Illustrator сохраняет PDF-совместимый файл)."""
    return not data.startswith(_RASTER_SIGS) and b"%PDF-" in data[:1024]


def _svg_size(data):
    txt = data[:65536].decode("utf-8", "ignore")
    m = re.search(r"<svg\b[^>]*>", txt, re.I | re.S)
    tag = m.group(0) if m else ""
    vb = re.search(r"viewBox\s*=\s*[\"']\s*([-\d.eE]+)[\s,]+([-\d.eE]+)[\s,]+([\d.eE]+)[\s,]+([\d.eE]+)", tag)
    if vb:
        return float(vb.group(3)), float(vb.group(4))
    w = re.search(r"\bwidth\s*=\s*[\"']\s*([\d.]+)", tag)
    h = re.search(r"\bheight\s*=\s*[\"']\s*([\d.]+)", tag)
    if w and h:
        return float(w.group(1)), float(h.group(1))
    return None


def rasterize_vector(data: bytes, max_side=2000) -> Image.Image:
    """Логотип в SVG или PDF (первая страница) → RGBA на прозрачном фоне."""
    if is_svg(data):
        import resvg_py
        # Внешние картинки и скрипты не нужны логотипу и небезопасны на сервере
        clean = _SVG_DANGER.sub(b"", data)
        clean = _SVG_IMG.sub(lambda m: m.group(0) if b"data:" in m.group(0).lower() else b"", clean)
        size = _svg_size(clean)
        kw = {}
        if size and size[0] > 0 and size[1] > 0:
            if size[0] >= size[1]:
                kw["width"] = max_side
            else:
                kw["height"] = max_side
        else:
            kw["width"] = max_side
        with tempfile.TemporaryDirectory() as empty:
            png = resvg_py.svg_to_bytes(svg_string=clean.decode("utf-8", "replace"), resources_dir=empty, **kw)
        return Image.open(io.BytesIO(bytes(png))).convert("RGBA")
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(data)
    try:
        page = pdf[0]
        w, h = page.get_size()
        scale = max_side / max(w, h, 1)
        bmp = page.render(scale=scale, fill_color=(0, 0, 0, 0), may_draw_forms=True, rev_byteorder=True)
        return bmp.to_pil().convert("RGBA")
    finally:
        pdf.close()


def prepare_logo(data: bytes):
    """RGBA-логотип, обрезанный по видимой части. SVG и PDF растрируются.
    Если прозрачности нет — фон определяется по углам, альфа строится из
    контраста с ним. Возвращает (png_bytes, had_alpha)."""
    if is_svg(data) or is_pdf(data):
        img = rasterize_vector(data)
    else:
        img = open_image(data).convert("RGBA")
    if max(img.size) > 3000:
        img.thumbnail((3000, 3000), Image.LANCZOS)
    arr = np.array(img)
    alpha = arr[:, :, 3]
    had_alpha = bool((alpha < 10).mean() > 0.01)
    if not had_alpha:
        gray = arr[:, :, :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
        h, w = gray.shape
        k = max(2, int(min(h, w) * 0.05))
        corners = np.concatenate([gray[:k, :k].ravel(), gray[:k, -k:].ravel(),
                                  gray[-k:, :k].ravel(), gray[-k:, -k:].ravel()])
        diff = np.abs(gray - float(np.median(corners)))
        top = float(np.percentile(diff, 99.5)) or 1.0
        a = np.clip(diff / top * 255.0, 0, 255)
        a[a < 18] = 0
        alpha = a.astype(np.uint8)
        arr[:, :, 3] = alpha
    mask = alpha > 8
    if not mask.any():
        raise ValueError("empty logo")
    ys, xs = np.where(mask)
    out = Image.fromarray(arr[ys.min():ys.max() + 1, xs.min():xs.max() + 1], "RGBA")
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue(), had_alpha


def logo_colors(png: bytes, k=3):
    """Фирменные цвета из логотипа: до k заметных цветов, без белого, чёрного и серого."""
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    img.thumbnail((240, 240), Image.LANCZOS)
    arr = np.asarray(img).reshape(-1, 4)
    px = arr[arr[:, 3] > 170][:, :3]
    if len(px) < 30:
        return []
    q = Image.fromarray(px.reshape(1, -1, 3).astype(np.uint8), "RGB").quantize(colors=8, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()[:8 * 3]
    found = []
    for cnt, idx in sorted(q.getcolors() or [], reverse=True):
        rgb = tuple(pal[idx * 3: idx * 3 + 3])
        if cnt < len(px) * 0.04:
            continue
        if max(rgb) - min(rgb) < 30:          # белый, чёрный, серый — не фирменный цвет
            continue
        if any(sum((a - b) ** 2 for a, b in zip(rgb, c)) < 45 ** 2 for c in found):
            continue
        found.append(rgb)
    return ["#%02X%02X%02X" % c for c in found[:k]]


def sample_image(w=1600, h=2000) -> Image.Image:
    """Нейтральный фон для превью: светлый верх, тёмный низ."""
    y = np.linspace(0, 1, h).reshape(-1, 1)
    x = np.linspace(0, 1, w).reshape(1, -1)
    top, mid, low = (np.array(c, dtype=np.float32) for c in ([214, 222, 230], [198, 170, 150], [40, 42, 48]))
    t = np.clip(y * 1.4, 0, 1)[..., None]
    col = top * (1 - t) + mid * t
    t2 = np.clip((y - 0.62) * 3.2, 0, 1)[..., None]
    col = col * (1 - t2) + low * t2 + (x[..., None] - 0.5) * 18
    col = col + np.random.default_rng(7).normal(0, 3.5, (h, w, 1))
    img = Image.fromarray(np.clip(np.broadcast_to(col, (h, w, 3)), 0, 255).astype(np.uint8), "RGB")
    sun = Image.new("L", (w, h), 0)
    ImageDraw.Draw(sun).ellipse((w * 0.58, h * 0.18, w * 0.80, h * 0.36), fill=200)
    return Image.composite(Image.new("RGB", (w, h), (255, 240, 220)), img,
                           sun.filter(ImageFilter.GaussianBlur(w * 0.03)))


ZOOM_MAX = 4.0


def cover_box(iw, ih, cw, ch, focus=None, zoom=1.0):
    """Как фото ляжет в кадр: (dw, dh, x0, y0). focus — (fx, fy) от 0 до 1:
    какая часть фото остаётся в кадре при обрезке (0.5 — середина).
    zoom ≥ 1 — фото крупнее, чем «заполнить кадр»."""
    fx, fy = focus or (0.5, 0.5)
    z = max(1.0, min(ZOOM_MAX, float(zoom or 1.0)))
    ir, cr = iw / ih, cw / ch
    if ir > cr:
        dh, dw = ch, ch * ir
    else:
        dw, dh = cw, cw / ir
    dw, dh = int(round(dw * z)), int(round(dh * z))
    return dw, dh, int((dw - cw) * fx), int((dh - ch) * fy)


def fit_cover(img, cw, ch, focus=None, zoom=1.0):
    dw, dh, x0, y0 = cover_box(img.width, img.height, cw, ch, focus, zoom)
    if (x0, y0, dw, dh) == (0, 0, cw, ch) and img.size == (cw, ch):
        return img.copy()
    # большие масштабы: режем сначала исходник, потом уменьшаем — без лишней памяти
    sx, sy = img.width / dw, img.height / dh
    box = (x0 * sx, y0 * sy, (x0 + cw) * sx, (y0 + ch) * sy)
    return img.resize((cw, ch), Image.LANCZOS, box=box)


def crop_share(iw, ih, cw, ch):
    """Какая доля фото уходит при обрезке под формат (0 — ничего)."""
    dw, dh, _, _ = cover_box(iw, ih, cw, ch)
    return 1 - (cw * ch) / (dw * dh)


# ============ Цвет ============
def hex_rgb(s, default=(255, 255, 255)):
    try:
        s = s.lstrip("#")
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except Exception:
        return default


def avg_color(img, box):
    x, y, w, h = box
    x0, y0 = max(0, int(x)), max(0, int(y))
    x1, y1 = min(int(x + w), img.width), min(int(y + h), img.height)
    if x1 <= x0 or y1 <= y0:
        return 0.0, 0.0, 0.0
    a = np.asarray(img.crop((x0, y0, x1, y1)).convert("RGB"), dtype=np.float32).reshape(-1, 3).mean(axis=0)
    return float(a[0]), float(a[1]), float(a[2])


def luma(r, g, b):
    return (r * 299 + g * 587 + b * 114) / 1000


def shift_tone(r, g, b, pct):
    if pct > 0:
        r, g, b = (c + (255 - c) * pct / 100 for c in (r, g, b))
    else:
        r, g, b = (c - c * (-pct) / 100 for c in (r, g, b))
    return tuple(int(min(255, max(0, round(c)))) for c in (r, g, b))


class Ctx:
    """Всё, что нужно слоям: палитра, логотипы, свои шрифты, значения полей."""

    def __init__(self, palette=None, logos=None, customs=None, fields=None, dark=0.0, images=None, focus=None,
                 zoom=1.0):
        self.palette = palette or []
        self.logos = logos or {}          # {"logo": RGBA Image, "logo_alt": ...}
        self.customs = customs or {}      # {"font1": bytes, ...}
        self.fields = fields or {}        # title, subtitle, hashtag, i, n
        self.dark = float(dark)
        self.images = images              # callable(asset_id) -> RGBA Image | None (графика из макетов)
        self.focus = focus                # (fx, fy) — точка фокуса фото
        self.zoom = zoom or 1.0           # масштаб фото поверх «заполнить кадр» (пульт поста)
        self.photo = None                 # фото поста — для слоя «фото в рамке»
        self.boxes = {}                   # id слоя → (left, top, w, h): для текста «под слоем»
        self.notes = set()                # что пришлось сделать с текстом: title_cut, title_small

    def image(self, asset):
        if not self.images:
            return None
        try:
            return self.images(asset)
        except Exception as e:
            logger.warning("картинка %s: %s", asset, e)
            return None

    def ref(self, v, default="#FFFFFF"):
        v = v or default
        if isinstance(v, str) and len(v) == 2 and v[0] == "p" and v[1].isdigit():
            idx = int(v[1])
            v = self.palette[idx] if idx < len(self.palette) else default
        return hex_rgb(v)


def resolve_color(spec, ctx, canvas, box):
    """→ (r,g,b) или None (оригинальные цвета логотипа)."""
    spec = spec or {"mode": "fixed", "value": "#FFFFFF"}
    mode = spec.get("mode", "fixed")
    if mode == "original":
        return None
    if mode == "fixed":
        return ctx.ref(spec.get("value"))
    r, g, b = avg_color(canvas, box)
    dark_bg = luma(r, g, b) < 128
    if mode == "contrast":
        return ctx.ref(spec.get("light"), "#FFFFFF") if dark_bg else ctx.ref(spec.get("dark"), "#000000")
    return shift_tone(r, g, b, 45 if dark_bg else -45)  # adaptive


# ============ Геометрия ============
def place(anchor, x, y, w, h, W, H):
    v, hz = (anchor or "bl")[0], (anchor or "bl")[1]
    left = x * W if hz == "l" else (W - x * W - w if hz == "r" else W / 2 + x * W - w / 2)
    top = y * W if v == "t" else (H - y * W - h if v == "b" else H / 2 + y * W - h / 2)
    return left, top


def _rounded_layer(size, box, radius, fill, stroke=0):
    lay = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    x0, y0, x1, y1 = box
    if x1 - x0 < 1 or y1 - y0 < 1:
        return lay
    r = max(0, min(radius, (x1 - x0) / 2, (y1 - y0) / 2))
    if stroke > 0:
        d.rounded_rectangle(box, radius=r, outline=fill, width=max(1, int(round(stroke))))
    else:
        d.rounded_rectangle(box, radius=r, fill=fill)
    return lay


# ============ Текст ============
TEXT_LINES_DEFAULT = 5            # заголовок и подзаголовок без настройки — не больше 5 строк
SHRINK_STEPS = 6                  # длинный текст уменьшается шагами по 5% до 70% кегля
ELLIPSIS = "…"


def apply_case(s, case):
    return s.upper() if case == "upper" else (s.lower() if case == "lower" else s)


def clean_tag(s):
    """Рубрика без решётки, «_» → пробел: #Vive_la_différence → Vive la différence."""
    return (s or "").lstrip("#").replace("_", " ").strip()


def text_content(L, ctx):
    src = L.get("source", "static")
    f = ctx.fields
    if src == "title":
        s = typograf(f.get("title") or "")
    elif src == "subtitle":
        s = typograf(f.get("subtitle") or "")
    elif src == "hashtag":
        s = f.get("hashtag") or ""
        if L.get("tag") == "clean":
            s = clean_tag(s)
    elif src == "counter":
        n = int(f.get("n") or 0)
        s = (L.get("text") or "{i} / {n}").replace("{i}", str(f.get("i", 1))).replace("{n}", str(n)) if n > 1 else ""
    else:
        s = L.get("text") or ""
    return apply_case(s, L.get("case", "none"))


def max_lines(L):
    v = L.get("lines")
    if v is None:
        return TEXT_LINES_DEFAULT if L.get("source") in ("title", "subtitle") else 0
    return int(v)


# ============ Выделение в тексте ============
# «*слово*» в заголовке, подзаголовке или своём тексте рисуется стилем выделения слоя (L["em"]:
# цвет, вес, шрифт). Жирный текст в подписи Telegram бот переводит в звёздочки сам.
# «\*» — сама звёздочка; непарная звёздочка остаётся знаком.
MARK = "*"


def parse_marks(s):
    """→ (текст без разметки, маска выделения по знакам)."""
    toks, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s) and s[i + 1] == MARK:
            toks.append((MARK, False)); i += 2
            continue
        toks.append((c, c == MARK)); i += 1
    marks = [k for k, (_, m) in enumerate(toks) if m]
    if len(marks) % 2:
        toks[marks[-1]] = (MARK, False)
    out, mask, em = [], [], False
    for c, m in toks:
        if m:
            em = not em
            continue
        out.append(c); mask.append(em)
    return "".join(out), mask


class Styled:
    """Два начертания слоя: основное и выделение. Без выделения — ровно как раньше."""

    def __init__(self, L, size, customs):
        key, weight = L.get("font", DEFAULT_FONT), L.get("weight", 400)
        em = L.get("em") or {}
        self.face = Face(key, weight, size, customs)
        ekey, ew = em.get("font") or key, int(em.get("weight") or weight)
        self.eface = self.face if (ekey, ew) == (key, weight) else Face(ekey, ew, size, customs)
        self.size = self.face.size

    def segs(self, s, mask):
        out, start = [], 0
        for k in range(1, len(s) + 1):
            if k == len(s) or mask[k] != mask[start]:
                out.append((s[start:k], mask[start]))
                start = k
        return out

    def length(self, s, mask, ls):
        if not s:
            return 0.0
        return sum((self.eface if e else self.face).length(t) for t, e in self.segs(s, mask)) + ls * (len(s) - 1)


def wrap(text, mask, sf, ls, maxw):
    """Строки как (текст, маска). Переносы — по пробелам и по «\\n» из подписи."""
    out, pos = [], 0
    for para in text.split("\n"):
        pm = mask[pos:pos + len(para)]
        pos += len(para) + 1
        words, k = [], 0
        for w in para.split(" "):
            words.append((k, k + len(w)))
            k += len(w) + 1
        a = b = None
        for ws, we in words:
            if a is None:
                a, b = ws, we
                continue
            if maxw > 0 and sf.length(para[a:we], pm[a:we], ls) > maxw:
                out.append((para[a:b], pm[a:b]))
                a = ws
            b = we
        out.append((para[a:b], pm[a:b]) if a is not None else ("", []))
    return out


def _truncate(lines, n, sf, ls, maxw):
    keep = lines[:n]
    s, m = keep[-1]
    while maxw > 0 and " " in s and sf.length(s + ELLIPSIS, m + [False], ls) > maxw:
        k = s.rfind(" ")
        s, m = s[:k], m[:k]
    k = len(s.rstrip(" ,;:—–-"))
    keep[-1] = (s[:k] + ELLIPSIS, m[:k] + [bool(m[k - 1]) if k else False])
    return keep


def tracked_w(face, s, ls):
    return face.length(s) + ls * (len(s) - 1) if s else 0.0


def layout_text(L, ctx, content, W):
    """Строки, шрифт и габариты блока. Общая логика с webapp.html → layoutText."""
    text, mask = parse_marks(content)
    size0 = float(L.get("size", 0.04)) * W
    tr = float(L.get("tracking", 0))
    maxw = float(L.get("maxw", 0)) * W
    nmax = max_lines(L)
    size = size0
    sf = Styled(L, size, ctx.customs)
    ls = tr * size
    lines = wrap(text, mask, sf, ls, maxw)
    small = cut = False
    if nmax > 0 and len(lines) > nmax:
        if maxw > 0:
            for k in range(1, SHRINK_STEPS + 1):
                size = size0 * (1 - 0.05 * k)
                sf = Styled(L, size, ctx.customs)
                ls = tr * size
                lines = wrap(text, mask, sf, ls, maxw)
                if len(lines) <= nmax:
                    break
            small = True
        if len(lines) > nmax:
            lines = _truncate(lines, nmax, sf, ls, maxw)
            cut = True
    widest = max((sf.length(s, m, ls) for s, m in lines), default=0)
    if maxw > 0 and widest > maxw:  # одно слово шире рамки — уменьшаем кегль
        size = size * maxw / widest
        sf = Styled(L, size, ctx.customs)
        ls = tr * size
        widest = max((sf.length(s, m, ls) for s, m in lines), default=0)
    font = sf.face.font
    cap = -font.getbbox("H", anchor="ls")[1]
    adv = float(L.get("leading", 1.1)) * size
    return dict(lines=lines, sf=sf, face=sf.face, font=font, ls=ls, size=size, cap=cap, adv=adv,
                w=widest, h=cap + (len(lines) - 1) * adv, small=small, cut=cut)


def _draw_line(d, sf, line, bx, by, ls, fills):
    s, mask = line
    x = bx
    for seg, em in sf.segs(s, mask):
        face, fill = (sf.eface, fills[1]) if em else (sf.face, fills[0])
        for text, font in face.runs(seg):
            if ls == 0:
                d.text((x, by), text, font=font, fill=fill, anchor="ls")
                x += font.getlength(text)
            else:
                for ch in text:
                    d.text((x, by), ch, font=font, fill=fill, anchor="ls")
                    x += font.getlength(ch) + ls


def draw_text_layer(canvas, L, ctx):
    W, H = canvas.size
    content = text_content(L, ctx)
    if not content.strip():
        return None
    m = layout_text(L, ctx, content, W)
    if L.get("source") in ("title", "subtitle"):
        if m["cut"]:
            ctx.notes.add(L["source"] + "_cut")
        elif m["small"]:
            ctx.notes.add(L["source"] + "_small")
    plate = L.get("plate")
    padx = float(plate.get("padx", 0.8)) * m["size"] if plate else 0
    pady = float(plate.get("pady", 0.5)) * m["size"] if plate else 0
    bw, bh = m["w"] + 2 * padx, m["h"] + 2 * pady
    left, top = place(L.get("anchor", "bl"), float(L.get("x", 0)), float(L.get("y", 0)), bw, bh, W, H)
    prev = ctx.boxes.get(L.get("after")) if L.get("after") else None
    if prev:   # поток: слой стоит под другим слоем, отступ — в долях ширины
        top = prev[1] + prev[3] + float(L.get("gap", 0.02)) * W
    opacity = float(L.get("opacity", 1))

    if plate:
        pc = resolve_color(plate.get("color"), ctx, canvas, (left, top, bw, bh)) or (0, 0, 0)
        pa = int(round(255 * float(plate.get("opacity", 1)) * opacity))
        radius = float(plate.get("radius", 0)) * bh
        canvas.alpha_composite(_rounded_layer(canvas.size, (left, top, left + bw, top + bh), radius, pc + (pa,)))

    tx, ty = left + padx, top + pady
    col = resolve_color(L.get("color"), ctx, canvas, (tx, ty, m["w"], m["h"])) or (255, 255, 255)
    em = L.get("em") or {}
    ecol = resolve_color(em["color"], ctx, canvas, (tx, ty, m["w"], m["h"])) if em.get("color") else None
    a = int(round(255 * opacity))
    fills = (col + (a,), (ecol or col) + (a,))
    shadow = L.get("shadow")
    blur = float(shadow.get("blur", 0.3)) * m["size"] if shadow else 0.0
    # Отдельный слой → корректное наложение полупрозрачного текста и тени
    pad = int(m["size"] + 2 * blur)
    lx0, ly0 = int(tx) - pad, int(ty) - pad
    lay = Image.new("RGBA", (int(m["w"]) + 2 * pad + 2, int(m["h"]) + 2 * pad + 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    align = L.get("align", "left")
    sf, ls = m["sf"], m["ls"]
    for i, ln in enumerate(m["lines"]):
        lw = sf.length(ln[0], ln[1], ls)
        off = 0 if align == "left" else ((m["w"] - lw) / 2 if align == "center" else m["w"] - lw)
        _draw_line(d, sf, ln, tx + off - lx0, ty + m["cap"] + i * m["adv"] - ly0, ls, fills)
    if shadow and blur > 0:
        sc = resolve_color(shadow.get("color"), ctx, canvas, (tx, ty, m["w"], m["h"])) or (0, 0, 0)
        sa = float(shadow.get("opacity", 0.5))
        mask = lay.split()[3].filter(ImageFilter.GaussianBlur(blur / 2))
        sh = Image.new("RGBA", lay.size, sc + (0,))
        sh.putalpha(mask.point(lambda p: int(round(p * sa))))
        over(canvas, sh, lx0, ly0)
    over(canvas, lay, lx0, ly0)
    return (left, top, bw, bh)


def over(canvas, lay, x, y):
    """alpha_composite с обрезкой по краям канваса (слой может выходить за край)."""
    x, y = int(x), int(y)
    sx0, sy0 = max(0, -x), max(0, -y)
    dx0, dy0 = max(0, x), max(0, y)
    w = min(lay.width - sx0, canvas.width - dx0)
    h = min(lay.height - sy0, canvas.height - dy0)
    if w <= 0 or h <= 0:
        return
    canvas.alpha_composite(lay.crop((sx0, sy0, sx0 + w, sy0 + h)), (dx0, dy0))


# ============ Остальные слои ============
def tint(logo, color, alpha):
    solid = Image.new("RGBA", logo.size, color + (0,))
    solid.putalpha(logo.split()[3].point(lambda p: int(round(p * alpha))))
    return solid


def draw_logo_layer(canvas, L, ctx):
    W, H = canvas.size
    logo = ctx.logos.get(L.get("asset", "logo")) or ctx.logos.get("logo")
    if logo is None:
        return None
    w = max(1, int(round(float(L.get("w", 0.08)) * W)))
    h = max(1, int(round(w * logo.height / logo.width)))
    left, top = place(L.get("anchor", "bl"), float(L.get("x", 0)), float(L.get("y", 0)), w, h, W, H)
    left, top = int(round(left)), int(round(top))
    rs = logo.resize((w, h), Image.LANCZOS)
    opacity = float(L.get("opacity", 1))
    col = resolve_color(L.get("color"), ctx, canvas, (left, top, w, h))
    if col is None:
        a = rs.split()[3].point(lambda p: int(round(p * opacity)))
        rs.putalpha(a)
        piece = rs
    else:
        piece = tint(rs, col, opacity)
    over(canvas, piece, left, top)
    return (left, top, w, h)


def rect_box(L, W, H):
    if L.get("fit") == "inset":
        m = float(L.get("m", 0.04)) * W
        return m, m, W - 2 * m, H - 2 * m
    w, h = float(L.get("w", 0.2)) * W, float(L.get("h", 0.1)) * W
    left, top = place(L.get("anchor", "mc"), float(L.get("x", 0)), float(L.get("y", 0)), w, h, W, H)
    return left, top, w, h


def draw_rect_layer(canvas, L, ctx):
    W, H = canvas.size
    left, top, w, h = rect_box(L, W, H)
    col = resolve_color(L.get("color"), ctx, canvas, (left, top, w, h)) or (0, 0, 0)
    a = int(round(255 * float(L.get("opacity", 1))))
    radius = float(L.get("radius", 0)) * min(w, h) / 2
    stroke = float(L.get("stroke", 0)) * W
    canvas.alpha_composite(_rounded_layer(canvas.size, (left, top, left + w, top + h), radius, col + (a,), stroke))
    return (left, top, w, h)


# Плавная кривая затемнения (easing gradient): у края кадра — полная плотность,
# к границе зоны — ноль без видимой ступеньки. Те же точки — в webapp.html → drawGradient.
EASE_POS = (0, 0.19, 0.34, 0.47, 0.565, 0.65, 0.73, 0.802, 0.861, 0.91, 0.952, 0.982, 1)
EASE_VAL = (1, 0.738, 0.541, 0.382, 0.278, 0.194, 0.126, 0.075, 0.042, 0.021, 0.008, 0.002, 0)


def gradient_ramp(ext, alpha):
    """Плотность 0..255 от края кадра (индекс 0) внутрь зоны, с лёгким шумом против полос."""
    p = (np.arange(ext, dtype=np.float64) + 0.5) / ext
    return np.interp(p, EASE_POS, EASE_VAL) * 255.0 * alpha


def draw_gradient_layer(canvas, L, ctx):
    W, H = canvas.size
    side = L.get("side", "bottom")
    vertical = side in ("bottom", "top")
    D = H if vertical else W
    st = int(round(float(L.get("start", 0)) * D))     # сплошная часть у края: градиент начинается дальше
    ext = int(round(float(L.get("extent", 0.4)) * D))
    ext = max(1, min(ext, D - st))
    tot = st + ext
    zone = {"bottom": (0, H - tot, W, tot), "top": (0, 0, W, tot),
            "left": (0, 0, tot, H), "right": (W - tot, 0, tot, H)}[side]
    alpha = float(L.get("opacity", 0.6))
    if L.get("adaptive"):
        alpha *= 0.4 + 0.6 * luma(*avg_color(canvas, zone)) / 255
    alpha = max(0.0, min(0.99, alpha + ctx.dark))
    ramp = gradient_ramp(ext, alpha)                 # ramp[0] — у края кадра
    if st:
        ramp = np.concatenate([np.full(st, 255.0 * alpha), ramp])
    if vertical:
        band = np.repeat(ramp[:, None], W, axis=1)
        if side == "bottom":
            band = band[::-1]
    else:
        band = np.repeat(ramp[None, :], H, axis=0)
        if side == "right":
            band = band[:, ::-1]
    noise = np.random.default_rng(1).random(band.shape) - 0.5     # дизеринг: без полос на гладком небе
    band = np.clip(np.round(band + noise * (band > 0.5)), 0, 255).astype(np.uint8)
    col = resolve_color(L.get("color"), ctx, canvas, zone) or (0, 0, 0)
    x0, y0, zw, zh = zone
    piece = Image.new("RGBA", (zw, zh), col + (0,))
    piece.putalpha(Image.fromarray(band, "L"))
    canvas.alpha_composite(piece, (x0, y0))
    return zone


def draw_overlay_layer(canvas, L, ctx):
    W, H = canvas.size
    col = resolve_color(L.get("color"), ctx, canvas, (0, 0, W, H)) or (0, 0, 0)
    a = max(0.0, min(0.99, float(L.get("opacity", 0.2)) + ctx.dark * 0.5))
    canvas.alpha_composite(Image.new("RGBA", (W, H), col + (int(round(255 * a)),)))
    return (0, 0, W, H)


def draw_image_layer(canvas, L, ctx):
    """Графика из макета. fit: box — свой размер и якорь; cover — заполнить кадр
    с обрезкой; stretch — растянуть точно по кадру (рамки, обводки)."""
    W, H = canvas.size
    img = ctx.image(L.get("asset"))
    if img is None:
        return None
    fit = L.get("fit", "box")
    if fit == "band":   # полоса у края: во всю ширину (высоту), доля кадра сохраняется
        side = L.get("side", "b")
        frac = float(L.get("s", 0.3))
        if side in ("t", "b"):
            bh = max(1, int(round(frac * H)))
            piece, left, top = img.resize((W, bh), Image.LANCZOS), 0, (0 if side == "t" else H - bh)
        else:
            bw = max(1, int(round(frac * W)))
            piece, left, top = img.resize((bw, H), Image.LANCZOS), (0 if side == "l" else W - bw), 0
    elif fit == "stretch":
        piece, left, top = img.resize((W, H), Image.LANCZOS), 0, 0
    elif fit == "cover":
        piece, left, top = fit_cover(img, W, H), 0, 0
    else:
        w = max(1, int(round(float(L.get("w", 0.2)) * W)))
        h = max(1, int(round(w * img.height / img.width)))
        left, top = place(L.get("anchor", "mc"), float(L.get("x", 0)), float(L.get("y", 0)), w, h, W, H)
        left, top = int(round(left)), int(round(top))
        piece = img.resize((w, h), Image.LANCZOS)
    opacity = float(L.get("opacity", 1))
    if opacity < 1:
        piece = piece.copy()
        piece.putalpha(piece.split()[3].point(lambda p: int(round(p * opacity))))
    over(canvas, piece, left, top)
    return (left, top, piece.width, piece.height)


def draw_photo_layer(canvas, L, ctx):
    """Фото поста в рамке: прямоугольник (или поля от края), скругление, своя точка
    фокуса и масштаб. Если в кадре есть такой слой, фон кадра — цвет слоя (bg)."""
    if ctx.photo is None:
        return None
    W, H = canvas.size
    left, top, w, h = rect_box(L, W, H)
    bw, bh = int(round(w)), int(round(h))
    if bw < 2 or bh < 2:
        return None
    focus = (float(L["fx"]), float(L["fy"])) if "fx" in L and "fy" in L else ctx.focus
    zoom = float(L.get("zoom", 1)) * float(ctx.zoom or 1)
    piece = fit_cover(ctx.photo, bw, bh, focus, zoom).convert("RGBA")
    a = int(round(255 * float(L.get("opacity", 1))))
    radius = float(L.get("radius", 0)) * min(bw, bh) / 2
    mask = _rounded_layer((bw, bh), (0, 0, bw - 1, bh - 1), radius, (0, 0, 0, a)).split()[3] if (radius > 0 or a < 255) else None
    if mask is not None:
        piece.putalpha(mask)
    over(canvas, piece, int(round(left)), int(round(top)))
    return (left, top, w, h)


DRAW = {"photo": draw_photo_layer, "text": draw_text_layer, "logo": draw_logo_layer, "rect": draw_rect_layer,
        "gradient": draw_gradient_layer, "overlay": draw_overlay_layer, "image": draw_image_layer}


def on_slide(L, i):
    """Показывать ли слой на кадре i карусели (1 — обложка)."""
    s = L.get("slides", "all")
    return i <= 1 if s == "first" else (i > 1 if s == "rest" else True)


def frame_layer(layers, i):
    """Первый видимый слой «фото в рамке» на кадре i или None."""
    for L in layers or []:
        if L.get("type") == "photo" and not L.get("hidden") and on_slide(L, i):
            return L
    return None


def photo_box(layers, W, H, i=1):
    """Куда ляжет фото на кадре i: размер рамки или всего кадра — для подсказки об обрезке."""
    L = frame_layer(layers, i)
    if not L:
        return W, H
    _, _, w, h = rect_box(L, W, H)
    return max(1, int(round(w))), max(1, int(round(h)))


def render_surface(photo, W, H, layers, ctx) -> Image.Image:
    i = int(ctx.fields.get("i") or 1)
    ctx.photo, ctx.boxes = photo, {}
    frame = frame_layer(layers, i)
    if frame:   # фото в рамке: под ним — фон кадра
        bg = ctx.ref((frame.get("bg") or {}).get("value", "p0") if isinstance(frame.get("bg"), dict) else "p0")
        canvas = Image.new("RGBA", (W, H), bg + (255,))
    else:
        canvas = fit_cover(photo, W, H, ctx.focus, ctx.zoom).convert("RGBA")
    for L in layers or []:
        if L.get("hidden") or not on_slide(L, i):
            continue
        fn = DRAW.get(L.get("type"))
        if fn:
            try:
                box = fn(canvas, L, ctx)
                if box and L.get("id"):
                    ctx.boxes[L["id"]] = box
            except Exception as e:
                logger.exception("слой %s: %s", L.get("type"), e)
    return canvas.convert("RGB")


# ============ Форматы и шаблоны ============
FEED_SIZES = {
    "4:5": (1920, 2400), "3:4": (1920, 2560), "1:1": (1920, 1920),
    "3:2": (1920, 1280), "9:16": (1080, 1920),
    "16:9": (1920, 1080), "1.91:1": (1920, 1005),     # превью ссылки, X, Facebook
}
STORY_SIZE = (1080, 1920)
DARK_STEPS = [-0.4, -0.2, 0.0, 0.2, 0.4]
DARK_DEFAULT_IDX = 2


def feed_size(img, fmt):
    if fmt in FEED_SIZES:
        return FEED_SIZES[fmt]
    w, h = img.size
    tw = min(max(w, 1920), 2560)
    return tw, int(round(h * tw / w))


def spec_fields(spec) -> set:
    """Какие данные шаблон спросит у пользователя при создании поста."""
    out = set()
    surfaces = [spec.get("feed", {})]
    if spec.get("story", {}).get("enabled"):
        surfaces.append(spec["story"])
    for s in surfaces:
        for L in s.get("layers", []):
            if L.get("type") == "text" and not L.get("hidden") and L.get("source") in ("title", "subtitle", "hashtag"):
                out.add(L["source"])
    return out


def spec_has_shade(spec) -> bool:
    surfaces = [spec.get("feed", {})] + ([spec["story"]] if spec.get("story", {}).get("enabled") else [])
    return any(L.get("type") in ("gradient", "overlay") and not L.get("hidden")
               for s in surfaces for L in s.get("layers", []))


def render_template(photo, spec, fmt, ctx):
    """→ [(suffix, Image)]: лента в выбранном формате + сторис, если включены."""
    W, H = feed_size(photo, fmt)
    out = [("feed", render_surface(photo, W, H, spec.get("feed", {}).get("layers"), ctx))]
    st = spec.get("story", {})
    if st.get("enabled"):
        out.append(("story", render_surface(photo, *STORY_SIZE, st.get("layers"), ctx)))
    return out


# ============ Вывод ============
def to_jpeg(img, quality=92) -> bytes:
    """JPEG без цветовой субдискретизации (4:4:4): края цветных букв и плашек остаются чистыми."""
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True, subsampling=0,
                            icc_profile=SRGB_ICC)
    return buf.getvalue()


def to_preview(img, max_side=1200) -> bytes:
    im = img.copy()
    im.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    im.convert("RGB").save(buf, format="JPEG", quality=85, optimize=True)
    return buf.getvalue()

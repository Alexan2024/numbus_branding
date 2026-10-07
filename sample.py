"""NUMBUS Branding — шаблон по образцу поста.

Клиент присылает свой фирменный пост (скриншот или файл). Дальше три шага:

1. РАЗБОР. Модель, которая видит изображение, описывает макет в JSON: где фото (на весь
   кадр или в рамке), фон, текстовые блоки (роль, текст по строкам, рамка, цвет, начертание,
   выделенные слова, плашка), логотип, градиенты, фигуры. Провайдер настраивается: формат
   Anthropic Messages или любой OpenAI-совместимый API с картинками (VISION_*).
2. ПОДГОНКА. Модель даёт смысл и примерные рамки, точность — локально: маска текста
   снимается с образца, движок NUMBUS рисует тот же текст кандидатами шрифтов из каталога
   (и своими шрифтами бренда), кегль, интерлиньяж и положение подбираются по маске,
   шрифт — по лучшему совпадению (IoU). Рамка фото уточняется по краям, цвета — по пикселям.
3. ПРОВЕРКА. Шаблон рисуется тем же движком; клиент видит «образец / шаблон» рядом.

Выход — обычный шаблон (spec), прошедший sanitize_spec: его можно сохранить и править.
"""
import base64
import io
import json
import logging
import math
import os
import re
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import render as R
import spec as S

logger = logging.getLogger(__name__)

def _env_seconds(name, default):
    """«90», «90s», « 90 » → 90.0; пусто или мусор → default (с предупреждением, без падения)."""
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*(s|sec|с|сек)?", raw)
    if not m or float(m.group(1).replace(",", ".")) <= 0:
        logger.warning("%s=%r — не число секунд, беру %s", name, raw, default)
        return default
    return float(m.group(1).replace(",", "."))


VISION_API_KEY = os.getenv("VISION_API_KEY", "").strip()
# Формат API: anthropic или openai (любой OpenAI-совместимый). Не задан, а ключ Anthropic (sk-ant-…) — anthropic.
VISION_PROVIDER = (os.getenv("VISION_PROVIDER", "").strip().lower()
                   or ("anthropic" if VISION_API_KEY.startswith("sk-ant-") else "openai"))
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"
VISION_MODEL = os.getenv("VISION_MODEL", "").strip() or (DEFAULT_ANTHROPIC_MODEL if VISION_PROVIDER == "anthropic" else "")
VISION_BASE_URL = os.getenv("VISION_BASE_URL", "").strip().rstrip("/")
VISION_TIMEOUT = _env_seconds("VISION_TIMEOUT", 90.0)
# Размышление модели. off (по умолчанию) — на Claude Sonnet 5.5 отключено предварительное размышление
# (thinking: between_tools): JSON макета приходит быстрее, дешевле и не упирается в max_tokens.
# on — параметр не отправляется: модель думает, как задумано по умолчанию (Sonnet/Opus 5.5 — адаптивно).
VISION_THINKING = (os.getenv("VISION_THINKING", "off").strip().lower() or "off")
MAX_TOKENS = 8000
RETRY_STATUS = (429, 500, 502, 503, 504, 529)     # повторяем один раз: перегрузка, лимит, сбой
RETRY_MAX_WAIT = 20                               # сек: дольше просит ждать — не повторяем
WORK_W = 1080            # образец приводится к этой ширине: координаты и подгонка — в ней
WEAK_SCORE = 0.55        # ниже — бот просит проверить блок в редакторе (совпадение вёрстки, маски расширены)


def enabled() -> bool:
    return bool(VISION_API_KEY and VISION_MODEL)


class SampleError(Exception):
    """Ошибка разбора (code → текст в texts.py).
    provider=True — сбой провайдера или сети (клиенту: «сервис недоступен», админам — оповещение);
    provider=False — образец не разобрался (клиенту: «пришлите другой образец»).
    status — HTTP-код ответа, meta — usage и модель, если провайдер ответил (за вызов списаны деньги)."""

    def __init__(self, code, detail="", provider=False, status=None, meta=None):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
        self.provider = provider
        self.status = status
        self.meta = meta


# ============ 1. Разбор моделью ============
PROMPT = """You are a layout analyst for a social-media branding tool. Describe the DESIGN TEMPLATE of this
post image so it can be rebuilt as layers. Coordinates are fractions of image width (x, w) and
image height (y, h), origin top-left. List layers from bottom to top. Return ONLY JSON:

{
 "canvas": {"bg": "#RRGGBB or null — flat background colour visible outside the photo, if any"},
 "photo": {"mode": "full|frame|none", "box": [x, y, w, h], "radius": 0..0.5},
 "layers": [
  {"kind": "gradient", "side": "top|bottom|left|right", "start": 0..1, "extent": 0..1,
   "color": "#RRGGBB", "opacity": 0..1},
  {"kind": "overlay", "color": "#RRGGBB", "opacity": 0..1},
  {"kind": "rect", "box": [x, y, w, h], "color": "#RRGGBB", "radius": 0..1, "opacity": 0..1},
  {"kind": "logo", "box": [x, y, w, h], "color": "#RRGGBB or null if multicolour"},
  {"kind": "text", "role": "title|subtitle|rubric|label|credit|counter",
   "lines": ["line 1 exactly as shown", "line 2"], "box": [x, y, w, h],
   "align": "left|center|right", "color": "#RRGGBB", "case": "none|upper|lower",
   "font": "sans|serif|slab|condensed|wide|rounded|mono|script", "weight": 100..900,
   "emphasis": ["words drawn in another colour or weight"], "em_color": "#RRGGBB or null",
   "em_weight": 100..900 or null,
   "plate": {"color": "#RRGGBB", "box": [x, y, w, h], "radius": 0..0.5} or null}
 ]
}

"photo" may also carry "z": the index in "layers" before which the photo is drawn (layers below it
are behind the photo, e.g. a card it sits on). Rules: "photo.mode" is "full" when the photo fills the whole image (text drawn over it), "frame" when
the photo sits in its own rectangle on a flat background. "title" is the main headline, "subtitle"
the secondary text, "rubric" a short category/kicker line, "label" a fixed badge such as BREAKING,
"credit" a photo credit. Boxes must be tight around the visible ink. A gradient is a darkening
used under text; "start" is where it begins from that edge (0 = at the edge). Keep text exactly
as printed, one array item per printed line. Use null when unsure. No commentary."""


def _image_b64(img: Image.Image, side=1568):
    im = img.copy()
    im.thumbnail((side, side), Image.LANCZOS)
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()


def parse_json(text: str) -> dict:
    t = re.sub(r"```(?:json)?", "", text or "").strip()
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b <= a:
        raise SampleError("model_json", (text or "")[:200])
    try:
        out = json.loads(t[a:b + 1])
    except json.JSONDecodeError as e:
        raise SampleError("model_json", str(e))
    if not isinstance(out, dict):
        raise SampleError("model_json", "not an object")
    return out


def thinking_param(model):
    """Что отправить в поле thinking (по документации Anthropic, октябрь 2026), или None.
    Claude Sonnet 5.5 думает по умолчанию; {type: "disabled"} он отклоняет (400), а самый низкий
    уровень — {type: "between_tools"}: без инструментов ответ приходит без размышления.
    Claude Haiku 4.5 без поля не думает; Opus 5.5 размышление выключить не даёт — поле не шлём."""
    if VISION_THINKING in ("on", "auto", "adaptive", "1", "true", "yes"):
        return None
    m = (model or "").lower()
    if m.startswith("claude-sonnet-5-5"):
        return {"type": "between_tools"}
    return None


def _provider_message(r):
    """Текст ошибки провайдера: error.message из JSON, иначе начало тела."""
    try:
        data = r.json()
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:300]
        if isinstance(err, str):
            return err[:300]
    except Exception:
        pass
    return (getattr(r, "text", "") or "")[:300]


def _retry_after(r):
    try:
        v = (getattr(r, "headers", None) or {}).get("retry-after")
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _request(img):
    b64 = _image_b64(img)
    if VISION_PROVIDER == "anthropic":
        url = (VISION_BASE_URL or "https://api.anthropic.com") + "/v1/messages"
        headers = {"x-api-key": VISION_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        body = {"model": VISION_MODEL, "max_tokens": MAX_TOKENS, "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
            {"type": "text", "text": PROMPT}]}]}
        th = thinking_param(VISION_MODEL)
        if th:
            body["thinking"] = th
    else:
        url = (VISION_BASE_URL or "https://api.openai.com/v1") + "/chat/completions"
        headers = {"Authorization": f"Bearer {VISION_API_KEY}", "content-type": "application/json"}
        body = {"model": VISION_MODEL, "max_tokens": MAX_TOKENS, "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}]}
    return url, headers, body


def _post(url, headers, body):
    import httpx
    try:
        return httpx.post(url, headers=headers, json=body, timeout=VISION_TIMEOUT)
    except httpx.HTTPError as e:            # таймаут, DNS, обрыв соединения
        raise SampleError("model_net", f"{type(e).__name__}: {e}"[:300], provider=True)


def _answer_text(data):
    """→ (текст ответа, stop_reason, usage). Блоки размышления (thinking, redacted_thinking) пропускаются."""
    if VISION_PROVIDER == "anthropic":
        blocks = data.get("content") if isinstance(data.get("content"), list) else []
        text = "".join(str(c.get("text") or "") for c in blocks if isinstance(c, dict) and c.get("type") == "text")
        return text, data.get("stop_reason"), data.get("usage")
    ch = (data.get("choices") or [{}])
    ch = ch[0] if isinstance(ch, list) and ch and isinstance(ch[0], dict) else {}
    content = (ch.get("message") or {}).get("content") if isinstance(ch.get("message"), dict) else None
    if isinstance(content, list):          # контент частями: [{"type": "text", "text": …}]
        content = "".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
    stop = {"length": "max_tokens", "content_filter": "refusal"}.get(ch.get("finish_reason"), ch.get("finish_reason"))
    return content or "", stop, data.get("usage")


def ask_model(img: Image.Image) -> dict:
    """Описание макета от модели. Синхронно — вызывать из потока (asyncio.to_thread).
    В ответ добавляется «_meta» (модель, usage, stop_reason) — для учёта расходов; build() его не читает.
    Сбой провайдера → SampleError(provider=True): один повтор на 429/5xx/529, если просят ждать ≤ 20 с."""
    if not enabled():
        raise SampleError("not_configured")
    url, headers, body = _request(img)
    tried_retry = tried_no_thinking = False
    while True:
        r = _post(url, headers, body)
        status = getattr(r, "status_code", 0)
        if status in RETRY_STATUS and not tried_retry:
            wait = _retry_after(r)
            wait = 2.0 if wait is None else wait
            if wait <= RETRY_MAX_WAIT:
                tried_retry = True
                time.sleep(max(0.0, wait))
                continue
        if status == 400 and "thinking" in body and not tried_no_thinking and \
                "thinking" in _provider_message(r).lower():
            tried_no_thinking = True              # модель не принимает это поле — без него
            body = {k: v for k, v in body.items() if k != "thinking"}
            continue
        break
    if status >= 400:
        raise SampleError("model_http", f"HTTP {status}: {_provider_message(r)}", provider=True, status=status)
    try:
        data = r.json()
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except Exception:
        raise SampleError("model_body", f"HTTP {status}: не JSON: {(getattr(r, 'text', '') or '')[:200]}",
                          provider=True, status=status)
    text, stop, usage = _answer_text(data)
    meta = {"model": data.get("model") or VISION_MODEL, "usage": usage if isinstance(usage, dict) else {},
            "stop_reason": stop}
    if stop == "max_tokens":                       # ответ оборван — виноват не образец
        raise SampleError("model_cut", f"stop_reason=max_tokens, max_tokens={MAX_TOKENS}", provider=True,
                          status=status, meta=meta)
    if stop == "refusal":
        raise SampleError("model_refusal", text[:200], meta=meta)
    try:
        out = parse_json(text)
    except SampleError as e:
        e.meta = meta
        raise
    out["_meta"] = meta
    return out


# ============ 2. Подгонка ============
FMT_RATIOS = {"4:5": 0.8, "3:4": 0.75, "1:1": 1.0, "3:2": 1.5, "16:9": 16 / 9, "1.91:1": 1.91, "9:16": 9 / 16}
FONT_GROUPS = {
    "sans": ["inter", "onest", "golos", "manrope", "geologica", "montserrat", "rubik", "jost"],
    "serif": ["lora", "playfair", "cormorant"],
    "slab": ["lora", "playfair"],
    "condensed": ["oswald", "inter"],
    "wide": ["unbounded", "montserrat", "manrope"],
    "rounded": ["nunito", "comfortaa", "rubik"],
    "mono": ["jetbrains"],
    "script": ["cormorant", "playfair"],
}
_HEX = re.compile(r"^#?[0-9A-Fa-f]{6}$")


def hexc(v, d=None):
    if isinstance(v, str) and _HEX.match(v.strip()):
        v = v.strip()
        return ("#" + v.lstrip("#")).upper()
    return d


def rgb_of(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], np.float32)


def to_hex(c):
    c = [int(max(0, min(255, round(float(v))))) for v in c]
    return "#%02X%02X%02X" % tuple(c)


def pick_format(w, h):
    r = w / h
    best = min(FMT_RATIOS, key=lambda k: abs(math.log(FMT_RATIOS[k] / r)))
    return best if abs(math.log(FMT_RATIOS[best] / r)) < 0.03 else "orig"


def _box_px(box, W, H, pad=0.0):
    try:
        x, y, w, h = [float(v) for v in box]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x, y, w, h)):
        return None
    p = pad * W
    x0, y0 = max(0, int(x * W - p)), max(0, int(y * H - p))
    x1, y1 = min(W, int((x + w) * W + p)), min(H, int((y + h) * H + p))
    return (x0, y0, x1, y1) if x1 - x0 > 2 and y1 - y0 > 2 else None


def _rows(mask, min_h=2):
    """Строки маски: [(верх, низ)] по сплошным полосам с пикселями."""
    on = mask.any(1)
    out, start = [], None
    for i, v in enumerate(on):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= min_h:
                out.append((start, i - 1))
            start = None
    if start is not None and len(on) - start >= min_h:
        out.append((start, len(on) - 1))
    return out


def _merge_rows(rows, gap):
    out = []
    for a, b in rows:
        if out and a - out[-1][1] <= gap:
            out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


# ---- ответы модели бывают неаккуратными: «0.4», «40%», "bold", строка вместо списка ----
_WEIGHTS = {"thin": 100, "hairline": 100, "extralight": 200, "ultralight": 200, "light": 300, "regular": 400,
            "normal": 400, "book": 400, "medium": 500, "semibold": 600, "demibold": 600, "bold": 700,
            "extrabold": 800, "ultrabold": 800, "heavy": 900, "black": 900}


def num(v, d=0.0, lo=None, hi=None):
    """Число из ответа модели: 0.4, "0.4", "0,4", "40%" → 0.4; не число → d. Границы — lo/hi."""
    x = None
    if isinstance(v, bool):
        x = float(v)
    elif isinstance(v, (int, float)):
        x = float(v)
    elif isinstance(v, str):
        t = v.strip().replace(",", ".")
        pct = t.endswith("%")
        try:
            x = float(t.rstrip("%").strip())
            x = x / 100 if pct else x
        except ValueError:
            x = None
    if x is None or not math.isfinite(x):
        return d
    if lo is not None:
        x = max(lo, x)
    if hi is not None:
        x = min(hi, x)
    return x


def weight_of(v, d=500):
    if isinstance(v, str) and v.strip().lower().replace("-", "").replace(" ", "") in _WEIGHTS:
        return _WEIGHTS[v.strip().lower().replace("-", "").replace(" ", "")]
    w = num(v, None)
    if w is None:
        return d
    return int(max(100, min(900, round(w / 100) * 100 if w > 9 else w * 100)))


def _strs(v):
    """Строка или список → список непустых строк."""
    if isinstance(v, str):
        v = v.split("\n")
    if not isinstance(v, (list, tuple)):
        return []
    return [str(s) for s in v if s is not None and not isinstance(s, (dict, list)) and str(s).strip()]


def ink_mask(arr, box, color, bg=None):
    """Маска букв в рамке: пиксели ближе к цвету текста, чем к фону рамки."""
    x0, y0, x1, y1 = box
    reg = arr[y0:y1, x0:x1].astype(np.float32)
    if bg is None:
        border = np.concatenate([reg[0], reg[-1], reg[:, 0], reg[:, -1]])
        bg = np.median(border, axis=0)
    tc = rgb_of(color)
    dt = np.sqrt(((reg - tc) ** 2).sum(2))
    db = np.sqrt(((reg - bg) ** 2).sum(2))
    sep = float(np.sqrt(((tc - bg) ** 2).sum()))
    m = (dt < db) & (db > max(25.0, sep * 0.35))
    full = np.zeros(arr.shape[:2], bool)
    full[y0:y1, x0:x1] = m
    return full, bg


def _bbox(m):
    ys, xs = np.where(m)
    if not len(ys):
        return None
    return xs.min(), ys.min(), xs.max() + 1, ys.max() + 1


def _iou(a, b, box):
    x0, y0, x1, y1 = box
    A, B = a[y0:y1, x0:x1], b[y0:y1, x0:x1]
    u = (A | B).sum()
    return float((A & B).sum() / u) if u else 0.0


def _render_mask(L, text, size, W, H, customs):
    ctx = R.Ctx(fields={"title": text, "subtitle": text}, customs=customs)
    cv = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    lay = dict(L, size=size)
    box = R.draw_text_layer(cv, lay, ctx)
    return np.asarray(cv.split()[3]) > 110, box


def fit_text(arr, T, customs, font_keys):
    """Подбор шрифта, кегля, интерлиньяжа и положения текстового блока по маске образца."""
    H, W = arr.shape[:2]
    box = _box_px(T.get("box"), W, H, pad=0.012)
    color = hexc(T.get("color"), "#FFFFFF")
    lines = _strs(T.get("lines"))
    if not box or not lines:
        return None
    if T.get("plate") and isinstance(T["plate"], dict):
        pc = hexc(T["plate"].get("color"))
        pb = refine_plate(arr, _box_px(T["plate"].get("box"), W, H), pc)
        if pb:      # буквы на плашке ищем только внутри плашки: снаружи — фото, его цвета путают маску
            m2 = max(1, int(0.004 * W))
            box = (pb[0] + m2, pb[1] + m2, pb[2] - m2, pb[3] - m2)
        mask, bg = ink_mask(arr, box, color, rgb_of(pc) if pc else None)
    else:
        mask, bg = ink_mask(arr, box, color)
    sb = _bbox(mask)
    if sb is None:
        return None
    srows = _merge_rows(_rows(mask[sb[1]:sb[3], sb[0]:sb[2]]), gap=2)
    case = T.get("case") if T.get("case") in ("none", "upper", "lower") else "none"
    align = T.get("align") if T.get("align") in ("left", "center", "right") else "left"
    text = "\n".join(lines)
    emph = _strs(T.get("emphasis"))
    for w in sorted(set(emph), key=len, reverse=True):
        text = text.replace(w, f"*{w}*", 1)
    em = {}
    if emph:
        ec = hexc(T.get("em_color"))
        if ec:
            em["color"] = {"mode": "fixed", "value": ec}
        if T.get("em_weight"):
            em["weight"] = weight_of(T["em_weight"], 700)
    weight = weight_of(T.get("weight"), 500)
    n = max(1, len(lines))
    pitch_s = (srows[1][0] - srows[0][0]) if len(srows) >= 2 and len(srows) == n else None
    best = None
    tries = []
    for key in font_keys:
        info = R.FONTS.get(key)
        wts = [weight] if not info else sorted({max(info["min"], min(info["max"], w)) for w in (weight, weight + 150, weight - 150)})
        for wt in wts:
            L = dict(type="text", source="title", anchor="tl", x=0.1, y=0.1, font=key, weight=wt, tracking=0,
                     leading=1.15, case=case, align="left", maxw=0, lines=0,
                     color={"mode": "fixed", "value": "#FFFFFF"}, opacity=1)
            if em:
                L["em"] = dict(em)
            size = (sb[3] - sb[1]) / W / (0.8 + (n - 1) * 1.15) * 1.0
            ok = True
            for _ in range(3):                         # кегль и интерлиньяж — по высоте и шагу строк
                m, _box = _render_mask(L, text, size, W, H, customs)
                rb = _bbox(m)
                if rb is None:
                    ok = False
                    break
                rrows = _merge_rows(_rows(m[rb[1]:rb[3], rb[0]:rb[2]]), gap=2)
                kw = (sb[2] - sb[0]) / max(1, rb[2] - rb[0])
                kh = (sb[3] - sb[1]) / max(1, rb[3] - rb[1])
                k = math.sqrt(kw * kh) if n > 1 else (kw * 0.5 + kh * 0.5)
                size *= k
                if pitch_s and len(rrows) >= 2:
                    pr = (rrows[1][0] - rrows[0][0]) * k
                    L["leading"] = float(max(0.7, min(2.5, L["leading"] * pitch_s / max(1.0, pr))))
            if not ok:
                continue
            m, lbox = _render_mask(L, text, size, W, H, customs)
            rb = _bbox(m)
            if rb is None:
                continue
            dx = (sb[0] - rb[0]) if align == "left" else ((sb[2] - rb[2]) if align == "right"
                                                          else ((sb[0] + sb[2]) - (rb[0] + rb[2])) / 2)
            dy = sb[1] - rb[1]
            shifted = np.zeros_like(m)
            ys, xs = np.where(m)
            ys2, xs2 = (ys + int(round(dy))).clip(0, H - 1), (xs + int(round(dx))).clip(0, W - 1)
            shifted[ys2, xs2] = True
            score = _iou(mask, shifted, box)
            tries.append((score, key, wt))
            if best is None or score > best[0]:
                best = (score, key, wt, size, L["leading"], lbox[0] + dx, lbox[1] + dy, lbox[2], shifted)
    if best is None:
        return None
    glyph, key, wt, size, leading, left, top, bw, shifted = best
    # оценка вёрстки, а не формы букв: маски слегка расширены — другой шрифт с тем же
    # кеглем и положением строк не должен считаться «не совпало»
    k = max(3, int(size * W * 0.18)) | 1
    dil = lambda m: np.asarray(Image.fromarray(m.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(k))) > 0
    score = _iou(dil(mask), dil(shifted), box)
    # якорь по выравниванию: так длинный и короткий текст встают как в образце
    if align == "center":
        anchor, x = "tc", (left + bw / 2 - W / 2) / W
    elif align == "right":
        anchor, x = "tr", (W - (left + bw)) / W
    else:
        anchor, x = "tl", left / W
    tc = arr[mask].reshape(-1, 3)
    color_fit = to_hex(np.median(tc, 0)) if len(tc) > 30 else color
    return dict(font=key, weight=wt, size=size, leading=round(leading, 3), anchor=anchor, x=round(x, 4),
                y=round(top / W, 4), align=align, case=case, text=text, em=em, color=color_fit,
                maxw=round(min(1.0, (sb[2] - sb[0]) / W * 1.06), 3), lines=n, score=round(score, 3),
                glyph=round(glyph, 3),
                box=box, ink=sb, bg=to_hex(bg), tries=sorted(tries, reverse=True)[:3])


def refine_plate(arr, box, color_hex):
    """Плашка: от рамки модели — к реальным краям ровного цвета (рамки модели бывают узкими)."""
    if not box or not color_hex:
        return box
    H, W = arr.shape[:2]
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    X0, Y0, X1, Y1 = max(0, x0 - bw), max(0, y0 - bh), min(W, x1 + bw), min(H, y1 + bh)
    reg = arr[Y0:Y1, X0:X1].astype(np.float32)
    inner = arr[y0:y1, x0:x1].reshape(-1, 3).astype(np.float32)
    want = rgb_of(color_hex)                      # реальный цвет плашки: самый частый близкий к заявленному
    close = inner[np.sqrt(((inner - want) ** 2).sum(1)) < 70]
    if len(close) > 20:
        want = np.median(close, 0)
    near = np.sqrt(((reg - want) ** 2).sum(2)) < 30
    cy, cx = (y0 + y1) // 2 - Y0, (x0 + x1) // 2 - X0
    rows = near.mean(1) > 0.25 * (bw / max(1, X1 - X0))
    cols = near[max(0, cy - bh // 3):cy + bh // 3 + 1].mean(0) > 0.3

    def close_gaps(v, g):                         # буквы на плашке рвут полосу — сшиваем короткие разрывы
        v = v.copy()
        idx = np.where(v)[0]
        for a, b in zip(idx[:-1], idx[1:]):
            if 1 < b - a <= g:
                v[a:b] = True
        return v
    cols, rows = close_gaps(cols, int(bh * 1.2)), close_gaps(rows, max(2, bh // 3))
    def run(v, c):
        if c >= len(v) or not v[c]:
            return None
        a = b = c
        while a > 0 and v[a - 1]:
            a -= 1
        while b < len(v) - 1 and v[b + 1]:
            b += 1
        return a, b
    rx, ry = run(cols, cx), run(rows, cy)
    if not rx or not ry:
        return box
    return X0 + rx[0], Y0 + ry[0], X0 + rx[1] + 1, Y0 + ry[1] + 1


def plate_color(arr, box, hint):
    """Цвет плашки по пикселям: самый частый цвет рамки, близкий к заявленному."""
    if not box or not hint:
        return hint
    x0, y0, x1, y1 = box
    px = arr[y0:y1, x0:x1].reshape(-1, 3).astype(np.float32)
    near = px[np.sqrt(((px - rgb_of(hint)) ** 2).sum(1)) < 70]
    return to_hex(np.median(near, 0)) if len(near) > 20 else hint


def refine_frame(arr, P, bg_hex):
    """Рамка фото: от рамки модели к краям, где кончается ровный фон."""
    H, W = arr.shape[:2]
    box = _box_px(P.get("box"), W, H, pad=0.03)
    if not box or not bg_hex:
        return _box_px(P.get("box"), W, H)
    bg = rgb_of(bg_hex)
    x0, y0, x1, y1 = box
    reg = arr[y0:y1, x0:x1].astype(np.float32)
    d = np.sqrt(((reg - bg) ** 2).sum(2)) > 22
    cols, rows = d.mean(0) > 0.5, d.mean(1) > 0.5
    if not cols.any() or not rows.any():
        return _box_px(P.get("box"), W, H)
    xs, ys = np.where(cols)[0], np.where(rows)[0]
    return x0 + xs[0], y0 + ys[0], x0 + xs[-1] + 1, y0 + ys[-1] + 1


def fill_holes(img: Image.Image, mask: np.ndarray) -> Image.Image:
    """Залить маску (стёртый текст) окружением: пирамида размытий по известным пикселям."""
    arr = np.asarray(img.convert("RGB")).astype(np.float32)
    known = (~mask).astype(np.float32)
    out = arr.copy()
    acc = np.zeros_like(arr)
    wsum = np.zeros(mask.shape, np.float32)
    for r in (3, 9, 27, 81):
        k = Image.fromarray((known * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(r))
        kv = np.asarray(k).astype(np.float32) / 255
        pv = np.stack([np.asarray(Image.fromarray((arr[..., c] * known).astype(np.uint8)).filter(
            ImageFilter.GaussianBlur(r))).astype(np.float32) for c in range(3)], 2)
        w = (kv > 0.05) & (wsum < 0.5)
        acc[w] = pv[w] / kv[w][:, None]
        wsum[w] = 1
    out[mask] = acc[mask]
    return Image.fromarray(out.clip(0, 255).astype(np.uint8))


def build(img: Image.Image, desc: dict, customs=None, has_logo=True):
    """Описание модели + образец → (spec, fields, fmt, preview_photo, report)."""
    img = img.convert("RGB")
    if img.width != WORK_W:
        img = img.resize((WORK_W, int(round(img.height * WORK_W / img.width))), Image.LANCZOS)
    arr = np.asarray(img)
    H, W = arr.shape[:2]
    customs = customs or {}
    fmt = pick_format(W, H)
    layers, fields, report = [], {}, []
    erase = np.zeros((H, W), bool)
    desc = desc if isinstance(desc, dict) else {}
    canvas = desc.get("canvas") if isinstance(desc.get("canvas"), dict) else {}
    canvas_bg = hexc(canvas.get("bg"))
    P = desc.get("photo") if isinstance(desc.get("photo"), dict) else {}
    mode = P.get("mode") if P.get("mode") in ("full", "frame", "none") else "full"
    frame_px = None
    if mode in ("frame", "none"):
        if mode == "frame":
            frame_px = refine_frame(arr, P, canvas_bg)
        if frame_px is None:                # фото нет — «рамка» в 1 px за кадром, фон — цвет кадра
            frame_px = (0, H, W, H + 1)
        x0, y0, x1, y1 = frame_px
        layers.append(dict(type="photo", id="ph", fit="box", anchor="tl", x=x0 / W, y=y0 / W, w=(x1 - x0) / W,
                           h=max(1, y1 - y0) / W, radius=num(P.get("radius"), 0, 0, 0.5),
                           zoom=1, bg={"mode": "fixed", "value": canvas_bg or "#FFFFFF"}, opacity=1))
    groups = list(FONT_GROUPS["sans"])
    used_roles = set()
    raw_layers = desc.get("layers") if isinstance(desc.get("layers"), list) else []
    for i, Lm in enumerate(raw_layers):
        if not isinstance(Lm, dict):
            continue
        kind = Lm.get("kind")
        lid = f"l{i}"
        if kind == "gradient":
            side = Lm.get("side") if Lm.get("side") in ("top", "bottom", "left", "right") else "bottom"
            g = dict(type="gradient", id=lid, side=side, extent=num(Lm.get("extent"), 0.4, 0, 1) or 0.4,
                     color={"mode": "fixed", "value": hexc(Lm.get("color"), "#000000")},
                     opacity=num(Lm.get("opacity"), 0.7, 0, 1) or 0.7, adaptive=False)
            if num(Lm.get("start"), 0, 0, 1):
                g["start"] = num(Lm.get("start"), 0, 0, 1)
            layers.append(g)
        elif kind == "overlay":
            layers.append(dict(type="overlay", id=lid, color={"mode": "fixed", "value": hexc(Lm.get("color"), "#000000")},
                               opacity=num(Lm.get("opacity"), 0.25, 0, 1) or 0.25))
        elif kind == "rect":
            b = _box_px(Lm.get("box"), W, H)
            if b:
                layers.append(dict(type="rect", id=lid, fit="box", anchor="tl", x=b[0] / W, y=b[1] / W,
                                   w=(b[2] - b[0]) / W, h=(b[3] - b[1]) / W, radius=num(Lm.get("radius"), 0, 0, 1),
                                   stroke=0, color={"mode": "fixed", "value": hexc(Lm.get("color"), "#FFFFFF")},
                                   opacity=num(Lm.get("opacity"), 1, 0, 1) or 1))
        elif kind == "logo":
            b = _box_px(Lm.get("box"), W, H)
            if b:
                erase[b[1]:b[3], b[0]:b[2]] = True
                c = hexc(Lm.get("color"))
                if has_logo:
                    layers.append(dict(type="logo", id=lid, anchor="tl", x=b[0] / W, y=b[1] / W, w=(b[2] - b[0]) / W,
                                       asset="logo", opacity=1,
                                       color={"mode": "fixed", "value": c} if c else {"mode": "original"}))
                report.append(("logo", b))
        elif kind == "text":
            role = Lm.get("role") if Lm.get("role") in ("title", "subtitle", "rubric", "label", "credit", "counter") else "label"
            fk = FONT_GROUPS.get(Lm.get("font") if isinstance(Lm.get("font"), str) else "sans", FONT_GROUPS["sans"])
            cands = list(dict.fromkeys(fk + list(customs.keys())))
            fit = fit_text(arr, Lm, customs, cands)
            if not fit:
                report.append((role, None))
                continue
            x0, y0, x1, y1 = fit["ink"]
            pad = max(2, int(fit["size"] * W * 0.12))
            erase[max(0, y0 - pad):y1 + pad, max(0, x0 - pad):x1 + pad] |= ink_mask(arr, fit["box"], fit["color"])[0][
                max(0, y0 - pad):y1 + pad, max(0, x0 - pad):x1 + pad]
            src = {"title": "title", "subtitle": "subtitle", "rubric": "hashtag", "counter": "counter"}.get(role, "static")
            if src in used_roles and src != "static":
                src = "static"
            used_roles.add(src)
            L = dict(type="text", id=lid, source=src, anchor=fit["anchor"], x=fit["x"], y=fit["y"], font=fit["font"],
                     weight=fit["weight"], size=round(fit["size"], 4), tracking=0, leading=fit["leading"],
                     case=fit["case"], align=fit["align"], maxw=fit["maxw"],
                     lines=fit["lines"] + (1 if src in ("title", "subtitle") else 0),
                     color={"mode": "fixed", "value": fit["color"]}, opacity=1)
            if fit["em"]:
                L["em"] = fit["em"]
            if src == "hashtag":
                L["tag"] = "clean"
                fields["hashtag"] = "#" + fit["text"].replace("*", "").replace("\n", " ").replace(" ", "_")
            elif src in ("title", "subtitle"):
                fields[src] = fit["text"]
            elif src == "counter":
                L["text"] = "{i} / {n}"
            else:
                L["text"] = fit["text"]
            pl = Lm.get("plate")
            if isinstance(pl, dict):
                pc = hexc(pl.get("color"), fit["bg"])
                pb = refine_plate(arr, _box_px(pl.get("box"), W, H), pc)
                pc = plate_color(arr, pb, pc)
                sz = fit["size"] * W
                padx = (x0 - pb[0]) / sz if pb else 0.6
                pady = (y0 - pb[1]) / sz if pb else 0.4
                L["plate"] = {"color": {"mode": "fixed", "value": pc}, "opacity": 1,
                              "radius": num(pl.get("radius"), 0, 0, 0.5), "padx": round(max(0, padx), 2),
                              "pady": round(max(0, pady), 2)}
                L["x"], L["y"] = (pb[0] / W, pb[1] / W) if (pb and fit["anchor"] == "tl") else (L["x"], L["y"])
                if pb:
                    erase[pb[1]:pb[3], pb[0]:pb[2]] = True
            layers.append(L)
            report.append((role, fit))
    if layers and layers[0].get("type") == "photo":
        ph = layers.pop(0)
        px0, py0, px1, py1 = ph["x"], ph["y"], ph["x"] + ph["w"], ph["y"] + ph["h"]
        at = 0
        for k, L in enumerate(layers):       # карточка под фото: прямоугольник, внутри которого рамка
            if L.get("type") == "rect" and L["x"] <= px0 + 0.005 and L["y"] <= py0 + 0.005 \
                    and L["x"] + L["w"] >= px1 - 0.005 and L["y"] + L["h"] >= py1 - 0.005:
                at = k + 1
            elif L.get("type") == "gradient" and at == k:   # фон-градиент под карточкой
                at = k + 1
        idx = P.get("z")
        if isinstance(idx, int) and not isinstance(idx, bool) and 0 <= idx <= len(layers):
            at = idx
        layers.insert(at, ph)
    spec = S.sanitize_spec({"feed": {"layers": layers}, "story": {"enabled": False, "layers": []}})
    # фото для превью: из рамки — как есть, на весь кадр — образец со стёртым текстом и логотипом
    if frame_px and frame_px[1] < H:
        x0, y0, x1, y1 = frame_px
        photo = img.crop((x0, y0, x1, y1))
    else:
        photo = fill_holes(img, np.asarray(Image.fromarray(erase.astype(np.uint8) * 255).filter(
            ImageFilter.MaxFilter(5))) > 0)
    return spec, fields, fmt, photo, report


def side_by_side(sample: Image.Image, result: Image.Image, labels=("Образец", "Стиль NUMBUS"), h=1100):
    font = None
    try:
        font = ImageFont.truetype(os.path.join(R.FONT_DIR, "Inter.ttf"), 34)
    except Exception:
        pass
    a = sample.convert("RGB").resize((int(sample.width * h / sample.height), h), Image.LANCZOS)
    b = result.convert("RGB").resize((int(result.width * h / result.height), h), Image.LANCZOS)
    out = Image.new("RGB", (a.width + b.width + 60, h + 80), (245, 244, 239))
    d = ImageDraw.Draw(out)
    d.text((20, 18), labels[0], fill=(20, 20, 20), font=font)
    d.text((a.width + 40, 18), labels[1], fill=(20, 20, 20), font=font)
    out.paste(a, (20, 70))
    out.paste(b, (a.width + 40, 70))
    return out


def make(data: bytes, base_ctx, has_logo=True, desc=None):
    """Полный цикл для бота: байты образца → (spec, fmt, превью «рядом» JPEG, заметки).
    desc — готовое описание (тесты, повтор без нового вызова модели)."""
    img = R.open_image(data, R.PHOTO_SHORT).convert("RGB")
    if desc is None:
        desc = ask_model(img)
    spec, fields, fmt, photo, report = build(img, desc, base_ctx.customs, has_logo)
    if not spec["feed"]["layers"]:
        raise SampleError("empty")
    ctx = R.Ctx(base_ctx.palette, base_ctx.logos, base_ctx.customs, dict(fields, i=1, n=1), 0.0, base_ctx.images)
    out = R.render_template(photo, spec, fmt, ctx)[0][1]
    work = img.resize((WORK_W, int(round(img.height * WORK_W / img.width))), Image.LANCZOS)
    sbs = side_by_side(work, out)
    weak = [r for r, f in report if f and isinstance(f, dict) and f["score"] < WEAK_SCORE]
    missed = [r for r, f in report if f is None]
    notes = {"fonts": sorted({f["font"] for r, f in report if isinstance(f, dict) and "font" in f}),
             "weak": weak, "missed": missed}
    return spec, fmt, fields, R.to_jpeg(sbs, 88), notes, desc

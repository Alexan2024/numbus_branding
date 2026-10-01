"""NUMBUS Branding — схема шаблона: проверка и стартовые стили.

Шаблон приходит из Mini App как JSON — всё, что пишется в БД, проходит через
sanitize_spec: неизвестные поля выбрасываются, числа зажимаются в диапазоны.
"""
import re
import copy
import secrets

ANCHORS = {"tl", "tc", "tr", "ml", "mc", "mr", "bl", "bc", "br"}
SOURCES = {"title", "subtitle", "hashtag", "counter", "static"}
SLIDES = {"first", "rest"}
MAX_LAYERS = 30
MARGIN = 0.06          # поля бренда: один отступ от края для всех слоёв
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_PAL = re.compile(r"^p[0-4]$")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,16}$")
_IMG = re.compile(r"^img_[0-9a-f]{12}$")


def _num(v, lo, hi, d):
    try:
        v = float(v)
        if v != v:  # NaN
            return d
        return max(lo, min(hi, v))
    except (TypeError, ValueError):
        return d


def _cval(v, d):
    return v if isinstance(v, str) and (_HEX.match(v) or _PAL.match(v)) else d


def _color(c, allow_original=False, d=None):
    d = d or {"mode": "fixed", "value": "p0"}
    if not isinstance(c, dict):
        return dict(d)
    mode = c.get("mode")
    if mode == "fixed":
        return {"mode": "fixed", "value": _cval(c.get("value"), "p0")}
    if mode == "adaptive":
        return {"mode": "adaptive"}
    if mode == "contrast":
        return {"mode": "contrast", "light": _cval(c.get("light"), "p0"), "dark": _cval(c.get("dark"), "p1")}
    if mode == "original" and allow_original:
        return {"mode": "original"}
    return dict(d)


def _font(v):
    from render import FONTS, CUSTOM_FONT_SLOTS, DEFAULT_FONT
    return v if v in FONTS or v in CUSTOM_FONT_SLOTS else DEFAULT_FONT


def _base(L):
    lid = L.get("id") if isinstance(L.get("id"), str) and _ID.match(L.get("id")) else "l" + secrets.token_hex(3)
    out = {"id": lid, "type": L.get("type")}
    if L.get("hidden"):
        out["hidden"] = True
    if L.get("slides") in SLIDES:          # только на обложке карусели или только на остальных кадрах
        out["slides"] = L["slides"]
    name = L.get("name")
    if isinstance(name, str) and name.strip():
        out["name"] = name.strip()[:40]
    return out


def _pos(L, out, d_anchor="bl"):
    out["anchor"] = L.get("anchor") if L.get("anchor") in ANCHORS else d_anchor
    out["x"] = _num(L.get("x"), -1, 1, 0.05)
    out["y"] = _num(L.get("y"), -1, 2, 0.05)


def sanitize_layer(L):
    if not isinstance(L, dict):
        return None
    t = L.get("type")
    out = _base(L)
    if t == "logo":
        _pos(L, out)
        out["asset"] = "logo_alt" if L.get("asset") == "logo_alt" else "logo"
        out["w"] = _num(L.get("w"), 0.01, 1, 0.08)
        out["color"] = _color(L.get("color"), allow_original=True, d={"mode": "adaptive"})
        out["opacity"] = _num(L.get("opacity"), 0, 1, 1)
    elif t == "text":
        _pos(L, out)
        out["source"] = L.get("source") if L.get("source") in SOURCES else "static"
        out["text"] = str(L.get("text") or "")[:300]
        out["font"] = _font(L.get("font"))
        out["weight"] = int(_num(L.get("weight"), 100, 1000, 500))
        out["size"] = _num(L.get("size"), 0.005, 0.4, 0.04)
        out["tracking"] = _num(L.get("tracking"), -0.2, 0.6, 0)
        out["leading"] = _num(L.get("leading"), 0.6, 3, 1.1)
        out["case"] = L.get("case") if L.get("case") in ("none", "upper", "lower") else "none"
        out["align"] = L.get("align") if L.get("align") in ("left", "center", "right") else "left"
        out["maxw"] = _num(L.get("maxw"), 0, 1, 0)
        d_lines = 5 if out["source"] in ("title", "subtitle") else 0
        out["lines"] = int(_num(L.get("lines"), 0, 12, d_lines))
        out["color"] = _color(L.get("color"))
        out["opacity"] = _num(L.get("opacity"), 0, 1, 1)
        sh = L.get("shadow")
        if isinstance(sh, dict):      # мягкая тень без смещения
            out["shadow"] = {
                "blur": _num(sh.get("blur"), 0.02, 1.5, 0.35),
                "opacity": _num(sh.get("opacity"), 0, 1, 0.5),
                "color": _color(sh.get("color"), d={"mode": "fixed", "value": "p1"}),
            }
        em = L.get("em")
        if isinstance(em, dict):      # выделение «*…*»: свой цвет, вес, шрифт
            e = {}
            if isinstance(em.get("color"), dict):
                e["color"] = _color(em.get("color"), d={"mode": "fixed", "value": "p2"})
            if em.get("weight") is not None:
                e["weight"] = int(_num(em.get("weight"), 100, 1000, 700))
            if em.get("font") is not None:
                e["font"] = _font(em.get("font"))
            if e:
                out["em"] = e
        if out["source"] == "hashtag" and L.get("tag") == "clean":
            out["tag"] = "clean"      # рубрика без «#», «_» → пробел
        if isinstance(L.get("after"), str) and _ID.match(L["after"]):
            out["after"] = L["after"]  # поток: верх слоя — под низом другого слоя
            out["gap"] = _num(L.get("gap"), -0.5, 1, 0.02)
        p = L.get("plate")
        if isinstance(p, dict):
            out["plate"] = {
                "color": _color(p.get("color"), d={"mode": "fixed", "value": "p1"}),
                "opacity": _num(p.get("opacity"), 0, 1, 1),
                "radius": _num(p.get("radius"), 0, 0.5, 0),
                "padx": _num(p.get("padx"), 0, 4, 0.8),
                "pady": _num(p.get("pady"), 0, 4, 0.5),
            }
    elif t == "rect":
        out["fit"] = "inset" if L.get("fit") == "inset" else "box"
        _pos(L, out, "mc")
        out["m"] = _num(L.get("m"), 0, 0.4, 0.04)
        out["w"] = _num(L.get("w"), 0.001, 2, 0.2)
        out["h"] = _num(L.get("h"), 0.001, 4, 0.1)
        out["radius"] = _num(L.get("radius"), 0, 1, 0)
        out["stroke"] = _num(L.get("stroke"), 0, 0.1, 0)
        out["color"] = _color(L.get("color"), d={"mode": "fixed", "value": "p1"})
        out["opacity"] = _num(L.get("opacity"), 0, 1, 1)
    elif t == "photo":                 # фото поста в рамке
        out["fit"] = "inset" if L.get("fit") == "inset" else "box"
        _pos(L, out, "tl")
        out["m"] = _num(L.get("m"), 0, 0.4, 0.04)
        out["w"] = _num(L.get("w"), 0.02, 2, 1)
        out["h"] = _num(L.get("h"), 0.02, 4, 0.6)
        out["radius"] = _num(L.get("radius"), 0, 1, 0)
        out["zoom"] = _num(L.get("zoom"), 1, 4, 1)
        if L.get("fx") is not None and L.get("fy") is not None:
            out["fx"] = _num(L.get("fx"), 0, 1, 0.5)
            out["fy"] = _num(L.get("fy"), 0, 1, 0.5)
        out["bg"] = _color(L.get("bg"), d={"mode": "fixed", "value": "p0"})
        if out["bg"]["mode"] != "fixed":
            out["bg"] = {"mode": "fixed", "value": "p0"}
        out["opacity"] = _num(L.get("opacity"), 0, 1, 1)
    elif t == "gradient":
        out["side"] = L.get("side") if L.get("side") in ("bottom", "top", "left", "right") else "bottom"
        out["extent"] = _num(L.get("extent"), 0.05, 1, 0.45)
        if L.get("start"):
            out["start"] = _num(L.get("start"), 0, 0.9, 0)    # градиент начинается не от края
        out["color"] = _color(L.get("color"), d={"mode": "fixed", "value": "p1"})
        out["opacity"] = _num(L.get("opacity"), 0, 1, 0.7)
        out["adaptive"] = bool(L.get("adaptive"))
    elif t == "image":
        if not isinstance(L.get("asset"), str) or not _IMG.match(L["asset"]):
            return None
        out["asset"] = L["asset"]
        out["fit"] = L.get("fit") if L.get("fit") in ("box", "cover", "stretch", "band") else "box"
        if out["fit"] == "band":
            out["side"] = L.get("side") if L.get("side") in ("t", "b", "l", "r") else "b"
            out["s"] = _num(L.get("s"), 0.01, 1, 0.3)
        _pos(L, out, "mc")
        out["w"] = _num(L.get("w"), 0.005, 2, 0.2)
        out["opacity"] = _num(L.get("opacity"), 0, 1, 1)
    elif t == "overlay":
        out["color"] = _color(L.get("color"), d={"mode": "fixed", "value": "p1"})
        out["opacity"] = _num(L.get("opacity"), 0, 1, 0.25)
    else:
        return None
    return out


def _layers(v):
    out = []
    for L in (v if isinstance(v, list) else [])[:MAX_LAYERS]:
        c = sanitize_layer(L)
        if c:
            out.append(c)
    return out


def sanitize_spec(spec):
    spec = spec if isinstance(spec, dict) else {}
    feed = spec.get("feed") if isinstance(spec.get("feed"), dict) else {}
    story = spec.get("story") if isinstance(spec.get("story"), dict) else {}
    return {
        "v": 1,
        "feed": {"layers": _layers(feed.get("layers"))},
        "story": {"enabled": bool(story.get("enabled")), "layers": _layers(story.get("layers"))},
    }


# Роли цветов палитры: p0 — светлый, p1 — тёмный, p2 — акцент, p3 и p4 — дополнительные
PALETTE_DEFAULT = ["#FFFFFF", "#141414", "#EFEAE2", "#8A8A8A", "#FFFFFF"]
PALETTE_ROLES = {"ru": ["Светлый", "Тёмный", "Акцент", "Доп. 1", "Доп. 2"],
                 "en": ["Light", "Dark", "Accent", "Extra 1", "Extra 2"]}


def sanitize_palette(v):
    base = PALETTE_DEFAULT
    v = (v if isinstance(v, list) else [])[:5]
    v = v + base[len(v):]
    return [(c.upper() if isinstance(c, str) and _HEX.match(c) else base[i]) for i, c in enumerate(v)]


# ============ Стартовые стили ============
# Палитра: p0 — светлый, p1 — тёмный, p2 — акцент (по умолчанию — из логотипа).
# Общая сетка: поля 6% ширины, логотип 14–15%, рубрика (хештег) 3,2%, заголовок 6,6–7,4% —
# это не ниже читаемого на телефоне. Затемнение — только под текстом, по плавной кривой.
# Сторис в стилях выключены, но уже сверстаны под безопасные зоны: сверху 14%, снизу 35%.
# В карусели заголовок, рубрика и затемнение под ними — только на обложке (F = slides first),
# на остальных кадрах — логотип: так делают редакции, и текст не повторяется на каждом фото.
CONTRAST = {"mode": "contrast", "light": "p0", "dark": "p1"}
LIGHT = {"mode": "fixed", "value": "p0"}
DARK = {"mode": "fixed", "value": "p1"}
M = MARGIN
# Безопасные зоны сторис 1080×1920 в долях ширины: верх 14% высоты, низ 35% высоты
STORY_TOP = round(0.14 * 1920 / 1080, 3)       # 0.249
STORY_BOTTOM = round(0.35 * 1920 / 1080, 3)    # 0.622


def _t(**kw):
    base = dict(type="text", anchor="bl", x=M, y=M, source="static", text="", font="onest",
                weight=600, size=0.066, tracking=-0.01, leading=1.08, case="none", align="left", maxw=0.84,
                lines=4, color=dict(LIGHT), opacity=1)
    base.update(kw)
    return base


def _rubric(**kw):
    base = dict(source="hashtag", font="onest", size=0.032, weight=600, case="upper", tracking=0.08,
                maxw=0, lines=1, leading=1.0, color=dict(CONTRAST))
    base.update(kw)
    return _t(**base)


def _logo(**kw):
    base = dict(type="logo", anchor="tl", x=M, y=M, asset="logo", w=0.15, color=dict(CONTRAST), opacity=1)
    base.update(kw)
    return base


def _counter(**kw):
    base = dict(source="counter", text="{i} / {n}", font="jetbrains", weight=500, size=0.03, maxw=0, lines=1,
                tracking=0, color=dict(CONTRAST), slides="rest")
    base.update(kw)
    return _t(**base)


F = "first"


def _grad(side="bottom", extent=0.6, opacity=0.85, **kw):
    base = dict(type="gradient", side=side, extent=extent, color=dict(DARK), opacity=opacity, adaptive=False)
    base.update(kw)
    return base


PRESETS = [
    {"key": "editorial", "name": {"ru": "Редакция", "en": "Editorial"}, "spec": {
        "feed": {"layers": [
            _grad("bottom", 0.62, 0.88, slides=F),
            _logo(anchor="tl"),
            _rubric(anchor="tr", align="right", slides=F),
            _t(anchor="bl", source="title", size=0.068, leading=1.06, slides=F)]},
        "story": {"enabled": False, "layers": [
            _grad("bottom", 0.8, 0.9),
            _logo(anchor="tl", x=0.08, y=STORY_TOP, w=0.2),
            _rubric(anchor="tr", x=0.08, y=STORY_TOP + 0.01, size=0.04, align="right"),
            _t(anchor="bl", x=0.08, y=STORY_BOTTOM + 0.02, source="title", size=0.09, leading=1.05)]}}},

    {"key": "caption", "name": {"ru": "Подпись", "en": "Caption"}, "spec": {
        "feed": {"layers": [
            _logo(anchor="tr", w=0.14),
            _rubric(anchor="tl", slides=F),
            _t(anchor="bl", x=0, y=M, source="title", font="golos", weight=700, size=0.05, leading=1.12,
               tracking=0, maxw=0.8, color=dict(CONTRAST), slides=F,
               plate={"color": {"mode": "fixed", "value": "p2"}, "opacity": 1, "radius": 0, "padx": 1.2, "pady": 0.7})]},
        "story": {"enabled": False, "layers": [
            _logo(anchor="tr", x=0.08, y=STORY_TOP, w=0.18),
            _rubric(anchor="tl", x=0.08, y=STORY_TOP + 0.01, size=0.04),
            _t(anchor="bl", x=0, y=STORY_BOTTOM + 0.02, source="title", font="golos", weight=700, size=0.066,
               leading=1.12, tracking=0, maxw=0.82, color=dict(CONTRAST),
               plate={"color": {"mode": "fixed", "value": "p2"}, "opacity": 1, "radius": 0, "padx": 1.2, "pady": 0.7})]}}},

    {"key": "frame", "name": {"ru": "Рамка", "en": "Frame"}, "spec": {
        "feed": {"layers": [
            {"type": "rect", "fit": "inset", "m": 0.035, "stroke": 0.0028, "radius": 0,
             "color": dict(CONTRAST), "opacity": 0.95},
            _rubric(anchor="tl", x=M + 0.025, y=M + 0.025, slides=F),
            _logo(anchor="br", x=M + 0.025, y=M + 0.025, w=0.14)]},
        "story": {"enabled": False, "layers": [
            {"type": "rect", "fit": "inset", "m": 0.05, "stroke": 0.003, "radius": 0,
             "color": dict(CONTRAST), "opacity": 0.95},
            _rubric(anchor="tl", x=0.09, y=STORY_TOP, size=0.04),
            _logo(anchor="bl", x=0.09, y=STORY_BOTTOM, w=0.2)]}}},

    {"key": "carousel", "name": {"ru": "Карусель", "en": "Carousel"}, "spec": {
        "feed": {"layers": [
            _grad("top", 0.58, 0.85, slides=F),
            _t(anchor="tl", source="title", size=0.074, leading=1.04, slides=F),
            _counter(anchor="br"),
            _logo(anchor="bl", w=0.14)]},
        "story": {"enabled": False, "layers": []}}},

    {"key": "center", "name": {"ru": "Центр", "en": "Center"}, "spec": {
        "feed": {"layers": [
            {"type": "overlay", "color": dict(DARK), "opacity": 0.35, "slides": F},
            _t(anchor="mc", x=0, y=0, source="title", font="cormorant", weight=600, size=0.1,
               leading=1.0, tracking=0, maxw=0.8, align="center", slides=F),
            _logo(anchor="bc", x=0, y=M, w=0.14, color=dict(LIGHT))]},
        "story": {"enabled": False, "layers": [
            {"type": "overlay", "color": dict(DARK), "opacity": 0.35},
            _t(anchor="mc", x=0, y=-0.1, source="title", font="cormorant", weight=600, size=0.13,
               leading=1.0, tracking=0, maxw=0.82, align="center"),
            _logo(anchor="tc", x=0, y=STORY_TOP, w=0.2, color=dict(LIGHT))]}}},

    {"key": "mark", "name": {"ru": "Знак", "en": "Mark"}, "spec": {
        "feed": {"layers": [_logo(anchor="tr", w=0.14)]},
        "story": {"enabled": False, "layers": [_logo(anchor="tr", x=0.08, y=STORY_TOP, w=0.2)]}}},

    {"key": "blank", "name": {"ru": "С нуля", "en": "Blank"}, "designer": True, "spec": {
        "feed": {"layers": []}, "story": {"enabled": False, "layers": []}}},
]
# Три варианта, которые бот показывает на фото клиента при подключении
ONBOARD_PRESETS = ("editorial", "caption", "frame")
# Если клиент пропустил выбор — эти стили создаются сами
SEED_PRESETS = ("editorial", "caption")


def preset(key):
    for p in PRESETS:
        if p["key"] == key:
            return p
    return None


def preset_spec(key):
    p = preset(key)
    return sanitize_spec(copy.deepcopy(p["spec"])) if p else sanitize_spec({})


def preset_name(key, lang="ru"):
    p = preset(key)
    return (p["name"].get(lang) or p["name"]["ru"]) if p else key


def presets_public(lang="ru"):
    return [{"key": p["key"], "name": p["name"].get(lang) or p["name"]["ru"], "designer": bool(p.get("designer")),
             "spec": sanitize_spec(copy.deepcopy(p["spec"]))} for p in PRESETS]

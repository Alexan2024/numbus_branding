"""Обновление движка: выделение «*…*», поток «под слоем», фото в рамке, рубрика без «#»,
масштаб фото, форматы 16:9 и 1.91:1, жирный текст из Telegram → разметка."""
import numpy as np
from PIL import Image

import render as R
import spec as S

PAL = S.sanitize_palette(["#FFFFFF", "#111111", "#FF0000"])
RED = np.array([255, 0, 0])


def _photo(c=(30, 120, 200), size=(1500, 1000)):
    return Image.new("RGB", size, c)


def _render(layers, fields=None, photo=None, fmt="4:5", **kw):
    spec = S.sanitize_spec({"feed": {"layers": layers}})
    ctx = R.Ctx(palette=PAL, fields=fields or {}, **kw)
    return R.render_template(photo or _photo(), spec, fmt, ctx)[0][1], ctx


def _text(**k):
    base = dict(type="text", id="t", source="title", anchor="tl", x=0.05, y=0.05, size=0.08, maxw=0.9,
                color={"mode": "fixed", "value": "p0"})
    base.update(k)
    return base


def test_parse_marks():
    assert R.parse_marks("a *b c* d") == ("a b c d", [False, False, True, True, True, False, False])
    assert R.parse_marks("5 \\* 3")[0] == "5 * 3"
    assert R.parse_marks("один *звёздочка")[0] == "один *звёздочка"     # непарная — знак
    assert R.parse_marks("без разметки")[1] == [False] * len("без разметки")


def test_plain_text_unchanged_by_markup_engine():
    """Без звёздочек и без em — ровно как было: маркеры не влияют на вёрстку."""
    a, _ = _render([_text()], dict(title="Обычный заголовок в две строки для проверки"))
    b, _ = _render([_text(em={"color": {"mode": "fixed", "value": "p2"}})], dict(title="Обычный заголовок в две строки для проверки"))
    assert np.abs(np.asarray(a, int) - np.asarray(b, int)).max() == 0


def test_emphasis_colours_only_marked_words():
    img, _ = _render([_text(em={"color": {"mode": "fixed", "value": "p2"}, "weight": 800})],
                     dict(title="Слово *выделено* тут"))
    arr = np.asarray(img, int)
    red = (np.abs(arr - RED).sum(2) < 60)
    assert red.sum() > 500                      # выделение нарисовано красным
    white = (arr.min(2) > 235)
    assert white.sum() > 500                    # остальное — основным цветом
    assert "*" not in R.parse_marks("Слово *выделено* тут")[0]


def test_after_places_layer_below_previous():
    t1 = _text(id="t1", lines=6)
    t2 = _text(id="t2", source="subtitle", y=0.9, size=0.04, after="t1", gap=0.03)
    short, c1 = _render([t1, t2], dict(title="Коротко", subtitle="Подзаголовок"))
    long_, c2 = _render([t1, t2], dict(title="Очень длинный заголовок, который займёт сразу несколько строк", subtitle="Подзаголовок"))
    for c in (c1, c2):
        b1, b2 = c.boxes["t1"], c.boxes["t2"]
        assert abs(b2[1] - (b1[1] + b1[3] + 0.03 * 1920)) < 1.5
    assert c2.boxes["t2"][1] > c1.boxes["t2"][1] + 100   # длинный заголовок отодвинул подзаголовок


def test_photo_frame_background_and_box():
    L = dict(type="photo", id="ph", anchor="bl", x=0.1, y=0.1, w=0.8, h=0.5, radius=0,
             bg={"mode": "fixed", "value": "#F5F4EF"})
    img, ctx = _render([L])
    arr = np.asarray(img, int)
    W, H = img.size
    assert tuple(arr[10, 10]) == (245, 244, 239)                 # фон кадра — цвет слоя
    cy = int(H - 0.1 * W - 0.25 * W)
    assert tuple(arr[cy, W // 2]) == (30, 120, 200)              # внутри рамки — фото
    assert R.photo_box([S.sanitize_layer(L)], W, H) == (int(round(0.8 * W)), int(round(0.5 * W)))


def test_photo_frame_zoom_and_focus():
    half = Image.new("RGB", (1000, 1000), (0, 0, 255))
    half.paste((255, 0, 0), (0, 0, 500, 1000))                   # левая половина красная
    L = dict(type="photo", id="ph", anchor="tl", x=0, y=0, w=1, h=1.25, zoom=2, fx=0, fy=0.5)
    img, _ = _render([L], photo=half)
    arr = np.asarray(img, int)
    assert (np.abs(arr - RED).sum(2) < 30).mean() > 0.95         # ×2 и фокус слева — только красное


def test_ctx_zoom_full_bleed():
    half = Image.new("RGB", (1000, 1000), (0, 0, 255))
    half.paste((255, 0, 0), (0, 0, 500, 1000))
    img, _ = _render([], photo=half, fmt="1:1", zoom=2.0, focus=(0.0, 0.5))
    assert (np.abs(np.asarray(img, int) - RED).sum(2) < 30).mean() > 0.95


def test_clean_rubric():
    L = dict(type="text", source="hashtag", tag="clean")
    ctx = R.Ctx(fields=dict(hashtag="#Vive_la_différence"))
    assert R.text_content(S.sanitize_layer(L), ctx) == "Vive la différence"
    ctx = R.Ctx(fields=dict(hashtag="#рубрика"))
    assert R.text_content(S.sanitize_layer(dict(type="text", source="hashtag")), ctx) == "#рубрика"


def test_sanitize_new_fields():
    out = S.sanitize_spec({"feed": {"layers": [
        dict(type="photo", zoom=9, radius=2, bg={"mode": "adaptive"}),
        dict(type="text", em={"weight": 5000, "color": {"mode": "fixed", "value": "#FFEC1B"}, "junk": 1},
             after="t1", gap=9, tag="clean", source="title"),
        dict(type="text", after="bad id!", tag="clean", source="title")]}})["feed"]["layers"]
    assert out[0]["zoom"] == 4 and out[0]["radius"] == 1 and out[0]["bg"] == {"mode": "fixed", "value": "p0"}
    assert out[1]["em"] == {"color": {"mode": "fixed", "value": "#FFEC1B"}, "weight": 1000}
    assert out[1]["after"] == "t1" and out[1]["gap"] == 1 and "tag" not in out[1]
    assert "after" not in out[2]


def test_new_formats():
    assert R.FEED_SIZES["16:9"] == (1920, 1080)
    img, _ = _render([], fmt="1.91:1")
    assert img.size == (1920, 1005)


def test_telegram_bold_to_marks():
    import bot

    class E:
        def __init__(self, o, n):
            self.offset, self.length, self.type = o, n, "bold"
    t = "Налог 🎓 на вклады ₽1 трлн "
    off = len(t[:t.index("₽")].encode("utf-16-le")) // 2
    assert bot.marked(t, [E(off, 8)]) == "Налог 🎓 на вклады *₽1 трлн* "
    assert bot.unmark("*₽1 трлн*") == "₽1 трлн"
    assert bot.marked("без выделения", None) == "без выделения"


def test_gradient_start_offset():
    """start — сплошная часть у края: левее начала градиента плотность полная, без start — как раньше."""
    g = dict(type="gradient", side="left", extent=0.3, start=0.25, color={"mode": "fixed", "value": "#000000"},
             opacity=1)
    img, _ = _render([g], photo=_photo((200, 200, 200)))
    arr = np.asarray(img, int)
    W = img.width
    assert arr[100, int(0.2 * W)].max() < 10          # в сплошной части — почти чёрный
    assert arr[100, int(0.6 * W)].min() > 190         # за градиентом — фото
    a, _ = _render([dict(g, start=0)], photo=_photo((200, 200, 200)))
    b, _ = _render([{k: v for k, v in g.items() if k != "start"}], photo=_photo((200, 200, 200)))
    assert np.array_equal(np.asarray(a), np.asarray(b))

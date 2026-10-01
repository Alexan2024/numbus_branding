"""Шаблон по образцу: подгонка по маске (образец — наш же рендер, ответ известен), рамка
фото, плашка, клиент модели (оба формата API), сквозной сценарий в боте."""
import asyncio
import io
import json

import numpy as np
import pytest
from PIL import Image

import render as R
import sample as SM
import spec as S


def _photo(w=1600, h=1200):
    y, x = np.mgrid[0:h, 0:w]
    arr = np.stack([(x * 255 / w), (y * 255 / h), np.full_like(x, 120)], 2).astype(np.uint8)
    return Image.fromarray(arr)


def _sample(layers, fields, fmt="4:5", photo=None):
    spec = S.sanitize_spec({"feed": {"layers": layers}})
    ctx = R.Ctx(palette=S.sanitize_palette(["#FFFFFF", "#111111", "#FF2200"]), fields=fields)
    img = R.render_template(photo or _photo(), spec, fmt, ctx)[0][1]
    return img.resize((SM.WORK_W, int(img.height * SM.WORK_W / img.width)), Image.LANCZOS)


def _desc_text(box, lines, **kw):
    d = {"kind": "text", "role": "title", "lines": lines, "box": box, "align": "left", "color": "#FFFFFF",
         "font": "sans", "weight": 700}
    d.update(kw)
    return d


def test_roundtrip_title_full_bleed():
    """Образец нарисован движком (Montserrat 700, 0.07): подгонка находит кегль, место и шрифт."""
    truth = dict(type="text", id="t", source="title", anchor="tl", x=0.07, y=0.12, font="montserrat", weight=700,
                 size=0.07, leading=1.1, maxw=0.9, color={"mode": "fixed", "value": "#FFFFFF"})
    img = _sample([dict(type="gradient", side="top", extent=0.6, color={"mode": "fixed", "value": "#000000"}, opacity=0.9),
                   truth], {"title": "Новый сезон кофе\nуже в меню"})
    W, H = img.size
    desc = {"photo": {"mode": "full"}, "layers": [
        _desc_text([0.05, 0.08, 0.8, 0.16], ["Новый сезон кофе", "уже в меню"])]}   # рамка модели — с запасом
    spec, fields, fmt, photo, report = SM.build(img, desc)
    assert fmt == "4:5"
    T = [L for L in spec["feed"]["layers"] if L["type"] == "text"][0]
    assert T["source"] == "title" and fields["title"] == "Новый сезон кофе\nуже в меню"
    assert abs(T["size"] - 0.07) / 0.07 < 0.08
    assert abs(T["x"] - 0.07) < 0.012 and abs(T["y"] - 0.12) < 0.012
    assert report[0][1]["score"] > 0.6
    assert T["font"] in ("montserrat", "manrope", "inter", "onest", "golos", "geologica", "rubik", "jost")


def test_roundtrip_frame_and_rubric():
    layers = [dict(type="photo", id="ph", anchor="tl", x=0, y=0.55, w=1, h=0.7, bg={"mode": "fixed", "value": "#F5F4EF"}),
              dict(type="text", id="k", source="hashtag", tag="clean", anchor="tl", x=0.1, y=0.08, size=0.03,
                   weight=600, font="inter", color={"mode": "fixed", "value": "#D01C1A"}),
              dict(type="text", id="t", source="title", anchor="tl", x=0.1, y=0.16, size=0.06, weight=500,
                   font="lora", leading=1.1, maxw=0.9, color={"mode": "fixed", "value": "#121212"})]
    img = _sample(layers, {"title": "Bond markets give\nFrance a whacking", "hashtag": "#Vive_la_différence"})
    desc = {"canvas": {"bg": "#F5F4EF"}, "photo": {"mode": "frame", "box": [0.02, 0.42, 0.96, 0.55]}, "layers": [
        _desc_text([0.08, 0.05, 0.4, 0.04], ["Vive la différence"], role="rubric", color="#D01C1A", weight=600),
        _desc_text([0.08, 0.11, 0.7, 0.12], ["Bond markets give", "France a whacking"], color="#121212", font="serif",
                   weight=500)]}
    spec, fields, fmt, photo, _ = SM.build(img, desc)
    ph = spec["feed"]["layers"][0]
    assert ph["type"] == "photo" and abs(ph["y"] - 0.55) < 0.01 and abs(ph["w"] - 1) < 0.01
    assert ph["bg"]["value"] == "#F5F4EF"
    rub = [L for L in spec["feed"]["layers"] if L.get("source") == "hashtag"][0]
    assert rub["tag"] == "clean" and fields["hashtag"] == "#Vive_la_différence"
    t = [L for L in spec["feed"]["layers"] if L.get("source") == "title"][0]
    assert t["font"] in ("lora", "playfair", "cormorant") and abs(t["size"] - 0.06) / 0.06 < 0.1
    assert photo.size[1] < img.size[1]                 # фото для превью — вырезано из рамки


def test_plate_refined_and_text_inside():
    lab = dict(type="text", id="p", source="static", text="BREAKING NEWS", anchor="tl", x=0.08, y=0.08, size=0.035,
               weight=700, font="inter", color={"mode": "fixed", "value": "#FFFFFF"},
               plate={"color": {"mode": "fixed", "value": "#CC0001"}, "opacity": 1, "radius": 0, "padx": 0.4, "pady": 0.4})
    img = _sample([lab], {})
    desc = {"photo": {"mode": "full"}, "layers": [_desc_text([0.09, 0.07, 0.2, 0.03], ["BREAKING NEWS"], role="label",
                                                              plate={"color": "#CC0001", "box": [0.08, 0.065, 0.15, 0.04]})]}
    spec, *_ = SM.build(img, desc)
    L = [L for L in spec["feed"]["layers"] if L["type"] == "text"][0]
    assert L["text"] == "BREAKING NEWS" and L["plate"]["color"]["value"].startswith("#C")
    assert abs(L["size"] - 0.035) / 0.035 < 0.12


def test_emphasis_from_model():
    t = dict(type="text", id="t", source="title", anchor="tl", x=0.07, y=0.7, size=0.06, weight=500, font="lora",
             maxw=0.9, color={"mode": "fixed", "value": "#FFFFFF"}, em={"color": {"mode": "fixed", "value": "#FFEC1B"}})
    img = _sample([t], {"title": "Donates record *$3bn* to campus"})
    desc = {"photo": {"mode": "full"}, "layers": [_desc_text([0.05, 0.53, 0.9, 0.06], ["Donates record $3bn to campus"],
                                                              font="serif", weight=500, emphasis=["$3bn"],
                                                              em_color="#FFEC1B")]}
    spec, fields, *_ = SM.build(img, desc)
    L = [L for L in spec["feed"]["layers"] if L["type"] == "text"][0]
    assert "*$3bn*" in fields["title"] and L["em"]["color"]["value"] == "#FFEC1B"


def test_pick_format_and_parse_json():
    assert SM.pick_format(1080, 1350) == "4:5" and SM.pick_format(1200, 630) == "1.91:1"
    assert SM.pick_format(1000, 777) == "orig"
    assert SM.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(SM.SampleError):
        SM.parse_json("нет json")


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_ask_model_providers(monkeypatch, provider):
    import httpx
    seen = {}

    class Resp:
        status_code = 200

        def json(self):
            js = json.dumps({"photo": {"mode": "full"}, "layers": []})
            return {"content": [{"type": "text", "text": js}]} if provider == "anthropic" else \
                {"choices": [{"message": {"content": "```json\n" + js + "\n```"}}]}

    def post(url, headers, json, timeout):
        seen.update(url=url, headers=headers, body=json)
        return Resp()
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(SM, "VISION_PROVIDER", provider)
    monkeypatch.setattr(SM, "VISION_API_KEY", "k")
    monkeypatch.setattr(SM, "VISION_MODEL", "m")
    monkeypatch.setattr(SM, "VISION_BASE_URL", "")
    out = SM.ask_model(_photo(400, 500))
    assert out["photo"]["mode"] == "full"
    if provider == "anthropic":
        assert seen["url"].endswith("/v1/messages") and seen["headers"]["x-api-key"] == "k"
        assert seen["body"]["messages"][0]["content"][0]["type"] == "image"
    else:
        assert seen["url"].endswith("/chat/completions") and seen["headers"]["Authorization"] == "Bearer k"
        assert seen["body"]["messages"][0]["content"][1]["type"] == "image_url"


def test_not_configured(monkeypatch):
    monkeypatch.setattr(SM, "VISION_API_KEY", "")
    with pytest.raises(SM.SampleError) as e:
        SM.ask_model(_photo(100, 100))
    assert e.value.code == "not_configured"


def test_bot_sample_flow(fresh_db, monkeypatch):
    import bot
    import db
    from fake_tg import Harness, buttons
    from test_bot_flows import onboard
    monkeypatch.setattr(bot, "REFRESH_DELAY", 0.01)
    monkeypatch.setattr(SM, "VISION_API_KEY", "k")
    monkeypatch.setattr(SM, "VISION_MODEL", "m")
    truth = dict(type="text", id="t", source="title", anchor="tl", x=0.07, y=0.1, font="inter", weight=700, size=0.07,
                 maxw=0.9, color={"mode": "fixed", "value": "#FFFFFF"})
    img = _sample([truth], {"title": "Образец заголовка"})
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    desc = {"photo": {"mode": "full"}, "layers": [_desc_text([0.05, 0.06, 0.8, 0.08], ["Образец заголовка"])]}
    monkeypatch.setattr(SM, "ask_model", lambda im: desc)

    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 731)
            n0 = len(db.list_templates(bid))
            await u.press("smp:start")
            assert "фирменный пост" in u.last_text()
            await u.text("а можно текстом?")                         # ждём картинку — напоминание
            assert h.app.user_data[731].get("await") == "sample"
            await u.document(buf.getvalue(), "sample.jpg")
            res = h.tg.last(731, lambda m: "photo" in m and buttons(m))
            assert res is not None
            cbs = [b["callback_data"] for b in buttons(res)]
            assert cbs[:1] == ["smp:save"] and "smp:again" in cbs
            await u.press("smp:save")
            tpls = db.list_templates(bid)
            assert len(tpls) == n0 + 1 and tpls[-1]["name"] == "По образцу"
            assert any(L.get("source") == "title" for L in tpls[-1]["spec"]["feed"]["layers"])
            assert db.get_prefs(731, bid)["tid"] == tpls[-1]["id"]
            assert db.events("sample_saved")
    asyncio.run(go())

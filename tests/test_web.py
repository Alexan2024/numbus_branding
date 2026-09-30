"""Сервер редактора: вход, роли, бренд по умолчанию, лимиты запросов, новые поля и ручки."""
import io
import hmac
import json
import time
import struct
import asyncio
import hashlib
from urllib.parse import urlencode

import pytest
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

import db
import web
import spec as S
import render as R
from conftest import make_logo

TOKEN = "123456:TEST"


def init_data(uid, token=TOKEN, age=0):
    pairs = {"auth_date": str(int(time.time()) - age), "query_id": "AAE",
             "user": json.dumps({"id": uid, "first_name": "T"})}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


def run(coro):
    return asyncio.run(coro)


async def client():
    c = TestClient(TestServer(web.build_web(TOKEN)))
    await c.start_server()
    return c


def H(uid, **kw):
    return {"X-Init-Data": init_data(uid, **kw)}


@pytest.fixture()
def brand(fresh_db):
    db.ensure_user(10, "ru")
    bid = db.create_brand(10, "pilot", 30)
    db.update_kit(bid, name="Тест")
    db.seed_templates(bid, "ru")
    for uid, role in ((11, "designer"), (12, "editor")):
        db.ensure_user(uid, "ru")
        db.add_member(bid, uid, role)
    web.LIMITER.hits.clear()
    return bid


def test_roles_and_default_brand(brand):
    async def go():
        c = await client()
        try:
            r = await c.get(f"/api/state?b={brand}", headers=H(10))
            assert r.status == 200
            st = await r.json()
            assert st["role"] == "owner" and st["brand"]["name"] == "Тест"
            assert st["palette_roles"][2] == "Акцент"
            assert st["safe"]["story_top"] == S.STORY_TOP
            assert any(p["key"] == "blank" and p["designer"] for p in st["presets"])
            assert (await c.get(f"/api/state?b={brand}", headers=H(11))).status == 200     # дизайнер
            assert (await c.get(f"/api/state?b={brand}", headers=H(12))).status == 403     # участник
            assert (await c.get(f"/api/state?b={brand}", headers=H(99))).status == 403     # чужой
            # кнопка меню бота открывает редактор без ?b= — берётся активный бренд
            r = await c.get("/api/state", headers=H(11))
            assert r.status == 200 and (await r.json())["brand"]["id"] == brand
            assert (await c.get("/api/state", headers=H(12))).status == 403
            # подпись старше суток и чужая подпись не проходят
            assert (await c.get(f"/api/state?b={brand}", headers=H(10, age=90000))).status == 401
            assert (await c.get(f"/api/state?b={brand}", headers=H(10, token="1:other"))).status == 401
            assert [e["kind"] for e in db.events("editor_open")]
        finally:
            await c.close()
    run(go())


def test_desktop_session(brand):
    async def go():
        c = await client()
        try:
            k = db.create_login_link(11, brand)
            r = await c.post("/auth/login", json={"k": k})
            sess = (await r.json())["session"]
            assert (await c.post("/auth/login", json={"k": k})).status == 401        # ссылка одноразовая
            assert (await c.get("/api/state", headers={"X-Session": sess})).status == 200
            assert (await c.get(f"/api/state?b={brand + 1}", headers={"X-Session": sess})).status == 401
            db.set_member_role(brand, 11, "editor")                                   # разжаловали
            assert (await c.get("/api/state", headers={"X-Session": sess})).status == 403
            with db._conn() as con:                                                   # токен хранится хэшем
                assert not con.execute("SELECT 1 FROM sessions WHERE token=?", (sess,)).fetchone()
        finally:
            await c.close()
    run(go())


def test_rate_limit_preview(brand, monkeypatch):
    monkeypatch.setitem(web.LIMITS, "preview", (3, 60))

    async def go():
        c = await client()
        try:
            spec = S.preset_spec("carousel")
            codes = []
            for i in range(4):
                r = await c.post(f"/api/preview?b={brand}", headers=H(10),
                                 json={"spec": spec, "fields": {"title": "Заголовок"}, "i": 2, "n": 3})
                codes.append(r.status)
                if r.status == 200:
                    assert r.content_type == "image/jpeg"
            assert codes == [200, 200, 200, 429]
        finally:
            await c.close()
    run(go())


def test_logo_colors_and_font_missing(brand):
    async def go():
        c = await client()
        try:
            fd = FormData()
            fd.add_field("file", make_logo(color=(220, 40, 60)), filename="logo.png", content_type="image/png")
            r = await c.post(f"/api/upload/logo?b={brand}", headers=H(11), data=fd)
            out = await r.json()
            assert r.status == 200 and out["colors"], out
            r = await c.get(f"/api/logo/colors?b={brand}", headers=H(10))
            assert (await r.json())["colors"] == out["colors"]
            # шрифт только с латиницей: редактор должен предупредить о кириллице и ₽
            from fontTools import subset
            from fontTools.ttLib import TTFont
            f = TTFont(R.os.path.join(R.FONT_DIR, "Inter.ttf"))
            sub = subset.Subsetter()
            sub.populate(text="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 .,")
            sub.subset(f)
            buf = io.BytesIO()
            f.save(buf)
            fd = FormData()
            fd.add_field("file", buf.getvalue(), filename="Latin.ttf", content_type="font/ttf")
            r = await c.post(f"/api/upload/font1?b={brand}", headers=H(10), data=fd)
            out = await r.json()
            assert r.status == 200, out
            assert "Ж" in out["missing"] and "₽" in out["missing"]
        finally:
            await c.close()
    run(go())


def test_templates_and_kit(brand):
    async def go():
        c = await client()
        try:
            r = await c.post(f"/api/templates?b={brand}", headers=H(11),
                             json={"name": "", "spec": S.preset_spec("mark"), "from": "mark"})
            t = await r.json()
            assert r.status == 200 and t["name"] == "Шаблон"
            r = await c.put(f"/api/templates/{t['id']}?b={brand}", headers=H(11),
                            json={"name": "Мой", "spec": t["spec"]})
            assert r.status == 200
            kinds = [e["kind"] for e in db.events()]
            assert "tpl_create" in kinds and "tpl_save" in kinds
            r = await c.put(f"/api/kit?b={brand}", headers=H(10),
                            json={"palette": ["#fff", "#111111", "#e4572e"], "hashtags": ["новости", "#Новости", "меню"]})
            out = await r.json()
            assert out["brand"]["palette"][2] == "#E4572E"
            assert out["brand"]["palette"][0] == S.PALETTE_DEFAULT[0]      # «#fff» — не цвет формата #RRGGBB
            assert out["brand"]["hashtags"] == ["#новости", "#меню"]
            r = await c.put(f"/api/kit?b={brand}", headers=H(10), data="[1,2]")
            assert r.status == 400
        finally:
            await c.close()
    run(go())


def _psd_header(w, h):
    return (b"8BPS" + struct.pack(">H", 1) + b"\0" * 6 + struct.pack(">HIIHH", 3, h, w, 8, 3)
            + struct.pack(">I", 0) + struct.pack(">I", 0) + struct.pack(">I", 0) + struct.pack(">H", 0))


def test_import_psd_too_big(brand):
    async def go():
        c = await client()
        try:
            fd = FormData()
            fd.add_field("file", _psd_header(12000, 9000), filename="big.psd", content_type="image/vnd.adobe.photoshop")
            r = await c.post(f"/api/import?b={brand}", headers=H(11), data=fd)
            assert r.status == 422
            assert (await r.json())["error"] == "psd_big"
        finally:
            await c.close()
    run(go())


def test_healthz_and_headers(brand):
    async def go():
        c = await client()
        try:
            r = await c.get("/healthz")
            assert r.status == 200 and (await r.json())["web"] == "ok"
            assert r.headers["X-Content-Type-Options"] == "nosniff"
            r = await c.get("/")
            assert r.status == 200 and r.headers.get("Referrer-Policy") == "no-referrer"
        finally:
            await c.close()
    run(go())

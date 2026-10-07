"""v6 (пилот): лимиты тела запросов, адрес клиента для лимитов, initData без id, ошибки редактора
→ админам, уборка графики, честный статус хранилища, язык по умолчанию, настройки из окружения,
пределы импорта PSD/PDF/PNG, редактор (закрытие с несохранёнными правками, тексты)."""
import io
import os
import re
import hmac
import json
import time
import asyncio
import hashlib
import sqlite3
import logging
from datetime import timedelta
from urllib.parse import urlencode

import numpy as np
import pytest
from aiohttp import FormData, web as aioweb
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image, ImageDraw

import db
import web
import backups
import importer as I
import spec as S
from test_web import TOKEN, H, init_data, run, client, brand  # noqa: F401  (фикстура brand)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _png(w=300, h=200, color=(200, 40, 90, 255)):
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((10, 10, w - 10, h - 10), fill=color)
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


# ============ 1. Тело запроса ============
def test_login_rejects_big_body_before_parsing(brand, monkeypatch):
    parsed = []
    real = json.loads
    monkeypatch.setattr(web.json, "loads", lambda s, *a, **k: parsed.append(len(s)) or real(s, *a, **k))

    async def go():
        c = await client()
        try:
            r = await c.post("/auth/login", data=b'{"k": "' + b"A" * 5000 + b'"}',
                             headers={"Content-Type": "application/json"})
            assert r.status == 413 and (await r.json())["error"] == "too_big"

            async def chunks():                       # без Content-Length — режется по ходу чтения
                for _ in range(64):
                    yield b"A" * 1024
            r = await c.post("/auth/login", data=chunks(), headers={"Content-Type": "application/json"})
            assert r.status == 413
            assert not parsed                          # до json.loads дело не дошло
            r = await c.post("/auth/login", json={"k": "nope"})
            assert r.status == 401                     # обычный вход работает
        finally:
            await c.close()
    run(go())


def test_default_body_limit_1mb_and_kit_caps(brand):
    async def go():
        c = await client()
        try:
            big = {"name": "x", "spec": S.preset_spec("mark"), "pad": "x" * (web.BODY_MAX + 10)}
            r = await c.post(f"/api/templates?b={brand}", headers=H(10), json=big)
            assert r.status == 413 and (await r.json())["error"] == "too_big"
            r = await c.post(f"/api/preview?b={brand}", headers=H(10), json=big)
            assert r.status == 413
            # бренд: больше 64 КБ — отказ, хештеги — не больше 16, мусор пропускается
            r = await c.put(f"/api/kit?b={brand}", headers=H(10), json={"hashtags": ["t%d" % i for i in range(20000)]})
            assert r.status == 413
            tags = [{"x": 1}, ["y"], True, None, "#" + "д" * 500] + ["тег%d" % i for i in range(100)]
            r = await c.put(f"/api/kit?b={brand}", headers=H(10), json={"hashtags": tags})
            out = (await r.json())["brand"]["hashtags"]
            assert r.status == 200 and len(out) == 16 and out[0] == "#" + "д" * 30 and out[1] == "#тег0"
        finally:
            await c.close()
    run(go())


def test_upload_and_import_caps(brand, monkeypatch):
    monkeypatch.setattr(web, "UPLOAD_MAX", 200_000)
    monkeypatch.setattr(web, "IMPORT_MAX", 200_000)
    monkeypatch.setattr(web, "FONT_MAX", 100_000)

    async def go():
        c = await client()
        try:
            def form(data, name):
                fd = FormData()
                fd.add_field("file", data, filename=name, content_type="application/octet-stream")
                return fd
            r = await c.post(f"/api/upload/image?b={brand}", headers=H(11), data=form(b"\0" * 400_000, "a.png"))
            assert r.status == 413 and (await r.json())["error"] == "file_big"
            r = await c.post(f"/api/upload/font1?b={brand}", headers=H(11), data=form(b"\0" * 150_000, "a.ttf"))
            assert r.status == 413 and (await r.json())["error"] == "font_big"
            r = await c.post(f"/api/import?b={brand}", headers=H(11), data=form(b"\0" * 400_000, "a.psd"))
            assert r.status == 413 and (await r.json())["error"] == "file_big"
            # под лимитом — как раньше
            r = await c.post(f"/api/upload/image?b={brand}", headers=H(11), data=form(_png(), "g.png"))
            assert r.status == 200 and (await r.json())["asset"].startswith("img_")
            # не multipart — 400, а не 500
            r = await c.post(f"/api/upload/image?b={brand}", headers=H(11), data=b"junk",
                             )
            assert r.status == 400
        finally:
            await c.close()
    run(go())


def test_login_limit_uses_rightmost_forwarded_hop(brand, monkeypatch):
    monkeypatch.setitem(web.LIMITS, "login", (3, 60))

    async def go():
        c = await client()
        try:
            codes = []
            for i in range(5):     # клиент подменяет начало заголовка, прокси дописывает настоящий адрес в конец
                r = await c.post("/auth/login", json={"k": "x"}, headers={"X-Forwarded-For": f"10.0.0.{i}, 203.0.113.7"})
                codes.append(r.status)
            assert codes == [401, 401, 401, 429, 429]
            r = await c.post("/auth/login", json={"k": "x"}, headers={"X-Forwarded-For": "203.0.113.8"})
            assert r.status == 401                     # другой человек — свой лимит
        finally:
            await c.close()
    run(go())


# ============ 4. initData без id ============
def test_signed_init_data_without_user_id_is_401(brand):
    def signed(user):
        pairs = {"auth_date": str(int(time.time())), "user": json.dumps(user)}
        dcs = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
        pairs["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
        return {"X-Init-Data": urlencode(pairs)}

    async def go():
        c = await client()
        try:
            for user in ({"first_name": "x"}, {"id": None}, {"id": "abc"}, [1, 2], {"id": True}):
                r = await c.get(f"/api/state?b={brand}", headers=signed(user))
                assert r.status == 401, user
            assert (await c.get(f"/api/state?b={brand}", headers=signed({"id": 10}))).status == 200
        finally:
            await c.close()
    run(go())


# ============ 8. Ошибки редактора — админам ============
def test_unexpected_error_json_and_alert(brand, monkeypatch):
    got = []

    async def hook(err, uid=None, where=""):
        got.append((type(err).__name__, uid, where))
    monkeypatch.setattr(web, "ALERT", hook)

    def boom(*a, **k):
        raise RuntimeError("секрет в тексте ошибки")
    monkeypatch.setattr(web.db, "list_templates", boom)

    async def go():
        c = await client()
        try:
            r = await c.get(f"/api/state?b={brand}", headers=H(10))
            assert r.status == 500
            body = await r.text()
            assert json.loads(body) == {"error": "server"} and "секрет" not in body and "Traceback" not in body
            assert r.headers["X-Content-Type-Options"] == "nosniff"
            await asyncio.sleep(0.05)
            assert got == [("RuntimeError", 10, "web GET /api/state")]
            # ожидаемые ответы (404, 401) — не ошибки, админам не пишем
            assert (await c.get("/api/state", headers={"X-Init-Data": "x"})).status == 401
            assert (await c.get(f"/api/asset/zzz?b={brand}", headers=H(10))).status == 404
            assert len(got) == 1
        finally:
            await c.close()
    run(go())
    monkeypatch.setattr(web, "ALERT", lambda *a, **k: (_ for _ in ()).throw(ValueError("hook broke")))

    async def go2():                    # сломанное оповещение не мешает ответу
        c = await client()
        try:
            assert (await c.get(f"/api/state?b={brand}", headers=H(10))).status == 500
        finally:
            await c.close()
    run(go2())


# ============ 5. Хранилище ============
def test_healthz_reports_storage(brand, monkeypatch):
    async def go(flag):
        monkeypatch.setattr(db, "STORAGE_PERSISTENT", flag)
        c = await client()
        try:
            r = await c.get("/healthz")
            j = await r.json()
            assert r.status == 200 and j["web"] == "ok" and "telegram" in j and j["persistent"] is flag
        finally:
            await c.close()
    run(go(False))
    run(go(True))


def test_data_dir_set_by_hand_is_not_persistent(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(d))
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    monkeypatch.setattr(db.os.path, "ismount", lambda p: p == "/")
    assert db._resolve_data_dir() == (str(d), False)          # раньше: True при любом DATA_DIR
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(d))     # Railway Volume — постоянное
    assert db._resolve_data_dir() == (str(d), True)
    monkeypatch.setenv("DATA_DIR", str(d / "sub"))             # папка внутри тома — тоже
    assert db._resolve_data_dir() == (str(d / "sub"), True)
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH")
    monkeypatch.setattr(db.os.path, "ismount", lambda p: p in ("/", str(d)))   # том docker-compose
    assert db._resolve_data_dir() == (str(d / "sub"), True)


# ============ 3. Уборка графики ============
def test_fresh_unused_image_survives_cleanup(brand):
    async def go():
        c = await client()
        try:
            fd = FormData()
            fd.add_field("file", _png(), filename="g.png", content_type="image/png")
            aid = (await (await c.post(f"/api/upload/image?b={brand}", headers=H(11), data=fd)).json())["asset"]
            t = db.list_templates(brand)[0]
            r = await c.put(f"/api/templates/{t['id']}?b={brand}", headers=H(10), json={"name": t["name"], "spec": t["spec"]})
            assert r.status == 200
            assert (await c.get(f"/api/asset/{aid}?b={brand}", headers=H(11))).status == 200   # раньше — 404
            # через сутки ненужная картинка уходит
            old = (db._now() - timedelta(hours=25)).isoformat()
            with db._conn() as con:
                con.execute("UPDATE assets SET created_at=? WHERE brand_id=? AND kind=?", (old, brand, aid))
            assert db.gc_images(brand) == 1 and not db.has_asset(brand, aid)
        finally:
            await c.close()
    run(go())


def test_used_old_image_kept_and_logo_untouched(brand):
    db.set_asset(brand, "img_aaaaaaaaaaaa", _png())
    old = (db._now() - timedelta(days=3)).isoformat()
    with db._conn() as con:
        con.execute("UPDATE assets SET created_at=? WHERE brand_id=?", (old, brand))
    t = db.list_templates(brand)[0]
    t["spec"]["feed"]["layers"].append({"type": "image", "asset": "img_aaaaaaaaaaaa", "fit": "box", "anchor": "mc",
                                        "x": 0, "y": 0, "w": 0.3, "opacity": 1})
    db.update_template(brand, t["id"], t["name"], t["spec"])
    db.set_asset(brand, "logo", _png())
    assert db.gc_images(brand) == 0 and db.has_asset(brand, "img_aaaaaaaaaaaa") and db.has_asset(brand, "logo")


def test_assets_created_at_migration(tmp_path, monkeypatch):
    path = str(tmp_path / "old.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE assets (brand_id INTEGER NOT NULL, kind TEXT NOT NULL, data BLOB NOT NULL, PRIMARY KEY (brand_id, kind))")
    con.execute("INSERT INTO assets VALUES (1, 'img_000000000000', x'00')")
    con.commit()
    con.close()
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    db.init_db()                                     # повторный запуск — без ошибок
    with db._conn() as c:
        row = c.execute("SELECT created_at FROM assets").fetchone()
    assert row["created_at"]                          # старым файлам — дата миграции
    assert db.gc_images(1) == 0                       # и сутки их не трогаем


# ============ 7. Язык ============
def test_unknown_language_defaults_to_russian(fresh_db):
    for uid, code, lang in ((1, "de", "ru"), (2, None, "ru"), (3, "", "ru"), (4, "en", "en"), (5, "en-GB", "en"),
                            (6, "uk", "ru"), (7, "zh-hans", "ru")):
        assert db.ensure_user(uid, code)["lang"] == lang, code


# ============ 6. Настройки из окружения ============
def test_env_fallbacks(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="numbus.backup")
    for raw, hour in (("", 4), ("04:00", 4), ("4", 4), ("23", 23), ("24", 4), ("x", 4), (" 7 ", 7), ("-1", 4)):
        monkeypatch.setenv("BACKUP_HOUR", raw)
        assert backups.backup_hour() == hour, raw
    monkeypatch.delenv("BACKUP_HOUR")
    assert backups.backup_hour() == 4
    assert any("BACKUP_HOUR" in r.message for r in caplog.records)
    for k, v in (("ENDPOINT", "s3.example"), ("BUCKET", "b"), ("KEY", "k"), ("SECRET", "s")):
        monkeypatch.setenv("BACKUP_S3_" + k, v)
    monkeypatch.setenv("BACKUP_S3_REGION", "")
    monkeypatch.setenv("BACKUP_S3_PREFIX", " ")
    cfg = backups.s3_config()
    assert cfg["region"] == "ru-1" and cfg["prefix"] == "numbus/"
    monkeypatch.setenv("BACKUP_S3_SECRET", "  ")
    assert backups.s3_config() is None                 # пустой секрет — S3 не настроен, а не падение
    monkeypatch.setenv("PILOT_DAYS", "тридцать")
    assert backups.env_int("PILOT_DAYS", 30, 1, 3650) == 30
    monkeypatch.setenv("PILOT_DAYS", "35")
    assert backups.env_int("PILOT_DAYS", 30, 1, 3650) == 35
    monkeypatch.setenv("BACKUP_TO_TELEGRAM", "TRUE ")
    assert backups.env_bool("BACKUP_TO_TELEGRAM") is True


# ============ 2. Импорт: память ============
def _psd(W, H, layers):
    from psd_tools import PSDImage
    from psd_tools.api.layers import PixelLayer
    psd = PSDImage.new("RGB", (W, H))
    for name, im, top, left in layers:
        psd.append(PixelLayer.frompil(im, psd, name, top=top, left=left))
    b = io.BytesIO()
    psd.save(b)
    return b.getvalue()


def test_psd_limits(monkeypatch):
    assert I.PSD_MAX_PIXELS == 16_000_000
    from test_web import _psd_header
    with pytest.raises(I.ImportFail) as e:
        I.parse_file(_psd_header(4100, 4000), "big.psd")                 # 16,4 Мп
    assert e.value.code == "psd_big"
    plate = Image.new("RGBA", (200, 80), (20, 30, 160, 255))
    huge = Image.new("RGBA", (900, 900), (0, 0, 0, 0))
    ImageDraw.Draw(huge).ellipse((300, 300, 500, 500), fill=(200, 30, 60, 255))
    data = _psd(400, 500, [("Plate", plate, 40, 40), ("Huge", huge, -300, -300)])
    monkeypatch.setattr(I, "PSD_LAYER_MAX_PIXELS", 500_000)               # 900×900 = 0,81 Мп
    with pytest.raises(I.ImportFail) as e:
        I.parse_file(data, "huge.psd")
    assert e.value.code == "psd_layer_big"
    monkeypatch.setattr(I, "PSD_LAYER_MAX_PIXELS", 16_000_000)
    lay = I.parse_file(data, "huge.psd")
    assert lay.pieces


def test_too_tall_pdf_and_png_rejected_before_render():
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument.new()
    pdf.new_page(200, 6000)                                                 # 1920 × 57 600 px
    b = io.BytesIO()
    pdf.save(b)
    with pytest.raises(I.ImportFail) as e:
        I.parse_file(b.getvalue(), "long.ai")
    assert e.value.code == "too_tall"                                        # а не «AI без PDF-совместимости»
    with pytest.raises(I.ImportFail) as e:
        I.parse_file(_png(100, 500), "tall.png")                            # 1920 × 9600
    assert e.value.code == "too_tall"
    with pytest.raises(I.ImportFail) as e:
        I.figma_plan({"type": "FRAME", "absoluteBoundingBox": {"x": 0, "y": 0, "width": 100, "height": 900}})
    assert e.value.code == "too_tall"
    I.check_work_size(1080, 1920)                                            # сторис — можно


def test_import_jobs_run_one_at_a_time(monkeypatch):
    seen, now = [], [0]

    def job(*a):
        now[0] += 1
        seen.append(now[0])
        time.sleep(0.05)
        now[0] -= 1

    async def go():
        await asyncio.gather(*(web.heavy_import(job) for _ in range(3)))
    run(go())
    assert max(seen) == 1


# ============ Редактор (webapp.html) ============
HTML = open(os.path.join(ROOT, "webapp.html"), encoding="utf-8").read()


def test_editor_closing_confirmation_and_texts():
    assert "enableClosingConfirmation" in HTML and "disableClosingConfirmation" in HTML
    assert 'isVersionAtLeast("6.2")' in HTML                     # старые клиенты — без ошибки
    assert "application/pdf" in re.search(r'const LOGO_ACCEPT = "([^"]+)"', HTML).group(1)
    assert "PNG, JPG, SVG или PDF" in HTML and "PNG, JPG, SVG or PDF" in HTML
    assert "Не сохранилось. Попробуйте ещё раз." in HTML and 'errText(e, "save")' in HTML
    assert 'bootScreen(t("loading"))' in HTML and 'retry: "Повторить"' in HTML
    assert "chips.unshift(fileBtn)" in HTML                       # «Своё фото» — первым
    for ru in ('sizeS: "Размер"', 'alignS: "Выравнивание"', 'plateS: "Подложка"'):
        assert ru in HTML
    # каждый код ошибки сервера, который видит человек, переведён на оба языка
    for code in ("file_big", "font_big", "too_tall", "psd_layer_big", "psd_big"):
        assert HTML.count(f"e_{code}:") == 2, code


def test_looks_apply_to_feed_and_story():
    body = HTML[HTML.index("function applyLook(p)"):]
    body = body[:body.index("\n}\n")]
    assert "spec.feed.layers = " in body and "spec.story.layers = " in body

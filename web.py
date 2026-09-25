"""NUMBUS Branding — сервер Mini App (редактор бренда и шаблонов).

Работает в том же процессе, что и бот. Каждый запрос к /api подписан
Telegram: заголовок X-Init-Data проверяется HMAC-ом по токену бота
(https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app).
Редактировать может только владелец бренда.
"""
import os
import io
import json
import hmac
import time
import asyncio
import hashlib
import logging
from urllib.parse import parse_qsl

from aiohttp import web
from PIL import Image

import db
import render as R
import spec as S

logger = logging.getLogger("numbus.web")
BASE = os.path.dirname(os.path.abspath(__file__))
WEBAPP_FILE = os.path.join(BASE, "webapp.html")
INIT_MAX_AGE = 24 * 3600
UPLOAD_KINDS = {"logo", "logo_alt", "sample"} | set(R.CUSTOM_FONT_SLOTS)
_SEM = asyncio.Semaphore(2)
_DEFAULT_SAMPLE = None


def check_init_data(init_data: str, token: str):
    """Возвращает dict пользователя Telegram или None."""
    if not init_data or not token:
        return None
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    got = pairs.pop("hash", None)
    if not got:
        return None
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, got):
        return None
    try:
        if time.time() - int(pairs.get("auth_date", "0")) > INIT_MAX_AGE:
            return None
        return json.loads(pairs.get("user", "{}")) or None
    except Exception:
        return None


async def heavy(fn, *a):
    async with _SEM:
        return await asyncio.to_thread(fn, *a)


def jerr(status, code):
    return web.json_response({"error": code}, status=status)


@web.middleware
async def auth_mw(request, handler):
    if not request.path.startswith("/api/"):
        return await handler(request)
    user = check_init_data(request.headers.get("X-Init-Data", ""), request.app["token"])
    if not user:
        return jerr(401, "auth")
    try:
        bid = int(request.query.get("b", "0"))
    except ValueError:
        return jerr(400, "brand")
    if db.member_role(bid, int(user["id"])) != "owner":
        return jerr(403, "owner_only")
    request["uid"] = int(user["id"])
    request["bid"] = bid
    request["lang"] = (db.get_user(int(user["id"])) or {}).get("lang", "ru")
    return await handler(request)


# ============ Статика ============
async def index(request):
    return web.FileResponse(WEBAPP_FILE, headers={"Cache-Control": "no-cache"})


_FONT_FILES = {v["file"] for v in R.FONTS.values()}


async def font_file(request):
    name = request.match_info["name"]
    path = os.path.join(R.FONT_DIR, name)
    if name not in _FONT_FILES or not os.path.exists(path):
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "public, max-age=2592000",
                                           "Content-Type": "font/ttf"})


# ============ Состояние ============
def _fonts_public(kit):
    out = [{"key": k, "label": v["label"], "file": v["file"], "min": v["min"], "max": v["max"],
            "group": v["group"]} for k, v in R.FONTS.items()]
    for slot, name in (kit.get("custom_fonts") or {}).items():
        out.append({"key": slot, "label": name, "custom": True, "min": 1, "max": 1000, "group": "custom"})
    return out


async def api_state(request):
    bid = request["bid"]
    b = db.get_brand(bid)
    kit = b["kit"]
    return web.json_response({
        "brand": {"id": bid, "name": kit.get("name") or "", "palette": S.sanitize_palette(kit.get("palette")),
                  "hashtags": kit.get("hashtags") or [], "custom_fonts": kit.get("custom_fonts") or {}},
        "assets": {k: db.has_asset(bid, k) for k in ("logo", "logo_alt", "sample")},
        "fonts": _fonts_public(kit),
        "templates": [{"id": t["id"], "name": t["name"], "spec": t["spec"]} for t in db.list_templates(bid)],
        "presets": S.presets_public(request["lang"]),
        "lang": request["lang"],
        "max_templates": db.MAX_TEMPLATES,
    })


async def api_kit(request):
    bid = request["bid"]
    try:
        body = await request.json()
    except Exception:
        return jerr(400, "json")
    changes = {}
    if "name" in body:
        name = str(body["name"]).strip()[:40]
        if name:
            changes["name"] = name
    if "palette" in body:
        changes["palette"] = S.sanitize_palette(body["palette"])
    if "hashtags" in body:
        tags, seen = [], set()
        for t in body["hashtags"] if isinstance(body["hashtags"], list) else []:
            t = "#" + str(t).strip().lstrip("#")[:30]
            if len(t) > 1 and t.lower() not in seen:
                seen.add(t.lower())
                tags.append(t)
        changes["hashtags"] = tags[:16]
    if changes:
        db.update_kit(bid, **changes)
    return web.json_response({"ok": True})


# ============ Ассеты ============
def _default_sample_jpeg():
    global _DEFAULT_SAMPLE
    if _DEFAULT_SAMPLE is None:
        img = R.sample_image(1200, 1500)
        _DEFAULT_SAMPLE = R.to_jpeg(img, 85)
    return _DEFAULT_SAMPLE


async def api_asset(request):
    bid, kind = request["bid"], request.match_info["kind"]
    if kind not in ("logo", "logo_alt", "sample"):
        raise web.HTTPNotFound()
    data = db.get_asset(bid, kind)
    if not data:
        if kind == "sample":
            return web.Response(body=await heavy(_default_sample_jpeg), content_type="image/jpeg")
        raise web.HTTPNotFound()
    ctype = "image/jpeg" if kind == "sample" else "image/png"
    return web.Response(body=data, content_type=ctype, headers={"Cache-Control": "no-cache"})


async def api_font(request):
    slot = request.match_info["slot"]
    if slot not in R.CUSTOM_FONT_SLOTS:
        raise web.HTTPNotFound()
    data = db.get_asset(request["bid"], slot)
    if not data:
        raise web.HTTPNotFound()
    return web.Response(body=data, content_type="font/ttf")


def _prep_sample(data):
    img = R.open_photo(data)
    img.thumbnail((1600, 1600), Image.LANCZOS)
    return R.to_jpeg(img, 88)


async def api_upload(request):
    bid, kind = request["bid"], request.match_info["kind"]
    if kind not in UPLOAD_KINDS:
        return jerr(400, "kind")
    reader = await request.multipart()
    part = await reader.next()
    if part is None or part.name != "file":
        return jerr(400, "file")
    filename = part.filename or ""
    data = await part.read(decode=False)
    if not data:
        return jerr(400, "empty")
    if kind in ("logo", "logo_alt"):
        try:
            png, had_alpha = await heavy(R.prepare_logo, data)
        except ValueError:
            return jerr(422, "logo_empty")
        except Exception:
            return jerr(422, "logo_bad")
        db.set_asset(bid, kind, png)
        return web.json_response({"ok": True, "bg_removed": not had_alpha})
    if kind == "sample":
        try:
            jpg = await heavy(_prep_sample, data)
        except Exception:
            return jerr(422, "photo_bad")
        db.set_asset(bid, "sample", jpg)
        return web.json_response({"ok": True})
    # свой шрифт
    if not filename.lower().endswith((".ttf", ".otf")) or not R.validate_font(data):
        return jerr(422, "font_bad")
    db.set_asset(bid, kind, data)
    kit = db.get_brand(bid)["kit"]
    fonts = dict(kit.get("custom_fonts") or {})
    fonts[kind] = R.font_name(data, os.path.splitext(filename)[0] or kind)
    db.update_kit(bid, custom_fonts=fonts)
    return web.json_response({"ok": True, "name": fonts[kind]})


async def api_asset_delete(request):
    bid, kind = request["bid"], request.match_info["kind"]
    if kind not in ("logo_alt", "sample") and kind not in R.CUSTOM_FONT_SLOTS:
        return jerr(400, "kind")  # основной логотип удалить нельзя — только заменить
    db.del_asset(bid, kind)
    if kind in R.CUSTOM_FONT_SLOTS:
        kit = db.get_brand(bid)["kit"]
        fonts = dict(kit.get("custom_fonts") or {})
        fonts.pop(kind, None)
        db.update_kit(bid, custom_fonts=fonts)
    return web.json_response({"ok": True})


# ============ Шаблоны ============
async def _body_tpl(request):
    try:
        body = await request.json()
    except Exception:
        return None, None
    name = str(body.get("name") or "").strip()[:40] or "Шаблон"
    return name, S.sanitize_spec(body.get("spec"))


async def api_tpl_create(request):
    name, spec = await _body_tpl(request)
    if spec is None:
        return jerr(400, "json")
    tid = db.create_template(request["bid"], name, spec)
    if not tid:
        return jerr(409, "limit")
    return web.json_response({"id": tid, "name": name, "spec": spec})


async def api_tpl_update(request):
    name, spec = await _body_tpl(request)
    if spec is None:
        return jerr(400, "json")
    if not db.update_template(request["bid"], int(request.match_info["tid"]), name, spec):
        return jerr(404, "template")
    return web.json_response({"id": int(request.match_info["tid"]), "name": name, "spec": spec})


async def api_tpl_delete(request):
    if not db.delete_template(request["bid"], int(request.match_info["tid"])):
        return jerr(404, "template")
    return web.json_response({"ok": True})


def brand_ctx(bid, fields=None, dark=0.0):
    b = db.get_brand(bid)
    kit = b["kit"]
    logos = {}
    for k in ("logo", "logo_alt"):
        data = db.get_asset(bid, k)
        if data:
            logos[k] = Image.open(io.BytesIO(data)).convert("RGBA")
    customs = {slot: db.get_asset(bid, slot) for slot in (kit.get("custom_fonts") or {})}
    return R.Ctx(S.sanitize_palette(kit.get("palette")), logos, customs, fields or {}, dark)


def _server_preview(bid, spec, surface, fmt, fields):
    data = db.get_asset(bid, "sample")
    photo = R.open_photo(data) if data else R.sample_image(1200, 1500)
    ctx = brand_ctx(bid, fields)
    if surface == "story":
        W, H = R.STORY_SIZE
        layers = spec["story"]["layers"]
    else:
        W, H = R.feed_size(photo, fmt)
        layers = spec["feed"]["layers"]
    return R.to_preview(R.render_surface(photo, W, H, layers, ctx), 1400)


async def api_preview(request):
    """Точный рендер движком бота — то, что клиент получит в итоге."""
    try:
        body = await request.json()
    except Exception:
        return jerr(400, "json")
    spec = S.sanitize_spec(body.get("spec"))
    f = body.get("fields") if isinstance(body.get("fields"), dict) else {}
    fields = {k: str(f.get(k) or "")[:300] for k in ("title", "subtitle", "hashtag")}
    fields.update(i=1, n=8)
    fmt = body.get("fmt") if body.get("fmt") in R.FEED_SIZES else "4:5"
    surface = "story" if body.get("surface") == "story" else "feed"
    jpg = await heavy(_server_preview, request["bid"], spec, surface, fmt, fields)
    return web.Response(body=jpg, content_type="image/jpeg")


def build_web(token: str) -> web.Application:
    app = web.Application(middlewares=[auth_mw], client_max_size=20 * 1024 * 1024)
    app["token"] = token
    app.router.add_get("/", index)
    app.router.add_get("/fonts/{name}", font_file)
    app.router.add_get("/api/state", api_state)
    app.router.add_put("/api/kit", api_kit)
    app.router.add_get("/api/asset/{kind}", api_asset)
    app.router.add_delete("/api/asset/{kind}", api_asset_delete)
    app.router.add_get("/api/font/{slot}", api_font)
    app.router.add_post("/api/upload/{kind}", api_upload)
    app.router.add_post("/api/templates", api_tpl_create)
    app.router.add_put("/api/templates/{tid:\\d+}", api_tpl_update)
    app.router.add_delete("/api/templates/{tid:\\d+}", api_tpl_delete)
    app.router.add_post("/api/preview", api_preview)
    return app

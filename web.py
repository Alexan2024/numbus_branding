"""NUMBUS Branding — сервер Mini App (редактор бренда и шаблонов).

Работает в том же процессе, что и бот. Каждый запрос к /api подписан
Telegram: заголовок X-Init-Data проверяется HMAC-ом по токену бота
(https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app).
С компьютера вход — по одноразовой ссылке из бота (заголовок X-Session).
Редактировать могут владелец бренда и дизайнер.
"""
import os
import io
import re
import json
import hmac
import time
import asyncio
import hashlib
import inspect
import logging
from collections import deque
from urllib.parse import parse_qsl

import aiohttp
from aiohttp import web
from PIL import Image

import db
import render as R
import spec as S
import importer as I

logger = logging.getLogger("numbus.web")
BASE = os.path.dirname(os.path.abspath(__file__))
WEBAPP_FILE = os.path.join(BASE, "webapp.html")
INIT_MAX_AGE = 24 * 3600
# Сколько байт можно прислать. По умолчанию — 1 МБ (шаблон, бренд, превью); большие
# файлы — только в загрузку и импорт: каждый лишний мегабайт до проверки — это память
# и время общего с ботом процесса.
BODY_MAX = 1024 * 1024
IMPORT_MAX = 60 * 1024 * 1024          # PSD бывают тяжёлыми
UPLOAD_MAX = 60 * 1024 * 1024          # фото, логотип, графика слоя
FONT_MAX = 10 * 1024 * 1024            # TTF/OTF
LOGIN_MAX = 4096                       # {"k": "…"} — несколько десятков байт
KIT_MAX = 64 * 1024                    # название, палитра, хештеги
MULTIPART_SLACK = 64 * 1024            # заголовки частей формы сверх самого файла
HASHTAGS_IN = 64                       # сколько хештегов вообще разбираем (сохраняем 16)
IMG_KIND = re.compile(r"^img_[0-9a-f]{12}$")
FIGMA_API = "https://api.figma.com/v1"
UPLOAD_KINDS = {"logo", "logo_alt", "sample", "image"} | set(R.CUSTOM_FONT_SLOTS)
_SEM = asyncio.Semaphore(2)
_DEFAULT_SAMPLE = None
# Оповещение админов о неожиданной ошибке редактора. bot.py ставит сюда свою функцию:
# web.ALERT = lambda err, uid=None, where="": alert_admins(app.bot, err, uid, where)
# (может вернуть корутину — она выполнится в фоне, ответ клиенту не ждёт).
ALERT = None
_ALERT_TASKS = set()

# Сколько тяжёлых запросов можно за окно (секунд) одному человеку
LIMITS = {"preview": (40, 60), "upload": (30, 60), "import": (8, 300), "login": (20, 60)}


class Limiter:
    """Скользящее окно в памяти: хватает на один процесс бота."""

    def __init__(self):
        self.hits = {}

    def allow(self, key, limit, per):
        now = time.monotonic()
        q = self.hits.get(key)
        if q is None:
            if len(self.hits) > 5000:          # чистим пустые окна, чтобы словарь не рос
                for k in [k for k, v in self.hits.items() if not v or now - v[-1] > 600]:
                    self.hits.pop(k, None)
            q = self.hits[key] = deque()
        while q and now - q[0] > per:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


LIMITER = Limiter()


def rate_ok(request, kind, who=None):
    limit, per = LIMITS[kind]
    return LIMITER.allow((who if who is not None else request.get("uid"), kind), limit, per)


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
        user = json.loads(pairs.get("user", "{}"))
        # подпись верна, но без id человека (или id не число) — войти некому
        if not isinstance(user, dict) or isinstance(user.get("id"), bool) or int(user.get("id")) <= 0:
            return None
        return user
    except Exception:
        return None


async def heavy(fn, *a):
    async with _SEM:
        return await asyncio.to_thread(fn, *a)


# Импорт PSD берёт до ~2 ГБ памяти на холсте 16 Мп — два сразу не запускаем
_IMPORT_SEM = asyncio.Semaphore(1)


async def heavy_import(fn, *a):
    async with _IMPORT_SEM:
        return await heavy(fn, *a)


def jerr(status, code):
    return web.json_response({"error": code}, status=status)


class BodyTooBig(Exception):
    """Тело запроса больше допустимого — ответ 413 с кодом для редактора."""

    def __init__(self, code="too_big", limit=0):
        super().__init__(code)
        self.code = code
        self.limit = limit


async def read_body(request, cap, code="too_big"):
    """Тело запроса, но не больше cap байт. По Content-Length отказ — до чтения;
    без него (chunked) — как только пришёл лишний байт. Остаток aiohttp отбросит сам."""
    if request.content_length is not None and request.content_length > cap:
        raise BodyTooBig(code, cap)
    buf = bytearray()
    while True:
        chunk = await request.content.read(256 * 1024)
        if not chunk:
            break
        buf += chunk
        if len(buf) > cap:
            raise BodyTooBig(code, cap)
    return bytes(buf)


async def json_body(request, cap=BODY_MAX):
    """JSON из тела запроса с ограничением размера. None — не JSON."""
    raw = await read_body(request, cap)
    try:
        return json.loads(raw)
    except ValueError:
        return None


async def read_part(part, cap, code="file_big"):
    """Часть multipart-формы (файл) с ограничением размера: читаем кусками."""
    buf = bytearray()
    while True:
        chunk = await part.read_chunk(256 * 1024)
        if not chunk:
            break
        buf += chunk
        if len(buf) > cap:
            raise BodyTooBig(code, cap)
    return bytes(buf)


def check_length(request, cap, code="file_big"):
    """Форма с файлом: заведомо слишком большую отклоняем, не читая."""
    if request.content_length is not None and request.content_length > cap + MULTIPART_SLACK:
        raise BodyTooBig(code, cap)


def resolve_brand(uid, bid):
    """Бренд из адреса; без него (кнопка меню бота) — активный бренд человека,
    иначе первый, где он владелец или дизайнер. None — редактировать нечего."""
    if bid:
        return bid if db.can_edit(bid, uid) else None
    ab = (db.get_user(uid) or {}).get("active_brand")
    if ab and db.can_edit(ab, uid):
        return ab
    for b in db.user_brands(uid):
        if b["role"] in db.EDITOR_ROLES:
            return b["id"]
    return None


@web.middleware
async def auth_mw(request, handler):
    if not request.path.startswith("/api/"):
        return await handler(request)
    try:
        bid = int(request.query.get("b") or 0)
    except ValueError:
        return jerr(400, "brand")
    user = check_init_data(request.headers.get("X-Init-Data", ""), request.app["token"])
    if user:
        uid = int(user["id"])
    else:
        # Вход с компьютера: сессия, выданная по одноразовой ссылке из бота
        sess = db.check_session(request.headers.get("X-Session", ""))
        if not sess or (bid and sess[1] != bid):
            return jerr(401, "auth")
        uid, bid = sess[0], sess[1]
    resolved = resolve_brand(uid, bid)
    if not resolved:
        # участник без прав редактора или человек не из команды
        return jerr(403, "owner_only")
    request["uid"] = uid
    request["bid"] = resolved
    request["role"] = db.member_role(resolved, uid)
    request["lang"] = (db.get_user(uid) or {}).get("lang", "ru")
    return await handler(request)


@web.middleware
async def headers_mw(request, handler):
    resp = await handler(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    return resp


def _alert(err, request):
    """Сообщает админам через ALERT (если бот его поставил). Никогда не падает."""
    hook = ALERT
    if hook is None:
        return
    try:
        res = hook(err, request.get("uid"), f"web {request.method} {request.path}")
        if inspect.isawaitable(res):
            task = asyncio.ensure_future(res)
            _ALERT_TASKS.add(task)
            task.add_done_callback(_ALERT_TASKS.discard)
    except Exception as e:
        logger.warning("оповещение об ошибке не отправлено: %s", e)


@web.middleware
async def errors_mw(request, handler):
    """Неожиданная ошибка: в лог и админам, клиенту — короткий JSON без подробностей."""
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except BodyTooBig as e:
        return jerr(413, e.code)
    except Exception as e:
        logger.exception("Ошибка редактора: %s %s", request.method, request.path)
        _alert(e, request)
        return jerr(500, "server")


# ============ Статика ============
async def index(request):
    return web.FileResponse(WEBAPP_FILE, headers={"Cache-Control": "no-cache"})


STATUS = {"telegram": "подключаюсь"}      # бот обновляет: ok | нет связи


async def healthz(request):
    """Жив ли сервер. 200 даже без связи с Telegram — бот сам переподключится.
    persistent: false — база лежит не на подключённом диске и пропадёт при деплое."""
    return web.json_response({"web": "ok", **STATUS, "persistent": bool(db.STORAGE_PERSISTENT)})


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


def _font_missing(bid, kit):
    """Каких нужных знаков нет в своих шрифтах бренда (их нарисует Inter)."""
    out = {}
    for slot in (kit.get("custom_fonts") or {}):
        data = db.get_asset(bid, slot)
        cov = R.font_cover(slot, {slot: data}) if data else None
        out[slot] = "".join(ch for ch in R.CHECK_CHARS if cov is not None and ord(ch) not in cov)
    return out


async def api_state(request):
    bid = request["bid"]
    b = db.get_brand(bid)
    kit = b["kit"]
    lang = request["lang"]
    db.log_event("editor_open", request["uid"], bid, role=request["role"])
    return web.json_response({
        "brand": {"id": bid, "name": kit.get("name") or "", "palette": S.sanitize_palette(kit.get("palette")),
                  "hashtags": kit.get("hashtags") or [], "custom_fonts": kit.get("custom_fonts") or {}},
        "role": request["role"],
        "assets": {k: db.has_asset(bid, k) for k in ("logo", "logo_alt", "sample")},
        "fonts": _fonts_public(kit),
        "templates": [{"id": t["id"], "name": t["name"], "spec": t["spec"]} for t in db.list_templates(bid)],
        "presets": S.presets_public(lang, S.brand_traits(db.get_asset(bid, "logo"), kit.get("palette"))),
        "palette_roles": S.PALETTE_ROLES.get(lang) or S.PALETTE_ROLES["ru"],
        "recent_titles": db.recent_titles(bid),
        "font_missing": _font_missing(bid, kit),
        "safe": {"margin": S.MARGIN, "story_top": S.STORY_TOP, "story_bottom": S.STORY_BOTTOM},
        "lang": lang,
        "max_templates": db.MAX_TEMPLATES,
    })


async def api_kit(request):
    bid = request["bid"]
    body = await json_body(request, KIT_MAX)
    if not isinstance(body, dict):
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
        raw = body["hashtags"] if isinstance(body["hashtags"], list) else []
        for t in raw[:HASHTAGS_IN]:
            if not isinstance(t, (str, int, float)) or isinstance(t, bool):
                continue
            t = "#" + str(t)[:64].strip().lstrip("#")[:30]
            if len(t) > 1 and t.lower() not in seen:
                seen.add(t.lower())
                tags.append(t)
        changes["hashtags"] = tags[:16]
    if changes:
        db.update_kit(bid, **changes)
    kit = db.get_brand(bid)["kit"]
    return web.json_response({"ok": True, "brand": {
        "name": kit.get("name") or "", "palette": S.sanitize_palette(kit.get("palette")),
        "hashtags": kit.get("hashtags") or []}})


# ============ Ассеты ============
def _default_sample_jpeg():
    global _DEFAULT_SAMPLE
    if _DEFAULT_SAMPLE is None:
        img = R.sample_image(1200, 1500)
        _DEFAULT_SAMPLE = R.to_jpeg(img, 85)
    return _DEFAULT_SAMPLE


async def api_asset(request):
    bid, kind = request["bid"], request.match_info["kind"]
    if kind not in ("logo", "logo_alt", "sample") and not IMG_KIND.match(kind):
        raise web.HTTPNotFound()
    data = db.get_asset(bid, kind)
    if not data:
        if kind == "sample":
            return web.Response(body=await heavy(_default_sample_jpeg), content_type="image/jpeg")
        raise web.HTTPNotFound()
    ctype = "image/jpeg" if kind == "sample" else "image/png"
    cache = "public, max-age=31536000, immutable" if kind.startswith("img_") else "no-cache"
    return web.Response(body=data, content_type=ctype, headers={"Cache-Control": cache})


async def api_font(request):
    slot = request.match_info["slot"]
    if slot not in R.CUSTOM_FONT_SLOTS:
        raise web.HTTPNotFound()
    data = db.get_asset(request["bid"], slot)
    if not data:
        raise web.HTTPNotFound()
    return web.Response(body=data, content_type="font/ttf")


async def api_logo_colors(request):
    """Фирменные цвета, найденные в логотипе — для кнопки «Взять из логотипа»."""
    data = db.get_asset(request["bid"], "logo")
    if not data:
        return web.json_response({"colors": []})
    try:
        colors = await heavy(R.logo_colors, data, 3)
    except Exception:
        colors = []
    return web.json_response({"colors": colors})


def _prep_sample(data):
    img = R.open_photo(data)
    img.thumbnail((1600, 1600), Image.LANCZOS)
    return R.to_jpeg(img, 88)


def _prep_layer_image(data):
    img = R.open_image(data).convert("RGBA")
    img.thumbnail((2400, 2400), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    png = buf.getvalue()
    return "img_" + hashlib.sha1(png).hexdigest()[:12], png


def _prep_logo(data):
    png, had_alpha = R.prepare_logo(data)
    try:
        colors = R.logo_colors(png, 3)
    except Exception:
        colors = []
    return png, had_alpha, colors


async def api_upload(request):
    bid, kind = request["bid"], request.match_info["kind"]
    if kind not in UPLOAD_KINDS:
        return jerr(400, "kind")
    if not rate_ok(request, "upload"):
        return jerr(429, "rate")
    font = kind in R.CUSTOM_FONT_SLOTS
    cap, code = (FONT_MAX, "font_big") if font else (UPLOAD_MAX, "file_big")
    check_length(request, cap, code)
    try:
        reader = await request.multipart()
        part = await reader.next()
    except (AssertionError, ValueError):
        return jerr(400, "file")
    if part is None or getattr(part, "name", None) != "file":
        return jerr(400, "file")
    filename = part.filename or ""
    try:
        data = await read_part(part, cap, code)
    except (AssertionError, ValueError):
        return jerr(400, "file")
    if not data:
        return jerr(400, "empty")
    if kind in ("logo", "logo_alt"):
        try:
            png, had_alpha, colors = await heavy(_prep_logo, data)
        except R.TooBig:
            return jerr(422, "logo_big")
        except ValueError:
            return jerr(422, "logo_empty")
        except Exception:
            return jerr(422, "logo_bad")
        db.set_asset(bid, kind, png)
        db.log_event("kit_logo", request["uid"], bid, where="editor", slot=kind)
        return web.json_response({"ok": True, "bg_removed": not had_alpha, "colors": colors})
    if kind == "image":
        try:
            aid, png = await heavy(_prep_layer_image, data)
        except Exception:
            return jerr(422, "photo_bad")
        db.set_asset(bid, aid, png)
        return web.json_response({"ok": True, "asset": aid})
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
    # каких знаков нет — их нарисует запасной шрифт; редактор предупредит
    return web.json_response({"ok": True, "name": fonts[kind], "missing": R.missing_chars(data)})


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
    body = await json_body(request)
    if not isinstance(body, dict):
        return None, None, None
    fallback = "Шаблон" if request["lang"] == "ru" else "Template"
    name = str(body.get("name") or "").strip()[:40] or fallback
    return name, S.sanitize_spec(body.get("spec")), body


async def api_tpl_create(request):
    name, spec, body = await _body_tpl(request)
    if spec is None:
        return jerr(400, "json")
    tid = db.create_template(request["bid"], name, spec)
    if not tid:
        return jerr(409, "limit")
    src = str(body.get("from") or "")[:20]
    db.log_event("tpl_create", request["uid"], request["bid"], tid=tid, src=src, role=request["role"])
    return web.json_response({"id": tid, "name": name, "spec": spec})


async def api_tpl_update(request):
    name, spec, body = await _body_tpl(request)
    if spec is None:
        return jerr(400, "json")
    tid = int(request.match_info["tid"])
    if not db.update_template(request["bid"], tid, name, spec):
        return jerr(404, "template")
    db.gc_images(request["bid"])
    db.log_event("tpl_save", request["uid"], request["bid"], tid=tid, role=request["role"])
    return web.json_response({"id": tid, "name": name, "spec": spec})


async def api_tpl_delete(request):
    if not db.delete_template(request["bid"], int(request.match_info["tid"])):
        return jerr(404, "template")
    db.gc_images(request["bid"])
    return web.json_response({"ok": True})


def image_loader(bid):
    cache = {}

    def load(asset):
        if asset not in cache:
            data = db.get_asset(bid, asset) if isinstance(asset, str) and asset.startswith("img_") else None
            cache[asset] = Image.open(io.BytesIO(data)).convert("RGBA") if data else None
        return cache[asset]
    return load


def brand_ctx(bid, fields=None, dark=0.0, focus=None):
    b = db.get_brand(bid)
    kit = b["kit"]
    logos = {}
    for k in ("logo", "logo_alt"):
        data = db.get_asset(bid, k)
        if data:
            logos[k] = Image.open(io.BytesIO(data)).convert("RGBA")
    customs = {slot: db.get_asset(bid, slot) for slot in (kit.get("custom_fonts") or {})}
    return R.Ctx(S.sanitize_palette(kit.get("palette")), logos, customs, fields or {}, dark,
                 image_loader(bid), focus)


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


def _int(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


async def api_preview(request):
    """Точный рендер движком бота — то, что клиент получит в итоге."""
    if not rate_ok(request, "preview"):
        return jerr(429, "rate")
    body = await json_body(request)
    if not isinstance(body, dict):
        return jerr(400, "json")
    spec = S.sanitize_spec(body.get("spec"))
    f = body.get("fields") if isinstance(body.get("fields"), dict) else {}
    fields = {k: str(f.get(k) or "")[:300] for k in ("title", "subtitle", "hashtag")}
    # i/n — какой слайд карусели показать: обложку (1) или следующий (2)
    n = _int(body.get("n"), 1, 20, 8)
    fields.update(i=_int(body.get("i"), 1, n, 1), n=n)
    fmt = body.get("fmt") if body.get("fmt") in R.FEED_SIZES else "4:5"
    surface = "story" if body.get("surface") == "story" else "feed"
    jpg = await heavy(_server_preview, request["bid"], spec, surface, fmt, fields)
    return web.Response(body=jpg, content_type="image/jpeg")


# ============ Импорт макетов ============
def _customs(bid):
    kit = db.get_brand(bid)["kit"]
    names = kit.get("custom_fonts") or {}
    return {slot: db.get_asset(bid, slot) for slot in names}, names


def _import_file_job(bid, data, filename):
    customs, names = _customs(bid)
    return I.build(I.parse_file(data, filename), customs, names)


def _import_figma_job(bid, plan, images):
    customs, names = _customs(bid)
    return I.build(I.figma_assemble(plan, images), customs, names)


def _save_import(bid, result, target, tid, name):
    layers, assets, report = result
    if not layers:
        raise I.ImportFail("empty")
    for aid, png in assets.items():
        db.set_asset(bid, aid, png)
    if target == "story" and tid:
        tpl = db.get_template(bid, tid)
        if not tpl:
            raise I.ImportFail("template")
        spec = tpl["spec"]
        spec["story"] = {"enabled": True, "layers": layers}
        spec = S.sanitize_spec(spec)
        db.update_template(bid, tid, tpl["name"], spec)
        out = {"id": tid, "name": tpl["name"], "spec": spec}
    else:
        spec = S.sanitize_spec({"feed": {"layers": layers}, "story": {"enabled": False, "layers": []}})
        name = (name or report.get("name") or "Импорт").strip()[:40] or "Импорт"
        new_id = db.create_template(bid, name, spec)
        if not new_id:
            raise I.ImportFail("limit")
        out = {"id": new_id, "name": name, "spec": spec}
    db.gc_images(bid)
    return {"template": out, "report": report}


def _import_error(e):
    status = 409 if e.code == "limit" else 422
    return web.json_response({"error": e.code}, status=status)


async def api_import(request):
    """Файл макета: PSD, AI, PDF или PNG. Поля: target=new|story, tid, file."""
    bid = request["bid"]
    if not rate_ok(request, "import"):
        return jerr(429, "rate")
    target, tid, data, filename = "new", None, None, ""
    check_length(request, IMPORT_MAX)
    try:
        reader = await request.multipart()
    except (AssertionError, ValueError):
        return jerr(400, "file")
    try:
        for _ in range(8):                   # target, tid, file — больше частей не бывает
            part = await reader.next()
            if part is None:
                break
            name = getattr(part, "name", None)
            if name == "target":
                txt = (await read_part(part, 64, "too_big")).decode("utf-8", "ignore")
                target = "story" if txt.strip() == "story" else "new"
            elif name == "tid":
                txt = (await read_part(part, 64, "too_big")).decode("utf-8", "ignore").strip()
                tid = int(txt) if txt.isdigit() else None
            elif name == "file":
                filename = part.filename or ""
                data = await read_part(part, IMPORT_MAX)   # bytes: pdfium не принимает bytearray
    except (AssertionError, ValueError):
        return jerr(400, "file")
    if not data:
        return jerr(400, "file")
    try:
        result = await heavy_import(_import_file_job, bid, data, filename)
        out = _save_import(bid, result, target, tid, None)
        db.log_event("tpl_create", request["uid"], bid, tid=out["template"]["id"], src="import:" + target)
        return web.json_response(out)
    except I.ImportFail as e:
        return _import_error(e)
    except Exception as e:
        logger.exception("import %s: %s", filename, e)
        return jerr(422, "import_failed")


async def _figma_get(session, url, token, **params):
    async with session.get(url, params=params, headers={"X-Figma-Token": token}) as r:
        if r.status in (401, 403):
            raise I.ImportFail("figma_token")
        if r.status == 404:
            raise I.ImportFail("figma_access")
        if r.status == 429:
            raise I.ImportFail("figma_rate")
        if r.status != 200:
            raise I.ImportFail("figma_http", str(r.status))
        return await r.json()


async def api_import_figma(request):
    """Фрейм Figma по ссылке. Токен используется для одного запроса и не сохраняется."""
    bid = request["bid"]
    if not rate_ok(request, "import"):
        return jerr(429, "rate")
    body = await json_body(request)
    if not isinstance(body, dict):
        return jerr(400, "json")
    token = str(body.get("token") or "").strip()
    target = "story" if body.get("target") == "story" else "new"
    tid = body.get("tid") if isinstance(body.get("tid"), int) else None
    if not token:
        return jerr(422, "figma_token")
    try:
        key, node = I.figma_ref(str(body.get("url") or ""))
        timeout = aiohttp.ClientTimeout(total=90)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            doc = await _figma_get(s, f"{FIGMA_API}/files/{key}/nodes", token, ids=node)
            frame = ((doc.get("nodes") or {}).get(node) or {}).get("document")
            if not frame:
                raise I.ImportFail("figma_access")
            plan = I.figma_plan(frame)
            ids = [r["id"] for r in plan["render"]]
            images = {}
            scale = f"{max(0.01, min(4.0, plan['k'])):.3f}"
            for i in range(0, len(ids), 40):
                res = await _figma_get(s, f"{FIGMA_API}/images/{key}", token,
                                       ids=",".join(ids[i:i + 40]), format="png", scale=scale)
                for nid, url in (res.get("images") or {}).items():
                    if url:
                        async with s.get(url) as r:
                            if r.status == 200:
                                images[nid] = await r.read()
        result = await heavy_import(_import_figma_job, bid, plan, images)
        out = _save_import(bid, result, target, tid, None)
        db.log_event("tpl_create", request["uid"], bid, tid=out["template"]["id"], src="figma:" + target)
        return web.json_response(out)
    except I.ImportFail as e:
        return _import_error(e)
    except asyncio.TimeoutError:
        return jerr(422, "figma_timeout")
    except Exception as e:
        logger.exception("figma import: %s", type(e).__name__)
        return jerr(422, "import_failed")


# ============ Вход с компьютера ============
def _client_ip(request):
    """Адрес человека для лимитов. Прокси Railway (и Caddy на своём сервере) дописывает
    адрес клиента в КОНЕЦ X-Forwarded-For; всё левее мог написать сам клиент — первому
    звену верить нельзя, иначе лимит входа обходится подменой заголовка."""
    hops = [h.strip() for v in request.headers.getall("X-Forwarded-For", []) for h in v.split(",")]
    hops = [h for h in hops if h]
    return hops[-1] if hops else (request.remote or "?")


async def auth_login(request):
    if not rate_ok(request, "login", who="ip:" + _client_ip(request)):
        return jerr(429, "rate")
    body = await json_body(request, LOGIN_MAX)      # больше 4 КБ — отказ до разбора
    if not isinstance(body, dict):
        return jerr(400, "json")
    res = db.redeem_login_link(str(body.get("k") or ""))
    if not res:
        return jerr(401, "link")
    sess, uid, bid = res
    return web.json_response({"session": sess, "b": bid})


async def auth_logout(request):
    db.drop_session(request.headers.get("X-Session", ""))
    return web.json_response({"ok": True})


def build_web(token: str) -> web.Application:
    app = web.Application(middlewares=[headers_mw, errors_mw, auth_mw], client_max_size=BODY_MAX)
    app["token"] = token
    app.router.add_get("/", index)
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/fonts/{name}", font_file)
    app.router.add_post("/auth/login", auth_login)
    app.router.add_post("/auth/logout", auth_logout)
    app.router.add_get("/api/state", api_state)
    app.router.add_put("/api/kit", api_kit)
    app.router.add_get("/api/asset/{kind}", api_asset)
    app.router.add_delete("/api/asset/{kind}", api_asset_delete)
    app.router.add_get("/api/font/{slot}", api_font)
    app.router.add_get("/api/logo/colors", api_logo_colors)
    app.router.add_post("/api/upload/{kind}", api_upload)
    app.router.add_post("/api/templates", api_tpl_create)
    app.router.add_put("/api/templates/{tid:\\d+}", api_tpl_update)
    app.router.add_delete("/api/templates/{tid:\\d+}", api_tpl_delete)
    app.router.add_post("/api/preview", api_preview)
    app.router.add_post("/api/import", api_import)
    app.router.add_post("/api/import/figma", api_import_figma)
    return app

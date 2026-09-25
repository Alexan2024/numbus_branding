"""NUMBUS Branding — Telegram-бот.

Один бот, много брендов. Клиент активирует инвайт-код, загружает логотип и
собирает собственный стиль в редакторе (Mini App, web.py + webapp.html).
Посты делаются в чате: шаблон → фото → текст → формат → хештег → готово.
"""
import io
import os
import re
import html
import signal
import asyncio
import logging
import warnings
from datetime import datetime

from aiohttp import web as aioweb
from telegram import (Update, InlineKeyboardButton as Btn, InlineKeyboardMarkup as KB,
                      InputMediaPhoto, WebAppInfo)
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application, BaseUpdateProcessor, CallbackQueryHandler, CommandHandler,
    ContextTypes, ConversationHandler, MessageHandler, filters,
)
from telegram.warnings import PTBUserWarning
from PIL import Image

import db
import render as R
import web
from texts import t as _t

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
logger = logging.getLogger("numbus")
warnings.filterwarnings("ignore", category=PTBUserWarning)

TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_IDS = {int(x) for x in re.split(r"[,\s]+", os.environ.get("ADMIN_IDS", "")) if x.strip().isdigit()}
SUPPORT = os.environ.get("SUPPORT_CONTACT", "администратору")
PORT = int(os.environ.get("PORT", "8080"))
_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
WEBAPP_URL = (os.environ.get("WEBAPP_URL") or (f"https://{_domain}" if _domain else "")).rstrip("/")
MAX_BATCH = 30
RENDER_SEM = asyncio.Semaphore(int(os.environ.get("RENDER_WORKERS", "2")))
HTML = ParseMode.HTML
esc = html.escape

(MENU, CODE, K_NAME, K_LOGO, P_TPL, P_PHOTOS, P_TITLE, P_SUBTITLE,
 P_FORMAT, P_TAG, P_CUSTOM_TAG, P_DARK) = range(12)

FORMATS = ["4:5", "3:4", "1:1", "3:2", "9:16", "orig"]
CODE_RE = r"(?i)^\s*NB-[A-Z0-9]{4}-[A-Z0-9]{4}\s*$"


# ============ Параллельно между юзерами, по очереди внутри юзера ============
class PerUserProcessor(BaseUpdateProcessor):
    def __init__(self, max_concurrent_updates=64):
        super().__init__(max_concurrent_updates)
        self._locks = {}

    async def do_process_update(self, update, coroutine):
        uid = update.effective_user.id if isinstance(update, Update) and update.effective_user else None
        if uid is None:
            await coroutine
            return
        async with self._locks.setdefault(uid, asyncio.Lock()):
            await coroutine

    async def initialize(self):
        pass

    async def shutdown(self):
        pass


# ============ Утилиты ============
def L(ctx, update) -> str:
    lang = ctx.user_data.get("lang")
    if not lang:
        u = update.effective_user
        lang = db.ensure_user(u.id, u.language_code).get("lang") or "ru"
        ctx.user_data["lang"] = lang
    return lang


def tx(ctx, key, **kw):
    return _t(ctx.user_data.get("lang", "ru"), key, **kw)


def reset_session(ctx):
    for k in ("post", "wiz", "kit_bid", "status_msg"):
        ctx.user_data.pop(k, None)


def fmt_date(iso) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m.%Y")
    except Exception:
        return "—"


def plan_limits(b):
    return db.PLANS.get(b["plan"], db.PLANS["pilot"])


def current_brand(uid):
    brands = db.user_brands(uid)
    if not brands:
        return None, brands
    u = db.get_user(uid) or {}
    b = next((x for x in brands if x["id"] == u.get("active_brand")), None) or brands[0]
    if b["id"] != u.get("active_brand"):
        db.set_active_brand(uid, b["id"])
    return b, brands


def editor_btn(ctx, bid):
    if not WEBAPP_URL:
        return None
    return Btn(tx(ctx, "b_editor"), web_app=WebAppInfo(url=f"{WEBAPP_URL}/?b={bid}"))


def safe_name(name: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", (name or "").strip())[:30].strip("_")
    return s or "numbus"


async def run(fn, *a):
    async with RENDER_SEM:
        return await asyncio.to_thread(fn, *a)


async def answer(update):
    if update.callback_query:
        try:
            await update.callback_query.answer()
        except TelegramError:
            pass


async def say(update, text, kb=None):
    return await update.effective_chat.send_message(text, parse_mode=HTML, reply_markup=kb,
                                                    disable_web_page_preview=True)


async def strip_kb(update):
    q = update.callback_query
    if q and q.message:
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except TelegramError:
            pass


async def edit_or_say(update, text, kb=None):
    q = update.callback_query
    if q and q.message and not q.message.photo:
        try:
            await q.edit_message_text(text, parse_mode=HTML, reply_markup=kb, disable_web_page_preview=True)
            return
        except BadRequest:
            pass
    await strip_kb(update)
    await say(update, text, kb)


async def put_photo(update, data: bytes, caption, kb):
    q = update.callback_query
    if q and q.message and q.message.photo:
        try:
            await q.edit_message_media(InputMediaPhoto(io.BytesIO(data), caption=caption, parse_mode=HTML),
                                       reply_markup=kb)
            return
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return
            logger.warning("edit_message_media: %s", e)
    await strip_kb(update)
    await update.effective_chat.send_photo(io.BytesIO(data), caption=caption, parse_mode=HTML, reply_markup=kb)


async def get_file_bytes(update, ctx):
    msg = update.message
    try:
        if msg.document:
            f = await msg.document.get_file()
            return bytes(await f.download_as_bytearray()), False
        if msg.photo:
            f = await msg.photo[-1].get_file()
            return bytes(await f.download_as_bytearray()), True
    except BadRequest as e:
        if "too big" in str(e).lower():
            await say(update, tx(ctx, "file_big"))
            return None, False
        raise
    return None, False


def is_image(data: bytes) -> bool:
    try:
        Image.open(io.BytesIO(data))
        return True
    except Exception:
        return False


# ============ Главное меню ============
def menu_text(ctx, b):
    lines = [tx(ctx, "menu_head", brand=esc(b["kit"].get("name") or "—"))]
    until = fmt_date(b["plan_until"])
    if db.plan_active(b):
        lines.append(tx(ctx, "menu_plan", plan=b["plan"].capitalize(), until=until,
                        used=db.photos_used(b["id"]), limit=plan_limits(b)["photos"]))
    else:
        lines.append(tx(ctx, "menu_expired", until=until, support=esc(SUPPORT)))
    if not db.has_asset(b["id"], "logo"):
        lines.append("\n" + tx(ctx, "menu_no_kit"))
    return "\n".join(lines)


def menu_kb(ctx, b, brands):
    rows = [[Btn(tx(ctx, "b_new"), callback_data="menu:new")]]
    if b["role"] == "owner":
        eb = editor_btn(ctx, b["id"])
        if eb and db.has_asset(b["id"], "logo"):
            rows.append([eb])
        elif not db.has_asset(b["id"], "logo"):
            rows.append([Btn(tx(ctx, "k_setup"), callback_data="menu:setup")])
        rows.append([Btn(tx(ctx, "b_team"), callback_data="menu:team")])
    row = []
    if len(brands) > 1:
        row.append(Btn(tx(ctx, "b_switch"), callback_data="menu:switch"))
    row.append(Btn(tx(ctx, "b_code"), callback_data="menu:code"))
    rows.append(row)
    rows.append([Btn(tx(ctx, "b_lang"), callback_data="menu:lang")])
    return KB(rows)


async def show_menu(update, ctx, edit=False):
    b, brands = current_brand(update.effective_user.id)
    if not b:
        await say(update, tx(ctx, "welcome_new"))
        return CODE
    text, kb = menu_text(ctx, b), menu_kb(ctx, b, brands)
    if edit:
        await edit_or_say(update, text, kb)
    else:
        await say(update, text, kb)
    return MENU


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    L(ctx, update)
    reset_session(ctx)
    args = ctx.args or []
    if args and args[0].startswith("j_"):
        return await do_join(update, ctx, args[0][2:])
    return await show_menu(update, ctx)


async def cmd_cancel(update, ctx):
    L(ctx, update)
    reset_session(ctx)
    await say(update, tx(ctx, "cancelled"))
    return await show_menu(update, ctx)


async def on_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    L(ctx, update)
    await answer(update)
    action = update.callback_query.data.split(":", 1)[1]
    uid = update.effective_user.id
    reset_session(ctx)

    if action == "home":
        return await show_menu(update, ctx, edit=True)
    if action == "lang":
        new = "en" if ctx.user_data.get("lang") == "ru" else "ru"
        db.set_lang(uid, new)
        ctx.user_data["lang"] = new
        return await show_menu(update, ctx, edit=True)
    if action == "code":
        await edit_or_say(update, tx(ctx, "ask_code"), KB([[Btn(tx(ctx, "b_menu"), callback_data="menu:home")]]))
        return CODE

    b, brands = current_brand(uid)
    if not b:
        return await show_menu(update, ctx)
    if action == "switch":
        rows = [[Btn(("✓ " if x["id"] == b["id"] else "") + (x["kit"].get("name") or f"#{x['id']}"),
                     callback_data=f"sw:{x['id']}")] for x in brands]
        rows.append([Btn(tx(ctx, "b_menu"), callback_data="menu:home")])
        await edit_or_say(update, tx(ctx, "switch_head"), KB(rows))
        return MENU
    if action == "setup":
        if b["role"] != "owner":
            await say(update, tx(ctx, "kit_owner_only"))
            return MENU
        ctx.user_data["kit_bid"] = b["id"]
        ctx.user_data["wiz"] = True
        return await ask_name(update, ctx)
    if action == "team":
        return await show_team(update, ctx, b, edit=True)
    if action == "new":
        return await start_post(update, ctx, b)
    return await show_menu(update, ctx, edit=True)


async def on_switch(update, ctx):
    L(ctx, update)
    await answer(update)
    bid = int(update.callback_query.data.split(":")[1])
    if db.member_role(bid, update.effective_user.id):
        db.set_active_brand(update.effective_user.id, bid)
    return await show_menu(update, ctx, edit=True)


# ============ Доступ ============
async def on_code(update, ctx):
    L(ctx, update)
    res = db.redeem_invite(update.message.text or "")
    if not res:
        await say(update, tx(ctx, "code_bad"))
        return CODE
    plan, days = res
    bid = db.create_brand(update.effective_user.id, plan, days)
    b = db.get_brand(bid)
    await say(update, tx(ctx, "code_ok", plan=plan.capitalize(), until=fmt_date(b["plan_until"])))
    ctx.user_data["kit_bid"] = bid
    ctx.user_data["wiz"] = True
    return await ask_name(update, ctx)


async def do_join(update, ctx, token):
    u = update.effective_user
    b = db.brand_by_token(token)
    if not b:
        await say(update, tx(ctx, "join_bad"))
        return await show_menu(update, ctx)
    name = esc(b["kit"].get("name") or "—")
    if db.member_role(b["id"], u.id):
        db.set_active_brand(u.id, b["id"])
        await say(update, tx(ctx, "join_ok", brand=name))
        return await show_menu(update, ctx)
    if db.member_count(b["id"]) >= plan_limits(b)["members"]:
        await say(update, tx(ctx, "join_full", brand=name))
        return await show_menu(update, ctx)
    db.add_member(b["id"], u.id, "editor")
    db.set_active_brand(u.id, b["id"])
    await say(update, tx(ctx, "join_ok", brand=name))
    owner = db.get_user(b["owner_id"]) or {}
    who = esc(u.full_name) + (f" (@{u.username})" if u.username else "")
    try:
        await ctx.bot.send_message(b["owner_id"], _t(owner.get("lang", "ru"), "join_notify", who=who, brand=name),
                                   parse_mode=HTML)
    except TelegramError:
        pass
    return await show_menu(update, ctx)


async def show_team(update, ctx, b, edit=False):
    if b["role"] != "owner":
        await say(update, tx(ctx, "team_owner_only"))
        return MENU
    link = f"https://t.me/{ctx.bot.username}?start=j_{b['join_token']}"
    text = tx(ctx, "team_head", brand=esc(b["kit"].get("name") or "—"), n=db.member_count(b["id"]),
              limit=plan_limits(b)["members"], link=link)
    kb = KB([[Btn(tx(ctx, "b_team_new"), callback_data="team:new")],
             [Btn(tx(ctx, "b_menu"), callback_data="menu:home")]])
    await (edit_or_say(update, text, kb) if edit else say(update, text, kb))
    return MENU


async def on_team(update, ctx):
    L(ctx, update)
    await answer(update)
    b, _ = current_brand(update.effective_user.id)
    if not b or b["role"] != "owner":
        return await show_menu(update, ctx, edit=True)
    db.regen_token(b["id"])
    b, _ = current_brand(update.effective_user.id)
    return await show_team(update, ctx, b, edit=True)


# ============ Онбординг: название → логотип → редактор ============
def step(ctx, n):
    return tx(ctx, "step", n=n) if ctx.user_data.get("wiz") else ""


def kit_bid(ctx):
    return ctx.user_data.get("kit_bid")


async def ask_name(update, ctx):
    await edit_or_say(update, step(ctx, 1) + tx(ctx, "ask_name"))
    return K_NAME


async def on_name(update, ctx):
    name = (update.message.text or "").strip()
    if not 1 <= len(name) <= 40:
        await say(update, tx(ctx, "name_bad"))
        return K_NAME
    db.update_kit(kit_bid(ctx), name=name)
    await say(update, step(ctx, 2) + tx(ctx, "ask_logo"))
    return K_LOGO


async def on_logo(update, ctx):
    data, as_photo = await get_file_bytes(update, ctx)
    if not data:
        return K_LOGO
    try:
        png, had_alpha = await run(R.prepare_logo, data)
    except ValueError:
        await say(update, tx(ctx, "logo_empty"))
        return K_LOGO
    except Exception as e:
        logger.info("logo open failed: %s", e)
        await say(update, tx(ctx, "logo_bad"))
        return K_LOGO
    bid = kit_bid(ctx)
    db.set_asset(bid, "logo", png)
    if as_photo:
        await say(update, tx(ctx, "logo_photo"))
    elif not had_alpha:
        await say(update, tx(ctx, "logo_bg"))
    eb = editor_btn(ctx, bid)
    await say(update, tx(ctx, "wiz_done") if eb else tx(ctx, "editor_off"), KB([[eb]]) if eb else None)
    reset_session(ctx)
    return await show_menu(update, ctx)


# ============ Создание поста ============
def post(ctx):
    return ctx.user_data.setdefault("post", {})


async def start_post(update, ctx, b):
    if not db.plan_active(b):
        await say(update, tx(ctx, "no_access", support=esc(SUPPORT)))
        return MENU
    if not db.has_asset(b["id"], "logo"):
        await say(update, tx(ctx, "no_logo"))
        return MENU
    ctx.user_data["post"] = {"bid": b["id"], "photos": []}
    return await ask_tpl(update, ctx)


async def ask_tpl(update, ctx):
    p = post(ctx)
    tpls = db.list_templates(p["bid"])
    if not tpls:
        b = db.get_brand(p["bid"])
        eb = editor_btn(ctx, b["id"])
        rows = ([[eb]] if eb else []) + [[Btn(tx(ctx, "b_menu"), callback_data="menu:home")]]
        await edit_or_say(update, tx(ctx, "no_tpl"), KB(rows))
        return MENU
    rows = [[Btn(tp["name"] + (tx(ctx, "tpl_story") if tp["spec"]["story"].get("enabled") else ""),
                 callback_data=f"tp:{tp['id']}")] for tp in tpls]
    rows.append([Btn(tx(ctx, "b_menu"), callback_data="menu:home")])
    await edit_or_say(update, tx(ctx, "ask_tpl"), KB(rows))
    return P_TPL


async def on_tpl(update, ctx):
    await answer(update)
    p = post(ctx)
    tp = db.get_template(p["bid"], int(update.callback_query.data.split(":")[1]))
    if not tp:
        return await ask_tpl(update, ctx)
    p.update(tid=tp["id"], spec=tp["spec"], tname=tp["name"], fields=R.spec_fields(tp["spec"]))
    return await ask_photos(update, ctx)


async def ask_photos(update, ctx):
    n = len(post(ctx).get("photos", []))
    text = tx(ctx, "ask_photos") + (f"\n\n{tx(ctx, 'photos_n', n=n)}" if n else "")
    rows = []
    if n:
        rows.append([Btn(tx(ctx, "b_done_ph"), callback_data="p:done")])
    rows.append([Btn(tx(ctx, "b_back"), callback_data="p:back")])
    await edit_or_say(update, text, KB(rows))
    return P_PHOTOS


async def on_photo(update, ctx):
    p = post(ctx)
    photos = p.setdefault("photos", [])
    if len(photos) >= MAX_BATCH:
        if not p.get("warned_max"):
            p["warned_max"] = True
            await say(update, tx(ctx, "photos_max", n=MAX_BATCH))
        return P_PHOTOS
    data, _ = await get_file_bytes(update, ctx)
    if not data:
        return P_PHOTOS
    if not is_image(data):
        await say(update, tx(ctx, "photo_bad"))
        return P_PHOTOS
    photos.append(data)
    prev = ctx.user_data.pop("status_msg", None)
    m = await say(update, tx(ctx, "photos_n", n=len(photos)),
                  KB([[Btn(tx(ctx, "b_done_ph"), callback_data="p:done")]]))
    ctx.user_data["status_msg"] = m.message_id
    if prev:
        try:
            await ctx.bot.delete_message(update.effective_chat.id, prev)
        except TelegramError:
            pass
    return P_PHOTOS


async def on_photos_done(update, ctx):
    p = post(ctx)
    photos = p.get("photos", [])
    if not photos:
        if update.callback_query:
            await update.callback_query.answer(tx(ctx, "photos_none"), show_alert=True)
        else:
            await say(update, tx(ctx, "photos_none"))
        return P_PHOTOS
    await answer(update)
    b = db.get_brand(p["bid"])
    left = plan_limits(b)["photos"] - db.photos_used(b["id"])
    if len(photos) > left:
        await say(update, tx(ctx, "limit_hit", left=max(0, left), n=len(photos), support=esc(SUPPORT)))
        return P_PHOTOS
    ctx.user_data.pop("status_msg", None)
    await strip_kb(update)
    return await next_field(update, ctx, after=P_PHOTOS)


async def next_field(update, ctx, after):
    """Шаги после фото зависят от шаблона: какие поля он использует."""
    p = post(ctx)
    order = [(P_TITLE, "title"), (P_SUBTITLE, "subtitle")]
    for state, field in order:
        if state > after and field in p["fields"]:
            if state == P_TITLE:
                await say(update, tx(ctx, "ask_title"), KB([[Btn(tx(ctx, "b_back"), callback_data="p:back")]]))
            else:
                await say(update, tx(ctx, "ask_subtitle"),
                          KB([[Btn(tx(ctx, "b_skip_field"), callback_data="p:skipsub")],
                              [Btn(tx(ctx, "b_back"), callback_data="p:back")]]))
            return state
    return await ask_format(update, ctx)


async def on_title(update, ctx):
    title = (update.message.text or "").strip("\n")
    if not title.strip():
        await say(update, tx(ctx, "title_bad"))
        return P_TITLE
    post(ctx)["title"] = title[:300]
    return await next_field(update, ctx, after=P_TITLE)


async def on_subtitle(update, ctx):
    post(ctx)["subtitle"] = (update.message.text or "").strip()[:300]
    return await ask_format(update, ctx)


async def on_skip_subtitle(update, ctx):
    await answer(update)
    await strip_kb(update)
    post(ctx)["subtitle"] = ""
    return await ask_format(update, ctx)


async def ask_format(update, ctx):
    p = post(ctx)
    keys = [k for k in FORMATS if not (k == "9:16" and p["spec"]["story"].get("enabled"))]
    main = [k for k in keys if k != "orig"]
    rows = [[Btn(k, callback_data=f"fmt:{k}") for k in main[i:i + 3]] for i in range(0, len(main), 3)]
    rows.append([Btn(tx(ctx, "fmt_orig"), callback_data="fmt:orig")])
    rows.append([Btn(tx(ctx, "b_back"), callback_data="p:back")])
    await edit_or_say(update, tx(ctx, "ask_format", n=len(p["photos"])), KB(rows))
    return P_FORMAT


async def on_format(update, ctx):
    await answer(update)
    p = post(ctx)
    p["fmt"] = update.callback_query.data.split(":", 1)[1]
    if "hashtag" in p["fields"]:
        return await ask_tag(update, ctx)
    await strip_kb(update)
    return await proceed(update, ctx, "")


def tag_kb(ctx):
    tags = db.get_brand(post(ctx)["bid"])["kit"].get("hashtags") or []
    rows, row = [], []
    for i, tag in enumerate(tags):
        row.append(Btn(tag, callback_data=f"tag:i:{i}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([Btn(tx(ctx, "tag_none"), callback_data="tag:none")])
    rows.append([Btn(tx(ctx, "tag_custom"), callback_data="tag:custom")])
    rows.append([Btn(tx(ctx, "b_back"), callback_data="p:back")])
    return KB(rows)


async def ask_tag(update, ctx):
    await edit_or_say(update, tx(ctx, "ask_tag"), tag_kb(ctx))
    return P_TAG


async def on_tag(update, ctx):
    await answer(update)
    parts = update.callback_query.data.split(":")
    if parts[1] == "custom":
        await edit_or_say(update, tx(ctx, "ask_custom_tag"), KB([[Btn(tx(ctx, "b_back"), callback_data="p:back")]]))
        return P_CUSTOM_TAG
    tag = ""
    if parts[1] == "i":
        tags = db.get_brand(post(ctx)["bid"])["kit"].get("hashtags") or []
        idx = int(parts[2])
        tag = tags[idx] if idx < len(tags) else ""
    await strip_kb(update)
    return await proceed(update, ctx, tag)


async def on_custom_tag(update, ctx):
    words = (update.message.text or "").split()
    token = words[0].lstrip("#").strip() if words else ""
    if not token:
        await say(update, tx(ctx, "custom_tag_bad"))
        return P_CUSTOM_TAG
    return await proceed(update, ctx, "#" + token[:30])


def back_for(state):
    async def handler(update, ctx):
        await answer(update)
        p = post(ctx)
        if state == P_TPL or not p.get("tid"):
            return await show_menu(update, ctx, edit=True)
        if state == P_PHOTOS:
            return await ask_tpl(update, ctx)
        if state == P_TITLE:
            await strip_kb(update)
            return await ask_photos(update, ctx)
        if state == P_SUBTITLE:
            await strip_kb(update)
            if "title" in p["fields"]:
                return await next_field(update, ctx, after=P_PHOTOS)
            return await ask_photos(update, ctx)
        if state == P_FORMAT:
            await strip_kb(update)
            if "subtitle" in p["fields"]:
                return await next_field(update, ctx, after=P_TITLE)
            if "title" in p["fields"]:
                return await next_field(update, ctx, after=P_PHOTOS)
            return await ask_photos(update, ctx)
        if state == P_TAG:
            return await ask_format(update, ctx)
        if state == P_CUSTOM_TAG:
            return await ask_tag(update, ctx)
        if state == P_DARK:
            try:
                await update.callback_query.message.delete()
            except TelegramError:
                pass
            if "hashtag" in p["fields"]:
                await say(update, tx(ctx, "ask_tag"), tag_kb(ctx))
                return P_TAG
            return await ask_format(update, ctx)
        return await show_menu(update, ctx, edit=True)
    return handler


def _ctx_for(base, p, i, n, dark):
    return R.Ctx(base.palette, base.logos, base.customs,
                 dict(title=p.get("title", ""), subtitle=p.get("subtitle", ""), hashtag=p.get("tag", ""), i=i, n=n),
                 dark)


def _render_job(data, spec, fmt, ctx):
    return [(suf, R.to_jpeg(im)) for suf, im in R.render_template(R.open_photo(data), spec, fmt, ctx)]


def _preview_job(data, spec, fmt, ctx):
    photo = R.open_photo(data)
    W, H = R.feed_size(photo, fmt)
    return R.to_preview(R.render_surface(photo, W, H, spec["feed"]["layers"], ctx))


async def proceed(update, ctx, tag):
    p = post(ctx)
    p["tag"] = tag
    p["base"] = await run(web.brand_ctx, p["bid"])
    if R.spec_has_shade(p["spec"]):
        p["dark"] = R.DARK_DEFAULT_IDX
        return await show_dark(update, ctx, fresh=True)
    return await render_batch(update, ctx, 0.0)


def dark_meter(idx):
    return "●" * (idx + 1) + "○" * (len(R.DARK_STEPS) - idx - 1)


def dark_kb(ctx, idx):
    last = len(R.DARK_STEPS) - 1
    return KB([
        [Btn(tx(ctx, "b_lighter") if idx > 0 else "· · ·", callback_data="dk:down" if idx > 0 else "dk:noop"),
         Btn(tx(ctx, "b_darker") if idx < last else "· · ·", callback_data="dk:up" if idx < last else "dk:noop")],
        [Btn(tx(ctx, "b_render"), callback_data="dk:ok")],
        [Btn(tx(ctx, "b_back"), callback_data="p:back")],
    ])


async def show_dark(update, ctx, fresh=False):
    p = post(ctx)
    idx = p["dark"]
    c = _ctx_for(p["base"], p, 1, len(p["photos"]), R.DARK_STEPS[idx])
    data = await run(_preview_job, p["photos"][0], p["spec"], p["fmt"], c)
    caption = tx(ctx, "dark_cap", meter=dark_meter(idx))
    if fresh:
        await update.effective_chat.send_photo(io.BytesIO(data), caption=caption, parse_mode=HTML,
                                               reply_markup=dark_kb(ctx, idx))
    else:
        await put_photo(update, data, caption, dark_kb(ctx, idx))
    return P_DARK


async def on_dark(update, ctx):
    q = update.callback_query
    action = q.data.split(":")[1]
    p = post(ctx)
    if action == "noop":
        await answer(update)
        return P_DARK
    if action in ("up", "down"):
        new = max(0, min(len(R.DARK_STEPS) - 1, p["dark"] + (1 if action == "up" else -1)))
        if new == p["dark"]:
            await q.answer(tx(ctx, "edge"))
            return P_DARK
        await answer(update)
        p["dark"] = new
        return await show_dark(update, ctx)
    await answer(update)
    await strip_kb(update)
    return await render_batch(update, ctx, R.DARK_STEPS[p["dark"]])


async def render_batch(update, ctx, dark):
    p = post(ctx)
    photos, bid, n = p["photos"], p["bid"], len(p["photos"])
    await say(update, tx(ctx, "working", n=n))
    base = safe_name(db.get_brand(bid)["kit"].get("name")) + "_" + safe_name(p.get("tname"))
    ok = 0
    for i, data in enumerate(photos, 1):
        try:
            outs = await run(_render_job, data, p["spec"], p["fmt"], _ctx_for(p["base"], p, i, n, dark))
            for suf, blob in outs:
                name = f"{base}_{i}.jpg" if suf == "feed" else f"{base}_{i}_story.jpg"
                await update.effective_chat.send_document(io.BytesIO(blob), filename=name)
            ok += 1
        except Exception as e:
            logger.exception("photo %s: %s", i, e)
            await say(update, tx(ctx, "photo_err", i=i))
    db.record_event(bid, update.effective_user.id, "tpl", ok)
    await say(update, tx(ctx, "done", ok=ok, n=n))
    reset_session(ctx)
    return await show_menu(update, ctx)


# ============ Админ ============
def is_admin(update):
    return update.effective_user and update.effective_user.id in ADMIN_IDS


async def cmd_myid(update, ctx):
    if ADMIN_IDS and not is_admin(update):
        return
    await update.message.reply_text(f"Telegram ID: <code>{update.effective_user.id}</code>", parse_mode=HTML)


async def cmd_newcode(update, ctx):
    """/newcode [тариф=pilot] [дней=30] [использований=1]"""
    if not is_admin(update):
        return
    a = ctx.args or []
    plan = a[0].lower() if a else "pilot"
    if plan not in db.PLANS:
        await update.message.reply_text("Тарифы: " + ", ".join(db.PLANS))
        return
    try:
        days = int(a[1]) if len(a) > 1 else 30
        uses = int(a[2]) if len(a) > 2 else 1
    except ValueError:
        await update.message.reply_text("Формат: /newcode pilot 30 1")
        return
    code = db.create_invite(plan, days, uses)
    await update.message.reply_text(
        f"<code>{code}</code> — {plan}, {days} дн., использований: {uses}\n\n"
        f"Для клиента:\nВаш код доступа к NUMBUS Branding: <code>{code}</code>\n"
        f"Откройте @{ctx.bot.username} и отправьте код.", parse_mode=HTML)


async def cmd_codes(update, ctx):
    if not is_admin(update):
        return
    rows = db.list_invites()
    if not rows:
        await update.message.reply_text("Кодов пока нет. /newcode")
        return
    lines = [f"<code>{r['code']}</code> · {r['plan']} · {r['days']}д · {r['uses']}/{r['max_uses']}" for r in rows]
    await update.message.reply_text("\n".join(lines), parse_mode=HTML)


async def cmd_brands(update, ctx):
    if not is_admin(update):
        return
    rows = db.list_brands()
    if not rows:
        await update.message.reply_text("Брендов пока нет.")
        return
    lines = []
    for b in rows:
        state = "✅" if db.plan_active(b) else "⛔"
        lines.append(f"{state} <b>#{b['id']}</b> {esc(b['kit'].get('name') or '—')} · {b['plan']} до "
                     f"{fmt_date(b['plan_until'])} · 👥{db.member_count(b['id'])} · "
                     f"🧩{db.template_count(b['id'])} · 📷{db.photos_used(b['id'])}/{plan_limits(b)['photos']}")
    storage = "постоянное" if db.STORAGE_PERSISTENT else "⚠️ ВРЕМЕННОЕ — подключи Volume"
    editor = WEBAPP_URL or "⚠️ не задан WEBAPP_URL"
    await update.message.reply_text("\n".join(lines) + f"\n\nХранилище: {storage}\nРедактор: {editor}",
                                    parse_mode=HTML, disable_web_page_preview=True)


async def cmd_extend(update, ctx):
    """/extend <id бренда> <дней> [тариф]"""
    if not is_admin(update):
        return
    a = ctx.args or []
    try:
        bid, days = int(a[0].lstrip("#")), int(a[1])
    except (IndexError, ValueError):
        await update.message.reply_text("Формат: /extend 3 30 [media]")
        return
    plan = a[2].lower() if len(a) > 2 else None
    if plan and plan not in db.PLANS:
        await update.message.reply_text("Тарифы: " + ", ".join(db.PLANS))
        return
    if not db.extend_brand(bid, days, plan):
        await update.message.reply_text("Бренд не найден.")
        return
    b = db.get_brand(bid)
    await update.message.reply_text(f"#{bid}: {b['plan']} до {fmt_date(b['plan_until'])}")


async def on_stale(update, ctx):
    L(ctx, update)
    try:
        await update.callback_query.answer(tx(ctx, "stale"), show_alert=True)
    except TelegramError:
        pass


async def on_orphan(update, ctx):
    L(ctx, update)
    await say(update, tx(ctx, "stale"))


async def on_error(update, ctx):
    logger.error("Ошибка при обработке апдейта", exc_info=ctx.error)


# ============ Сборка ============
def build_app(token=None):
    app = (Application.builder().token(token or TOKEN)
           .concurrent_updates(PerUserProcessor(64))
           .read_timeout(120).write_timeout(120).connect_timeout(30)
           .build())
    IMG = filters.PHOTO | filters.Document.ALL
    TXT = filters.TEXT & ~filters.COMMAND
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            CommandHandler("menu", cmd_start),
            CallbackQueryHandler(on_menu, pattern="^menu:"),
            MessageHandler(filters.Regex(CODE_RE), on_code),
        ],
        states={
            MENU: [CallbackQueryHandler(on_switch, pattern=r"^sw:\d+$"),
                   CallbackQueryHandler(on_team, pattern="^team:new$")],
            CODE: [MessageHandler(TXT, on_code)],
            K_NAME: [MessageHandler(TXT, on_name)],
            K_LOGO: [MessageHandler(IMG, on_logo)],
            P_TPL: [CallbackQueryHandler(on_tpl, pattern=r"^tp:\d+$"),
                    CallbackQueryHandler(back_for(P_TPL), pattern="^p:back$")],
            P_PHOTOS: [MessageHandler(IMG, on_photo),
                       CommandHandler("done", on_photos_done),
                       CallbackQueryHandler(on_photos_done, pattern="^p:done$"),
                       CallbackQueryHandler(back_for(P_PHOTOS), pattern="^p:back$")],
            P_TITLE: [MessageHandler(TXT, on_title),
                      CallbackQueryHandler(back_for(P_TITLE), pattern="^p:back$")],
            P_SUBTITLE: [MessageHandler(TXT, on_subtitle),
                         CallbackQueryHandler(on_skip_subtitle, pattern="^p:skipsub$"),
                         CallbackQueryHandler(back_for(P_SUBTITLE), pattern="^p:back$")],
            P_FORMAT: [CallbackQueryHandler(on_format, pattern="^fmt:"),
                       CallbackQueryHandler(back_for(P_FORMAT), pattern="^p:back$")],
            P_TAG: [CallbackQueryHandler(on_tag, pattern="^tag:"),
                    CallbackQueryHandler(back_for(P_TAG), pattern="^p:back$")],
            P_CUSTOM_TAG: [MessageHandler(TXT, on_custom_tag),
                           CallbackQueryHandler(back_for(P_CUSTOM_TAG), pattern="^p:back$")],
            P_DARK: [CallbackQueryHandler(on_dark, pattern="^dk:"),
                     CallbackQueryHandler(back_for(P_DARK), pattern="^p:back$")],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel), CommandHandler("start", cmd_start)],
        allow_reentry=True,
    )
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("newcode", cmd_newcode))
    app.add_handler(CommandHandler("codes", cmd_codes))
    app.add_handler(CommandHandler("brands", cmd_brands))
    app.add_handler(CommandHandler("extend", cmd_extend))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(on_stale))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, on_orphan))
    app.add_error_handler(on_error)
    return app


async def amain():
    db.init_db()
    if not ADMIN_IDS:
        logger.warning("ADMIN_IDS не задан — админ-команды недоступны. Узнай свой ID через /myid.")
    if not WEBAPP_URL:
        logger.warning("WEBAPP_URL не задан и RAILWAY_PUBLIC_DOMAIN нет — кнопка редактора скрыта.")
    runner = aioweb.AppRunner(web.build_web(TOKEN))
    await runner.setup()
    await aioweb.TCPSite(runner, "0.0.0.0", PORT).start()
    logger.info("Редактор: порт %s, адрес %s", PORT, WEBAPP_URL or "—")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    app = build_app()
    async with app:
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        await stop.wait()
        await app.updater.stop()
        await app.stop()
    await runner.cleanup()


def main():
    if not TOKEN:
        raise SystemExit("BOT_TOKEN не задан")
    asyncio.run(amain())


if __name__ == "__main__":
    main()

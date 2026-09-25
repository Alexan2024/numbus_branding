"""NUMBUS Branding — Telegram-бот.

Один бот, много брендов. Клиент активирует инвайт-код, собирает бренд-кит
(логотип, расположение, цвет, шрифт, хештеги) и дальше делает посты сам.
"""
import io
import os
import re
import html
import asyncio
import logging
import warnings
from datetime import datetime

from telegram import Update, InlineKeyboardButton as Btn, InlineKeyboardMarkup as KB, InputMediaPhoto
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    Application, BaseUpdateProcessor, CallbackQueryHandler, CommandHandler,
    ContextTypes, ConversationHandler, MessageHandler, filters,
)
from telegram.warnings import PTBUserWarning
from PIL import Image

import db
import render as R
from texts import t as _t

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("numbus")
warnings.filterwarnings("ignore", category=PTBUserWarning)

TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_IDS = {int(x) for x in re.split(r"[,\s]+", os.environ.get("ADMIN_IDS", "")) if x.strip().isdigit()}
SUPPORT = os.environ.get("SUPPORT_CONTACT", "администратору")
MAX_BATCH = 30
RENDER_SEM = asyncio.Semaphore(int(os.environ.get("RENDER_WORKERS", "2")))
HTML = ParseMode.HTML
esc = html.escape

(MENU, CODE, K_NAME, K_LOGO, K_SAMPLE, K_LAYOUT, K_FONT, K_FONT_UP, K_TAGS, KIT, K_COVER,
 P_TPL, P_PHOTOS, P_TITLE, P_FORMAT, P_TAG, P_CUSTOM_TAG, P_DARK) = range(18)

FEED_FORMATS = ["4:5", "3:4", "1:1", "9:16", "3:2", "orig"]
COVER_FEED_FORMATS = ["4:5", "3:4", "1:1", "3:2", "orig"]
CODE_RE = r"(?i)^\s*NB-[A-Z0-9]{4}-[A-Z0-9]{4}\s*$"
POS_ICONS = {"tl": "↖️", "tr": "↗️", "bl": "↙️", "br": "↘️"}


# ============ Обработка апдейтов: параллельно между юзерами, по очереди внутри юзера ============
class PerUserProcessor(BaseUpdateProcessor):
    """Пока один клиент рендерит пачку, остальные не ждут. А апдейты одного
    пользователя идут строго по порядку — диалог не «разъезжается»."""

    def __init__(self, max_concurrent_updates=64):
        super().__init__(max_concurrent_updates)
        self._locks = {}

    async def do_process_update(self, update, coroutine):
        uid = update.effective_user.id if isinstance(update, Update) and update.effective_user else None
        if uid is None:
            await coroutine
            return
        lock = self._locks.setdefault(uid, asyncio.Lock())
        async with lock:
            await coroutine

    async def initialize(self):
        pass

    async def shutdown(self):
        pass


# ============ Утилиты ============
def L(ctx, update) -> str:
    """Язык пользователя (кэш в user_data, источник — БД)."""
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
    """Активный бренд пользователя (+ список всех его брендов)."""
    brands = db.user_brands(uid)
    if not brands:
        return None, brands
    u = db.get_user(uid) or {}
    b = next((x for x in brands if x["id"] == u.get("active_brand")), None) or brands[0]
    if b["id"] != u.get("active_brand"):
        db.set_active_brand(uid, b["id"])
    return b, brands


def load_brand_obj(bid) -> R.Brand:
    b = db.get_brand(bid)
    kit = b["kit"]
    font = db.get_asset(bid, "font") if kit.get("font") == "custom" else None
    return R.Brand(kit, db.get_asset(bid, "logo"), db.get_asset(bid, "logo_cover"), font)


_SAMPLE = None


def sample_for(bid) -> Image.Image:
    global _SAMPLE
    data = db.get_asset(bid, "sample")
    if data:
        return R.open_photo(data)
    if _SAMPLE is None:
        _SAMPLE = R.sample_image()
    return _SAMPLE


def safe_name(name: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", (name or "").strip())[:30].strip("_")
    return s or "numbus"


async def run(fn, *a):
    """Тяжёлый рендер — в отдельном потоке, не больше RENDER_WORKERS одновременно."""
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


async def strip_kb(update):
    q = update.callback_query
    if q and q.message:
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except TelegramError:
            pass


async def put_photo(update, data: bytes, caption, kb):
    """Показ превью: редактируем текущее фото-сообщение, иначе шлём новое."""
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
    """(bytes, sent_as_photo) из фото или документа. None + сообщение, если не вышло."""
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
        rows.append([Btn(tx(ctx, "b_kit"), callback_data="menu:kit"),
                     Btn(tx(ctx, "b_team"), callback_data="menu:team")])
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
    if action not in ("new",):
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
    if action == "kit":
        if b["role"] != "owner":
            await say(update, tx(ctx, "kit_owner_only"))
            return MENU
        ctx.user_data["kit_bid"] = b["id"]
        ctx.user_data["wiz"] = not db.has_asset(b["id"], "logo")  # кит не собран — ведём мастером
        if ctx.user_data["wiz"]:
            return await ask_name(update, ctx)
        return await show_kit(update, ctx, edit=True)
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


# ============ Доступ: инвайт-коды и вступление в команду ============
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
    if edit:
        await edit_or_say(update, text, kb)
    else:
        await say(update, text, kb)
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


# ============ Бренд-кит: мастер и редактор ============
def wiz(ctx):
    return bool(ctx.user_data.get("wiz"))


def step(ctx, n):
    return tx(ctx, "step", n=n) if wiz(ctx) else ""


def edit_back_row(ctx):
    return [] if wiz(ctx) else [[Btn(tx(ctx, "b_back"), callback_data="kit:back")]]


def kit_bid(ctx):
    return ctx.user_data.get("kit_bid")


def kit(ctx):
    return db.get_brand(kit_bid(ctx))["kit"]


async def ask_name(update, ctx):
    await edit_or_say(update, step(ctx, 1) + tx(ctx, "ask_name"), KB(edit_back_row(ctx)) if not wiz(ctx) else None)
    return K_NAME


async def on_name(update, ctx):
    name = (update.message.text or "").strip()
    if not 1 <= len(name) <= 40:
        await say(update, tx(ctx, "name_bad"))
        return K_NAME
    db.update_kit(kit_bid(ctx), name=name)
    if wiz(ctx):
        return await ask_logo(update, ctx)
    return await show_kit(update, ctx)


async def ask_logo(update, ctx):
    await edit_or_say(update, step(ctx, 2) + tx(ctx, "ask_logo"), KB(edit_back_row(ctx)) if not wiz(ctx) else None)
    return K_LOGO


async def _receive_logo(update, ctx, kind):
    """Общий приём логотипа (основного или для обложек). True, если сохранён."""
    data, as_photo = await get_file_bytes(update, ctx)
    if not data:
        return False
    try:
        png, had_alpha = await run(R.prepare_logo, data)
    except ValueError:
        await say(update, tx(ctx, "logo_empty"))
        return False
    except Exception as e:
        logger.info("logo open failed: %s", e)
        await say(update, tx(ctx, "logo_bad"))
        return False
    db.set_asset(kit_bid(ctx), kind, png)
    if as_photo:
        await say(update, tx(ctx, "logo_photo"))
    elif not had_alpha:
        await say(update, tx(ctx, "logo_bg"))
    return True


async def on_logo(update, ctx):
    if not await _receive_logo(update, ctx, "logo"):
        return K_LOGO
    if wiz(ctx):
        return await ask_sample(update, ctx)
    return await show_layout(update, ctx)


async def ask_sample(update, ctx):
    rows = [[Btn(tx(ctx, "b_skip"), callback_data="smp:skip")]] + edit_back_row(ctx)
    await edit_or_say(update, step(ctx, 3) + tx(ctx, "ask_sample"), KB(rows))
    return K_SAMPLE


def _prepare_sample(data):
    img = R.open_photo(data)
    img.thumbnail((1600, 1600), Image.LANCZOS)
    return R.to_jpeg(img, 88)


async def on_sample(update, ctx):
    data, _ = await get_file_bytes(update, ctx)
    if not data:
        return K_SAMPLE
    try:
        jpg = await run(_prepare_sample, data)
    except Exception:
        await say(update, tx(ctx, "photo_bad"))
        return K_SAMPLE
    db.set_asset(kit_bid(ctx), "sample", jpg)
    return await show_layout(update, ctx)


async def on_sample_skip(update, ctx):
    await answer(update)
    if wiz(ctx):
        return await show_layout(update, ctx)
    return await show_kit(update, ctx, edit=True)


# ---- Расположение и цвет (живое превью) ----
def _layout_preview(bid, tag):
    brand = load_brand_obj(bid)
    return R.to_preview(R.render_branding(sample_for(bid), "4:5", tag, brand))


def preview_tag(k):
    return (k.get("hashtags") or ["#hashtag"])[0]


def layout_kb(ctx, k):
    def mark(ok, label):
        return ("✓ " if ok else "") + label

    pos = k.get("pos", "bl")
    size = k.get("size", "m")
    col = k.get("color", "adaptive")
    rows = [
        [Btn(mark(pos == p, POS_ICONS[p]), callback_data=f"lay:pos:{p}") for p in ("tl", "tr")],
        [Btn(mark(pos == p, POS_ICONS[p]), callback_data=f"lay:pos:{p}") for p in ("bl", "br")],
        [Btn(mark(size == s, s.upper()), callback_data=f"lay:size:{s}") for s in ("s", "m", "l")],
        [Btn(mark(col == c, tx(ctx, f"c_{c}")), callback_data=f"lay:col:{c}") for c in ("adaptive", "white")],
        [Btn(mark(col == c, tx(ctx, f"c_{c}")), callback_data=f"lay:col:{c}") for c in ("black", "original")],
        [Btn(tx(ctx, "b_next") if wiz(ctx) else tx(ctx, "b_save"), callback_data="lay:ok")],
    ]
    return KB(rows)


async def show_layout(update, ctx):
    k = kit(ctx)
    data = await run(_layout_preview, kit_bid(ctx), preview_tag(k))
    await put_photo(update, data, step(ctx, 4) + tx(ctx, "layout_cap"), layout_kb(ctx, k))
    return K_LAYOUT


async def on_layout(update, ctx):
    await answer(update)
    parts = update.callback_query.data.split(":")
    if parts[1] == "ok":
        await strip_kb(update)
        if wiz(ctx):
            return await show_font(update, ctx, fresh=True)
        return await show_kit(update, ctx)
    field = {"pos": "pos", "size": "size", "col": "color"}[parts[1]]
    if kit(ctx).get(field) == parts[2]:
        return K_LAYOUT
    db.update_kit(kit_bid(ctx), **{field: parts[2]})
    return await show_layout(update, ctx)


# ---- Шрифт (превью обложки) ----
def _font_preview(bid, title, tag):
    brand = load_brand_obj(bid)
    return R.to_preview(R.render_cover_feed(sample_for(bid), "4:5", title, tag, brand))


def font_kb(ctx, k):
    cur = k.get("font")
    keys = list(R.FONTS.keys())
    rows = []
    for i in range(0, len(keys), 2):
        rows.append([Btn(("✓ " if cur == key else "") + R.FONTS[key]["label"], callback_data=f"fnt:{key}")
                     for key in keys[i:i + 2]])
    up = tx(ctx, "b_font_up")
    if cur == "custom":
        up = "✓ " + up
    rows.append([Btn(up, callback_data="fnt:up")])
    rows.append([Btn(tx(ctx, "b_next") if wiz(ctx) else tx(ctx, "b_save"), callback_data="fnt:ok")])
    return KB(rows)


async def show_font(update, ctx, fresh=False):
    k = kit(ctx)
    data = await run(_font_preview, kit_bid(ctx), tx(ctx, "preview_title"), preview_tag(k))
    caption = step(ctx, 5) + tx(ctx, "font_cap")
    if fresh:
        await update.effective_chat.send_photo(io.BytesIO(data), caption=caption, parse_mode=HTML,
                                               reply_markup=font_kb(ctx, k))
    else:
        await put_photo(update, data, caption, font_kb(ctx, k))
    return K_FONT


async def on_font(update, ctx):
    await answer(update)
    key = update.callback_query.data.split(":", 1)[1]
    if key == "ok":
        await strip_kb(update)
        if wiz(ctx):
            return await ask_tags(update, ctx)
        return await show_kit(update, ctx)
    if key == "up":
        await strip_kb(update)
        await say(update, tx(ctx, "ask_font"), KB([[Btn(tx(ctx, "b_back"), callback_data="fnt:back")]]))
        return K_FONT_UP
    if key == "back":
        await strip_kb(update)
        return await show_font(update, ctx, fresh=True)
    if key in R.FONTS and kit(ctx).get("font") != key:
        db.update_kit(kit_bid(ctx), font=key)
        return await show_font(update, ctx)
    return K_FONT


async def on_font_file(update, ctx):
    doc = update.message.document
    name = (doc.file_name or "").lower() if doc else ""
    if not doc or not name.endswith((".ttf", ".otf")):
        await say(update, tx(ctx, "font_bad"))
        return K_FONT_UP
    data, _ = await get_file_bytes(update, ctx)
    if not data or not R.validate_font(data):
        await say(update, tx(ctx, "font_bad"))
        return K_FONT_UP
    db.set_asset(kit_bid(ctx), "font", data)
    db.update_kit(kit_bid(ctx), font="custom")
    return await show_font(update, ctx, fresh=True)


# ---- Хештеги ----
async def ask_tags(update, ctx):
    rows = [[Btn(tx(ctx, "b_skip"), callback_data="tags:skip")]] + edit_back_row(ctx)
    await say(update, step(ctx, 6) + tx(ctx, "ask_tags"), KB(rows))
    return K_TAGS


def parse_tags(text):
    seen, out = set(), []
    for w in re.findall(r"#?([\w\-]{1,30})", text or ""):
        tag = "#" + w
        if tag.lower() not in seen:
            seen.add(tag.lower())
            out.append(tag)
    return out[:16]


async def on_tags(update, ctx):
    tags = parse_tags(update.message.text)
    if not tags:
        await say(update, tx(ctx, "tags_bad"))
        return K_TAGS
    db.update_kit(kit_bid(ctx), hashtags=tags)
    if wiz(ctx):
        return await finish_wizard(update, ctx)
    return await show_kit(update, ctx)


async def on_tags_skip(update, ctx):
    await answer(update)
    await strip_kb(update)
    if wiz(ctx):
        return await finish_wizard(update, ctx)
    return await show_kit(update, ctx)


async def finish_wizard(update, ctx):
    ctx.user_data["wiz"] = False
    k = kit(ctx)
    data = await run(_layout_preview, kit_bid(ctx), preview_tag(k))
    await update.effective_chat.send_photo(io.BytesIO(data), caption=tx(ctx, "wiz_done"), parse_mode=HTML)
    reset_session(ctx)
    return await show_menu(update, ctx)


# ---- Меню кита ----
def kit_text(ctx, bid):
    b = db.get_brand(bid)
    k = b["kit"]
    font = R.FONTS[k["font"]]["label"] if k.get("font") in R.FONTS else tx(ctx, "font_custom")
    return tx(ctx, "kit_head", brand=esc(k.get("name") or "—"), font=esc(font),
              pos=tx(ctx, "pos_" + k.get("pos", "bl")), size=k.get("size", "m").upper(),
              color=tx(ctx, "col_" + k.get("color", "adaptive")),
              cover=tx(ctx, "cover_own" if db.has_asset(bid, "logo_cover") else "cover_same"),
              tags=esc(" ".join(k.get("hashtags") or [])) or tx(ctx, "tags_none"))


def kit_kb(ctx):
    b = lambda key, cb: Btn(tx(ctx, key), callback_data=f"kit:{cb}")  # noqa: E731
    return KB([
        [b("k_name", "name"), b("k_logo", "logo")],
        [b("k_cover", "cover"), b("k_sample", "sample")],
        [b("k_layout", "layout"), b("k_font", "font")],
        [b("k_tags", "tags")],
        [Btn(tx(ctx, "b_menu"), callback_data="menu:home")],
    ])


async def show_kit(update, ctx, edit=False):
    ctx.user_data["wiz"] = False
    text, kb = kit_text(ctx, kit_bid(ctx)), kit_kb(ctx)
    if edit:
        await edit_or_say(update, text, kb)
    else:
        await say(update, text, kb)
    return KIT


async def on_kit(update, ctx):
    await answer(update)
    action = update.callback_query.data.split(":", 1)[1]
    if not kit_bid(ctx):
        return await show_menu(update, ctx, edit=True)
    ctx.user_data["wiz"] = False
    if action == "back":
        await strip_kb(update)
        return await show_kit(update, ctx, edit=not (update.callback_query.message.photo))
    if action == "name":
        return await ask_name(update, ctx)
    if action == "logo":
        return await ask_logo(update, ctx)
    if action == "sample":
        return await ask_sample(update, ctx)
    if action == "layout":
        await strip_kb(update)
        return await show_layout(update, ctx)
    if action == "font":
        await strip_kb(update)
        return await show_font(update, ctx, fresh=True)
    if action == "tags":
        await strip_kb(update)
        return await ask_tags(update, ctx)
    if action == "cover":
        rows = [[Btn(tx(ctx, "b_cover_reset"), callback_data="cov:reset")],
                [Btn(tx(ctx, "b_back"), callback_data="kit:back")]]
        await edit_or_say(update, tx(ctx, "ask_cover"), KB(rows))
        return K_COVER
    return KIT


async def on_cover_file(update, ctx):
    if not await _receive_logo(update, ctx, "logo_cover"):
        return K_COVER
    k = kit(ctx)
    data = await run(_font_preview, kit_bid(ctx), tx(ctx, "preview_title"), preview_tag(k))
    await update.effective_chat.send_photo(io.BytesIO(data))
    return await show_kit(update, ctx)


async def on_cover_reset(update, ctx):
    await answer(update)
    db.del_asset(kit_bid(ctx), "logo_cover")
    return await show_kit(update, ctx, edit=True)


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
    kb = KB([[Btn(tx(ctx, "tpl_branding"), callback_data="tpl:branding")],
             [Btn(tx(ctx, "tpl_cover"), callback_data="tpl:cover")],
             [Btn(tx(ctx, "b_menu"), callback_data="menu:home")]])
    await edit_or_say(update, tx(ctx, "tpl_head"), kb)
    return P_TPL


async def on_tpl(update, ctx):
    await answer(update)
    post(ctx)["tpl"] = update.callback_query.data.split(":")[1]
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
    # Одно «живое» статус-сообщение внизу вместо десяти одинаковых
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
    if p["tpl"] == "cover":
        return await ask_title(update, ctx)
    return await ask_format(update, ctx)


async def ask_title(update, ctx):
    await say(update, tx(ctx, "ask_title"), KB([[Btn(tx(ctx, "b_back"), callback_data="p:back")]]))
    return P_TITLE


async def on_title(update, ctx):
    title = (update.message.text or "").strip("\n")
    if not title.strip():
        await say(update, tx(ctx, "title_bad"))
        return P_TITLE
    post(ctx)["title"] = title
    return await ask_format(update, ctx)


async def ask_format(update, ctx):
    p = post(ctx)
    keys = COVER_FEED_FORMATS if p["tpl"] == "cover" else FEED_FORMATS
    label = lambda k: tx(ctx, "fmt_orig") if k == "orig" else k  # noqa: E731
    main = [k for k in keys if k != "orig"]
    rows = [[Btn(label(k), callback_data=f"fmt:{k}") for k in main[i:i + 3]] for i in range(0, len(main), 3)]
    rows.append([Btn(label("orig"), callback_data="fmt:orig")])
    rows.append([Btn(tx(ctx, "b_back"), callback_data="p:back")])
    text = tx(ctx, "ask_format_cover") if p["tpl"] == "cover" else tx(ctx, "ask_format", n=len(p["photos"]))
    await edit_or_say(update, text, KB(rows))
    return P_FORMAT


async def on_format(update, ctx):
    await answer(update)
    post(ctx)["fmt"] = update.callback_query.data.split(":", 1)[1]
    return await ask_tag(update, ctx)


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
        await edit_or_say(update, tx(ctx, "ask_custom_tag"),
                          KB([[Btn(tx(ctx, "b_back"), callback_data="p:back")]]))
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


async def on_post_back(update, ctx):
    """«Назад» внутри создания поста — шаг зависит от текущего состояния."""
    await answer(update)
    state = ctx.user_data.get("_state")
    p = post(ctx)
    if state == P_TPL or not p.get("tpl"):
        return await show_menu(update, ctx, edit=True)
    if state == P_PHOTOS:
        return await ask_tpl(update, ctx)
    if state == P_TITLE:
        await strip_kb(update)
        return await ask_photos(update, ctx)
    if state == P_FORMAT:
        if p["tpl"] == "cover":
            await strip_kb(update)
            return await ask_title(update, ctx)
        return await ask_photos(update, ctx)
    if state in (P_TAG,):
        return await ask_format(update, ctx)
    if state in (P_CUSTOM_TAG, P_DARK):
        if state == P_DARK:
            try:
                await update.callback_query.message.delete()
            except TelegramError:
                pass
            await say(update, tx(ctx, "ask_tag"), tag_kb(ctx))
            return P_TAG
        return await ask_tag(update, ctx)
    return await show_menu(update, ctx, edit=True)


def back_for(state):
    """Оборачивает on_post_back, запоминая состояние, из которого нажали «Назад»."""
    async def handler(update, ctx):
        ctx.user_data["_state"] = state
        return await on_post_back(update, ctx)
    return handler


async def proceed(update, ctx, tag):
    p = post(ctx)
    p["tag"] = tag
    if p["tpl"] == "cover":
        p["dark"] = R.DARK_DEFAULT_IDX
        return await show_dark(update, ctx, fresh=True)
    return await render_branding_batch(update, ctx)


def _branding_job(data, fmt, tag, brand):
    return R.to_jpeg(R.render_branding(R.open_photo(data), fmt, tag, brand))


def _cover_job(data, fmt, title, tag, brand, level):
    img = R.open_photo(data)
    return (R.to_jpeg(R.render_cover_feed(img, fmt, title, tag, brand, level)),
            R.to_jpeg(R.render_cover_story(img, "ig", title, brand, level)),
            R.to_jpeg(R.render_cover_story(img, "tg", title, brand, level)))


def _cover_preview(data, fmt, title, tag, brand, level):
    return R.to_preview(R.render_cover_feed(R.open_photo(data), fmt, title, tag, brand, level))


async def render_branding_batch(update, ctx):
    p = post(ctx)
    photos, bid = p["photos"], p["bid"]
    await say(update, tx(ctx, "working", n=len(photos)))
    brand = load_brand_obj(bid)
    base = safe_name(brand.kit.get("name"))
    ok = 0
    for i, data in enumerate(photos, 1):
        try:
            jpg = await run(_branding_job, data, p["fmt"], p["tag"], brand)
            await update.effective_chat.send_document(io.BytesIO(jpg), filename=f"{base}_{i}.jpg")
            ok += 1
        except Exception as e:
            logger.exception("branding photo %s: %s", i, e)
            await say(update, tx(ctx, "photo_err", i=i))
    db.record_event(bid, update.effective_user.id, "branding", ok)
    await say(update, tx(ctx, "done", ok=ok, n=len(photos)))
    reset_session(ctx)
    return await show_menu(update, ctx)


def dark_meter(idx):
    return "●" * (idx + 1) + "○" * (len(R.DARK_LEVELS) - idx - 1)


def dark_kb(ctx, idx):
    last = len(R.DARK_LEVELS) - 1
    return KB([
        [Btn(tx(ctx, "b_lighter") if idx > 0 else "· · ·", callback_data="dk:down" if idx > 0 else "dk:noop"),
         Btn(tx(ctx, "b_darker") if idx < last else "· · ·", callback_data="dk:up" if idx < last else "dk:noop")],
        [Btn(tx(ctx, "b_render"), callback_data="dk:ok")],
        [Btn(tx(ctx, "b_back"), callback_data="p:back")],
    ])


async def show_dark(update, ctx, fresh=False):
    p = post(ctx)
    if "brand_obj" not in p:
        p["brand_obj"] = load_brand_obj(p["bid"])
    idx = p["dark"]
    data = await run(_cover_preview, p["photos"][0], p["fmt"], p["title"], p["tag"], p["brand_obj"],
                     R.DARK_LEVELS[idx])
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
        new = max(0, min(len(R.DARK_LEVELS) - 1, p["dark"] + (1 if action == "up" else -1)))
        if new == p["dark"]:
            await q.answer(tx(ctx, "edge"))
            return P_DARK
        await answer(update)
        p["dark"] = new
        return await show_dark(update, ctx)
    # ok → финальный рендер
    await answer(update)
    await strip_kb(update)
    await say(update, tx(ctx, "working_cover"))
    brand = p.get("brand_obj") or load_brand_obj(p["bid"])
    base = safe_name(brand.kit.get("name"))
    level = R.DARK_LEVELS[p["dark"]]
    ok = 0
    for i, data in enumerate(p["photos"], 1):
        try:
            feed, ig, tg = await run(_cover_job, data, p["fmt"], p["title"], p["tag"], brand, level)
            for blob, suffix in ((feed, "feed"), (ig, "story_ig"), (tg, "story_tg")):
                await update.effective_chat.send_document(io.BytesIO(blob), filename=f"{base}_cover_{i}_{suffix}.jpg")
            ok += 1
        except Exception as e:
            logger.exception("cover photo %s: %s", i, e)
            await say(update, tx(ctx, "photo_err", i=i))
    db.record_event(p["bid"], update.effective_user.id, "cover", ok)
    await say(update, tx(ctx, "done", ok=ok, n=len(p["photos"])))
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
                     f"📷{db.photos_used(b['id'])}/{plan_limits(b)['photos']}")
    storage = "постоянное" if db.STORAGE_PERSISTENT else "⚠️ ВРЕМЕННОЕ — подключи Volume"
    await update.message.reply_text("\n".join(lines) + f"\n\nХранилище: {storage}", parse_mode=HTML)


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
    """Кнопка из старого сообщения, когда диалог уже в другом месте."""
    L(ctx, update)
    try:
        await update.callback_query.answer(tx(ctx, "stale"), show_alert=True)
    except TelegramError:
        pass


async def on_orphan(update, ctx):
    """Сообщение вне диалога (например, после перезапуска бота) — показываем меню."""
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
    kit_back = CallbackQueryHandler(on_kit, pattern="^kit:back$")

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            CommandHandler("menu", cmd_start),
            CallbackQueryHandler(on_menu, pattern="^menu:"),
            # Код, присланный без /start (клиент просто переслал его боту)
            MessageHandler(filters.Regex(CODE_RE), on_code),
        ],
        states={
            MENU: [CallbackQueryHandler(on_switch, pattern=r"^sw:\d+$"),
                   CallbackQueryHandler(on_team, pattern="^team:new$")],
            CODE: [MessageHandler(TXT, on_code)],
            K_NAME: [MessageHandler(TXT, on_name), kit_back],
            K_LOGO: [MessageHandler(IMG, on_logo), kit_back],
            K_SAMPLE: [MessageHandler(IMG, on_sample),
                       CallbackQueryHandler(on_sample_skip, pattern="^smp:skip$"), kit_back],
            K_LAYOUT: [CallbackQueryHandler(on_layout, pattern="^lay:")],
            K_FONT: [CallbackQueryHandler(on_font, pattern="^fnt:")],
            K_FONT_UP: [MessageHandler(filters.Document.ALL, on_font_file),
                        CallbackQueryHandler(on_font, pattern="^fnt:back$")],
            K_TAGS: [MessageHandler(TXT, on_tags),
                     CallbackQueryHandler(on_tags_skip, pattern="^tags:skip$"), kit_back],
            KIT: [CallbackQueryHandler(on_kit, pattern="^kit:")],
            K_COVER: [MessageHandler(IMG, on_cover_file),
                      CallbackQueryHandler(on_cover_reset, pattern="^cov:reset$"), kit_back],
            P_TPL: [CallbackQueryHandler(on_tpl, pattern="^tpl:"),
                    CallbackQueryHandler(back_for(P_TPL), pattern="^p:back$")],
            P_PHOTOS: [MessageHandler(IMG, on_photo),
                       CommandHandler("done", on_photos_done),
                       CallbackQueryHandler(on_photos_done, pattern="^p:done$"),
                       CallbackQueryHandler(back_for(P_PHOTOS), pattern="^p:back$")],
            P_TITLE: [MessageHandler(TXT, on_title),
                      CallbackQueryHandler(back_for(P_TITLE), pattern="^p:back$")],
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


def main():
    if not TOKEN:
        raise SystemExit("BOT_TOKEN не задан")
    db.init_db()
    if not ADMIN_IDS:
        logger.warning("ADMIN_IDS не задан — админ-команды недоступны. Узнай свой ID через /myid.")
    build_app().run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

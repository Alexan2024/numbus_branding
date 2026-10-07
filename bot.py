"""NUMBUS Branding — Telegram-бот.

Один бот, много брендов. Клиент получает доступ по коду или по заявке, за пару
минут собирает стиль (название → логотип → цвет → три стиля на своём фото),
а дальше присылает фото с подписью и получает готовые файлы. Тонкая настройка —
в редакторе (Mini App, web.py + webapp.html).
"""
import io
import os
import re
import html
import json
import hashlib
import time
import signal
import asyncio
import logging
import warnings
import traceback
from datetime import datetime, time as dtime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from aiohttp import web as aioweb
from telegram import (Update, InlineKeyboardButton as Btn, InlineKeyboardMarkup as KB, BotCommand,
                      BotCommandScopeChat, BotCommandScopeDefault, InputMediaDocument, InputMediaPhoto,
                      MenuButtonDefault, MenuButtonWebApp, WebAppInfo)
from telegram.constants import ParseMode
from telegram.error import BadRequest, NetworkError, RetryAfter, TelegramError, TimedOut
from telegram.ext import (
    AIORateLimiter, Application, BaseUpdateProcessor, CallbackQueryHandler, CommandHandler,
    ContextTypes, ConversationHandler, ExtBot, MessageHandler, TypeHandler, filters,
)
from telegram.request import HTTPXRequest
from telegram.warnings import PTBUserWarning
from PIL import Image, ImageDraw, ImageFont

import db
import drafts
import render as R
import sample as SMP
import spec as S
import web
import backups
import persist
import quota
from texts import t as _t, MONTHS_GEN
from typo import typograf

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
logger = logging.getLogger("numbus")
warnings.filterwarnings("ignore", category=PTBUserWarning)


def env_int(name, default, lo=None, hi=None):
    """Число из переменной окружения. Пусто, «4h», «-1» вне границ — значение по умолчанию и
    предупреждение в логе: опечатка в Railway не должна ронять бота."""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        v = int(float(raw.replace(",", ".")))
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            raise ValueError(raw)
        return v
    except (TypeError, ValueError, OverflowError):
        logger.warning("%s=%r — не подходит, беру %s", name, raw, default)
        return default


def env_tz(name, default="Europe/Moscow"):
    raw = (os.environ.get(name) or "").strip()
    try:
        return ZoneInfo(raw or default)
    except Exception:
        logger.warning("%s=%r — неизвестный часовой пояс, беру %s", name, raw, default)
        return ZoneInfo(default)


TOKEN = (os.environ.get("BOT_TOKEN") or "").strip() or None
ADMIN_IDS = {int(x) for x in re.split(r"[,;\s]+", os.environ.get("ADMIN_IDS", "")) if x.strip().isdigit()}
SUPPORT = os.environ.get("SUPPORT_CONTACT", "").strip() or "администратору"
PORT = env_int("PORT", 8080, 1, 65535)
_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
WEBAPP_URL = ((os.environ.get("WEBAPP_URL") or "").strip() or (f"https://{_domain.strip()}" if _domain else "")).rstrip("/")
PRIVACY_URL = os.environ.get("PRIVACY_URL", "").strip()
TG_PROXY = os.environ.get("TG_PROXY", "").strip()           # socks5://… или http://… — если Telegram режут
TG_API_URL = os.environ.get("TG_API_URL", "").strip().rstrip("/")   # свой сервер Bot API (telegram-bot-api)
TZ = env_tz("BOT_TZ")
BACKUP_HOUR = backups.backup_hour(4)                                  # "", "04:00", "4" → 4
BACKUP_TO_TELEGRAM = backups.env_bool("BACKUP_TO_TELEGRAM", False)    # true / 1 / yes / on
PILOT_DAYS = env_int("PILOT_DAYS", 30, 1, 3650)
MAX_BATCH = 30
ALBUM = 10                              # Telegram собирает в альбом до 10 файлов
CAPTION_MAX = 1024                      # подпись под фото или альбомом в Telegram
RENDER_SEM = asyncio.Semaphore(env_int("RENDER_WORKERS", 2, 1, 32))
HTML = ParseMode.HTML
esc = html.escape

MENU, CODE, K_NAME, K_LOGO, K_COLOR, K_PHOTO, REQ = range(7)

FORMATS = ["4:5", "3:4", "1:1", "3:2", "16:9", "1.91:1", "9:16", "orig"]
# Код доступа где угодно в сообщении: «NB-AAAA-BBBB», «nb aaaa bbbb», пересланный блок «Для клиента»…
CODE_RE = r"(?i)(?<![A-Z0-9])NB[\s\-\u2010-\u2015_.]*[A-Z0-9]{4}[\s\-\u2010-\u2015_.]*[A-Z0-9]{4}(?![A-Z0-9])"
CODE_FIND = re.compile(CODE_RE)
CODE_LIKE = re.compile(r"(?i)(?<![A-Z0-9])NB[\s\-\u2010-\u2015_.]*[A-Z0-9]")
# буквы, которые при перепечатке кода легко набрать кириллицей
HOMOGLYPHS = str.maketrans("АВЕКМНОРСТХУавекмнорстху", "ABEKMHOPCTXYABEKMHOPCTXY")
HEX_RE = re.compile(r"^#?([0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})$")


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


# ============ Уборка чата ============
# Бот сам убирает отработанное, чтобы в чате оставались только меню, пульт
# и готовые файлы:
#   • ваши сообщения (команды, ответы, исходные фото) — сразу после обработки;
#   • вопросы бота (kind="prompt") — когда вы ответили следующим сообщением;
#   • предупреждения (kind="notice") — через NOTICE_TTL секунд;
#   • старое меню и старый пульт — когда появились новые.
# Готовые файлы не трогаются. Всё, что бот отправил, пишется в журнал
# (db.msglog) — по нему работает «Очистить чат» в админ-панели.
NOTICE_TTL = 20
PROMPTS = {}        # chat_id → id вопросов, ждущих ответа
MENU_MSG = {}       # chat_id → id текущего меню
_TASKS = set()


class LoggingBot(ExtBot):
    """Записывает всё, что бот отправляет в личку, в журнал сообщений."""

    async def _send_message(self, endpoint, data, *args, **kwargs):
        msg = await super()._send_message(endpoint, data, *args, **kwargs)
        try:
            if msg and getattr(msg, "chat", None) and msg.chat.type == "private":
                db.log_msg(msg.chat.id, msg.message_id, "result" if endpoint == "sendDocument" else "bot")
        except Exception as e:
            logger.debug("msglog: %s", e)
        return msg

    async def send_media_group(self, *args, **kwargs):
        msgs = await super().send_media_group(*args, **kwargs)
        try:
            for m in msgs or ():
                if m.chat.type == "private":
                    db.log_msg(m.chat.id, m.message_id, "result")
        except Exception as e:
            logger.debug("msglog: %s", e)
        return msgs


async def delete_ids(bot, chat_id, ids):
    ids = sorted({int(i) for i in ids if i})
    if not ids:
        return
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        try:
            await bot.delete_messages(chat_id, chunk)
        except TelegramError:
            for m in chunk:
                try:
                    await bot.delete_message(chat_id, m)
                except TelegramError:
                    pass
    db.unlog_msgs(chat_id, ids)


def delete_later(bot, chat_id, ids, delay):
    async def job():
        try:
            await asyncio.sleep(delay)
            await delete_ids(bot, chat_id, ids)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug("delete_later: %s", e)
    task = asyncio.create_task(job())
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


def track(msg, kind="prompt", ttl=None):
    if msg is None:
        return msg
    ttl = ttl or NOTICE_TTL
    chat_id = msg.chat_id
    if kind == "prompt":
        PROMPTS.setdefault(chat_id, []).append(msg.message_id)
    elif kind == "notice":
        delete_later(msg.get_bot(), chat_id, [msg.message_id], ttl)
    return msg


async def pre_update(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """До обработки: запоминаем вопросы, на которые сейчас пришёл ответ."""
    u = update.effective_user
    if u and not u.is_bot:
        try:
            db.ensure_user(u.id, u.language_code)
            db.touch_user(u.id, u.full_name, u.username)
        except Exception as e:
            logger.debug("touch_user: %s", e)
    m = update.message
    if m and m.chat.type == "private" and update.effective_user and not update.effective_user.is_bot:
        db.log_msg(m.chat_id, m.message_id, "user")
        ctx.user_data["_answered"] = list(PROMPTS.get(m.chat_id, []))


# Команды, которые не отвечают на открытый вопрос (/help, /desktop…): вопрос остаётся в чате.
# /start, /menu и /cancel начинают заново — старый вопрос уходит.
RESET_CMDS = ("start", "menu", "cancel")


def _side_command(m) -> bool:
    t = (m.text or "").strip()
    if not t.startswith("/"):
        return False
    cmd = t[1:].split()[0].split("@")[0].lower() if len(t) > 1 else ""
    return cmd not in RESET_CMDS


async def post_update(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """После обработки: убираем ответ пользователя и вопросы, на которые он ответил."""
    m = update.message
    if m and m.chat.type == "private" and update.effective_user and not update.effective_user.is_bot:
        keep = ctx.user_data.pop("_keep_msg", False)
        answered = ctx.user_data.pop("_answered", [])
        if _side_command(m):
            answered = []                              # на вопрос не ответили — он остаётся
        else:
            left = [i for i in PROMPTS.get(m.chat_id, []) if i not in answered]
            if left:
                PROMPTS[m.chat_id] = left
            else:
                PROMPTS.pop(m.chat_id, None)
        await delete_ids(ctx.bot, m.chat_id, answered + ([] if keep else [m.message_id]))


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
    for k in ("wiz", "kit_bid", "await", "code", "wiz_colors", "wiz_album", "wiz_t0", "wiz_seen", "wiz_gid", "wiz_fuid",
              "smp_gid"):
        ctx.user_data.pop(k, None)


# Брошенная настройка стиля не должна перехватывать посты. Внутри процесса это делает
# conversation_timeout, но после перезапуска PTB таймер не восстанавливает — поэтому шаг
# мастера помнит, когда задал вопрос, и старый ответ уходит туда, где его ждут сейчас.
WIZ_TTL = 3600


def wiz_touch(ctx):
    ctx.user_data["wiz_seen"] = time.time()


def wiz_stale(ctx) -> bool:
    seen = ctx.user_data.get("wiz_seen")
    return bool(seen) and time.time() - seen > WIZ_TTL


async def wiz_expired(update, ctx):
    """Мастер давно брошен: заканчиваем его и обрабатываем сообщение как обычное."""
    aw = ctx.user_data.get("await")
    reset_session(ctx)
    if aw:
        ctx.user_data["await"] = aw          # человек отвечал пульту — ответ дойдёт туда
    if update.callback_query:
        await on_stale(update, ctx)
    elif update.message and (update.message.photo or update.message.document):
        await on_quick_photo(update, ctx)
    elif update.message:
        await on_quick_text(update, ctx)
    return ConversationHandler.END


def _local(iso):
    dt = datetime.fromisoformat(iso) if isinstance(iso, str) else iso
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def fmt_date(iso) -> str:
    """Дата по часовому поясу бота (по умолчанию Москва): 12.11.2026."""
    try:
        return _local(iso).strftime("%d.%m.%Y")
    except Exception:
        return "—"


def fmt_day(iso, lang="ru") -> str:
    """«12 ноября» (с годом, если он не текущий)."""
    try:
        dt = _local(iso)
    except Exception:
        return "—"
    months = MONTHS_GEN.get(lang, MONTHS_GEN["ru"])
    s = f"{dt.day} {months[dt.month - 1]}" if lang == "ru" else f"{months[dt.month - 1]} {dt.day}"
    return s + (f" {dt.year}" if dt.year != datetime.now(TZ).year else "")


def plan_limits(b):
    return db.PLANS.get(b["plan"], db.PLANS["pilot"])


def plan_label(ctx, plan):
    return tx(ctx, "plan_" + plan) if ("plan_" + plan) in __import__("texts").T else plan.capitalize()


def current_brand(uid):
    brands = db.user_brands(uid)
    if not brands:
        return None, brands
    u = db.get_user(uid) or {}
    b = next((x for x in brands if x["id"] == u.get("active_brand")), None) or brands[0]
    if b["id"] != u.get("active_brand"):
        db.set_active_brand(uid, b["id"])
    return b, brands


def is_editor(b):
    return b and b.get("role") in db.EDITOR_ROLES


def editor_btn(ctx, bid, key="b_editor"):
    if not WEBAPP_URL:
        return None
    return Btn(tx(ctx, key), web_app=WebAppInfo(url=f"{WEBAPP_URL}/?b={bid}"))


MENU_BTN = {}        # tg_id → стоит ли сейчас у человека кнопка «Редактор»


async def sync_menu_button(bot, uid, force=False):
    """Кнопка «Редактор» у поля ввода — только тем, кто может править стиль (владелец, дизайнер).
    Остальным — обычное меню команд. Telegram хранит кнопку для каждого чата отдельно."""
    if not WEBAPP_URL or not uid:
        return
    can = any(b["role"] in db.EDITOR_ROLES for b in db.user_brands(uid))
    if not force and MENU_BTN.get(uid) == can:
        return
    lang = (db.get_user(uid) or {}).get("lang") or "ru"
    btn = MenuButtonWebApp(PROFILE.get(lang, PROFILE["ru"])["menu"], WebAppInfo(f"{WEBAPP_URL}/")) if can \
        else MenuButtonDefault()
    try:
        await bot.set_chat_menu_button(chat_id=uid, menu_button=btn)
        MENU_BTN[uid] = can
    except TelegramError as e:
        logger.info("menu button %s: %s", uid, e)


async def sync_all_menu_buttons(bot):
    """После запуска: кнопка редактора у всех владельцев и дизайнеров (в фоне)."""
    for uid in db.editor_user_ids():
        await sync_menu_button(bot, uid, force=True)


def safe_name(name: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", (name or "").strip())[:30].strip("_")
    return s or "numbus"


async def run(fn, *a):
    async with RENDER_SEM:
        return await asyncio.to_thread(fn, *a)


def _secs(v):
    return v.total_seconds() if hasattr(v, "total_seconds") else float(v)


async def tg_call(fn, *a, **kw):
    """Вызов Bot API с повтором: «подождите N секунд» (429) и сетевые сбои не превращаются в ошибку поста."""
    for attempt in range(4):
        try:
            return await fn(*a, **kw)
        except RetryAfter as e:
            await asyncio.sleep(_secs(e.retry_after) + 1)
        except (TimedOut, NetworkError) as e:
            if attempt == 3 or isinstance(e, BadRequest):
                raise
            await asyncio.sleep(2 + attempt * 2)
    return await fn(*a, **kw)


async def answer(update, text=None, alert=False):
    if update.callback_query:
        try:
            await update.callback_query.answer(text, show_alert=alert)
        except TelegramError:
            pass


async def say(update, text, kb=None, kind="prompt", ttl=None):
    """kind: prompt — убрать после ответа; notice — убрать через ttl; keep/menu — оставить."""
    m = await update.effective_chat.send_message(text, parse_mode=HTML, reply_markup=kb,
                                                 disable_web_page_preview=True)
    return track(m, kind, ttl)


async def drop_prompt(update, ctx):
    """Вопрос, на который ответили кнопкой, больше не нужен — убираем его целиком."""
    q = update.callback_query
    if q and q.message:
        chat_id = q.message.chat_id
        if q.message.message_id in PROMPTS.get(chat_id, []):
            PROMPTS[chat_id].remove(q.message.message_id)
        await delete_ids(ctx.bot, chat_id, [q.message.message_id])


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
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return
    await strip_kb(update)
    await say(update, text, kb)


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
            await say(update, tx(ctx, "file_big"), kind="notice")
            return None, False
        raise
    return None, False


class _Size:
    """Размер фото без декодирования — для R.feed_size."""
    def __init__(self, w, h):
        self.width, self.height = w, h
        self.size = (w, h)


def photo_dims(data: bytes):
    """Ширина и высота фото с учётом поворота из EXIF (как его покажет рендер)."""
    im = Image.open(io.BytesIO(data))
    w, h = im.size
    try:
        if im.getexif().get(0x0112) in (5, 6, 7, 8):
            w, h = h, w
    except Exception:
        pass
    return w, h


def is_image(data: bytes) -> bool:
    try:
        Image.open(io.BytesIO(data))
        return True
    except Exception:
        return False


WORK_LONG = 4000            # рабочая копия HEIC и огромных фото: длинная сторона, px
WORK_PIXELS = 24_000_000    # не-JPEG больше этого — тоже в рабочую копию


def working_copy(data: bytes) -> bytes:
    """HEIC (и огромный PNG/TIFF) декодируется долго — при каждом нажатии пульта заново.
    Такие фото один раз превращаются в рабочий JPEG: q95, длинная сторона до 4000, sRGB,
    поворот из EXIF уже применён. JPEG остаётся как есть: он и так открывается быстро."""
    try:
        im = Image.open(io.BytesIO(data))
        fmt, px = (im.format or "").upper(), im.size[0] * im.size[1]
    except Exception:
        return data
    if fmt in ("JPEG", "MPO") or (fmt not in ("HEIF", "HEIC", "AVIF") and px <= WORK_PIXELS):
        return data
    try:
        img = R.open_image(data, R.PHOTO_SHORT)   # фото, а не ассет: предел 100 Мп, раскрытие с уменьшением
    except Exception as e:                    # не открылось — пусть решает обычная проверка
        logger.info("working copy: %s", e)
        return data
    img = img.convert("RGB")
    img.thumbnail((WORK_LONG, WORK_LONG), Image.LANCZOS)
    return R.to_jpeg(img, 95)


def is_admin(update):
    return bool(update.effective_user and update.effective_user.id in ADMIN_IDS)


def find_code(text):
    """Код доступа из любого места сообщения → «NB-XXXX-XXXX» (или None)."""
    m = CODE_FIND.search((text or "").translate(HOMOGLYPHS))
    if not m:
        return None
    raw = re.sub(r"[^A-Za-z0-9]", "", m.group(0)).upper()
    return f"NB-{raw[2:6]}-{raw[6:10]}"


class _HasCode(filters.MessageFilter):
    """В тексте есть код доступа (в любом месте, в любом написании)."""
    def filter(self, message):
        return bool(find_code(message.text))


HAS_CODE = _HasCode()


def uniq(seq):
    out = []
    for x in seq:
        if x and x not in out:
            out.append(x)
    return out


# ============ Главное меню ============
def brand_label(ctx, b):
    """Название бренда для меню и кнопок; до настройки — «Новый бренд», а не прочерк."""
    return (b.get("kit") or {}).get("name") or tx(ctx, "brand_unnamed")


def menu_text(ctx, b, note=None):
    lang = ctx.user_data.get("lang", "ru")
    lines = ([note + "\n"] if note else []) + [tx(ctx, "menu_head", brand=esc(brand_label(ctx, b)))]
    if b.get("locked"):
        lines.append(tx(ctx, "menu_locked", support=esc(SUPPORT)))
    elif db.plan_active(b):
        _, reset = db.period_bounds(db.get_brand(b["root_id"]) or b)
        until, reset = fmt_day(b["plan_until"], lang), fmt_day(reset, lang)
        lines.append(tx(ctx, "menu_plan" if reset != until else "menu_plan_noreset", plan=plan_label(ctx, b["plan"]),
                        until=until, used=db.photos_used(b["id"]), limit=plan_limits(b)["photos"], reset=reset))
    else:
        lines.append(tx(ctx, "menu_expired", until=fmt_day(b["plan_until"], lang), support=esc(SUPPORT)))
    n_brands, max_brands = len(db.sub_brand_ids(b["id"])), plan_limits(b)["brands"]
    if max_brands > 1:
        lines.append(tx(ctx, "brands_count", n=n_brands, limit=max_brands))
    if not db.has_asset(b["id"], "logo"):
        lines.append("\n" + tx(ctx, "menu_no_kit"))
    elif db.plan_active(b) and not b.get("locked"):
        lines.append("\n" + tx(ctx, "menu_hint"))
    return "\n".join(lines)


async def drop_menu(ctx, chat_id):
    """Убрать текущее меню (или приветствие): начался другой сценарий, оно устарело."""
    old = MENU_MSG.pop(chat_id, None)
    if old:
        await delete_ids(ctx.bot, chat_id, [old])


def menu_kb(ctx, b, brands, admin=False):
    rows = []
    if is_editor(b):
        eb = editor_btn(ctx, b["id"])
        if eb and db.has_asset(b["id"], "logo"):
            rows.append([eb, Btn(tx(ctx, "b_desktop"), callback_data="menu:desktop")])
        elif not db.has_asset(b["id"], "logo"):
            rows.append([Btn(tx(ctx, "k_setup"), callback_data="menu:setup")])
        if db.has_asset(b["id"], "logo") and SMP.enabled() and db.plan_active(b) and not b.get("locked"):
            rows.append([Btn(tx(ctx, "b_sample"), callback_data="smp:start")])
    if can_add_brand(b):
        rows.append([Btn(tx(ctx, "b_add_brand"), callback_data="menu:addbrand")])
    row = []
    if b["role"] == "owner":
        row.append(Btn(tx(ctx, "b_team"), callback_data="menu:team"))
    if len(brands) > 1:
        row.append(Btn(tx(ctx, "b_switch"), callback_data="menu:switch"))
    row.append(Btn(tx(ctx, "b_code"), callback_data="menu:code"))
    rows.append(row)
    rows.append([Btn(tx(ctx, "b_settings"), callback_data="set:show"), Btn(tx(ctx, "b_help"), callback_data="menu:help")])
    if admin:
        rows.append([Btn("Админ", callback_data="adm:panel")])
    return KB(rows)


def can_add_brand(b):
    """Добавлять бренды может владелец подписки на тарифе, где брендов больше одного."""
    root = db.get_brand(b["root_id"])
    return bool(root and b["role"] == "owner" and db.plan_active(root)
                and len(db.sub_brand_ids(b["id"])) < plan_limits(root)["brands"])


def welcome_kb(ctx, admin=False):
    rows = [[Btn(tx(ctx, "b_request"), callback_data="acc:req"), Btn(tx(ctx, "b_have_code"), callback_data="menu:code")],
            [Btn(tx(ctx, "b_settings"), callback_data="set:show"), Btn(tx(ctx, "b_help"), callback_data="menu:help")]]
    if admin:
        rows.append([Btn("Админ", callback_data="adm:panel")])
    return KB(rows)


async def show_menu(update, ctx, edit=False, note=None):
    b, brands = current_brand(update.effective_user.id)
    chat_id = update.effective_chat.id
    await sync_menu_button(ctx.bot, update.effective_user.id)
    if not b:
        q = update.callback_query
        if edit and q and q.message and not q.message.photo:
            await edit_or_say(update, tx(ctx, "welcome_new"), welcome_kb(ctx, is_admin(update)))
            MENU_MSG[chat_id] = q.message.message_id
            return CODE
        old = MENU_MSG.get(chat_id)
        m = await say(update, tx(ctx, "welcome_new"), welcome_kb(ctx, is_admin(update)), kind="menu")
        MENU_MSG[chat_id] = m.message_id
        if old and old != m.message_id:
            await delete_ids(ctx.bot, chat_id, [old])
        return CODE
    text, kb = menu_text(ctx, b, note), menu_kb(ctx, b, brands, is_admin(update))
    q = update.callback_query
    if edit and q and q.message and not q.message.photo:
        try:
            await q.edit_message_text(text, parse_mode=HTML, reply_markup=kb, disable_web_page_preview=True)
            MENU_MSG[chat_id] = q.message.message_id
            return MENU
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return MENU
    old = MENU_MSG.get(chat_id)
    m = await say(update, text, kb, kind="menu")
    MENU_MSG[chat_id] = m.message_id
    if old and old != m.message_id:
        await delete_ids(ctx.bot, chat_id, [old])
    return MENU


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    L(ctx, update)
    reset_session(ctx)
    args = ctx.args or []
    if not db.user_brands(update.effective_user.id):
        db.log_event("start", update.effective_user.id, arg=(args[0][:20] if args else ""))
    if args and args[0].startswith("j_"):
        return await do_join(update, ctx, args[0][2:])
    return await show_menu(update, ctx)


async def cmd_cancel(update, ctx):
    L(ctx, update)
    reset_session(ctx)
    await say(update, tx(ctx, "cancelled"), kind="notice")
    return await show_menu(update, ctx)


async def cmd_help(update, ctx):
    L(ctx, update)
    await say(update, tx(ctx, "help", support=esc(SUPPORT)),
              KB([[Btn(tx(ctx, "b_menu"), callback_data="menu:home")]]), kind="keep")


async def on_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    L(ctx, update)
    await answer(update)
    action = update.callback_query.data.split(":", 1)[1]
    uid = update.effective_user.id
    reset_session(ctx)

    if action == "home":
        return await show_menu(update, ctx, edit=True)
    if action == "code":
        await edit_or_say(update, tx(ctx, "ask_code"), KB([[Btn(tx(ctx, "b_menu"), callback_data="menu:home")]]))
        return CODE
    if action == "help":
        await edit_or_say(update, tx(ctx, "help", support=esc(SUPPORT)),
                          KB([[Btn(tx(ctx, "b_menu"), callback_data="menu:home")]]))
        return MENU

    b, brands = current_brand(uid)
    if not b:
        return await show_menu(update, ctx)
    if action == "switch":
        rows = [[Btn(("✓ " if x["id"] == b["id"] else "") + brand_label(ctx, x)
                     + (tx(ctx, "b_paused") if x.get("locked") else ""), callback_data=f"sw:{x['id']}")] for x in brands]
        rows.append([Btn(tx(ctx, "b_menu"), callback_data="menu:home")])
        await edit_or_say(update, tx(ctx, "switch_head"), KB(rows))
        return MENU
    if action == "setup":
        if not is_editor(b):
            await say(update, tx(ctx, "kit_owner_only"), kind="notice")
            return MENU
        ctx.user_data["kit_bid"] = b["id"]
        ctx.user_data["wiz"] = True
        return await ask_name(update, ctx)
    if action == "team":
        return await show_team(update, ctx, b, edit=True)
    if action == "addbrand":
        if not can_add_brand(b):
            await say(update, tx(ctx, "brand_limit"), kind="notice")
            return MENU
        bid = db.create_brand(uid, b["plan"], 0, sub_id=b["root_id"])
        ctx.user_data["kit_bid"] = bid
        ctx.user_data["wiz"] = True
        ctx.user_data["wiz_t0"] = time.time()
        return await ask_name(update, ctx)
    if action == "desktop":
        await send_desktop_link(update, ctx, b)
        return MENU
    if action == "new":   # кнопка из старых сообщений
        await say(update, tx(ctx, "q_hint"), kind="notice")
        return MENU
    return await show_menu(update, ctx, edit=True)


async def on_switch(update, ctx):
    L(ctx, update)
    await answer(update)
    bid = int(update.callback_query.data.split(":")[1])
    if db.member_role(bid, update.effective_user.id):
        db.set_active_brand(update.effective_user.id, bid)
    return await show_menu(update, ctx, edit=True)


async def send_desktop_link(update, ctx, b):
    """Одноразовая ссылка на редактор для компьютера (15 минут)."""
    if not is_editor(b):
        await say(update, tx(ctx, "kit_owner_only"), kind="notice")
        return
    if not WEBAPP_URL:
        await say(update, tx(ctx, "editor_off", support=esc(SUPPORT)), kind="notice")
        return
    tok = db.create_login_link(update.effective_user.id, b["id"])
    url = f"{WEBAPP_URL}/?b={b['id']}&k={tok}"
    await say(update, tx(ctx, "open_desktop", min=db.LINK_TTL_MIN),
              KB([[Btn(tx(ctx, "b_open_desktop"), url=url)]]), kind="notice", ttl=db.LINK_TTL_MIN * 60)


async def cmd_desktop(update, ctx):
    L(ctx, update)
    b, _ = current_brand(update.effective_user.id)
    if not b:
        await say(update, tx(ctx, "welcome_new"), welcome_kb(ctx))
        return
    await send_desktop_link(update, ctx, b)


# ============ Настройки ============
def settings_kb(ctx, uid, b):
    rows = [[Btn(tx(ctx, "b_lang"), callback_data="set:lang")]]
    n = db.session_count(uid)
    if n:
        rows.append([Btn(tx(ctx, "b_logout_all", n=n), callback_data="set:logout")])
    if PRIVACY_URL:
        rows.append([Btn(tx(ctx, "b_privacy"), url=PRIVACY_URL)])
    if b and b["role"] == "owner":
        rows.append([Btn(tx(ctx, "b_del_brand", brand=brand_label(ctx, b)[:24]),
                         callback_data="set:delb")])
    rows.append([Btn(tx(ctx, "b_del_me"), callback_data="set:delme")])
    rows.append([Btn(tx(ctx, "b_menu"), callback_data="menu:home")])
    return KB(rows)


async def on_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    L(ctx, update)
    q = update.callback_query
    uid = update.effective_user.id
    action = q.data.split(":", 1)[1]
    b, _ = current_brand(uid)
    back = [Btn(tx(ctx, "b_back"), callback_data="set:show")]
    if action == "lang":
        new = "en" if ctx.user_data.get("lang") == "ru" else "ru"
        db.set_lang(uid, new)
        ctx.user_data["lang"] = new
        action = "show"
    if action == "logout":
        db.drop_user_sessions(uid)
        await answer(update, tx(ctx, "logout_done"), alert=True)
        action = "show"
    if action == "show":
        await answer(update)
        await edit_or_say(update, tx(ctx, "settings_head"), settings_kb(ctx, uid, b))
        return
    if action == "delb" and b and b["role"] == "owner":
        await answer(update)
        names = ""
        if b["root_id"] == b["id"]:
            others = [x["kit"].get("name") or f"#{x['id']}" for x in db.sub_brands(b["id"]) if x["id"] != b["id"]]
            names = tx(ctx, "del_brand_sub", names=esc(", ".join(others))) if others else ""
        await edit_or_say(update, tx(ctx, "del_brand_confirm", brand=esc(brand_label(ctx, b)), sub=names),
                          KB([[Btn(tx(ctx, "b_del_yes"), callback_data=f"set:delbyes:{b['id']}")], back]))
        return
    if action.startswith("delbyes:"):
        bid = int(action.split(":")[1]) if action.split(":")[1].isdigit() else 0
        if db.member_role(bid, uid) != "owner":
            await on_stale(update, ctx)
            return
        ids = db.sub_brand_ids(bid) if (db.get_brand(bid) or {}).get("root_id") == bid else [bid]
        team = {r["tg_id"] for x in ids for r in db.team_stats(x)} | {uid}
        gone = db.delete_brand(bid)
        forget_samples(team)
        db.log_event("delete_brand", uid, None, brands=gone)
        for member in team:
            await sync_menu_button(ctx.bot, member)
        await answer(update, tx(ctx, "del_brand_done"), alert=True)
        return await show_menu(update, ctx, edit=True)
    if action == "delme":
        await answer(update)
        await edit_or_say(update, tx(ctx, "del_me_confirm"),
                          KB([[Btn(tx(ctx, "b_del_yes"), callback_data="set:delmeyes")], back]))
        return
    if action == "delmeyes":
        await answer(update)
        lang = ctx.user_data.get("lang", "ru")
        drafts.clear(uid)
        ctx.user_data.clear()
        gone = db.delete_user(uid)
        persist.forget_user(uid)
        forget_samples([uid])
        quota.forget_user(uid)
        db.log_event("delete_user", None, None, brands=len(gone))
        await edit_or_say(update, _t(lang, "del_me_done"))
        await sync_menu_button(ctx.bot, uid)
        return ConversationHandler.END
    await answer(update)


# ============ Доступ: заявка и коды ============
async def on_access_request(update, ctx):
    L(ctx, update)
    await answer(update)
    uid = update.effective_user.id
    if db.user_brands(uid):
        await edit_or_say(update, tx(ctx, "req_has"))
        return ConversationHandler.END
    if db.open_request(uid):
        await edit_or_say(update, tx(ctx, "req_dup"))
        return ConversationHandler.END
    await edit_or_say(update, tx(ctx, "req_ask"), KB([[Btn(tx(ctx, "b_menu"), callback_data="menu:home")]]))
    return REQ


async def maybe_quick(update, ctx) -> bool:
    """Человек отвечает на вопрос пульта («Текст», «Своя рубрика») или админки, а диалог
    всё ещё ждёт другое (код, цвет, название) — ответ уходит туда, где его ждут."""
    if ctx.user_data.get("await"):
        await on_quick_text(update, ctx)
        return True
    return False


async def on_request_text(update, ctx):
    L(ctx, update)
    if not find_code(update.message.text) and await maybe_quick(update, ctx):
        return ConversationHandler.END
    u = update.effective_user
    text = (update.message.text or "").strip()
    if not text:
        return REQ
    if find_code(text):
        return await on_code(update, ctx)
    rid = db.create_request(u.id, u.full_name, u.username, text)
    db.log_event("access_request", u.id, None, request=rid)
    await say(update, tx(ctx, "req_sent"), kind="keep")
    who = esc(u.full_name or "—") + (f" (@{esc(u.username)})" if u.username else "") + f", id <code>{u.id}</code>"
    kb = KB([[Btn("Открыть доступ", callback_data=f"acc:ok:{rid}"), Btn("Отклонить", callback_data=f"acc:no:{rid}")]])
    for aid in ADMIN_IDS:
        try:
            await ctx.bot.send_message(aid, f"<b>Заявка на доступ #{rid}</b>\n{who}\n\n{esc(text[:600])}",
                                       parse_mode=HTML, reply_markup=kb, disable_web_page_preview=True)
        except TelegramError as e:
            logger.warning("admin notify %s: %s", aid, e)
    return ConversationHandler.END


async def on_access_admin(update, ctx):
    """Админ отвечает на заявку: открыть доступ (пилот) или отклонить."""
    q = update.callback_query
    if not is_admin(update):
        await on_stale(update, ctx)
        return
    _, act, rid = q.data.split(":")
    r = db.get_request(int(rid))
    if not r:
        await answer(update, "Заявка не найдена", alert=True)
        return
    if not db.set_request_status(r["id"], "approved" if act == "ok" else "declined"):
        await answer(update, "По этой заявке уже есть решение", alert=True)
        return
    ulang = (db.get_user(r["tg_id"]) or {}).get("lang", "ru")
    if act == "ok":
        if not db.user_brands(r["tg_id"]):
            bid = db.create_brand(r["tg_id"], "pilot", PILOT_DAYS)
            b = db.get_brand(bid)
            db.log_event("code_ok", r["tg_id"], bid, via="request")
            await sync_menu_button(ctx.bot, r["tg_id"])
            try:
                await ctx.bot.send_message(r["tg_id"], _t(ulang, "req_ok_user", until=fmt_day(b["plan_until"], ulang)),
                                           parse_mode=HTML, reply_markup=KB([[Btn(_t(ulang, "b_wiz_start"),
                                                                                  callback_data="wiz:start")]]))
            except TelegramError as e:
                logger.warning("request user notify: %s", e)
        verdict = "Доступ открыт"
    else:
        try:
            await ctx.bot.send_message(r["tg_id"], _t(ulang, "req_no_user", support=esc(SUPPORT)), parse_mode=HTML)
        except TelegramError:
            pass
        verdict = "Отклонено"
    await answer(update, verdict)
    try:
        await q.edit_message_text((q.message.text_html or "") + f"\n\n<b>{verdict}</b> · {esc(update.effective_user.full_name)}",
                                  parse_mode=HTML)
    except TelegramError:
        pass


async def on_wiz_start(update, ctx):
    """Кнопка «Начать настройку» после одобренной заявки."""
    L(ctx, update)
    await answer(update)
    b, _ = current_brand(update.effective_user.id)
    if not b or not is_editor(b):
        return await show_menu(update, ctx)
    ctx.user_data["kit_bid"] = b["id"]
    ctx.user_data["wiz"] = True
    ctx.user_data["wiz_t0"] = time.time()
    await drop_menu(ctx, update.effective_chat.id)
    return await ask_name(update, ctx)


async def on_code(update, ctx):
    L(ctx, update)
    uid = update.effective_user.id
    text = update.message.text or ""
    code = find_code(text)
    if code:
        ctx.user_data.pop("await", None)     # прислали код — он важнее вопроса пульта
    elif await maybe_quick(update, ctx):
        return ConversationHandler.END
    if not code:
        if db.user_brands(uid) and not CODE_LIKE.search(text.translate(HOMOGLYPHS)):
            # «Ввести код» нажали и передумали: обычный текст — не код, отвечаем как вне диалога
            await on_quick_text(update, ctx)
            return ConversationHandler.END
        # на приветствии написали не код: подсказка, как он выглядит, и «Запросить доступ»
        rows = [] if db.user_brands(uid) else [[Btn(tx(ctx, "b_request"), callback_data="acc:req")]]
        await say(update, tx(ctx, "code_format"), KB(rows) if rows else None)
        return CODE
    res = db.peek_invite(code)
    if not res:
        await say(update, tx(ctx, "code_bad"))
        return CODE
    plan, days = res
    roots = db.owned_roots(uid)
    if roots:
        ctx.user_data["code"] = code
        rows = [[Btn(tx(ctx, "b_code_extend", brand=brand_label(ctx, r)[:24]),
                     callback_data=f"code:ext:{r['id']}")] for r in roots[:3]]
        rows.append([Btn(tx(ctx, "b_code_new"), callback_data="code:new")])
        rows.append([Btn(tx(ctx, "b_menu"), callback_data="menu:home")])
        await say(update, tx(ctx, "code_choose", plan=plan_label(ctx, plan), days=days), KB(rows))
        return CODE
    return await redeem_new(update, ctx, code)


async def redeem_new(update, ctx, code):
    uid = update.effective_user.id
    res = db.redeem_invite(code)
    if not res:
        await say(update, tx(ctx, "code_bad"))
        return CODE
    plan, days = res
    bid = db.create_brand(uid, plan, days)
    b = db.get_brand(bid)
    db.log_event("code_ok", uid, bid, plan=plan)
    lang = ctx.user_data.get("lang", "ru")
    await drop_menu(ctx, update.effective_chat.id)
    await say(update, tx(ctx, "code_ok", plan=plan_label(ctx, plan), until=fmt_day(b["plan_until"], lang)), kind="notice", ttl=60)
    ctx.user_data["kit_bid"] = bid
    ctx.user_data["wiz"] = True
    ctx.user_data["wiz_t0"] = time.time()
    return await ask_name(update, ctx)


async def on_code_choice(update, ctx):
    L(ctx, update)
    await answer(update)
    uid = update.effective_user.id
    code = ctx.user_data.pop("code", None)
    parts = update.callback_query.data.split(":")
    if not code:
        return await show_menu(update, ctx, edit=True)
    if parts[1] == "new":
        await strip_kb(update)
        return await redeem_new(update, ctx, code)
    bid = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    if db.member_role(bid, uid) != "owner":
        return await show_menu(update, ctx, edit=True)
    res = db.redeem_invite(code)
    if not res:
        await edit_or_say(update, tx(ctx, "code_bad"))
        return CODE
    plan, days = res
    db.extend_brand(bid, days, plan)
    b = db.get_brand(bid)
    db.log_event("code_extend", uid, bid, plan=plan, days=days)
    await edit_or_say(update, tx(ctx, "code_extended", brand=esc(brand_label(ctx, b)),
                                 until=fmt_day(b["plan_until"], ctx.user_data.get("lang", "ru"))))
    db.set_active_brand(uid, bid)
    return await show_menu(update, ctx)


async def do_join(update, ctx, token):
    u = update.effective_user
    b = db.brand_by_token(token)
    if not b:
        await say(update, tx(ctx, "join_bad"), kind="notice", ttl=60)
        return await show_menu(update, ctx)
    name = esc(brand_label(ctx, b))
    if db.member_role(b["id"], u.id):
        db.set_active_brand(u.id, b["id"])
        await say(update, tx(ctx, "join_ok", brand=name), kind="notice")
        return await show_menu(update, ctx)
    in_sub = any(db.member_role(x, u.id) for x in db.sub_brand_ids(b["id"]))
    if not in_sub and db.sub_member_count(b["id"]) >= plan_limits(b)["members"]:
        await say(update, tx(ctx, "join_full", brand=name), kind="notice", ttl=60)
        return await show_menu(update, ctx)
    db.add_member(b["id"], u.id, "editor")
    db.set_active_brand(u.id, b["id"])
    db.log_event("join", u.id, b["id"])
    await say(update, tx(ctx, "join_ok", brand=name), kind="notice")
    owner = db.get_user(b["owner_id"]) or {}
    who = esc(u.full_name) + (f" (@{esc(u.username)})" if u.username else "")
    try:
        await ctx.bot.send_message(b["owner_id"], _t(owner.get("lang", "ru"), "join_notify", who=who, brand=name),
                                   parse_mode=HTML)
    except TelegramError:
        pass
    return await show_menu(update, ctx)


# ============ Команда ============
def person_name(r):
    return r.get("name") or (("@" + r["username"]) if r.get("username") else f"ID {r['tg_id']}")


async def show_team(update, ctx, b, edit=False):
    if b["role"] != "owner":
        await say(update, tx(ctx, "team_owner_only"), kind="notice")
        return MENU
    link = f"https://t.me/{ctx.bot.username}?start=j_{b['join_token']}"
    text = tx(ctx, "team_head", brand=esc(brand_label(ctx, b)), n=db.sub_member_count(b["id"]),
              limit=plan_limits(b)["members"], link=link)
    stats = db.team_stats(b["id"])
    text += tx(ctx, "team_activity")
    marks = {"owner": tx(ctx, "mark_owner"), "designer": tx(ctx, "mark_designer")}
    for r in stats:
        text += "\n• " + tx(ctx, "team_line", name=esc(person_name(r)), photos=r["photos_month"], posts=r["posts_month"],
                            role=marks.get(r["role"], ""))
    others = [r for r in stats if r["role"] != "owner"]
    if not others:
        text += "\n" + tx(ctx, "team_empty")
    rows = [[Btn(person_name(r)[:30], callback_data=f"tm:{r['tg_id']}")] for r in others]
    rows += [[Btn(tx(ctx, "b_team_new"), callback_data="team:new")],
             [Btn(tx(ctx, "b_menu"), callback_data="menu:home")]]
    kb = KB(rows)
    await (edit_or_say(update, text, kb) if edit else say(update, text, kb, kind="menu"))
    return MENU


async def on_team_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Кнопки экрана «Команда»: новая ссылка, карточка участника, роль, удаление."""
    L(ctx, update)
    q = update.callback_query
    b, _ = current_brand(update.effective_user.id)
    if not b or b["role"] != "owner":
        await on_stale(update, ctx)
        return
    parts = q.data.split(":")
    if parts[0] == "team":
        if parts[1] == "new":
            db.regen_token(b["id"])
            b, _ = current_brand(update.effective_user.id)
        await answer(update)
        await show_team(update, ctx, b, edit=True)
        return
    uid = int(parts[1]) if parts[1].isdigit() else 0
    r = next((x for x in db.team_stats(b["id"]) if x["tg_id"] == uid and x["role"] != "owner"), None)
    if not r:
        await answer(update)
        await show_team(update, ctx, b, edit=True)
        return
    name = esc(person_name(r))
    brand = esc(brand_label(ctx, b))
    back = [Btn(tx(ctx, "b_team_back"), callback_data="team:show")]
    act = parts[2] if len(parts) > 2 else ""
    if act == "del":
        await answer(update)
        await edit_or_say(update, tx(ctx, "member_del_confirm", name=name, brand=brand),
                          KB([[Btn(tx(ctx, "b_member_del_yes"), callback_data=f"tm:{uid}:delyes")], back]))
        return
    if act == "delyes":
        db.remove_member(b["id"], uid)
        db.regen_token(b["id"])
        await sync_menu_button(ctx.bot, uid)
        await answer(update, tx(ctx, "member_removed"), alert=True)
        lang = (db.get_user(uid) or {}).get("lang", "ru")
        try:
            await ctx.bot.send_message(uid, _t(lang, "removed_notify", brand=brand), parse_mode=HTML)
        except TelegramError:
            pass
        b, _ = current_brand(update.effective_user.id)
        await show_team(update, ctx, b, edit=True)
        return
    if act in ("designer", "editor"):
        db.set_member_role(b["id"], uid, act)
        await answer(update, tx(ctx, "role_changed"))
        await sync_menu_button(ctx.bot, uid)
        if act == "designer":
            lang = (db.get_user(uid) or {}).get("lang", "ru")
            try:
                await ctx.bot.send_message(uid, _t(lang, "designer_notify", brand=brand), parse_mode=HTML)
            except TelegramError:
                pass
        r = next((x for x in db.team_stats(b["id"]) if x["tg_id"] == uid), r)
    else:
        await answer(update)
    never = tx(ctx, "never")
    role = tx(ctx, "role_designer") if r["role"] == "designer" else tx(ctx, "role_editor")
    text = tx(ctx, "member_card", name=name, user=(" @" + esc(r["username"])) if r.get("username") else "", role=role,
              joined=fmt_date(r.get("joined_at")) if r.get("joined_at") else "—",
              pm=r["photos_month"], po=r["posts_month"], pt=r["photos_total"],
              last=fmt_date(r["last_post"]) if r.get("last_post") else never,
              seen=fmt_date(r["last_seen"]) if r.get("last_seen") else never)
    role_btn = Btn(tx(ctx, "b_make_editor"), callback_data=f"tm:{uid}:editor") if r["role"] == "designer" else \
        Btn(tx(ctx, "b_make_designer"), callback_data=f"tm:{uid}:designer")
    await edit_or_say(update, text, KB([[role_btn], [Btn(tx(ctx, "b_member_del"), callback_data=f"tm:{uid}:del")], back]))


# ============ Настройка стиля: название → логотип → цвет → три стиля на фото ============
def step(ctx, n):
    return tx(ctx, "step", n=n) if ctx.user_data.get("wiz") else ""


def kit_bid(ctx, uid=None):
    """Какой бренд сейчас настраивается. После перезапуска без сохранённого ключа —
    текущий бренд, если человек может его редактировать."""
    bid = ctx.user_data.get("kit_bid")
    if bid or uid is None:
        return bid
    b, _ = current_brand(uid)
    if b and is_editor(b):
        ctx.user_data["kit_bid"] = b["id"]
        return b["id"]
    return None


async def ask_name(update, ctx):
    wiz_touch(ctx)
    await edit_or_say(update, step(ctx, 1) + tx(ctx, "ask_name"))
    return K_NAME


async def on_name(update, ctx):
    L(ctx, update)
    if wiz_stale(ctx):
        return await wiz_expired(update, ctx)
    if await maybe_quick(update, ctx):
        return ConversationHandler.END
    bid = kit_bid(ctx, update.effective_user.id)
    if not bid:
        return await show_menu(update, ctx)
    name = (update.message.text or "").strip()
    if not 1 <= len(name) <= 40:
        return await wiz_reask(update, ctx, K_NAME, tx(ctx, "name_bad"))
    db.update_kit(bid, name=name)
    db.log_event("kit_name", update.effective_user.id, bid)
    wiz_touch(ctx)
    await say(update, step(ctx, 2) + tx(ctx, "ask_logo"))
    return K_LOGO


def swatch_png(colors):
    """Картинка с найденными цветами: круги с номерами на светлом и тёмном фоне."""
    n = len(colors)
    W, H = 240 * n + 40, 300
    im = Image.new("RGB", (W, H), (245, 244, 240))
    d = ImageDraw.Draw(im)
    d.rectangle((0, H // 2, W, H), fill=(22, 22, 24))
    font = ImageFont.truetype(os.path.join(R.FONT_DIR, "Inter.ttf"), 44) if os.path.exists(
        os.path.join(R.FONT_DIR, "Inter.ttf")) else ImageFont.load_default(44)
    for i, c in enumerate(colors):
        cx = 40 + i * 240 + 100
        rgb = R.hex_rgb(c)
        d.ellipse((cx - 90, 60, cx + 90, 240), fill=rgb)
        ink = (255, 255, 255) if R.luma(*rgb) < 150 else (20, 20, 20)
        d.text((cx, 150), str(i + 1), font=font, fill=ink, anchor="mm")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


async def on_logo(update, ctx):
    L(ctx, update)
    if wiz_stale(ctx):
        return await wiz_expired(update, ctx)
    bid = kit_bid(ctx, update.effective_user.id)
    if not bid:
        return await show_menu(update, ctx)
    data, as_photo = await get_file_bytes(update, ctx)
    if not data:
        return K_LOGO
    try:
        png, had_alpha = await run(R.prepare_logo, data)
    except R.TooBig:
        await say(update, tx(ctx, "logo_big"))
        return K_LOGO
    except ValueError:
        await say(update, tx(ctx, "logo_empty"))
        return K_LOGO
    except Exception as e:
        logger.info("logo open failed: %s", e)
        await say(update, tx(ctx, "logo_bad"))
        return K_LOGO
    db.set_asset(bid, "logo", png)
    db.log_event("kit_logo", update.effective_user.id, bid, vector=R.is_svg(data) or R.is_pdf(data))
    if as_photo:
        await say(update, tx(ctx, "logo_photo"), kind="notice", ttl=40)
    elif not had_alpha:
        await say(update, tx(ctx, "logo_bg"), kind="notice", ttl=40)
    return await ask_color(update, ctx, png)


async def ask_color(update, ctx, png, note=None):
    wiz_touch(ctx)
    try:
        colors = await run(R.logo_colors, png)
    except Exception:
        colors = []
    ctx.user_data["wiz_colors"] = colors
    pre = (note + "\n\n") if note else ""
    skip = Btn(tx(ctx, "b_skip"), callback_data="wz:c:skip")
    if colors:
        rows = [[Btn(tx(ctx, "b_color_n", n=i + 1), callback_data=f"wz:c:{i}") for i in range(len(colors))],
                [Btn(tx(ctx, "b_color_own"), callback_data="wz:c:own"), skip]]
        m = await update.effective_chat.send_photo(io.BytesIO(swatch_png(colors)),
                                                   caption=pre + step(ctx, 3) + tx(ctx, "ask_color_found"),
                                                   parse_mode=HTML, reply_markup=KB(rows))
        track(m, "prompt")
    else:
        await say(update, pre + step(ctx, 3) + tx(ctx, "ask_color_none"), KB([[skip]]))
    return K_COLOR


def set_accent(bid, color):
    kit = db.get_brand(bid)["kit"]
    pal = S.sanitize_palette(kit.get("palette"))
    pal[2] = color.upper()
    db.update_kit(bid, palette=pal)


async def on_color_cb(update, ctx):
    L(ctx, update)
    if wiz_stale(ctx):
        return await wiz_expired(update, ctx)
    await answer(update)
    arg = update.callback_query.data.split(":")[2]
    bid = kit_bid(ctx, update.effective_user.id)
    if not bid:
        return await show_menu(update, ctx)
    colors = ctx.user_data.get("wiz_colors") or []
    if not colors and arg.isdigit():          # после перезапуска бота — считаем цвета заново
        logo = db.get_asset(bid, "logo")
        colors = (await run(R.logo_colors, logo)) if logo else []
    if arg == "own":
        await drop_prompt(update, ctx)
        await say(update, step(ctx, 3) + tx(ctx, "ask_color_hex"), KB([[Btn(tx(ctx, "b_skip"), callback_data="wz:c:skip")]]))
        return K_COLOR
    if arg.isdigit() and int(arg) < len(colors):
        set_accent(bid, colors[int(arg)])
        db.log_event("kit_color", update.effective_user.id, bid, source="logo")
    await drop_prompt(update, ctx)
    return await ask_photo(update, ctx)


async def on_color_text(update, ctx):
    L(ctx, update)
    if wiz_stale(ctx):
        return await wiz_expired(update, ctx)
    if await maybe_quick(update, ctx):
        return ConversationHandler.END
    m = HEX_RE.match((update.message.text or "").strip())
    if not m:
        return await wiz_reask(update, ctx, K_COLOR, tx(ctx, "color_bad"))
    bid = kit_bid(ctx, update.effective_user.id)
    if not bid:
        return await show_menu(update, ctx)
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    set_accent(bid, "#" + h)
    db.log_event("kit_color", update.effective_user.id, bid, source="hex")
    return await ask_photo(update, ctx)


async def ask_photo(update, ctx, note=None):
    wiz_touch(ctx)
    await say(update, ((note + "\n\n") if note else "") + step(ctx, 4) + tx(ctx, "ask_photo"),
              KB([[Btn(tx(ctx, "b_skip"), callback_data="wz:skip")]]))
    return K_PHOTO


async def wiz_reask(update, ctx, state, note=None):
    """Шаг мастера получил не то, что ждал: тот же вопрос ещё раз — с короткой подсказкой сверху.
    Так в чате всегда виден вопрос текущего шага, а ответ не уходит в посты."""
    wiz_touch(ctx)
    pre = (note + "\n\n") if note else ""
    if state == K_NAME:
        await say(update, pre + step(ctx, 1) + tx(ctx, "ask_name"))
    elif state == K_LOGO:
        await say(update, pre + step(ctx, 2) + tx(ctx, "ask_logo"))
    elif state == K_COLOR:
        bid = kit_bid(ctx, update.effective_user.id)
        logo = db.get_asset(bid, "logo") if bid else None
        if not logo:                                   # логотипа нет (удалили в редакторе) — шаг логотипа
            await say(update, pre + step(ctx, 2) + tx(ctx, "ask_logo"))
            return K_LOGO
        return await ask_color(update, ctx, logo, note=note)
    elif state == K_PHOTO:
        if ctx.user_data.get("wiz_album"):            # три стиля уже показаны — ждём выбор кнопкой
            await say(update, note or tx(ctx, "wiz_pick_hint"), kind="notice")
        else:
            return await ask_photo(update, ctx, note=note)
    return state


def _wiz_other(state):
    """Запасной обработчик шага: стикер, видео, текст вместо картинки и наоборот."""
    notes = {K_NAME: "wiz_need_name", K_LOGO: "wiz_need_logo", K_COLOR: "wiz_need_color", K_PHOTO: "wiz_need_photo"}

    async def handler(update, ctx):
        L(ctx, update)
        if wiz_stale(ctx):
            return await wiz_expired(update, ctx)
        m = update.message
        if m is None:
            return state
        if m.text and await maybe_quick(update, ctx):   # ответ на вопрос пульта («Текст», «Своя рубрика»)
            return ConversationHandler.END
        if state == K_COLOR and m.document:
            return await on_logo(update, ctx)            # прислали логотип файлом (как советовали) — замена
        note = tx(ctx, "wiz_photo_is_post") if (state == K_COLOR and m.photo) else tx(ctx, notes[state])
        if state == K_PHOTO and ctx.user_data.get("wiz_album"):
            note = tx(ctx, "wiz_pick_hint")
        return await wiz_reask(update, ctx, state, note)
    handler.__name__ = f"on_wiz_other_{state}"
    return handler


def brand_traits(bid):
    """Признаки бренда для стартовых стилей: многоцветный логотип остаётся в своих цветах,
    ширина логотипа — по его пропорциям (spec.brand_traits). Никогда не падает."""
    try:
        b = db.get_brand(bid) or {}
        return S.brand_traits(db.get_asset(bid, "logo"), (b.get("kit") or {}).get("palette"))
    except Exception:
        logger.exception("brand_traits")
        return None


def _onboard_job(bid, data, title, tag, lang):
    """Три стиля на фото клиента → [(key, jpeg)] для альбома."""
    ctx = web.brand_ctx(bid, dict(title=title, subtitle="", hashtag=tag, i=1, n=1))
    photo = R.open_photo(data)
    W, H = 1080, 1350
    out = []
    traits = brand_traits(bid)
    for key in S.ONBOARD_PRESETS:
        spec = S.preset_spec(key, traits)
        ctx.notes = set()
        out.append((key, R.to_preview(R.render_surface(photo, W, H, spec["feed"]["layers"], ctx), 1080)))
    return out


async def on_wiz_photo(update, ctx):
    L(ctx, update)
    if wiz_stale(ctx):
        return await wiz_expired(update, ctx)
    bid = kit_bid(ctx, update.effective_user.id)
    if not bid:
        return await show_menu(update, ctx)
    msg = update.message
    gid = msg.media_group_id
    fuid = msg.photo[-1].file_unique_id if msg.photo else (msg.document.file_unique_id if msg.document else None)
    if ctx.user_data.get("wiz_album") or ctx.user_data.get("wiz_gid"):
        # три стиля уже показаны (или рисуются): остальные фото того же альбома и то же фото
        # ещё раз не множат примеры. Новое фото — заменяет прежние примеры.
        if (gid and gid == ctx.user_data.get("wiz_gid")) or (fuid and fuid == ctx.user_data.get("wiz_fuid")):
            return K_PHOTO
    chat_id = update.effective_chat.id
    data, _ = await get_file_bytes(update, ctx)
    if not data or not is_image(data):
        if ctx.user_data.get("wiz_album"):
            await say(update, tx(ctx, "photo_bad"), kind="notice")
            return K_PHOTO
        return await ask_photo(update, ctx, note=tx(ctx, "photo_bad"))
    old = ctx.user_data.pop("wiz_album", None)
    if old:
        await delete_ids(ctx.bot, chat_id, old)
    ctx.user_data["wiz_gid"], ctx.user_data["wiz_fuid"] = gid, fuid
    lang = ctx.user_data.get("lang", "ru")
    title, subtitle, tag, tags, _ = parse_text(marked(msg.caption, msg.caption_entities))
    wait = await say(update, tx(ctx, "styles_wait"), kind="keep")
    try:
        data = await run(working_copy, data)
        db.set_asset(bid, "sample", await run(web._prep_sample, data))
        shots = await run(_onboard_job, bid, data, image_text(title) or tx(ctx, "style_sample_title"),
                          tag or ("#рубрика" if lang == "ru" else "#section"), lang)
    except Exception as e:
        logger.exception("onboard render: %s", e)
        ctx.user_data.pop("wiz_gid", None)
        ctx.user_data.pop("wiz_fuid", None)
        await delete_ids(ctx.bot, chat_id, [wait.message_id])
        return await ask_photo(update, ctx, note=tx(ctx, "photo_bad"))
    media = [InputMediaPhoto(io.BytesIO(jpg), caption=f"{i + 1} · {S.preset_name(key, lang)}")
             for i, (key, jpg) in enumerate(shots)]
    msgs = await tg_call(ctx.bot.send_media_group, chat_id, media)
    await delete_ids(ctx.bot, chat_id, [wait.message_id])
    rows = [[Btn(f"{i + 1} · {S.preset_name(key, lang)}", callback_data=f"wz:s:{key}")]
            for i, (key, _) in enumerate(shots)]
    pick = await say(update, tx(ctx, "styles_pick"), KB(rows), kind="keep")
    ctx.user_data["wiz_album"] = [m.message_id for m in msgs] + [pick.message_id]
    return K_PHOTO


async def on_style_pick(update, ctx):
    L(ctx, update)
    if wiz_stale(ctx):
        return await wiz_expired(update, ctx)
    await answer(update)
    bid = kit_bid(ctx, update.effective_user.id)
    key = update.callback_query.data.split(":")[2]
    if not bid or not S.preset(key):
        return await show_menu(update, ctx)
    lang = ctx.user_data.get("lang", "ru")
    pick_starter_style(bid, key, lang, update.effective_user.id)
    db.log_event("onboard_style", update.effective_user.id, bid, style=key)
    chat_id = update.effective_chat.id
    await delete_ids(ctx.bot, chat_id, (ctx.user_data.pop("wiz_album", None) or []) + [update.callback_query.message.message_id])
    return await finish_wizard(update, ctx, S.preset_name(key, lang))


def _same_spec(a, b):
    """Стили совпадают с точностью до id слоёв (они случайные при каждом создании)."""
    def norm(x):
        if isinstance(x, dict):
            return {k: norm(v) for k, v in x.items() if k != "id"}
        if isinstance(x, (list, tuple)):
            return [norm(v) for v in x]
        return x
    try:
        return json.dumps(norm(a), sort_keys=True, ensure_ascii=False) == \
            json.dumps(norm(b), sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return False


def pick_starter_style(bid, key, lang, uid=None):
    """Выбранный в мастере стиль — первым, без копий. Стартовые стили, которые уже есть
    (их добавляет первый пост, если настройку бросили, или повторный выбор), не дублируются:
    нетронутый — встаёт первым, изменённый в редакторе — остаётся как есть."""
    tpls = db.list_templates(bid)
    had = bool(tpls)
    traits = brand_traits(bid)
    names = {S.preset_name(k, l) for k in S.ONBOARD_PRESETS for l in ("ru", "en")}
    want = json.loads(json.dumps(S.preset_spec(key, traits)))
    name = S.preset_name(key, lang)
    same = [t for t in tpls if t["name"] in (S.preset_name(key, "ru"), S.preset_name(key, "en"))]
    tid = None
    for t in same:
        if _same_spec(t["spec"], want):
            db.delete_template(bid, t["id"])           # нетронутая копия — пересоздаём первой
        elif tid is None:
            tid = t["id"]                              # её правили в редакторе — не трогаем
    if tid is None:
        tid = db.create_template(bid, name, S.preset_spec(key, traits), first=True)
    if not had:
        for other in S.ONBOARD_PRESETS:
            if other != key:
                db.create_template(bid, S.preset_name(other, lang), S.preset_spec(other, traits))
    else:                                              # остальные стартовые — по одной копии
        seen = set()
        for t in db.list_templates(bid):
            if t["name"] in names and t["id"] != tid:
                k = t["name"]
                if k in seen and any(_same_spec(t["spec"], json.loads(json.dumps(S.preset_spec(o, traits))))
                                     for o in S.ONBOARD_PRESETS):
                    db.delete_template(bid, t["id"])
                seen.add(k)
    if uid and isinstance(tid, int):
        db.set_prefs(uid, bid, tid=tid)
    return tid


async def on_wiz_skip(update, ctx):
    L(ctx, update)
    if wiz_stale(ctx):
        return await wiz_expired(update, ctx)
    await answer(update)
    bid = kit_bid(ctx, update.effective_user.id)
    if not bid:
        return await show_menu(update, ctx)
    await drop_prompt(update, ctx)
    lang = ctx.user_data.get("lang", "ru")
    if db.template_count(bid) == 0:
        db.seed_templates(bid, lang)
    db.log_event("onboard_style", update.effective_user.id, bid, style="skip")
    first = (db.list_templates(bid) or [{"name": "—"}])[0]["name"]
    return await finish_wizard(update, ctx, first)


async def finish_wizard(update, ctx, style_name):
    bid = kit_bid(ctx, update.effective_user.id)
    t0 = ctx.user_data.get("wiz_t0")
    db.log_event("onboard_done", update.effective_user.id, bid, secs=round(time.time() - t0) if t0 else None)
    reset_session(ctx)
    if bid:
        db.set_active_brand(update.effective_user.id, bid)
    return await show_menu(update, ctx, note=tx(ctx, "wiz_done", style=esc(style_name)))


# ============ Пост: фото → превью с пультом ============
# Человек присылает фото (одно или альбомом) с подписью. Бот сразу отвечает
# превью по последнему шаблону и формату этого человека; под превью — пульт.
# Любая правка перерисовывает превью на месте. «Файлы» отдают готовые JPG
# альбомом, следом — текст поста; пульт появляется снова под ними.
DRAFT_TTL = 180          # сек: фото без подписи в этот срок дополняют текущий пост
SPLIT_GAP = 10           # сек: следующий альбом без подписи в этот срок — продолжение карусели
REFRESH_DELAY = 1.2      # сек: ждём остальные фото альбома
TAG_RE = re.compile(r"#[\w\-]+", re.U)
TEXT_MAX = 300
ZOOM_STEP, ZOOM_MAX = 0.25, 3.0


def marked(text, entities):
    """Жирный текст из Telegram → «*…*»: так выделение доходит до шаблона (стиль em слоя).
    Смещения сущностей в Telegram — в единицах UTF-16."""
    text = text or ""
    ents = [e for e in (entities or []) if str(getattr(e, "type", "")) in ("bold", "MessageEntityType.BOLD")]
    if not ents:
        return text
    u = text.encode("utf-16-le")
    cuts = {}
    for e in ents:
        a, b = e.offset, e.offset + e.length
        while b > a and u[2 * (b - 1):2 * b] in (b" \x00", b"\n\x00"):   # хвостовые пробелы — вне выделения
            b -= 1
        if b > a:
            cuts[a] = cuts.get(a, "") + "*"
            cuts[b] = "*" + cuts.get(b, "")
    out, prev = [], 0
    for k in sorted(cuts):
        out.append(u[2 * prev:2 * k].decode("utf-16-le"))
        out.append(cuts[k])
        prev = k
    out.append(u[2 * prev:].decode("utf-16-le"))
    return "".join(out)


def unmark(s):
    """Текст поста для канала — без звёздочек разметки."""
    return R.parse_marks(s or "")[0]


def parse_text(text, fields=None):
    """Подпись → (заголовок, подзаголовок, рубрика|None, все хештеги, обрезано ли).
    Пустая строка отделяет подзаголовок. Если в шаблоне есть подзаголовок,
    а пустой строки нет — первая строка заголовок, остальное подзаголовок."""
    fields = fields or set()
    text = text or ""
    tags = uniq(t[:31] for t in TAG_RE.findall(text))
    body = TAG_RE.sub("", text)
    body = "\n".join(re.sub(r"[ \t]+", " ", ln).strip() for ln in body.split("\n")).strip()
    title = subtitle = ""
    if body:
        paras = re.split(r"\n\s*\n", body, maxsplit=1)
        if len(paras) == 2:
            title, subtitle = paras[0].strip(), paras[1].strip()
        elif "subtitle" in fields and "\n" in body:
            title, subtitle = body.split("\n", 1)
        else:
            title = body
    # Подпись хранится целиком: в тексте поста и в подписи альбома — полностью.
    # Укорачивается только то, что рисуется на картинке (image_text).
    cut = len(title.strip()) > TEXT_MAX or len(subtitle.strip()) > TEXT_MAX
    return title.strip(), subtitle.strip(), (tags[0] if tags else None), tags, cut


# Эмодзи на картинке шрифты не рисуют (выходят пустые квадраты) — на картинке их нет,
# в тексте поста они остаются. Вместе с ними уходят невидимые склейки: FE0F, ZWJ, тоны кожи.
EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U0001FC00-\U0001FFFF\u2600-\u27BF\u2B05-\u2B07\u2B1B\u2B1C\u2B50\u2B55"
    "\u231A\u231B\u2328\u23CF\u23E9-\u23F3\u23F8-\u23FA\u3030\u303D\u3297\u3299"
    "\uFE0E\uFE0F\u200D\u20E3\U000E0020-\U000E007F]"
    "|[\u00A9\u00AE\u203C\u2049\u2122\u2139\u2194-\u2199\u21A9\u21AA\u24C2\u25AA\u25AB\u25B6\u25C0\u25FB-\u25FE]"
    "(?=\uFE0F)")


def strip_emoji(s):
    if not s:
        return s or ""
    s = EMOJI_RE.sub("", s)
    return "\n".join(re.sub(r"[ \t]{2,}", " ", ln).strip() for ln in s.split("\n")).strip()


def image_text(s, limit=None):
    """Текст для картинки: без эмодзи и не длиннее limit — по границе слова, с многоточием."""
    limit = limit or TEXT_MAX
    s = strip_emoji(s or "")
    if len(s) <= limit:
        return s
    cut = s[:limit]
    sp = max(cut.rfind(" "), cut.rfind("\n"))
    if sp > limit * 0.6:
        cut = cut[:sp]
    cut = cut.rstrip(" \n,.;:—–-")
    if len(re.findall(r"(?<!\\)\*", cut)) % 2:      # выделение «*…*» оборвалось — закрываем
        cut += "*"
    return cut + "…"


def utf16_len(s) -> int:
    """Длина так, как её считает Telegram (единицы UTF-16)."""
    return len((s or "").encode("utf-16-le")) // 2


def draft(ctx, uid=None):
    d = ctx.user_data.get("q")
    if d is None and uid is not None:
        d = drafts.load(uid)
        if d:
            ctx.user_data["q"] = d
    return d


def save_draft(ctx, uid):
    d = ctx.user_data.get("q")
    if d:
        drafts.save(uid, d)


def _tpl(d):
    return db.get_template(d["bid"], d["tid"])


def _focus(d, i):
    f = (d.get("focus") or {}).get(str(i))
    return tuple(f) if f else None


def _zoom(d, i):
    return float((d.get("zoom") or {}).get(str(i)) or 1.0)


def _ctx_for(base, d, i, n, dark):
    return R.Ctx(base.palette, base.logos, base.customs,
                 dict(title=image_text(d.get("title", "")), subtitle=image_text(d.get("subtitle", "")),
                      hashtag=d.get("tag") or "", i=i, n=n), dark, base.images, focus=_focus(d, i - 1),
                 zoom=_zoom(d, i - 1))


def surface(spec, fmt):
    """Какие слои рисовать → (слои, это сторис). Формат 9:16 у стиля со слоями сторис —
    поверхность сторис с её безопасными зонами, а не лента, растянутая в 9:16. Слои сторис
    есть у всех стартовых стилей, даже когда выдача сторис к каждому фото выключена;
    редактор по кнопке 9:16 открывает ту же поверхность."""
    st = spec.get("story") or {}
    if fmt == "9:16" and (st.get("enabled") or st.get("layers")) and st.get("layers"):
        return st.get("layers"), True
    return (spec.get("feed") or {}).get("layers") or [], False


def _surface_size(photo, fmt, story):
    return R.STORY_SIZE if story else R.feed_size(photo, fmt)


def _render_job(path, spec, fmt, ctx, out_dir, stem):
    """Рендер одного фото: лента (+ сторис) в файлы → [(suffix, path)]."""
    photo = R.open_photo(drafts.read(path))
    out = []
    layers, story = surface(spec, fmt)
    shots = [("feed", R.render_surface(photo, *R.STORY_SIZE, layers, ctx))] if story else \
        R.render_template(photo, spec, fmt, ctx)
    for suf, im in shots:
        p = os.path.join(out_dir, f"{stem}{'' if suf == 'feed' else '_story'}.jpg")
        with open(p, "wb") as f:
            f.write(R.to_jpeg(im))
        out.append((suf, p))
    return out


def _preview_job(path, spec, fmt, ctx):
    photo = R.open_photo(drafts.read(path))
    layers, story = surface(spec, fmt)
    W, H = _surface_size(photo, fmt, story)
    img = R.render_surface(photo, W, H, layers, ctx)
    cw, ch = R.photo_box(layers, W, H, int(ctx.fields.get("i") or 1))   # кадр или рамка фото
    share = R.crop_share(photo.width, photo.height, cw, ch)
    horiz = photo.width / photo.height > cw / ch
    return R.to_preview(img), sorted(ctx.notes), share, horiz


def fmt_label(ctx, fmt):
    return tx(ctx, "fmt_orig") if fmt == "orig" else fmt


def post_text(d):
    """Текст поста для копирования: заголовок, подзаголовок и хештеги — через типограф."""
    parts = [unmark(typograf(d.get("title") or "")), unmark(typograf(d.get("subtitle") or ""))]
    tags = uniq([d.get("tag")] + list(d.get("tags") or []))
    parts.append(" ".join(tags))
    return "\n\n".join(p for p in parts if p)


def pult_caption(ctx, d, tp, notes=(), share=0.0):
    fields = R.spec_fields(tp["spec"])
    n = len(d["photos"])
    bits = [f"<b>{esc(tp['name'])}</b>", fmt_label(ctx, d["fmt"])]
    if "hashtag" in fields:
        bits.append(esc(d.get("tag") or tx(ctx, "q_no_tag")))
    line2 = tx(ctx, "q_photos", n=n) + (tx(ctx, "tpl_story") if tp["spec"]["story"].get("enabled")
                                        and d["fmt"] != "9:16" else "")
    if n > 1:
        line2 += " · " + tx(ctx, "q_frame", i=d.get("cur", 0) + 1, n=n)
    lines = [" · ".join(bits), line2]
    if "title_cut" in notes or d.get("cut"):
        lines.append(tx(ctx, "q_title_cut"))
    elif "title_small" in notes:
        lines.append(tx(ctx, "q_title_small"))
    if share > 0.03:
        lines.append(tx(ctx, "q_crop", pct=round(share * 100)))
    if "title" in fields and not d.get("title"):
        lines.append(tx(ctx, "q_no_title"))
    return "\n".join(lines)


def pult_kb(ctx, d, tp, share=0.0, horiz=False):
    fields = R.spec_fields(tp["spec"])
    name = tp["name"] if len(tp["name"]) <= 14 else tp["name"][:13] + "…"
    row = [Btn(tx(ctx, "b_q_style", name=name), callback_data="q:tpl"),
           Btn(tx(ctx, "b_q_fmt", fmt=fmt_label(ctx, d["fmt"])), callback_data="q:fmt")]
    if "hashtag" in fields:
        row.append(Btn(d.get("tag") or tx(ctx, "q_rubric"), callback_data="q:tag"))
    rows = [row]
    if R.spec_has_shade(tp["spec"]):
        last = len(R.DARK_STEPS) - 1
        rows.append([Btn(tx(ctx, "b_lighter") if d["dark"] > 0 else "·", callback_data="q:lighter"),
                     Btn(tx(ctx, "b_darker") if d["dark"] < last else "·", callback_data="q:darker")])
    n = len(d["photos"])
    if n > 1:
        cur = d.get("cur", 0)
        rows.append([Btn("‹", callback_data="q:prev"), Btn(f"{cur + 1} / {n}", callback_data="q:noop"),
                     Btn("›", callback_data="q:next")])
    if share > 0.03:
        f = _focus(d, d.get("cur", 0)) or (0.5, 0.5)
        v = f[0] if horiz else f[1]
        keys = ("b_focus_l", "b_focus_c", "b_focus_r") if horiz else ("b_focus_t", "b_focus_c", "b_focus_b")
        rows.append([Btn(("✓ " if abs(v - val) < 0.01 else "") + tx(ctx, k), callback_data=f"q:fc:{val}")
                     for k, val in zip(keys, (0.0, 0.5, 1.0))])
    z = _zoom(d, d.get("cur", 0))
    rows.append([Btn("−" if z > 1 else "·", callback_data="q:zm:-"), Btn(tx(ctx, "b_zoom", z=f"{z:g}"), callback_data="q:noop"),
                 Btn("+" if z < ZOOM_MAX else "·", callback_data="q:zm:+")])
    rows.append([Btn(tx(ctx, "b_q_text"), callback_data="q:text")])
    if d.get("sent"):
        rows.append([Btn(tx(ctx, "b_q_again", n=n), callback_data="q:send"),
                     Btn(tx(ctx, "b_q_channel"), callback_data="q:chan")])
        rows.append([Btn(tx(ctx, "b_q_done"), callback_data="q:done")])
    else:
        rows.append([Btn(tx(ctx, "b_q_send", n=n), callback_data="q:send"),
                     Btn(tx(ctx, "b_q_cancel"), callback_data="q:done")])
    return KB(rows)


# Фоновые перерисовки живут вне user_data: PTB копирует user_data для сохранения,
# а задачу asyncio скопировать нельзя.
PULT_TASKS = {}      # tg_id → последняя запланированная перерисовка
PULT_GEN = {}        # tg_id → номер последнего запроса: устаревшие просто выходят
PULT_LOCK = {}       # tg_id → пульт рисуется строго по одному, без гонок и лишних сообщений


def _pult_lock(uid):
    return PULT_LOCK.setdefault(uid, asyncio.Lock())


def _photo_ok(path):
    """True — фото открывается; "big" — больше 100 Мп; False — не открывается."""
    try:
        R.open_photo(drafts.read(path))
        return True
    except R.TooBig:
        return "big"
    except Exception:
        return False


def drop_photo(d, k):
    """Убрать k-е фото из черновика; точки фокуса остальных сдвигаются."""
    path = d["photos"].pop(k)
    try:
        os.remove(path)
    except OSError:
        pass
    focus = {}
    for key, v in (d.get("focus") or {}).items():
        i = int(key)
        if i != k:
            focus[str(i if i < k else i - 1)] = v
    d["focus"] = focus
    zoom = {}
    for key, v in (d.get("zoom") or {}).items():
        i = int(key)
        if i != k:
            zoom[str(i if i < k else i - 1)] = v
    d["zoom"] = zoom
    d["cur"] = max(0, min(d.get("cur", 0), len(d["photos"]) - 1))


async def show_pult(bot, chat_id, ctx, uid, new=False, gen=None):
    async with _pult_lock(uid):
        if gen is not None and PULT_GEN.get(uid) != gen:
            return          # пока ждали, пришли новые фото — нарисует следующий запрос
        await _show_pult(bot, chat_id, ctx, uid, new, gen)


async def _show_pult(bot, chat_id, ctx, uid, new=False, gen=None):
    """Рисует превью текущего кадра и показывает или обновляет пульт."""
    d = draft(ctx, uid)
    if not d or not d["photos"]:
        return

    def still_current():
        # пока рендерили, человек мог начать новый пост или закрыть этот — тогда молча выходим
        return ctx.user_data.get("q") is d and (gen is None or PULT_GEN.get(uid) == gen)
    tp = _tpl(d)
    if not tp:
        tpls = db.list_templates(d["bid"])
        if not tpls:
            return
        tp = tpls[0]
        d["tid"] = tp["id"]
    lang = ctx.user_data.get("lang", "ru")
    base = await run(web.brand_ctx, d["bid"])       # свежий: правки в редакторе видны сразу
    while True:
        d["cur"] = max(0, min(d.get("cur", 0), len(d["photos"]) - 1))
        cur = d["cur"]
        c = _ctx_for(base, d, cur + 1, len(d["photos"]), R.DARK_STEPS[d["dark"]])
        try:
            data, notes, share, horiz = await run(_preview_job, d["photos"][cur], tp["spec"], d["fmt"], c)
            break
        except Exception:
            if not still_current():
                return
            why = await run(_photo_ok, d["photos"][cur])
            if why is True:
                raise                                # фото в порядке — ошибка в другом, пусть узнает админ
            if not still_current():
                return
            drop_photo(d, cur)                       # фото битое или огромное: убираем и говорим почему
            m = await bot.send_message(chat_id, _t(lang, "photo_too_big" if why == "big" else "photo_dropped", i=cur + 1))
            track(m, "notice", 60)
            if not d["photos"]:
                if d.get("msg"):
                    await delete_ids(bot, chat_id, [d["msg"]])
                ctx.user_data.pop("q", None)
                drafts.clear(uid)
                return
    if not still_current():
        return
    caption, kb = pult_caption(ctx, d, tp, notes, share), pult_kb(ctx, d, tp, share, horiz)
    db.log_event("preview", uid, d["bid"], draft=d["id"]) if not d.get("previewed") else None
    d["previewed"] = True
    if d.get("msg") and not new:
        try:
            await bot.edit_message_media(chat_id=chat_id, message_id=d["msg"],
                                         media=InputMediaPhoto(io.BytesIO(data), caption=caption, parse_mode=HTML),
                                         reply_markup=kb)
            save_draft(ctx, uid)
            return
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return
            logger.info("pult edit failed, sending new: %s", e)
    old = d.get("msg")
    m = await bot.send_photo(chat_id, io.BytesIO(data), caption=caption, parse_mode=HTML, reply_markup=kb)
    if ctx.user_data.get("q") is not d:            # пока отправляли, начат новый пост: этот пульт лишний
        await delete_ids(bot, chat_id, [m.message_id])
        return
    d["msg"] = m.message_id
    save_draft(ctx, uid)
    if old and old != m.message_id:
        await delete_ids(bot, chat_id, [old])    # старый пульт больше не нужен


def schedule_pult(update, ctx):
    """Перерисовать пульт чуть позже: ждём остальные фото альбома."""
    bot, chat_id, uid = ctx.bot, update.effective_chat.id, update.effective_user.id
    gen = PULT_GEN.get(uid, 0) + 1
    PULT_GEN[uid] = gen

    async def later():
        try:
            await asyncio.sleep(REFRESH_DELAY)
            if PULT_GEN.get(uid) == gen:
                await show_pult(bot, chat_id, ctx, uid, gen=gen)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.exception("pult: %s", e)
            await alert_admins(bot, e, uid, "перерисовка превью (пульт)")
            try:
                m = await bot.send_message(chat_id, _t(ctx.user_data.get("lang", "ru"), "error_user"))
                track(m, "notice", 60)
            except TelegramError:
                pass
    task = asyncio.create_task(later())
    PULT_TASKS[uid] = task
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


# ============ Стиль по образцу ============
# Образец разбирает модель (платно), подгонка — локально. Вызов модели идёт вне RENDER_SEM:
# 20–90 секунд ожидания провайдера не занимают места рендера других клиентов. Описание макета
# кэшируется по sha1 картинки — «Собрать ещё раз» и тот же образец повторно не платят второй раз.
# Расходы и лимиты — quota.py.
SAMPLE_DIR = os.path.join(db.DATA_DIR, "samples")


def _sample_path(uid):
    return os.path.join(SAMPLE_DIR, f"{uid}.img")


def _sample_cache_path(uid):
    return os.path.join(SAMPLE_DIR, f"{uid}.desc.json")


def _sample_cache_get(uid, sha):
    try:
        with open(_sample_cache_path(uid), encoding="utf-8") as f:
            c = json.load(f)
        return c["desc"] if c.get("sha1") == sha and isinstance(c.get("desc"), dict) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _sample_cache_put(uid, sha, desc):
    try:
        os.makedirs(SAMPLE_DIR, exist_ok=True)
        tmp = _sample_cache_path(uid) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"sha1": sha, "desc": desc}, f, ensure_ascii=False)
        os.replace(tmp, _sample_cache_path(uid))
    except (OSError, TypeError, ValueError) as e:
        logger.info("sample cache: %s", e)


def forget_samples(uids):
    """«Удалить мои данные» и удаление бренда: образец и описание макета тоже уходят с диска."""
    for uid in uids:
        for p in (_sample_path(uid), _sample_cache_path(uid), _sample_cache_path(uid) + ".tmp"):
            try:
                os.remove(p)
            except OSError:
                pass


def _sample_kb(ctx):
    return KB([[Btn(tx(ctx, "b_smp_save"), callback_data="smp:save")],
               [Btn(tx(ctx, "b_smp_again"), callback_data="smp:again"),
                Btn(tx(ctx, "b_smp_other"), callback_data="smp:start")]])


def _sample_image(data):
    return R.open_image(data).convert("RGB")


async def notify_admins(bot, text):
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(aid, text, parse_mode=HTML, disable_web_page_preview=True)
        except TelegramError:
            pass


async def sample_refusal(update, ctx, b):
    """Лимиты и бюджет «Стиля по образцу»: None — можно, иначе клиент уже получил ответ."""
    why = quota.check(b["id"])
    if not why:
        return None
    day, month = quota.keys()
    db.log_event("sample_refused", update.effective_user.id, b["id"], why=why)
    if why == "budget":
        await say(update, tx(ctx, "smp_budget", support=esc(SUPPORT)), kind="notice", ttl=60)
        if quota.once(f"stop:{month}"):
            await notify_admins(ctx.bot, f"<b>Стиль по образцу остановлен</b>: расход за месяц достиг бюджета "
                                         f"${quota.VISION_BUDGET_USD:.2f}. Клиенты видят «временно недоступен» до 1-го числа. "
                                         f"Поднять бюджет — переменная VISION_BUDGET_USD в Railway.")
    elif why == "day":
        await say(update, tx(ctx, "smp_limit_day"), kind="notice", ttl=60)
        if quota.once(f"day:{day}"):
            await notify_admins(ctx.bot, f"<b>Стиль по образцу</b>: на сегодня исчерпан общий лимит — "
                                         f"{quota.SAMPLE_PER_DAY} разборов (SAMPLE_PER_DAY). Завтра счётчик обнулится.")
    else:
        await say(update, tx(ctx, "smp_limit_brand", n=quota.SAMPLE_PER_BRAND_DAY), kind="notice", ttl=60)
    return why


async def _record_call(bot, uid, bid, meta):
    """Учесть платный вызов модели: счётчики, событие sample_call, предупреждения админам."""
    meta = meta if isinstance(meta, dict) else {}
    model = meta.get("model") or SMP.VISION_MODEL
    rec = await asyncio.to_thread(quota.record, bid, uid, model, meta.get("usage"))
    db.log_event("sample_call", uid, bid, model=model, tokens_in=rec["tokens_in"], tokens_out=rec["tokens_out"],
                 usd=rec["usd"], month_usd=rec["month_usd"], stop=meta.get("stop_reason"))
    budget = quota.VISION_BUDGET_USD
    if budget and rec["share"] >= 1 and quota.once(f"stop:{rec['month']}"):
        await notify_admins(bot, f"<b>Стиль по образцу остановлен</b>: расход за месяц ${rec['month_usd']:.2f} "
                                 f"из ${budget:.2f}. Следующие разборы — с 1-го числа или после увеличения "
                                 f"VISION_BUDGET_USD в Railway.")
    elif budget and rec["share"] >= quota.WARN_SHARE and quota.once(f"warn80:{rec['month']}"):
        await notify_admins(bot, f"<b>Стиль по образцу</b>: израсходовано ${rec['month_usd']:.2f} из ${budget:.2f} "
                                 f"месячного бюджета (80%). На 100% разбор остановится до 1-го числа.")


async def _sample_build(update, ctx, b, data):
    """Образец → стиль → «рядом» с кнопками. → True, если получилось.
    Ошибки — понятным текстом: сбой провайдера («сервис недоступен», админам — оповещение)
    отдельно от неудачного образца («пришлите другой»)."""
    uid = update.effective_user.id
    sha = hashlib.sha1(data).hexdigest()
    desc = _sample_cache_get(uid, sha)
    if desc is None and await sample_refusal(update, ctx, b):
        return None
    wait = await say(update, tx(ctx, "smp_wait"), kind="keep")
    try:
        base = await run(web.brand_ctx, b["id"])
        if desc is None:
            img = await run(_sample_image, data)
            meta, called = None, False
            try:
                desc = await asyncio.to_thread(SMP.ask_model, img)   # вне RENDER_SEM: ждём провайдера
                called = True
                meta = desc.pop("_meta", None) if isinstance(desc, dict) else None
            except SMP.SampleError as e:
                meta, called = e.meta, e.meta is not None
                raise
            finally:
                if called:
                    await _record_call(ctx.bot, uid, b["id"], meta)
            _sample_cache_put(uid, sha, desc)
        spec, fmt, _, jpg, notes, _ = await run(SMP.make, data, base, bool(base.logos), desc)
    except SMP.SampleError as e:
        logger.warning("образец: %s", e)
        db.log_event("sample_fail", uid, b["id"], code=e.code, status=e.status, provider=bool(e.provider))
        if e.code == "not_configured":
            await say(update, tx(ctx, "smp_off", support=esc(SUPPORT)), kind="notice")
            return None
        if e.provider:
            await say(update, tx(ctx, "smp_unavailable"), kind="notice", ttl=60)
            await alert_admins(ctx.bot, e, uid, "sample: провайдер модели")
            return None                      # сервис недоступен: ожидание образца снимается, следующее фото — пост
        await say(update, tx(ctx, "smp_fail"), kind="notice", ttl=60)
        return False
    except Exception as e:
        logger.exception("образец: %s", e)
        db.log_event("sample_fail", uid, b["id"], code="crash")
        await say(update, tx(ctx, "smp_fail"), kind="notice", ttl=60)
        await alert_admins(ctx.bot, e, uid, "sample: сборка стиля")
        return False
    finally:
        if wait:
            await delete_ids(ctx.bot, update.effective_chat.id, [wait.message_id] if hasattr(wait, "message_id") else [])
    ctx.user_data["smp"] = {"bid": b["id"], "spec": spec, "fmt": fmt}
    extra = ""
    if notes.get("weak") or notes.get("missed"):
        what = ", ".join(tx(ctx, "smp_role_" + r) for r in dict.fromkeys(notes["weak"] + notes["missed"]))
        extra += tx(ctx, "smp_weak", what=what)
    if notes.get("fonts"):
        extra += tx(ctx, "smp_fonts", fonts=", ".join(R.FONTS[f]["label"] for f in notes["fonts"] if f in R.FONTS) or "—")
    await update.effective_chat.send_photo(io.BytesIO(jpg), caption=tx(ctx, "smp_ready", notes=extra),
                                           parse_mode=HTML, reply_markup=_sample_kb(ctx))
    db.log_event("sample_built", uid, b["id"], fmt=fmt, layers=len(spec["feed"]["layers"]),
                 weak=len(notes.get("weak") or []))
    return True


async def _sample_run(update, ctx, b, data):
    """Разбор с ожиданием: пока не получилось (и не /cancel) — следующая картинка тоже образец,
    а не пост. Лимит или бюджет — ожидание снимается: присылать снова бессмысленно."""
    ok = await _sample_build(update, ctx, b, data)
    if ok is False:
        ctx.user_data["await"] = "sample"
    else:
        ctx.user_data.pop("await", None)


async def on_sample_photo(update, ctx, b):
    gid = update.message.media_group_id
    if gid and gid == ctx.user_data.get("smp_gid"):
        return                                  # образец пришёл альбомом: разбираем только первое фото
    ctx.user_data["smp_gid"] = gid
    data, _ = await get_file_bytes(update, ctx)
    if not data or not is_image(data):
        ctx.user_data["await"] = "sample"
        await say(update, tx(ctx, "photo_bad"), kind="notice")
        return
    os.makedirs(SAMPLE_DIR, exist_ok=True)
    with open(_sample_path(update.effective_user.id), "wb") as f:
        f.write(data)
    await _sample_run(update, ctx, b, data)


async def on_sample_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    L(ctx, update)
    uid = update.effective_user.id
    action = update.callback_query.data.split(":", 1)[1]
    b, _ = current_brand(uid)
    if not b or not is_editor(b) or not db.plan_active(b):
        await answer(update)
        return
    if action == "start":
        await answer(update)
        if not SMP.enabled():
            await say(update, tx(ctx, "smp_off", support=esc(SUPPORT)), kind="notice")
            return
        if await sample_refusal(update, ctx, b):
            return
        ctx.user_data["await"] = "sample"
        await say(update, tx(ctx, "smp_ask"))
    elif action == "again":
        await answer(update)
        try:
            with open(_sample_path(uid), "rb") as f:
                data = f.read()
        except OSError:
            ctx.user_data["await"] = "sample"
            await say(update, tx(ctx, "smp_ask"))
            return
        await _sample_run(update, ctx, b, data)          # описание макета — из кэша, без нового вызова
    elif action == "save":
        st = ctx.user_data.get("smp")
        if not st or st.get("bid") != b["id"]:
            await answer(update)
            return
        n = sum(1 for t in db.list_templates(b["id"]) if t["name"].startswith(tx(ctx, "smp_name"))) + 1
        name = tx(ctx, "smp_name") + (f" {n}" if n > 1 else "")
        tid = db.create_template(b["id"], name, S.sanitize_spec(st["spec"]))
        await answer(update)
        if not isinstance(tid, int):
            await say(update, tx(ctx, "smp_full", n=db.MAX_TEMPLATES), kind="notice")
            return
        db.set_prefs(uid, b["id"], tid=tid, fmt=st["fmt"] if st["fmt"] in FORMATS else "orig")
        ctx.user_data.pop("smp", None)
        try:
            await update.callback_query.edit_message_reply_markup(reply_markup=None)
        except TelegramError:
            pass
        eb = editor_btn(ctx, b["id"])
        await say(update, tx(ctx, "smp_saved", name=esc(name)), KB([[eb]]) if eb else None, kind="keep")
        db.log_event("sample_saved", uid, b["id"], fmt=st["fmt"])


async def on_quick_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    L(ctx, update)
    uid = update.effective_user.id
    b, _ = current_brand(uid)
    if not b:
        await say(update, tx(ctx, "welcome_new"), welcome_kb(ctx))
        return
    if not db.plan_active(b):
        await say(update, tx(ctx, "no_access", support=esc(SUPPORT)))
        return
    if not db.has_asset(b["id"], "logo"):
        await say(update, tx(ctx, "no_logo"))
        return
    doc = update.message.document
    if doc and (doc.mime_type or "").split("/")[0] in ("video", "audio"):
        await say(update, tx(ctx, "not_photo"), kind="notice")      # видео или звук файлом — не фото
        return
    if ctx.user_data.get("await") == "sample" and is_editor(b):
        return await on_sample_photo(update, ctx, b)
    if update.message.media_group_id and update.message.media_group_id == ctx.user_data.get("smp_gid"):
        return                                  # остальные фото альбома-образца — не пост
    tpls = db.list_templates(b["id"])
    if not tpls:               # настройку бросили до выбора стиля — берём стартовые
        db.seed_templates(b["id"], ctx.user_data.get("lang", "ru"))
        tpls = db.list_templates(b["id"])
    data, _ = await get_file_bytes(update, ctx)
    if not data:
        return
    if not is_image(data):
        await say(update, tx(ctx, "photo_bad"), kind="notice")
        return
    data = await run(working_copy, data)
    msg = update.message
    gid, cap = msg.media_group_id, marked(msg.caption, msg.caption_entities) if msg.caption else msg.caption
    d = draft(ctx, uid)
    now = time.time()
    same_album = d and gid and d.get("group") == gid
    add_more = (d and not gid and not cap and not d.get("sent") and d["bid"] == b["id"]
                and now - d["ts"] < DRAFT_TTL)
    # Больше 10 фото Telegram делит на несколько альбомов по 10 (подпись — только у первого):
    # фото без подписи сразу следом продолжают тот же пост, как бы ни назывался альбом
    split_album = (d and gid and not same_album and not cap and not d.get("sent") and d["bid"] == b["id"]
                   and now - d["ts"] < SPLIT_GAP)
    if split_album:
        d["group"] = gid
    if (same_album or add_more or split_album) and d["bid"] == b["id"]:
        if len(d["photos"]) >= MAX_BATCH:
            if not d.get("warned_max"):
                d["warned_max"] = True
                await say(update, tx(ctx, "photos_max", n=MAX_BATCH), kind="notice")
            return
        drafts.add_photo(uid, d, data)
        d["ts"] = now
        if cap:
            fields = R.spec_fields(_tpl(d)["spec"]) if _tpl(d) else set()
            d["title"], d["subtitle"], tag, d["tags"], d["cut"] = parse_text(cap, fields)
            d["tag"] = tag or d.get("tag")
    else:
        prefs = db.get_prefs(uid, b["id"])
        tp = next((x for x in tpls if x["id"] == prefs.get("tid")), tpls[0])
        fmt = prefs.get("fmt") if prefs.get("fmt") in FORMATS else "4:5"
        title, subtitle, tag, tags, cut = parse_text(cap, R.spec_fields(tp["spec"]))
        if d and d.get("msg"):   # пульт прошлого поста уходит, когда начат новый
            await delete_ids(ctx.bot, update.effective_chat.id, [d["msg"]])
        d = drafts.new(uid, bid=b["id"], group=gid, tid=tp["id"], fmt=fmt, dark=R.DARK_DEFAULT_IDX,
                       title=title, subtitle=subtitle, tag=tag, tags=tags, cut=cut)
        drafts.add_photo(uid, d, data)
        ctx.user_data["q"] = d
        ctx.user_data.pop("await", None)
        db.log_event("draft", uid, b["id"], draft=d["id"], title=title[:120])
    save_draft(ctx, uid)
    schedule_pult(update, ctx)


async def edit_kb(update, kb):
    try:
        await update.callback_query.edit_message_reply_markup(reply_markup=kb)
    except BadRequest:
        pass


async def on_quick_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    L(ctx, update)
    q = update.callback_query
    uid = update.effective_user.id
    d = draft(ctx, uid)
    if d and (not db.member_role(d["bid"], uid) or not all(os.path.exists(p) for p in d["photos"])):
        ctx.user_data.pop("q", None)       # из бренда убрали или черновик устарел (старше суток)
        drafts.clear(uid)
        d = None
    # «Сделать N из M» живёт в отдельном сообщении о лимите, остальные кнопки — только на пульте
    if not d or (d.get("msg") != q.message.message_id and q.data != "q:part"):
        await on_stale(update, ctx)
        return
    head, _, arg = q.data.partition(":")      # «qf:1:1» → формат «1:1» целиком
    tp = _tpl(d)
    if not tp:
        tpls = db.list_templates(d["bid"])
        if not tpls:
            await on_stale(update, ctx)
            return
        tp = tpls[0]
        d["tid"] = tp["id"]
    back = [Btn(tx(ctx, "b_back"), callback_data="q:back")]
    chat_id = q.message.chat_id
    redraw = False

    if head == "q" and arg == "noop":
        await answer(update)
        return
    if head == "q" and arg == "tpl":
        await answer(update)
        rows = [[Btn(("✓ " if x["id"] == d["tid"] else "") + x["name"]
                     + (tx(ctx, "tpl_story") if x["spec"]["story"].get("enabled") else ""),
                     callback_data=f"qt:{x['id']}")] for x in db.list_templates(d["bid"])]
        await edit_kb(update, KB(rows + [back]))
        return
    if head == "q" and arg == "fmt":
        await answer(update)
        main = [k for k in FORMATS if k != "orig"]
        rows = [[Btn(("✓ " if k == d["fmt"] else "") + k, callback_data=f"qf:{k}") for k in main[i:i + 3]]
                for i in range(0, len(main), 3)]
        rows.append([Btn(("✓ " if d["fmt"] == "orig" else "") + tx(ctx, "fmt_orig"), callback_data="qf:orig")])
        await edit_kb(update, KB(rows + [back]))
        return
    if head == "q" and arg == "tag":
        await answer(update)
        cap_tags = list(d.get("tags") or [])
        kit_tags = [t for t in (db.get_brand(d["bid"])["kit"].get("hashtags") or []) if t not in cap_tags]
        rows, row = [], []
        for kind, lst in (("c", cap_tags), ("i", kit_tags)):
            for i, tag in enumerate(lst):
                row.append(Btn(("✓ " if tag == d.get("tag") else "") + tag, callback_data=f"qh:{kind}:{i}"))
                if len(row) == 2:
                    rows.append(row)
                    row = []
        if row:
            rows.append(row)
        rows.append([Btn(tx(ctx, "tag_none"), callback_data="qh:none"),
                     Btn(tx(ctx, "tag_custom"), callback_data="qh:custom")])
        await edit_kb(update, KB(rows + [back]))
        return
    if head == "q" and arg == "back":
        await answer(update)
        redraw = True
    elif head == "q" and arg == "text":
        await answer(update)
        ctx.user_data["await"] = "text"
        await say(update, tx(ctx, "q_ask_text"))
        return
    elif head == "q" and arg in ("lighter", "darker"):
        new = max(0, min(len(R.DARK_STEPS) - 1, d["dark"] + (1 if arg == "darker" else -1)))
        if new == d["dark"]:
            await answer(update, tx(ctx, "edge"))
            return
        await answer(update)
        d["dark"] = new
        d["actions"] = d.get("actions", 0) + 1
        redraw = True
    elif head == "q" and arg in ("prev", "next"):
        await answer(update)
        n = len(d["photos"])
        d["cur"] = (d.get("cur", 0) + (1 if arg == "next" else -1)) % n
        redraw = True
    elif head == "q" and arg.startswith("zm:"):
        await answer(update)
        cur = str(d.get("cur", 0))
        z = _zoom(d, int(cur)) + (ZOOM_STEP if arg.endswith("+") else -ZOOM_STEP)
        z = max(1.0, min(ZOOM_MAX, round(z / ZOOM_STEP) * ZOOM_STEP))
        d.setdefault("zoom", {})[cur] = z
        d["actions"] = d.get("actions", 0) + 1
        redraw = True
    elif head == "q" and arg.startswith("fc:"):
        await answer(update)
        try:
            val = max(0.0, min(1.0, float(arg[3:])))
        except ValueError:
            return
        pw, ph = photo_dims(drafts.read(d["photos"][d.get("cur", 0)]))
        W, H = _surface_size(_Size(pw, ph), d["fmt"], surface(tp["spec"], d["fmt"])[1])
        horiz = pw / ph > W / H
        d.setdefault("focus", {})[str(d.get("cur", 0))] = [val, 0.5] if horiz else [0.5, val]
        d["actions"] = d.get("actions", 0) + 1
        redraw = True
    elif head == "qt":
        await answer(update)
        ntp = db.get_template(d["bid"], int(arg)) if arg.isdigit() else None
        if ntp:
            d["tid"] = ntp["id"]
            db.set_prefs(uid, d["bid"], tid=ntp["id"], fmt=d["fmt"])
            d["actions"] = d.get("actions", 0) + 1
        redraw = True
    elif head == "qf":
        await answer(update)
        if arg in FORMATS:
            d["fmt"] = arg
            db.set_prefs(uid, d["bid"], fmt=arg)
            d["actions"] = d.get("actions", 0) + 1
        redraw = True
    elif head == "qh":
        await answer(update)
        if arg == "custom":
            ctx.user_data["await"] = "tag"
            await say(update, tx(ctx, "ask_custom_tag"))
            return
        if arg == "none":
            d["tag"] = None
        elif arg[:2] in ("i:", "c:"):
            lst = list(d.get("tags") or []) if arg[0] == "c" else \
                [t for t in (db.get_brand(d["bid"])["kit"].get("hashtags") or []) if t not in (d.get("tags") or [])]
            idx = int(arg[2:]) if arg[2:].isdigit() else -1
            if 0 <= idx < len(lst):
                d["tag"] = lst[idx]
        d["actions"] = d.get("actions", 0) + 1
        redraw = True
    elif head == "q" and arg == "send":
        await send_files(update, ctx, d, tp)
        return
    elif head == "q" and arg == "part":
        if q.message.message_id != d.get("msg"):
            await delete_ids(ctx.bot, chat_id, [q.message.message_id])
        await send_files(update, ctx, d, tp, partial=True)
        return
    elif head == "q" and arg == "chan":
        await send_channel_album(update, ctx, d)
        return
    elif head == "q" and arg == "done":   # выйти из пульта: файлы остаются, пульт уходит
        await answer(update)
        ctx.user_data.pop("q", None)
        drafts.clear(uid)
        await delete_ids(ctx.bot, chat_id, [q.message.message_id])
        await show_menu(update, ctx)
        return
    else:
        await answer(update)
        return
    if redraw:
        save_draft(ctx, uid)
        await show_pult(ctx.bot, chat_id, ctx, uid)


async def send_files(update, ctx, d, tp, partial=False):
    uid = update.effective_user.id
    b = db.get_brand(d["bid"])
    lang = ctx.user_data.get("lang", "ru")
    n_all = len(d["photos"])
    if not db.plan_active(b):
        await answer(update, tx(ctx, "no_access_short"), alert=True)
        return
    # «Файлы ещё раз» по тому же посту не тратят лимит второй раз: уже засчитанные фото — бесплатно
    counted = min(int(d.get("counted") or 0), n_all)
    left = plan_limits(b)["photos"] - db.photos_used(b["id"])
    _, reset = db.period_bounds(db.get_brand(b["root_id"]) or b)
    can = left + counted                         # уже засчитанные фото этого поста — без нового списания
    n = min(n_all, max(0, can)) if partial else n_all
    if n <= 0 or n > can:
        await answer(update)
        if left > 0:
            await say(update, tx(ctx, "limit_hit", left=left, n=n_all, reset=fmt_day(reset, lang)),
                      KB([[Btn(tx(ctx, "b_part", left=can, n=n_all), callback_data="q:part")]]), kind="notice", ttl=120)
        else:
            await say(update, tx(ctx, "limit_zero", reset=fmt_day(reset, lang), support=esc(SUPPORT)), kind="notice", ttl=90)
        return
    await answer(update, tx(ctx, "q_sending"))
    chat = update.effective_chat
    base = await run(web.brand_ctx, d["bid"])        # свежий: логотип и палитра — как сейчас в редакторе
    dark = R.DARK_STEPS[d["dark"]]
    stem = safe_name(b["kit"].get("name")) + "_" + safe_name(tp["name"])
    out_dir = os.path.join(drafts.ROOT, str(uid), "out")
    os.makedirs(out_dir, exist_ok=True)
    progress = await chat.send_message(tx(ctx, "q_progress", i=0, n=n)) if n > 2 else None
    feed, story, failed = [], [], []
    last_edit = time.time()
    for i, path in enumerate(d["photos"][:n], 1):
        try:
            outs = await run(_render_job, path, tp["spec"], d["fmt"], _ctx_for(base, d, i, n, dark),
                             out_dir, f"{stem}_{i}")
            for suf, p in outs:
                (feed if suf == "feed" else story).append(p)
        except Exception as e:
            logger.exception("photo %s: %s", i, e)
            failed.append(i)
        if progress and (time.time() - last_edit > 1.5 or i == n):
            try:
                await progress.edit_text(tx(ctx, "q_progress", i=i, n=n))
                last_edit = time.time()
            except TelegramError:
                pass
    try:
        for group in (feed, story):
            for k in range(0, len(group), ALBUM):
                chunk = group[k:k + ALBUM]
                if len(chunk) == 1:
                    with open(chunk[0], "rb") as f:
                        await tg_call(chat.send_document, f.read(), filename=os.path.basename(chunk[0]))
                else:
                    media = []
                    for p in chunk:
                        with open(p, "rb") as f:
                            media.append(InputMediaDocument(f.read(), filename=os.path.basename(p)))
                    await tg_call(ctx.bot.send_media_group, chat.id, media)
    finally:
        if progress:
            await delete_ids(ctx.bot, chat.id, [progress.message_id])
    for i in failed:
        await say(update, tx(ctx, "photo_err", i=i), kind="notice", ttl=90)
    ok = n - len(failed)
    if not ok:
        db.log_event("error", uid, d["bid"], where="files", draft=d["id"])
        return
    text = post_text(d)
    for chunk in (split_text(text, 3500) if text else []):
        await chat.send_message(f"<pre>{esc(chunk)}</pre>", parse_mode=HTML)
    db.record_event(d["bid"], uid, tp["id"], max(0, ok - counted))
    d["counted"] = max(counted, ok)
    db.log_event("files", uid, d["bid"], draft=d["id"], n=ok, tid=tp["id"], fmt=d["fmt"], actions=d.get("actions", 0),
                 secs=round(time.time() - d.get("created", time.time())), story=bool(story))
    db.set_prefs(uid, d["bid"], tid=d["tid"], fmt=d["fmt"])
    d["sent"] = True
    d["out"] = feed
    save_draft(ctx, uid)
    await show_pult(ctx.bot, chat.id, ctx, uid, new=True)   # пульт снова под файлами


async def send_channel_album(update, ctx, d):
    """Альбом фото с подписью — его можно сразу переслать в канал."""
    await answer(update)
    files = [p for p in (d.get("out") or []) if os.path.exists(p)]
    if not files:
        await on_stale(update, ctx)
        return
    text = post_text(d)
    # Подпись под альбомом в Telegram — до 1024 знаков. Длиннее: альбом без подписи,
    # текст поста целиком — следующим сообщением (его тоже можно переслать)
    long = utf16_len(text) > CAPTION_MAX
    caption = None if long else (text or None)
    chat = update.effective_chat
    for k in range(0, len(files), ALBUM):
        media = []
        for j, p in enumerate(files[k:k + ALBUM]):
            with open(p, "rb") as f:
                media.append(InputMediaPhoto(f.read(), caption=caption if (k == 0 and j == 0) else None))
        if len(media) == 1:
            await tg_call(chat.send_photo, media[0].media, caption=media[0].caption)
        else:
            await tg_call(ctx.bot.send_media_group, chat.id, media)
    if long:
        for chunk in split_text(text, 4000):
            await tg_call(chat.send_message, chunk, disable_web_page_preview=True)
    db.log_event("channel_album", update.effective_user.id, d["bid"], draft=d["id"], n=len(files), long=long)
    await say(update, tx(ctx, "q_channel_long" if long else "q_channel_hint"), kind="notice", ttl=45 if long else 30)


async def on_quick_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    L(ctx, update)
    uid = update.effective_user.id
    aw = ctx.user_data.pop("await", None)
    if aw == "adm_find" and is_admin(update):
        return await admin_find_results(update, ctx, update.message.text or "")
    if aw == "sample":                    # ждём картинку образца, а пришёл текст — напоминаем
        ctx.user_data["await"] = "sample"
        await say(update, tx(ctx, "smp_ask"), kind="notice")
        return
    d = draft(ctx, uid)
    if d and not db.member_role(d["bid"], uid):
        ctx.user_data.pop("q", None)
        drafts.clear(uid)
        d = None
    if not aw and d and d.get("msg") and update.message and update.message.text:
        aw = "text"                 # превью открыто, бот ничего не спрашивал: текст — новый «Текст» поста
    if not aw or not d:
        b, _ = current_brand(uid)
        if b:
            await say(update, tx(ctx, "q_hint"), kind="notice")
        else:
            await say(update, tx(ctx, "welcome_new"), welcome_kb(ctx))
        return
    text = marked(update.message.text, update.message.entities)
    if aw == "tag":
        words = text.split()
        token = words[0].lstrip("#").strip() if words else ""
        if not token:
            ctx.user_data["await"] = "tag"
            await say(update, tx(ctx, "custom_tag_bad"))
            return
        d["tag"] = "#" + token[:30]
    else:
        tp = _tpl(d)
        d["title"], d["subtitle"], tag, tags, d["cut"] = parse_text(text, R.spec_fields(tp["spec"]) if tp else set())
        if tags:
            d["tags"] = tags
        if tag:
            d["tag"] = tag
        db.log_event("draft_text", uid, d["bid"], draft=d["id"], title=d["title"][:120])
    d["actions"] = d.get("actions", 0) + 1
    save_draft(ctx, uid)
    await show_pult(ctx.bot, update.effective_chat.id, ctx, uid, new=True)


# ============ Админ ============
def admin_text():
    brands = db.list_brands()
    active = [b for b in brands if db.plan_active(b)]
    reqs = [r for r in db.events("access_request")]
    pending = sum(1 for r in reqs if (db.get_request(r["data"].get("request") or 0) or {}).get("status") == "new")
    return ("<b>Админ-панель</b>\n"
            f"Брендов: {len(brands)}, активных: {len(active)}\n"
            f"Заявок ждут ответа: {pending}\n"
            f"Хранилище: {'постоянное' if db.STORAGE_PERSISTENT else 'ВРЕМЕННОЕ — подключите диск'}\n"
            f"Копии: {'S3' if backups.s3_config() else ('в Telegram' if BACKUP_TO_TELEGRAM else 'только на диске')}\n"
            f"Редактор: {WEBAPP_URL or 'не задан WEBAPP_URL'}\n"
            f"Стиль по образцу: {('расход ' + quota.summary()) if SMP.enabled() else 'не подключён (VISION_API_KEY)'}")


def admin_kb():
    return KB([
        [Btn("Статистика пилота", callback_data="adm:stats"), Btn("Найти бренд", callback_data="adm:find")],
        [Btn("Заявки", callback_data="adm:reqs"), Btn("Копия базы", callback_data="adm:backup")],
        [Btn("Отладка", callback_data="adm:debug")],
        [Btn("Очистить чат", callback_data="adm:clear"), Btn("Вместе с файлами", callback_data="adm:clearall")],
        [Btn("‹ Меню", callback_data="menu:home")],
    ])


PLAN_LABEL = {"pilot": "Пилот", "solo": "Solo", "media": "Media", "studio": "Studio"}


def debug_text(uid, b):
    if not b:
        return "<b>Отладка</b>\nАктивного бренда нет. Создайте код /newcode и отправьте его боту."
    lim = plan_limits(b)
    role = db.member_role(b["id"], uid)
    active = db.plan_active(db.get_brand(b["root_id"]))
    start, end = db.period_bounds(db.get_brand(b["root_id"]) or b)
    return ("<b>Отладка</b> — действует на ваш текущий бренд\n"
            f"Бренд: <b>{esc(b['kit'].get('name') or '—')}</b> #{b['id']}\n"
            f"Тариф: {PLAN_LABEL.get(b['plan'], b['plan'])} · до {fmt_date(b['plan_until'])} · "
            f"{'активен' if active else 'истёк'}{' · бренд на паузе' if b.get('locked') else ''}\n"
            f"Период: {fmt_date(start.isoformat())} – {fmt_date(end.isoformat())}\n"
            f"Фото за период: {db.photos_used(b['id'])} из {lim['photos']}\n"
            f"Людей в подписке: {db.sub_member_count(b['id'])} из {lim['members']}\n"
            f"Ваша роль: {role}")


def debug_kb(uid, b):
    rows = []
    if b:
        rows.append([Btn(("✓ " if b["plan"] == k else "") + v, callback_data=f"adm:plan:{k}")
                     for k, v in PLAN_LABEL.items() if k in db.PLANS])
        rows.append([Btn("Сделать истёкшим", callback_data="adm:expire"), Btn("+30 дней", callback_data="adm:extend")])
        rows.append([Btn("Обнулить фото периода", callback_data="adm:reset")])
        role = db.member_role(b["id"], uid)
        rows.append([Btn("Смотреть как участник" if role == "owner" else "Вернуть роль владельца",
                         callback_data="adm:role")])
    rows.append([Btn("‹ Админ", callback_data="adm:panel")])
    return KB(rows)


async def show_admin(update, ctx, text=None, kb=None):
    text, kb = text or admin_text(), kb or admin_kb()
    q = update.callback_query
    if q and q.message and not q.message.photo:
        try:
            await q.edit_message_text(text, parse_mode=HTML, reply_markup=kb, disable_web_page_preview=True)
            MENU_MSG[update.effective_chat.id] = q.message.message_id
            return
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return
    old = MENU_MSG.get(update.effective_chat.id)
    m = await say(update, text, kb, kind="menu")
    MENU_MSG[update.effective_chat.id] = m.message_id
    if old and old != m.message_id:
        await delete_ids(ctx.bot, update.effective_chat.id, [old])


async def clear_chat(update, ctx, with_results):
    chat_id = update.effective_chat.id
    ids = db.chat_msgs(chat_id, with_results=with_results)
    await delete_ids(ctx.bot, chat_id, ids)
    PROMPTS.pop(chat_id, None)
    MENU_MSG.pop(chat_id, None)
    ctx.user_data.pop("q", None)
    drafts.clear(update.effective_user.id)
    return len(ids)


async def cmd_admin(update, ctx):
    if not is_admin(update):
        return
    L(ctx, update)
    await show_admin(update, ctx)


def brand_card(bid):
    b = db.get_brand(bid)
    if not b:
        return "Бренд не найден.", None
    owner = db.get_user(b["owner_id"]) or {}
    start, end = db.period_bounds(db.get_brand(b["root_id"]) or b)
    members = db.team_stats(bid)
    tpls = db.list_templates(bid)
    files = db.events("files", brand_id=bid)[-5:]
    drafts_n = len(db.events("draft", brand_id=bid))
    lines = [f"<b>{esc(b['kit'].get('name') or '—')}</b> #{bid}" + (f" (в подписке #{b['root_id']})" if b["root_id"] != bid else ""),
             f"Владелец: {esc(owner.get('name') or '—')}" + (f" @{esc(owner['username'])}" if owner.get("username") else "")
             + f", id <code>{b['owner_id']}</code>",
             f"Тариф: {PLAN_LABEL.get(b['plan'], b['plan'])} до {fmt_date(b['plan_until'])} "
             f"({'активен' if db.plan_active(b) else 'истёк'})",
             f"Период: {fmt_date(start.isoformat())} – {fmt_date(end.isoformat())}, фото {db.photos_used(bid)} из "
             f"{plan_limits(b)['photos']}",
             f"Логотип: {'есть' if db.has_asset(bid, 'logo') else 'нет'}; постов начато: {drafts_n}, "
             f"выдано файлов: {len(db.events('files', brand_id=bid))}",
             "", "<b>Команда</b>"]
    for r in members:
        lines.append(f"• {esc(person_name(r))} — {r['role']}, фото за период: {r['photos_month']}")
    lines += ["", f"<b>Стили</b> ({len(tpls)})"] + [f"• {esc(t['name'])}" for t in tpls[:12]]
    if files:
        lines += ["", "<b>Последние выдачи</b>"]
        for e in reversed(files):
            lines.append(f"• {fmt_date(e['ts'])}: {e['data'].get('n')} фото, {e['data'].get('fmt')}")
    kb = KB([[Btn("+30 дней", callback_data=f"adm:bx:{bid}"), Btn("Тариф…", callback_data=f"adm:bp:{bid}")],
             [Btn("‹ Админ", callback_data="adm:panel")]])
    return "\n".join(lines), kb


async def admin_find_results(update, ctx, query):
    found = db.find_brands(query)
    if not found:
        await say(update, "Ничего не нашлось. Пришлите #id, часть названия или @username владельца.", kind="notice")
        return
    if len(found) == 1:
        text, kb = brand_card(found[0]["id"])
        await say(update, text, kb, kind="menu")
        return
    rows = [[Btn(f"#{b['id']} {(b['kit'].get('name') or '—')[:28]}", callback_data=f"adm:b:{b['id']}")] for b in found]
    rows.append([Btn("‹ Админ", callback_data="adm:panel")])
    await say(update, "Найдено:", KB(rows), kind="menu")


async def on_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    L(ctx, update)
    q = update.callback_query
    if not is_admin(update):
        await on_stale(update, ctx)
        return
    parts = q.data.split(":")
    action = parts[1] if len(parts) > 1 else "panel"
    uid = update.effective_user.id
    b, _ = current_brand(uid)
    note = None
    if action in ("clear", "clearall"):
        await answer(update, "Чищу…")
        n = await clear_chat(update, ctx, action == "clearall")
        logger.info("admin %s cleared %s messages", uid, n)
        await show_menu(update, ctx)
        return
    if action == "stats":
        await answer(update)
        chunks = split_text(stats_text())
        if len(chunks) == 1:
            await show_admin(update, ctx, chunks[0], KB([[Btn("‹ Админ", callback_data="adm:panel")]]))
        else:                      # много брендов — несколькими сообщениями
            for ch in chunks:
                await say(update, ch, kind="keep")
        return
    if action == "find":
        await answer(update)
        ctx.user_data["await"] = "adm_find"
        await say(update, "Пришлите #id бренда, часть названия или @username владельца.")
        return
    if action == "b" and len(parts) > 2:
        await answer(update)
        text, kb = brand_card(int(parts[2]))
        await show_admin(update, ctx, text, kb)
        return
    if action == "bx" and len(parts) > 2:
        db.extend_brand(int(parts[2]), 30)
        await answer(update, "Продлено на 30 дней")
        text, kb = brand_card(int(parts[2]))
        await show_admin(update, ctx, text, kb)
        return
    if action == "bp" and len(parts) > 2:
        await answer(update)
        bid = int(parts[2])
        rows = [[Btn(v, callback_data=f"adm:bpp:{bid}:{k}") for k, v in PLAN_LABEL.items()],
                [Btn("‹ Бренд", callback_data=f"adm:b:{bid}")]]
        await show_admin(update, ctx, f"Тариф для #{bid}:", KB(rows))
        return
    if action == "bpp" and len(parts) > 3:
        db.set_plan(int(parts[2]), parts[3])
        await answer(update, f"Тариф: {PLAN_LABEL.get(parts[3], parts[3])}")
        text, kb = brand_card(int(parts[2]))
        await show_admin(update, ctx, text, kb)
        return
    if action == "reqs":
        await answer(update)
        pend = [db.get_request(r["data"].get("request") or 0) for r in db.events("access_request")]
        pend = [r for r in pend if r and r["status"] == "new"]
        if not pend:
            await show_admin(update, ctx, "Новых заявок нет.", KB([[Btn("‹ Админ", callback_data="adm:panel")]]))
            return
        for r in pend[-10:]:
            who = esc(r.get("name") or "—") + (f" (@{esc(r['username'])})" if r.get("username") else "")
            await say(update, f"<b>Заявка #{r['id']}</b> · {fmt_date(r['created_at'])}\n{who}\n\n{esc(r['channel'] or '')}",
                      KB([[Btn("Открыть доступ", callback_data=f"acc:ok:{r['id']}"),
                           Btn("Отклонить", callback_data=f"acc:no:{r['id']}")]]), kind="keep")
        return
    if action == "backup":
        await answer(update, "Делаю копию…")
        await do_backup(ctx.bot, to_chat=update.effective_chat.id)
        return
    if action == "debug":
        await answer(update)
        await show_admin(update, ctx, debug_text(uid, b), debug_kb(uid, b))
        return
    if b and action == "plan" and len(parts) > 2:
        db.set_plan(b["id"], parts[2])
        note = f"Тариф: {PLAN_LABEL.get(parts[2], parts[2])}"
    elif b and action == "expire":
        db.expire_brand(b["id"])
        note = "Подписка истекла"
    elif b and action == "extend":
        db.extend_brand(b["id"], 30)
        note = "Продлено на 30 дней"
    elif b and action == "reset":
        db.reset_usage(b["id"])
        note = "Счётчик фото обнулён"
    elif b and action == "role":
        role = db.member_role(b["id"], uid)
        db.set_member_role(b["id"], uid, "editor" if role == "owner" else "owner")
        note = "Роль: участник" if role == "owner" else "Роль: владелец"
    if action in ("plan", "expire", "extend", "reset", "role"):
        await answer(update, note or "")
        b, _ = current_brand(uid)
        await show_admin(update, ctx, debug_text(uid, b), debug_kb(uid, b))
        return
    await answer(update)
    await show_admin(update, ctx)


def stats_text(days=45):
    """Метрики пилота: активация, время до первых файлов, регулярность, превью → файлы, правки."""
    since = datetime.now(TZ) - timedelta(days=days)
    brands = [b for b in db.list_brands() if b["root_id"] == b["id"]]
    files = db.events("files", since=since)
    drafts_ev = db.events("draft", since=since)
    by_brand = {}
    for e in files:
        by_brand.setdefault(e["brand_id"], []).append(e)
    rows, act_ok, act_all, ttf = [], 0, 0, []
    week_ago = datetime.now(TZ) - timedelta(days=7)
    for b in brands:
        created = _local(b["created_at"])
        ev = by_brand.get(b["id"], [])
        first = _local(ev[0]["ts"]) if ev else None
        if created >= since:
            act_all += 1
            if first and (first - created) <= timedelta(hours=24) and db.has_asset(b["id"], "logo"):
                act_ok += 1
            if first:
                ttf.append((first - created).total_seconds() / 60)
        days_week = len({_local(e["ts"]).date() for e in ev if _local(e["ts"]) >= week_ago})
        posts = len(ev)
        rows.append((b, first, days_week, posts))
    with_files = {e["data"].get("draft") for e in files}
    conv = (len([d for d in drafts_ev if d["data"].get("draft") in with_files]) / len(drafts_ev)) if drafts_ev else 0
    acts = [e["data"].get("actions", 0) for e in files if isinstance(e["data"].get("actions"), int)]
    paid = [b for b in brands if b["plan"] != "pilot" and db.plan_active(b)]
    lines = [f"<b>Пилот за {days} дней</b>",
             f"Активация (файлы за сутки после кода): {act_ok} из {act_all} — цель 8 из 10",
             f"Время до первых файлов, медиана: {round(median(ttf)) if ttf else '—'} мин — цель меньше 10",
             f"Превью → файлы: {round(conv * 100)}% — цель 60% и выше",
             f"Нажатий пульта до файлов, медиана: {median(acts) if acts else '—'} — цель 2 и меньше",
             f"Платящих (не пилот, активны): {len(paid)}",
             "", "<b>Бренды</b> · дней с постами за неделю · выдач всего"]
    for b, first, dw, posts in sorted(rows, key=lambda r: -r[3])[:40]:
        lines.append(f"• {esc(b['kit'].get('name') or '—')} #{b['id']}: {dw} дн. · {posts}"
                     + ("" if first else " · файлов ещё не было"))
    return "\n".join(lines)


async def cmd_stats(update, ctx):
    if not is_admin(update):
        return
    days = int(ctx.args[0]) if ctx.args and ctx.args[0].isdigit() else 45
    for chunk in split_text(stats_text(days)):
        await update.message.reply_text(chunk, parse_mode=HTML)


def split_text(text, limit=3900):
    """Длинный текст → куски не длиннее limit по границам строк (лимит Telegram — 4096)."""
    out, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit and cur:
            out.append(cur)
            cur = ""
        cur += ("\n" if cur else "") + line
    if cur:
        out.append(cur)
    return out


async def cmd_myid(update, ctx):
    if ADMIN_IDS and not is_admin(update):
        return
    await update.message.reply_text(f"Telegram ID: <code>{update.effective_user.id}</code>", parse_mode=HTML)


NEWCODE_HELP = ("<b>Новый код доступа</b>\n"
                "<code>/newcode</code> — пилот на {days} дн., код на одного человека.\n"
                "<code>/newcode pilot 30 1</code> — тариф, сколько дней, сколько человек могут ввести код.\n"
                "Пример: <code>/newcode media 60 1</code> — Media на 60 дней.\n"
                "Тарифы: {plans}.")
EXTEND_HELP = ("<b>Продлить бренд</b>\n"
               "<code>/extend 3 30</code> — бренду #3 ещё 30 дней.\n"
               "<code>/extend 3 30 media</code> — то же и сменить тариф на Media.\n"
               "Номер бренда — в /brands или «Админ → Найти бренд». Тарифы: {plans}.")


async def cmd_newcode(update, ctx):
    """/newcode [тариф=pilot] [дней=30] [использований=1]"""
    if not is_admin(update):
        return
    a = ctx.args or []
    plan = a[0].lower() if a else "pilot"
    if plan not in db.PLANS:
        await update.message.reply_text(NEWCODE_HELP.format(plans=", ".join(db.PLANS), days=PILOT_DAYS), parse_mode=HTML)
        return
    try:
        days = int(a[1]) if len(a) > 1 else PILOT_DAYS
        uses = int(a[2]) if len(a) > 2 else 1
        if days < 1 or uses < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text(NEWCODE_HELP.format(plans=", ".join(db.PLANS), days=PILOT_DAYS), parse_mode=HTML)
        return
    code = db.create_invite(plan, days, uses)
    who = "на одного человека" if uses == 1 else f"на {uses} человек"
    await update.message.reply_text(
        f"Код <code>{code}</code>: {PLAN_LABEL.get(plan, plan)} на {days} дн., {who}.\n"
        f"Перешлите клиенту следующее сообщение.", parse_mode=HTML)
    await update.message.reply_text(
        f"Ваш код доступа к NUMBUS: <code>{code}</code>\n"
        f"Откройте @{ctx.bot.username}, нажмите «Старт» и отправьте этот код.", parse_mode=HTML)


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
    if len(rows) > 60:      # много брендов — таблицей
        import csv
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "name", "plan", "until", "active", "members", "templates", "photos_period", "limit", "owner_id"])
        for b in rows:
            w.writerow([b["id"], b["kit"].get("name") or "", b["plan"], fmt_date(b["plan_until"]), db.plan_active(b),
                        db.member_count(b["id"]), db.template_count(b["id"]), db.photos_used(b["id"]),
                        plan_limits(b)["photos"], b["owner_id"]])
        await update.message.reply_document(io.BytesIO(buf.getvalue().encode("utf-8-sig")), filename="brands.csv")
        return
    lines = []
    for b in rows:
        state = "✓" if db.plan_active(b) else "✕"
        lines.append(f"{state} <b>#{b['id']}</b> {esc(b['kit'].get('name') or '—')} · {PLAN_LABEL.get(b['plan'], b['plan'])} до "
                     f"{fmt_date(b['plan_until'])} · людей {db.member_count(b['id'])} · "
                     f"стилей {db.template_count(b['id'])} · фото {db.photos_used(b['id'])}/{plan_limits(b)['photos']}")
    storage = "постоянное" if db.STORAGE_PERSISTENT else "ВРЕМЕННОЕ — подключите диск"
    editor = WEBAPP_URL or "не задан WEBAPP_URL"
    text = "\n".join(lines) + f"\n\nХранилище: {storage}\nРедактор: {editor}"
    for chunk in split_text(text):
        await update.message.reply_text(chunk, parse_mode=HTML, disable_web_page_preview=True)


async def cmd_extend(update, ctx):
    """/extend <id бренда> <дней> [тариф]"""
    if not is_admin(update):
        return
    a = ctx.args or []
    try:
        bid, days = int(a[0].lstrip("#")), int(a[1])
    except (IndexError, ValueError):
        await update.message.reply_text(EXTEND_HELP.format(plans=", ".join(db.PLANS)), parse_mode=HTML)
        return
    plan = a[2].lower() if len(a) > 2 else None
    if plan and plan not in db.PLANS:
        await update.message.reply_text(EXTEND_HELP.format(plans=", ".join(db.PLANS)), parse_mode=HTML)
        return
    if not db.extend_brand(bid, days, plan):
        await update.message.reply_text(f"Бренда #{bid} нет. Номера брендов — в /brands.")
        return
    b = db.get_brand(bid)
    await update.message.reply_text(f"#{bid}: {PLAN_LABEL.get(b['plan'], b['plan'])} до {fmt_date(b['plan_until'])}")


async def cmd_backup(update, ctx):
    if not is_admin(update):
        return
    await do_backup(ctx.bot, to_chat=update.effective_chat.id)


async def on_stale(update, ctx):
    L(ctx, update)
    await answer(update, tx(ctx, "stale"), alert=True)


async def on_orphan(update, ctx):
    """Видео, стикер, голосовое, GIF, контакт… — бот работает только с фото."""
    L(ctx, update)
    await say(update, tx(ctx, "not_photo"), kind="notice")


# ============ Ошибки и оповещения ============
_ALERTS = {}          # подпись ошибки → когда последний раз писали админу
ALERT_EVERY = 600


REPO_DIR = os.path.dirname(os.path.abspath(__file__))


def _repo_frame(f) -> bool:
    """Кадр из файлов проекта (bot.py, render.py…), а не из библиотек и стандартной библиотеки Python."""
    path = os.path.abspath(f.filename)
    return path.startswith(REPO_DIR + os.sep) and "site-packages" not in path and "dist-packages" not in path


def update_action(update) -> str:
    """Что человек сделал: «кнопка q:send», «фото (альбом)», «команда /start», «текст»…"""
    if not isinstance(update, Update):
        return ""
    if update.callback_query:
        return f"кнопка {(update.callback_query.data or '')[:40]}"
    m = update.message or update.edited_message
    if not m:
        return "обновление"
    if m.text:
        return f"команда {m.text.split()[0][:30]}" if m.text.startswith("/") else "текст"
    if m.photo:
        return "фото" + (" (альбом)" if m.media_group_id else "")
    if m.document:
        return f"файл {m.document.mime_type or ''}".strip() + (" (альбом)" if m.media_group_id else "")
    return "сообщение"


async def alert_admins(bot, err, uid=None, where="", action=None):
    """Сообщает админам об ошибке: что случилось, где в коде (файл проекта, а не библиотека),
    какой обработчик, что сделал человек и кто он. Одинаковые ошибки — не чаще раза в 10 минут."""
    if not ADMIN_IDS or err is None:
        return
    tb = traceback.extract_tb(err.__traceback__) if err.__traceback__ else []
    repo = [f for f in tb if _repo_frame(f)]
    frame = repo[-1] if repo else next((f for f in reversed(tb) if "site-packages" not in f.filename),
                                       tb[-1] if tb else None)
    place = f"{os.path.basename(frame.filename)}:{frame.lineno} {frame.name}" if frame else (where or "—")
    handler = repo[0].name if repo and repo[0] is not frame else ""
    code = getattr(err, "status", None) or getattr(err, "code", None)
    sig = f"{type(err).__name__}|{place}|{code}"      # 401 после «нет денег» — отдельное оповещение
    now = time.time()
    if now - _ALERTS.get(sig, 0) < ALERT_EVERY:
        return
    _ALERTS[sig] = now
    who = ""
    if uid:
        u = db.get_user(uid) or {}
        who = f"<code>{uid}</code>" + (f" {esc(u['name'])}" if u.get("name") else "") + \
            (f" @{esc(u['username'])}" if u.get("username") else "")
    text = (f"<b>Ошибка</b> {esc(type(err).__name__)}: {esc(str(err)[:300])}\n"
            f"Где: <code>{esc(place)}</code>"
            + (f"\nОбработчик: <code>{esc(handler)}</code>" if handler else "")
            + (f"\nКонтекст: {esc(where)}" if where else "")
            + (f"\nДействие: {esc(action)}" if action else "")
            + (f"\nПользователь: {who}" if who else ""))
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(aid, text, parse_mode=HTML)
        except TelegramError:
            pass


async def on_error(update, ctx):
    err = ctx.error
    logger.error("Ошибка при обработке апдейта", exc_info=err)
    if isinstance(update, Update) and update.message and ctx.user_data is not None:
        ctx.user_data["_keep_msg"] = True     # фото или текст человека остаются в чате — можно прислать снова
    uid = update.effective_user.id if isinstance(update, Update) and update.effective_user else None
    if isinstance(err, (TimedOut, NetworkError)) and not isinstance(err, BadRequest):
        # сеть до Telegram моргнула: админу писать нечего, человеку — сказать, что не дошло
        if isinstance(update, Update) and update.effective_chat and update.effective_chat.type == "private":
            try:
                lang = (db.get_user(uid) or {}).get("lang", "ru") if uid else "ru"
                m = await ctx.bot.send_message(update.effective_chat.id, _t(lang, "net_error"))
                track(m, "notice", 60)
            except TelegramError:
                pass
        return
    if isinstance(update, Update) and update.effective_chat and update.effective_chat.type == "private":
        try:
            lang = (db.get_user(uid) or {}).get("lang", "ru") if uid else "ru"
            m = await ctx.bot.send_message(update.effective_chat.id, _t(lang, "error_user"))
            track(m, "notice", 60)
        except TelegramError:
            pass
    await alert_admins(ctx.bot, err, uid, action=update_action(update))


# ============ Фоновые задачи: копии и уборка ============
async def do_backup(bot, to_chat=None):
    try:
        path = await asyncio.to_thread(db.backup)
        where = "на диске"
        if backups.s3_config():
            key = await asyncio.to_thread(backups.upload, path)
            where = f"в S3: {key}"
        size = os.path.getsize(path)
        if to_chat or (BACKUP_TO_TELEGRAM and not backups.s3_config()):
            targets = [to_chat] if to_chat else list(ADMIN_IDS)
            for chat in targets:
                if size < 45 * 1024 * 1024:
                    with open(path, "rb") as f:
                        await bot.send_document(chat, f, filename=os.path.basename(path),
                                                caption=f"Копия базы {fmt_date(datetime.now(TZ).isoformat())}, {size // 1024} КБ")
                else:
                    await bot.send_message(chat, f"Копия сделана {where}, но она больше 45 МБ — в Telegram не влезет.")
        logger.info("backup ok: %s (%s)", path, where)
        return path
    except Exception as e:
        logger.exception("backup failed: %s", e)
        await alert_admins(bot, e, None, "backup")
        return None


async def job_backup(context: ContextTypes.DEFAULT_TYPE):
    await do_backup(context.bot)


async def job_gc(context: ContextTypes.DEFAULT_TYPE):
    n = await asyncio.to_thread(drafts.gc)
    if n:
        logger.info("drafts gc: %s", n)


# ============ Профиль бота ============
PROFILE = {
    "ru": dict(short="Фото с подписью → готовый пост в стиле вашего канала. Без дизайнера, за минуту.",
               desc="NUMBUS превращает фото с подписью в готовый пост в стиле вашего канала: логотип, фирменный цвет, "
                    "шрифт заголовка и рубрика — на своих местах. Стиль собирается один раз за пару минут. Дальше "
                    "присылайте фото: первая строка подписи станет заголовком, #слово — рубрикой, а через минуту "
                    "придут файлы без сжатия и текст поста. Карусели до 30 фото, форматы для ленты, сторис и превью "
                    "ссылок. Доступ по приглашениям.",
               cmds=[("start", "Меню"), ("help", "Как сделать пост"), ("desktop", "Редактор на компьютере"),
                     ("cancel", "Отменить")], menu="Редактор"),
    "en": dict(short="Photo with a caption → a ready post in your channel's style. No designer, in a minute.",
               desc="NUMBUS turns a photo with a caption into a ready post in your channel's style: logo, brand colour, "
                    "headline font and section tag in place. Set up the style once in a couple of minutes. Then send "
                    "photos: the first caption line becomes the headline, a #word the tag, and a minute later you get "
                    "uncompressed files and the post text. Carousels up to 30 photos, formats for the feed, stories "
                    "and link previews. Access by invitation.",
               cmds=[("start", "Menu"), ("help", "How to make a post"), ("desktop", "Editor on a computer"),
                     ("cancel", "Cancel")], menu="Editor"),
}
ADMIN_CMDS = [("admin", "Админ-панель"), ("stats", "Статистика пилота"), ("brands", "Все бренды"),
              ("newcode", "Новый код доступа"), ("codes", "Коды"), ("extend", "Продлить бренд"),
              ("backup", "Копия базы"), ("myid", "Мой ID")]


STORAGE_WARNED = False


async def warn_storage(bot):
    """Диск Railway не подключён: база живёт во временной папке и пропадёт при деплое.
    Админам — одно сообщение за запуск (флаг в базе бесполезен: она сама пропадёт)."""
    global STORAGE_WARNED
    if STORAGE_WARNED or db.STORAGE_PERSISTENT:
        return
    STORAGE_WARNED = True
    logger.warning("Хранилище временное: %s", db.DATA_DIR)
    await notify_admins(bot, "<b>Внимание: диск не подключён — данные пропадут при следующем деплое.</b>\n"
                             "Бренды, стили, коды и черновики сейчас лежат во временной папке "
                             f"(<code>{esc(db.DATA_DIR)}</code>).\n\n"
                             "Как исправить: в Railway откройте проект, нажмите ⌘K (или правый клик по пустому месту "
                             "холста) → Volume → выберите сервис бота → путь <code>/data</code> → Deploy. "
                             "После перезапуска это предупреждение больше не придёт.")


async def post_init(app):
    """Описание, команды и кнопка редактора в профиле бота — при каждом запуске."""
    bot = app.bot
    for lang, p in PROFILE.items():
        code = None if lang == "en" else lang
        try:
            await bot.set_my_short_description(p["short"], language_code=code)
            await bot.set_my_description(p["desc"], language_code=code)
            await bot.set_my_commands([BotCommand(c, d) for c, d in p["cmds"]], scope=BotCommandScopeDefault(),
                                      language_code=code)
        except TelegramError as e:
            logger.warning("profile %s: %s", lang, e)
    for aid in ADMIN_IDS:
        try:
            await bot.set_my_commands([BotCommand(c, d) for c, d in PROFILE["ru"]["cmds"] + ADMIN_CMDS],
                                      scope=BotCommandScopeChat(aid))
        except TelegramError as e:
            logger.info("admin commands %s: %s", aid, e)
    try:     # по умолчанию — меню команд; кнопку «Редактор» получают владельцы и дизайнеры
        await bot.set_chat_menu_button(menu_button=MenuButtonDefault())
    except TelegramError as e:
        logger.warning("menu button: %s", e)
    if WEBAPP_URL:
        task = asyncio.create_task(sync_all_menu_buttons(bot))
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)
    if not db.STORAGE_PERSISTENT:
        await warn_storage(bot)
    if app.job_queue:
        app.job_queue.run_daily(job_backup, time=dtime(hour=BACKUP_HOUR, minute=10, tzinfo=TZ), name="backup")
        app.job_queue.run_repeating(job_gc, interval=3600, first=120, name="drafts_gc")


# ============ Сборка ============
def build_app(token=None, base_url=None, base_file_url=None, persistence=True):
    kw = {}
    api = base_url or (TG_API_URL + "/bot" if TG_API_URL else None)
    if api:
        kw["base_url"] = api
        kw["base_file_url"] = base_file_url or (TG_API_URL + "/file/bot" if TG_API_URL else api.replace("/bot", "/file/bot"))
    proxy = TG_PROXY or None
    bot = LoggingBot(token or TOKEN,
                     request=HTTPXRequest(connection_pool_size=64, read_timeout=120, write_timeout=120,
                                          connect_timeout=30, media_write_timeout=180, proxy=proxy),
                     get_updates_request=HTTPXRequest(connection_pool_size=2, read_timeout=60, proxy=proxy),
                     rate_limiter=AIORateLimiter(max_retries=3), **kw)
    builder = Application.builder().bot(bot).concurrent_updates(PerUserProcessor(64))
    if persistence:        # шаг настройки стиля переживает перезапуск и деплой
        builder = builder.persistence(persist.DbPersistence())
    app = builder.build()
    IMG = filters.PHOTO | filters.Document.ALL
    TXT = filters.TEXT & ~filters.COMMAND
    OTHER = filters.ChatType.PRIVATE & ~filters.COMMAND
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            CommandHandler("menu", cmd_start),
            CallbackQueryHandler(on_menu, pattern="^menu:"),
            CallbackQueryHandler(on_access_request, pattern="^acc:req$"),
            CallbackQueryHandler(on_wiz_start, pattern="^wiz:start$"),
            MessageHandler(filters.TEXT & HAS_CODE, on_code),
        ],
        states={
            MENU: [CallbackQueryHandler(on_switch, pattern=r"^sw:\d+$")],
            CODE: [MessageHandler(TXT, on_code), CallbackQueryHandler(on_code_choice, pattern="^code:")],
            REQ: [MessageHandler(TXT, on_request_text)],
            # у каждого шага — запасной обработчик: неожиданный ответ получает вопрос шага ещё раз
            # и никогда не уходит в посты
            K_NAME: [MessageHandler(TXT, on_name), MessageHandler(OTHER, _wiz_other(K_NAME))],
            K_LOGO: [MessageHandler(IMG, on_logo), MessageHandler(OTHER, _wiz_other(K_LOGO))],
            K_COLOR: [CallbackQueryHandler(on_color_cb, pattern="^wz:c:"), MessageHandler(TXT, on_color_text),
                      MessageHandler(OTHER, _wiz_other(K_COLOR))],
            K_PHOTO: [MessageHandler(IMG, on_wiz_photo), CallbackQueryHandler(on_style_pick, pattern="^wz:s:"),
                      CallbackQueryHandler(on_wiz_skip, pattern="^wz:skip$"), MessageHandler(OTHER, _wiz_other(K_PHOTO))],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel), CommandHandler("start", cmd_start)],
        allow_reentry=True,
        name="main",
        persistent=persistence,
        conversation_timeout=3600,   # брошенная настройка через час не перехватывает посты
    )
    app.add_handler(TypeHandler(Update, pre_update), group=-1)
    app.add_handler(TypeHandler(Update, post_update), group=1)
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(on_admin, pattern="^adm:"))
    app.add_handler(CallbackQueryHandler(on_access_admin, pattern=r"^acc:(ok|no):\d+$"))
    app.add_handler(CallbackQueryHandler(on_team_cb, pattern="^(team|tm):"))
    app.add_handler(CallbackQueryHandler(on_settings, pattern="^set:"))
    app.add_handler(CommandHandler("desktop", cmd_desktop))
    app.add_handler(CommandHandler("newcode", cmd_newcode))
    app.add_handler(CommandHandler("codes", cmd_codes))
    app.add_handler(CommandHandler("brands", cmd_brands))
    app.add_handler(CommandHandler("extend", cmd_extend))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("backup", cmd_backup))
    app.add_handler(conv)
    app.add_handler(CommandHandler("cancel", cmd_cancel))      # вне диалога: сбросить ожидание ответа
    # Пост: срабатывает, когда диалог настройки не ждёт это сообщение
    app.add_handler(CallbackQueryHandler(on_sample_cb, pattern=r"^smp:"))
    app.add_handler(CallbackQueryHandler(on_quick_cb, pattern=r"^q[tfh]?:"))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & IMG, on_quick_photo))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & TXT, on_quick_text))
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
    # ошибки редактора — тем же оповещением админам, что и ошибки бота
    web.ALERT = lambda err, uid=None, where="": alert_admins(app.bot, err, uid, where)
    # Telegram может быть недоступен (сеть, блокировки): редактор уже работает, бот ждёт связи
    delay = 5
    while not stop.is_set():
        try:
            await app.initialize()
            break
        except (NetworkError, TimedOut) as e:
            web.STATUS["telegram"] = "нет связи"
            logger.error("Нет связи с Telegram (%s). Повтор через %s с. Сервер в России — задайте TG_PROXY "
                         "или TG_API_URL.", e, delay)
            try:
                await asyncio.wait_for(stop.wait(), delay)
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 2, 300)
    if stop.is_set():
        await runner.cleanup()
        return
    web.STATUS["telegram"] = "ok"
    try:
        await app.start()
        await post_init(app)
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        await stop.wait()
        await app.updater.stop()
        await app.stop()
    finally:
        await app.shutdown()
        await runner.cleanup()


def main():
    if not TOKEN:
        raise SystemExit("BOT_TOKEN не задан")
    asyncio.run(amain())


if __name__ == "__main__":
    main()

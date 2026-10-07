"""NUMBUS Branding — расход на «Стиль по образцу».

Каждый разбор образца — платный вызов модели. Здесь три предохранителя:
  • дневной лимит на бренд — SAMPLE_PER_BRAND_DAY (по умолчанию 5);
  • дневной лимит на весь сервер — SAMPLE_PER_DAY (по умолчанию 40);
  • месячный бюджет в долларах — VISION_BUDGET_USD (по умолчанию 3.0): расход считается по
    токенам из ответа модели (usage) и ценам ниже. На 80% админы получают одно предупреждение,
    на 100% разбор останавливается до 1-го числа (админам — одно сообщение).
Сутки и месяц — по часовому поясу бота (BOT_TZ, по умолчанию Москва).

Счётчики лежат в той же базе SQLite (таблицы vision_calls и vision_flags создаются сами),
поэтому переживают перезапуск и деплой. Считаются только вызовы, на которые провайдер
ответил (за них списываются деньги); сбои сети и отказы провайдера лимит не тратят.
"""
import os
import math
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import db

logger = logging.getLogger("numbus.quota")


def _env_num(name, default, cast=float, lo=None):
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        v = cast(float(raw.replace(",", ".")))
        if isinstance(v, float) and not math.isfinite(v):
            raise ValueError(raw)
        if lo is not None and v < lo:
            raise ValueError(raw)
        return v
    except (TypeError, ValueError):
        logger.warning("%s=%r не число — беру %s", name, raw, default)
        return default


SAMPLE_PER_BRAND_DAY = _env_num("SAMPLE_PER_BRAND_DAY", 5, int, 0)
SAMPLE_PER_DAY = _env_num("SAMPLE_PER_DAY", 40, int, 0)
VISION_BUDGET_USD = _env_num("VISION_BUDGET_USD", 3.0, float, 0)
WARN_SHARE = 0.8

# Цены, USD за миллион токенов: (вход, выход). Источник — страница моделей Anthropic (октябрь 2026).
PRICES = {
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
}
DEFAULT_PRICE = (3.0, 15.0)        # неизвестная модель — считаем с запасом

SCHEMA = """
CREATE TABLE IF NOT EXISTS vision_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, day TEXT NOT NULL, month TEXT NOT NULL,
    brand_id INTEGER, tg_id INTEGER, model TEXT,
    tokens_in INTEGER, tokens_out INTEGER, usd REAL
);
CREATE INDEX IF NOT EXISTS vision_calls_day ON vision_calls(day);
CREATE INDEX IF NOT EXISTS vision_calls_month ON vision_calls(month);
CREATE TABLE IF NOT EXISTS vision_flags (key TEXT PRIMARY KEY, ts TEXT NOT NULL);
"""


def _tz():
    try:
        return ZoneInfo((os.environ.get("BOT_TZ") or "Europe/Moscow").strip() or "Europe/Moscow")
    except Exception:
        return ZoneInfo("Europe/Moscow")


def _keys(now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(_tz())
    return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")


def _conn():
    c = db._conn()
    c.executescript(SCHEMA)
    return c


def price(model):
    """USD за миллион токенов (вход, выход) для модели; неизвестная — DEFAULT_PRICE."""
    m = (model or "").strip().lower()
    if m in PRICES:
        return PRICES[m]
    for k in sorted(PRICES, key=len, reverse=True):     # снимок с датой, «-latest» и т. п.
        if m.startswith(k):
            return PRICES[k]
    return DEFAULT_PRICE


def _int(v):
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


def cost(model, usage):
    """usage из ответа (Anthropic или OpenAI-формат) → (токены входа, токены выхода, USD)."""
    u = usage if isinstance(usage, dict) else {}
    if "input_tokens" in u or "output_tokens" in u:
        cin = _int(u.get("input_tokens")) + _int(u.get("cache_creation_input_tokens")) * 1.25 + \
            _int(u.get("cache_read_input_tokens")) * 0.1
        tin = _int(u.get("input_tokens")) + _int(u.get("cache_creation_input_tokens")) + \
            _int(u.get("cache_read_input_tokens"))
        tout = _int(u.get("output_tokens"))
    else:
        tin = cin = _int(u.get("prompt_tokens"))
        tout = _int(u.get("completion_tokens"))
    pin, pout = price(model)
    return tin, tout, round((cin * pin + tout * pout) / 1_000_000, 6)


def month_usd(now=None) -> float:
    _, month = _keys(now)
    with _conn() as c:
        return float(c.execute("SELECT COALESCE(SUM(usd),0) FROM vision_calls WHERE month=?", (month,)).fetchone()[0])


def day_count(now=None) -> int:
    day, _ = _keys(now)
    with _conn() as c:
        return int(c.execute("SELECT COUNT(*) FROM vision_calls WHERE day=?", (day,)).fetchone()[0])


def brand_day_count(brand_id, now=None) -> int:
    day, _ = _keys(now)
    with _conn() as c:
        return int(c.execute("SELECT COUNT(*) FROM vision_calls WHERE day=? AND brand_id=?",
                             (day, brand_id)).fetchone()[0])


def check(brand_id, now=None):
    """Можно ли сейчас разбирать образец этого бренда? None — можно, иначе причина:
    "budget" (месячный бюджет исчерпан), "day" (лимит сервера на сегодня), "brand" (лимит бренда)."""
    try:
        if VISION_BUDGET_USD and month_usd(now) >= VISION_BUDGET_USD:
            return "budget"
        if SAMPLE_PER_DAY and day_count(now) >= SAMPLE_PER_DAY:
            return "day"
        if SAMPLE_PER_BRAND_DAY and brand_day_count(brand_id, now) >= SAMPLE_PER_BRAND_DAY:
            return "brand"
    except Exception as e:                      # счётчик сломался — не блокируем клиента
        logger.warning("quota check: %s", e)
    return None


def record(brand_id, tg_id, model, usage, now=None):
    """Записать вызов модели → {tokens_in, tokens_out, usd, month_usd, share}."""
    now = now or datetime.now(timezone.utc)
    day, month = _keys(now)
    tin, tout, usd = cost(model, usage)
    try:
        with _conn() as c:
            c.execute("INSERT INTO vision_calls (ts, day, month, brand_id, tg_id, model, tokens_in, tokens_out, usd) "
                      "VALUES (?,?,?,?,?,?,?,?,?)", (now.isoformat(), day, month, brand_id, tg_id, model or "",
                                                    tin, tout, usd))
        total = month_usd(now)
    except Exception as e:
        logger.warning("quota record: %s", e)
        total = 0.0
    share = (total / VISION_BUDGET_USD) if VISION_BUDGET_USD else 0.0
    return {"tokens_in": tin, "tokens_out": tout, "usd": usd, "month_usd": round(total, 4), "share": share,
            "month": month, "day": day}


def once(key) -> bool:
    """True только в первый раз для ключа: предупреждения админам — по одному на месяц или день."""
    try:
        with _conn() as c:
            cur = c.execute("INSERT OR IGNORE INTO vision_flags (key, ts) VALUES (?,?)",
                            (key, datetime.now(timezone.utc).isoformat()))
            return cur.rowcount == 1
    except Exception as e:
        logger.warning("quota flag: %s", e)
        return False


def keys(now=None):
    """(день, месяц) по часовому поясу бота — для ключей once()."""
    return _keys(now)


def forget_user(tg_id):
    """«Удалить мои данные»: расход остаётся в счётчиках, но без привязки к человеку."""
    try:
        with _conn() as c:
            c.execute("UPDATE vision_calls SET tg_id=0 WHERE tg_id=?", (tg_id,))
    except Exception as e:
        logger.warning("quota forget: %s", e)


def summary(now=None) -> str:
    """Строка для админ-панели: расход за месяц и разборы за сегодня."""
    try:
        return (f"${month_usd(now):.2f} из ${VISION_BUDGET_USD:.2f} за месяц, сегодня разборов: "
                f"{day_count(now)} из {SAMPLE_PER_DAY}")
    except Exception:
        return "—"

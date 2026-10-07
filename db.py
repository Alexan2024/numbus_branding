"""NUMBUS Branding — хранилище (SQLite).

Один файл numbus.db в постоянной папке (Volume на Railway, диск сервера в РФ).
Соединение открывается на каждый вызов — так безопасно при рендере в потоках
и достаточно быстро для бота. Режим WAL: бот и редактор пишут одновременно,
а чтения не ждут записей.
"""
import os
import json
import gzip
import shutil
import hashlib
import secrets
import sqlite3
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)
BASE = os.path.dirname(os.path.abspath(__file__))


def _on_disk(d) -> bool:
    """Папка лежит на подключённом диске (Railway Volume, том docker-compose)?
    Папка внутри контейнера пропадает при деплое — даже если её указали в DATA_DIR."""
    if not d:
        return False
    p = os.path.realpath(d)
    vol = (os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or "").strip()
    if vol:
        v = os.path.realpath(vol)
        if p == v or p.startswith(v.rstrip(os.sep) + os.sep):
            return True
    while p != os.path.dirname(p):          # сама папка или её родитель — точка монтирования;
        if os.path.ismount(p):              # корень «/» есть всегда и диском не считается
            return True
        p = os.path.dirname(p)
    return False


def _resolve_data_dir():
    """DATA_DIR из окружения, иначе Railway Volume (RAILWAY_VOLUME_MOUNT_PATH),
    иначе /data, иначе папка рядом с ботом (эфемерно). Постоянной папка считается,
    только если она на подключённом диске — как бы её ни задали."""
    explicit = (os.environ.get("DATA_DIR") or "").strip()
    vol = (os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or "").strip()
    candidates = ([explicit] if explicit else []) + ([vol] if vol else []) + ["/data", os.path.join(BASE, "data")]
    candidates = [(d, _on_disk(d) if d != os.path.join(BASE, "data") else False) for d in candidates]
    for d, persistent in candidates:
        try:
            os.makedirs(d, exist_ok=True)
            if os.access(d, os.W_OK):
                return d, persistent
        except Exception:
            pass
    return BASE, False


DATA_DIR, STORAGE_PERSISTENT = _resolve_data_dir()
DB_PATH = os.environ.get("NUMBUS_DB") or os.path.join(DATA_DIR, "numbus.db")
BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "backups")
BACKUP_KEEP = 7

# ============ Тарифы ============
# photos — лимит обработанных фото за период подписки (30 дней от активации)
# members — сколько человек в команде бренда (включая владельца)
# brands — сколько брендов в одной подписке. Лимиты фото и людей — на всю подписку.
PLANS = {
    "pilot":  {"photos": 500,  "members": 5,  "brands": 1},
    "solo":   {"photos": 150,  "members": 1,  "brands": 1},
    "media":  {"photos": 1000, "members": 5,  "brands": 1},
    "studio": {"photos": 5000, "members": 20, "brands": 10},
}
PERIOD_DAYS = 30
# Роли в бренде: владелец — всё; дизайнер — редактор стиля и шаблоны, без тарифа и команды;
# участник — только посты
ROLES = ("owner", "designer", "editor")
EDITOR_ROLES = ("owner", "designer")

DEFAULT_KIT = {
    "name": "",
    # p0 — светлый, p1 — тёмный, p2 — акцент, p3–p4 — дополнительные
    "palette": ["#FFFFFF", "#141414", "#EFEAE2", "#8A8A8A", "#FFFFFF"],
    "hashtags": [],
    "custom_fonts": {},    # {"font1": "Название шрифта"}
}
MAX_TEMPLATES = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id INTEGER PRIMARY KEY,
    lang TEXT,
    active_brand INTEGER,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS brands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL,
    kit TEXT NOT NULL,
    plan TEXT NOT NULL,
    plan_until TEXT,
    join_token TEXT UNIQUE,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS members (
    brand_id INTEGER NOT NULL,
    tg_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    PRIMARY KEY (brand_id, tg_id)
);
CREATE TABLE IF NOT EXISTS assets (
    brand_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    data BLOB NOT NULL,
    PRIMARY KEY (brand_id, kind)
);
CREATE TABLE IF NOT EXISTS invites (
    code TEXT PRIMARY KEY,
    plan TEXT NOT NULL,
    days INTEGER NOT NULL,
    max_uses INTEGER NOT NULL,
    uses INTEGER NOT NULL DEFAULT 0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id INTEGER NOT NULL,
    tg_id INTEGER NOT NULL,
    template TEXT NOT NULL,
    photos INTEGER NOT NULL,
    ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_brand_ts ON events (brand_id, ts);
CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    spec TEXT NOT NULL,
    sort INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_templates_brand ON templates (brand_id, sort);
CREATE TABLE IF NOT EXISTS msglog (
    chat_id INTEGER NOT NULL,
    msg_id INTEGER NOT NULL,
    kind TEXT NOT NULL,              -- user | bot | result (готовые файлы)
    ts TEXT NOT NULL,
    PRIMARY KEY (chat_id, msg_id)
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,          -- sha256 от токена: сам токен в базе не хранится
    kind TEXT NOT NULL,              -- link (одноразовая, 15 мин) | session (7 дней)
    tg_id INTEGER NOT NULL,
    brand_id INTEGER NOT NULL,
    expires TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prefs (
    tg_id INTEGER NOT NULL,
    brand_id INTEGER NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (tg_id, brand_id)
);
CREATE TABLE IF NOT EXISTS analytics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    tg_id INTEGER,
    brand_id INTEGER,
    kind TEXT NOT NULL,
    data TEXT
);
CREATE INDEX IF NOT EXISTS idx_analytics_kind_ts ON analytics (kind, ts);
CREATE INDEX IF NOT EXISTS idx_analytics_brand ON analytics (brand_id, ts);
CREATE TABLE IF NOT EXISTS access_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id INTEGER NOT NULL,
    name TEXT,
    username TEXT,
    channel TEXT,
    status TEXT NOT NULL DEFAULT 'new',   -- new | approved | declined
    created_at TEXT NOT NULL,
    handled_at TEXT
);
"""


def _now():
    return datetime.now(timezone.utc)


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=OFF")
    return c


MIGRATIONS = [
    "ALTER TABLE brands ADD COLUMN sub_id INTEGER",        # подписка: id корневого бренда
    "ALTER TABLE users ADD COLUMN name TEXT",
    "ALTER TABLE users ADD COLUMN username TEXT",
    "ALTER TABLE users ADD COLUMN last_seen TEXT",
    "ALTER TABLE members ADD COLUMN joined_at TEXT",
    "ALTER TABLE brands ADD COLUMN period_anchor TEXT",    # начало подписки: от него считаются 30-дневные периоды
    "ALTER TABLE assets ADD COLUMN created_at TEXT",       # когда загружен: свежую графику уборка не трогает
]


def _hash_token(tok: str) -> str:
    return hashlib.sha256((tok or "").encode()).hexdigest()


def init_db():
    with _conn() as c:
        try:
            c.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        c.executescript(SCHEMA)
        for sql in MIGRATIONS:
            try:
                c.execute(sql)
            except sqlite3.OperationalError:
                pass   # колонка уже есть
        c.execute("UPDATE brands SET period_anchor=created_at WHERE period_anchor IS NULL")
        # Файлы, загруженные до появления даты, считаем загруженными сейчас: уборка тронет их не раньше чем через сутки
        c.execute("UPDATE assets SET created_at=? WHERE created_at IS NULL", (_now().isoformat(),))
        # Сессии раньше хранились открытым текстом — заменяем на хэш (вход сохраняется)
        for r in c.execute("SELECT token FROM sessions").fetchall():
            tok = r["token"]
            if not (len(tok) == 64 and all(ch in "0123456789abcdef" for ch in tok)):
                c.execute("UPDATE OR REPLACE sessions SET token=? WHERE token=?", (_hash_token(tok), tok))
    logger.info("БД: %s (%s)", DB_PATH, "постоянная" if STORAGE_PERSISTENT else "ВРЕМЕННАЯ")


# ============ Пользователи ============
def ensure_user(tg_id: int, lang_hint: str = None) -> dict:
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        if row:
            return dict(row)
        # Пилот русскоязычный: английский — только если Telegram прямо говорит «en»,
        # неизвестный или пустой язык — русский
        lang = "en" if (lang_hint or "").strip().lower().startswith("en") else "ru"
        c.execute("INSERT INTO users (tg_id, lang, active_brand, created_at) VALUES (?,?,?,?)",
                  (tg_id, lang, None, _now().isoformat()))
        return {"tg_id": tg_id, "lang": lang, "active_brand": None}


def get_user(tg_id: int):
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        return dict(row) if row else None


def set_lang(tg_id: int, lang: str):
    with _conn() as c:
        c.execute("UPDATE users SET lang=? WHERE tg_id=?", (lang, tg_id))


def set_active_brand(tg_id: int, brand_id):
    with _conn() as c:
        c.execute("UPDATE users SET active_brand=? WHERE tg_id=?", (brand_id, tg_id))


# ============ Бренды ============
def _brand_row(row) -> dict:
    """Бренд с тарифом подписки. Дочерний бренд (sub_id) берёт тариф и срок у корня;
    если тариф корня не допускает несколько брендов — дочерний бренд заблокирован."""
    b = dict(row)
    kit = dict(DEFAULT_KIT)
    try:
        kit.update(json.loads(b["kit"]))
    except Exception:
        pass
    b["kit"] = kit
    root = b.get("sub_id")
    b["root_id"] = root or b["id"]
    b["locked"] = False
    if root and root != b["id"]:
        with _conn() as c:
            r = c.execute("SELECT plan, plan_until, period_anchor FROM brands WHERE id=?", (root,)).fetchone()
        if r:
            b["plan"], b["plan_until"], b["period_anchor"] = r["plan"], r["plan_until"], r["period_anchor"]
            b["locked"] = PLANS.get(r["plan"], PLANS["pilot"])["brands"] <= 1
    return b


def root_id(brand_id: int) -> int:
    with _conn() as c:
        r = c.execute("SELECT sub_id FROM brands WHERE id=?", (brand_id,)).fetchone()
    return (r["sub_id"] if r and r["sub_id"] else brand_id)


def sub_brand_ids(brand_id: int) -> list:
    rid = root_id(brand_id)
    with _conn() as c:
        return [r["id"] for r in c.execute(
            "SELECT id FROM brands WHERE id=? OR sub_id=? ORDER BY id", (rid, rid))]


def sub_brands(brand_id: int) -> list:
    return [get_brand(i) for i in sub_brand_ids(brand_id)]


def create_brand(owner_id: int, plan: str, days: int, sub_id: int = None, lang: str = None, seed: bool = False) -> int:
    """sub_id — добавить бренд в существующую подписку (тариф и срок берутся у неё).
    Стартовые стили создаются, когда клиент выберет свой на фото (или seed=True)."""
    now = _now()
    until = (now + timedelta(days=days)).isoformat()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO brands (owner_id, kit, plan, plan_until, join_token, created_at, sub_id, period_anchor) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (owner_id, json.dumps(DEFAULT_KIT, ensure_ascii=False), plan, until,
             secrets.token_urlsafe(9), now.isoformat(), sub_id, now.isoformat()))
        bid = cur.lastrowid
        c.execute("INSERT INTO members (brand_id, tg_id, role, joined_at) VALUES (?,?,?,?)",
                  (bid, owner_id, "owner", now.isoformat()))
        c.execute("UPDATE users SET active_brand=? WHERE tg_id=?", (bid, owner_id))
    if seed:
        seed_templates(bid, lang or (get_user(owner_id) or {}).get("lang") or "ru")
    return bid


def get_brand(brand_id):
    if not brand_id:
        return None
    with _conn() as c:
        row = c.execute("SELECT * FROM brands WHERE id=?", (brand_id,)).fetchone()
        return _brand_row(row) if row else None


def update_kit(brand_id: int, **changes):
    b = get_brand(brand_id)
    if not b:
        return
    kit = b["kit"]
    kit.update(changes)
    with _conn() as c:
        c.execute("UPDATE brands SET kit=? WHERE id=?", (json.dumps(kit, ensure_ascii=False), brand_id))


def user_brands(tg_id: int) -> list:
    with _conn() as c:
        rows = c.execute(
            "SELECT b.*, m.role FROM brands b JOIN members m ON m.brand_id=b.id "
            "WHERE m.tg_id=? ORDER BY b.id", (tg_id,)).fetchall()
        return [_brand_row(r) for r in rows]


def owned_roots(tg_id: int) -> list:
    """Подписки, которыми человек владеет (корневые бренды)."""
    return [b for b in user_brands(tg_id) if b["role"] == "owner" and b["root_id"] == b["id"]]


def member_role(brand_id: int, tg_id: int):
    with _conn() as c:
        row = c.execute("SELECT role FROM members WHERE brand_id=? AND tg_id=?",
                        (brand_id, tg_id)).fetchone()
        return row["role"] if row else None


def can_edit(brand_id: int, tg_id: int) -> bool:
    return member_role(brand_id, tg_id) in EDITOR_ROLES


def editor_user_ids() -> list:
    """Все, кто может открыть редактор хотя бы одного бренда."""
    with _conn() as c:
        return [r["tg_id"] for r in c.execute(
            "SELECT DISTINCT tg_id FROM members WHERE role IN (%s)" % ",".join("?" * len(EDITOR_ROLES)),
            EDITOR_ROLES)]


def member_count(brand_id: int) -> int:
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM members WHERE brand_id=?", (brand_id,)).fetchone()[0]


def brand_by_token(token: str):
    with _conn() as c:
        row = c.execute("SELECT * FROM brands WHERE join_token=?", (token,)).fetchone()
        return _brand_row(row) if row else None


def add_member(brand_id: int, tg_id: int, role: str = "editor"):
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO members (brand_id, tg_id, role, joined_at) VALUES (?,?,?,?)",
                  (brand_id, tg_id, role, _now().isoformat()))


def remove_member(brand_id: int, tg_id: int):
    """Убирает человека из одного бренда. Если это был его активный бренд — переключает."""
    with _conn() as c:
        c.execute("DELETE FROM members WHERE brand_id=? AND tg_id=? AND role != 'owner'", (brand_id, tg_id))
        c.execute("DELETE FROM sessions WHERE brand_id=? AND tg_id=?", (brand_id, tg_id))
        u = c.execute("SELECT active_brand FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        if u and u["active_brand"] == brand_id:
            nxt = c.execute("SELECT brand_id FROM members WHERE tg_id=? ORDER BY brand_id LIMIT 1", (tg_id,)).fetchone()
            c.execute("UPDATE users SET active_brand=? WHERE tg_id=?", (nxt["brand_id"] if nxt else None, tg_id))


def sub_member_count(brand_id: int) -> int:
    """Сколько разных людей во всех брендах подписки (с владельцем)."""
    ids = sub_brand_ids(brand_id)
    with _conn() as c:
        return c.execute("SELECT COUNT(DISTINCT tg_id) FROM members WHERE brand_id IN (%s)"
                         % ",".join("?" * len(ids)), ids).fetchone()[0]


def list_brands() -> list:
    with _conn() as c:
        rows = c.execute("SELECT * FROM brands ORDER BY id").fetchall()
        return [_brand_row(r) for r in rows]


def find_brands(q: str) -> list:
    """Поиск для поддержки: #id, id, часть названия или @username владельца."""
    q = (q or "").strip()
    if not q:
        return []
    out = []
    if q.lstrip("#").isdigit():
        b = get_brand(int(q.lstrip("#")))
        if b:
            out.append(b)
    ql = q.lower().lstrip("@")
    owners = {}
    with _conn() as c:
        for r in c.execute("SELECT tg_id, username, name FROM users"):
            owners[r["tg_id"]] = ((r["username"] or "") + " " + (r["name"] or "")).lower()
    for b in list_brands():
        if b in out:
            continue
        if ql in (b["kit"].get("name") or "").lower() or ql in owners.get(b["owner_id"], ""):
            out.append(b)
    return out[:10]


def extend_brand(brand_id: int, days: int, plan: str = None) -> bool:
    """Продление подписки. Если срок уже истёк — новый период начинается сейчас."""
    brand_id = root_id(brand_id) if get_brand(brand_id) else brand_id
    b = get_brand(brand_id)
    if not b:
        return False
    now = _now()
    try:
        cur = datetime.fromisoformat(b["plan_until"])
    except Exception:
        cur = now
    lapsed = cur <= now
    base = max(cur, now)
    until = (base + timedelta(days=days)).isoformat()
    with _conn() as c:
        if lapsed:
            c.execute("UPDATE brands SET plan_until=?, plan=?, period_anchor=? WHERE id=?",
                      (until, plan or b["plan"], now.isoformat(), brand_id))
        else:
            c.execute("UPDATE brands SET plan_until=?, plan=? WHERE id=?", (until, plan or b["plan"], brand_id))
    return True


def plan_active(brand: dict) -> bool:
    if brand.get("locked"):
        return False
    try:
        return datetime.fromisoformat(brand["plan_until"]) > _now()
    except Exception:
        return False


# ============ Удаление ============
def _delete_brand_rows(c, bid):
    for sql in ("DELETE FROM templates WHERE brand_id=?", "DELETE FROM assets WHERE brand_id=?",
                "DELETE FROM members WHERE brand_id=?", "DELETE FROM events WHERE brand_id=?",
                "DELETE FROM prefs WHERE brand_id=?", "DELETE FROM sessions WHERE brand_id=?",
                "DELETE FROM analytics WHERE brand_id=?", "DELETE FROM brands WHERE id=?"):
        c.execute(sql, (bid,))
    c.execute("UPDATE users SET active_brand=NULL WHERE active_brand=?", (bid,))


def delete_brand(brand_id: int) -> list:
    """Удаляет бренд со всеми шаблонами, файлами и статистикой. Корень подписки —
    вместе со всеми её брендами. Возвращает id удалённых брендов."""
    b = get_brand(brand_id)
    if not b:
        return []
    ids = sub_brand_ids(brand_id) if b["root_id"] == b["id"] else [brand_id]
    with _conn() as c:
        for bid in ids:
            _delete_brand_rows(c, bid)
    return ids


def delete_user(tg_id: int) -> list:
    """Удаляет всё о человеке: его подписки (со всеми брендами), участие в чужих брендах,
    сессии, настройки, журнал сообщений и статистику. Возвращает id удалённых брендов."""
    gone = []
    for b in owned_roots(tg_id):
        gone += delete_brand(b["id"])
    for b in user_brands(tg_id):          # дочерние бренды в чужих подписках, где он владелец
        if b["role"] == "owner":
            gone += delete_brand(b["id"])
    with _conn() as c:
        for sql in ("DELETE FROM members WHERE tg_id=?", "DELETE FROM sessions WHERE tg_id=?",
                    "DELETE FROM prefs WHERE tg_id=?", "DELETE FROM msglog WHERE chat_id=?",
                    "DELETE FROM analytics WHERE tg_id=?", "DELETE FROM access_requests WHERE tg_id=?",
                    "DELETE FROM users WHERE tg_id=?"):
            c.execute(sql, (tg_id,))
        c.execute("UPDATE events SET tg_id=0 WHERE tg_id=?", (tg_id,))   # фото в чужих брендах — без имени
    return gone


# ============ Ассеты (логотипы, шрифт, фото-образец) ============
def set_asset(brand_id: int, kind: str, data: bytes):
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO assets (brand_id, kind, data, created_at) VALUES (?,?,?,?)",
                  (brand_id, kind, sqlite3.Binary(data), _now().isoformat()))


def get_asset(brand_id: int, kind: str):
    with _conn() as c:
        row = c.execute("SELECT data FROM assets WHERE brand_id=? AND kind=?",
                        (brand_id, kind)).fetchone()
        return bytes(row["data"]) if row else None


def del_asset(brand_id: int, kind: str):
    with _conn() as c:
        c.execute("DELETE FROM assets WHERE brand_id=? AND kind=?", (brand_id, kind))


def has_asset(brand_id: int, kind: str) -> bool:
    with _conn() as c:
        return c.execute("SELECT 1 FROM assets WHERE brand_id=? AND kind=?", (brand_id, kind)).fetchone() is not None


# ============ Инвайт-коды ============
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # без 0/O/1/I — не путаются


def create_invite(plan: str, days: int, max_uses: int = 1) -> str:
    code = "NB-" + "".join(secrets.choice(_ALPHABET) for _ in range(4)) + "-" + \
           "".join(secrets.choice(_ALPHABET) for _ in range(4))
    with _conn() as c:
        c.execute("INSERT INTO invites (code, plan, days, max_uses, uses, created_at) VALUES (?,?,?,?,0,?)",
                  (code, plan, days, max_uses, _now().isoformat()))
    return code


def peek_invite(code: str):
    """Код ещё действует? → (plan, days) без траты использования, иначе None."""
    code = (code or "").strip().upper()
    with _conn() as c:
        row = c.execute("SELECT * FROM invites WHERE code=?", (code,)).fetchone()
    if not row or row["uses"] >= row["max_uses"]:
        return None
    return row["plan"], row["days"]


def redeem_invite(code: str):
    """Атомарно тратит одно использование кода. Возвращает (plan, days) или None."""
    code = (code or "").strip().upper()
    with _conn() as c:
        row = c.execute("SELECT * FROM invites WHERE code=?", (code,)).fetchone()
        if not row or row["uses"] >= row["max_uses"]:
            return None
        cur = c.execute("UPDATE invites SET uses=uses+1 WHERE code=? AND uses<max_uses", (code,))
        if cur.rowcount != 1:
            return None
        return row["plan"], row["days"]


def list_invites() -> list:
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM invites ORDER BY created_at DESC LIMIT 30")]


# ============ Использование: период подписки ============
def period_bounds(brand: dict, now=None):
    """Текущий 30-дневный период подписки: (начало, конец). Считается от даты активации,
    а не от 1-го числа — клиент, подключившийся 25-го, не получает двойной лимит."""
    now = now or _now()
    try:
        anchor = datetime.fromisoformat(brand.get("period_anchor") or brand.get("created_at"))
    except Exception:
        anchor = now
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    if anchor > now:
        return anchor, anchor + timedelta(days=PERIOD_DAYS)
    k = int((now - anchor) / timedelta(days=PERIOD_DAYS))
    start = anchor + timedelta(days=PERIOD_DAYS * k)
    return start, start + timedelta(days=PERIOD_DAYS)


def period_start(brand_id: int):
    b = get_brand(root_id(brand_id))
    return period_bounds(b)[0] if b else _now()


def photos_used(brand_id: int) -> int:
    """Фото за текущий период по всей подписке (все её бренды)."""
    ids = sub_brand_ids(brand_id)
    start = period_start(brand_id).isoformat()
    with _conn() as c:
        return c.execute("SELECT COALESCE(SUM(photos),0) FROM events WHERE brand_id IN (%s) AND ts>=?"
                         % ",".join("?" * len(ids)), (*ids, start)).fetchone()[0]


def record_event(brand_id: int, tg_id: int, template, photos: int):
    """Выдача файлов: сколько фото и по какому шаблону (id) — для лимита и статистики."""
    if photos <= 0:
        return
    with _conn() as c:
        c.execute("INSERT INTO events (brand_id, tg_id, template, photos, ts) VALUES (?,?,?,?,?)",
                  (brand_id, tg_id, str(template), photos, _now().isoformat()))


def regen_token(brand_id: int) -> str:
    tok = secrets.token_urlsafe(9)
    with _conn() as c:
        c.execute("UPDATE brands SET join_token=? WHERE id=?", (tok, brand_id))
    return tok


# ============ Шаблоны ============
def seed_templates(brand_id: int, lang: str = "ru", keys=None):
    """Стартовые стили, подогнанные под бренд: многоцветный логотип (плашка, аватарка) остаётся
    в своих цветах, ширина логотипа — по его пропорциям (spec.brand_traits)."""
    import spec as S
    b = get_brand(brand_id) or {}
    traits = S.brand_traits(get_asset(brand_id, "logo"), (b.get("kit") or {}).get("palette"))
    ids = []
    for key in keys or S.SEED_PRESETS:
        p = S.preset(key)
        if p:
            ids.append(create_template(brand_id, p["name"].get(lang) or p["name"]["ru"], S.preset_spec(key, traits)))
    return ids


def _tpl_row(r):
    t = dict(r)
    try:
        t["spec"] = json.loads(t["spec"])
    except Exception:
        t["spec"] = {"v": 1, "feed": {"layers": []}, "story": {"enabled": False, "layers": []}}
    return t


def list_templates(brand_id: int) -> list:
    with _conn() as c:
        return [_tpl_row(r) for r in c.execute(
            "SELECT * FROM templates WHERE brand_id=? ORDER BY sort, id", (brand_id,))]


def get_template(brand_id: int, tid: int):
    with _conn() as c:
        r = c.execute("SELECT * FROM templates WHERE id=? AND brand_id=?", (tid, brand_id)).fetchone()
        return _tpl_row(r) if r else None


def template_count(brand_id: int) -> int:
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM templates WHERE brand_id=?", (brand_id,)).fetchone()[0]


def create_template(brand_id: int, name: str, spec: dict, first: bool = False):
    """first=True — шаблон встаёт первым (шаблон по умолчанию)."""
    if template_count(brand_id) >= MAX_TEMPLATES:
        return None
    with _conn() as c:
        if first:
            sort = c.execute("SELECT COALESCE(MIN(sort),1)-1 FROM templates WHERE brand_id=?", (brand_id,)).fetchone()[0]
        else:
            sort = c.execute("SELECT COALESCE(MAX(sort),0)+1 FROM templates WHERE brand_id=?", (brand_id,)).fetchone()[0]
        cur = c.execute("INSERT INTO templates (brand_id, name, spec, sort, updated_at) VALUES (?,?,?,?,?)",
                        (brand_id, name[:40], json.dumps(spec, ensure_ascii=False), sort, _now().isoformat()))
        return cur.lastrowid


def update_template(brand_id: int, tid: int, name: str, spec: dict) -> bool:
    with _conn() as c:
        cur = c.execute("UPDATE templates SET name=?, spec=?, updated_at=? WHERE id=? AND brand_id=?",
                        (name[:40], json.dumps(spec, ensure_ascii=False), _now().isoformat(), tid, brand_id))
        return cur.rowcount == 1


def delete_template(brand_id: int, tid: int) -> bool:
    with _conn() as c:
        return c.execute("DELETE FROM templates WHERE id=? AND brand_id=?", (tid, brand_id)).rowcount == 1


# ============ Последние настройки поста (для быстрого режима) ============
def get_prefs(tg_id: int, brand_id: int) -> dict:
    with _conn() as c:
        r = c.execute("SELECT data FROM prefs WHERE tg_id=? AND brand_id=?", (tg_id, brand_id)).fetchone()
    try:
        return json.loads(r["data"]) if r else {}
    except Exception:
        return {}


def set_prefs(tg_id: int, brand_id: int, **changes):
    data = get_prefs(tg_id, brand_id)
    data.update(changes)
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO prefs (tg_id, brand_id, data) VALUES (?,?,?)",
                  (tg_id, brand_id, json.dumps(data, ensure_ascii=False)))


# ============ Графика из импортированных макетов ============
def image_assets(brand_id: int) -> list:
    with _conn() as c:
        return [r["kind"] for r in c.execute(
            "SELECT kind FROM assets WHERE brand_id=? AND kind LIKE 'img\\_%' ESCAPE '\\'", (brand_id,))]


GC_MIN_AGE_HOURS = 24


def gc_images(brand_id: int, min_age_hours: float = GC_MIN_AGE_HOURS) -> int:
    """Удаляет картинки, на которые не ссылается ни один шаблон бренда и которые
    загружены больше суток назад. Свежую не трогаем: её мог только что загрузить
    другой участник, а шаблон с ней ещё не сохранён."""
    used = set()
    for t in list_templates(brand_id):
        for surf in ("feed", "story"):
            for L in t["spec"].get(surf, {}).get("layers", []):
                if L.get("type") == "image":
                    used.add(L.get("asset"))
    cutoff = (_now() - timedelta(hours=min_age_hours)).isoformat()
    with _conn() as c:
        old = [r["kind"] for r in c.execute(
            "SELECT kind FROM assets WHERE brand_id=? AND kind LIKE 'img\\_%' ESCAPE '\\' "
            "AND created_at IS NOT NULL AND created_at < ?", (brand_id, cutoff))]
    dead = [k for k in old if k not in used]
    for k in dead:
        del_asset(brand_id, k)
    return len(dead)


# ============ Вход на компьютере: одноразовая ссылка → сессия ============
# В базе лежит только sha256 от токена: утечка файла базы не даёт войти в чужой редактор.
LINK_TTL_MIN = 15
SESSION_TTL_DAYS = 7


def _token():
    return secrets.token_urlsafe(24)


def create_login_link(tg_id: int, brand_id: int) -> str:
    tok = _token()
    exp = _now() + timedelta(minutes=LINK_TTL_MIN)
    with _conn() as c:
        c.execute("DELETE FROM sessions WHERE expires < ?", (_now().isoformat(),))
        c.execute("INSERT INTO sessions (token, kind, tg_id, brand_id, expires) VALUES (?,?,?,?,?)",
                  (_hash_token(tok), "link", tg_id, brand_id, exp.isoformat()))
    return tok


def redeem_login_link(tok: str):
    """Одноразовая ссылка → (session_token, tg_id, brand_id) или None."""
    h = _hash_token(tok or "")
    with _conn() as c:
        r = c.execute("SELECT * FROM sessions WHERE token=? AND kind='link'", (h,)).fetchone()
        if not r:
            return None
        c.execute("DELETE FROM sessions WHERE token=?", (h,))
        if r["expires"] < _now().isoformat():
            return None
        sess = _token()
        c.execute("INSERT INTO sessions (token, kind, tg_id, brand_id, expires) VALUES (?,?,?,?,?)",
                  (_hash_token(sess), "session", r["tg_id"], r["brand_id"],
                   (_now() + timedelta(days=SESSION_TTL_DAYS)).isoformat()))
        return sess, r["tg_id"], r["brand_id"]


def check_session(tok: str):
    if not tok:
        return None
    with _conn() as c:
        r = c.execute("SELECT * FROM sessions WHERE token=? AND kind='session'", (_hash_token(tok),)).fetchone()
    if not r or r["expires"] < _now().isoformat():
        return None
    return r["tg_id"], r["brand_id"]


def drop_session(tok: str):
    with _conn() as c:
        c.execute("DELETE FROM sessions WHERE token=?", (_hash_token(tok or ""),))


def drop_user_sessions(tg_id: int) -> int:
    """«Выйти на всех компьютерах»."""
    with _conn() as c:
        return c.execute("DELETE FROM sessions WHERE tg_id=?", (tg_id,)).rowcount


def session_count(tg_id: int) -> int:
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM sessions WHERE tg_id=? AND kind='session' AND expires>?",
                         (tg_id, _now().isoformat())).fetchone()[0]


# ============ Админ: режимы подписки для проверки ============
def set_plan(brand_id: int, plan: str) -> bool:
    """Меняет тариф; если подписка уже истекла — заодно продлевает на 30 дней."""
    brand_id = root_id(brand_id)
    b = get_brand(brand_id)
    if not b or plan not in PLANS:
        return False
    with _conn() as c:
        c.execute("UPDATE brands SET plan=? WHERE id=?", (plan, brand_id))
    if not plan_active(get_brand(brand_id)):
        extend_brand(brand_id, 30)
    return True


def expire_brand(brand_id: int):
    brand_id = root_id(brand_id)
    with _conn() as c:
        c.execute("UPDATE brands SET plan_until=? WHERE id=?",
                  ((_now() - timedelta(days=1)).isoformat(), brand_id))


def reset_usage(brand_id: int):
    """Обнуляет счётчик фото за текущий период (всей подписки)."""
    start = period_start(brand_id).isoformat()
    with _conn() as c:
        for bid in sub_brand_ids(brand_id):
            c.execute("DELETE FROM events WHERE brand_id=? AND ts>=?", (bid, start))


def set_member_role(brand_id: int, tg_id: int, role: str):
    if role not in ROLES:
        return
    with _conn() as c:
        c.execute("UPDATE members SET role=? WHERE brand_id=? AND tg_id=?", (role, brand_id, tg_id))


# ============ Журнал сообщений (для очистки чата) ============
# Telegram позволяет боту удалять сообщения в личке не старше 48 часов.
MSG_TTL_HOURS = 47


def log_msg(chat_id: int, msg_id: int, kind: str):
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO msglog (chat_id, msg_id, kind, ts) VALUES (?,?,?,?)",
                  (chat_id, msg_id, kind, _now().isoformat()))


def unlog_msgs(chat_id: int, ids):
    ids = list(ids)
    if not ids:
        return
    with _conn() as c:
        c.executemany("DELETE FROM msglog WHERE chat_id=? AND msg_id=?", [(chat_id, i) for i in ids])


def chat_msgs(chat_id: int, with_results=False) -> list:
    cutoff = (_now() - timedelta(hours=MSG_TTL_HOURS)).isoformat()
    with _conn() as c:
        c.execute("DELETE FROM msglog WHERE ts < ?", (cutoff,))
        q = "SELECT msg_id FROM msglog WHERE chat_id=?" + ("" if with_results else " AND kind != 'result'")
        return [r["msg_id"] for r in c.execute(q + " ORDER BY msg_id", (chat_id,))]


# ============ Команда: имена и активность ============
def touch_user(tg_id: int, name: str, username: str):
    with _conn() as c:
        c.execute("UPDATE users SET name=?, username=?, last_seen=? WHERE tg_id=?",
                  ((name or "")[:64], (username or "")[:64], _now().isoformat(), tg_id))


def team_stats(brand_id: int) -> list:
    """Участники бренда и их активность в этом бренде за текущий период подписки."""
    ms = period_start(brand_id).isoformat()
    with _conn() as c:
        rows = c.execute("""
            SELECT m.tg_id, m.role, m.joined_at, u.name, u.username, u.last_seen,
                   COALESCE(SUM(CASE WHEN e.ts >= ? THEN e.photos END), 0) AS photos_month,
                   COALESCE(SUM(CASE WHEN e.ts >= ? THEN 1 END), 0)        AS posts_month,
                   COALESCE(SUM(e.photos), 0)                              AS photos_total,
                   MAX(e.ts)                                               AS last_post
            FROM members m
            LEFT JOIN users u ON u.tg_id = m.tg_id
            LEFT JOIN events e ON e.brand_id = m.brand_id AND e.tg_id = m.tg_id
            WHERE m.brand_id = ?
            GROUP BY m.tg_id
            ORDER BY (m.role = 'owner') DESC, photos_month DESC, m.joined_at""", (ms, ms, brand_id)).fetchall()
    return [dict(r) for r in rows]


# ============ Аналитика: события пилота ============
# kind: start, access_request, code_ok, kit_name, kit_logo, kit_color, onboard_style, draft,
# preview, pult, files, editor_open, tpl_save, tpl_create, error, delete_brand, delete_user
def log_event(kind: str, tg_id: int = None, brand_id: int = None, **data):
    try:
        with _conn() as c:
            c.execute("INSERT INTO analytics (ts, tg_id, brand_id, kind, data) VALUES (?,?,?,?,?)",
                      (_now().isoformat(), tg_id, brand_id, kind,
                       json.dumps(data, ensure_ascii=False) if data else None))
    except Exception as e:
        logger.warning("analytics %s: %s", kind, e)


def events(kind=None, since=None, brand_id=None) -> list:
    q, args = "SELECT * FROM analytics WHERE 1=1", []
    if kind:
        kinds = [kind] if isinstance(kind, str) else list(kind)
        q += " AND kind IN (%s)" % ",".join("?" * len(kinds))
        args += kinds
    if since:
        q += " AND ts>=?"
        args.append(since.isoformat() if hasattr(since, "isoformat") else since)
    if brand_id:
        q += " AND brand_id=?"
        args.append(brand_id)
    with _conn() as c:
        rows = [dict(r) for r in c.execute(q + " ORDER BY ts, id", args)]
    for r in rows:
        try:
            r["data"] = json.loads(r["data"]) if r["data"] else {}
        except Exception:
            r["data"] = {}
    return rows


def recent_titles(brand_id: int, limit=5) -> list:
    """Последние настоящие заголовки клиента — для превью в редакторе."""
    out = []
    with _conn() as c:
        for r in c.execute("SELECT data FROM analytics WHERE brand_id=? AND kind='draft' ORDER BY id DESC LIMIT 60",
                           (brand_id,)):
            try:
                t = (json.loads(r["data"] or "{}").get("title") or "").strip()
            except Exception:
                t = ""
            if t and t not in out:
                out.append(t)
            if len(out) >= limit:
                break
    return out


# ============ Заявки на доступ ============
def create_request(tg_id: int, name: str, username: str, channel: str) -> int:
    with _conn() as c:
        return c.execute("INSERT INTO access_requests (tg_id, name, username, channel, status, created_at) "
                         "VALUES (?,?,?,?, 'new', ?)",
                         (tg_id, (name or "")[:64], (username or "")[:64], (channel or "")[:300],
                          _now().isoformat())).lastrowid


def get_request(rid: int):
    with _conn() as c:
        r = c.execute("SELECT * FROM access_requests WHERE id=?", (rid,)).fetchone()
        return dict(r) if r else None


def set_request_status(rid: int, status: str) -> bool:
    """Меняет статус, только если заявка ещё новая (два админа не одобрят дважды)."""
    with _conn() as c:
        return c.execute("UPDATE access_requests SET status=?, handled_at=? WHERE id=? AND status='new'",
                         (status, _now().isoformat(), rid)).rowcount == 1


def open_request(tg_id: int):
    with _conn() as c:
        r = c.execute("SELECT * FROM access_requests WHERE tg_id=? AND status='new' ORDER BY id DESC LIMIT 1",
                      (tg_id,)).fetchone()
        return dict(r) if r else None


# ============ Резервные копии ============
def backup(dest_dir: str = None, keep: int = BACKUP_KEEP) -> str:
    """Горячая копия базы (SQLite backup API — без остановки бота), сжатая gzip.
    Хранятся последние keep копий. Возвращает путь к файлу."""
    dest_dir = dest_dir or BACKUP_DIR
    os.makedirs(dest_dir, exist_ok=True)
    stamp = _now().strftime("%Y%m%d-%H%M%S")
    raw = os.path.join(dest_dir, f"numbus-{stamp}.db")
    src = sqlite3.connect(DB_PATH, timeout=30)
    try:
        dst = sqlite3.connect(raw)
        with dst:
            src.backup(dst)
        dst.close()
    finally:
        src.close()
    gz = raw + ".gz"
    with open(raw, "rb") as fi, gzip.open(gz, "wb", compresslevel=6) as fo:
        shutil.copyfileobj(fi, fo)
    os.remove(raw)
    files = sorted(f for f in os.listdir(dest_dir) if f.startswith("numbus-") and f.endswith(".db.gz"))
    for f in files[:-keep]:
        try:
            os.remove(os.path.join(dest_dir, f))
        except OSError:
            pass
    return gz

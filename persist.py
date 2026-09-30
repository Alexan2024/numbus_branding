"""NUMBUS Branding — шаг диалога и короткие настройки человека переживают перезапуск.

Без этого деплой посреди настройки стиля сбрасывал диалог: человек присылал
логотип, а бот уже не ждал его. Храним в той же базе SQLite:
  • состояние диалога (на каком шаге настройки человек);
  • несколько ключей user_data из белого списка (язык, какой бренд настраивается…).
Черновик поста хранится отдельно, на диске (drafts.py).
"""
import json
import time
import logging

from telegram.ext import BasePersistence, PersistenceInput

import db

logger = logging.getLogger("numbus.persist")

KEEP = ("lang", "kit_bid", "wiz", "wiz_t0", "wiz_seen", "wiz_colors", "code", "await")
STATE_TTL = 3600      # как conversation_timeout в bot.py: старый шаг после перезапуска не оживает

SCHEMA = """CREATE TABLE IF NOT EXISTS persist (
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (kind, key)
)"""


def _jsonable(v):
    try:
        json.dumps(v)
        return True
    except (TypeError, ValueError):
        return False


class DbPersistence(BasePersistence):
    def __init__(self, update_interval=30):
        super().__init__(store_data=PersistenceInput(bot_data=False, chat_data=False, user_data=True,
                                                     callback_data=False),
                         update_interval=update_interval)
        with db._conn() as c:
            c.execute(SCHEMA)

    # ---- диалоги ----
    async def get_conversations(self, name):
        """Шаги диалогов. PTB не восстанавливает таймер conversation_timeout после
        перезапуска, поэтому шаги старше STATE_TTL здесь просто отбрасываются."""
        out, stale = {}, []
        now = time.time()
        with db._conn() as c:
            for r in c.execute("SELECT key, data FROM persist WHERE kind=?", ("conv:" + name,)):
                try:
                    key = tuple(int(x) for x in r["key"].split(","))
                    v = json.loads(r["data"])
                    if isinstance(v, dict) and "s" in v:
                        if now - float(v.get("t") or 0) > STATE_TTL:
                            stale.append(r["key"])
                            continue
                        v = v["s"]
                    out[key] = v
                except Exception:
                    continue
            for k in stale:
                c.execute("DELETE FROM persist WHERE kind=? AND key=?", ("conv:" + name, k))
        return out

    async def update_conversation(self, name, key, new_state):
        k = ",".join(str(x) for x in key)
        with db._conn() as c:
            if new_state is None:
                c.execute("DELETE FROM persist WHERE kind=? AND key=?", ("conv:" + name, k))
            elif _jsonable(new_state):
                c.execute("INSERT OR REPLACE INTO persist (kind, key, data) VALUES (?,?,?)",
                          ("conv:" + name, k, json.dumps({"s": new_state, "t": time.time()})))

    # ---- user_data: только белый список ----
    async def get_user_data(self):
        out = {}
        with db._conn() as c:
            for r in c.execute("SELECT key, data FROM persist WHERE kind='user'"):
                try:
                    out[int(r["key"])] = json.loads(r["data"])
                except Exception:
                    continue
        return out

    async def update_user_data(self, user_id, data):
        keep = {k: v for k, v in (data or {}).items() if k in KEEP and _jsonable(v)}
        with db._conn() as c:
            if keep:
                c.execute("INSERT OR REPLACE INTO persist (kind, key, data) VALUES ('user',?,?)",
                          (str(user_id), json.dumps(keep, ensure_ascii=False)))
            else:
                c.execute("DELETE FROM persist WHERE kind='user' AND key=?", (str(user_id),))

    async def drop_user_data(self, user_id):
        with db._conn() as c:
            c.execute("DELETE FROM persist WHERE kind='user' AND key=?", (str(user_id),))

    async def refresh_user_data(self, user_id, user_data):
        pass

    # ---- остальное не храним ----
    async def get_chat_data(self):
        return {}

    async def get_bot_data(self):
        return {}

    async def get_callback_data(self):
        return None

    async def update_chat_data(self, chat_id, data):
        pass

    async def update_bot_data(self, data):
        pass

    async def update_callback_data(self, data):
        pass

    async def drop_chat_data(self, chat_id):
        pass

    async def refresh_chat_data(self, chat_id, chat_data):
        pass

    async def refresh_bot_data(self, bot_data):
        pass

    async def flush(self):
        pass


def forget_user(user_id: int):
    """Удаление аккаунта: стираем и сохранённый шаг диалога."""
    with db._conn() as c:
        c.execute(SCHEMA)
        c.execute("DELETE FROM persist WHERE (kind='user' AND key=?) OR (kind LIKE 'conv:%' AND key LIKE ?)",
                  (str(user_id), f"%,{int(user_id)}"))

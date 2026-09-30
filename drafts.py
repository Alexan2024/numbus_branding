"""NUMBUS Branding — черновики постов на диске.

Пока человек собирает пост (фото, подпись, пульт), черновик живёт в папке
DATA_DIR/drafts/<tg_id>: фото — файлами, настройки — в meta.json. Поэтому
перезапуск бота или деплой не теряют начатый пост, а память сервера не держит
по 30 фото на человека. Черновики старше суток удаляются сами.
"""
import os
import json
import time
import shutil
import secrets
import logging

import db

logger = logging.getLogger("numbus.drafts")
ROOT = os.path.join(db.DATA_DIR, "drafts")
TTL = 24 * 3600
TRANSIENT = ("base", "q_task", "album")     # то, что не сохраняется на диск


def _dir(uid):
    return os.path.join(ROOT, str(int(uid)))


def new(uid, **fields):
    """Новый черновик: прежний (если был) удаляется целиком."""
    clear(uid)
    os.makedirs(_dir(uid), exist_ok=True)
    d = {"id": secrets.token_hex(4), "photos": [], "focus": {}, "cur": 0, "actions": 0,
         "created": time.time(), "ts": time.time(), "sent": False, "msg": None}
    d.update(fields)
    return d


def add_photo(uid, d, data: bytes) -> str:
    os.makedirs(_dir(uid), exist_ok=True)
    path = os.path.join(_dir(uid), f"{len(d['photos']) + 1:02d}_{secrets.token_hex(3)}.img")
    with open(path, "wb") as f:
        f.write(data)
    d["photos"].append(path)
    return path


def read(path) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def save(uid, d):
    try:
        os.makedirs(_dir(uid), exist_ok=True)
        meta = {k: v for k, v in d.items() if k not in TRANSIENT}
        tmp = os.path.join(_dir(uid), "meta.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
        os.replace(tmp, os.path.join(_dir(uid), "meta.json"))
    except Exception as e:
        logger.warning("draft save %s: %s", uid, e)


def load(uid):
    """Черновик с диска (после перезапуска) или None."""
    path = os.path.join(_dir(uid), "meta.json")
    try:
        if time.time() - os.path.getmtime(path) > TTL:
            clear(uid)
            return None
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        d["photos"] = [p for p in d.get("photos", []) if os.path.exists(p)]
        d["focus"] = {str(k): v for k, v in (d.get("focus") or {}).items()}
        return d if d["photos"] else None
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning("draft load %s: %s", uid, e)
        return None


def clear(uid):
    shutil.rmtree(_dir(uid), ignore_errors=True)


def gc(ttl=TTL) -> int:
    """Удаляет черновики, которых не касались дольше ttl секунд."""
    n = 0
    if not os.path.isdir(ROOT):
        return 0
    now = time.time()
    for name in os.listdir(ROOT):
        p = os.path.join(ROOT, name)
        try:
            mt = max((os.path.getmtime(os.path.join(p, f)) for f in os.listdir(p)), default=os.path.getmtime(p))
            if now - mt > ttl:
                shutil.rmtree(p, ignore_errors=True)
                n += 1
        except OSError:
            pass
    return n

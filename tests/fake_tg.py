"""Поддельный сервер Telegram Bot API для проверки бота без сети.

Бот (python-telegram-bot) ходит сюда вместо api.telegram.org: сервер отвечает
правдоподобными сообщениями, запоминает каждый вызов и что сейчас видно в чате.
Апдейты от «пользователя» подаются прямо в Application.process_update.
"""
import io
import json
import time
import asyncio
import itertools

from aiohttp import web
from PIL import Image
from telegram import Update

BOT_USER = {"id": 7000000001, "is_bot": True, "first_name": "NUMBUS", "username": "numbus_test_bot",
            "can_join_groups": False, "can_read_all_group_messages": False, "supports_inline_queries": False}
RAW_TEXT = {"text", "caption", "description", "short_description", "callback_query_id", "filename"}


def _size(data):
    try:
        return Image.open(io.BytesIO(data)).size
    except Exception:
        return (1, 1)


class FakeTG:
    def __init__(self):
        self.calls = []                 # (method, params, files)
        self.msgs = {}                  # (chat_id, message_id) → сообщение
        self.deleted = set()
        self.files = {}                 # file_id → bytes
        self.answers = []               # ответы на нажатия (всплывающие подсказки)
        self._mid = itertools.count(100)
        self._fid = itertools.count(1)
        self.runner = None

    # ---- сервер ----
    async def start(self):
        app = web.Application(client_max_size=300 * 1024 * 1024)
        app.router.add_route("*", "/bot{token}/{method}", self.handle)
        app.router.add_get("/file/bot{token}/{path:.*}", self.file)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.base = f"http://127.0.0.1:{port}/bot"
        self.base_file = f"http://127.0.0.1:{port}/file/bot"

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()

    def register_file(self, data: bytes) -> str:
        fid = f"F{next(self._fid)}"
        self.files[fid] = data
        return fid

    async def file(self, request):
        fid = request.match_info["path"].split("/")[-1]
        if fid not in self.files:
            raise web.HTTPNotFound()
        return web.Response(body=self.files[fid])

    @staticmethod
    def _val(k, v):
        if k in RAW_TEXT:
            return v
        try:
            return json.loads(v)
        except (TypeError, ValueError):
            return v

    async def handle(self, request):
        method = request.match_info["method"]
        params, files = {}, {}
        if request.content_type.startswith("multipart/"):
            reader = await request.multipart()
            while True:
                part = await reader.next()
                if part is None:
                    break
                if part.filename is not None:
                    files[part.name] = (part.filename, bytes(await part.read()))
                else:
                    params[part.name] = await part.text()
        else:
            params = dict(await request.post())
        params = {k: self._val(k, v) for k, v in params.items()}
        self.calls.append((method, params, files))
        fn = getattr(self, "m_" + method, None)
        try:
            result = fn(params, files) if fn else True
            if asyncio.iscoroutine(result):
                result = await result
        except TgError as e:
            return web.json_response({"ok": False, "error_code": 400, "description": "Bad Request: " + str(e)})
        return web.json_response({"ok": True, "result": result})

    # ---- сообщения ----
    def _new(self, chat_id, **content):
        mid = next(self._mid)
        m = {"message_id": mid, "date": int(time.time()), "from": BOT_USER,
             "chat": {"id": int(chat_id), "type": "private", "first_name": "U"}}
        m.update({k: v for k, v in content.items() if v is not None})
        self.msgs[(int(chat_id), mid)] = m
        return m

    def _get(self, p):
        key = (int(p["chat_id"]), int(p["message_id"]))
        if key not in self.msgs or key in self.deleted:
            raise TgError("message to edit not found")
        return self.msgs[key]

    def _media(self, spec, files):
        """InputMedia → (тип, file_id, байты)."""
        ref = spec.get("media")
        if isinstance(ref, str) and ref.startswith("attach://"):
            name = ref[len("attach://"):]
            fname, data = files[name]
        else:
            fname, data = "file", self.files.get(ref, b"")
        fid = self.register_file(data)
        return fid, fname, data

    def _photo_obj(self, fid, data):
        w, h = _size(data)
        return [{"file_id": fid, "file_unique_id": "u" + fid, "width": w, "height": h, "file_size": len(data)}]

    def m_getMe(self, p, f):
        return BOT_USER

    async def m_getUpdates(self, p, f):
        await asyncio.sleep(0.3)          # «длинный опрос» без новых сообщений
        return []

    def m_sendMessage(self, p, f):
        return self._new(p["chat_id"], text=p.get("text"), reply_markup=p.get("reply_markup"))

    def m_sendPhoto(self, p, f):
        fname, data = f["photo"] if "photo" in f else ("photo", self.files.get(p.get("photo"), b""))
        fid = self.register_file(data)
        return self._new(p["chat_id"], photo=self._photo_obj(fid, data), caption=p.get("caption"),
                         reply_markup=p.get("reply_markup"))

    def m_sendDocument(self, p, f):
        fname, data = f["document"]
        fid = self.register_file(data)
        return self._new(p["chat_id"], caption=p.get("caption"),
                         document={"file_id": fid, "file_unique_id": "u" + fid,
                                   "file_name": p.get("filename") or fname, "file_size": len(data)})

    def m_sendMediaGroup(self, p, f):
        media = p["media"]
        if not 2 <= len(media) <= 10:
            raise TgError("wrong number of media in the album")
        gid = str(next(self._mid))
        out = []
        for spec in media:
            fid, fname, data = self._media(spec, f)
            content = {"photo": self._photo_obj(fid, data)} if spec["type"] == "photo" else \
                {"document": {"file_id": fid, "file_unique_id": "u" + fid, "file_name": fname, "file_size": len(data)}}
            out.append(self._new(p["chat_id"], media_group_id=gid, caption=spec.get("caption"), **content))
        return out

    def m_editMessageText(self, p, f):
        m = self._get(p)
        if m.get("text") == p.get("text") and m.get("reply_markup") == p.get("reply_markup"):
            raise TgError("message is not modified")
        if "photo" in m:
            raise TgError("there is no text in the message to edit")
        m["text"] = p.get("text")
        m["reply_markup"] = p.get("reply_markup")
        return m

    def m_editMessageReplyMarkup(self, p, f):
        m = self._get(p)
        if m.get("reply_markup") == p.get("reply_markup"):
            raise TgError("message is not modified")
        m["reply_markup"] = p.get("reply_markup")
        if m["reply_markup"] is None:
            m.pop("reply_markup")
        return m

    def m_editMessageCaption(self, p, f):
        m = self._get(p)
        m["caption"] = p.get("caption")
        m["reply_markup"] = p.get("reply_markup")
        return m

    def m_editMessageMedia(self, p, f):
        m = self._get(p)
        spec = p["media"]
        fid, fname, data = self._media(spec, f)
        m["photo"] = self._photo_obj(fid, data)
        m["caption"] = spec.get("caption")
        m["reply_markup"] = p.get("reply_markup")
        return m

    def m_deleteMessage(self, p, f):
        key = (int(p["chat_id"]), int(p["message_id"]))
        if key not in self.msgs or key in self.deleted:
            raise TgError("message to delete not found")
        self.deleted.add(key)
        return True

    def m_deleteMessages(self, p, f):
        for mid in p["message_ids"]:
            self.deleted.add((int(p["chat_id"]), int(mid)))
        return True

    def m_answerCallbackQuery(self, p, f):
        self.answers.append((p.get("text"), bool(p.get("show_alert"))))
        return True

    def m_getFile(self, p, f):
        fid = p["file_id"]
        return {"file_id": fid, "file_unique_id": "u" + fid, "file_size": len(self.files.get(fid, b"")),
                "file_path": f"docs/{fid}"}

    # ---- что видно в чате ----
    def visible(self, chat_id):
        return [m for (c, mid), m in sorted(self.msgs.items(), key=lambda kv: kv[0][1])
                if c == chat_id and (c, mid) not in self.deleted]

    def last(self, chat_id, pred=None):
        for m in reversed(self.visible(chat_id)):
            if pred is None or pred(m):
                return m
        return None

    def calls_of(self, method):
        return [c for c in self.calls if c[0] == method]


class TgError(Exception):
    pass


def buttons(m):
    rm = (m or {}).get("reply_markup") or {}
    return [b for row in rm.get("inline_keyboard", []) for b in row]


def body(m):
    return (m or {}).get("text") or (m or {}).get("caption") or ""


class Person:
    """Пользователь Telegram, который пишет боту."""
    _upd = itertools.count(1)

    def __init__(self, harness, uid, first="Анна", username=None, lang="ru"):
        self.h, self.id = harness, uid
        self.user = {"id": uid, "is_bot": False, "first_name": first, "language_code": lang}
        if username:
            self.user["username"] = username
        self._mid = itertools.count(1)

    def _message(self, **content):
        m = {"message_id": next(self._mid) + 10_000_000, "date": int(time.time()), "from": self.user,
             "chat": {"id": self.id, "type": "private", "first_name": self.user["first_name"]}}
        m.update(content)
        # сообщение человека тоже «видно» — бот должен его убрать
        self.h.tg.msgs[(self.id, m["message_id"])] = m
        return m

    async def _feed(self, upd):
        upd["update_id"] = next(self._upd)
        await self.h.app.process_update(Update.de_json(upd, self.h.app.bot))
        await self.h.settle(self.id)

    async def text(self, s):
        content = {"text": s}
        if s.startswith("/"):
            content["entities"] = [{"type": "bot_command", "offset": 0, "length": len(s.split()[0])}]
        await self._feed({"message": self._message(**content)})

    async def photo(self, data, caption=None, group=None):
        fid = self.h.tg.register_file(data)
        w, h = _size(data)
        content = {"photo": [{"file_id": fid, "file_unique_id": "u" + fid, "width": w, "height": h,
                              "file_size": len(data)}]}
        if caption:
            content["caption"] = caption
        if group:
            content["media_group_id"] = group
        await self._feed({"message": self._message(**content)})

    async def document(self, data, filename, caption=None, group=None, mime="image/png"):
        fid = self.h.tg.register_file(data)
        content = {"document": {"file_id": fid, "file_unique_id": "u" + fid, "file_name": filename,
                                "mime_type": mime, "file_size": len(data)}}
        if caption:
            content["caption"] = caption
        if group:
            content["media_group_id"] = group
        await self._feed({"message": self._message(**content)})

    def find(self, pred_cb):
        """Последнее видимое сообщение бота с кнопкой, у которой callback_data подходит."""
        for m in reversed(self.h.tg.visible(self.id)):
            for b in buttons(m):
                cb = b.get("callback_data") or ""
                if (pred_cb(cb) if callable(pred_cb) else cb == pred_cb):
                    return m, cb
        return None, None

    async def press(self, cb, message=None):
        """Нажать кнопку: cb — callback_data целиком или функция-фильтр."""
        if message is None:
            message, cb = self.find(cb)
            assert message is not None, f"кнопка {cb!r} не найдена в чате"
        upd = {"callback_query": {"id": str(next(self._upd)), "from": self.user, "chat_instance": "ci",
                                  "data": cb, "message": message}}
        await self._feed(upd)

    async def press_text(self, label_part):
        """Нажать кнопку по части подписи."""
        for m in reversed(self.h.tg.visible(self.id)):
            for b in buttons(m):
                if label_part in b.get("text", "") and b.get("callback_data"):
                    return await self.press(b["callback_data"], m)
        raise AssertionError(f"кнопка с текстом {label_part!r} не найдена")

    def screen(self):
        return self.h.tg.visible(self.id)

    def last_text(self):
        return body(self.h.tg.last(self.id, lambda m: m.get("from", {}).get("is_bot")))


class Harness:
    """Бот + поддельный Telegram в одном цикле событий."""

    def __init__(self, bot_module, persistence=False):
        self.botmod = bot_module
        self.persistence = persistence
        self.tg = FakeTG()
        self.app = None

    async def __aenter__(self):
        await self.tg.start()
        self.app = self.botmod.build_app(token="123456:TEST", base_url=self.tg.base,
                                         base_file_url=self.tg.base_file, persistence=self.persistence)
        await self.app.initialize()
        await self.app.start()
        return self

    async def __aexit__(self, *exc):
        for t in list(self.botmod._TASKS):
            t.cancel()
        try:
            await self.app.stop()
            await self.app.shutdown()
        finally:
            await self.tg.stop()

    async def settle(self, uid=None):
        """Дождаться фоновой перерисовки пульта."""
        for _ in range(3):
            await asyncio.sleep(0)
        task = self.botmod.PULT_TASKS.get(uid) if uid else None
        if task and not task.done():
            await asyncio.wait_for(task, 60)

    def person(self, uid, **kw):
        return Person(self, uid, **kw)

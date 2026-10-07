"""v6 (пилот): сценарии бота, найденные на QA перед пилотом. Каждый — отдельный тест,
чтобы ошибка не вернулась."""
import io

from PIL import Image

import bot
import db
from fake_tg import Harness, buttons, body
from conftest import make_photo, make_logo
from test_bot_flows import onboard, run, fast, texts_of  # noqa: F401  (fast — autouse-фикстура)

ADMIN = 1


def _pult(h, uid):
    return h.tg.last(uid, lambda m: "photo" in m and any((b.get("callback_data") or "").startswith("q:")
                                                         for b in buttons(m)))


# ---------- 1. Карусель больше 10 фото: Telegram делит её на альбомы по 10 ----------
def test_split_album_12_and_30(fresh_db):
    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 1901)
            for i in range(10):
                await u.photo(make_photo(600, 750, seed=i), group="ALB1",
                              caption="Карусель из 12 фото\n\nПодзаголовок #новости" if i == 0 else None)
            for i in range(10, 12):
                await u.photo(make_photo(600, 750, seed=i), group="ALB2")      # второй альбом, без подписи
            d = h.app.user_data[1901]["q"]
            assert len(d["photos"]) == 12
            assert d["title"] == "Карусель из 12 фото" and d["tag"] == "#новости"
            assert "1 / 12" in [b["text"] for b in buttons(_pult(h, 1901))]
            assert len(db.events("draft")) == 1
            await u.press("q:done")
            for a in range(4):                                                 # 40 фото: берутся 30
                for i in range(10):
                    await u.photo(make_photo(400, 500, seed=50 + a * 10 + i), group=f"T{a}",
                                  caption="Тридцать фото" if (a == 0 and i == 0) else None)
            d = h.app.user_data[1901]["q"]
            assert len(d["photos"]) == bot.MAX_BATCH and d["title"] == "Тридцать фото"
            assert any("до 30 фото" in (c[1].get("text") or "") for c in h.tg.calls_of("sendMessage"))
    run(go())


def test_new_captioned_album_is_a_new_post(fresh_db):
    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 1902)
            await u.photo(make_photo(600, 750, seed=1), group="A", caption="Первый пост")
            await u.photo(make_photo(600, 750, seed=2), group="B", caption="Второй пост")
            d = h.app.user_data[1902]["q"]
            assert d["title"] == "Второй пост" and len(d["photos"]) == 1
    run(go())


# ---------- 2. Длинная подпись: целиком в тексте поста и в альбоме для канала ----------
BODY = ("Работы начнутся 1 апреля и продлятся до осени. На время ремонта закроют проезд по Набережной "
        "улице от моста до площади, автобусы 5 и 12 пойдут в объезд. Пешеходная зона останется открытой. "
        "Подрядчик обещает, что шумные работы будут идти только днём. Обсуждение проекта прошло в марте, "
        "жители предложили добавить велодорожку и освещение — оба пункта вошли в проект.")


def test_long_caption_kept_in_post_text_and_channel(fresh_db):
    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 1911)
            await u.photo(make_photo(seed=1), caption="Набережную закроют на ремонт\n\n" + BODY + "\n\n#город")
            pult = _pult(h, 1911)
            assert "в тексте поста — полностью" in body(pult)
            await u.press("q:send")
            pre = [c[1]["text"] for c in h.tg.calls_of("sendMessage") if "<pre>" in (c[1].get("text") or "")][-1]
            assert "вошли в проект" in pre or "вошли в проект" in pre        # конец текста на месте
            await u.press("q:chan")
            cap = h.tg.calls_of("sendPhoto")[-1][1].get("caption")
            assert cap.endswith("#город") and "велодорожку" in cap
    run(go())


def test_caption_over_1024_goes_as_separate_message(fresh_db):
    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 1912)
            long_body = (BODY + " ") * 4
            await u.photo(make_photo(seed=1), caption="Заголовок\n\n" + long_body + "#город")
            await u.press("q:send")
            n_msgs = len(h.tg.calls_of("sendMessage"))
            await u.press("q:chan")
            assert h.tg.calls_of("sendPhoto")[-1][1].get("caption") in (None, "")
            after = [c[1].get("text") or "" for c in h.tg.calls_of("sendMessage")[n_msgs:]]
            assert any(t.endswith("#город") and "<pre>" not in t for t in after)
            assert any("1024" in t for t in after)                              # подсказка, что произошло
    run(go())


def test_image_text_cuts_on_word_and_strips_emoji():
    s = bot.image_text("слово " * 80)
    assert len(s) <= bot.TEXT_MAX + 1 and s.endswith("…") and not s.endswith(" …")
    assert bot.image_text("Лето 🔥☀️ уже здесь 🎉") == "Лето уже здесь"
    assert bot.image_text("Семья 👨‍👩‍👧 и флаг 🇷🇺!") == "Семья и флаг !"
    assert bot.image_text("Цена 100 ₽ — №1 © «кавычки»") == "Цена 100 ₽ — №1 © «кавычки»"
    t, sub, tag, tags, cut = bot.parse_text("А" * 400)
    assert len(t) == 400 and cut                                               # в черновике — целиком
    assert bot.image_text("*выделено " + "слово " * 80 + "*").count("*") == 2  # выделение закрыто


def test_emoji_kept_in_post_text_not_on_image(fresh_db):
    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 1913)
            await u.photo(make_photo(seed=2), caption="Лето 🔥 уже здесь")
            d = h.app.user_data[1913]["q"]
            c = bot._ctx_for(bot.web.brand_ctx(bid), d, 1, 1, 0.0)
            assert c.fields["title"] == "Лето уже здесь"
            assert "🔥" in bot.post_text(d)
    run(go())


# ---------- 3. Мастер настройки: неожиданные ответы ----------
async def _to_step(h, uid, step):
    code = db.create_invite("pilot", 30, 1)
    u = h.person(uid, first="Мария", username=f"user{uid}")
    await u.text("/start")
    await u.text(code)
    if step >= 2:
        await u.text("Тест")
    if step >= 3:
        await u.document(make_logo(), "logo.png")
    if step >= 4:
        await u.press("wz:c:0")
    return u


def test_wizard_reasks_step_question(fresh_db):
    async def go():
        async with Harness(bot) as h:
            u = await _to_step(h, 2001, 1)
            await u.photo(make_photo(seed=1), caption="Пост")                  # фото вместо названия
            assert "Шаг 1 из 4" in u.last_text() and "q" not in h.app.user_data[2001]
            u = await _to_step(h, 2002, 2)
            await u.text("у меня нет логотипа")                                # текст вместо логотипа
            assert "Шаг 2 из 4" in u.last_text()
            assert not any("Чтобы сделать пост" in t for t in texts_of(u))
            u = await _to_step(h, 2003, 4)
            await u.text("какой лучше?")                                       # текст вместо фото
            assert "Шаг 4 из 4" in u.last_text()
            assert u.find("wz:skip")[0] is not None
    run(go())


def test_logo_as_photo_then_file_replaces_logo(fresh_db):
    async def go():
        async with Harness(bot) as h:
            u = await _to_step(h, 2011, 2)
            await u.photo(make_logo(transparent=False))                        # сжатое фото — принято
            b = db.user_brands(2011)[0]
            first = db.get_asset(b["id"], "logo")
            assert first and "Шаг 3 из 4" in body(h.tg.last(2011, lambda m: "photo" in m))
            await u.document(make_logo(color=(20, 90, 200)), "logo.png")       # как советовали — файлом
            assert db.get_asset(b["id"], "logo") != first
            assert "q" not in h.app.user_data[2011]                             # не стал постом
            assert not db.list_templates(b["id"])
            assert u.find(lambda c: c.startswith("wz:c:"))[0] is not None
    run(go())


def test_bad_file_at_step4_leaves_question(fresh_db, monkeypatch):
    monkeypatch.setattr(bot, "NOTICE_TTL", 0.05)

    async def go():
        async with Harness(bot) as h:
            u = await _to_step(h, 2021, 4)
            await u.document(b"%PDF-1.4 not an image", "photo.pdf", mime="application/pdf")
            import asyncio
            await asyncio.sleep(0.3)
            assert any("Шаг 4 из 4" in t for t in texts_of(u))
    run(go())


def test_help_keeps_wizard_question(fresh_db):
    async def go():
        async with Harness(bot) as h:
            u = await _to_step(h, 2031, 2)
            await u.text("/help")
            assert any("Шаг 2 из 4" in t for t in texts_of(u))
    run(go())


def test_step4_album_and_repeat_do_not_multiply(fresh_db):
    async def go():
        async with Harness(bot) as h:
            u = await _to_step(h, 2041, 4)
            for i in range(3):
                await u.photo(make_photo(seed=20 + i), caption="Альбом" if i == 0 else None, group="WG")
            assert len(h.tg.calls_of("sendMediaGroup")) == 1
            await u.photo(make_photo(seed=30), caption="Другое фото")          # новое фото — заменяет примеры
            assert len(h.tg.calls_of("sendMediaGroup")) == 2
            picks = [m for m in h.tg.visible(2041) if any((b.get("callback_data") or "").startswith("wz:s:")
                                                           for b in buttons(m))]
            albums = [m for m in h.tg.visible(2041) if m.get("media_group_id")]
            assert len(picks) == 1 and len(albums) == 3
    run(go())


def test_no_duplicate_starter_styles(fresh_db):
    async def go():
        async with Harness(bot) as h:
            u = await _to_step(h, 2051, 4)
            b = db.user_brands(2051)[0]
            db.seed_templates(b["id"])                                         # стартовые уже есть
            await u.photo(make_photo(seed=1), caption="Фото")
            await u.press("wz:s:caption")
            names = [t["name"] for t in db.list_templates(b["id"])]
            assert len(names) == len(set(names)), names
            assert names[0] == bot.S.preset_name("caption", "ru")
    run(go())


# ---------- 5. Текст при открытом превью — новый «Текст» ----------
def test_text_with_open_preview_becomes_title(fresh_db):
    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 2101)
            await u.photo(make_photo(seed=1), caption="Опечатка в заголвке")
            await u.text("Опечатка в заголовке\n\nПодзаголовок #рубрика")
            d = h.app.user_data[2101]["q"]
            assert d["title"] == "Опечатка в заголовке" and d["subtitle"] == "Подзаголовок" and d["tag"] == "#рубрика"
            assert not any("Подпись станет заголовком" in t for t in texts_of(u))       # не подсказка «пришлите фото»
            assert _pult(h, 2101)["message_id"] == d["msg"]
    run(go())


# ---------- 8. Видео, стикер, голосовое — «Я работаю с фото» ----------
def test_non_photo_messages_get_photo_hint(fresh_db):
    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 2201)
            for content in ({"video": {"file_id": "V1", "file_unique_id": "uV1", "width": 720, "height": 1280,
                                       "duration": 5}},
                            {"sticker": {"file_id": "S1", "file_unique_id": "uS1", "type": "regular", "width": 512,
                                         "height": 512, "is_animated": False, "is_video": False}},
                            {"voice": {"file_id": "VO", "file_unique_id": "uVO", "duration": 3}}):
                await u._feed({"message": u._message(**content)})
                assert "оформляет только фото" in u.last_text(), content
            await u.document(b"\x00\x00\x00\x18ftypmp42" + b"0" * 100, "clip.mp4", mime="video/mp4")
            assert "оформляет только фото" in u.last_text()
            assert not any("устарела" in t for t in texts_of(u))
    run(go())


# ---------- 9. Коды доступа в любом написании ----------
def test_invite_code_anywhere_and_normalised(fresh_db):
    async def go():
        async with Harness(bot) as h:
            code = db.create_invite("pilot", 30, 1)
            u = h.person(2301)
            await u.text("/start")
            await u.text("Привет! Мне дали доступ")                         # не код — подсказка формата
            assert "NB-XXXX-XXXX" in u.last_text()
            assert u.find("acc:req")[0] is not None
            await u.text("NB-ZZZZ-ZZZZ")                                    # похоже на код, но неверный
            assert "не подошёл" in u.last_text()
            spaced = code.replace("-", " ").lower()
            await u.text(spaced)
            assert "Шаг 1 из 4" in u.last_text()
            code2 = db.create_invite("pilot", 30, 1)
            v = h.person(2302)
            await v.text(f"Ваш код доступа к NUMBUS: {code2}\nОткройте @bot, нажмите «Старт» и отправьте этот код.")
            assert "Шаг 1 из 4" in v.last_text()
    run(go())


def test_find_code_variants():
    assert bot.find_code("nb-ab2c-de3f") == "NB-AB2C-DE3F"
    assert bot.find_code("NB AB2C DE3F") == "NB-AB2C-DE3F"
    assert bot.find_code("NB—AB2C—DE3F") == "NB-AB2C-DE3F"
    assert bot.find_code("NB-АВ2С-DE3F") == "NB-AB2C-DE3F"                  # кириллица в коде
    assert bot.find_code("NB-ABC") is None and bot.find_code("XNB-ABCD-EFGH") is None


# ---------- 10. 9:16 у стиля со сторис — поверхность сторис ----------
def test_916_renders_story_surface(fresh_db):
    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 2401)
            spec = bot.S.sanitize_spec({
                "feed": {"layers": [dict(type="rect", id="f", anchor="tl", x=0, y=0, w=1, h=0.3,
                                         color={"mode": "fixed", "value": "#00FF00"}, opacity=1)]},
                "story": {"enabled": True, "layers": [dict(type="rect", id="s", anchor="tl", x=0, y=0, w=1, h=0.3,
                                                           color={"mode": "fixed", "value": "#FF0000"}, opacity=1)]}})
            tid = db.create_template(bid, "Сторис", spec, first=True)
            db.set_prefs(2401, bid, tid=tid, fmt="9:16")
            await u.photo(make_photo(1200, 1600, seed=1), caption="Сторис")
            d = h.app.user_data[2401]["q"]
            assert d["fmt"] == "9:16" and d["tid"] == tid
            prev = Image.open(io.BytesIO(h.tg.files[_pult(h, 2401)["photo"][0]["file_id"]])).convert("RGB")
            r, g, b = prev.getpixel((prev.width // 2, 5))
            assert r > 200 and g < 60, (r, g, b)                           # красный слой сторис, не зелёный ленты
            await u.press("q:send")
            docs = h.tg.calls_of("sendDocument")
            assert len(docs) == 1 and "_story" not in docs[-1][2]["document"][0]
            im = Image.open(io.BytesIO(docs[-1][2]["document"][1]))
            assert im.size == bot.R.STORY_SIZE
            await u.press("q:fmt")
            assert u.find("qf:9:16")[0] is not None                       # 9:16 доступен и у стиля со сторис
    run(go())


# ---------- 11. HEIC — рабочая копия JPEG ----------
def test_heic_stored_as_working_jpeg(fresh_db, monkeypatch):
    im = Image.open(io.BytesIO(make_photo(2400, 3200, seed=9)))
    buf = io.BytesIO()
    im.save(buf, "HEIF")

    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 2501)
            await u.document(buf.getvalue(), "IMG_1.HEIC", caption="HEIC", mime="image/heic")
            d = h.app.user_data[2501]["q"]
            data = bot.drafts.read(d["photos"][0])
            assert data[:2] == b"\xff\xd8"
            w, hh = Image.open(io.BytesIO(data)).size
            assert max(w, hh) <= bot.WORK_LONG and (w, hh) == (2400, 3200)
            jpg = make_photo(1600, 2000, seed=1)
            assert bot.working_copy(jpg) is jpg                            # JPEG — как есть
    run(go())


# ---------- 12. Переменные окружения не роняют бота ----------
def test_bad_env_falls_back(tmp_path):
    import json as _json
    import os
    import subprocess
    import sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, BACKUP_HOUR="", BOT_TZ="Mars/Base", PILOT_DAYS="30d", RENDER_WORKERS="0", PORT="abc",
               BOT_TOKEN=" 123:abc \n", DATA_DIR=str(tmp_path), NUMBUS_DB=str(tmp_path / "n.db"))
    code = ("import bot, json; print(json.dumps([bot.BACKUP_HOUR, str(bot.TZ), bot.PILOT_DAYS, "
            "bot.RENDER_SEM._value, bot.PORT, bot.TOKEN]))")
    out = subprocess.run([sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert _json.loads(out.stdout.strip().splitlines()[-1]) == [4, "Europe/Moscow", 30, 2, 8080, "123:abc"]
    assert "BOT_TZ" in out.stderr and "PORT" in out.stderr


# ---------- 13. Временный диск — админам предупреждение при запуске ----------
def test_temporary_storage_warns_admins_once(fresh_db, monkeypatch):
    monkeypatch.setattr(db, "STORAGE_PERSISTENT", False)
    monkeypatch.setattr(bot, "STORAGE_WARNED", False)

    async def go():
        async with Harness(bot) as h:
            await bot.post_init(h.app)
            await bot.post_init(h.app)
            said = [c[1].get("text") or "" for c in h.tg.calls_of("sendMessage") if c[1].get("chat_id") == ADMIN]
            assert len([t for t in said if "диск не подключён" in t]) == 1
    run(go())


# ---------- 14. Оповещение об ошибке читается ----------
def test_error_alert_is_readable(fresh_db, monkeypatch):
    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 2601, pick="editorial")
            await u.photo(make_photo(seed=5), caption="Пост")
            bot._ALERTS.clear()
            monkeypatch.setattr(bot, "pult_kb", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("сбой пульта")))
            await u.press("q:darker")
            alert = [c[1].get("text") or "" for c in h.tg.calls_of("sendMessage") if c[1].get("chat_id") == ADMIN][-1]
            assert "RuntimeError" in alert and "сбой пульта" in alert
            assert "bot.py" in alert and "Обработчик: <code>on_quick_cb</code>" in alert
            assert "кнопка q:darker" in alert and "2601" in alert and "Мария" in alert
    run(go())


# ---------- P2 ----------
def test_files_again_does_not_double_count(fresh_db):
    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 2701)
            for i in range(2):
                await u.photo(make_photo(600, 750, seed=i), group="G", caption="Два фото" if i == 0 else None)
            await u.press("q:send")
            assert db.photos_used(bid) == 2
            await u.press("q:send")                                         # «Файлы ещё раз»
            assert db.photos_used(bid) == 2
            assert len(h.tg.calls_of("sendMediaGroup")) == 2 + 1           # 1 — альбом стилей мастера
    run(go())


def test_menu_before_setup_and_reset_date(fresh_db):
    async def go():
        async with Harness(bot) as h:
            code = db.create_invite("pilot", 30, 1)
            u = h.person(2801)
            await u.text("/start")
            await u.text(code)
            await u.text("/cancel")
            menu = h.tg.last(2801, lambda m: "Фото:" in body(m))
            assert "Новый бренд" in body(menu) and "<b>—</b>" not in body(menu)
            assert "счётчик обновится" not in body(menu)                   # период = срок пилота
    run(go())

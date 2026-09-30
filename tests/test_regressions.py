"""Ошибки, найденные ревью ветки pilot-prep: каждая — отдельный сценарий, чтобы не вернулись."""
import io
import time
import asyncio

from PIL import Image

import bot
import db
import render as R
from fake_tg import Harness, body
from conftest import make_photo, make_logo
from test_bot_flows import onboard, run, fast  # noqa: F401  (fast — autouse-фикстура)

ADMIN = 1


def test_persistence_survives_posts(fresh_db):
    """Задача перерисовки в user_data ломала сохранение для всех после первого поста."""
    async def go():
        async with Harness(bot, persistence=True) as h:
            u, bid = await onboard(h, 701)
            await u.photo(make_photo(seed=1), caption="Пост")
            await h.app.update_persistence()             # раньше: TypeError при копировании user_data
            code = db.create_invite("pilot", 30, 1)
            v = h.person(702)
            await v.text("/start")
            await v.text(code)
            await h.app.update_persistence()
            with db._conn() as c:
                assert c.execute("SELECT 1 FROM persist WHERE kind='conv:main' AND key='702,702'").fetchone()
    run(go())


def test_broken_photo_is_dropped_with_notice(fresh_db):
    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 711)
            good = make_photo(seed=2)
            await u.photo(good[: len(good) // 3], caption="Битое фото")     # обрезанный JPEG
            said = [c[1].get("text") or "" for c in h.tg.calls_of("sendMessage") if c[1].get("chat_id") == 711]
            assert any("не открылось" in t for t in said)
            assert "q" not in h.app.user_data[711]
            await u.photo(good, caption="Хорошее")
            assert h.tg.last(711, lambda m: "photo" in m and m.get("reply_markup"))
    run(go())


def test_pult_text_while_waiting_for_code(fresh_db):
    """«Ввести код», потом пульт «Текст»: текст — заголовок, а не код."""
    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 721)
            await u.press_text("Ввести код")
            await u.photo(make_photo(seed=3), caption="Старый")
            await u.press("q:text")
            await u.text("Новый заголовок")
            assert h.app.user_data[721]["q"]["title"] == "Новый заголовок"
            assert "не подошёл" not in u.last_text()
    run(go())


def test_admin_search_while_in_code_state(fresh_db):
    async def go():
        async with Harness(bot) as h:
            owner, bid = await onboard(h, 731, name="Зерно")
            adm = h.person(ADMIN, first="Алекс")
            await adm.text("/start")                     # у админа нет бренда: диалог ждёт код
            await adm.text("/admin")
            await adm.press("adm:find")
            await adm.text("Зерно")
            assert f"#{bid}" in body(h.tg.last(ADMIN))
    run(go())


def test_cancel_outside_conversation(fresh_db):
    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 741)
            await u.photo(make_photo(seed=4), caption="Пост")
            await u.press("q:text")
            assert h.app.user_data[741].get("await") == "text"
            await u.text("/cancel")
            assert not h.app.user_data[741].get("await")
    run(go())


def test_removed_member_pult_is_stale(fresh_db):
    async def go():
        async with Harness(bot) as h:
            owner, bid = await onboard(h, 751)
            m = h.person(752)
            await m.text(f"/start j_{db.get_brand(bid)['join_token']}")
            await m.photo(make_photo(seed=5), caption="Пост участника")
            db.remove_member(bid, 752)
            await m.press("q:send")
            assert not [c for c in h.tg.calls_of("sendDocument") if c[1].get("chat_id") == 752]
            assert db.photos_used(bid) == 0
    run(go())


def test_network_error_keeps_photo_and_tells_user(fresh_db, monkeypatch):
    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 761)

            async def slow(*a, **kw):
                raise bot.TimedOut("download timed out")
            monkeypatch.setattr(bot, "get_file_bytes", slow)
            await u.photo(make_photo(seed=6), caption="Не скачалось")
            said = [c[1].get("text") or "" for c in h.tg.calls_of("sendMessage") if c[1].get("chat_id") == 761]
            assert any("Пришлите его ещё раз" in t for t in said)
            mine = [mid for (c, mid), m in h.tg.msgs.items() if c == 761 and "photo" in m and not m["from"].get("is_bot")]
            assert (761, max(mine)) not in h.tg.deleted         # фото человека осталось — можно переслать
    run(go())


def test_brand_edit_reaches_open_pult(fresh_db):
    """Пульт открыт, в редакторе сменили акцент — файлы уже в новом цвете."""
    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 771, pick="caption")        # плашка заголовка — цвет акцента p2
            await u.photo(make_photo(seed=7), caption="Цвет плашки")
            pal = db.get_brand(bid)["kit"]["palette"]
            pal[2] = "#00C853"
            db.update_kit(bid, palette=pal)
            await u.press("q:send")
            doc = h.tg.calls_of("sendDocument")[-1][2]["document"][1]
            im = Image.open(io.BytesIO(doc)).convert("RGB")
            w, hgt = im.size
            px = im.getpixel((int(w * 0.03), int(hgt * 0.93)))   # левый нижний угол — плашка
            assert px[1] > 150 and px[0] < 90, px
    run(go())


def test_abandoned_wizard_after_restart_does_not_eat_posts(fresh_db):
    async def go():
        code = db.create_invite("pilot", 30, 1)
        async with Harness(bot, persistence=True) as h:
            u = h.person(781)
            await u.text("/start")
            await u.text(code)
            await u.text("Бренд")
            await u.document(make_logo(), "logo.png")
            await u.press("wz:c:0")                    # шаг 4: ждём фото для стилей
            h.app.user_data[781]["wiz_seen"] = time.time() - 2 * 3600   # вопрос задан два часа назад
            await h.app.update_persistence()
        async with Harness(bot, persistence=True) as h2:
            u = h2.person(781)
            await u.photo(make_photo(seed=8), caption="Обычный пост")
            assert not h2.tg.calls_of("sendMediaGroup"), "фото ушло в мастер как образец"
            assert h2.tg.last(781, lambda m: "photo" in m and m.get("reply_markup")), "нет пульта"
    run(go())


def test_mpo_photo_decoded_small():
    im = Image.new("RGB", (6000, 8000), (120, 90, 60))
    buf = io.BytesIO()
    im.save(buf, "MPO", quality=80)
    out = R.open_photo(buf.getvalue())
    assert out.size[0] <= 3000                      # декодировано уменьшенным, как JPEG


def test_stale_redraw_does_not_touch_new_post(fresh_db, monkeypatch):
    """Рендер занят, человек успел прислать второй пост: старая перерисовка не удаляет новый
    черновик и не оставляет лишний пульт."""
    from telegram import Update
    monkeypatch.setattr(bot, "REFRESH_DELAY", 0.3)

    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 791)

            def upd(caption, seed, n):
                data = make_photo(seed=seed)
                fid = h.tg.register_file(data)
                msg = u._message(photo=[{"file_id": fid, "file_unique_id": "u" + fid, "width": 1600, "height": 2000,
                                         "file_size": len(data)}], caption=caption)
                return Update.de_json({"update_id": 95000 + n, "message": msg}, h.app.bot)
            await bot.RENDER_SEM.acquire()
            await bot.RENDER_SEM.acquire()                # рендер занят чужим альбомом
            try:
                await h.app.update_queue.put(upd("Пост 1", 1, 1))
                await asyncio.sleep(0.8)
                await h.app.update_queue.put(upd("Пост 2", 2, 2))
                await asyncio.sleep(0.8)
            finally:
                bot.RENDER_SEM.release()
                bot.RENDER_SEM.release()
            await asyncio.sleep(3)
            q = h.app.user_data[791].get("q")
            assert q and q["title"] == "Пост 2"
            assert all(__import__("os").path.exists(p) for p in q["photos"])
            pults = [m for m in h.tg.visible(791) if "photo" in m and m.get("reply_markup")]
            assert len(pults) == 1 and pults[0]["message_id"] == q["msg"]
            said = [c[1].get("text") or "" for c in h.tg.calls_of("sendMessage") if c[1].get("chat_id") == 791]
            assert not any("не открылось" in t for t in said)
    run(go())


def test_button_error_does_not_keep_next_message(fresh_db, monkeypatch):
    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 801, pick="editorial")      # у «Редакции» есть «Светлее / Темнее»
            await u.photo(make_photo(seed=5), caption="Пост")
            real = bot.pult_kb
            monkeypatch.setattr(bot, "pult_kb", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("сбой")))
            await u.press("q:darker")
            monkeypatch.setattr(bot, "pult_kb", real)
            assert not h.app.user_data[801].get("_keep_msg")
            await u.text("/help")
            mid = max(k[1] for k in h.tg.msgs if k[0] == 801 and k[1] > 10_000_000)
            assert (801, mid) in h.tg.deleted
    run(go())


def test_invite_code_beats_open_pult_question(fresh_db):
    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 811)
            await u.photo(make_photo(seed=4), caption="Пост")
            await u.press("q:text")
            code = db.create_invite("media", 60, 1)
            await u.text(code)
            assert h.app.user_data[811]["q"]["title"] == "Пост"          # код не стал заголовком
            assert u.find(lambda c: c.startswith("code:ext:"))[0] is not None   # предложено продлить
            await u.press(f"code:ext:{bid}")
            assert db.get_brand(bid)["plan"] == "media"
    run(go())

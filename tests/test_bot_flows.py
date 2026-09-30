"""Сквозные сценарии бота на поддельном Telegram: доступ, настройка стиля, пост,
карусель, лимиты, команда, настройки, админка, перезапуск посреди настройки."""
import asyncio

import pytest

import bot
import db
import spec as S
from fake_tg import Harness, buttons, body
from conftest import make_photo, make_logo, SVG_LOGO

ADMIN = 1


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(bot, "REFRESH_DELAY", 0.01)
    bot.MENU_BTN.clear()
    bot.MENU_MSG.clear()
    bot.PROMPTS.clear()


def texts_of(p):
    return [body(m) for m in p.screen() if m.get("from", {}).get("is_bot")]


async def onboard(h, uid, name="Кофейня Зерно", logo=None, pick="caption"):
    """Код → название → логотип → цвет 1 → фото → стиль. Возвращает id бренда."""
    code = db.create_invite("pilot", 30, 1)
    u = h.person(uid, first="Мария", username=f"user{uid}")
    await u.text("/start")
    await u.text(code)
    await u.text(name)
    await u.document(logo or make_logo(), "logo.png")
    m, cb = u.find(lambda c: c.startswith("wz:c:0"))
    assert m is not None, texts_of(u)
    await u.press("wz:c:0")
    await u.photo(make_photo(seed=uid), caption="Открываемся в субботу\n\nЖдём всех #новости")
    await u.press(f"wz:s:{pick}")
    b = db.user_brands(uid)[0]
    return u, b["id"]


def test_code_onboarding(fresh_db):
    async def go():
        async with Harness(bot) as h:
            code = db.create_invite("pilot", 30, 1)
            u = h.person(501, first="Мария", username="maria")
            await u.text("/start")
            welcome = h.tg.last(501)
            assert "NUMBUS" in body(welcome)
            assert {b["callback_data"] for b in buttons(welcome)} >= {"acc:req", "menu:code"}
            await u.text(code.lower())                       # код в любом регистре
            assert "Шаг 1 из 4" in u.last_text()
            await u.text("Кофейня Зерно")
            assert "Шаг 2 из 4" in u.last_text()
            await u.document(make_logo(color=(30, 140, 90)), "logo.png")
            swatch = h.tg.last(501, lambda m: "photo" in m)
            assert swatch and "Шаг 3 из 4" in body(swatch)
            cbs = [b["callback_data"] for b in buttons(swatch)]
            assert "wz:c:0" in cbs and "wz:c:own" in cbs
            await u.press("wz:c:own")
            await u.text("#12")                               # плохой цвет
            assert "Не понял цвет" in u.last_text()
            await u.text("#E4572E")
            assert "Шаг 4 из 4" in u.last_text()
            await u.photo(make_photo(seed=3), caption="Открываемся в субботу #новости")
            groups = h.tg.calls_of("sendMediaGroup")
            assert len(groups) == 1 and len(groups[0][1]["media"]) == 3
            pick = h.tg.last(501, lambda m: any(b.get("callback_data", "").startswith("wz:s:") for b in buttons(m)))
            assert [b["callback_data"] for b in buttons(pick)] == [f"wz:s:{k}" for k in S.ONBOARD_PRESETS]
            await u.press("wz:s:frame")
            b = db.user_brands(501)[0]
            tpls = db.list_templates(b["id"])
            assert [t["name"] for t in tpls][0] == S.preset_name("frame", "ru")
            assert len(tpls) == 3
            assert b["kit"]["name"] == "Кофейня Зерно"
            assert b["kit"]["palette"][2] == "#E4572E"
            assert db.has_asset(b["id"], "logo") and db.has_asset(b["id"], "sample")
            done = h.tg.last(501, lambda m: "Готово: стиль" in body(m))
            assert done is not None
            assert any("web_app" in bt for bt in buttons(done))  # «Тонкая настройка» открывает редактор
            menu = h.tg.last(501)
            assert "Кофейня Зерно" in body(menu)
            # альбом со стилями и вопросы мастера убраны из чата
            assert not any("Шаг" in t for t in texts_of(u))
            kinds = [e["kind"] for e in db.events()]
            for k in ("start", "code_ok", "kit_name", "kit_logo", "kit_color", "onboard_style", "onboard_done"):
                assert k in kinds, k
            # кнопка «Редактор» у поля ввода — владельцу
            btn_calls = [c for c in h.tg.calls_of("setChatMenuButton") if c[1].get("chat_id") == 501]
            assert btn_calls and btn_calls[-1][1]["menu_button"]["type"] == "web_app"
            # код одноразовый
            v = h.person(502)
            await v.text("/start")
            await v.text(code)
            assert "не подошёл" in v.last_text()
    run(go())


def test_svg_logo_and_skip(fresh_db):
    async def go():
        async with Harness(bot) as h:
            code = db.create_invite("pilot", 30, 1)
            u = h.person(511)
            await u.text("/start")
            await u.text(code)
            await u.text("Лес")
            await u.document(SVG_LOGO, "logo.svg", mime="image/svg+xml")
            swatch = h.tg.last(511, lambda m: "photo" in m)
            assert swatch is not None, texts_of(u)
            await u.press("wz:c:skip")
            await u.press("wz:skip")
            b = db.user_brands(511)[0]
            assert len(db.list_templates(b["id"])) == len(S.SEED_PRESETS)
            assert db.get_brand(b["id"])["kit"]["palette"][2] == db.DEFAULT_KIT["palette"][2]
    run(go())


def test_post_flow(fresh_db):
    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 521)
            await u.photo(make_photo(1600, 1200, seed=5),
                          caption="Новый сезон кофе — уже в меню\n\nДетали в профиле #новости #кофе")
            pult = h.tg.last(521, lambda m: "photo" in m and buttons(m))
            assert pult is not None, texts_of(u)
            cbs = [b["callback_data"] for b in buttons(pult)]
            for need in ("q:tpl", "q:fmt", "q:text", "q:send", "q:done"):
                assert need in cbs, (need, cbs)
            # горизонтальное фото в 4:5 обрезается — есть кнопки выбора части
            assert any(c.startswith("q:fc:") for c in cbs)
            assert "Уходит" in body(pult)
            await u.press("q:fc:0.0")
            d = h.app.user_data[521]["q"]
            assert d["focus"]["0"] == [0.0, 0.5]
            await u.press("q:tpl")
            await u.press(lambda c: c.startswith("qt:") and c != f"qt:{d['tid']}")
            await u.press("q:fmt")
            await u.press("qf:1:1")
            assert h.app.user_data[521]["q"]["fmt"] == "1:1"
            await u.press("q:tag")
            await u.press("qh:c:1")
            assert h.app.user_data[521]["q"]["tag"] == "#кофе"
            await u.press("q:text")
            await u.text("Другой заголовок для поста")
            assert h.app.user_data[521]["q"]["title"] == "Другой заголовок для поста"
            await u.press("q:send")
            docs = h.tg.calls_of("sendDocument")
            assert len(docs) == 1
            assert docs[0][2]["document"][0].endswith(".jpg")
            pre = [c for c in h.tg.calls_of("sendMessage") if "<pre>" in (c[1].get("text") or "")]
            assert pre and "для\u00a0поста" in pre[-1][1]["text"]          # текст поста прошёл типограф
            assert "#кофе" in pre[-1][1]["text"]
            assert db.photos_used(bid) == 1
            pult2 = h.tg.last(521, lambda m: "photo" in m and buttons(m))
            cbs2 = [b["callback_data"] for b in buttons(pult2)]
            assert "q:chan" in cbs2 and "q:done" in cbs2
            await u.press("q:chan")
            assert h.tg.calls_of("sendPhoto")[-1][1].get("caption", "").startswith("Другой")
            await u.press("q:done")
            assert "q" not in h.app.user_data[521]
            ev = db.events("files")[-1]["data"]
            assert ev["n"] == 1 and ev["fmt"] == "1:1" and ev["actions"] >= 4
    run(go())


def test_album_limit_and_partial(fresh_db, monkeypatch):
    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 531)
            monkeypatch.setitem(db.PLANS, "pilot", dict(db.PLANS["pilot"], photos=2))
            for i in range(3):
                await u.photo(make_photo(1200, 1500, seed=10 + i), caption="Карусель" if i == 0 else None,
                              group="G1")
            d = h.app.user_data[531]["q"]
            assert len(d["photos"]) == 3
            pult = h.tg.last(531, lambda m: "photo" in m and buttons(m))
            assert "q:next" in [b["callback_data"] for b in buttons(pult)]
            await u.press("q:next")
            assert h.app.user_data[531]["q"]["cur"] == 1
            await u.press("q:send")
            assert "осталось 2 фото" in u.last_text()
            await u.press("q:part")
            groups = h.tg.calls_of("sendMediaGroup")
            assert len(groups[-1][1]["media"]) == 2
            assert db.photos_used(bid) == 2
            await u.press("q:send")
            assert "закончились" in u.last_text()
    run(go())


def test_team_roles(fresh_db):
    async def go():
        async with Harness(bot) as h:
            owner, bid = await onboard(h, 541)
            b = db.get_brand(bid)
            m = h.person(542, first="Иван", username="ivan")
            await m.text(f"/start j_{b['join_token']}")
            assert db.member_role(bid, 542) == "editor"
            assert any("Иван" in (c[1].get("text") or "") for c in h.tg.calls_of("sendMessage")
                       if c[1].get("chat_id") == 541)
            menu = h.tg.last(542)
            assert not any("web_app" in x for x in buttons(menu))      # участнику редактор не показываем
            await owner.press("menu:team")
            await owner.press("tm:542")
            await owner.press("tm:542:designer")
            assert db.member_role(bid, 542) == "designer"
            btn = [c for c in h.tg.calls_of("setChatMenuButton") if c[1].get("chat_id") == 542]
            assert btn[-1][1]["menu_button"]["type"] == "web_app"
            await m.text("/start")
            assert any("web_app" in x for x in buttons(h.tg.last(542)))
            await owner.press("tm:542:del")
            await owner.press("tm:542:delyes")
            assert db.member_role(bid, 542) is None
            btn = [c for c in h.tg.calls_of("setChatMenuButton") if c[1].get("chat_id") == 542]
            assert btn[-1][1]["menu_button"]["type"] == "default"
            assert db.get_brand(bid)["join_token"] != b["join_token"]
    run(go())


def test_access_request(fresh_db):
    async def go():
        async with Harness(bot) as h:
            u = h.person(551, first="Олег", username="oleg")
            await u.text("/start")
            await u.press("acc:req")
            await u.text("https://t.me/oleg_channel — городской канал")
            assert "Заявка отправлена" in u.last_text()
            to_admin = [c for c in h.tg.calls_of("sendMessage") if c[1].get("chat_id") == ADMIN]
            assert to_admin and "Заявка на доступ" in to_admin[-1][1]["text"]
            await u.text("/start")
            await u.press("acc:req")
            assert "уже у нас" in u.last_text()
            adm = h.person(ADMIN, first="Алекс")
            msg = h.tg.msgs[(ADMIN, max(mid for (c, mid) in h.tg.msgs if c == ADMIN))]
            rid = to_admin[-1][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"].split(":")[2]
            await adm.press(f"acc:ok:{rid}", msg)
            assert db.user_brands(551)
            await adm.press(f"acc:ok:{rid}", msg)                 # второй раз — «уже есть решение»
            assert h.tg.answers[-1][0] and "решение" in h.tg.answers[-1][0]
            await u.press("wiz:start")
            assert "Шаг 1 из 4" in u.last_text()
    run(go())


def test_settings_language_and_delete(fresh_db):
    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 561)
            await u.press("set:show")
            await u.press("set:lang")
            assert db.get_user(561)["lang"] == "en"
            assert "Settings" in u.last_text()
            await u.press("set:delb")
            await u.press(f"set:delbyes:{bid}")
            assert db.get_brand(bid) is None
            assert db.list_templates(bid) == []
            await u.press("set:show")
            await u.press("set:delme")
            await u.press("set:delmeyes")
            assert db.get_user(561) is None or not db.user_brands(561)
            assert "deleted" in u.last_text()
    run(go())


def test_admin_tools(fresh_db):
    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 571)
            await u.photo(make_photo(seed=7), caption="Пост для статистики")
            await u.press("q:send")
            adm = h.person(ADMIN, first="Алекс")
            await adm.text("/stats")
            st = [c[1]["text"] for c in h.tg.calls_of("sendMessage") if c[1].get("chat_id") == ADMIN][-1]
            assert "Активация" in st and "Кофейня Зерно" in st
            await adm.text("/brands")
            await adm.text("/newcode media 60 2")
            assert "NB-" in [c[1]["text"] for c in h.tg.calls_of("sendMessage") if c[1].get("chat_id") == ADMIN][-1]
            await adm.text("/admin")
            await adm.press("adm:find")
            await adm.text("Зерно")
            card = h.tg.last(ADMIN)
            assert f"#{bid}" in body(card)
            await adm.press(f"adm:bx:{bid}", card)
            await adm.text("/backup")
            docs = [c for c in h.tg.calls_of("sendDocument") if c[1].get("chat_id") == ADMIN]
            assert docs and docs[-1][2]["document"][0].endswith(".db.gz")
            # чужой не видит админку
            await u.text("/stats")
            assert not [c for c in h.tg.calls_of("sendMessage") if c[1].get("chat_id") == 571
                        and "Активация" in (c[1].get("text") or "")]
    run(go())


def test_restart_mid_wizard(fresh_db):
    async def go():
        code = db.create_invite("pilot", 30, 1)
        async with Harness(bot, persistence=True) as h:
            u = h.person(581)
            await u.text("/start")
            await u.text(code)
            await u.text("После перезапуска")
        bot.MENU_BTN.clear()
        async with Harness(bot, persistence=True) as h2:        # новый процесс, та же база
            u = h2.person(581)
            await u.document(make_logo(), "logo.png")
            swatch = h2.tg.last(581, lambda m: "photo" in m)
            assert swatch is not None and "Шаг 3 из 4" in body(swatch)
            await u.press("wz:c:0")
            assert "Шаг 4 из 4" in u.last_text()
            b = db.user_brands(581)[0]
            assert b["kit"]["name"] == "После перезапуска"
    run(go())


def test_draft_survives_restart(fresh_db):
    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 591)
            await u.photo(make_photo(seed=9), caption="Черновик до деплоя")
            pult_id = h.app.user_data[591]["q"]["msg"]
        async with Harness(bot) as h2:
            u = h2.person(591)
            h2.tg.msgs[(591, pult_id)] = {"message_id": pult_id, "date": 0, "chat": {"id": 591, "type": "private"},
                                          "photo": [{"file_id": "x", "file_unique_id": "x", "width": 1, "height": 1}],
                                          "reply_markup": {"inline_keyboard": [[{"text": "Файлы", "callback_data": "q:send"}]]}}
            await u.press("q:send", h2.tg.msgs[(591, pult_id)])
            assert h2.tg.calls_of("sendDocument"), "черновик с диска не подхватился"
    run(go())


def test_profile_and_menu_buttons_on_start(fresh_db):
    async def go():
        async with Harness(bot) as h:
            db.ensure_user(601, "ru")
            db.create_brand(601, "pilot", 30)
            await bot.post_init(h.app)
            for t in list(bot._TASKS):
                await t
            names = [c[0] for c in h.tg.calls]
            for m in ("setMyShortDescription", "setMyDescription", "setMyCommands", "setChatMenuButton"):
                assert m in names, m
            default = [c for c in h.tg.calls_of("setChatMenuButton") if "chat_id" not in c[1]]
            assert default and default[0][1]["menu_button"]["type"] == "default"
            owner = [c for c in h.tg.calls_of("setChatMenuButton") if c[1].get("chat_id") == 601]
            assert owner and owner[-1][1]["menu_button"]["type"] == "web_app"
            admin_cmds = [c for c in h.tg.calls_of("setMyCommands") if (c[1].get("scope") or {}).get("type") == "chat"]
            assert admin_cmds and any(x["command"] == "stats" for x in admin_cmds[0][1]["commands"])
            assert h.app.job_queue.get_jobs_by_name("backup") and h.app.job_queue.get_jobs_by_name("drafts_gc")
    run(go())


def test_error_reaches_user_and_admin(fresh_db):
    async def boom(update, ctx):
        raise RuntimeError("проверка оповещения")

    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 611)
            for hd in h.app.handlers[0]:                  # обработчик текста падает
                if getattr(hd, "callback", None) and hd.callback.__name__ == "on_quick_text":
                    hd.callback = boom
            bot._ALERTS.clear()
            await u.text("просто текст")
            user_msgs = [c[1]["text"] for c in h.tg.calls_of("sendMessage") if c[1].get("chat_id") == 611]
            assert any("пошло не так" in t for t in user_msgs)
            admin_msgs = [c[1]["text"] for c in h.tg.calls_of("sendMessage") if c[1].get("chat_id") == ADMIN]
            assert any("проверка оповещения" in t for t in admin_msgs)
            n = len(admin_msgs)
            await u.text("ещё раз")                          # та же ошибка — админу не чаще раза в 10 минут
            assert len([c for c in h.tg.calls_of("sendMessage") if c[1].get("chat_id") == ADMIN]) == n
    run(go())

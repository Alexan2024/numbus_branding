"""Запуск bot.py целиком: редактор поднимается сразу, бот ждёт связи с Telegram
и подключается через TG_API_URL; SIGTERM завершает всё аккуратно."""
import os
import sys
import socket
import signal
import asyncio

import aiohttp

from fake_tg import FakeTG

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def health(port):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"http://127.0.0.1:{port}/healthz", timeout=aiohttp.ClientTimeout(total=2)) as r:
                return await r.json()
    except Exception:
        return None


async def wait_for(pred, timeout=40):
    for _ in range(int(timeout * 10)):
        v = await pred()
        if v:
            return v
        await asyncio.sleep(0.1)
    return None


def test_full_start_and_stop(tmp_path):
    async def go():
        tg = FakeTG()
        await tg.start()
        tg_port = int(tg.base.split(":")[2].split("/")[0])
        dead_port = free_port()
        web_port = free_port()
        env = dict(os.environ, BOT_TOKEN="123456:TEST", PORT=str(web_port), DATA_DIR=str(tmp_path),
                   NUMBUS_DB=str(tmp_path / "n.db"), TG_API_URL=f"http://127.0.0.1:{dead_port}",
                   WEBAPP_URL="https://numbus.test", ADMIN_IDS="1")
        # 1) Telegram недоступен: редактор работает, бот сообщает «нет связи»
        p = await asyncio.create_subprocess_exec(sys.executable, os.path.join(ROOT, "bot.py"), env=env, cwd=ROOT,
                                                 stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        st = await wait_for(lambda: _state(web_port, "нет связи"))
        assert st, "редактор не поднялся или не сообщил о связи"
        p.send_signal(signal.SIGTERM)
        assert await asyncio.wait_for(p.wait(), 20) == 0
        # 2) Telegram доступен через TG_API_URL: профиль и команды выставлены, опрос идёт
        env["TG_API_URL"] = f"http://127.0.0.1:{tg_port}"
        p = await asyncio.create_subprocess_exec(sys.executable, os.path.join(ROOT, "bot.py"), env=env, cwd=ROOT,
                                                 stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        st = await wait_for(lambda: _state(web_port, "ok"))
        assert st, "бот не подключился"
        ok = await wait_for(lambda: _calls(tg, "getUpdates"))
        assert ok
        for m in ("getMe", "setMyDescription", "setMyCommands", "setChatMenuButton"):
            assert tg.calls_of(m), m
        p.send_signal(signal.SIGTERM)
        assert await asyncio.wait_for(p.wait(), 30) == 0
        await tg.stop()
    asyncio.run(go())


async def _state(port, want):
    h = await health(port)
    return h if h and h.get("telegram") == want else None


async def _calls(tg, method):
    return bool(tg.calls_of(method))

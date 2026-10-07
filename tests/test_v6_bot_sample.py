"""v6: «Стиль по образцу» в эксплуатации — лимиты и бюджет, кэш описания, вызов модели вне
RENDER_SEM, сбои провайдера отдельно от плохого образца, устойчивость к ответу модели."""
import asyncio
import io
import json
import os
import subprocess
import sys
import time

import httpx
import pytest

import bot
import db
import quota
import sample as SM
from fake_tg import Harness, buttons, body
from conftest import make_photo
from test_bot_flows import onboard, run, fast  # noqa: F401
from test_sample import _sample, _desc_text, _photo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESC = {"photo": {"mode": "full"}, "layers": [_desc_text([0.05, 0.06, 0.8, 0.08], ["Образец заголовка"])]}


def _sample_jpeg(seed_title="Образец заголовка"):
    truth = dict(type="text", id="t", source="title", anchor="tl", x=0.07, y=0.1, font="inter", weight=700, size=0.07,
                 maxw=0.9, color={"mode": "fixed", "value": "#FFFFFF"})
    img = _sample([truth], {"title": seed_title})
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


@pytest.fixture()
def vision(monkeypatch, fresh_db):
    monkeypatch.setattr(SM, "VISION_API_KEY", "sk-ant-test")
    monkeypatch.setattr(SM, "VISION_MODEL", "claude-sonnet-5-5")
    monkeypatch.setattr(SM, "VISION_PROVIDER", "anthropic")
    monkeypatch.setattr(SM, "VISION_BASE_URL", "")
    monkeypatch.setattr(bot, "SAMPLE_DIR", str(fresh_db / "samples"))
    bot._ALERTS.clear()
    calls = []

    def fake(img, usage=None):
        calls.append(time.time())
        d = json.loads(json.dumps(DESC))
        d["_meta"] = {"model": "claude-sonnet-5-5", "usage": usage or {"input_tokens": 2000, "output_tokens": 1500},
                      "stop_reason": "end_turn"}
        return d
    monkeypatch.setattr(SM, "ask_model", fake)
    return calls


def _admin_texts(h):
    return [c[1].get("text") or "" for c in h.tg.calls_of("sendMessage") if c[1].get("chat_id") == 1]


# ---------- a. лимиты и бюджет ----------
def test_cost_table():
    assert quota.price("claude-sonnet-5-5") == (2.0, 10.0)
    assert quota.price("claude-haiku-4-5-20251001") == (1.0, 5.0)
    assert quota.price("claude-opus-5-5") == (4.0, 20.0)
    assert quota.price("gpt-something") == (3.0, 15.0)
    assert quota.cost("claude-sonnet-5-5", {"input_tokens": 1_000_000, "output_tokens": 100_000})[2] == 3.0
    assert quota.cost("x", {"prompt_tokens": 1000, "completion_tokens": 1000})[2] == pytest.approx(0.018)


def test_brand_daily_cap(vision, monkeypatch):
    monkeypatch.setattr(quota, "SAMPLE_PER_BRAND_DAY", 2)
    data = _sample_jpeg()

    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 3101)
            for i in range(2):
                await u.press("smp:start")
                await u.document(_sample_jpeg(f"Образец {i}"), "s.jpg")
            assert len(vision) == 2
            await u.press("smp:start")                                   # третий за день — отказ сразу
            assert "дневной лимит" in u.last_text().lower()
            assert h.app.user_data[3101].get("await") != "sample"
            assert len(vision) == 2
            ev = db.events("sample_call")
            assert len(ev) == 2 and ev[0]["data"]["tokens_in"] == 2000 and ev[0]["data"]["usd"] > 0
    run(go())


def test_budget_warn_and_stop(vision, monkeypatch):
    monkeypatch.setattr(quota, "VISION_BUDGET_USD", 0.05)
    big = {"input_tokens": 2000, "output_tokens": 4000}               # 0.004 + 0.04 = 0.044 → 88%

    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 3111)
            monkeypatch.setattr(SM, "ask_model", lambda img: dict(json.loads(json.dumps(DESC)), _meta={
                "model": "claude-sonnet-5-5", "usage": big}))
            await u.press("smp:start")
            await u.document(_sample_jpeg("Один"), "s.jpg")
            warn = [t for t in _admin_texts(h) if "80%" in t]
            assert len(warn) == 1
            await u.press("smp:start")
            await u.document(_sample_jpeg("Два"), "s.jpg")             # 0.088 ≥ 0.05 — бюджет исчерпан
            stop = [t for t in _admin_texts(h) if "остановлен" in t]
            assert len(stop) == 1
            await u.press("smp:start")
            assert "бюджет" in u.last_text()
            await u.press("smp:start")
            assert len([t for t in _admin_texts(h) if "остановлен" in t]) == 1     # оповещение одно
            assert len([t for t in _admin_texts(h) if "80%" in t]) == 1
    run(go())


# ---------- b. кэш описания: «Собрать ещё раз» не платит ----------
def test_again_uses_cache(vision):
    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 3121)
            await u.press("smp:start")
            await u.document(_sample_jpeg(), "s.jpg")
            for _ in range(3):
                await u.press("smp:again")
            assert len(vision) == 1
            res = h.tg.last(3121, lambda m: "photo" in m and buttons(m))
            assert "smp:save" in [b["callback_data"] for b in buttons(res)]
            await u.press("smp:start")                                   # тот же образец ещё раз — тоже из кэша
            await u.document(_sample_jpeg(), "s.jpg")
            assert len(vision) == 1
            assert len(db.events("sample_call")) == 1
    run(go())


# ---------- c. модель — вне RENDER_SEM ----------
def test_model_call_does_not_hold_render_slot(vision, monkeypatch):
    monkeypatch.setattr(bot, "RENDER_SEM", asyncio.Semaphore(1))

    def slow(img):
        time.sleep(2.5)
        return dict(json.loads(json.dumps(DESC)), _meta={"model": "m", "usage": {}})
    monkeypatch.setattr(SM, "ask_model", slow)

    async def go():
        async with Harness(bot) as h:
            a, _ = await onboard(h, 3131)
            c, _ = await onboard(h, 3132)
            await a.press("smp:start")
            t0 = time.time()
            task = asyncio.create_task(a.document(_sample_jpeg(), "s.jpg"))
            await asyncio.sleep(0.6)                                     # модель «думает»
            await c.photo(make_photo(800, 1000, seed=3), caption="Пост другого клиента")
            took = time.time() - t0
            assert h.tg.last(3132, lambda m: "photo" in m and m.get("reply_markup")), "нет превью"
            assert took < 2.4, took
            await task
    run(go())


# ---------- d. сбои провайдера и плохой образец ----------
class Resp:
    def __init__(self, status, payload=None, text=None, headers=None):
        self.status_code = status
        self._p = payload
        self.text = text if text is not None else json.dumps(payload)
        self.headers = headers or {}

    def json(self):
        if self._p is None:
            raise ValueError("no json")
        return self._p


def _err(status, typ, msg, headers=None):
    return Resp(status, {"type": "error", "error": {"type": typ, "message": msg}}, headers=headers)


def _ok(text, stop="end_turn", content=None):
    return Resp(200, {"model": "claude-sonnet-5-5", "stop_reason": stop,
                      "usage": {"input_tokens": 1000, "output_tokens": 200},
                      "content": content or [{"type": "text", "text": text}]})


@pytest.fixture()
def anth(monkeypatch):
    monkeypatch.setattr(SM, "VISION_API_KEY", "sk-ant-test")
    monkeypatch.setattr(SM, "VISION_MODEL", "claude-sonnet-5-5")
    monkeypatch.setattr(SM, "VISION_PROVIDER", "anthropic")
    monkeypatch.setattr(SM, "VISION_BASE_URL", "")
    monkeypatch.setattr(SM, "VISION_THINKING", "off")
    monkeypatch.setattr(SM.time, "sleep", lambda s: None)
    sent = []

    def install(*responses):
        seq = list(responses)

        def post(url, headers=None, json=None, timeout=None):
            sent.append(json)
            r = seq.pop(0) if len(seq) > 1 else seq[0]
            if isinstance(r, Exception):
                raise r
            return r
        monkeypatch.setattr(httpx, "post", post)
    return install, sent


@pytest.mark.parametrize("resp,code,status", [
    (_err(400, "invalid_request_error", "Your credit balance is too low to access the Anthropic API."), "model_http", 400),
    (_err(401, "authentication_error", "invalid x-api-key"), "model_http", 401),
    (_err(403, "permission_error", "forbidden"), "model_http", 403),
    (_err(404, "not_found_error", "model: claude-x"), "model_http", 404),
    (_err(429, "rate_limit_error", "rate"), "model_http", 429),
    (_err(500, "api_error", "internal"), "model_http", 500),
    (_err(529, "overloaded_error", "Overloaded"), "model_http", 529),
    (Resp(200, None, text="<html>502 Bad Gateway</html>"), "model_body", 200),
    (_ok('{"photo": {"mode": "fu', stop="max_tokens"), "model_cut", 200),
    (httpx.ReadTimeout("timed out"), "model_net", None),
    (httpx.ConnectError("[Errno -2] Name or service not known"), "model_net", None),
])
def test_provider_failures_are_provider(anth, resp, code, status):
    install, sent = anth
    install(resp)
    with pytest.raises(SM.SampleError) as e:
        SM.ask_model(_photo(300, 400))
    assert e.value.provider and e.value.code == code and e.value.status == status
    if status in (429, 500, 529):
        assert len(sent) == 2                                     # один повтор
    elif code != "model_net":
        assert len(sent) == 1
    if status == 400:
        assert "credit balance" in str(e.value)


def test_bad_sample_is_not_provider(anth):
    install, sent = anth
    install(_ok("I'm sorry, I can't find a layout here."))
    with pytest.raises(SM.SampleError) as e:
        SM.ask_model(_photo(300, 400))
    assert not e.value.provider and e.value.code == "model_json" and e.value.meta["usage"]["input_tokens"] == 1000


def test_retry_after_too_long_is_not_retried(anth):
    install, sent = anth
    install(_err(529, "overloaded_error", "Overloaded", headers={"retry-after": "60"}))
    with pytest.raises(SM.SampleError):
        SM.ask_model(_photo(300, 400))
    assert len(sent) == 1


def test_retry_then_success_and_thinking_blocks(anth):
    install, sent = anth
    js = json.dumps(DESC)
    install(_err(529, "overloaded_error", "Overloaded", headers={"retry-after": "1"}),
            _ok("", content=[{"type": "thinking", "thinking": "hmm", "signature": "x"},
                             {"type": "redacted_thinking", "data": "zz"}, {"type": "text", "text": js}]))
    out = SM.ask_model(_photo(300, 400))
    assert out["photo"]["mode"] == "full" and out["_meta"]["usage"]["output_tokens"] == 200
    assert sent[0]["max_tokens"] == 8000
    assert sent[0]["thinking"] == {"type": "between_tools"}          # Sonnet 5.5: без предварительного размышления


def test_thinking_param_by_model(anth, monkeypatch):
    assert SM.thinking_param("claude-sonnet-5-5") == {"type": "between_tools"}
    assert SM.thinking_param("claude-haiku-4-5-20251001") is None
    assert SM.thinking_param("claude-opus-5-5") is None
    monkeypatch.setattr(SM, "VISION_THINKING", "on")
    assert SM.thinking_param("claude-sonnet-5-5") is None


def test_rejected_thinking_param_retried_without(anth):
    install, sent = anth
    install(_err(400, "invalid_request_error", "thinking.type: between_tools is not supported for this model"),
            _ok(json.dumps(DESC)))
    out = SM.ask_model(_photo(300, 400))
    assert out["photo"]["mode"] == "full" and "thinking" not in sent[1]


def test_bot_provider_failure_releases_waiting(fresh_db, monkeypatch, anth):
    install, sent = anth
    install(_err(400, "invalid_request_error", "Your credit balance is too low to access the Anthropic API."))
    monkeypatch.setattr(bot, "SAMPLE_DIR", str(fresh_db / "samples"))
    bot._ALERTS.clear()

    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 3141)
            await u.press("smp:start")
            await u.document(_sample_jpeg(), "s.jpg")
            assert "Сервис разбора сейчас недоступен" in u.last_text()
            alerts = [t for t in _admin_texts(h) if "Ошибка" in t]
            assert len(alerts) == 1 and "HTTP 400" in alerts[0] and "credit balance" in alerts[0]
            assert "3141" in alerts[0]
            assert not h.app.user_data[3141].get("await")                # сервис недоступен — ожидание снято
            calls = len(sent)
            await u.photo(make_photo(800, 1000, seed=4))                   # следующее фото — обычный пост
            assert "q" in h.app.user_data[3141]
            assert len(sent) == calls                                     # нового платного вызова нет
            assert len([t for t in _admin_texts(h) if "Ошибка" in t]) == 1      # оповещение не повторяется
            assert not db.events("sample_call")                          # отказ провайдера не тратит лимит
    run(go())


def test_bot_bad_sample_message(fresh_db, monkeypatch, anth):
    install, sent = anth
    install(_ok("Not a layout."))
    monkeypatch.setattr(bot, "SAMPLE_DIR", str(fresh_db / "samples"))

    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 3151)
            await u.press("smp:start")
            await u.document(_sample_jpeg(), "s.jpg")
            assert "другой образец" in u.last_text()
            assert not [t for t in _admin_texts(h) if "Ошибка" in t]
            assert len(db.events("sample_call")) == 1                    # ответ модели оплачен — учтён
    run(go())


# ---------- e. устойчивость ----------
def test_build_tolerates_sloppy_model_json():
    img = _sample([], {})
    desc = {"canvas": "white", "photo": "full",
            "layers": ["junk", None,
                       {"kind": "gradient", "side": ["bottom"], "extent": "40%", "opacity": "0,6", "start": "x"},
                       {"kind": "overlay", "opacity": "half"},
                       {"kind": "rect", "box": "0.1,0.1,0.2,0.2", "radius": "big", "opacity": None},
                       {"kind": "rect", "box": [0.1, "0.1", 0.2, float("nan")]},
                       {"kind": "text", "role": ["title"], "lines": "Строка один\nСтрока два", "box": [0.05, 0.06, 0.8, 0.1],
                        "weight": "bold", "em_weight": "heavy", "emphasis": "один", "em_color": 5, "font": ["serif"],
                        "align": None, "case": 3, "plate": "red"}]}
    spec, fields, fmt, photo, report = SM.build(img, desc)
    assert spec["feed"]["layers"]
    g = [L for L in spec["feed"]["layers"] if L["type"] == "gradient"][0]
    assert g["extent"] == pytest.approx(0.4) and g["opacity"] == pytest.approx(0.6)
    assert SM.weight_of("bold") == 700 and SM.weight_of("600") == 600 and SM.weight_of(None, 500) == 500
    assert SM.num("40%") == pytest.approx(0.4) and SM.num("junk", 7) == 7


def _import_sample(env):
    code = ("import sample as S, json; print(json.dumps([S.VISION_TIMEOUT, S.VISION_PROVIDER, S.VISION_MODEL, "
            "S.enabled()]))")
    e = {k: v for k, v in os.environ.items() if not k.startswith("VISION_")}
    e.update(env)
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=e, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_vision_env_never_crashes_import():
    assert _import_sample({"VISION_TIMEOUT": "90s"})[0] == 90
    assert _import_sample({"VISION_TIMEOUT": ""})[0] == 90
    assert _import_sample({"VISION_TIMEOUT": "junk"})[0] == 90
    assert _import_sample({"VISION_TIMEOUT": "45"})[0] == 45
    t, prov, model, on = _import_sample({"VISION_API_KEY": "sk-ant-abc"})
    assert prov == "anthropic" and model == "claude-sonnet-5-5" and on
    t, prov, model, on = _import_sample({"VISION_API_KEY": "sk-abc"})
    assert prov == "openai" and not on                                   # для OpenAI модель нужно задать


# ---------- f. кнопка в меню — только когда работает ----------
def test_menu_hides_sample_when_off_or_expired(fresh_db, monkeypatch):
    async def go():
        async with Harness(bot) as h:
            monkeypatch.setattr(SM, "VISION_API_KEY", "")
            u, bid = await onboard(h, 3161)
            await u.text("/start")
            assert not u.find("smp:start")[0]
            monkeypatch.setattr(SM, "VISION_API_KEY", "k")
            monkeypatch.setattr(SM, "VISION_MODEL", "m")
            await u.text("/start")
            assert u.find("smp:start")[0]
            db.expire_brand(bid)
            await u.text("/start")
            assert not u.find("smp:start")[0]
    run(go())


# ---------- g. удаление данных убирает образец и кэш ----------
def test_delete_me_removes_sample_and_cache(vision):
    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 3171)
            await u.press("smp:start")
            await u.document(_sample_jpeg(), "s.jpg")
            assert os.path.exists(bot._sample_path(3171)) and os.path.exists(bot._sample_cache_path(3171))
            await u.press("set:show")
            await u.press("set:delme")
            await u.press("set:delmeyes")
            assert not os.path.exists(bot._sample_path(3171))
            assert not os.path.exists(bot._sample_cache_path(3171))
    run(go())


def test_delete_brand_removes_sample(vision):
    async def go():
        async with Harness(bot) as h:
            u, bid = await onboard(h, 3172)
            await u.press("smp:start")
            await u.document(_sample_jpeg(), "s.jpg")
            await u.press("set:show")
            await u.press("set:delb")
            await u.press(f"set:delbyes:{bid}")
            assert not os.path.exists(bot._sample_path(3172))
            assert not os.path.exists(bot._sample_cache_path(3172))
    run(go())

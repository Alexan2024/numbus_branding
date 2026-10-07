"""v6: связки между ветками — мастер и редактор берут стартовые стили под бренд, 9:16 = сторис."""
import bot
import db
import render as R
import spec as S
from test_v6_render import badge_logo


def _badge_brand():
    bid = db.create_brand(1001, "pilot", 30)
    png = R.prepare_logo(badge_logo())[0]
    db.set_asset(bid, "logo", png)
    db.update_kit(bid, palette=["#FFFFFF", "#141414", "#D7262D"])
    return bid


def _logo_modes(spec):
    return [L["color"].get("mode") for s in ("feed", "story") for L in spec[s]["layers"] if L["type"] == "logo"]


def test_wizard_and_pick_use_brand_traits(fresh_db):
    bid = _badge_brand()
    traits = bot.brand_traits(bid)
    assert traits and traits.get("multitone") is True
    tid = bot.pick_starter_style(bid, S.ONBOARD_PRESETS[0], "ru", 1001)
    tpls = db.list_templates(bid)
    assert tid and len(tpls) == len(S.ONBOARD_PRESETS)
    for t in tpls:
        assert set(_logo_modes(t["spec"])) == {"original"}, t["name"]


def test_brand_traits_never_raises(fresh_db):
    bot.brand_traits(999999)          # нет бренда — пустые признаки или None, но без исключения


def test_916_uses_story_layers_of_starters():
    for key in S.ONBOARD_PRESETS:
        sp = S.preset_spec(key)
        assert not sp["story"]["enabled"] and sp["story"]["layers"]
        layers, story = bot.surface(sp, "9:16")
        assert story and layers == sp["story"]["layers"]
        layers, story = bot.surface(sp, "4:5")
        assert not story and layers == sp["feed"]["layers"]


def test_916_without_story_layers_stays_feed():
    sp = S.sanitize_spec({"feed": {"layers": [{"type": "logo", "asset": "logo"}]}, "story": {"enabled": False, "layers": []}})
    layers, story = bot.surface(sp, "9:16")
    assert not story


# ---------- после проверки релиза ----------
def test_svg_filter_bomb_is_fast():
    """feGaussianBlur с огромным радиусом и областью фильтра раньше занимал минуты и гигабайты."""
    import time
    svg = (b'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200" viewBox="0 0 400 200">'
           b'<filter id="f" x="-10000%" y="-10000%" width="20000%" height="20000%">'
           b'<feGaussianBlur stdDeviation="100000"/><feTurbulence numOctaves="100000"/></filter>'
           b'<rect x="50" y="50" width="300" height="100" fill="#c00" filter="url(#f)" style="filter:url(#f)"/></svg>')
    t = time.time()
    png, _ = R.prepare_logo(svg)
    assert time.time() - t < 3 and png


def test_photo_at_colour_step_keeps_logo(fresh_db):
    """Обычное фото на шаге цвета — это пост раньше времени, а не новый логотип."""
    from test_v6_bot import _to_step
    from test_bot_flows import run
    from fake_tg import Harness
    from conftest import make_photo

    async def go():
        async with Harness(bot) as h:
            u = await _to_step(h, 2031, 3)
            b = db.user_brands(2031)[0]
            logo = db.get_asset(b["id"], "logo")
            await u.photo(make_photo(1200, 1500, seed=7))
            assert db.get_asset(b["id"], "logo") == logo
            assert "q" not in h.app.user_data[2031]
    run(go())

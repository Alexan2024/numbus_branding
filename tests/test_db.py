"""База: периоды подписки, продление, удаление, сессии, заявки, копии."""
import os
import gzip
import sqlite3
from datetime import timedelta

import db
import persist


def _age_brand(bid, days):
    """Сдвинуть дату активации бренда в прошлое."""
    past = (db._now() - timedelta(days=days)).isoformat()
    with db._conn() as c:
        c.execute("UPDATE brands SET created_at=?, period_anchor=? WHERE id=?", (past, past, bid))


def test_period_from_activation(fresh_db):
    bid = db.create_brand(1, "pilot", 90)
    _age_brand(bid, 45)
    start, end = db.period_bounds(db.get_brand(bid))
    assert timedelta(days=14, hours=23) < db._now() - start < timedelta(days=15, hours=1)
    assert end - start == timedelta(days=30)
    # фото прошлого периода не считаются
    with db._conn() as c:
        c.execute("INSERT INTO events (brand_id, tg_id, template, photos, ts) VALUES (?,?,?,?,?)",
                  (bid, 1, "1", 7, (db._now() - timedelta(days=20)).isoformat()))
    db.record_event(bid, 1, 5, 3)
    db.record_event(bid, 1, 5, 0)                   # пустая выдача не пишется
    assert db.photos_used(bid) == 3


def test_extend_after_lapse_restarts_period(fresh_db):
    bid = db.create_brand(1, "pilot", 30)
    _age_brand(bid, 40)
    db.expire_brand(bid)
    assert not db.plan_active(db.get_brand(bid))
    db.extend_brand(bid, 30, "media")
    b = db.get_brand(bid)
    assert db.plan_active(b) and b["plan"] == "media"
    start, _ = db.period_bounds(b)
    assert db._now() - start < timedelta(minutes=1)          # новый период — с момента продления
    until = b["plan_until"]
    db.extend_brand(bid, 10)                                   # продление активной — от конца срока
    assert db.get_brand(bid)["plan_until"] > until
    assert db.get_brand(bid)["period_anchor"] == b["period_anchor"]


def test_sub_brands_share_limits_and_delete(fresh_db):
    root = db.create_brand(1, "studio", 30)
    child = db.create_brand(1, "studio", 0, sub_id=root)
    db.record_event(child, 1, 1, 4)
    assert db.photos_used(root) == 4
    db.add_member(child, 2, "editor")
    assert db.sub_member_count(root) == 2
    assert sorted(db.delete_brand(root)) == sorted([root, child])
    assert db.get_brand(child) is None and db.member_role(child, 2) is None


def test_delete_user_everything(fresh_db):
    db.ensure_user(5, "ru")
    bid = db.create_brand(5, "pilot", 30)
    other = db.create_brand(6, "pilot", 30)
    db.add_member(other, 5, "designer")
    db.record_event(other, 5, 1, 2)
    db.create_login_link(5, bid)
    db.log_event("draft", 5, bid, title="x")
    db.create_request(5, "Имя", "user", "канал")
    gone = db.delete_user(5)
    assert gone == [bid]
    assert db.get_user(5) is None and db.member_role(other, 5) is None
    assert db.photos_used(other) == 2                          # фото в чужом бренде остаются, без имени
    with db._conn() as c:
        assert not c.execute("SELECT 1 FROM analytics WHERE tg_id=5").fetchone()
        assert not c.execute("SELECT 1 FROM sessions WHERE tg_id=5").fetchone()
        assert not c.execute("SELECT 1 FROM access_requests WHERE tg_id=5").fetchone()


def test_sessions_hashed_and_legacy_migrated(fresh_db):
    link = db.create_login_link(7, 1)
    sess, uid, bid = db.redeem_login_link(link)
    assert db.check_session(sess) == (7, 1)
    with db._conn() as c:
        assert not c.execute("SELECT 1 FROM sessions WHERE token=?", (sess,)).fetchone()
        # старая сессия открытым текстом — после init_db работает, но хранится хэшем
        c.execute("INSERT INTO sessions (token, kind, tg_id, brand_id, expires) VALUES (?,?,?,?,?)",
                  ("legacy-token", "session", 8, 2, (db._now() + timedelta(days=1)).isoformat()))
    db.init_db()
    assert db.check_session("legacy-token") == (8, 2)
    assert db.session_count(7) == 1 and db.drop_user_sessions(7) == 1


def test_requests_decided_once(fresh_db):
    rid = db.create_request(9, "Олег", "oleg", "t.me/oleg")
    assert db.open_request(9)["id"] == rid
    assert db.set_request_status(rid, "approved")
    assert not db.set_request_status(rid, "declined")
    assert db.open_request(9) is None


def test_invites_single_use(fresh_db):
    code = db.create_invite("pilot", 30, 1)
    assert db.peek_invite(code.lower()) == ("pilot", 30)
    assert db.redeem_invite(code) == ("pilot", 30)
    assert db.redeem_invite(code) is None and db.peek_invite(code) is None


def test_backup_rotation(fresh_db):
    db.create_brand(1, "pilot", 30)
    os.makedirs(db.BACKUP_DIR, exist_ok=True)
    for i in range(9):                                     # старые копии прошлых дней
        open(os.path.join(db.BACKUP_DIR, f"numbus-20260101-00000{i}.db.gz"), "wb").write(b"old")
    p = db.backup(keep=7)
    files = sorted(f for f in os.listdir(db.BACKUP_DIR) if f.endswith(".db.gz"))
    assert len(files) == 7 and os.path.basename(p) == files[-1]
    restored = fresh_db / "restored.db"
    restored.write_bytes(gzip.decompress(open(p, "rb").read()))
    assert sqlite3.connect(restored).execute("SELECT COUNT(*) FROM brands").fetchone()[0] == 1


def test_recent_titles_and_editors(fresh_db):
    bid = db.create_brand(1, "pilot", 30)
    db.add_member(bid, 2, "designer")
    db.add_member(bid, 3, "editor")
    for t in ("Первый", "Второй", "Первый", ""):
        db.log_event("draft", 3, bid, title=t)
    assert db.recent_titles(bid) == ["Первый", "Второй"]
    assert sorted(db.editor_user_ids()) == [1, 2]


def test_persist_forget_user(fresh_db):
    import asyncio
    p = persist.DbPersistence()

    async def go():
        await p.update_conversation("main", (5, 5), 3)
        await p.update_conversation("main", (6, 6), 4)
        with db._conn() as c:                  # шаг шестого — двухчасовой давности
            c.execute("UPDATE persist SET data=? WHERE key='6,6'", ('{"s": 4, "t": 1}',))
        await p.update_user_data(5, {"lang": "en", "kit_bid": 2, "q": object()})
        assert await p.get_conversations("main") == {(5, 5): 3}
        assert await p.get_user_data() == {5: {"lang": "en", "kit_bid": 2}}
        persist.forget_user(5)
        assert await p.get_conversations("main") == {}
        assert await p.get_user_data() == {}
    asyncio.run(go())

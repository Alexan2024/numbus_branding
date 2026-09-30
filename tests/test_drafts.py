"""Черновики постов на диске: переживают перезапуск, старые удаляются."""
import os
import time

import drafts


def test_roundtrip_and_ttl(fresh_db):
    d = drafts.new(42, bid=1, tid=2, fmt="4:5", title="Заголовок")
    drafts.add_photo(42, d, b"jpegbytes")
    d["base"] = object()                       # в памяти, на диск не пишется
    d["focus"]["0"] = [0.0, 0.5]
    drafts.save(42, d)
    back = drafts.load(42)
    assert back["title"] == "Заголовок" and back["focus"] == {"0": [0.0, 0.5]} and "base" not in back
    assert drafts.read(back["photos"][0]) == b"jpegbytes"
    old = time.time() - drafts.TTL - 10
    for f in os.listdir(os.path.join(drafts.ROOT, "42")):
        os.utime(os.path.join(drafts.ROOT, "42", f), (old, old))
    assert drafts.load(42) is None               # просроченный черновик не поднимается
    d = drafts.new(43, bid=1)
    drafts.add_photo(43, d, b"x")
    drafts.save(43, d)
    for f in os.listdir(os.path.join(drafts.ROOT, "43")):
        os.utime(os.path.join(drafts.ROOT, "43", f), (old, old))
    assert drafts.gc() == 1 and not os.path.exists(os.path.join(drafts.ROOT, "43"))


def test_new_replaces_previous(fresh_db):
    d = drafts.new(44, bid=1)
    p = drafts.add_photo(44, d, b"a")
    drafts.save(44, d)
    drafts.new(44, bid=1)
    assert not os.path.exists(p)

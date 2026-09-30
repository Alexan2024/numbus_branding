"""Общая подготовка тестов: отдельная папка данных, токен, админ и адрес редактора.
Переменные ставятся до импорта модулей бота — db.py читает их при импорте."""
import io
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="numbus-test-")
os.environ["DATA_DIR"] = _TMP
os.environ["NUMBUS_DB"] = os.path.join(_TMP, "numbus.db")
os.environ["BOT_TOKEN"] = "123456:TEST"
os.environ["ADMIN_IDS"] = "1"
os.environ["WEBAPP_URL"] = "https://numbus.test"
os.environ["SUPPORT_CONTACT"] = "@support_test"
for k in ("BACKUP_S3_ENDPOINT", "BACKUP_S3_BUCKET", "BACKUP_S3_KEY", "BACKUP_S3_SECRET", "TG_PROXY", "TG_API_URL"):
    os.environ.pop(k, None)

import numpy as np            # noqa: E402
from PIL import Image, ImageDraw   # noqa: E402

import db        # noqa: E402
import drafts    # noqa: E402


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """Своя база и папка черновиков на каждый тест."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "numbus.db"))
    monkeypatch.setattr(db, "BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(drafts, "ROOT", str(tmp_path / "drafts"))
    db.init_db()
    return tmp_path


def make_photo(w=1600, h=2000, seed=0, fmt="JPEG", exif_orientation=None) -> bytes:
    """Правдоподобное «фото»: небо, горизонт, пятна света — чтобы рендеру было что кадрировать."""
    rng = np.random.default_rng(seed)
    y = np.linspace(0, 1, h).reshape(-1, 1, 1)
    x = np.linspace(0, 1, w).reshape(1, -1, 1)
    top = np.array([120, 160, 210]) + rng.integers(-20, 20, 3)
    bottom = np.array([60, 45, 30]) + rng.integers(-10, 10, 3)
    arr = top * (1 - y) + bottom * y + 25 * np.sin(x * 9 + seed) * (1 - y)
    arr = np.clip(arr + rng.normal(0, 6, (h, w, 1)), 0, 255).astype(np.uint8)
    im = Image.fromarray(arr, "RGB")
    d = ImageDraw.Draw(im)
    for i in range(6):
        cx, cy, r = rng.integers(0, w), rng.integers(h // 3, h), rng.integers(40, 220)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=tuple(int(c) for c in rng.integers(40, 250, 3)))
    buf = io.BytesIO()
    if exif_orientation:
        ex = Image.Exif()
        ex[0x0112] = exif_orientation
        im.save(buf, fmt, quality=90, exif=ex.tobytes())
    else:
        im.save(buf, fmt, quality=90)
    return buf.getvalue()


def make_logo(color=(200, 60, 40), transparent=True) -> bytes:
    """Логотип: цветной знак и тёмная надпись, на прозрачном или белом фоне."""
    im = Image.new("RGBA", (900, 300), (0, 0, 0, 0) if transparent else (255, 255, 255, 255))
    d = ImageDraw.Draw(im)
    d.ellipse((20, 20, 280, 280), fill=color + (255,))
    d.rectangle((330, 110, 880, 190), fill=(30, 30, 30, 255))
    d.rectangle((330, 210, 700, 250), fill=(40, 90, 160, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


SVG_LOGO = b"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="400" height="120" viewBox="0 0 400 120">
  <script>alert(1)</script>
  <circle cx="60" cy="60" r="50" fill="#1E7A5C"/>
  <rect x="130" y="40" width="250" height="40" fill="#222"/>
</svg>"""

"""NUMBUS Branding — резервные копии базы в S3-совместимое хранилище.

Подходит любое хранилище с API S3: Selectel, Yandex Object Storage, Timeweb Cloud,
VK Cloud. Переменные окружения:
  BACKUP_S3_ENDPOINT  https://s3.ru-1.storage.selcloud.ru  (адрес хранилища)
  BACKUP_S3_BUCKET    имя бакета
  BACKUP_S3_KEY       ключ доступа (Access Key)
  BACKUP_S3_SECRET    секретный ключ (Secret Key)
  BACKUP_S3_REGION    регион, по умолчанию ru-1 (у Yandex — ru-central1)
  BACKUP_S3_PREFIX    папка внутри бакета, по умолчанию numbus/

Подпись запросов — AWS Signature V4, без сторонних библиотек.
Здесь же — чтение настроек из окружения без падений: пустое или кривое значение
(«BACKUP_HOUR=», «BACKUP_HOUR=04:00», «BACKUP_S3_REGION=») не роняет бота, а берётся
значение по умолчанию с предупреждением в логе.
"""
import os
import re
import hmac
import hashlib
import logging
import urllib.request
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

logger = logging.getLogger("numbus.backup")


# ============ Настройки из окружения ============
def env_str(name, default=""):
    """Строка из окружения; пустая (или из одних пробелов) — значение по умолчанию."""
    v = os.environ.get(name)
    if v is None:
        return default
    v = v.strip()
    if not v:
        if default:
            logger.warning("%s задана пустой — беру %r", name, default)
        return default
    return v


def env_int(name, default, lo=None, hi=None):
    """Целое из окружения. Пусто, не число или вне [lo, hi] — default и предупреждение."""
    raw = env_str(name, "")
    if not raw:
        if name in os.environ:
            logger.warning("%s задана пустой — беру %s", name, default)
        return default
    try:
        v = int(raw)
    except ValueError:
        logger.warning("%s=%r — не целое число, беру %s", name, raw, default)
        return default
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        logger.warning("%s=%s — вне допустимого (%s…%s), беру %s", name, v, lo, hi, default)
        return default
    return v


def env_bool(name, default=False):
    raw = env_str(name, "").lower()
    if not raw:
        if name in os.environ:
            logger.warning("%s задана пустой — беру %s", name, default)
        return default
    if raw in ("1", "true", "yes", "on", "да"):
        return True
    if raw in ("0", "false", "no", "off", "нет"):
        return False
    logger.warning("%s=%r — ожидается true или false, беру %s", name, raw, default)
    return default


def backup_hour(default=4):
    """Час ежедневной копии из BACKUP_HOUR: «4», «04» и «04:00» — это 4 часа.
    Пусто или ерунда — default (4) и предупреждение в логе."""
    raw = env_str("BACKUP_HOUR", "")
    if not raw:
        if "BACKUP_HOUR" in os.environ:
            logger.warning("BACKUP_HOUR задана пустой — беру %s", default)
        return default
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(?:\s*[чh])?", raw, re.I)
    if not m or int(m.group(1)) > 23:
        logger.warning("BACKUP_HOUR=%r — нужен час 0–23, например 4. Беру %s", raw, default)
        return default
    if m.group(2) and m.group(2) != "00":
        logger.warning("BACKUP_HOUR=%r — минуты не учитываются, копия в %s:10", raw, int(m.group(1)))
    return int(m.group(1))


def s3_config():
    cfg = {k: env_str("BACKUP_S3_" + k.upper()) for k in ("endpoint", "bucket", "key", "secret")}
    if not all(cfg.values()):
        return None
    cfg["endpoint"] = cfg["endpoint"].rstrip("/")
    if not cfg["endpoint"].startswith("http"):
        cfg["endpoint"] = "https://" + cfg["endpoint"]
    cfg["region"] = env_str("BACKUP_S3_REGION", "ru-1")
    cfg["prefix"] = env_str("BACKUP_S3_PREFIX", "numbus/")
    return cfg


def _hmac(key, msg):
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sign_put(cfg, object_key, data, now=None, content_type="application/gzip"):
    """→ (url, headers) для PUT с подписью AWS SigV4 (path-style адрес)."""
    now = now or datetime.now(timezone.utc)
    amz_date, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    host = urlparse(cfg["endpoint"]).netloc
    uri = "/" + quote(cfg["bucket"], safe="") + "/" + quote(object_key, safe="/-_.~")
    payload = hashlib.sha256(data).hexdigest()
    headers = {"host": host, "x-amz-content-sha256": payload, "x-amz-date": amz_date}
    signed = ";".join(sorted(headers))
    canonical = "\n".join(["PUT", uri, "", "".join(f"{k}:{headers[k]}\n" for k in sorted(headers)), signed, payload])
    scope = f"{day}/{cfg['region']}/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    k = _hmac(("AWS4" + cfg["secret"]).encode(), day)
    for part in (cfg["region"], "s3", "aws4_request"):
        k = _hmac(k, part)
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    out = {"x-amz-content-sha256": payload, "x-amz-date": amz_date, "Content-Type": content_type,
           "Authorization": f"AWS4-HMAC-SHA256 Credential={cfg['key']}/{scope}, SignedHeaders={signed}, Signature={sig}"}
    return cfg["endpoint"] + uri, out


def upload(path, cfg=None, timeout=120):
    """Загружает файл копии в хранилище. → ключ объекта. Ошибка — исключение."""
    cfg = cfg or s3_config()
    if not cfg:
        raise RuntimeError("S3 не настроен")
    with open(path, "rb") as f:
        data = f.read()
    key = cfg["prefix"] + os.path.basename(path)
    url, headers = sign_put(cfg, key, data)
    req = urllib.request.Request(url, data=data, method="PUT", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if r.status not in (200, 201, 204):
            raise RuntimeError(f"S3 ответил {r.status}")
    return key

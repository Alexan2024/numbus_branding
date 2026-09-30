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
"""
import os
import hmac
import hashlib
import logging
import urllib.request
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

logger = logging.getLogger("numbus.backup")


def s3_config():
    cfg = {k: os.environ.get("BACKUP_S3_" + k.upper()) for k in ("endpoint", "bucket", "key", "secret")}
    if not all(cfg.values()):
        return None
    cfg["endpoint"] = cfg["endpoint"].rstrip("/")
    if not cfg["endpoint"].startswith("http"):
        cfg["endpoint"] = "https://" + cfg["endpoint"]
    cfg["region"] = os.environ.get("BACKUP_S3_REGION", "ru-1")
    cfg["prefix"] = os.environ.get("BACKUP_S3_PREFIX", "numbus/")
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

"""Подпись загрузки копий в S3 (AWS Signature V4) — сверено с botocore."""
from datetime import datetime, timezone

import backups

CFG = {"endpoint": "https://s3.ru-1.storage.selcloud.ru", "bucket": "numbus-backups", "key": "AKIDEXAMPLE",
       "secret": "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "region": "ru-1", "prefix": "numbus/"}


def test_sigv4_matches_botocore():
    now = datetime(2026, 9, 30, 9, 13, 35, tzinfo=timezone.utc)
    url, headers = backups.sign_put(CFG, "numbus/numbus-20260930-120000.db.gz", b"hello backup" * 100, now)
    assert url == "https://s3.ru-1.storage.selcloud.ru/numbus-backups/numbus/numbus-20260930-120000.db.gz"
    assert headers["x-amz-date"] == "20260930T091335Z"
    assert headers["Authorization"].endswith(
        "Signature=13d3c4223a8f8da8fbe6001f896c0911a219979384dda419283ca3d92b3310a4")
    assert "Credential=AKIDEXAMPLE/20260930/ru-1/s3/aws4_request" in headers["Authorization"]


def test_config_from_env(monkeypatch):
    for k in ("ENDPOINT", "BUCKET", "KEY", "SECRET"):
        monkeypatch.delenv("BACKUP_S3_" + k, raising=False)
    assert backups.s3_config() is None
    monkeypatch.setenv("BACKUP_S3_ENDPOINT", "storage.yandexcloud.net/")
    monkeypatch.setenv("BACKUP_S3_BUCKET", "b")
    monkeypatch.setenv("BACKUP_S3_KEY", "k")
    monkeypatch.setenv("BACKUP_S3_SECRET", "s")
    monkeypatch.setenv("BACKUP_S3_REGION", "ru-central1")
    cfg = backups.s3_config()
    assert cfg["endpoint"] == "https://storage.yandexcloud.net" and cfg["region"] == "ru-central1"

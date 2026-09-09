"""文件日志：分目录、滚动、gzip、月打包、保留期。"""
from __future__ import annotations

import gzip
import tarfile
import time
from datetime import datetime
from pathlib import Path

import pytest

from app.services import file_log_service as fls


@pytest.fixture()
def log_root(tmp_path, monkeypatch):
    root = tmp_path / "logs"
    monkeypatch.setattr(fls, "resolve_log_root", lambda: root)
    fls.reset_file_log_handlers_for_tests()
    fls.ensure_log_dirs(root)
    yield root
    fls.reset_file_log_handlers_for_tests()


def test_ensure_dirs_and_client_write(log_root: Path):
    for name in ("backend", "frontend", "wechat", "alipay", "archive"):
        assert (log_root / name).is_dir()

    n = fls.write_client_entries(
        "wechat",
        [{"level": "ERROR", "message": "扫码失败", "page": "pages/index/index"}],
        device="devtools",
    )
    assert n == 1
    log_file = log_root / "wechat" / "app.log"
    assert log_file.is_file()
    text = log_file.read_text(encoding="utf-8")
    assert "扫码失败" in text
    assert "pages/index/index" in text


def test_frontend_and_alipay_separate(log_root: Path):
    fls.write_client_entries("frontend", [{"level": "INFO", "message": "fe-ok"}])
    fls.write_client_entries("alipay", [{"level": "WARN", "message": "ali-warn"}])
    assert "fe-ok" in (log_root / "frontend" / "app.log").read_text(encoding="utf-8")
    assert "ali-warn" in (log_root / "alipay" / "app.log").read_text(encoding="utf-8")
    assert not (log_root / "frontend" / "app.log").read_text(encoding="utf-8").count("ali-warn")


def test_maintain_gzip_pack_and_delete(log_root: Path):
    src = log_root / "backend"
    old_plain = src / "app1.log"
    old_plain.write_text("old plain line\n", encoding="utf-8")
    # 10 天前 → 应 gzip
    old_ts = time.time() - 10 * 86400
    import os

    os.utime(old_plain, (old_ts, old_ts))

    stats = fls.maintain_logs(now=datetime.now())
    assert stats["gzipped"] >= 1
    assert not old_plain.exists()
    gz = src / "app1.log.gz"
    assert gz.is_file()
    with gzip.open(gz, "rt", encoding="utf-8") as f:
        assert "old plain line" in f.read()

    # 再设为 40 天前 → 月打包
    pack_ts = time.time() - 40 * 86400
    os.utime(gz, (pack_ts, pack_ts))
    month = datetime.fromtimestamp(pack_ts).strftime("%Y-%m")
    stats2 = fls.maintain_logs(now=datetime.now())
    assert stats2["packed"] >= 1
    assert not gz.exists()
    archive = log_root / "archive" / "backend" / f"{month}.tar.gz"
    assert archive.is_file()
    with tarfile.open(archive, "r:gz") as tar:
        names = tar.getnames()
        assert any("app1.log.gz" in n for n in names)

    # 100 天前归档 → 删除
    gone_ts = time.time() - 100 * 86400
    os.utime(archive, (gone_ts, gone_ts))
    stats3 = fls.maintain_logs(now=datetime.now())
    assert stats3["archives_deleted"] >= 1
    assert not archive.exists()


def test_rotate_to_appN_log(log_root: Path, monkeypatch):
    monkeypatch.setattr(fls, "LOG_MAX_BYTES", 200)
    monkeypatch.setattr(fls, "LOG_BACKUP_COUNT", 3)
    fls.reset_file_log_handlers_for_tests()
    lg = fls.get_source_logger("frontend")
    for i in range(40):
        lg.info("line-%s-%s", i, "x" * 40)
    for h in lg.handlers:
        h.flush()
    assert (log_root / "frontend" / "app.log").is_file()
    assert (log_root / "frontend" / "app1.log").is_file()


def test_backend_reject_client_source(log_root: Path):
    with pytest.raises(ValueError):
        fls.write_client_entries("backend", [{"message": "nope"}])


def test_attach_backend_handler(log_root: Path):
    import logging

    root = logging.getLogger()
    fls.attach_backend_file_handler(root)
    logging.getLogger("test.filelog").info("backend-line-ok")
    for h in root.handlers:
        h.flush()
    text = (log_root / "backend" / "app.log").read_text(encoding="utf-8")
    assert "backend-line-ok" in text

"""Deployment utility regression tests."""
from __future__ import annotations

import gzip
import importlib.util
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "backup_restore_drill", ROOT / "scripts" / "备份恢复演练.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_database_identity_normalizes_async_mysql_driver_and_password_encoding():
    assert MODULE._database_identity(
        "mysql+aiomysql://operator:p%40ss@db.internal:3307/production?charset=utf8mb4"
    ) == ("db.internal", 3307, "production")


def test_database_identity_distinguishes_drill_database():
    production = MODULE._database_identity(
        "mysql+aiomysql://user:secret@db.internal:3306/smart_noodle"
    )
    drill = MODULE._database_identity(
        "mysql+pymysql://user:secret@db.internal:3306/smart_noodle_drill"
    )
    assert production != drill


@pytest.mark.parametrize(
    "url",
    [
        "",
        "redis://localhost:6379/0",
        "mysql://db.internal:3306/",
        "not-a-database-url",
    ],
)
def test_database_identity_rejects_invalid_mysql_target(url):
    with pytest.raises(ValueError):
        MODULE._database_identity(url)


BACKUP_SPEC = importlib.util.spec_from_file_location(
    "backup_database", ROOT / "scripts" / "备份数据库.py"
)
assert BACKUP_SPEC is not None and BACKUP_SPEC.loader is not None
BACKUP_MODULE = importlib.util.module_from_spec(BACKUP_SPEC)
BACKUP_SPEC.loader.exec_module(BACKUP_MODULE)


def test_backup_gzip_verification_rejects_empty_and_truncated_files(tmp_path):
    empty = tmp_path / "empty.sql.gz"
    with gzip.open(empty, "wb"):
        pass
    with pytest.raises(ValueError, match="为空"):
        BACKUP_MODULE._verify_gzip_backup(empty)

    valid = tmp_path / "valid.sql.gz"
    with gzip.open(valid, "wb") as stream:
        stream.write(b"SELECT 1;\\n" * 100)
    assert BACKUP_MODULE._verify_gzip_backup(valid) == len(b"SELECT 1;\\n" * 100)

    truncated = tmp_path / "truncated.sql.gz"
    truncated.write_bytes(valid.read_bytes()[:-8])
    with pytest.raises((EOFError, OSError)):
        BACKUP_MODULE._verify_gzip_backup(truncated)


def test_mysqldump_streaming_does_not_deadlock_when_stderr_is_large(tmp_path):
    output = tmp_path / "backup.sql.gz"
    sql = b"CREATE TABLE smoke_test (id INT);\\n"
    command = [
        sys.executable,
        "-c",
        "import sys; sys.stderr.write('E' * 2000000); "
        "sys.stdout.buffer.write(" + repr(sql) + ")",
    ]

    return_code, error = BACKUP_MODULE._run_mysqldump_to_gzip(
        command, output, os.environ.copy()
    )

    assert return_code == 0
    assert len(error) == 1024 * 1024
    with gzip.open(output, "rb") as stream:
        assert stream.read() == sql


RESTORE_SPEC = importlib.util.spec_from_file_location(
    "restore_database", ROOT / "scripts" / "恢复数据库.py"
)
assert RESTORE_SPEC is not None and RESTORE_SPEC.loader is not None
RESTORE_MODULE = importlib.util.module_from_spec(RESTORE_SPEC)
RESTORE_SPEC.loader.exec_module(RESTORE_MODULE)


def test_restore_decompresses_gzip_backup_to_temporary_file_in_chunks(tmp_path):
    source = tmp_path / "backup.sql.gz"
    expected = (b"INSERT INTO orders VALUES (1);\\n" * 10000)
    with gzip.open(source, "wb") as stream:
        stream.write(expected)

    restored = RESTORE_MODULE._decompress_backup_to_temp(source)
    try:
        assert restored.read_bytes() == expected
        assert restored != source
    finally:
        restored.unlink(missing_ok=True)


def test_mysql_restore_command_forces_utf8mb4_client_charset():
    command = RESTORE_MODULE._build_mysql_restore_command(
        "mysql",
        {
            "host": "db.internal",
            "port": "3307",
            "user": "operator",
            "database": "smart_noodle_house",
        },
    )

    assert command == [
        "mysql",
        "--default-character-set=utf8mb4",
        "-h",
        "db.internal",
        "-P",
        "3307",
        "-u",
        "operator",
        "smart_noodle_house",
    ]



def test_nginx_denies_direct_customer_avatar_static_access():
    config = (ROOT / "nginx" / "smart-noodle.conf").read_text(encoding="utf-8")
    assert "location ^~ /uploads/avatars/ {" in config
    assert "location ^~ /uploads/visitors/ {" in config
    assert config.index("location ^~ /uploads/avatars/ {") < config.index("location /uploads/ {")

def test_private_avatar_signed_queries_are_not_written_to_access_logs():
    nginx = (ROOT / "nginx" / "smart-noodle.conf").read_text(encoding="utf-8")
    service = (ROOT / "systemd" / "smart-noodle.service").read_text(encoding="utf-8")
    main = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")

    log_format = next(line for line in nginx.splitlines() if line.startswith("log_format private_media"))
    assert "$uri" in log_format
    assert '"$request ' not in log_format
    assert "$args" not in log_format
    assert "location ^~ /api/media/private/avatars/" in nginx
    assert "smart-noodle.private-media.access.log private_media" in nginx
    assert "error_log /dev/null emerg;" in nginx
    assert "--no-access-log" in service
    assert 'request.url.path.startswith("/api/media/private/avatars/")' in main


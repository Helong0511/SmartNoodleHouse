"""备份脚本的安全边界与完整性回归测试。"""
from datetime import datetime, timedelta
import gzip

import pytest

from scripts import 备份数据库 as backup


def test_mysqldump_command_keeps_credentials_out_of_arguments():
    command = backup._build_mysqldump_command(
        "/usr/bin/mysqldump",
        {
            "host": "db.example.test",
            "port": "3307",
            "user": "backup_user",
            "password": "never-put-password-in-argv",
            "database": "noodle_house",
        },
    )

    assert command[0] == "/usr/bin/mysqldump"
    assert command[-1] == "noodle_house"
    assert "--single-transaction" in command
    assert "--triggers" in command
    assert "--events" in command
    assert "--no-tablespaces" in command
    assert "--set-gtid-purged=OFF" in command
    assert "never-put-password-in-argv" not in command


def test_verify_gzip_backup_accepts_complete_nonempty_stream(tmp_path):
    path = tmp_path / "backup.sql.gz"
    payload = b"CREATE TABLE noodle (id INT PRIMARY KEY);\n"
    with gzip.open(path, "wb") as stream:
        stream.write(payload)

    assert backup._verify_gzip_backup(path) == len(payload)


def test_verify_gzip_backup_rejects_empty_stream(tmp_path):
    path = tmp_path / "empty.sql.gz"
    with gzip.open(path, "wb"):
        pass

    with pytest.raises(ValueError, match="输出为空"):
        backup._verify_gzip_backup(path)


def test_verify_gzip_backup_rejects_truncated_stream(tmp_path):
    path = tmp_path / "truncated.sql.gz"
    with gzip.open(path, "wb") as stream:
        stream.write(b"important database rows")
    path.write_bytes(path.read_bytes()[:-5])

    with pytest.raises((EOFError, OSError)):
        backup._verify_gzip_backup(path)


def test_prune_removes_only_expired_well_formed_backup_names(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "BACKUP_DIR", tmp_path)
    old_stamp = (datetime.now() - timedelta(days=20)).strftime("%Y%m%d_%H%M%S_%f")
    recent_stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    old_file = tmp_path / f"smart_noodle_house_{old_stamp}.sql.gz"
    recent_file = tmp_path / f"smart_noodle_house_{recent_stamp}.sql.gz"
    malformed_file = tmp_path / "smart_noodle_house_not-a-timestamp.sql.gz"
    unrelated_file = tmp_path / "keep-me.txt"
    for path in (old_file, recent_file, malformed_file, unrelated_file):
        path.write_bytes(b"backup")

    assert backup.prune(14) == 1
    assert not old_file.exists()
    assert recent_file.exists()
    assert malformed_file.exists()
    assert unrelated_file.exists()


def test_prune_zero_days_disables_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "BACKUP_DIR", tmp_path)
    stamp = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d_%H%M%S_%f")
    old_file = tmp_path / f"smart_noodle_house_{stamp}.sql.gz"
    old_file.write_bytes(b"backup")

    assert backup.prune(0) == 0
    assert old_file.exists()

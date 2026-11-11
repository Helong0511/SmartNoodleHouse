"""备份恢复演练的目标库隔离保护回归测试。"""
import pytest

from scripts.备份恢复演练 import (
    _database_identity,
    _isolation_confirmation_error,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "mysql+aiomysql://user:secret@DB.Example.com:3306/noodle",
            ("db.example.com", 3306, "noodle"),
        ),
        (
            "mysql+pymysql://user:secret@db.example.com./noodle",
            ("db.example.com", 3306, "noodle"),
        ),
        (
            "mysql://user:secret@db.example.com:3307/noodle%2Ddrill",
            ("db.example.com", 3307, "noodle-drill"),
        ),
    ],
)
def test_database_identity_normalizes_mysql_urls(url, expected):
    assert _database_identity(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        "postgresql://user:secret@db.example.com/noodle",
        "mysql://db.example.com",
        "mysql:///noodle",
    ],
)
def test_database_identity_rejects_invalid_urls(url):
    with pytest.raises(ValueError):
        _database_identity(url)


@pytest.mark.parametrize("value", [None, "", "yes", "true", "1", "NO"])
def test_restore_drill_requires_explicit_isolation_confirmation(value):
    assert _isolation_confirmation_error(value)


def test_restore_drill_accepts_explicit_isolation_confirmation():
    assert _isolation_confirmation_error("YES") is None

"""Deployment utility regression tests."""
from __future__ import annotations

import importlib.util
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

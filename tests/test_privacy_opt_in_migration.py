"""Regression coverage for privacy-safe rollback of migration 0042."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "backend"
    / "alembic"
    / "versions"
    / "0042_privacy_opt_in_default.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_0042_privacy_opt_in_default", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Connection:
    dialect = SimpleNamespace(name="mysql")

    def execute(self, *_args, **_kwargs):
        raise AssertionError(
            "downgrade must not change the affirmative-consent default"
        )


def test_downgrade_does_not_restore_opt_in_default(monkeypatch):
    migration = _load_migration()
    monkeypatch.setattr(
        migration,
        "op",
        SimpleNamespace(get_bind=lambda: _Connection()),
    )

    migration.downgrade()

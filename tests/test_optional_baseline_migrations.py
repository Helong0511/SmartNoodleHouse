"""Required baseline tables must not be silently skipped by schema migrations."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = {
    "0043": (ROOT / "backend/alembic/versions/0043_delivery_refund_fingerprint.py", "delivery_refunds"),
    "0047": (ROOT / "backend/alembic/versions/0047_payment_refund_idempotency_key.py", "payment_refunds"),
    "0049": (ROOT / "backend/alembic/versions/0049_menu_cache_outbox_retry.py", "outbox_events"),
    "0050": (ROOT / "backend/alembic/versions/0050_menu_db_generation.py", "stores"),
    "0051": (ROOT / "backend/alembic/versions/0051_pos_refund_execution_fencing.py", "pos_refund_requests"),
}


def _load(revision):
    path, _ = MIGRATIONS[revision]
    spec = importlib.util.spec_from_file_location(
        f"migration_{revision}_missing_required_table", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("revision", tuple(MIGRATIONS))
def test_upgrade_fails_clearly_when_required_baseline_table_is_missing(revision):
    _, table = MIGRATIONS[revision]
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            module = _load(revision)
            module.op = Operations(MigrationContext.configure(conn))
            with pytest.raises(RuntimeError, match=f"required table.*{table}"):
                module.upgrade()
            assert not sa.inspect(conn).has_table(table)
    finally:
        engine.dispose()

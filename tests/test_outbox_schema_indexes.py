"""Regression tests keeping the Outbox ORM and canonical MySQL schema aligned."""
from __future__ import annotations

import re
from pathlib import Path

from app.models import OutboxEvent


ROOT = Path(__file__).resolve().parents[1]


def _canonical_index_columns(index_name: str) -> tuple[str, ...]:
    schema = (ROOT / "sql" / "struct.sql").read_text(encoding="utf-8")
    match = re.search(
        rf"INDEX\s+`{re.escape(index_name)}`\s*\(([^)]*)\)",
        schema,
        flags=re.IGNORECASE,
    )
    assert match is not None, f"canonical schema is missing index {index_name}"
    return tuple(re.findall(r"`([^\`]+)`", match.group(1)))


def test_outbox_retry_index_matches_canonical_schema_and_verifier():
    indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in OutboxEvent.__table__.indexes
    }

    assert indexes["idx_oe_status_retry"] == ("status", "next_retry_at")
    assert _canonical_index_columns("idx_oe_status_retry") == indexes["idx_oe_status_retry"]

    verifier = (ROOT / "scripts" / "验证数据库.py").read_text(encoding="utf-8")
    assert '("outbox_events", "idx_oe_status_retry")' in verifier
    assert "ix_oe_next_retry_at_status" not in verifier


def test_outbox_lease_index_matches_canonical_schema():
    indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in OutboxEvent.__table__.indexes
    }

    assert indexes["idx_oe_status_locked"] == ("status", "locked_at")
    assert _canonical_index_columns("idx_oe_status_locked") == indexes["idx_oe_status_locked"]

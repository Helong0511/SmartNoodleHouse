"""Regression tests for production database verification."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_database", ROOT / "scripts" / "验证数据库.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_current_migration_head_is_detected_without_revision_prefix_assumptions():
    heads = MODULE._expected_alembic_heads()
    assert "0048_reconcile_store_scope" in heads
    assert MODULE._alembic_heads_match({"0048_reconcile_store_scope"}, heads)


def test_old_or_partial_database_revision_is_not_reported_as_current():
    heads = MODULE._expected_alembic_heads()
    assert not MODULE._alembic_heads_match({"0001_baseline"}, heads)
    assert not MODULE._alembic_heads_match({"0047_payment_refund_idem"}, heads)


def test_multiple_heads_must_match_exactly():
    assert MODULE._alembic_heads_match({"head_a", "head_b"}, {"head_a", "head_b"})
    assert not MODULE._alembic_heads_match({"head_a"}, {"head_a", "head_b"})
    assert not MODULE._alembic_heads_match(set(), {"head_a"})


def test_production_verifier_includes_reconciliation_archive_and_outbox_tables():
    required = set(MODULE.REQUIRED_TABLES)
    assert {
        "payment_reconcile_batches",
        "payment_reconcile_diffs",
        "archived_order_snapshots",
        "outbox_events",
    } <= required


def test_production_verifier_checks_store_scope_and_outbox_recovery_columns():
    required = set(MODULE.REQUIRED_COLUMNS)
    assert {
        ("payment_reconcile_batches", "scope_store_id"),
        ("archived_order_snapshots", "store_id"),
        ("outbox_events", "status"),
        ("outbox_events", "attempt_count"),
        ("outbox_events", "locked_at"),
    } <= required


def test_database_charset_verifier_accepts_utf8mb4_everywhere():
    assert MODULE._charset_issues(
        {
            "character_set_client": "utf8mb4",
            "character_set_connection": "utf8mb4",
            "character_set_results": "utf8mb4",
        },
        "utf8mb4",
        "utf8mb4_unicode_ci",
        [("orders", "utf8mb4_unicode_ci"), ("customers", "utf8mb4_unicode_ci")],
        [("customers", "nickname", "utf8mb4")],
    ) == []


def test_database_charset_verifier_reports_connection_schema_table_and_column_mismatch():
    issues = MODULE._charset_issues(
        {
            "character_set_client": "latin1",
            "character_set_connection": "utf8mb4",
            "character_set_results": "utf8mb4",
        },
        "latin1",
        "latin1_swedish_ci",
        [("orders", "utf8mb4_0900_ai_ci")],
        [("customers", "nickname", "utf8mb3")],
    )

    assert any("character_set_client" in issue for issue in issues)
    assert any("database default charset" in issue for issue in issues)
    assert any("database default collation" in issue for issue in issues)
    assert any("table orders collation" in issue for issue in issues)
    assert any("customers.nickname charset" in issue for issue in issues)

"""Guard destructive Alembic migration contracts against accidental changes.

These are source-level safety checks, not a substitute for production-data preflight.
They deliberately make destructive behavior visible in CI so a future migration edit
cannot silently remove the warning or alter the retained-row policy.
"""
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "backend" / "alembic" / "versions"


def _source(name: str) -> str:
    return (MIGRATIONS / name).read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    # Alembic revisions may use either single-line or multi-line function signatures.
    match = re.search(
        rf"(?ms)^def {re.escape(name)}\([\s\S]*?\):[ \t]*\n"
        rf"(?P<body>.*?)(?=^def |^class |\Z)",
        source,
    )
    assert match, f"missing {name}()"
    return match.group("body")


def test_0002_downgrade_is_explicitly_data_destructive():
    source = _source("0002_p0_hardening.py")
    downgrade = _function_body(source, "downgrade")

    assert re.search(r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?point_ledger", downgrade, re.I)
    assert re.search(r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?financial_ledger", downgrade, re.I)


def test_0003_coupon_deduplication_conflict_remains_documented():
    source = _source("0003_p1_hardening.py")
    upgrade = _function_body(source, "upgrade")

    assert "DELETE cc FROM customer_coupons cc" in upgrade
    assert "keep.id < cc.id" in upgrade
    assert "uk_cc_customer_template" in upgrade
    assert "同模板可多张" in source
    assert "勿在已有多张同模板券的库上" in source


def test_0010_removes_the_coupon_uniqueness_rule_introduced_by_0003():
    source = _source("0010_coupon_ledger_idempotency.py")

    assert "uk_cc_customer_template" in source
    assert re.search(r"DROP\s+INDEX\s+uk_cc_customer_template", source, re.I)
    assert "同模板可多张" in source


def test_0017_financial_ledger_deduplication_keys_are_explicit():
    source = _source("0017_financial_ledger_idempotency.py")

    for column in ("payment_id", "refund_id", "delivery_order_id", "delivery_refund_id"):
        assert column in source
    # The migration iterates over _CONSTRAINTS and executes DELETE in that loop.
    # Assert the destructive statement and retained-row predicates at file scope:
    # the helper's text extraction is intentionally not coupled to nested loop shape.
    assert "DELETE f1 FROM financial_ledger f1 JOIN financial_ledger f2" in source
    assert "MIN(id)" in source
    assert "f1.id > f2.id" in source
    assert "uq_fin_ledger_entry_delivery_order" in source

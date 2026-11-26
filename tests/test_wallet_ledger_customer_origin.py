"""Wallet ledger search must include both current and original customer IDs after merge."""
from sqlalchemy import select
from sqlalchemy.dialects import mysql

from app.api.wallet import _ledger_customer_filter
from app.models import BalanceLedger


def test_wallet_ledger_customer_filter_matches_current_or_origin_customer():
    statement = select(1).select_from(BalanceLedger).where(_ledger_customer_filter(123))
    compiled = str(statement.compile(dialect=mysql.dialect()))
    assert "balance_ledger.customer_id =" in compiled
    assert "balance_ledger.origin_customer_id =" in compiled
    assert "OR" in compiled.upper()
    assert "123" in compiled

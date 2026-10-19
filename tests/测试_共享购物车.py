"""同桌共享购物车：规则常量、外带桌判定、乐观锁。"""
from types import SimpleNamespace

import pytest

from app.core.exceptions import AppException
from app.services.shared_cart_service import (
    CONFLICT_STRATEGY,
    DELETE_POLICY,
    HOST_CAN_CLEAR,
    SETTLEMENT_MODE,
    SharedCartService,
    is_takeaway_table,
)


def test_locked_business_rules():
    rules = SharedCartService.rules_payload()
    assert rules["settlement_mode"] == SETTLEMENT_MODE == "ONE_PAYS_ALL"
    assert rules["delete_policy"] == DELETE_POLICY == "OWN_LINES_ONLY"
    assert rules["host_can_clear"] is HOST_CAN_CLEAR is True
    assert rules["conflict_strategy"] == CONFLICT_STRATEGY == "OPTIMISTIC_REVISION"


def test_is_takeaway_table():
    assert is_takeaway_table(None) is True
    assert is_takeaway_table(SimpleNamespace(table_no="WM", table_name="外卖")) is True
    assert is_takeaway_table(SimpleNamespace(table_no="TKW", table_name="")) is True
    assert is_takeaway_table(SimpleNamespace(table_no="A01", table_name="外带自取")) is True
    assert is_takeaway_table(SimpleNamespace(table_no="A01", table_name="1号桌")) is False


def test_revision_conflict_raises_409():
    party = SimpleNamespace(revision=3)
    SharedCartService._check_revision(party, None)
    SharedCartService._check_revision(party, 3)
    with pytest.raises(AppException) as ei:
        SharedCartService._check_revision(party, 2)
    assert ei.value.code == 40920
    assert getattr(ei.value, "status_code", None) == 409

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


@pytest.mark.asyncio
async def test_shared_cart_default_disabled(monkeypatch):
    """门店开关默认关闭：join_party 不建局。"""

    async def _off(_db, _key, _default=False, store_id=None):
        return False

    monkeypatch.setattr(
        "app.services.system_settings_service.SystemSettingsService.effective_bool",
        _off,
    )

    class _Db:
        pass

    table = SimpleNamespace(id=1, store_id=1, status=1, table_no="A01", table_name="1号桌")
    customer = SimpleNamespace(id=9, nickname="测")
    assert await SharedCartService.is_enabled(_Db(), 1) is False
    with pytest.raises(AppException) as ei:
        await SharedCartService.ensure_enabled(_Db(), 1)
    assert ei.value.code == 40020
    out = await SharedCartService.join_party(_Db(), customer=customer, table=table)
    assert out is None


@pytest.mark.asyncio
async def test_validate_product_options_requires_group():
    """无规格绑定时直接通过；有必选组且未选时拒绝。"""
    from decimal import Decimal

    from app.services import shared_cart_service as scs

    class _EmptyResult:
        def all(self):
            return []

    class _Db:
        async def execute(self, *_a, **_k):
            return _EmptyResult()

    extra = await scs._validate_product_options(
        _Db(), product_id=1, option_ids=[], store_id=1
    )
    assert extra == Decimal("0.00")

    group = SimpleNamespace(
        id=10, name="辣度", is_required=True, selection_type="SINGLE", store_id=1, status=1
    )
    pog = SimpleNamespace(product_id=1, group_id=10)

    class _BoundResult:
        def all(self):
            return [(pog, group)]

    class _DbBound:
        async def execute(self, *_a, **_k):
            return _BoundResult()

    with pytest.raises(AppException) as ei:
        await scs._validate_product_options(
            _DbBound(), product_id=1, option_ids=[], store_id=1
        )
    assert ei.value.code == 40006

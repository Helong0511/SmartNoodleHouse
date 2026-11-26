from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.wallet import AdjustBody, adjust_balance


@pytest.mark.asyncio
async def test_super_admin_wallet_adjust_still_checks_customer_store_scope():
    row = SimpleNamespace(
        id=91,
        biz_no="WA-TEST",
        principal_after=Decimal("20.00"),
        gift_after=Decimal("5.00"),
    )
    db = MagicMock()
    user = SimpleNamespace(id=1, role=SimpleNamespace(code="SUPER_ADMIN"))
    request = MagicMock()

    with patch(
        "app.api.customers._assert_customer_in_store",
        new_callable=AsyncMock,
    ) as assert_customer_scope, patch(
        "app.api.wallet.BalanceService.adjust",
        new_callable=AsyncMock,
        return_value=row,
    ) as adjust, patch(
        "app.api.wallet.AuditService.log",
        new_callable=AsyncMock,
    ):
        await adjust_balance(
            AdjustBody(
                customer_id=42,
                delta_principal=Decimal("10.00"),
                store_id=7,
            ),
            request,
            db,
            user,
        )

    assert_customer_scope.assert_awaited_once_with(
        db, 42, 7, action="调账"
    )
    assert adjust.await_args.kwargs["customer_id"] == 42
    assert adjust.await_args.kwargs["store_id"] == 7


@pytest.mark.asyncio
async def test_super_admin_wallet_adjust_requires_explicit_store():
    from app.core.exceptions import AppException

    db = MagicMock()
    user = SimpleNamespace(id=1, role=SimpleNamespace(code="SUPER_ADMIN"))

    with pytest.raises(AppException):
        await adjust_balance(
            AdjustBody(
                customer_id=42,
                delta_principal=Decimal("10.00"),
                store_id=None,
            ),
            MagicMock(),
            db,
            user,
        )

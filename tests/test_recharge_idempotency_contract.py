import pytest
from pydantic import ValidationError

from app.api.wallet import RechargeCreate


def test_recharge_request_requires_idempotency_key():
    with pytest.raises(ValidationError):
        RechargeCreate(
            package_id=1,
            store_id=2,
            provider="WECHAT",
            channel_type="miniprogram",
        )


def test_recharge_request_normalizes_idempotency_key():
    request = RechargeCreate(
        package_id=1,
        store_id=2,
        provider="WECHAT",
        channel_type="miniprogram",
        idempotency_key="  recharge-key-123  ",
    )
    assert request.idempotency_key == "recharge-key-123"


def test_recharge_request_rejects_blank_idempotency_key():
    with pytest.raises(ValidationError):
        RechargeCreate(
            package_id=1,
            store_id=2,
            provider="WECHAT",
            channel_type="miniprogram",
            idempotency_key="        ",
        )


def test_existing_recharge_retry_precedes_new_package_validation():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "backend/app/api/wallet.py"
    ).read_text(encoding="utf-8")
    start = source.index("async def create_recharge(")
    end = source.index("@router.get(\"/recharge/mine\")", start)
    method = source[start:end]
    # A retry must resolve its immutable RechargeOrder snapshot before checking
    # whether the package is still enabled for new purchases.
    assert method.index("RechargeOrder.idempotency_key") < method.index(
        "pkg.status"
    )
    assert "int(existed.package_id or 0) != int(data.package_id)" in method

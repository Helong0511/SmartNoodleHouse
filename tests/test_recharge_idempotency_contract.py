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

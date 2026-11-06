"""支付回调验签边界回归：商户归属、金额字段与支付宝签名类型。"""
from __future__ import annotations

import base64
import json
import time
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import AppException
from app.services.payment_verify import AlipayVerifier, WechatPayVerifier
from app.services.payment_service import AlipayProvider, PaymentService
from app.services.alipay_client import trade_refund_query
from app.services.payment_service import (
    _validate_refund_callback_binding,
    _validate_duplicate_paid_callback,
    _wechat_callback_receipt_identity,
)


def _wechat_settings():
    return SimpleNamespace(
        wechat_api_v3_key="k" * 32,
        wechat_mch_id="1900000001",
        wechat_app_id="wx-app-1",
        wechat_platform_public_key="PUBLIC KEY",
        wechat_platform_serial_no="ABCD",
        wechat_platform_cert_path="/nonexistent/platform.pem",
    )


def _wechat_call(inner, serial="ABCD"):
    inner.setdefault("appid", "wx-app-1")
    body = json.dumps(
        {
            "event_type": "TRANSACTION.SUCCESS",
            "resource": {"ciphertext": "YQ==", "nonce": "123456789012"},
        },
        separators=(",", ":"),
    ).encode()
    headers = {
        "Wechatpay-Signature": base64.b64encode(b"signature").decode(),
        "Wechatpay-Timestamp": str(int(time.time())),
        "Wechatpay-Nonce": "nonce",
        "Wechatpay-Serial": serial,
    }
    key = MagicMock()
    key.verify = MagicMock(return_value=None)
    with patch("app.services.payment_verify.get_settings", return_value=_wechat_settings()), patch(
        "app.services.payment_verify._load_wechat_platform_public_key", return_value=key
    ), patch.object(WechatPayVerifier, "_decrypt_resource", return_value=json.dumps(inner)):
        return WechatPayVerifier().verify_and_parse(headers, body)


def test_wechat_transaction_callback_requires_matching_merchant():
    with pytest.raises(ValueError, match="商户号"):
        _wechat_call(
            {
                "out_trade_no": "P1",
                "transaction_id": "T1",
                "trade_state": "SUCCESS",
                "amount": {"total": 100, "currency": "CNY"},
            }
        )


def test_wechat_transaction_callback_rejects_appid_mismatch():
    with pytest.raises(ValueError, match="appid"):
        _wechat_call(
            {
                "appid": "other-app",
                "mchid": "1900000001",
                "out_trade_no": "P1",
                "transaction_id": "T1",
                "trade_state": "SUCCESS",
                "amount": {"total": 100, "currency": "CNY"},
            }
        )


def test_wechat_transaction_callback_requires_positive_amount():
    with pytest.raises(ValueError, match="金额"):
        _wechat_call(
            {
                "mchid": "1900000001",
                "out_trade_no": "P1",
                "transaction_id": "T1",
                "trade_state": "SUCCESS",
                "amount": {"total": 0, "currency": "CNY"},
            }
        )


def test_wechat_transaction_callback_rejects_non_cny():
    with pytest.raises(ValueError, match="CNY"):
        _wechat_call(
            {
                "mchid": "1900000001",
                "out_trade_no": "P1",
                "transaction_id": "T1",
                "trade_state": "SUCCESS",
                "amount": {"total": 100, "currency": "USD"},
            }
        )


def test_wechat_transaction_callback_accepts_valid_payload():
    result = _wechat_call(
        {
            "mchid": "1900000001",
            "out_trade_no": "P1",
            "transaction_id": "T1",
            "trade_state": "SUCCESS",
            "amount": {"total": 100, "currency": "CNY"},
        }
    )
    assert result["out_trade_no"] == "P1"
    assert result["amount"]["total"] == 100


def test_alipay_callback_requires_rsa2_and_app_id():
    verifier = AlipayVerifier()
    settings = SimpleNamespace(alipay_public_key="PUBLIC KEY", alipay_app_id="app-1")
    body = b"sign=ZmFrZQ%3D%3D&sign_type=RSA2&app_id=app-1&trade_status=TRADE_SUCCESS"
    with patch("app.services.payment_verify.get_settings", return_value=settings), patch.object(
        AlipayVerifier, "_verify_rsa2", return_value=True
    ):
        parsed = verifier.verify_and_parse({}, body)
    assert parsed["app_id"] == "app-1"


def test_alipay_callback_rejects_missing_app_id():
    verifier = AlipayVerifier()
    settings = SimpleNamespace(alipay_public_key="PUBLIC KEY", alipay_app_id="app-1")
    body = b"sign=ZmFrZQ%3D%3D&sign_type=RSA2&trade_status=TRADE_SUCCESS"
    with patch("app.services.payment_verify.get_settings", return_value=settings), patch.object(
        AlipayVerifier, "_verify_rsa2", return_value=True
    ):
        with pytest.raises(ValueError, match="app_id"):
            verifier.verify_and_parse({}, body)


def test_alipay_callback_rejects_non_rsa2():
    verifier = AlipayVerifier()
    settings = SimpleNamespace(alipay_public_key="PUBLIC KEY", alipay_app_id="app-1")
    body = b"sign=ZmFrZQ%3D%3D&sign_type=RSA&app_id=app-1&trade_status=TRADE_SUCCESS"
    with patch("app.services.payment_verify.get_settings", return_value=settings):
        with pytest.raises(ValueError, match="RSA2"):
            verifier.verify_and_parse({}, body)


def test_wechat_public_key_callback_requires_matching_serial():
    with pytest.raises(ValueError, match="序列号"):
        _wechat_call(
            {
                "mchid": "1900000001",
                "out_trade_no": "P1",
                "transaction_id": "T1",
                "trade_state": "SUCCESS",
                "amount": {"total": 100, "currency": "CNY"},
            },
            serial="WRONG",
        )


def test_alipay_signature_base64_must_be_strict():
    fake_key = MagicMock()
    with patch("app.services.payment_verify._load_pem", return_value=fake_key):
        assert AlipayVerifier._verify_rsa2("content", "%%%not-base64%%%", "PUBLIC KEY") is False
    fake_key.verify.assert_not_called()



def test_alipay_production_callback_requires_configured_app_id():
    verifier = AlipayVerifier()
    settings = SimpleNamespace(
        alipay_public_key="PUBLIC KEY",
        alipay_app_id="",
        is_production=True,
    )
    body = b"sign=ZmFrZQ%3D%3D&sign_type=RSA2&app_id=app-1&trade_status=TRADE_SUCCESS"
    with patch("app.services.payment_verify.get_settings", return_value=settings), patch.object(
        AlipayVerifier, "_verify_rsa2", return_value=True
    ):
        with pytest.raises(ValueError, match="ALIPAY_APP_ID"):
            verifier.verify_and_parse({}, body)


def test_alipay_callback_rejects_app_id_mismatch():
    verifier = AlipayVerifier()
    settings = SimpleNamespace(
        alipay_public_key="PUBLIC KEY",
        alipay_app_id="expected-app",
        is_production=False,
    )
    body = b"sign=ZmFrZQ%3D%3D&sign_type=RSA2&app_id=other-app&trade_status=TRADE_SUCCESS"
    with patch("app.services.payment_verify.get_settings", return_value=settings), patch.object(
        AlipayVerifier, "_verify_rsa2", return_value=True
    ):
        with pytest.raises(ValueError, match="app_id"):
            verifier.verify_and_parse({}, body)



def _refund_binding_objects(*, provider="WECHAT", payment_no="P-100", refund_no="R-100"):
    payment = SimpleNamespace(
        id=10,
        order_id=20,
        payment_no=payment_no,
        provider=provider,
        provider_trade_no="TRADE-100",
    )
    refund = SimpleNamespace(
        id=30,
        payment_id=payment.id,
        order_id=payment.order_id,
        refund_no=refund_no,
    )
    parsed = {
        "payment_no": payment_no,
        "refund_no": refund_no,
        "provider_trade_no": "TRADE-100",
    }
    return payment, refund, parsed


def test_refund_callback_requires_exact_original_payment_binding():
    payment, refund, parsed = _refund_binding_objects()
    parsed["payment_no"] = "SOME-OTHER-PAYMENT"
    with pytest.raises(AppException, match="原支付单号"):
        _validate_refund_callback_binding("WECHAT", parsed, refund, payment)


def test_refund_callback_rejects_provider_mismatch():
    payment, refund, parsed = _refund_binding_objects(provider="ALIPAY")
    with pytest.raises(AppException, match="支付渠道"):
        _validate_refund_callback_binding("WECHAT", parsed, refund, payment)


def test_refund_callback_rejects_payment_without_order_binding():
    payment, refund, parsed = _refund_binding_objects()
    payment.order_id = None
    with pytest.raises(AppException, match="订单关联不一致"):
        _validate_refund_callback_binding("WECHAT", parsed, refund, payment)


def test_refund_callback_allows_non_order_recharge_when_both_order_ids_are_null():
    payment, refund, parsed = _refund_binding_objects()
    payment.order_id = None
    refund.order_id = None
    _validate_refund_callback_binding("WECHAT", parsed, refund, payment)


def test_refund_callback_rejects_cross_order_refund_record():
    payment, refund, parsed = _refund_binding_objects()
    refund.order_id = 999
    with pytest.raises(AppException, match="关联不一致"):
        _validate_refund_callback_binding("WECHAT", parsed, refund, payment)


def test_refund_callback_rejects_mismatched_provider_trade_no():
    payment, refund, parsed = _refund_binding_objects()
    parsed["provider_trade_no"] = "OTHER-TRADE"
    with pytest.raises(AppException, match="原交易号"):
        _validate_refund_callback_binding("WECHAT", parsed, refund, payment)


def test_refund_callback_rejects_missing_local_provider_trade_no():
    payment, refund, parsed = _refund_binding_objects()
    payment.provider_trade_no = None
    with pytest.raises(AppException, match="原交易号"):
        _validate_refund_callback_binding("WECHAT", parsed, refund, payment)


def test_refund_callback_rejects_mismatched_refund_no():
    payment, refund, parsed = _refund_binding_objects()
    parsed["refund_no"] = "ANOTHER-REFUND"
    with pytest.raises(AppException, match="退款单号"):
        _validate_refund_callback_binding("WECHAT", parsed, refund, payment)


def test_refund_callback_accepts_exact_alipay_refund_request_number():
    payment, refund, parsed = _refund_binding_objects(provider="ALIPAY")
    _validate_refund_callback_binding("ALIPAY", parsed, refund, payment)


def test_refund_callback_rejects_payment_number_as_refund_number():
    payment, refund, parsed = _refund_binding_objects(provider="ALIPAY")
    parsed["refund_no"] = payment.payment_no
    with pytest.raises(AppException, match="退款单号"):
        _validate_refund_callback_binding("ALIPAY", parsed, refund, payment)


def test_refund_callback_rejects_missing_refund_number():
    payment, refund, parsed = _refund_binding_objects(provider="ALIPAY")
    parsed.pop("refund_no")
    with pytest.raises(AppException, match="缺少商户退款单号"):
        _validate_refund_callback_binding("ALIPAY", parsed, refund, payment)



class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _RefundCallbackDB:
    def __init__(self, refund, payment):
        self.refund = refund
        self.payment = payment
        self.flush = AsyncMock()

    async def execute(self, statement):
        return _ScalarResult(self.refund)

    async def get(self, model, row_id):
        return self.payment


@pytest.mark.asyncio
async def test_alipay_provider_preserves_actual_refund_status_instead_of_synthesizing_success():
    parsed = await AlipayProvider().handle_callback(
        {
            "out_trade_no": "P-100",
            "trade_no": "TRADE-100",
            "out_biz_no": "R-100",
            "refund_fee": "7.00",
            "refund_status": "WAIT_SELLER_AGREE",
            "trade_status": "TRADE_SUCCESS",
        }
    )
    assert parsed["is_refund"] is True
    assert parsed["refund_status"] == "WAIT_SELLER_AGREE"
    assert parsed["refund_fee"] == "7.00"


@pytest.mark.asyncio
async def test_alipay_callback_uses_exact_refund_query_amount_not_cumulative_refund_fee(monkeypatch):
    payment, refund, parsed = _refund_binding_objects(provider="ALIPAY")
    refund.amount = 5
    payment.amount = 20
    parsed["refund_fee"] = "10.00"  # 已累计退款 10 元；本次退款申请仅 5 元
    db = _RefundCallbackDB(refund, payment)
    query = AsyncMock(
        return_value={
            "status": "SUCCESS",
            "amount": "5.00",
            "provider_refund_no": "TRADE-100",
            "raw": {"refund_status": "REFUND_SUCCESS"},
        }
    )
    monkeypatch.setattr("app.services.payment_service.alipay_client.trade_refund_query", query)
    finalize = AsyncMock(return_value=refund)
    monkeypatch.setattr(PaymentService, "_finalize_refund", finalize)

    result = await PaymentService._apply_refund_callback(
        db, "ALIPAY", {}, parsed, "receipt-1"
    )

    assert result is refund
    query.assert_awaited_once_with(payment_no="P-100", refund_no="R-100")
    assert finalize.await_args.args[2] == "SUCCESS"


@pytest.mark.asyncio
async def test_alipay_callback_can_confirm_by_query_when_optional_cumulative_fee_is_absent(monkeypatch):
    payment, refund, parsed = _refund_binding_objects(provider="ALIPAY")
    refund.amount = 5
    payment.amount = 20
    parsed.pop("refund_fee", None)
    db = _RefundCallbackDB(refund, payment)
    monkeypatch.setattr(
        "app.services.payment_service.alipay_client.trade_refund_query",
        AsyncMock(return_value={"status": "SUCCESS", "amount": "5.00", "raw": {}}),
    )
    finalize = AsyncMock(return_value=refund)
    monkeypatch.setattr(PaymentService, "_finalize_refund", finalize)

    await PaymentService._apply_refund_callback(db, "ALIPAY", {}, parsed, "receipt-no-cumulative")

    assert finalize.await_args.args[2] == "SUCCESS"


@pytest.mark.asyncio
async def test_alipay_callback_does_not_promote_waiting_refund_to_success(monkeypatch):
    payment, refund, parsed = _refund_binding_objects(provider="ALIPAY")
    refund.amount = 5
    payment.amount = 20
    parsed["refund_fee"] = "2.00"  # 之前已有退款；当前这笔仍在等待
    db = _RefundCallbackDB(refund, payment)
    monkeypatch.setattr(
        "app.services.payment_service.alipay_client.trade_refund_query",
        AsyncMock(return_value={"status": "PROCESSING", "amount": None, "raw": {}}),
    )
    finalize = AsyncMock(return_value=refund)
    monkeypatch.setattr(PaymentService, "_finalize_refund", finalize)

    await PaymentService._apply_refund_callback(db, "ALIPAY", {}, parsed, "receipt-2")

    assert finalize.await_args.args[2] == "PROCESSING"


@pytest.mark.asyncio
async def test_alipay_callback_rejects_refund_query_amount_mismatch(monkeypatch):
    payment, refund, parsed = _refund_binding_objects(provider="ALIPAY")
    refund.amount = 5
    payment.amount = 20
    parsed["refund_fee"] = "10.00"
    db = _RefundCallbackDB(refund, payment)
    monkeypatch.setattr(
        "app.services.payment_service.alipay_client.trade_refund_query",
        AsyncMock(return_value={"status": "SUCCESS", "amount": "4.00", "raw": {}}),
    )
    finalize = AsyncMock(return_value=refund)
    monkeypatch.setattr(PaymentService, "_finalize_refund", finalize)

    with pytest.raises(AppException, match="本次退款金额"):
        await PaymentService._apply_refund_callback(db, "ALIPAY", {}, parsed, "receipt-3")
    finalize.assert_not_awaited()



def _verify_alipay_with_settings(settings, body):
    with patch("app.services.payment_verify.get_settings", return_value=settings), patch.object(
        AlipayVerifier, "_verify_rsa2", return_value=True
    ):
        return AlipayVerifier().verify_and_parse({}, body)


def test_alipay_callback_rejects_seller_id_mismatch():
    settings = SimpleNamespace(
        alipay_public_key="PUBLIC KEY",
        alipay_app_id="app-1",
        alipay_seller_id="seller-1",
        alipay_seller_email="",
        is_production=True,
    )
    body = b"sign=ZmFrZQ%3D%3D&sign_type=RSA2&app_id=app-1&seller_id=seller-2"
    with pytest.raises(ValueError, match="seller_id"):
        _verify_alipay_with_settings(settings, body)


def test_alipay_callback_requires_seller_identity_configuration_in_production():
    settings = SimpleNamespace(
        alipay_public_key="PUBLIC KEY",
        alipay_app_id="app-1",
        alipay_seller_id="",
        alipay_seller_email="",
        is_production=True,
    )
    body = b"sign=ZmFrZQ%3D%3D&sign_type=RSA2&app_id=app-1&seller_id=seller-1"
    with pytest.raises(ValueError, match="SELLER_ID"):
        _verify_alipay_with_settings(settings, body)


def test_alipay_callback_accepts_matching_seller_email_in_production():
    settings = SimpleNamespace(
        alipay_public_key="PUBLIC KEY",
        alipay_app_id="app-1",
        alipay_seller_id="",
        alipay_seller_email="merchant@example.com",
        is_production=True,
    )
    body = (
        b"sign=ZmFrZQ%3D%3D&sign_type=RSA2&app_id=app-1"
        b"&seller_email=MERCHANT%40EXAMPLE.COM"
    )
    parsed = _verify_alipay_with_settings(settings, body)
    assert parsed["seller_email"].casefold() == "merchant@example.com"



@pytest.mark.asyncio
async def test_wechat_refund_callback_binds_original_total_and_refund_amount(monkeypatch):
    payment, refund, parsed = _refund_binding_objects(provider="WECHAT")
    payment.amount = 10
    refund.amount = 5
    parsed["refund_amount"] = 500
    parsed["amount"] = {"total": 1_000, "refund": 500, "currency": "CNY"}
    db = _RefundCallbackDB(refund, payment)
    finalize = AsyncMock(return_value=refund)
    monkeypatch.setattr(PaymentService, "_finalize_refund", finalize)

    await PaymentService._apply_refund_callback(db, "WECHAT", {"event_type": "REFUND.SUCCESS"}, parsed, "wx-receipt")

    assert finalize.await_args.args[2] == "SUCCESS"


@pytest.mark.asyncio
async def test_wechat_refund_callback_rejects_original_total_mismatch(monkeypatch):
    payment, refund, parsed = _refund_binding_objects(provider="WECHAT")
    payment.amount = 10
    refund.amount = 5
    parsed["refund_amount"] = 500
    parsed["amount"] = {"total": 900, "refund": 500, "currency": "CNY"}
    db = _RefundCallbackDB(refund, payment)
    finalize = AsyncMock(return_value=refund)
    monkeypatch.setattr(PaymentService, "_finalize_refund", finalize)

    with pytest.raises(AppException, match="原订单总金额"):
        await PaymentService._apply_refund_callback(db, "WECHAT", {"event_type": "REFUND.SUCCESS"}, parsed, "wx-receipt")
    finalize.assert_not_awaited()


@pytest.mark.asyncio
async def test_wechat_refund_callback_rejects_non_cny_refund(monkeypatch):
    payment, refund, parsed = _refund_binding_objects(provider="WECHAT")
    payment.amount = 10
    refund.amount = 5
    parsed["refund_amount"] = 500
    parsed["amount"] = {"total": 1_000, "refund": 500, "currency": "USD"}
    db = _RefundCallbackDB(refund, payment)
    finalize = AsyncMock(return_value=refund)
    monkeypatch.setattr(PaymentService, "_finalize_refund", finalize)

    with pytest.raises(AppException, match="币种"):
        await PaymentService._apply_refund_callback(db, "WECHAT", {"event_type": "REFUND.SUCCESS"}, parsed, "wx-receipt")
    finalize.assert_not_awaited()



@pytest.mark.asyncio
async def test_alipay_refund_query_waiting_status_is_not_marked_failed(monkeypatch):
    monkeypatch.setattr(
        "app.services.alipay_client._gateway_call",
        AsyncMock(
            return_value={
                "code": "10000",
                "refund_status": "WAIT_SELLER_AGREE",
                "refund_amount": "5.00",
            }
        ),
    )
    result = await trade_refund_query(payment_no="P-100", refund_no="R-100")
    assert result["status"] == "PROCESSING"
    assert result["amount"] == "5.00"


@pytest.mark.asyncio
async def test_alipay_refund_query_only_refund_success_is_success(monkeypatch):
    monkeypatch.setattr(
        "app.services.alipay_client._gateway_call",
        AsyncMock(
            return_value={
                "code": "10000",
                "refund_status": "REFUND_SUCCESS",
                "refund_amount": "5.00",
            }
        ),
    )
    result = await trade_refund_query(payment_no="P-100", refund_no="R-100")
    assert result["status"] == "SUCCESS"


@pytest.mark.asyncio
async def test_wechat_abnormal_refund_remains_reserved_and_not_failed():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from decimal import Decimal

    row = SimpleNamespace(status="PROCESSING", fail_reason=None)
    db = SimpleNamespace(flush=AsyncMock())
    await PaymentService._finalize_refund(
        db,
        row,
        "ABNORMAL",
        fail_reason="微信退款异常",
        source="CALLBACK",
    )
    assert row.status == "ABNORMAL"
    assert row.status != "FAILED"


@pytest.mark.asyncio
async def test_abnormal_refund_is_included_in_refundable_amount_reservation():
    from decimal import Decimal
    from unittest.mock import AsyncMock
    from types import SimpleNamespace

    db = SimpleNamespace(scalar=AsyncMock(return_value=Decimal("10.00")))
    payment = SimpleNamespace(id=77, amount=Decimal("10.00"))
    remaining = await PaymentService.refundable_amount(db, payment)
    statement = db.scalar.await_args.args[0]
    compiled = statement.compile(compile_kwargs={"literal_binds": True})
    assert "ABNORMAL" in str(compiled)
    assert remaining == Decimal("0.00")


@pytest.mark.asyncio
async def test_alipay_refund_query_does_not_mislabel_original_trade_no_as_refund_id(monkeypatch):
    monkeypatch.setattr(
        "app.services.alipay_client._gateway_call",
        AsyncMock(
            return_value={
                "code": "10000",
                "refund_status": "REFUND_SUCCESS",
                "refund_amount": "5.00",
                "trade_no": "2026110400000000000000000000",
            }
        ),
    )
    result = await trade_refund_query(payment_no="P-100", refund_no="R-100")
    assert result["status"] == "SUCCESS"
    assert result["provider_refund_no"] is None


@pytest.mark.asyncio
async def test_alipay_callback_does_not_treat_payment_trade_no_as_refund_id():
    data = {
        "trade_status": "",
        "out_trade_no": "P-100",
        "trade_no": "2026110400000000000000000000",
        "refund_fee": "5.00",
        "gmt_refund": "2026-11-04 10:00:00",
        "out_biz_no": "R-100",
    }
    parsed = await AlipayProvider().handle_callback(data)
    assert parsed["provider_trade_no"] == data["trade_no"]
    assert parsed["provider_refund_no"] is None



class _SyncRefundDB:
    def __init__(self, payment, refund):
        self.payment = payment
        self.refund = refund

    async def get(self, model, row_id):
        return self.payment

    async def execute(self, statement):
        result = MagicMock()
        result.scalar_one_or_none.return_value = self.refund
        return result


def _sync_refund_objects(provider="ALIPAY"):
    payment = SimpleNamespace(
        id=1,
        order_id=2,
        provider=provider,
        payment_no="P-100",
        provider_trade_no="TRADE-100",
        amount=20,
    )
    refund = SimpleNamespace(
        id=9,
        payment_id=1,
        order_id=2,
        refund_no="R-100",
        amount=5,
        status="PROCESSING",
    )
    return payment, refund


@pytest.mark.asyncio
async def test_alipay_refund_query_poll_validates_per_request_amount(monkeypatch):
    payment, refund = _sync_refund_objects("ALIPAY")
    db = _SyncRefundDB(payment, refund)
    query = AsyncMock(
        return_value={
            "refund_no": "R-100",
            "payment_no": "P-100",
            "provider_trade_no": "TRADE-100",
            "status": "SUCCESS",
            "amount": "5.00",
        }
    )
    monkeypatch.setattr(PaymentService, "_query_channel_refund", query)
    finalize = AsyncMock(return_value=refund)
    monkeypatch.setattr(PaymentService, "_finalize_refund", finalize)

    result = await PaymentService.sync_refund_status(db, refund)

    assert result is refund
    assert finalize.await_args.args[2] == "SUCCESS"


@pytest.mark.asyncio
async def test_alipay_refund_query_poll_rejects_amount_mismatch(monkeypatch):
    payment, refund = _sync_refund_objects("ALIPAY")
    db = _SyncRefundDB(payment, refund)
    monkeypatch.setattr(
        PaymentService,
        "_query_channel_refund",
        AsyncMock(
            return_value={
                "refund_no": "R-100",
                "payment_no": "P-100",
                "provider_trade_no": "TRADE-100",
                "status": "SUCCESS",
                "amount": "4.00",
            }
        ),
    )
    finalize = AsyncMock(return_value=refund)
    monkeypatch.setattr(PaymentService, "_finalize_refund", finalize)

    with pytest.raises(AppException, match="本次退款金额"):
        await PaymentService.sync_refund_status(db, refund)
    finalize.assert_not_awaited()


@pytest.mark.asyncio
async def test_wechat_refund_query_poll_validates_refund_and_original_total(monkeypatch):
    payment, refund = _sync_refund_objects("WECHAT")
    payment.amount = 10
    db = _SyncRefundDB(payment, refund)
    monkeypatch.setattr(
        PaymentService,
        "_query_channel_refund",
        AsyncMock(
            return_value={
                "refund_no": "R-100",
                "payment_no": "P-100",
                "provider_trade_no": "TRADE-100",
                "status": "SUCCESS",
                "amount": {"refund": 500, "total": 1_000, "currency": "CNY"},
            }
        ),
    )
    finalize = AsyncMock(return_value=refund)
    monkeypatch.setattr(PaymentService, "_finalize_refund", finalize)

    await PaymentService.sync_refund_status(db, refund)

    assert finalize.await_args.args[2] == "SUCCESS"


@pytest.mark.asyncio
async def test_refund_query_poll_rejects_mismatched_refund_identifier(monkeypatch):
    payment, refund = _sync_refund_objects("ALIPAY")
    db = _SyncRefundDB(payment, refund)
    monkeypatch.setattr(
        PaymentService,
        "_query_channel_refund",
        AsyncMock(
            return_value={
                "refund_no": "R-OTHER",
                "payment_no": "P-100",
                "provider_trade_no": "TRADE-100",
                "status": "SUCCESS",
                "amount": "5.00",
            }
        ),
    )
    finalize = AsyncMock(return_value=refund)
    monkeypatch.setattr(PaymentService, "_finalize_refund", finalize)

    with pytest.raises(AppException, match="退款单号"):
        await PaymentService.sync_refund_status(db, refund)
    finalize.assert_not_awaited()



def test_duplicate_paid_callback_requires_same_provider_amount_and_trade_id():
    payment = SimpleNamespace(
        provider="ALIPAY",
        amount=Decimal("5.00"),
        provider_trade_no="TRADE-100",
    )
    parsed = {
        "status": "PAID",
        "amount": "5.00",
        "provider_trade_no": "TRADE-100",
    }
    _validate_duplicate_paid_callback("ALIPAY", payment, parsed)


def test_duplicate_paid_callback_rejects_cross_provider():
    payment = SimpleNamespace(
        provider="WECHAT",
        amount=Decimal("5.00"),
        provider_trade_no="WX-TRADE-100",
    )
    parsed = {
        "status": "PAID",
        "amount": 500,
        "provider_trade_no": "WX-TRADE-100",
    }
    with pytest.raises(AppException, match="渠道"):
        _validate_duplicate_paid_callback("ALIPAY", payment, parsed)


def test_duplicate_paid_callback_rejects_amount_or_trade_mismatch():
    payment = SimpleNamespace(
        provider="WECHAT",
        amount=Decimal("5.00"),
        provider_trade_no="WX-TRADE-100",
    )
    with pytest.raises(AppException, match="金额"):
        _validate_duplicate_paid_callback(
            "WECHAT",
            payment,
            {"status": "PAID", "amount": 400, "provider_trade_no": "WX-TRADE-100"},
        )
    with pytest.raises(AppException, match="交易号"):
        _validate_duplicate_paid_callback(
            "WECHAT",
            payment,
            {"status": "PAID", "amount": 500, "provider_trade_no": "WX-TRADE-OTHER"},
        )


@pytest.mark.asyncio
async def test_wechat_refund_query_uses_official_out_refund_no_path(monkeypatch):
    """微信官方接口路径为 /v3/refund/domestic/refunds/{out_refund_no}。"""
    from app.services import wechat_pay_client

    request_mock = AsyncMock(
        return_value={
            "status": "SUCCESS",
            "out_refund_no": "RF202611040001",
            "out_trade_no": "PAY202611040001",
            "transaction_id": "420000000000000001",
            "refund_id": "500000000000000001",
            "amount": {"refund": 100, "total": 100, "currency": "CNY"},
        }
    )
    monkeypatch.setattr(wechat_pay_client, "_request", request_mock)

    result = await wechat_pay_client.query_refund(refund_no="RF202611040001")

    request_mock.assert_awaited_once_with(
        "GET", "/v3/refund/domestic/refunds/RF202611040001"
    )
    assert result["status"] == "SUCCESS"
    assert result["refund_no"] == "RF202611040001"
    assert result["payment_no"] == "PAY202611040001"
    assert result["provider_trade_no"] == "420000000000000001"
    assert result["provider_refund_no"] == "500000000000000001"


@pytest.mark.asyncio
async def test_refund_finalization_locks_order_before_payment():
    """退款成功入账的锁顺序必须与支付回调一致：Order → Payment。"""
    order = SimpleNamespace(id=11)
    payment = SimpleNamespace(id=22, order_id=11)
    statements = []

    class _Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class _DB:
        async def execute(self, statement):
            sql = str(statement).lower()
            statements.append(sql)
            if "from orders" in sql:
                return _Result(order)
            if "from payments" in sql:
                return _Result(payment)
            raise AssertionError(f"Unexpected SQL: {sql}")

    locked_payment, locked_order = await PaymentService._lock_order_then_payment_for_refund(
        _DB(), SimpleNamespace(order_id=11, payment_id=22)
    )

    assert locked_order is order
    assert locked_payment is payment
    assert "from orders" in statements[0]
    assert "from payments" in statements[1]


@pytest.mark.asyncio
async def test_refund_finalization_rejects_cross_order_payment_binding():
    order = SimpleNamespace(id=11)
    payment = SimpleNamespace(id=22, order_id=12)

    class _Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class _DB:
        async def execute(self, statement):
            sql = str(statement).lower()
            if "from orders" in sql:
                return _Result(order)
            if "from payments" in sql:
                return _Result(payment)
            raise AssertionError(f"Unexpected SQL: {sql}")

    with pytest.raises(AppException, match="关联不一致"):
        await PaymentService._lock_order_then_payment_for_refund(
            _DB(), SimpleNamespace(order_id=11, payment_id=22)
        )




@pytest.mark.asyncio
async def test_business_integrity_error_is_rethrown_for_provider_retry(monkeypatch):
    import sqlite3

    from sqlalchemy.exc import IntegrityError

    from app.services import payment_service

    class Result:
        def scalar_one_or_none(self):
            return None

    class FakeDB:
        def add(self, obj):
            self.receipt = obj

        async def flush(self):
            return None

        async def rollback(self):
            return None

        async def execute(self, statement):
            return Result()

    class CleanDB(FakeDB):
        async def delete(self, obj):
            return None

        async def commit(self):
            return None

    class SessionContext:
        async def __aenter__(self):
            return CleanDB()

        async def __aexit__(self, *args):
            return False

    class Provider:
        async def handle_callback(self, data):
            return {
                "payment_no": "P-TEST",
                "provider_trade_no": "TRADE-TEST",
                "amount": "1.00",
                "status": "PAID",
                "app_id": "",
                "is_refund": False,
            }

    error = IntegrityError(
        "INSERT", {}, sqlite3.IntegrityError("FOREIGN KEY constraint failed")
    )
    release = AsyncMock()

    monkeypatch.setattr(payment_service.PaymentService, "get_provider", lambda _p: Provider())
    monkeypatch.setattr(AlipayVerifier, "verify_and_parse", lambda self, headers, body: {
        "out_trade_no": "P-TEST",
        "trade_no": "TRADE-TEST",
        "notify_id": "NOTIFY-TEST",
        "total_amount": "1.00",
        "trade_status": "TRADE_SUCCESS",
    })
    monkeypatch.setattr(payment_service, "get_settings", lambda: SimpleNamespace(
        is_production=False, alipay_app_id=""
    ))
    monkeypatch.setattr(payment_service, "claim_once", AsyncMock(return_value="owner-token"))
    monkeypatch.setattr(payment_service, "release_claim", release)
    monkeypatch.setattr(payment_service, "make_receipt_key", lambda *args: "receipt-test")
    monkeypatch.setattr(payment_service, "AsyncSessionLocal", lambda: SessionContext())
    monkeypatch.setattr(
        payment_service.PaymentService,
        "_apply_payment_callback",
        AsyncMock(side_effect=error),
    )

    with pytest.raises(IntegrityError):
        await PaymentService.handle_callback(FakeDB(), "ALIPAY", {}, b"callback")

    release.assert_awaited_once_with("pay:ALIPAY", "receipt-test", "owner-token")


@pytest.mark.asyncio
async def test_receipt_integrity_error_without_matching_receipt_is_not_acknowledged(monkeypatch):
    import sqlite3

    from sqlalchemy.exc import IntegrityError

    from app.services import payment_service

    class Result:
        def scalar_one_or_none(self):
            return None

    class FakeDB:
        def add(self, obj):
            self.receipt = obj

        async def flush(self):
            raise IntegrityError(
                "INSERT", {}, sqlite3.IntegrityError("FOREIGN KEY constraint failed")
            )

        async def rollback(self):
            return None

        async def execute(self, statement):
            return Result()

    class Provider:
        async def handle_callback(self, data):
            return {
                "payment_no": "P-TEST",
                "provider_trade_no": "TRADE-TEST",
                "amount": "1.00",
                "status": "PAID",
                "app_id": "",
                "is_refund": False,
            }

    release = AsyncMock()
    monkeypatch.setattr(payment_service.PaymentService, "get_provider", lambda _p: Provider())
    monkeypatch.setattr(AlipayVerifier, "verify_and_parse", lambda self, headers, body: {
        "out_trade_no": "P-TEST",
        "trade_no": "TRADE-TEST",
        "notify_id": "NOTIFY-TEST",
        "total_amount": "1.00",
        "trade_status": "TRADE_SUCCESS",
    })
    monkeypatch.setattr(payment_service, "get_settings", lambda: SimpleNamespace(
        is_production=False, alipay_app_id=""
    ))
    monkeypatch.setattr(payment_service, "claim_once", AsyncMock(return_value="owner-token"))
    monkeypatch.setattr(payment_service, "release_claim", release)
    monkeypatch.setattr(payment_service, "make_receipt_key", lambda *args: "receipt-test")

    with pytest.raises(IntegrityError):
        await PaymentService.handle_callback(FakeDB(), "ALIPAY", {}, b"callback")

    release.assert_awaited_once_with("pay:ALIPAY", "receipt-test", "owner-token")


def test_wechat_receipt_identity_is_stable_across_http_signature_nonce_retries():
    data = {
        "id": "EVT-100",
        "event_type": "TRANSACTION.SUCCESS",
        "out_trade_no": "P-100",
        "transaction_id": "WX-TRADE-100",
    }
    parsed = {
        "is_refund": False,
        "payment_no": "P-100",
        "provider_trade_no": "WX-TRADE-100",
        "status": "PAID",
    }

    # HTTP Wechatpay-Nonce 不参与业务通知身份；同一通知重试必须得到同一 key。
    first = _wechat_callback_receipt_identity(data, parsed)
    second = _wechat_callback_receipt_identity(data, parsed)
    assert first == second
    assert first[0] == "EVT-100"


def test_wechat_refund_receipt_identity_distinguishes_notification_states():
    base = {
        "out_refund_no": "R-100",
        "event_type": "REFUND.SUCCESS",
        "success_time": "2026-11-05T09:00:00+08:00",
    }
    parsed = {"is_refund": True, "refund_no": "R-100", "refund_status": "SUCCESS"}

    assert _wechat_callback_receipt_identity(base, parsed) == _wechat_callback_receipt_identity(
        dict(base), parsed
    )
    processing = {**base, "event_type": "REFUND.PROCESSING", "success_time": ""}
    assert _wechat_callback_receipt_identity(base, parsed) != _wechat_callback_receipt_identity(
        processing, {**parsed, "refund_status": "PROCESSING"}
    )

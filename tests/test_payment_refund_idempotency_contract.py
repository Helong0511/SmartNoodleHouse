from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_direct_refund_api_requires_idempotency_header() -> None:
    source = (ROOT / "backend/app/api/payments.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    endpoint = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "refund_payment"
    )
    argument_names = [arg.arg for arg in endpoint.args.args]
    assert "idempotency_key" in argument_names
    index = argument_names.index("idempotency_key")
    default = endpoint.args.defaults[index - (len(argument_names) - len(endpoint.args.defaults))]
    assert isinstance(default, ast.Call)
    assert isinstance(default.func, ast.Name) and default.func.id == "Header"
    assert any(
        keyword.arg == "alias"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value == "Idempotency-Key"
        for keyword in default.keywords
    )
    assert any(
        keyword.arg == "min_length"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value == 16
        for keyword in default.keywords
    )


def test_refund_ui_persists_same_request_key_before_network_call() -> None:
    source = (ROOT / "frontend/src/views/Payments.vue").read_text(encoding="utf-8")
    persist_at = source.index("persistRefundAttempt(paymentNo, attempt)")
    request_at = source.index("api.post(`/payments/${paymentNo}/refund`")
    assert persist_at < request_at
    assert "headers: { 'Idempotency-Key': attempt.idempotencyKey }" in source
    assert "readRefundAttempt(String(row.payment_no))" in source
    assert "sessionStorage.setItem" in source
    assert "sessionStorage.removeItem" in source


def test_wechat_refund_query_requires_explicit_currency() -> None:
    source = (ROOT / "backend/app/services/payment_service.py").read_text(encoding="utf-8")
    start = source.index("def _validate_refund_query_result(")
    end = source.index("async def sync_refund_status(", start)
    validator = source[start:end]
    assert 'currency_raw = amount_info.get("currency")' in validator
    assert "if not currency_raw:" in validator
    assert "缺少币种，拒绝确认退款成功" in validator
    assert 'amount_info.get("currency") or "CNY"' not in validator

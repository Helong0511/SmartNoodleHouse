"""顾客伪造 channel=POS/MEITUAN 须被拒绝或由服务端覆盖。"""
from unittest.mock import MagicMock, patch

import pytest

from app.core.exceptions import AppException


def _request(auth: str | None = None):
    req = MagicMock()
    headers = {}
    if auth:
        headers["authorization"] = auth
    req.headers.get = lambda k, default=None: headers.get(k.lower(), headers.get(k, default))
    return req


def test_customer_channel_pos_rejected_without_jwt():
    from app.api.v1.orders import _resolve_customer_order_channel

    with pytest.raises(AppException) as ei:
        _resolve_customer_order_channel(_request(), "POS")
    assert ei.value.code == 40009


def test_customer_channel_meituan_rejected():
    from app.api.v1.orders import _resolve_customer_order_channel

    with pytest.raises(AppException) as ei:
        _resolve_customer_order_channel(_request(), "MEITUAN")
    assert ei.value.code == 40009


def test_jwt_wechat_overrides_client_pos():
    """JWT channel=WECHAT 时，客户端传 POS 仍强制为 WECHAT。"""
    from app.api.v1.orders import _resolve_customer_order_channel

    with patch(
        "app.core.security.decode_customer_token",
        return_value={"channel": "WECHAT", "sub": "1"},
    ):
        ch = _resolve_customer_order_channel(_request("Bearer fake.jwt"), "POS")
    assert ch == "WECHAT"


def test_h5_hint_allowed_without_platform_jwt():
    from app.api.v1.orders import _resolve_customer_order_channel

    assert _resolve_customer_order_channel(_request(), "H5") == "H5"
    assert _resolve_customer_order_channel(_request(), "WECHAT") == "WECHAT"

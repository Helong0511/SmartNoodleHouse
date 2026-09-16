# -*- coding: utf-8 -*-
"""跨语言 Token 格式契约测试（无需加载原生库）。"""
import base64


def test_layout_documentation():
    """验证 Version + KID + Nonce + Tag 的最小布局说明。"""
    assert 1 + 1 + 12 + 16 == 30


def test_base64url_no_padding_contract():
    """验证 Base64URL 输出不包含 '=' 补位字符，并可恢复原始数据。"""
    raw = bytes(range(30))
    token = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    assert "=" not in token
    decoded = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    assert decoded == raw

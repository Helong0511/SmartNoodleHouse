"""桌台一码通扫链接生成。"""
from app.services.qr_links import build_table_qr_links, resolve_h5_public_base


def test_build_table_qr_links_contains_scan_and_schemes():
    links = build_table_qr_links("test-token-abc")
    assert "/scan?token=" in links["scan_url"]
    assert "test-token-abc" in links["scan_url"]
    assert links["wechat_scheme"].startswith("weixin://")
    assert "wechat" in links["wechat_scheme"]
    assert links["alipay_scheme"].startswith("alipays://")
    assert "alipay" in links["alipay_scheme"]
    assert "/order?token=" in links["h5_order_url"]


def test_resolve_h5_public_base_prefers_config(monkeypatch):
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "h5_public_base_url", "https://order.example.com")
    assert resolve_h5_public_base() == "https://order.example.com"

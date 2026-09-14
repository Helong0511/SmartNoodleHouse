"""桌台一码通扫链接生成。"""
from app.services.qr_links import build_table_qr_links, resolve_h5_public_base


def test_build_table_qr_links_contains_scan_and_schemes():
    links = build_table_qr_links("test-token-abc")
    assert "/scan?token=" in links["scan_url"]
    assert "test-token-abc" in links["scan_url"]
    assert links["wechat_scheme"].startswith("weixin://")
    assert "wechat" in links["wechat_scheme"]
    assert "env_version=" in links["wechat_scheme"]
    assert links["alipay_scheme"].startswith("alipays://")
    assert "alipay" in links["alipay_scheme"]
    assert "/order?token=" in links["h5_order_url"]


def test_build_table_qr_links_env_version_from_settings(monkeypatch):
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "mini_program_env_version", "trial")
    links = build_table_qr_links("tok")
    assert "env_version=trial" in links["wechat_scheme"]
    assert links["mini_program_env_version"] == "trial"


def test_resolve_h5_public_base_prefers_config(monkeypatch):
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "h5_public_base_url", "https://order.example.com")
    assert resolve_h5_public_base() == "https://order.example.com"


def test_resolve_h5_public_base_rewrites_localhost_in_dev(monkeypatch):
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "h5_public_base_url", "http://127.0.0.1:5173")
    monkeypatch.setattr(settings, "dev_lan_host", "192.168.1.110")
    assert resolve_h5_public_base() == "http://192.168.1.110:5173"

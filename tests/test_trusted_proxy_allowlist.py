from fastapi import Request

from app.core.ip_allowlist import _parse_allowlist, client_ip
from app.core.startup_checks import _invalid_trusted_proxy_entries


def test_trusted_proxy_validator_rejects_invalid_and_universal_ranges():
    assert _invalid_trusted_proxy_entries("10.0.0.0/8,127.0.0.1") == []
    assert _invalid_trusted_proxy_entries("0.0.0.0/0") == ["0.0.0.0/0"]
    assert _invalid_trusted_proxy_entries("::/0") == ["::/0"]
    assert _invalid_trusted_proxy_entries("not-an-ip") == ["not-an-ip"]
    assert _invalid_trusted_proxy_entries("") == []


def test_runtime_allowlist_parser_rejects_universal_cidr():
    parsed = _parse_allowlist("0.0.0.0/0,10.0.0.0/8")
    assert len(parsed) == 1
    assert str(parsed[0]) == "10.0.0.0/8"


def test_universal_trusted_proxy_range_cannot_enable_spoofed_forwarded_ip(monkeypatch):
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/auth/login",
        "headers": [(b"x-real-ip", b"203.0.113.99")],
        "client": ("198.51.100.10", 12345),
        "server": ("testserver", 80),
        "scheme": "http",
        "query_string": b"",
    })

    class Settings:
        trusted_proxy_ips = "0.0.0.0/0"

    class SettingsService:
        @staticmethod
        def resolved_str_sync(_key, fallback):
            return "0.0.0.0/0"

    monkeypatch.setattr("app.core.ip_allowlist.get_settings", lambda: Settings())
    monkeypatch.setattr(
        "app.services.system_settings_service.SystemSettingsService",
        SettingsService,
    )

    assert client_ip(request) == "198.51.100.10"

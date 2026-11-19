from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "迁移数据只读预检.py"
SPEC = importlib.util.spec_from_file_location("migration_readonly_preflight", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
PREFLIGHT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREFLIGHT)


def _financial_row(**overrides):
    row = {
        "id": 1,
        "store_id": 10,
        "entry_type": "SALE",
        "channel": "WECHAT",
        "provider": "WECHAT",
        "currency": "CNY",
        "order_id": 20,
        "payment_id": 30,
        "refund_id": None,
        "delivery_order_id": None,
        "delivery_refund_id": None,
        "amount": Decimal("18.00"),
    }
    row.update(overrides)
    return row


def test_financial_duplicate_report_flags_amount_store_and_channel_mismatches():
    result = PREFLIGHT.summarize_financial_rows([
        _financial_row(id=1),
        _financial_row(id=2, store_id=11, channel="ALIPAY", amount=Decimal("19.00")),
    ])

    assert result["facts_consistent"] is False
    assert set(result["fact_mismatches"]) == {"store_id", "channel", "amount"}
    assert result["fact_mismatches"]["amount"] == [
        {"id": 1, "value": "18.00"},
        {"id": 2, "value": "19.00"},
    ]


def test_financial_duplicate_report_accepts_identical_business_facts():
    result = PREFLIGHT.summarize_financial_rows([
        _financial_row(id=1),
        _financial_row(id=2),
    ])

    assert result["facts_consistent"] is True
    assert result["fact_mismatches"] == {}


def test_decimal_json_conversion_is_exact():
    assert PREFLIGHT._json_value(Decimal("0.10")) == "0.10"


def test_connection_requires_explicit_database_url(monkeypatch):
    monkeypatch.delenv("MIGRATION_PREFLIGHT_DATABASE_URL", raising=False)

    with pytest.raises(ValueError, match="显式设置"):
        PREFLIGHT._connect()


@pytest.mark.parametrize("database_url", [
    "mysql+aiomysql://user:password@db.example/smart_noodle_house",
    "sqlite:///production.db",
    "mysql+pymysql://user:password@db.example/",
])
def test_connection_rejects_unsupported_or_incomplete_database_urls(monkeypatch, database_url):
    monkeypatch.setenv("MIGRATION_PREFLIGHT_DATABASE_URL", database_url)

    with pytest.raises(ValueError):
        PREFLIGHT._connect()


def test_connection_rejects_unreviewed_url_query_parameters(monkeypatch):
    monkeypatch.setenv(
        "MIGRATION_PREFLIGHT_DATABASE_URL",
        "mysql+pymysql://user:password@db.example/smart_noodle_house?init_command=DROP",
    )

    with pytest.raises(ValueError, match="不支持的连接参数"):
        PREFLIGHT._connect()


def test_applied_revision_walks_current_head_ancestry(monkeypatch):
    parents = {
        "0051_head": "0017_fin_ledger_idempotency",
        "0017_fin_ledger_idempotency": "0003_p1_hardening",
        "0003_p1_hardening": "0002_p0_hardening",
        "0002_p0_hardening": "0001_baseline",
        "0001_baseline": None,
    }

    class FakeScripts:
        @classmethod
        def from_config(cls, _config):
            return cls()

        def get_revision(self, revision):
            if revision not in parents:
                return None
            return SimpleNamespace(down_revision=parents[revision])

    class FakeConfig:
        def __init__(self, _path):
            self.options = {}

        def set_main_option(self, key, value):
            self.options[key] = value

    monkeypatch.setattr(PREFLIGHT, "Config", FakeConfig)
    monkeypatch.setattr(PREFLIGHT, "ScriptDirectory", FakeScripts)

    applied = PREFLIGHT._applied_revisions(["0051_head"])

    assert PREFLIGHT.TARGET_COUPON in applied
    assert PREFLIGHT.TARGET_FINANCIAL in applied
    assert "0001_baseline" in applied


def test_unknown_database_revision_fails_closed(monkeypatch):
    class FakeScripts:
        @classmethod
        def from_config(cls, _config):
            return cls()

        def get_revision(self, _revision):
            return None

    class FakeConfig:
        def __init__(self, _path):
            self.options = {}

        def set_main_option(self, key, value):
            self.options[key] = value

    monkeypatch.setattr(PREFLIGHT, "Config", FakeConfig)
    monkeypatch.setattr(PREFLIGHT, "ScriptDirectory", FakeScripts)

    with pytest.raises(RuntimeError, match="revision 不存在"):
        PREFLIGHT._applied_revisions(["unknown_revision"])

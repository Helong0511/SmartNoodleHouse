"""券模板门店隔离单元测试。"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.exceptions import AppException
from app.services.coupon_service import CouponService


def test_template_visible_hq_and_store():
    hq = SimpleNamespace(store_id=None)
    s1 = SimpleNamespace(store_id=1)
    s2 = SimpleNamespace(store_id=2)
    assert CouponService.template_visible_to_store(hq, None) is True
    assert CouponService.template_visible_to_store(s1, None) is True
    assert CouponService.template_visible_to_store(hq, 1) is True
    assert CouponService.template_visible_to_store(s1, 1) is True
    assert CouponService.template_visible_to_store(s2, 1) is False


# ── P2-4 总部模板门店白名单 ─────────────────────────────────────────────────


def test_hq_whitelist_restricts_visibility():
    hq = SimpleNamespace(store_id=None, store_whitelist=[1, 3])
    assert CouponService.template_visible_to_store(hq, 1) is True
    assert CouponService.template_visible_to_store(hq, 3) is True
    assert CouponService.template_visible_to_store(hq, 2) is False
    # 超管全店视角仍可见
    assert CouponService.template_visible_to_store(hq, None) is True


def test_hq_whitelist_null_means_all_stores():
    hq = SimpleNamespace(store_id=None, store_whitelist=None)
    assert CouponService.template_visible_to_store(hq, 99) is True


def test_hq_whitelist_empty_list_means_all_stores():
    hq = SimpleNamespace(store_id=None, store_whitelist=[])
    assert CouponService.template_visible_to_store(hq, 99) is True


def test_normalize_whitelist_dedup_and_positive():
    assert CouponService._normalize_whitelist(None) is None
    assert CouponService._normalize_whitelist([]) is None
    assert CouponService._normalize_whitelist([3, 1, 3, 2, 0, -5]) == [1, 2, 3]


def test_normalize_whitelist_rejects_invalid():
    with pytest.raises(AppException) as ei:
        CouponService._normalize_whitelist("not-a-list")
    assert ei.value.code == 40069


def test_template_payload_includes_store_scope():
    tmpl = MagicMock()
    tmpl.id = 1
    tmpl.store_id = None
    tmpl.name = "新客满20减3"
    tmpl.coupon_type = "FIXED"
    tmpl.discount_value = "3"
    tmpl.min_order_amount = "20"
    tmpl.valid_days = 30
    tmpl.status = 1
    tmpl.created_at = None
    payload = CouponService.template_payload(tmpl)
    assert payload["store_id"] is None
    assert payload["store_scope"] == "HQ"

    tmpl.store_id = 3
    payload2 = CouponService.template_payload(tmpl)
    assert payload2["store_id"] == 3
    assert payload2["store_scope"] == "STORE"

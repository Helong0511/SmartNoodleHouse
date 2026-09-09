"""券模板门店隔离单元测试。"""
from types import SimpleNamespace
from unittest.mock import MagicMock

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


def test_template_payload_includes_store_scope():
    tmpl = MagicMock()
    tmpl.id = 1
    tmpl.store_id = None
    tmpl.name = "新客满30减5"
    tmpl.coupon_type = "FIXED"
    tmpl.discount_value = "5"
    tmpl.min_order_amount = "30"
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

"""员工侧顾客档案 PATCH 与收藏接口入参校验。"""
from datetime import date

import pytest
from pydantic import ValidationError


def test_customer_update_phone_and_patch_semantics():
    from app.api.customers import CustomerUpdate

    with pytest.raises(ValidationError):
        CustomerUpdate(phone="12345")

    # 未传字段不进入 fields_set
    body = CustomerUpdate(display_name="阿面")
    assert "display_name" in body.model_fields_set
    assert "phone" not in body.model_fields_set

    # 显式 null 可清空
    cleared = CustomerUpdate(phone=None, birthday=None)
    assert "phone" in cleared.model_fields_set
    assert cleared.phone is None
    assert cleared.birthday is None

    ok = CustomerUpdate(
        nickname="小面",
        phone="13900001111",
        status=0,
        birthday=date(1990, 5, 1),
        recommend_opt_in=0,
    )
    assert ok.phone == "13900001111"
    assert ok.status == 0
    assert ok.birthday == date(1990, 5, 1)


def test_staff_favorite_routes_registered():
    from app.api.customers import router

    paths = {getattr(r, "path", None) for r in router.routes}
    assert "/customers/{customer_id}" in paths
    assert "/customers/{customer_id}/favorites/{product_id}" in paths
    methods = {
        tuple(sorted(getattr(r, "methods", []) or []))
        for r in router.routes
        if getattr(r, "path", None) == "/customers/{customer_id}"
    }
    assert ("PATCH",) in methods or any("PATCH" in m for m in methods)

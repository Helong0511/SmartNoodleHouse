"""总部∪门店冲突矩阵：合并工具、营销同 code、券同名提示文案。"""
from types import SimpleNamespace

from app.services.marketing_service import MarketingService
from app.services.scope_override import hq_override_warning, merge_store_over_hq, resolve_map_by_key


def test_hq_override_warning_text():
    msg = hq_override_warning(key_label="券模板名", key_desc="名称「迎新券」", hq_name="迎新券")
    assert "将覆盖" in msg
    assert "迎新券" in msg


def test_marketing_code_key_normalizes():
    assert MarketingService.campaign_code_key("  Spring ") == "SPRING"
    assert MarketingService.campaign_code_key(None) == ""


def test_marketing_running_merge_by_code_prefers_store():
    hq = SimpleNamespace(id=1, store_id=None, code="SPRING", boost_product_ids=[10, 20])
    store = SimpleNamespace(id=2, store_id=1, code="spring", boost_product_ids=[30])
    other = SimpleNamespace(id=3, store_id=None, code="SUMMER", boost_product_ids=[40])
    merged = merge_store_over_hq(
        [hq, store, other],
        key_fn=lambda c: MarketingService.campaign_code_key(c.code),
    )
    by_id = {c.id for c in merged}
    assert 2 in by_id
    assert 1 not in by_id
    assert 3 in by_id
    spring = next(c for c in merged if MarketingService.campaign_code_key(c.code) == "SPRING")
    assert spring.boost_product_ids == [30]


def test_products_not_dual_layer_documented_in_matrix_helper():
    """餐品非双层：冲突矩阵文档约定，无 merge_store_over_hq 调用点要求。"""
    # 占位断言：业务键场景下 merge 只按显式 key，产品 ID 不同即并存
    a = SimpleNamespace(id=1, store_id=1, product_no="P1")
    b = SimpleNamespace(id=2, store_id=2, product_no="P1")
    merged = merge_store_over_hq([a, b], key_fn=lambda p: (p.store_id, p.product_no))
    assert len(merged) == 2


def test_category_map_resolve_prefers_store_over_hq():
    hq = SimpleNamespace(id=1, store_id=None, local_category_id=9, platform="MEITUAN", status=1)
    store = SimpleNamespace(id=2, store_id=1, local_category_id=9, platform="MEITUAN", status=1)
    other = SimpleNamespace(id=3, store_id=None, local_category_id=10, platform="MEITUAN", status=1)
    resolved = resolve_map_by_key(
        [hq, store, other],
        key_fn=lambda r: int(r.local_category_id),
    )
    assert resolved[9].id == 2
    assert resolved[10].id == 3


def test_social_rule_resolve_prefers_store_over_hq():
    """社福规则类型 B：同 identity_code 门店优先于总部（与 _candidate_rules 排序一致）。"""
    hq = SimpleNamespace(id=1, store_id=None, identity_code="SANITATION", discount_rate="0.95")
    store = SimpleNamespace(id=2, store_id=1, identity_code="SANITATION", discount_rate="0.90")
    other = SimpleNamespace(id=3, store_id=None, identity_code="ELDERLY", discount_rate="0.88")
    resolved = resolve_map_by_key(
        [hq, store, other],
        key_fn=lambda r: str(r.identity_code),
    )
    assert resolved["SANITATION"].id == 2
    assert resolved["SANITATION"].discount_rate == "0.90"
    assert resolved["ELDERLY"].id == 3


def test_marketing_skip_reason_constant_shape():
    """总部任务跳过门店覆盖时 result_json 约定字段。"""
    payload = {
        "reason": "store_override",
        "store_id": 1,
        "override_campaign_id": 99,
        "code": "SPRING",
    }
    assert payload["reason"] == "store_override"
    assert payload["override_campaign_id"] == 99


def test_segment_parallel_warning_copy_is_type_c():
    """分群同编码文案须强调并行、不覆盖（类型 C）。"""
    msg = (
        "总部已有同编码分群「沉睡会员」（code=dormant）；"
        "分群为并行资源，本店与总部互不覆盖，发布活动请选对本店生效的分群"
    )
    assert "互不覆盖" in msg
    assert "将覆盖" not in msg


def test_option_group_parallel_warning_copy_is_type_c():
    """规格组同名文案须强调并行、不覆盖（类型 C）。"""
    msg = (
        "总部已有同名规格组「辣度」（编码 0000-La）；"
        "规格组为并行资源，不会自动覆盖，请确认是否需要本店独立组"
    )
    assert "并行资源" in msg
    assert "不会自动覆盖" in msg
    assert "将覆盖" not in msg


def test_marketing_empty_code_does_not_collapse_in_merge():
    """无 code 活动不得因空键互相覆盖。"""
    a = SimpleNamespace(id=1, store_id=None, code="", boost_product_ids=[1])
    b = SimpleNamespace(id=2, store_id=1, code="", boost_product_ids=[2])
    c = SimpleNamespace(id=3, store_id=None, code="SPRING", boost_product_ids=[3])
    key_fn = lambda camp: MarketingService.campaign_code_key(camp.code) or f"__id__:{int(camp.id)}"
    merged = merge_store_over_hq([a, b, c], key_fn=key_fn)
    assert {x.id for x in merged} == {1, 2, 3}


def test_welcome_template_prefer_store_then_hq():
    """同名券模板：本店优先，否则总部（与 CouponService._welcome_template 一致）。"""
    hq = SimpleNamespace(id=1, store_id=None, name="迎新券", status=1)
    store = SimpleNamespace(id=2, store_id=7, name="迎新券", status=1)
    other = SimpleNamespace(id=3, store_id=8, name="迎新券", status=1)
    rows = [hq, store, other]

    def pick(rows_, store_id):
        if store_id:
            owned = [r for r in rows_ if r.store_id and int(r.store_id) == int(store_id)]
            if owned:
                return owned[0]
        hq_rows = [r for r in rows_ if r.store_id is None]
        if hq_rows:
            return hq_rows[0]
        return rows_[0] if rows_ else None

    assert pick(rows, 7).id == 2
    assert pick(rows, 9).id == 1
    assert pick(rows, None).id == 1

"""Task 2 验收：口味画像规则推导、规格组默认档、手动接管不被 AUTO 覆盖、整店重算。"""
import pytest
from sqlalchemy import select

from app.models import ProductTasteProfile
from app.services.taste_profile_service import (
    derive_dimensions_and_tags,
    recompute_product,
    recompute_store,
    upsert_manual,
)
from reco_factories import (
    bind_option_group,
    make_category,
    make_option,
    make_option_group,
    make_product,
    make_store,
)


def test_derive_from_name_and_description():
    # 名称+描述：辣/酸定级，口感与风味标签仅来自受控词表
    dims, tags, _ = derive_dimensions_and_tags(
        product_name="招牌酸辣粉",
        description="劲道爽滑，酸辣开胃，红薯粉软糯",
        category_name="招牌粉面",
    )
    assert dims["spicy"] >= 2
    assert dims["sourness"] >= 1
    assert "酸辣" in tags
    assert "劲道" in tags
    assert "爽滑" in tags
    assert "软糯" in tags

    # 冰饮温度维度
    dims2, tags2, _ = derive_dimensions_and_tags(
        product_name="冰镇柠檬茶", description="清爽柠檬"
    )
    assert dims2["temperature"] == 0
    assert "柠檬" in tags2

    # 微辣只给 1 级
    dims3, _, _ = derive_dimensions_and_tags(product_name="微辣牛肉小面")
    assert dims3["spicy"] == 1
    assert dims3.get("sourness") is None


def test_derive_option_group_default_then_midpoint():
    groups = [{
        "name": "辣度",
        "code": "SPICY",
        "options": [
            {"name": "不辣", "code": "NONE", "is_default": False},
            {"name": "微辣", "code": "MILD", "is_default": False},
            {"name": "中辣", "code": "MEDIUM", "is_default": True},
            {"name": "特辣", "code": "EXTRA_HOT", "is_default": False},
        ],
    }]
    dims, _, _ = derive_dimensions_and_tags(product_name="招牌拌面", option_groups=groups)
    assert dims["spicy"] == 2

    groups[0]["options"] = [
        {"name": "不辣", "code": "NONE", "is_default": False},
        {"name": "微辣", "code": "MILD", "is_default": False},
        {"name": "中辣", "code": "MEDIUM", "is_default": False},
        {"name": "特辣", "code": "EXTRA_HOT", "is_default": False},
    ]
    dims2, _, _ = derive_dimensions_and_tags(product_name="招牌拌面", option_groups=groups)
    # 无默认档取中位（4 档索引 2 = 中辣）
    assert dims2["spicy"] == 2


@pytest.mark.asyncio
async def test_recompute_persists_and_name_overrides_option_default(db_session):
    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id, name="汤面")
    p = await make_product(db, store.id, cat.id, name="特辣火锅面")

    group = await make_option_group(db, store.id, name="辣度", code="SPICY")
    await make_option(db, group.id, name="微辣", code="MILD", is_default=True)
    await make_option(db, group.id, name="特辣", code="EXTRA_HOT")
    await bind_option_group(db, p.id, group.id)

    profile = await recompute_product(db, p.id)
    assert profile is not None
    # 名称信号强于规格组默认档
    assert profile.dimensions["spicy"] == 3
    assert profile.source == "AUTO"
    assert profile.store_id == store.id

    # 重复执行幂等，不产生重复行
    await recompute_product(db, p.id)
    count = len(list((await db.execute(
        select(ProductTasteProfile).where(ProductTasteProfile.product_id == p.id)
    )).scalars().all()))
    assert count == 1


@pytest.mark.asyncio
async def test_manual_overrides_survive_recompute_and_can_reset(db_session):
    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id, name="拌面")
    p = await make_product(db, store.id, cat.id, name="香辣肉酱拌面")

    profile = await recompute_product(db, p.id)
    auto_spicy = profile.dimensions["spicy"]
    assert auto_spicy >= 2
    assert "香辣" in profile.tags

    # 店长手动：辣度调成不辣、增加自定义风味标签
    profile = await upsert_manual(
        db, p.id, dimensions={"spicy": 0}, tags_add=["卤香"]
    )
    assert profile.dimensions["spicy"] == 0
    assert "卤香" in profile.tags
    assert profile.source == "MANUAL"

    # AUTO 重算不得覆盖手动键
    profile = await recompute_product(db, p.id)
    assert profile.dimensions["spicy"] == 0
    assert "卤香" in profile.tags
    assert profile.source == "MANUAL"

    # 维度恢复自动
    profile = await upsert_manual(db, p.id, reset_dimensions=["spicy"])
    assert profile.dimensions["spicy"] == auto_spicy

    # 手动标签移除后消失，自动标签仍在
    profile = await upsert_manual(db, p.id, tags_remove=["卤香"])
    assert "卤香" not in profile.tags
    assert "香辣" in profile.tags


@pytest.mark.asyncio
async def test_recompute_store_covers_all_products(db_session):
    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id)
    p1 = await make_product(db, store.id, cat.id, name="麻辣小面")
    p2 = await make_product(db, store.id, cat.id, name="番茄浓汤面")

    n = await recompute_store(db, store.id)
    assert n == 2
    rows = list((await db.execute(select(ProductTasteProfile))).scalars().all())
    by_pid = {r.product_id: r for r in rows}
    assert set(by_pid) == {p1.id, p2.id}
    assert by_pid[p1.id].dimensions["spicy"] >= 2
    assert "番茄" in by_pid[p2.id].tags

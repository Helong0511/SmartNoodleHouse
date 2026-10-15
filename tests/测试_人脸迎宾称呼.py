"""人脸迎宾占位符模板与称呼解析。

占位符：{称呼} {会员等级} {店名} {口味}（英文 name/level/store/taste）
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.face_service import WelcomeService


def test_build_welcome_text_user_template_example():
    """尊贵的 {称呼} {会员等级}，欢迎您再次光临{店名}"""
    text = WelcomeService.build_welcome_text(
        call_as="老张",
        level_name="金卡会员",
        store_name="肠旺面（总店）",
        greeting_template="尊贵的 {称呼} {会员等级}，欢迎您再次光临{店名}",
    )
    assert text == "尊贵的 老张 金卡会员，欢迎您再次光临肠旺面（总店）"


def test_build_welcome_text_legacy_prefix_and_preference_append():
    # 无占位符 = 旧前缀模式
    assert (
        WelcomeService.build_welcome_text(call_as="老张", greeting_template="欢迎回来")
        == "欢迎回来，老张。"
    )
    assert (
        WelcomeService.build_welcome_text(
            call_as="老张",
            greeting_template="欢迎回来，{称呼}。",
            preference_bits=["辣度·微辣", "宽细·细面"],
        )
        == "欢迎回来，老张。今天还是老样子：辣度·微辣、宽细·细面？"
    )


def test_build_welcome_text_taste_in_main_template():
    text = WelcomeService.build_welcome_text(
        call_as="老张",
        level_name="银卡会员",
        store_name="总店",
        greeting_template="{称呼}你好，还是要点{口味}吗？欢迎来到{店名}",
        preference_bits=["辣度·微辣"],
    )
    assert text == "老张你好，还是要点辣度·微辣吗？欢迎来到总店"
    # 主模板已含 {口味} 时不再追加附句
    assert "今天还是老样子" not in text


def test_build_welcome_text_empty_call_with_level_placeholder():
    text = WelcomeService.build_welcome_text(
        call_as="",
        level_name="金卡会员",
        store_name="肠旺面（总店）",
        greeting_template="尊贵的 {称呼} {会员等级}，欢迎您再次光临{店名}",
    )
    assert text == "尊贵的 金卡会员，欢迎您再次光临肠旺面（总店）"


def test_build_welcome_text_custom_preference_template():
    text = WelcomeService.build_welcome_text(
        call_as="老张",
        greeting_template="欢迎{称呼}",
        preference_template="您常点{口味}，要一样的吗？",
        preference_bits=["红油"],
    )
    assert text == "欢迎老张。您常点红油，要一样的吗？"


def test_build_welcome_text_product_placeholder_and_append():
    text = WelcomeService.build_welcome_text(
        call_as="老张",
        store_name="总店",
        greeting_template="{称呼}您好，欢迎光临{店名}。今天推荐{餐品}。",
        product_bits=["红烧牛肉面", "冒菜"],
    )
    assert text == "老张您好，欢迎光临总店。今天推荐红烧牛肉面、冒菜。"

    # 主模板无 {餐品} 且附句为空：不追加
    text2 = WelcomeService.build_welcome_text(
        call_as="老张",
        greeting_template="欢迎回来，{称呼}。",
        product_template="",
        product_bits=["红烧牛肉面"],
        preference_bits=[],
    )
    assert "红烧牛肉面" not in text2

    text3 = WelcomeService.build_welcome_text(
        call_as="老张",
        greeting_template="欢迎回来，{称呼}。",
        product_template="要不要来份{餐品}？",
        product_bits=["红烧牛肉面"],
        preference_bits=[],
    )
    assert text3 == "欢迎回来，老张。要不要来份红烧牛肉面？"


@pytest.mark.asyncio
async def test_resolve_welcome_identity_display_nick_level():
    db = AsyncMock()
    db.get = AsyncMock(return_value=SimpleNamespace(name="金卡会员"))

    c = SimpleNamespace(display_name="展示A", nickname="昵称B", member_level_id=2)
    call_as, level = await WelcomeService.resolve_welcome_identity(db, c)
    assert call_as == "展示A"
    assert level == "金卡会员"

    db.get = AsyncMock(return_value=SimpleNamespace(name="银卡会员"))
    c = SimpleNamespace(display_name="微信用户", nickname="支付宝用户", member_level_id=3)
    call_as, level = await WelcomeService.resolve_welcome_identity(db, c)
    assert call_as == ""
    assert level == "银卡会员"

    # 兼容单字段：无名 → 尊贵的等级
    assert await WelcomeService.resolve_welcome_name(db, c) == "尊贵的银卡会员"

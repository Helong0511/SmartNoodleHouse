"""后台顾客评价列表：鉴权形状、门店隔离、情感筛选。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.models import ProductFeedback
from app.services.feedback_service import FeedbackService
from reco_factories import (
    make_category,
    make_customer,
    make_paid_order,
    make_product,
    make_store,
)


@pytest.mark.asyncio
async def test_admin_list_product_feedbacks_shape():
    from app.api.deps import get_current_user, get_db
    from app.main import app

    role = SimpleNamespace(code="STORE_ADMIN")
    user = SimpleNamespace(id=1, role_id=2, store_id=1, role=role, status=1, token_version=0)
    fake_db = MagicMock()
    count_result = MagicMock(scalar=MagicMock(return_value=0))
    list_result = MagicMock(all=MagicMock(return_value=[]))
    fake_db.execute = AsyncMock(side_effect=[count_result, list_result])

    async def override_db():
        yield fake_db

    async def override_user():
        return user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        with patch("app.api.deps.get_user_permissions", AsyncMock(return_value={"order:read"})):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                res = await client.get("/api/product-feedbacks")
        assert res.status_code == 200
        body = res.json()
        assert body["code"] == 0
        assert body["data"]["items"] == []
        assert body["data"]["total"] == 0
        assert "verdict_codes" in body["data"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_feedback_list_store_scope_and_sentiment(db_session):
    db = db_session
    s1 = await make_store(db, store_no="FB01")
    s2 = await make_store(db, store_no="FB02")
    cat1 = await make_category(db, s1.id)
    cat2 = await make_category(db, s2.id)
    p1 = await make_product(db, s1.id, cat1.id, name="红油面")
    p2 = await make_product(db, s2.id, cat2.id, name="清汤面")
    c = await make_customer(db)
    o1 = await make_paid_order(db, c.id, s1.id, [(p1, 1, [])])
    o2 = await make_paid_order(db, c.id, s2.id, [(p2, 1, [])])

    await FeedbackService.submit_order_feedback(
        db, o1, [{"product_id": p1.id, "verdicts": ["TASTY"]}]
    )
    await FeedbackService.submit_order_feedback(
        db, o2, [{"product_id": p2.id, "verdicts": ["DISLIKE"]}]
    )
    await db.commit()

    rows_s1 = list(
        (
            await db.execute(
                select(ProductFeedback).where(ProductFeedback.store_id == s1.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows_s1) == 1
    assert rows_s1[0].sentiment == "POSITIVE"

    neg = list(
        (
            await db.execute(
                select(ProductFeedback).where(ProductFeedback.sentiment == "NEGATIVE")
            )
        )
        .scalars()
        .all()
    )
    assert len(neg) == 1
    assert neg[0].product_id == p2.id

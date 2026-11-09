"""支付后推荐学习事件必须独立重试，不能被偏好更新吞掉。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.paid_side_effects import (
    _handle_pref_update,
    _handle_taste_learning,
    enqueue_paid_side_effects,
)


@pytest.mark.asyncio
async def test_paid_side_effects_enqueue_taste_learning_as_independent_event():
    db = SimpleNamespace()
    order = SimpleNamespace(id=901)

    with patch(
        "app.services.paid_side_effects.enqueue_outbox",
        new_callable=AsyncMock,
    ) as enqueue:
        await enqueue_paid_side_effects(db, order)

    event_kinds = [
        call.kwargs["event_kind"] for call in enqueue.await_args_list
    ]
    assert event_kinds == [
        "points_award",
        "coupon_mark_used",
        "pref_update",
        "taste_learning",
        "milestone_coupon",
        "social_benefit_confirm",
        "campaign_attribution",
    ]


@pytest.mark.asyncio
async def test_preference_update_does_not_execute_recommendation_learning():
    order = SimpleNamespace(id=902, customer_id=33)
    db = SimpleNamespace(get=AsyncMock(return_value=order))
    row = SimpleNamespace(aggregate_id=order.id)

    with (
        patch(
            "app.services.customer_service.PreferenceService.update_from_order",
            new_callable=AsyncMock,
        ) as update_preferences,
        patch(
            "app.services.taste_learning_service.TasteLearningService.record_paid_order_events",
            new_callable=AsyncMock,
        ) as learn,
    ):
        await _handle_pref_update(db, row)

    update_preferences.assert_awaited_once_with(db, order.id)
    learn.assert_not_awaited()


@pytest.mark.asyncio
async def test_taste_learning_failure_propagates_for_outbox_retry():
    order = SimpleNamespace(id=903, customer_id=34)
    db = SimpleNamespace(get=AsyncMock(return_value=order))
    row = SimpleNamespace(aggregate_id=order.id)

    with patch(
        "app.services.taste_learning_service.TasteLearningService.record_paid_order_events",
        new_callable=AsyncMock,
        side_effect=RuntimeError("learning store temporarily unavailable"),
    ) as learn:
        with pytest.raises(RuntimeError, match="learning store temporarily unavailable"):
            await _handle_taste_learning(db, row)

    learn.assert_awaited_once_with(db, order.id)

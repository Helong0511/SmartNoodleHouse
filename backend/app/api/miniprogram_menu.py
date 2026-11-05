"""小程序菜单/推荐/行为事件/评价：从 miniprogram 路由拆出，路径仍挂在 /api/miniprogram。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.api.deps import CurrentCustomer, DbSession, OptionalCustomer
from app.core.cache_keys import cart_fingerprint
from app.core.exceptions import AppException
from app.core.response import money, success
from app.models import Customer, Order
from app.schemas.reco import BehaviorEventBatch, FeedbackBatchIn
from app.services.ab_service import DEFAULT_EXPERIMENT_KEY, AbService
from app.services.behavior_event_service import MAX_BATCH, ingest_events
from app.services.feedback_service import EVALUABLE_PAYMENT_STATUSES, FeedbackService
from app.services.reco_cache import (
    get_cached_reco,
    release_reco_lock,
    set_cached_reco,
    try_acquire_reco_lock,
    wait_for_reco,
)
from app.services.recommendation_service import RecommendationService

router = APIRouter(prefix="/miniprogram", tags=["小程序"])

@router.post("/events")
async def submit_behavior_events(
    data: BehaviorEventBatch,
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    """顾客隐式行为批量上报（≤50 条）：event_id 幂等，关闭个性化时直接丢弃。"""
    if len(data.events) > MAX_BATCH:
        raise AppException(40001, f"单批最多 {MAX_BATCH} 条事件")
    result = await ingest_events(db, customer, [e.model_dump() for e in data.events])
    return success(result, request=request)


@router.get("/menu")
async def get_menu_bundle_api(
    request: Request,
    db: DbSession,
    store_id: int,
    version: int | None = None,
    customer: OptionalCustomer = None,
):
    """菜单聚合（分类 + 在售餐品一次成型），带版本戳的轻量校验。

    先经 get_or_build 校验当前分类时段方案；跨时段会 bump version。
    客户端 version 与当前一致时返回 changed:false；否则返回完整 bundle。
    Redis 缓存协调不可用且无本地可用缓存时返回可重试 503，避免并发回源。
    限时/生日菜在返回前按今日与顾客生日过滤。
    """
    if not store_id:
        raise AppException(40001, "store_id 必填")
    sid = int(store_id)

    from app.models import Store
    from app.core.menu_cache import get_menu_version
    from app.services.menu_service import apply_menu_visibility, get_or_build_menu_bundle
    from app.utils.birthday import is_birthday_today, normalize_calendar

    store = await db.get(Store, sid)
    if not store or int(getattr(store, "status", 0) or 0) != 1:
        raise AppException(40011, "门店不存在或已停业")

    # 先组装/校验当前时段：跨时段时会 bump version，避免客户端凭旧 version 一直 changed:false
    bundle = await get_or_build_menu_bundle(db, sid)
    current_version = int(bundle.get("version") or await get_menu_version(sid) or 0)
    from app.utils.birthday import today_beijing
    from app.utils.product_visibility import VIS_ALWAYS, normalize_visibility_rule

    visibility_day = today_beijing().isoformat()
    # 限时/年节/生日：可见集合随「日期」或「顾客」变化，不能仅凭 menu version 短路，
    # 否则跨日仍 changed:false 会让客户端一直展示昨日菜单（如教师节隔天仍可见）。
    has_dynamic_visibility = any(
        normalize_visibility_rule((it or {}).get("visibility_rule")) != VIS_ALWAYS
        for it in ((bundle.get("products") or {}).get("items") or [])
    )
    if (
        version is not None
        and current_version
        and int(version) == int(current_version)
        and not has_dynamic_visibility
    ):
        return success(
            {
                "changed": False,
                "version": current_version,
                "visibility_day": visibility_day,
            },
            request=request,
        )

    is_bday = False
    if customer is not None:
        is_bday = is_birthday_today(
            customer.birthday,
            normalize_calendar(getattr(customer, "birthday_calendar", None)),
        )
    visible = apply_menu_visibility(bundle, is_birthday_today=is_bday)

    return success(
        {
            "changed": True,
            "version": current_version,
            "visibility_day": visibility_day,
            "categories": visible["categories"],
            "products": visible["products"],
            "is_birthday_today": is_bday,
        },
        request=request,
    )

_RECO_SCENES = {"recommend_menu", "settle_addon"}


def _serialize_reco(ranked, state, meta, ab_bucket, ab_status=None):
    items = []
    for r in ranked:
        p = r.product
        badges = []
        if p.is_hot:
            badges.append("hot")
        if r.scores.get("novel", 0) >= 0.6:
            badges.append("new")
        if p.is_recommended:
            badges.append("staff")
        items.append(
            {
                "id": p.id,
                "name": p.name,
                "price": str(money(p.price)),
                "main_image": p.main_image,
                "category_id": p.category_id,
                "category_name": p.category_name,
                "taste_tags": list(p.tags),
                "badges": badges,
                "reason": r.reason,
            }
        )
    return {
        "state": state,
        "items": items,
        "meta": meta,
        "ab": {"experiment": DEFAULT_EXPERIMENT_KEY, "bucket": ab_bucket, "status": ab_status},
    }


@router.get("/recommendations")
async def get_recommendations(
    request: Request,
    db: DbSession,
    customer: OptionalCustomer,
    store_id: int,
    scene: str = "recommend_menu",
    limit: int = 10,
    cart_item_ids: str | None = None,
):
    """智能推荐（未登录/关闭个性化走冷启动热销+新品）。结果 Redis 缓存 ≤120s。"""
    if not store_id:
        raise AppException(40001, "store_id 必填")
    if scene not in _RECO_SCENES:
        raise AppException(40001, "非法推荐场景")
    limit = max(1, min(int(limit or 10), 20))

    exclude_ids = []
    for raw in (cart_item_ids or "").split(","):
        raw = raw.strip()
        if raw.isdigit():
            exclude_ids.append(int(raw))

    ab_bucket = "v1"
    ab_status = None
    weight_override = None
    fingerprint = cart_fingerprint(exclude_ids) if scene == "settle_addon" else ""
    cid = customer.id if customer else None

    if cid is not None:
        ab_bucket, ab_params, ab_exp = await AbService.assign(db, cid, DEFAULT_EXPERIMENT_KEY)
        if ab_exp is not None:
            ab_status = ab_exp.status
        weight_override = (ab_params or {}).get("reco_weights")

    cached = await get_cached_reco(cid, store_id, scene, ab_bucket, fingerprint)
    if cached is not None:
        return success(cached, request=request)

    # Redis 锁负责跨进程协调；本地 single-flight 在 Redis 故障时限制同 worker 并发重算。
    from app.core.async_utils import local_singleflight

    flight_key = f"reco:{cid or 0}:{store_id}:{scene}:{ab_bucket}:{fingerprint}"
    async with local_singleflight(flight_key):
        # 等待本地前一个请求期间，先重新检查 Redis 缓存，避免重复计算。
        cached_after_wait = await get_cached_reco(cid, store_id, scene, ab_bucket, fingerprint)
        if cached_after_wait is not None:
            return success(cached_after_wait, request=request)

        lock_token = await try_acquire_reco_lock(cid, store_id, scene, ab_bucket, fingerprint)
        lock_ok = lock_token is not None
        if not lock_ok:
            waited = await wait_for_reco(cid, store_id, scene, ab_bucket, fingerprint, 2.0)
            if waited is not None:
                return success(waited, request=request)
            # Redis 锁竞争/不可用且短缓存未命中时，不允许所有 worker 同时重算。
            # 返回可重试错误，保护推荐查询背后的订单/行为画像查询链路。
            raise AppException(
                50301,
                "推荐服务正在刷新或缓存协调服务暂不可用，请稍后重试",
                status_code=503,
            )
        try:
            ranked, state, meta = await RecommendationService.recommend(
                db,
                store_id,
                customer_id=cid,
                scene=scene,
                limit=limit,
                exclude_ids=exclude_ids,
                weight_override=weight_override,
            )
            payload = _serialize_reco(ranked, state, meta, ab_bucket, ab_status)
            await set_cached_reco(cid, store_id, scene, ab_bucket, payload, fingerprint)
            payload["cache_hit"] = False
        finally:
            if lock_ok:
                await release_reco_lock(
                    cid, store_id, scene, ab_bucket, fingerprint, token=lock_token
                )
    return success(payload, request=request)


async def _load_owned_order(db, order_id: int, customer: Customer) -> Order:
    order = await db.get(Order, int(order_id))
    if not order:
        raise AppException(40404, "订单不存在")
    if int(order.customer_id or 0) != int(customer.id):
        raise AppException(40301, "无权操作该订单", status_code=403)
    return order


@router.get("/orders/{order_id}/feedback")
async def get_order_feedback(
    order_id: int,
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    """回显订单可评单品与已提交评价（轻量评价条渲染/置灰用）。"""
    order = await _load_owned_order(db, order_id, customer)
    return success(await FeedbackService.order_feedback_payload(db, order), request=request)


@router.post("/orders/{order_id}/feedback")
async def submit_order_feedback(
    order_id: int,
    data: FeedbackBatchIn,
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    """提交订单单品轻量评价：仅本人已支付订单；每单品一次，重复提交幂等。"""
    order = await _load_owned_order(db, order_id, customer)
    if (order.payment_status or "") not in EVALUABLE_PAYMENT_STATUSES:
        raise AppException(40061, "订单未支付，暂不能评价")
    try:
        result = await FeedbackService.submit_order_feedback(
            db, order, [it.model_dump() for it in data.items]
        )
    except ValueError as e:
        raise AppException(40060, str(e)) from e
    return success(result, request=request)

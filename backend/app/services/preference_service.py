"""智能口味：按顾客近期已付订单重算规格偏好。"""
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import now_local
from app.models import Customer, CustomerPreference, Order, OrderItem, OrderItemOption


class PreferenceService:
    """智能口味：根据已付订单持续分析并修正偏好。

    每次支付成功（或退款后）按顾客近期已付订单全量重算：
    - 按行数量加权（买 2 份加料计 2 次）
    - 近期订单加权更高（口味改变时自动修正）
    - 午餐/晚餐时段、周末轻微加权；部分退款弱负反馈降权
    - 全额退款订单不再参与分析
    """

    HIGH_PREFERENCE_THRESHOLD = Decimal("0.25")
    MIN_ORDER_COUNT = 2
    # 迎宾 TTS 等场景自行 slice；口味爱好应用须返回全部分项（7 组 + 多项加料可 >8）
    TOP_N = 8
    # 偏好分母：已支付订单（全额退款后 payment_status=REFUNDED 不计入）
    PREFERENCE_SCORE_PAYMENT_STATUSES = ("PAID", "PARTIAL_REFUND")
    # 近期加权：越新权重越高；分析最近 N 单
    RECENCY_DECAY = Decimal("0.85")
    RECENCY_WINDOW = 40
    # 时段场景：午餐/晚餐略加权
    MEAL_HOUR_BOOST = Decimal("1.15")
    # 周末订单轻微加权（休闲场景口味）
    WEEKEND_BOOST = Decimal("1.08")
    # 部分退款：弱负反馈，降低该单规格权重
    PARTIAL_REFUND_WEIGHT = Decimal("0.5")

    @staticmethod
    def _score_quantize(value: Decimal) -> Decimal:
        return min(Decimal("1"), max(Decimal("0"), value.quantize(Decimal("0.0001"))))

    @staticmethod
    def _order_weight(order, *, idx: int, decay: Decimal) -> Decimal:
        weight = (decay ** idx) if decay > 0 else Decimal("1")
        when = order.paid_at or order.created_at
        try:
            hour = int(when.hour) if when is not None else -1
        except Exception:
            hour = -1
        if hour in range(11, 15) or hour in range(17, 22):
            weight *= PreferenceService.MEAL_HOUR_BOOST
        try:
            wd = int(when.weekday()) if when is not None else -1
        except Exception:
            wd = -1
        if wd >= 5:
            weight *= PreferenceService.WEEKEND_BOOST
        if getattr(order, "payment_status", None) == "PARTIAL_REFUND":
            weight *= PreferenceService.PARTIAL_REFUND_WEIGHT
        return weight

    @staticmethod
    async def update_from_order(db: AsyncSession, order_id: int) -> None:
        order = await db.get(Order, order_id)
        # 仅在支付成功当下触发（order_lifecycle 在 target=PAID 时调用）
        if not order or not order.customer_id or order.status != "PAID":
            return
        await PreferenceService.recompute_for_customer(db, order.customer_id)
        customer = await db.get(Customer, order.customer_id)
        if customer:
            customer.last_order_at = now_local()

    @staticmethod
    async def recompute_for_customer(db: AsyncSession, customer_id: int) -> None:
        """根据顾客近期已付订单重算全部口味偏好（分析 + 修正）。"""
        if not customer_id:
            return

        orders = list(
            (
                await db.execute(
                    select(Order)
                    .where(
                        Order.customer_id == customer_id,
                        Order.payment_status.in_(PreferenceService.PREFERENCE_SCORE_PAYMENT_STATUSES),
                    )
                    .order_by(
                        func.coalesce(Order.paid_at, Order.created_at).desc(),
                        Order.id.desc(),
                    )
                    .limit(PreferenceService.RECENCY_WINDOW)
                )
            ).scalars().all()
        )
        if not orders:
            existing = (
                await db.execute(
                    select(CustomerPreference).where(CustomerPreference.customer_id == customer_id)
                )
            ).scalars().all()
            for pref in existing:
                await db.delete(pref)
            return

        order_ids = [o.id for o in orders]
        items = (
            await db.execute(select(OrderItem).where(OrderItem.order_id.in_(order_ids)))
        ).scalars().all()
        item_ids = [i.id for i in items]
        opts_by_item: dict[int, list] = {}
        if item_ids:
            opts = (
                await db.execute(select(OrderItemOption).where(OrderItemOption.order_item_id.in_(item_ids)))
            ).scalars().all()
            for opt in opts:
                opts_by_item.setdefault(opt.order_item_id, []).append(opt)

        items_by_order: dict[int, list] = {}
        for it in items:
            items_by_order.setdefault(it.order_id, []).append(it)

        agg: dict[tuple[str, str], dict] = {}
        total_weight = Decimal("0")
        decay = PreferenceService.RECENCY_DECAY

        for idx, order in enumerate(orders):
            weight = PreferenceService._order_weight(order, idx=idx, decay=decay)
            when = order.paid_at or order.created_at
            order_last = when or now_local()
            for it in items_by_order.get(order.id, []):
                qty = max(1, int(getattr(it, "quantity", None) or 1))
                line_w = weight * Decimal(qty)
                for opt in opts_by_item.get(it.id, []):
                    category = str(opt.group_name_snapshot or "").strip() or "规格"
                    code = str(opt.option_code or "").strip()
                    if not code:
                        continue
                    total_weight += line_w
                    key = (category, code)
                    name = opt.option_name_snapshot or code
                    row = agg.get(key)
                    if not row:
                        agg[key] = {
                            "category": category,
                            "option_code": code,
                            "option_name": name,
                            "order_count": qty,
                            "weighted": line_w,
                            "last_used_at": order_last,
                        }
                    else:
                        row["order_count"] += qty
                        row["weighted"] += line_w
                        if name:
                            row["option_name"] = name
                        if row["last_used_at"] is None or (
                            order_last and order_last > row["last_used_at"]
                        ):
                            row["last_used_at"] = order_last

        if total_weight <= 0:
            total_weight = Decimal("1")

        existing = {
            (p.category, p.option_code): p
            for p in (
                await db.execute(
                    select(CustomerPreference).where(CustomerPreference.customer_id == customer_id)
                )
            ).scalars().all()
        }

        keep_keys: set[tuple[str, str]] = set()
        for key, data in agg.items():
            keep_keys.add(key)
            score = PreferenceService._score_quantize(Decimal(data["weighted"]) / total_weight)
            pref = existing.get(key)
            if pref:
                pref.order_count = int(data["order_count"])
                pref.preference_score = score
                pref.option_name = data["option_name"]
                pref.last_used_at = data["last_used_at"] or now_local()
            else:
                db.add(
                    CustomerPreference(
                        customer_id=customer_id,
                        category=data["category"],
                        option_code=data["option_code"],
                        option_name=data["option_name"],
                        order_count=int(data["order_count"]),
                        preference_score=score,
                        last_used_at=data["last_used_at"] or now_local(),
                    )
                )

        for key, pref in existing.items():
            if key not in keep_keys:
                await db.delete(pref)

    @staticmethod
    async def get_recommendations(
        db: AsyncSession,
        customer_id: int,
        *,
        store_id: int | None = None,
        apply_scene: bool = True,
    ) -> dict:
        prefs_result = await db.execute(
            select(CustomerPreference)
            .where(
                CustomerPreference.customer_id == customer_id,
                CustomerPreference.order_count >= PreferenceService.MIN_ORDER_COUNT,
            )
            .order_by(CustomerPreference.preference_score.desc(), CustomerPreference.order_count.desc())
        )
        prefs = list(prefs_result.scalars().all())
        # 若无达到阈值的，退回全部（首单也能出推荐）
        if not prefs:
            prefs = list(
                (
                    await db.execute(
                        select(CustomerPreference)
                        .where(CustomerPreference.customer_id == customer_id)
                        .order_by(CustomerPreference.preference_score.desc())
                    )
                ).scalars().all()
            )
        high = [p for p in prefs if p.preference_score >= PreferenceService.HIGH_PREFERENCE_THRESHOLD]
        use = high or prefs

        scene: dict = {}
        scene_on = False
        if apply_scene:
            try:
                from app.services.system_settings_service import SystemSettingsService

                scene_on = await SystemSettingsService.effective_bool(
                    db, "preference_scene_enabled", True, store_id=store_id
                )
            except Exception:
                scene_on = True
        if scene_on:
            try:
                from app.services.preference_scene import apply_scene_multiplier, build_scene_context

                scene = await build_scene_context(db, store_id)
            except Exception:
                scene = {}
                scene_on = False

        items: list[dict] = []
        for p in use:
            base = float(p.preference_score)
            score = base
            if scene_on and scene:
                from app.services.preference_scene import apply_scene_multiplier

                score = apply_scene_multiplier(
                    base,
                    option_code=str(p.option_code or ""),
                    last_used_at=getattr(p, "last_used_at", None),
                    season=str(scene.get("season") or "spring"),
                    weather_condition=scene.get("condition"),
                )
            items.append(
                {
                    "category": p.category,
                    "option_code": p.option_code,
                    "option_name": p.option_name or p.option_code,
                    "score": score,
                    "base_score": base,
                    "order_count": p.order_count,
                    "label": f"{p.category}·{(p.option_name or p.option_code)}"
                    if p.category
                    else str(p.option_name or p.option_code),
                }
            )
        items.sort(key=lambda x: (-float(x["score"]), -int(x.get("order_count") or 0)))
        return {
            "preferences": items,
            "scene": {
                "season": scene.get("season"),
                "condition": scene.get("condition"),
                "temp_c": scene.get("temp_c"),
                "enabled": scene_on,
            }
            if scene_on
            else {"enabled": False},
        }

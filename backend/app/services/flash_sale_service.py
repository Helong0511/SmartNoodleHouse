"""限量免费抢购（射洪霸王面）：顾客端活动列表、抢购资格、我的资格。

约束：
- 活动资格 = 活动 RUNNING + 位于排期窗口 + 门店参与（总部活动默认全店，可排除）
- 库存扣减必须使用条件 UPDATE（remaining_quantity >= 1），禁止先查后减
- 每人限购：顾客行锁串行化 + unit_no 唯一键兜底
- 时间基准统一北京时间 naive（naive_local_now）
"""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.biz_no import BIZ_OT, BIZ_PF, TABLE_TAKEAWAY, generate_no
from app.core.exceptions import AppException
from app.core.response import naive_local_now
from app.models import (
    Customer,
    FlashSaleClaim,
    FlashSaleConfig,
    FlashSaleStock,
    MarketingCampaign,
    Order,
    OrderItem,
    Payment,
    Product,
    Store,
)
from app.services.marketing_service import MarketingService
from app.services.stock_service import deduct_stock
from app.utils.campaign_schedule import campaign_in_schedule_window

logger = logging.getLogger(__name__)

STATUS_CLAIMED = "CLAIMED"
STATUS_USED = "USED"
STATUS_EXPIRED = "EXPIRED"
STATUS_CANCELLED = "CANCELLED"

# 已消耗库存的资格状态（含超时失效：名额不返还）
_CONSUMED_STATUSES = (STATUS_CLAIMED, STATUS_USED, STATUS_EXPIRED)


class FlashSaleService:
    @staticmethod
    def _dt(value) -> str | None:
        return value.isoformat(sep=" ", timespec="seconds") if value else None

    @staticmethod
    def _stock_product_id(stock: FlashSaleStock | None, cfg: FlashSaleConfig | None) -> int | None:
        """门店抢购餐品：门店库存行优先，其次活动配置（仅单店活动可安全回退）。"""
        if stock is not None and stock.product_id:
            return int(stock.product_id)
        if cfg is not None and cfg.product_id:
            return int(cfg.product_id)
        return None

    @classmethod
    async def _require_store(cls, db: AsyncSession, store_id: int) -> Store:
        store = await db.get(Store, int(store_id))
        if not store or int(getattr(store, "status", 0) or 0) != 1:
            raise AppException(40404, "门店不存在或已停用")
        return store

    @classmethod
    async def _visible_campaigns(cls, db: AsyncSession, *, store_id: int) -> list[MarketingCampaign]:
        """本店可抢购的活动：RUNNING + 排期窗口内 + 门店参与 + 存在抢购配置。"""
        now = naive_local_now()
        rows = list(
            (
                await db.execute(
                    select(MarketingCampaign)
                    .where(MarketingCampaign.status == "RUNNING")
                    .order_by(MarketingCampaign.id.desc())
                )
            )
            .scalars()
            .all()
        )
        rows = await MarketingService.filter_campaigns_for_store(
            db, rows, store_id=int(store_id)
        )
        rows = [c for c in rows if campaign_in_schedule_window(c, now=now)]
        if not rows:
            return []
        cfg_ids = set(
            (
                await db.execute(
                    select(FlashSaleConfig.campaign_id).where(
                        FlashSaleConfig.campaign_id.in_([int(c.id) for c in rows])
                    )
                )
            )
            .scalars()
            .all()
        )
        return [c for c in rows if int(c.id) in {int(x) for x in cfg_ids}]

    @classmethod
    async def _stock_map(
        cls, db: AsyncSession, *, campaign_ids: list[int], store_id: int
    ) -> dict[int, FlashSaleStock]:
        if not campaign_ids:
            return {}
        rows = (
            await db.execute(
                select(FlashSaleStock).where(
                    FlashSaleStock.campaign_id.in_(campaign_ids),
                    FlashSaleStock.store_id == int(store_id),
                )
            )
        ).scalars().all()
        return {int(r.campaign_id): r for r in rows}

    @classmethod
    async def _cfg_map(
        cls, db: AsyncSession, *, campaign_ids: list[int]
    ) -> dict[int, FlashSaleConfig]:
        if not campaign_ids:
            return {}
        rows = (
            await db.execute(
                select(FlashSaleConfig).where(FlashSaleConfig.campaign_id.in_(campaign_ids))
            )
        ).scalars().all()
        return {int(r.campaign_id): r for r in rows}

    @classmethod
    async def list_active(
        cls, db: AsyncSession, *, store_id: int, customer_id: int | None = None
    ) -> list[dict]:
        store = await cls._require_store(db, store_id)
        sid = int(store.id)
        if not await MarketingService.is_enabled(db, sid):
            return []
        camps = await cls._visible_campaigns(db, store_id=sid)
        if not camps:
            return []
        camp_ids = [int(c.id) for c in camps]
        cfgs = await cls._cfg_map(db, campaign_ids=camp_ids)
        stocks = await cls._stock_map(db, campaign_ids=camp_ids, store_id=sid)
        product_ids: set[int] = set()
        for camp in camps:
            pid = cls._stock_product_id(stocks.get(int(camp.id)), cfgs.get(int(camp.id)))
            if pid:
                product_ids.add(pid)
        products: dict[int, Product] = {}
        if product_ids:
            for p in (
                await db.execute(select(Product).where(Product.id.in_(product_ids)))
            ).scalars().all():
                products[int(p.id)] = p

        my_claims: dict[int, FlashSaleClaim] = {}
        counts: dict[int, int] = {}
        if customer_id:
            claims = (
                await db.execute(
                    select(FlashSaleClaim).where(
                        FlashSaleClaim.campaign_id.in_(camp_ids),
                        FlashSaleClaim.customer_id == int(customer_id),
                        FlashSaleClaim.status.in_(_CONSUMED_STATUSES),
                    )
                )
            ).scalars().all()
            for claim in claims:
                cid = int(claim.campaign_id)
                counts[cid] = counts.get(cid, 0) + 1
                if claim.status == STATUS_CLAIMED:
                    my_claims[cid] = claim

        out: list[dict] = []
        for camp in camps:
            cfg = cfgs.get(int(camp.id))
            if cfg is None:
                continue
            stock = stocks.get(int(camp.id))
            remaining = int(stock.remaining_quantity) if stock else 0
            total = int(stock.total_quantity) if stock else 0
            product_id = cls._stock_product_id(stock, cfg)
            product = products.get(product_id) if product_id else None
            # 门店未配置可售餐品时不展示，避免顾客点击后必然失败
            if product is None or int(product.store_id) != sid:
                continue
            held = my_claims.get(int(camp.id))
            out.append(
                {
                    "campaign_id": int(camp.id),
                    "code": camp.code,
                    "name": camp.name,
                    "campaign_type": camp.campaign_type,
                    "notice": cfg.notice,
                    "product_id": product_id,
                    "product_name": product.name,
                    "product_price": str(product.price),
                    "total_quantity": total,
                    "remaining_quantity": remaining,
                    "sold_out": remaining <= 0,
                    "per_customer_limit": max(1, int(cfg.per_customer_limit or 1)),
                    "claim_valid_minutes": max(1, int(cfg.claim_valid_minutes or 30)),
                    "start_at": cls._dt(camp.start_at),
                    "end_at": cls._dt(camp.end_at),
                    "verify_deadline_at": cls._dt(cfg.verify_deadline_at),
                    "my_claimed_count": counts.get(int(camp.id), 0),
                    "my_claim": cls._claim_brief(held) if held else None,
                }
            )
        return out

    @staticmethod
    def _claim_brief(claim: FlashSaleClaim) -> dict:
        now = naive_local_now()
        status = claim.status
        if status == STATUS_CLAIMED and claim.expires_at and claim.expires_at <= now:
            status = STATUS_EXPIRED
        return {
            "claim_id": int(claim.id),
            "claim_no": claim.claim_no,
            "status": status,
            "store_id": int(claim.store_id),
            "claimed_at": FlashSaleService._dt(claim.claimed_at),
            "expires_at": FlashSaleService._dt(claim.expires_at),
            "verify_deadline_at": FlashSaleService._dt(claim.verify_deadline_at),
        }

    @classmethod
    def _resolve_expiry(cls, cfg: FlashSaleConfig, camp: MarketingCampaign, now):
        """资格有效截止：取「现在 + 有效分钟」「配置绝对截止」「活动结束」三者最早。"""
        expiry = now + timedelta(minutes=max(1, int(cfg.claim_valid_minutes or 30)))
        for deadline in (cfg.verify_deadline_at, camp.end_at):
            if deadline and deadline < expiry:
                expiry = deadline
        return expiry

    @classmethod
    async def _claim_payload(
        cls, db: AsyncSession, claim: FlashSaleClaim, *, already_claimed: bool = False
    ) -> dict:
        camp = await db.get(MarketingCampaign, int(claim.campaign_id))
        cfg = await db.scalar(
            select(FlashSaleConfig).where(FlashSaleConfig.campaign_id == int(claim.campaign_id))
        )
        stock = await db.scalar(
            select(FlashSaleStock).where(
                FlashSaleStock.campaign_id == int(claim.campaign_id),
                FlashSaleStock.store_id == int(claim.store_id),
            )
        )
        product_id = cls._stock_product_id(stock, cfg)
        product = await db.get(Product, product_id) if product_id else None
        data = cls._claim_brief(claim)
        data.update(
            {
                "campaign_id": int(claim.campaign_id),
                "campaign_name": camp.name if camp else None,
                "product_id": product_id,
                "product_name": product.name if product else None,
                "already_claimed": already_claimed,
            }
        )
        return data

    @classmethod
    async def claim(
        cls,
        db: AsyncSession,
        *,
        campaign_id: int,
        store_id: int,
        customer_id: int,
        client_request_id: str | None = None,
    ) -> dict:
        """抢购一份免费资格（原子扣库存 + 每人限购 + 幂等重放）。"""
        now = naive_local_now()
        store = await cls._require_store(db, store_id)
        sid = int(store.id)
        cid = int(customer_id)
        camp = await db.get(MarketingCampaign, int(campaign_id))
        if not camp:
            raise AppException(40404, "活动不存在")
        if not await MarketingService.is_enabled(db, sid):
            raise AppException(40008, "本店已关闭营销活动")
        if camp.status != "RUNNING" or not campaign_in_schedule_window(camp, now=now):
            raise AppException(40009, "活动未开始或已结束")
        if not await MarketingService.is_store_participating(db, camp, store_id=sid):
            raise AppException(40301, "本店不参与该活动")

        cfg = await db.scalar(
            select(FlashSaleConfig).where(FlashSaleConfig.campaign_id == int(camp.id))
        )
        if not cfg:
            raise AppException(40404, "该活动未配置抢购商品")
        stock_row = await db.scalar(
            select(FlashSaleStock).where(
                FlashSaleStock.campaign_id == int(camp.id),
                FlashSaleStock.store_id == sid,
            )
        )
        product_id = cls._stock_product_id(stock_row, cfg)
        product = await db.get(Product, product_id) if product_id else None
        if (
            product is None
            or int(product.store_id) != sid
            or (product.status or "").upper() != "ON_SALE"
        ):
            raise AppException(40011, "本店未配置可用的抢购餐品")

        # 顾客行锁：串行化同一顾客的并发抢购，保证限购计数与 unit_no 正确
        await db.execute(select(Customer.id).where(Customer.id == cid).with_for_update())

        request_id = (client_request_id or "").strip() or None
        if request_id:
            if (
                len(request_id) > 64
                or not request_id.isascii()
                or not all(ch.isalnum() or ch in "_-" for ch in request_id)
            ):
                raise AppException(40012, "抢购请求标识格式无效")
            replay = await db.scalar(
                select(FlashSaleClaim).where(FlashSaleClaim.client_request_id == request_id)
            )
            if replay is not None:
                # 全局唯一请求标识必须绑定顾客、活动和门店，禁止跨活动/门店静默复用旧资格。
                if int(replay.customer_id) != cid:
                    raise AppException(40301, "请求标识已被占用")
                if int(replay.campaign_id) != int(camp.id) or int(replay.store_id) != sid:
                    raise AppException(40909, "抢购请求标识与活动或门店不一致", status_code=409)
                return await cls._claim_payload(db, replay, already_claimed=True)

        held = (
            await db.execute(
                select(FlashSaleClaim)
                .where(
                    FlashSaleClaim.campaign_id == int(camp.id),
                    FlashSaleClaim.customer_id == cid,
                    FlashSaleClaim.status == STATUS_CLAIMED,
                )
                .order_by(FlashSaleClaim.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if held is not None:
            if held.expires_at and held.expires_at > now:
                return await cls._claim_payload(db, held, already_claimed=True)
            # 超时未核销：名额作废（不返还库存）
            held.status = STATUS_EXPIRED
            await db.flush()

        used = int(
            await db.scalar(
                select(func.count())
                .select_from(FlashSaleClaim)
                .where(
                    FlashSaleClaim.campaign_id == int(camp.id),
                    FlashSaleClaim.customer_id == cid,
                    FlashSaleClaim.status.in_(_CONSUMED_STATUSES),
                )
            )
            or 0
        )
        limit = max(1, int(cfg.per_customer_limit or 1))
        if used >= limit:
            raise AppException(40010, f"已达每人限购上限（{limit} 份）")
        max_unit = int(
            await db.scalar(
                select(func.coalesce(func.max(FlashSaleClaim.unit_no), 0)).where(
                    FlashSaleClaim.campaign_id == int(camp.id),
                    FlashSaleClaim.customer_id == cid,
                )
            )
            or 0
        )

        # 原子扣库存：条件 UPDATE，避免先查后减的超卖
        deducted = await db.execute(
            update(FlashSaleStock)
            .where(
                FlashSaleStock.campaign_id == int(camp.id),
                FlashSaleStock.store_id == sid,
                FlashSaleStock.remaining_quantity >= 1,
            )
            .values(remaining_quantity=FlashSaleStock.remaining_quantity - 1)
        )
        if not deducted.rowcount:
            raise AppException(40011, "本店名额已抢完")

        expiry = cls._resolve_expiry(cfg, camp, now)
        claim = FlashSaleClaim(
            campaign_id=int(camp.id),
            store_id=sid,
            customer_id=cid,
            unit_no=max_unit + 1,
            claim_no=generate_no("FS", store_no=getattr(store, "store_no", None)),
            status=STATUS_CLAIMED,
            claimed_at=now,
            expires_at=expiry,
            verify_deadline_at=cfg.verify_deadline_at,
            client_request_id=request_id,
        )
        db.add(claim)
        await db.flush()
        logger.info(
            "抢购成功 campaign=%s store=%s customer=%s claim=%s",
            int(camp.id),
            sid,
            cid,
            claim.claim_no,
        )
        return await cls._claim_payload(db, claim)

    @classmethod
    async def list_my_claims(
        cls, db: AsyncSession, *, customer_id: int, store_id: int | None = None
    ) -> list[dict]:
        """我的抢购资格（待核销 / 已核销 / 已失效）。"""
        q = select(FlashSaleClaim).where(FlashSaleClaim.customer_id == int(customer_id))
        if store_id is not None:
            q = q.where(FlashSaleClaim.store_id == int(store_id))
        rows = list(
            (
                await db.execute(q.order_by(FlashSaleClaim.id.desc()).limit(100))
            )
            .scalars()
            .all()
        )
        if not rows:
            return []
        camp_ids = {int(r.campaign_id) for r in rows}
        camps = {}
        if camp_ids:
            for c in (
                await db.execute(select(MarketingCampaign).where(MarketingCampaign.id.in_(camp_ids)))
            ).scalars().all():
                camps[int(c.id)] = c
        cfgs = {
            int(c.id): c
            for c in (
                await db.execute(select(FlashSaleConfig).where(FlashSaleConfig.campaign_id.in_(camp_ids)))
            ).scalars().all()
        } if camp_ids else {}
        stocks = {
            (int(s.campaign_id), int(s.store_id)): s
            for s in (
                await db.execute(
                    select(FlashSaleStock).where(
                        FlashSaleStock.campaign_id.in_(camp_ids),
                        FlashSaleStock.store_id.in_({int(r.store_id) for r in rows}),
                    )
                )
            ).scalars().all()
        } if camp_ids else {}
        resolved: dict[int, int | None] = {}
        for r in rows:
            resolved[int(r.id)] = cls._stock_product_id(
                stocks.get((int(r.campaign_id), int(r.store_id))),
                cfgs.get(int(r.campaign_id)),
            )
        product_ids = {pid for pid in resolved.values() if pid}
        products: dict[int, Product] = {}
        if product_ids:
            for p in (
                await db.execute(select(Product).where(Product.id.in_(product_ids)))
            ).scalars().all():
                products[int(p.id)] = p
        out: list[dict] = []
        for r in rows:
            data = cls._claim_brief(r)
            camp = camps.get(int(r.campaign_id))
            pid = resolved.get(int(r.id))
            product = products.get(pid) if pid else None
            data.update(
                {
                    "campaign_id": int(r.campaign_id),
                    "campaign_name": camp.name if camp else None,
                    "product_id": pid,
                    "product_name": product.name if product else None,
                    "order_id": int(r.order_id) if r.order_id else None,
                    "verified_at": cls._dt(r.verified_at),
                }
            )
            out.append(data)
        return out

    @classmethod
    async def verify(
        cls,
        db: AsyncSession,
        *,
        claim_no: str,
        store_id: int | None,
        operator_id: int | None,
    ) -> dict:
        """核销抢购资格并生成 0 元免单订单（PAID + FREE + non_revenue）。

        - 资格行锁 + 状态校验保证并发核销只生成一单
        - 已核销的重复请求返回原订单，不重复建单
        - 写入 0 元 FREE Payment 流水以保持支付进度一致；不调用第三方支付、不计入普通营业收入
        """
        from app.services.status_history import record_order_status

        code = (claim_no or "").strip().upper()
        if not code:
            raise AppException(40008, "请填写核销码")
        now = naive_local_now()
        claim = await db.scalar(
            select(FlashSaleClaim)
            .where(FlashSaleClaim.claim_no == code)
            .with_for_update()
        )
        if not claim:
            raise AppException(40404, "抢购资格不存在")
        # store_id 由后端门店范围决定（非超管强制本店），不接受前端指定他店
        if store_id is not None and int(claim.store_id) != int(store_id):
            raise AppException(40300, "无权核销其他门店的抢购资格", status_code=403)

        camp = await db.get(MarketingCampaign, int(claim.campaign_id))
        cfg = await db.scalar(
            select(FlashSaleConfig).where(FlashSaleConfig.campaign_id == int(claim.campaign_id))
        )
        stock = await db.scalar(
            select(FlashSaleStock).where(
                FlashSaleStock.campaign_id == int(claim.campaign_id),
                FlashSaleStock.store_id == int(claim.store_id),
            )
        )
        product_id = cls._stock_product_id(stock, cfg)
        product = await db.get(Product, product_id) if product_id else None
        if product is not None and int(product.store_id) != int(claim.store_id):
            product = None
        product_name = product.name if product else "免费餐品"

        if claim.status == STATUS_USED:
            existing = await db.get(Order, int(claim.order_id)) if claim.order_id else None
            return {
                "already_verified": True,
                "claim_id": int(claim.id),
                "claim_no": claim.claim_no,
                "status": claim.status,
                "store_id": int(claim.store_id),
                "customer_id": int(claim.customer_id),
                "product_id": product_id,
                "product_name": product_name,
                "order_id": int(claim.order_id) if claim.order_id else None,
                "order_no": existing.order_no if existing else None,
                "total_amount": "0.00",
                "verified_at": cls._dt(claim.verified_at),
            }
        if claim.status == STATUS_CANCELLED:
            raise AppException(40013, "该资格已取消")
        if claim.status != STATUS_CLAIMED:
            raise AppException(40012, "该资格已失效")
        if claim.expires_at and claim.expires_at <= now:
            # 核销时发现超时：落库失效（名额不返还），再返回失败
            claim.status = STATUS_EXPIRED
            await db.flush()
            await db.commit()
            raise AppException(40012, "该资格已过期")
        if camp is None or camp.status not in ("RUNNING", "PAUSED"):
            raise AppException(40009, "活动已结束，无法核销")
        if product is None:
            raise AppException(40011, "本店未配置可用的抢购餐品")

        store = await db.get(Store, int(claim.store_id))
        if not store or int(getattr(store, "status", 0) or 0) != 1:
            raise AppException(40404, "门店不存在或已停用")
        if cfg and cfg.verify_deadline_at and cfg.verify_deadline_at <= now:
            raise AppException(40012, "已超过活动核销截止时间")

        # 核销扣减本店常规餐品库存：库存不足时整体回滚，资格仍可重试
        await deduct_stock(db, int(product.id), 1, store_id=int(claim.store_id))

        order = Order(
            order_no=generate_no(
                BIZ_OT, getattr(store, "store_no", None), None, table_placeholder=TABLE_TAKEAWAY
            ),
            customer_id=int(claim.customer_id),
            store_id=int(claim.store_id),
            status="PAID",
            payment_status="PAID",
            payment_method="FREE",
            channel="POS",
            dining_type="TAKEAWAY",
            subtotal=Decimal("0.00"),
            discount_amount=Decimal("0.00"),
            total_amount=Decimal("0.00"),
            # 唯一幂等键兜底：同一资格不可能生成第二张订单
            idempotency_key=f"flash:{int(claim.id)}",
            operator_id=int(operator_id) if operator_id is not None else None,
            remark=f"射洪霸王面免费抢购核销 {claim.claim_no}",
            ordered_at=now,
            paid_at=now,
            non_revenue=True,
            marketing_campaign_id=int(claim.campaign_id),
            flash_claim_id=int(claim.id),
        )
        db.add(order)
        await db.flush()
        # 免费营销核销也必须有 PAID 的零元 Payment，保证支付进度、支付流水和订单状态一致。
        # provider=FREE + non_revenue 标记，不产生 SALE 财务收入，也不发放付费订单积分/券副作用。
        db.add(
            Payment(
                payment_no=generate_no(
                    BIZ_PF, getattr(store, "store_no", None), None,
                    table_placeholder=TABLE_TAKEAWAY,
                ),
                order_id=int(order.id),
                customer_id=int(claim.customer_id),
                operator_id=int(operator_id) if operator_id is not None else None,
                provider="FREE",
                channel="POS",
                amount=Decimal("0.00"),
                currency="CNY",
                status="PAID",
                paid_at=now,
                callback_raw={
                    "source": "FLASH_SALE",
                    "claim_id": int(claim.id),
                    "non_revenue": True,
                },
            )
        )
        db.add(
            OrderItem(
                order_id=int(order.id),
                product_id=int(product.id),
                product_name_snapshot=str(product.name)[:100],
                unit_price_snapshot=Decimal("0.00"),
                quantity=1,
                subtotal=Decimal("0.00"),
                remark="免费抢购",
            )
        )
        await record_order_status(
            db,
            order_id=int(order.id),
            from_status=None,
            to_status="PAID",
            source="FLASH_SALE",
            operator_id=int(operator_id) if operator_id is not None else None,
            remark=f"抢购资格 {claim.claim_no} 核销免单",
        )
        claim.status = STATUS_USED
        claim.order_id = int(order.id)
        claim.verified_at = now
        claim.verified_by = int(operator_id) if operator_id is not None else None
        await db.flush()
        logger.info(
            "抢购核销成功 claim=%s store=%s order=%s operator=%s",
            claim.claim_no,
            int(claim.store_id),
            order.order_no,
            operator_id,
        )
        return {
            "already_verified": False,
            "claim_id": int(claim.id),
            "claim_no": claim.claim_no,
            "status": claim.status,
            "store_id": int(claim.store_id),
            "customer_id": int(claim.customer_id),
            "product_id": int(product.id),
            "product_name": str(product.name),
            "order_id": int(order.id),
            "order_no": order.order_no,
            "total_amount": "0.00",
            "verified_at": cls._dt(claim.verified_at),
        }

    @classmethod
    async def announce_verified(
        cls,
        db: AsyncSession,
        *,
        store_id: int,
        customer_id: int,
        product_name: str,
    ) -> None:
        """核销提交后的异步语音播报；任何异常只记录日志，不影响已完成的核销。"""
        try:
            from app.models import Camera
            from app.services.face_service import WelcomeService

            camera_id = await db.scalar(
                select(Camera.id)
                .where(Camera.store_id == int(store_id), Camera.status == "ONLINE")
                .order_by(Camera.id)
                .limit(1)
            )
            text = f"恭喜您，{product_name}免费抢购核销成功，请到取餐口领取。"
            await WelcomeService.enqueue(
                {
                    "customer_id": int(customer_id) if customer_id else None,
                    "text": text,
                    "camera_id": int(camera_id) if camera_id else None,
                    "kind": "flash_sale_verify",
                    "target": "pc" if camera_id is None else "camera",
                }
            )
        except Exception:
            logger.exception(
                "抢购核销语音播报失败（不影响核销） store=%s customer=%s",
                store_id,
                customer_id,
            )

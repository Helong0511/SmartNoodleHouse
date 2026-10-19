"""同桌共享购物车（共享车）。

业务规则（立项锁定，勿随意改）：
1. 结算：一人结全桌——共享草稿由结算人一次性提交为正式订单。
2. 删改：成员只能改/删自己加的行；HOST（桌主/先扫码者）可清整车。
3. 冲突：party.revision 乐观锁，写操作携带 expected_revision，不匹配则 409。
4. 外带虚拟桌（WM 等）不建共享车。
5. 共享车仅为下单前草稿；已支付订单另聚合。
"""
from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.response import ensure_local_aware, now_local, serialize_dt
from app.models import (
    Customer,
    DiningTable,
    Product,
    SharedCartItem,
    TableParty,
    TablePartyMember,
)

# ---- 锁定规则常量 ----
SETTLEMENT_MODE = "ONE_PAYS_ALL"
DELETE_POLICY = "OWN_LINES_ONLY"
HOST_CAN_CLEAR = True
CONFLICT_STRATEGY = "OPTIMISTIC_REVISION"

PARTY_HOURS = 4
MAX_QTY = 999

_TAKEAWAY_NOS = frozenset({"WM", "TKW", "TAKEAWAY"})


def is_takeaway_table(table: DiningTable | None) -> bool:
    if not table:
        return True
    no = str(getattr(table, "table_no", "") or "").strip().upper()
    if no in _TAKEAWAY_NOS:
        return True
    name = str(getattr(table, "table_name", "") or "")
    return bool(re.search(r"外卖|外带自取", name))


def _money(v) -> Decimal:
    return Decimal(str(v or "0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _opt_key(option_ids) -> str:
    ids = sorted(int(x) for x in (option_ids or []) if x is not None)
    return ",".join(str(i) for i in ids)


async def _validate_product_options(
    db: AsyncSession,
    *,
    product_id: int,
    option_ids: list[int],
    store_id: int,
) -> Decimal:
    """服务端再校验必选规格；返回规格加价合计（不含餐品基价）。"""
    from app.models import Option, OptionGroup, ProductOptionGroup

    pog_rows = (
        await db.execute(
            select(ProductOptionGroup, OptionGroup)
            .join(OptionGroup, OptionGroup.id == ProductOptionGroup.group_id)
            .where(
                ProductOptionGroup.product_id == int(product_id),
                OptionGroup.status == 1,
            )
        )
    ).all()
    bound = {int(g.id): g for _, g in pog_rows}
    if not bound:
        return Decimal("0.00")

    opts = [int(x) for x in (option_ids or []) if x is not None]
    options_map = {}
    if opts:
        options_map = {
            int(o.id): o
            for o in (
                await db.execute(
                    select(Option).where(Option.id.in_(opts), Option.status == 1)
                )
            ).scalars()
        }

    selected_by_group: dict[int, list] = {}
    extra = Decimal("0.00")
    for oid in opts:
        opt = options_map.get(int(oid))
        if not opt:
            raise AppException(40004, f"规格选项无效: {oid}")
        group = bound.get(int(opt.group_id))
        if not group:
            raise AppException(40005, f"规格不属于该餐品: {oid}")
        if group.store_id is not None and int(group.store_id) != int(store_id):
            raise AppException(40002, f"规格组不属于本门店: {group.name}")
        selected_by_group.setdefault(int(group.id), []).append(opt)
        extra += _money(getattr(opt, "extra_price", 0))

    for gid, group in bound.items():
        selected = selected_by_group.get(int(gid), [])
        if group.is_required and not selected:
            raise AppException(40006, f"缺少必选规格组: {group.name}")
        if str(getattr(group, "selection_type", "") or "") == "SINGLE" and len(selected) > 1:
            raise AppException(40007, f"规格组 {group.name} 只能单选")
    return extra


class SharedCartService:
    @staticmethod
    def rules_payload() -> dict:
        return {
            "settlement_mode": SETTLEMENT_MODE,
            "delete_policy": DELETE_POLICY,
            "host_can_clear": HOST_CAN_CLEAR,
            "conflict_strategy": CONFLICT_STRATEGY,
        }

    @staticmethod
    async def expire_stale_parties(db: AsyncSession, *, store_id: int, table_id: int) -> None:
        now = now_local()
        await db.execute(
            update(TableParty)
            .where(
                TableParty.store_id == int(store_id),
                TableParty.table_id == int(table_id),
                TableParty.status == "OPEN",
                TableParty.expires_at <= now,
            )
            .values(status="CLOSED", closed_at=now)
        )

    @staticmethod
    async def get_open_party(
        db: AsyncSession, *, store_id: int, table_id: int
    ) -> TableParty | None:
        await SharedCartService.expire_stale_parties(db, store_id=store_id, table_id=table_id)
        now = now_local()
        return (
            await db.execute(
                select(TableParty)
                .where(
                    TableParty.store_id == int(store_id),
                    TableParty.table_id == int(table_id),
                    TableParty.status == "OPEN",
                    TableParty.expires_at > now,
                )
                .order_by(TableParty.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def join_party(
        db: AsyncSession,
        *,
        customer: Customer,
        table: DiningTable,
    ) -> dict | None:
        """扫码绑桌后加入/创建桌局。外带桌返回 None。"""
        if is_takeaway_table(table):
            return None
        if int(getattr(table, "status", 0) or 0) != 1:
            raise AppException(40404, "桌台不存在或已禁用")

        store_id = int(table.store_id)
        table_id = int(table.id)
        cid = int(customer.id)
        nick = (getattr(customer, "nickname", None) or getattr(customer, "display_name", None) or "").strip() or None

        party = await SharedCartService.get_open_party(db, store_id=store_id, table_id=table_id)
        now = now_local()
        expires = now + timedelta(hours=PARTY_HOURS)

        if not party:
            party = TableParty(
                store_id=store_id,
                table_id=table_id,
                host_customer_id=cid,
                status="OPEN",
                revision=1,
                expires_at=expires,
            )
            db.add(party)
            await db.flush()
            db.add(
                TablePartyMember(
                    party_id=int(party.id),
                    customer_id=cid,
                    role="HOST",
                    nickname_snap=nick,
                    joined_at=now,
                )
            )
            await db.flush()
            return await SharedCartService.cart_payload(db, party=party, viewer_id=cid)

        # 续期桌局
        party.expires_at = expires
        mem = (
            await db.execute(
                select(TablePartyMember).where(
                    TablePartyMember.party_id == int(party.id),
                    TablePartyMember.customer_id == cid,
                )
            )
        ).scalar_one_or_none()
        if mem:
            mem.left_at = None
            if nick:
                mem.nickname_snap = nick
            if int(party.host_customer_id) == cid:
                mem.role = "HOST"
        else:
            db.add(
                TablePartyMember(
                    party_id=int(party.id),
                    customer_id=cid,
                    role="HOST" if int(party.host_customer_id) == cid else "MEMBER",
                    nickname_snap=nick,
                    joined_at=now,
                )
            )
        await db.flush()
        return await SharedCartService.cart_payload(db, party=party, viewer_id=cid)

    @staticmethod
    async def _require_member(
        db: AsyncSession, *, party: TableParty, customer_id: int
    ) -> TablePartyMember:
        mem = (
            await db.execute(
                select(TablePartyMember).where(
                    TablePartyMember.party_id == int(party.id),
                    TablePartyMember.customer_id == int(customer_id),
                    TablePartyMember.left_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if not mem:
            raise AppException(40310, "请先扫码加入本桌后再操作共享车")
        return mem

    @staticmethod
    async def _lock_party(db: AsyncSession, party_id: int) -> TableParty:
        party = (
            await db.execute(
                select(TableParty).where(TableParty.id == int(party_id)).with_for_update()
            )
        ).scalar_one_or_none()
        if not party or party.status != "OPEN":
            raise AppException(40410, "本桌共享车已关闭，请重新扫码")
        expires = ensure_local_aware(party.expires_at)
        if expires and expires <= now_local():
            party.status = "CLOSED"
            party.closed_at = now_local()
            await db.flush()
            raise AppException(40410, "本桌共享车已过期，请重新扫码")
        return party

    @staticmethod
    def _check_revision(party: TableParty, expected_revision: int | None) -> None:
        if expected_revision is None:
            return
        if int(party.revision) != int(expected_revision):
            raise AppException(
                40920,
                "共享车已被同伴更新，请刷新后重试",
                status_code=409,
            )

    @staticmethod
    async def cart_payload(
        db: AsyncSession, *, party: TableParty, viewer_id: int
    ) -> dict:
        rows = list(
            (
                await db.execute(
                    select(SharedCartItem)
                    .where(SharedCartItem.party_id == int(party.id))
                    .order_by(SharedCartItem.id.asc())
                )
            )
            .scalars()
            .all()
        )
        members = list(
            (
                await db.execute(
                    select(TablePartyMember).where(
                        TablePartyMember.party_id == int(party.id),
                        TablePartyMember.left_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        nick_map = {int(m.customer_id): (m.nickname_snap or f"顾客{m.customer_id}") for m in members}
        is_host = int(party.host_customer_id) == int(viewer_id)
        items = []
        for r in rows:
            mine = int(r.added_by_customer_id) == int(viewer_id)
            qty = int(r.quantity or 1)
            unit = _money(r.unit_price)
            items.append(
                {
                    "id": int(r.id),
                    "shared_item_id": int(r.id),
                    "product_id": int(r.product_id),
                    "name": r.product_name,
                    "quantity": qty,
                    "unit_price": f"{unit:.2f}",
                    "subtotal": f"{(unit * qty).quantize(Decimal('0.01')):.2f}",
                    "option_ids": list(r.option_ids or []),
                    "option_text": r.option_text or "",
                    "option_labels": [
                        x for x in str(r.option_text or "").replace("，", "、").split("、") if x
                    ],
                    "remark": r.remark or "",
                    "category_id": int(r.category_id) if r.category_id else None,
                    "main_image": r.main_image or "",
                    "imageUrl": "",
                    "no_member_discount": bool(r.no_member_discount),
                    "added_by_customer_id": int(r.added_by_customer_id),
                    "added_by_label": nick_map.get(int(r.added_by_customer_id), "同伴"),
                    "mine": mine,
                    "can_edit": mine,
                }
            )
        return {
            "enabled": True,
            "party_id": int(party.id),
            "store_id": int(party.store_id),
            "table_id": int(party.table_id),
            "host_customer_id": int(party.host_customer_id),
            "is_host": is_host,
            "revision": int(party.revision),
            "status": party.status,
            "expires_at": serialize_dt(party.expires_at) if party.expires_at else None,
            "member_count": len(members),
            "items": items,
            "rules": SharedCartService.rules_payload(),
        }

    @staticmethod
    async def get_cart_for_customer(
        db: AsyncSession, *, customer: Customer, table: DiningTable
    ) -> dict:
        if is_takeaway_table(table):
            return {"enabled": False, "items": [], "rules": SharedCartService.rules_payload()}
        party = await SharedCartService.get_open_party(
            db, store_id=int(table.store_id), table_id=int(table.id)
        )
        if not party:
            # 自动加入（幂等建局）
            joined = await SharedCartService.join_party(db, customer=customer, table=table)
            return joined or {"enabled": False, "items": [], "rules": SharedCartService.rules_payload()}
        await SharedCartService._require_member(db, party=party, customer_id=int(customer.id))
        return await SharedCartService.cart_payload(db, party=party, viewer_id=int(customer.id))

    @staticmethod
    async def add_item(
        db: AsyncSession,
        *,
        customer: Customer,
        table: DiningTable,
        product_id: int,
        quantity: int = 1,
        option_ids: list[int] | None = None,
        option_text: str | None = None,
        remark: str | None = None,
        unit_price: str | Decimal | None = None,
        expected_revision: int | None = None,
    ) -> dict:
        if is_takeaway_table(table):
            raise AppException(40060, "外带桌不支持共享购物车")
        party = await SharedCartService.get_open_party(
            db, store_id=int(table.store_id), table_id=int(table.id)
        )
        if not party:
            await SharedCartService.join_party(db, customer=customer, table=table)
            party = await SharedCartService.get_open_party(
                db, store_id=int(table.store_id), table_id=int(table.id)
            )
        if not party:
            raise AppException(50010, "无法创建共享车")
        party = await SharedCartService._lock_party(db, int(party.id))
        SharedCartService._check_revision(party, expected_revision)
        await SharedCartService._require_member(db, party=party, customer_id=int(customer.id))

        product = await db.get(Product, int(product_id))
        if not product:
            raise AppException(40420, "餐品不存在")
        if int(product.store_id) != int(table.store_id):
            raise AppException(40062, "餐品不属于当前门店")
        if str(product.status or "") != "ON_SALE":
            raise AppException(40061, "餐品暂不可售")
        stock = int(getattr(product, "stock", 999) or 0)
        if stock == 0:
            raise AppException(40063, "餐品已售罄")

        qty = max(1, min(MAX_QTY, int(quantity or 1)))
        opts = [int(x) for x in (option_ids or []) if x is not None]
        key = _opt_key(opts)
        cid = int(customer.id)

        # 服务端再校验必选规格，并以服务端基价+规格加价为准（不信任前端金额）
        opt_extra = await _validate_product_options(
            db,
            product_id=int(product_id),
            option_ids=opts,
            store_id=int(table.store_id),
        )
        price = _money(getattr(product, "price", 0)) + opt_extra
        _ = unit_price  # 草稿行金额以服务端为准

        existing = None
        for row in (
            await db.execute(
                select(SharedCartItem).where(
                    SharedCartItem.party_id == int(party.id),
                    SharedCartItem.added_by_customer_id == cid,
                    SharedCartItem.product_id == int(product_id),
                )
            )
        ).scalars().all():
            if _opt_key(row.option_ids) == key:
                existing = row
                break

        if existing:
            existing.quantity = min(MAX_QTY, int(existing.quantity or 0) + qty)
            if option_text:
                existing.option_text = option_text[:255]
            if remark is not None:
                existing.remark = (remark or "")[:200] or None
        else:
            db.add(
                SharedCartItem(
                    party_id=int(party.id),
                    added_by_customer_id=cid,
                    product_id=int(product_id),
                    product_name=str(product.name or "")[:100],
                    quantity=qty,
                    unit_price=price,
                    option_ids=opts,
                    option_text=(option_text or "")[:255] or None,
                    remark=(remark or "")[:200] or None,
                    category_id=int(product.category_id) if getattr(product, "category_id", None) else None,
                    main_image=(getattr(product, "main_image", None) or None),
                    no_member_discount=1 if getattr(product, "no_member_discount", False) else 0,
                )
            )

        party.revision = int(party.revision or 1) + 1
        party.expires_at = now_local() + timedelta(hours=PARTY_HOURS)
        await db.flush()
        return await SharedCartService.cart_payload(db, party=party, viewer_id=cid)

    @staticmethod
    async def update_item(
        db: AsyncSession,
        *,
        customer: Customer,
        item_id: int,
        quantity: int,
        expected_revision: int | None = None,
    ) -> dict:
        row = await db.get(SharedCartItem, int(item_id))
        if not row:
            raise AppException(40421, "购物车行不存在")
        party = await SharedCartService._lock_party(db, int(row.party_id))
        SharedCartService._check_revision(party, expected_revision)
        mem = await SharedCartService._require_member(
            db, party=party, customer_id=int(customer.id)
        )
        mine = int(row.added_by_customer_id) == int(customer.id)
        if not mine:
            raise AppException(40311, "只能修改自己加的菜")
        qty = int(quantity or 0)
        if qty <= 0:
            await db.delete(row)
        else:
            row.quantity = min(MAX_QTY, qty)
        party.revision = int(party.revision or 1) + 1
        party.expires_at = now_local() + timedelta(hours=PARTY_HOURS)
        _ = mem
        await db.flush()
        return await SharedCartService.cart_payload(
            db, party=party, viewer_id=int(customer.id)
        )

    @staticmethod
    async def remove_item(
        db: AsyncSession,
        *,
        customer: Customer,
        item_id: int,
        expected_revision: int | None = None,
    ) -> dict:
        return await SharedCartService.update_item(
            db,
            customer=customer,
            item_id=item_id,
            quantity=0,
            expected_revision=expected_revision,
        )

    @staticmethod
    async def clear_cart(
        db: AsyncSession,
        *,
        customer: Customer,
        table: DiningTable,
        expected_revision: int | None = None,
    ) -> dict:
        party = await SharedCartService.get_open_party(
            db, store_id=int(table.store_id), table_id=int(table.id)
        )
        if not party:
            return {"enabled": True, "items": [], "revision": 0, "rules": SharedCartService.rules_payload()}
        party = await SharedCartService._lock_party(db, int(party.id))
        SharedCartService._check_revision(party, expected_revision)
        await SharedCartService._require_member(db, party=party, customer_id=int(customer.id))
        if int(party.host_customer_id) != int(customer.id):
            raise AppException(40312, "仅桌主可清空共享车")
        rows = list(
            (
                await db.execute(
                    select(SharedCartItem).where(SharedCartItem.party_id == int(party.id))
                )
            )
            .scalars()
            .all()
        )
        for r in rows:
            await db.delete(r)
        party.revision = int(party.revision or 1) + 1
        await db.flush()
        return await SharedCartService.cart_payload(
            db, party=party, viewer_id=int(customer.id)
        )

    @staticmethod
    async def clear_after_order(
        db: AsyncSession, *, store_id: int, table_id: int | None, customer_id: int
    ) -> None:
        """一人结全桌成功后清空本桌共享草稿。"""
        if not table_id:
            return
        party = await SharedCartService.get_open_party(
            db, store_id=int(store_id), table_id=int(table_id)
        )
        if not party:
            return
        # 结算人须为本局成员
        mem = (
            await db.execute(
                select(TablePartyMember).where(
                    TablePartyMember.party_id == int(party.id),
                    TablePartyMember.customer_id == int(customer_id),
                    TablePartyMember.left_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if not mem:
            return
        party = await SharedCartService._lock_party(db, int(party.id))
        rows = list(
            (
                await db.execute(
                    select(SharedCartItem).where(SharedCartItem.party_id == int(party.id))
                )
            )
            .scalars()
            .all()
        )
        for r in rows:
            await db.delete(r)
        party.revision = int(party.revision or 1) + 1
        await db.flush()

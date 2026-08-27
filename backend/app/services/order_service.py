"""订单价格计算（批量查规格，消除 N+1）+ 库存扣减"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.response import money
from app.models import Option, OptionGroup, Order, OrderItem, OrderItemOption, Product, ProductOptionGroup
from app.services.stock_service import deduct_for_order_items


class OrderPriceCalculator:
    """服务端订单价格计算 - 禁止信任前端金额"""

    MAX_ITEMS = 50

    @staticmethod
    async def calculate_items_batch(
        db: AsyncSession,
        items_data: list[dict],
    ) -> list[tuple[Product, Decimal, list[tuple[OptionGroup, Option, int]], int]]:
        if not items_data:
            raise AppException(40000, "订单商品不能为空")
        if len(items_data) > OrderPriceCalculator.MAX_ITEMS:
            raise AppException(40000, f"单次下单最多 {OrderPriceCalculator.MAX_ITEMS} 个品项")

        product_ids = [int(i["product_id"]) for i in items_data]
        products = {
            p.id: p
            for p in (
                await db.execute(select(Product).where(Product.id.in_(product_ids), Product.status == "ON_SALE"))
            ).scalars()
        }
        if len(products) != len(set(product_ids)):
            missing = set(product_ids) - set(products)
            raise AppException(40002, f"商品不可用: {sorted(missing)}")

        # 批量规格组
        pog_rows = (
            await db.execute(
                select(ProductOptionGroup, OptionGroup)
                .join(OptionGroup, OptionGroup.id == ProductOptionGroup.group_id)
                .where(ProductOptionGroup.product_id.in_(product_ids), OptionGroup.status == 1)
            )
        ).all()
        bound_by_product: dict[int, dict[int, OptionGroup]] = {}
        for pog, group in pog_rows:
            bound_by_product.setdefault(pog.product_id, {})[group.id] = group

        all_option_ids: list[int] = []
        for item in items_data:
            all_option_ids.extend(int(x) for x in (item.get("option_ids") or []))
        options_map: dict[int, Option] = {}
        if all_option_ids:
            options_map = {
                o.id: o
                for o in (
                    await db.execute(select(Option).where(Option.id.in_(set(all_option_ids)), Option.status == 1))
                ).scalars()
            }

        group_ids = {o.group_id for o in options_map.values()}
        groups_map: dict[int, OptionGroup] = {}
        if group_ids:
            groups_map = {
                g.id: g for g in (await db.execute(select(OptionGroup).where(OptionGroup.id.in_(group_ids)))).scalars()
            }

        results = []
        for item in items_data:
            product_id = int(item["product_id"])
            quantity = int(item["quantity"])
            option_ids = [int(x) for x in (item.get("option_ids") or [])]
            if quantity <= 0 or quantity > 99:
                raise AppException(40001, "数量非法")

            product = products[product_id]
            bound_groups = bound_by_product.get(product_id, {})
            if not option_ids and bound_groups:
                required = [g for g in bound_groups.values() if g.is_required]
                if required:
                    raise AppException(40003, f"缺少必选规格: {product.name}")

            options_detail: list[tuple[OptionGroup, Option, int]] = []
            selected_by_group: dict[int, list[Option]] = {}
            for oid in option_ids:
                opt = options_map.get(oid)
                if not opt:
                    raise AppException(40004, f"规格选项无效: {oid}")
                group = groups_map.get(opt.group_id)
                if not group or group.id not in bound_groups:
                    raise AppException(40005, f"规格不属于该商品: {oid}")
                selected_by_group.setdefault(group.id, []).append(opt)

            for gid, group in bound_groups.items():
                selected = selected_by_group.get(gid, [])
                if group.is_required and not selected:
                    raise AppException(40006, f"缺少必选规格组: {group.name}")
                if group.selection_type == "SINGLE" and len(selected) > 1:
                    raise AppException(40007, f"规格组 {group.name} 只能单选")
                for opt in selected:
                    options_detail.append((group, opt, 1))

            unit_extra = sum(opt.extra_price for _, opt, _ in options_detail)
            unit_price = money(product.price + unit_extra)
            subtotal = money(unit_price * quantity)
            results.append((product, subtotal, options_detail, quantity))
        return results

    @staticmethod
    async def calculate_item(
        db: AsyncSession,
        product_id: int,
        quantity: int,
        option_ids: list[int] | None = None,
    ) -> tuple[Product, Decimal, list[tuple[OptionGroup, Option, int]]]:
        option_ids = list(option_ids or [])
        rows = await OrderPriceCalculator.calculate_items_batch(
            db, [{"product_id": product_id, "quantity": quantity, "option_ids": option_ids}]
        )
        product, subtotal, options_detail, _ = rows[0]
        return product, subtotal, options_detail

    @staticmethod
    async def build_order_items(
        db: AsyncSession,
        order: Order,
        items_data: list[dict],
        *,
        deduct_stock: bool = True,
    ) -> Decimal:
        rows = await OrderPriceCalculator.calculate_items_batch(db, items_data)
        if deduct_stock:
            await deduct_for_order_items(
                db,
                [{"product_id": p.id, "quantity": qty} for p, _, _, qty in rows],
            )

        total = Decimal("0.00")
        for (product, subtotal, options_detail, quantity), item in zip(rows, items_data):
            unit_extra = sum(o.extra_price for _, o, _ in options_detail)
            unit_price = money(product.price + unit_extra)
            order_item = OrderItem(
                order_id=order.id,
                product_id=product.id,
                product_name_snapshot=product.name,
                unit_price_snapshot=unit_price,
                quantity=quantity,
                subtotal=subtotal,
                remark=item.get("remark"),
            )
            db.add(order_item)
            await db.flush()
            for group, opt, qty in options_detail:
                db.add(
                    OrderItemOption(
                        order_item_id=order_item.id,
                        group_name_snapshot=group.name,
                        option_name_snapshot=opt.name,
                        option_code=opt.code,
                        extra_price_snapshot=opt.extra_price,
                        quantity=qty,
                    )
                )
            total += subtotal
        return money(total)

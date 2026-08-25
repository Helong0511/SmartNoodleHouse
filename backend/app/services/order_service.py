from decimal import Decimal
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.response import money
from app.models import Option, OptionGroup, Order, OrderItem, OrderItemOption, Product, ProductOptionGroup


class OrderPriceCalculator:
    """服务端订单价格计算 - 禁止信任前端金额"""

    @staticmethod
    async def calculate_item(
        db: AsyncSession,
        product_id: int,
        quantity: int,
        option_ids: list[int],
    ) -> tuple[Product, Decimal, list[tuple[OptionGroup, Option, int]]]:
        if quantity <= 0:
            raise AppException(40001, "数量必须大于0")

        product = await db.get(Product, product_id)
        if not product or product.status != "ON_SALE":
            raise AppException(40002, f"商品不可用: {product_id}")

        # 获取商品绑定的规格组
        pog_result = await db.execute(
            select(ProductOptionGroup, OptionGroup)
            .join(OptionGroup, OptionGroup.id == ProductOptionGroup.group_id)
            .where(ProductOptionGroup.product_id == product_id, OptionGroup.status == 1)
            .order_by(ProductOptionGroup.sort_order)
        )
        bound_groups = {row[1].id: row[1] for row in pog_result.all()}

        if not option_ids and bound_groups:
            required = [g for g in bound_groups.values() if g.is_required]
            if required:
                raise AppException(40003, "缺少必选规格")

        options_detail: list[tuple[OptionGroup, Option, int]] = []
        selected_by_group: dict[int, list[Option]] = {}

        for oid in option_ids:
            opt = await db.get(Option, oid)
            if not opt or opt.status != 1:
                raise AppException(40004, f"规格选项无效: {oid}")
            group = await db.get(OptionGroup, opt.group_id)
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
        return product, subtotal, options_detail

    @staticmethod
    async def build_order_items(
        db: AsyncSession,
        order: Order,
        items_data: list[dict],
    ) -> Decimal:
        total = Decimal("0.00")
        for item in items_data:
            product, subtotal, options_detail = await OrderPriceCalculator.calculate_item(
                db, item["product_id"], item["quantity"], item.get("option_ids", [])
            )
            unit_extra = sum(o.extra_price for _, o, _ in options_detail)
            unit_price = money(product.price + unit_extra)

            order_item = OrderItem(
                order_id=order.id,
                product_id=product.id,
                product_name_snapshot=product.name,
                unit_price_snapshot=unit_price,
                quantity=item["quantity"],
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

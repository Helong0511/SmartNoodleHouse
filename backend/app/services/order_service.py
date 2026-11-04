"""订单价格计算（批量查规格，消除 N+1）+ 库存扣减"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.response import money
from app.models import Option, OptionGroup, Order, OrderItem, OrderItemOption, Product, ProductCategory, ProductOptionGroup
from app.schemas.order import MAX_ORDER_ITEM_QUANTITY
from app.services.category_require import format_require_hint, resolve_product_requires
from app.services.stock_service import deduct_for_order_items

ORDER_STATUS_LABEL = {
    "PENDING_PAYMENT": "待支付",
    "PAID": "已支付",
    "CONFIRMED": "已接单",
    "PREPARING": "备餐中",
    "COOKING": "制作中",
    "READY": "待取餐",
    "COMPLETED": "已完成",
    "CANCELLED": "已取消",
    "REFUND_PENDING": "退款中",
    "REFUNDED": "已退款",
}

PAYMENT_STATUS_LABEL = {
    "UNPAID": "未支付",
    "FREE": "免费",
    "PAID": "已支付",
    "PARTIAL_PAID": "部分支付",
    "REFUNDING": "退款中",
    "PARTIAL_REFUND": "部分退款",
    "REFUNDED": "已退款",
}


class OrderReorderService:
    """根据历史订单解析可再次下单的购物车条目"""

    @staticmethod
    async def build_cart_from_order(db: AsyncSession, order: Order) -> dict:
        items = list(
            (await db.execute(select(OrderItem).where(OrderItem.order_id == order.id).order_by(OrderItem.id))).scalars()
        )
        if not items:
            raise AppException(40040, "该订单没有可复购餐品")

        item_ids = [i.id for i in items]
        opts = list(
            (await db.execute(select(OrderItemOption).where(OrderItemOption.order_item_id.in_(item_ids)))).scalars()
        )
        opts_by_item: dict[int, list[OrderItemOption]] = {}
        for o in opts:
            opts_by_item.setdefault(o.order_item_id, []).append(o)

        product_ids = list({i.product_id for i in items})
        products = {
            p.id: p
            for p in (await db.execute(select(Product).where(Product.id.in_(product_ids)))).scalars()
        }

        pog_rows = (
            await db.execute(
                select(ProductOptionGroup, OptionGroup)
                .join(OptionGroup, OptionGroup.id == ProductOptionGroup.group_id)
                .where(ProductOptionGroup.product_id.in_(product_ids), OptionGroup.status == 1)
            )
        ).all()
        bound_groups: dict[int, list[OptionGroup]] = {}
        group_ids: list[int] = []
        for pog, group in pog_rows:
            bound_groups.setdefault(pog.product_id, []).append(group)
            group_ids.append(group.id)

        options_by_group: dict[int, list[Option]] = {}
        if group_ids:
            for opt in (
                await db.execute(select(Option).where(Option.group_id.in_(set(group_ids)), Option.status == 1))
            ).scalars():
                options_by_group.setdefault(opt.group_id, []).append(opt)

        cart_items: list[dict] = []
        skipped: list[dict] = []

        for item in items:
            product = products.get(item.product_id)
            snap_name = item.product_name_snapshot
            hist_opts = opts_by_item.get(item.id, [])
            if not product or product.status != "ON_SALE":
                skipped.append(
                    {
                        "product_id": item.product_id,
                        "name": snap_name,
                        "reason": "餐品已下架或不可用",
                    }
                )
                continue

            groups = bound_groups.get(product.id, [])
            option_ids: list[int] = []
            option_labels: list[str] = []
            missing_required = False

            # 先按历史选项 code / 组名匹配
            matched_group_ids: set[int] = set()
            for ho in hist_opts:
                found: Option | None = None
                for g in groups:
                    if g.name != ho.group_name_snapshot:
                        continue
                    for opt in options_by_group.get(g.id, []):
                        if opt.code == ho.option_code:
                            found = opt
                            break
                    if found:
                        break
                if not found:
                    for g in groups:
                        for opt in options_by_group.get(g.id, []):
                            if opt.code == ho.option_code:
                                found = opt
                                break
                        if found:
                            break
                if found:
                    option_ids.append(found.id)
                    matched_group_ids.add(found.group_id)
                    if found.code != "NORMAL":
                        option_labels.append(found.name)

            # 必选规格未匹配到时，尝试选默认 NORMAL / 第一项
            for g in groups:
                if not g.is_required or g.id in matched_group_ids:
                    continue
                opts_g = options_by_group.get(g.id, [])
                if not opts_g:
                    missing_required = True
                    break
                default = next((o for o in opts_g if o.code == "NORMAL"), opts_g[0])
                option_ids.append(default.id)
                matched_group_ids.add(g.id)
                if default.code != "NORMAL":
                    option_labels.append(default.name)

            if missing_required:
                skipped.append(
                    {
                        "product_id": product.id,
                        "name": product.name,
                        "reason": "缺少必选规格，请手动点餐",
                    }
                )
                continue

            # 用服务端计价，保证与下单一致（含限时/生日可见性，需带原单顾客）
            try:
                priced = await OrderPriceCalculator.calculate_items_batch(
                    db,
                    [{"product_id": product.id, "quantity": item.quantity, "option_ids": option_ids}],
                    store_id=order.store_id,
                    customer_id=int(order.customer_id) if order.customer_id else None,
                )
                _p, line_subtotal, _detail, qty = priced[0]
                unit_price = money(line_subtotal / Decimal(qty)) if qty else line_subtotal
            except AppException as e:
                skipped.append({"product_id": product.id, "name": product.name, "reason": e.message})
                continue

            cart_items.append(
                {
                    "product_id": product.id,
                    "name": product.name,
                    "quantity": qty,
                    "option_ids": option_ids,
                    "option_labels": option_labels,
                    "option_text": "、".join(option_labels),
                    "unit_price": str(money(unit_price)),
                    "subtotal": str(money(line_subtotal)),
                    "imageUrl": product.main_image or "",
                    "main_image": product.main_image or "",
                    "no_member_discount": bool(getattr(product, "no_member_discount", False)),
                }
            )

        if not cart_items:
            raise AppException(40041, "没有可复购的在售餐品，请重新点餐")

        return {
            "order_id": order.id,
            "order_no": order.order_no,
            "store_id": int(order.store_id) if order.store_id else None,
            "cart_items": cart_items,
            "skipped": skipped,
            "cart_count": sum(i["quantity"] for i in cart_items),
        }


class OrderPriceCalculator:
    """服务端订单价格计算 - 禁止信任前端金额"""

    MAX_ITEMS = 50

    @staticmethod
    async def calculate_items_batch(
        db: AsyncSession,
        items_data: list[dict],
        *,
        store_id: int | None = None,
        customer_id: int | None = None,
    ) -> list[tuple[Product, Decimal, list[tuple[OptionGroup, Option, int]], int]]:
        if not items_data:
            raise AppException(40000, "订单餐品不能为空")
        if len(items_data) > OrderPriceCalculator.MAX_ITEMS:
            raise AppException(40000, f"单次下单最多 {OrderPriceCalculator.MAX_ITEMS} 个品项")
        if store_id is None or int(store_id) <= 0:
            raise AppException(40008, "计价须指定门店，禁止跨店餐品")

        store_id = int(store_id)
        product_ids = [int(i["product_id"]) for i in items_data]
        products = {
            p.id: p
            for p in (
                await db.execute(
                    select(Product).where(
                        Product.id.in_(product_ids),
                        Product.status == "ON_SALE",
                        Product.store_id == store_id,
                    )
                )
            ).scalars()
        }
        if len(products) != len(set(product_ids)):
            # 区分「不存在/下架」与「跨店」
            any_products = {
                p.id: p
                for p in (
                    await db.execute(select(Product).where(Product.id.in_(product_ids)))
                ).scalars()
            }
            cross = [pid for pid in set(product_ids) if pid in any_products and int(any_products[pid].store_id) != store_id]
            if cross:
                raise AppException(40002, f"餐品不属于本门店，禁止跨店下单: {sorted(cross)}")
            missing = set(product_ids) - set(products)
            raise AppException(40002, f"餐品不可用: {sorted(missing)}")

        # 限时/生日专属：不可点则拒绝（防改请求偷加）
        from app.models import Customer
        from app.utils.birthday import is_birthday_today, normalize_calendar, today_beijing
        from app.utils.product_visibility import is_product_visible

        is_bday = False
        if customer_id:
            cust = await db.get(Customer, int(customer_id))
            if cust:
                is_bday = is_birthday_today(
                    cust.birthday,
                    normalize_calendar(getattr(cust, "birthday_calendar", None)),
                )
        today = today_beijing()
        for p in products.values():
            if not is_product_visible(p, today=today, is_birthday_today=is_bday):
                raise AppException(
                    40002,
                    f"「{p.name}」今日不可点（限时或生日专属）",
                )

        # 购买依赖（餐品级优先，否则继承分类默认；分类 OR 指定餐品）
        cat_ids = list({p.category_id for p in products.values() if p.category_id})
        cats_map: dict[int, ProductCategory] = {}
        if cat_ids:
            cats_map = {
                c.id: c
                for c in (
                    await db.execute(
                        select(ProductCategory).where(
                            ProductCategory.id.in_(cat_ids),
                            ProductCategory.store_id == store_id,
                        )
                    )
                ).scalars()
            }
            if len(cats_map) != len(set(cat_ids)):
                raise AppException(40002, "餐品分类不属于本门店")
        cart_cat_ids = {int(p.category_id) for p in products.values() if p.category_id}
        cart_product_ids = set(products.keys())

        need_cat_names: set[int] = set()
        need_prod_names: set[int] = set()
        resolved: dict[int, tuple[list[int], list[int]]] = {}
        for p in products.values():
            req_cats, req_prods, _ = resolve_product_requires(p, cats_map.get(p.category_id))
            resolved[p.id] = (req_cats, req_prods)
            for rid in req_cats:
                if rid not in cart_cat_ids:
                    need_cat_names.add(rid)
            for rid in req_prods:
                if rid not in cart_product_ids:
                    need_prod_names.add(rid)

        cat_name_map: dict[int, str] = {c.id: c.name for c in cats_map.values()}
        if need_cat_names - set(cat_name_map):
            for c in (
                await db.execute(
                    select(ProductCategory).where(
                        ProductCategory.id.in_(need_cat_names - set(cat_name_map)),
                        ProductCategory.store_id == store_id,
                    )
                )
            ).scalars().all():
                cat_name_map[c.id] = c.name
        prod_name_map: dict[int, str] = {pid: products[pid].name for pid in cart_product_ids}
        if need_prod_names:
            for rp in (
                await db.execute(
                    select(Product).where(
                        Product.id.in_(need_prod_names),
                        Product.store_id == store_id,
                    )
                )
            ).scalars().all():
                prod_name_map[rp.id] = rp.name

        for p in products.values():
            req_cats, req_prods = resolved[p.id]
            if not req_cats and not req_prods:
                continue
            ok = any(rid in cart_cat_ids for rid in req_cats) or any(
                rid in cart_product_ids for rid in req_prods
            )
            if ok:
                continue
            hint = format_require_hint(
                require_category_ids=req_cats,
                require_product_ids=req_prods,
                category_names=cat_name_map,
                product_names=prod_name_map,
            )
            raise AppException(40008, f"「{p.name}」须先点：{hint}")

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
            # P0-1: 跨门店规格校验 — group.store_id 非 NULL 时必须等于订单门店
            cross_store_groups = [
                g.name
                for g in groups_map.values()
                if g.store_id is not None and int(g.store_id) != store_id
            ]
            if cross_store_groups:
                raise AppException(40002, f"规格组不属于本门店，禁止跨店下单: {cross_store_groups}")

        results = []
        for item in items_data:
            product_id = int(item["product_id"])
            quantity = int(item["quantity"])
            option_ids = [int(x) for x in (item.get("option_ids") or [])]
            if quantity <= 0 or quantity > MAX_ORDER_ITEM_QUANTITY:
                raise AppException(40001, f"单品数量须在 1～{MAX_ORDER_ITEM_QUANTITY}")

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
                    raise AppException(40005, f"规格不属于该餐品: {oid}")
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
        *,
        store_id: int | None = None,
    ) -> tuple[Product, Decimal, list[tuple[OptionGroup, Option, int]]]:
        option_ids = list(option_ids or [])
        rows = await OrderPriceCalculator.calculate_items_batch(
            db,
            [{"product_id": product_id, "quantity": quantity, "option_ids": option_ids}],
            store_id=store_id,
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
        need_pack: bool = False,
    ) -> tuple[Decimal, Decimal, Decimal]:
        """返回 (餐品小计, 打包费, 可参与会员折扣的小计)。打包费 = Σ(餐品 box_price × 数量)，仅 need_pack 时计。"""
        rows = await OrderPriceCalculator.calculate_items_batch(
            db,
            items_data,
            store_id=order.store_id,
            customer_id=int(order.customer_id) if order.customer_id else None,
        )
        # P1-1 纵深防御：最终断言所有餐品门店与订单门店一致（calculate_items_batch 已校验，此处为第二道防线）
        if rows and order.store_id is not None:
            order_store = int(order.store_id)
            mismatched = [int(p.id) for p, _, _, _ in rows if p.store_id is not None and int(p.store_id) != order_store]
            if mismatched:
                raise AppException(40002, f"订单门店与餐品门店不一致: product_ids={mismatched}")
        if deduct_stock:
            await deduct_for_order_items(
                db,
                [{"product_id": p.id, "quantity": qty} for p, _, _, qty in rows],
                store_id=order.store_id,
            )

        total = Decimal("0.00")
        packing = Decimal("0.00")
        discountable = Decimal("0.00")
        # zip 静默截断防护：计价结果与输入数量必须一致
        if len(rows) != len(items_data):
            raise AppException(50001, "计价结果与输入数量不一致，疑似并发修改")
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
            if not getattr(product, "no_member_discount", False):
                discountable += subtotal
            if need_pack:
                packing += money((product.box_price or Decimal("0")) * quantity)
        return money(total), money(packing), money(discountable)

"""跨门店复制分类 / 规格组 / 商品（新建副本，不改源数据）。"""
from __future__ import annotations

from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models import Option, OptionGroup, Product, ProductCategory, ProductImage, ProductOptionGroup, Store


ConflictMode = Literal["skip", "rename"]


class MenuCopyService:
    @classmethod
    async def _next_id(cls, db: AsyncSession, model) -> int | None:
        """SQLite 对 BigInteger PK 自增不可靠；MySQL 走 AUTO_INCREMENT。"""
        bind = db.get_bind()
        dialect = getattr(getattr(bind, "dialect", None), "name", "") or ""
        if dialect != "sqlite":
            return None
        cur = await db.scalar(select(func.coalesce(func.max(model.id), 0)))
        return int(cur or 0) + 1

    @classmethod
    def _with_id(cls, model_cls, nid: int | None, **kwargs):
        if nid is not None:
            kwargs["id"] = nid
        return model_cls(**kwargs)

    @classmethod
    async def copy_to_store(
        cls,
        db: AsyncSession,
        *,
        target_store_id: int,
        category_ids: list[int] | None = None,
        option_group_ids: list[int] | None = None,
        product_ids: list[int] | None = None,
        on_product_conflict: ConflictMode = "skip",
        clone_shared_option_groups: bool = False,
    ) -> dict[str, Any]:
        target = await db.get(Store, int(target_store_id))
        if not target or int(target.status or 0) != 1:
            raise AppException(40003, "目标门店不存在或已停用")

        cat_ids = [int(x) for x in (category_ids or []) if x]
        group_ids = [int(x) for x in (option_group_ids or []) if x]
        prod_ids = [int(x) for x in (product_ids or []) if x]
        if not cat_ids and not group_ids and not prod_ids:
            raise AppException(40001, "请至少选择分类、规格组或商品")

        summary: dict[str, Any] = {
            "target_store_id": int(target_store_id),
            "categories": {"created": 0, "reused": 0, "skipped": 0},
            "option_groups": {"created": 0, "reused": 0, "skipped_shared": 0, "cloned_shared": 0},
            "products": {"created": 0, "skipped": 0, "renamed": 0},
            "category_id_map": {},
            "option_group_id_map": {},
            "product_id_map": {},
            "warnings": [],
        }

        products = await cls._load_products(db, prod_ids)
        # 商品自动带上所属分类 + 私有规格组
        for p in products:
            cat_ids.append(int(p.category_id))
            for rid in p.require_category_ids or []:
                try:
                    cat_ids.append(int(rid))
                except (TypeError, ValueError):
                    pass
            for link in await cls._product_group_links(db, int(p.id)):
                group_ids.append(int(link.group_id))

        cat_ids = list(dict.fromkeys(cat_ids))
        group_ids = list(dict.fromkeys(group_ids))

        cat_map = await cls._copy_categories(db, cat_ids, int(target_store_id), summary)
        group_map = await cls._copy_option_groups(
            db,
            group_ids,
            int(target_store_id),
            summary,
            clone_shared=clone_shared_option_groups,
        )
        prod_map = await cls._copy_products(
            db,
            products,
            int(target_store_id),
            cat_map,
            group_map,
            on_product_conflict,
            summary,
        )

        # 商品 require_product_ids 二次映射
        for old_id, new_id in prod_map.items():
            src = next((p for p in products if int(p.id) == int(old_id)), None)
            if not src:
                continue
            dst = await db.get(Product, int(new_id))
            if not dst:
                continue
            if src.require_product_ids is None:
                continue
            remapped = []
            for rid in src.require_product_ids:
                try:
                    oid = int(rid)
                except (TypeError, ValueError):
                    continue
                if oid in prod_map:
                    remapped.append(prod_map[oid])
            dst.require_product_ids = remapped

        await db.flush()
        summary["category_id_map"] = {str(k): v for k, v in cat_map.items()}
        summary["option_group_id_map"] = {str(k): v for k, v in group_map.items()}
        summary["product_id_map"] = {str(k): v for k, v in prod_map.items()}
        return summary

    @classmethod
    async def _load_products(cls, db: AsyncSession, product_ids: list[int]) -> list[Product]:
        if not product_ids:
            return []
        rows = list(
            (await db.execute(select(Product).where(Product.id.in_(product_ids)))).scalars().all()
        )
        found = {int(p.id) for p in rows}
        missing = [i for i in product_ids if i not in found]
        if missing:
            raise AppException(40003, f"商品不存在: {missing}")
        stores = {int(p.store_id) for p in rows}
        if len(stores) > 1:
            raise AppException(40001, "请选择同一门店的商品再复制")
        return rows

    @classmethod
    async def _product_group_links(cls, db: AsyncSession, product_id: int) -> list[ProductOptionGroup]:
        return list(
            (
                await db.execute(
                    select(ProductOptionGroup)
                    .where(ProductOptionGroup.product_id == product_id)
                    .order_by(ProductOptionGroup.sort_order, ProductOptionGroup.id)
                )
            )
            .scalars()
            .all()
        )

    @classmethod
    async def _copy_categories(
        cls,
        db: AsyncSession,
        category_ids: list[int],
        target_store_id: int,
        summary: dict,
    ) -> dict[int, int]:
        if not category_ids:
            return {}
        cats = list(
            (await db.execute(select(ProductCategory).where(ProductCategory.id.in_(category_ids))))
            .scalars()
            .all()
        )
        found = {int(c.id) for c in cats}
        missing = [i for i in category_ids if i not in found]
        if missing:
            raise AppException(40003, f"分类不存在: {missing}")

        existing = list(
            (
                await db.execute(
                    select(ProductCategory).where(ProductCategory.store_id == target_store_id)
                )
            )
            .scalars()
            .all()
        )
        by_name = {str(c.name).strip(): c for c in existing}
        by_logical = {}
        from app.services.option_code import (
            format_category_no,
            group_logical_key,
            name_to_logical_code,
            normalize_store_prefix,
        )

        target_store = await db.get(Store, int(target_store_id))
        target_prefix = normalize_store_prefix(getattr(target_store, "store_no", None) if target_store else None)
        for c in existing:
            no = getattr(c, "category_no", None) or ""
            if no:
                by_logical[group_logical_key(no)] = c
            else:
                by_logical[name_to_logical_code(c.name)] = c
        id_map: dict[int, int] = {}
        created_rows: list[tuple[ProductCategory, ProductCategory]] = []

        for src in cats:
            key = str(src.name).strip()
            raw_no = (getattr(src, "category_no", None) or "").strip()
            src_logical = group_logical_key(raw_no) if raw_no else name_to_logical_code(src.name)
            hit = by_name.get(key) or by_logical.get(src_logical)
            if hit:
                id_map[int(src.id)] = int(hit.id)
                summary["categories"]["reused"] += 1
                continue
            new_no = format_category_no(target_prefix, raw_no or src_logical)
            row = cls._with_id(
                ProductCategory,
                await cls._next_id(db, ProductCategory),
                store_id=target_store_id,
                category_no=new_no,
                name=src.name,
                description=src.description,
                sort_order=src.sort_order,
                status=src.status,
                require_category_ids=None,
            )
            db.add(row)
            await db.flush()
            id_map[int(src.id)] = int(row.id)
            by_name[key] = row
            by_logical[src_logical] = row
            created_rows.append((src, row))
            summary["categories"]["created"] += 1

        for src, row in created_rows:
            if not src.require_category_ids:
                row.require_category_ids = src.require_category_ids
                continue
            remapped = []
            for rid in src.require_category_ids:
                try:
                    oid = int(rid)
                except (TypeError, ValueError):
                    continue
                if oid in id_map:
                    remapped.append(id_map[oid])
                else:
                    summary["warnings"].append(
                        f"分类「{src.name}」依赖分类#{oid}未一并复制，已忽略该依赖"
                    )
            row.require_category_ids = remapped or None

        await db.flush()
        return id_map

    @classmethod
    async def _clone_group_to_store(
        cls,
        db: AsyncSession,
        src: OptionGroup,
        target_store_id: int,
        target_prefix: str,
        target_by_code: dict[str, OptionGroup],
        target_by_logical: dict[str, OptionGroup],
        summary: dict,
        *,
        from_shared: bool,
    ) -> int:
        from app.services.option_code import format_group_code, format_option_code, group_logical_key

        new_group_code = format_group_code(target_prefix, src.code)
        logical = group_logical_key(src.code)
        hit = target_by_code.get(new_group_code) or target_by_logical.get(logical)
        if hit:
            summary["option_groups"]["reused"] += 1
            return int(hit.id)
        row = cls._with_id(
            OptionGroup,
            await cls._next_id(db, OptionGroup),
            store_id=target_store_id,
            name=src.name,
            code=new_group_code,
            selection_type=src.selection_type,
            is_required=bool(src.is_required),
            sort_order=src.sort_order,
            status=src.status,
        )
        db.add(row)
        await db.flush()
        opts = list(
            (
                await db.execute(
                    select(Option).where(Option.group_id == src.id).order_by(Option.sort_order, Option.id)
                )
            )
            .scalars()
            .all()
        )
        for o in opts:
            db.add(
                cls._with_id(
                    Option,
                    await cls._next_id(db, Option),
                    group_id=row.id,
                    name=o.name,
                    code=format_option_code(target_prefix, new_group_code, o.code),
                    extra_price=o.extra_price,
                    sort_order=o.sort_order,
                    is_default=bool(o.is_default),
                    status=o.status,
                )
            )
            await db.flush()
        target_by_code[new_group_code] = row
        target_by_logical[logical] = row
        summary["option_groups"]["created"] += 1
        if from_shared:
            summary["option_groups"]["cloned_shared"] += 1
        return int(row.id)

    @classmethod
    async def _copy_option_groups(
        cls,
        db: AsyncSession,
        group_ids: list[int],
        target_store_id: int,
        summary: dict,
        *,
        clone_shared: bool = False,
    ) -> dict[int, int]:
        if not group_ids:
            return {}
        from app.services.option_code import group_logical_key, normalize_store_prefix

        groups = list(
            (await db.execute(select(OptionGroup).where(OptionGroup.id.in_(group_ids)))).scalars().all()
        )
        found = {int(g.id) for g in groups}
        missing = [i for i in group_ids if i not in found]
        if missing:
            raise AppException(40003, f"规格组不存在: {missing}")

        target_store = await db.get(Store, int(target_store_id))
        target_prefix = normalize_store_prefix(getattr(target_store, "store_no", None) if target_store else None)

        existing = list(
            (
                await db.execute(
                    select(OptionGroup).where(OptionGroup.store_id == target_store_id)
                )
            )
            .scalars()
            .all()
        )
        target_by_code = {str(g.code): g for g in existing}
        target_by_logical = {group_logical_key(str(g.code)): g for g in existing}
        id_map: dict[int, int] = {}

        for src in groups:
            if src.store_id is None:
                if not clone_shared:
                    id_map[int(src.id)] = int(src.id)
                    summary["option_groups"]["skipped_shared"] += 1
                    continue
                id_map[int(src.id)] = await cls._clone_group_to_store(
                    db,
                    src,
                    target_store_id,
                    target_prefix,
                    target_by_code,
                    target_by_logical,
                    summary,
                    from_shared=True,
                )
                continue
            id_map[int(src.id)] = await cls._clone_group_to_store(
                db,
                src,
                target_store_id,
                target_prefix,
                target_by_code,
                target_by_logical,
                summary,
                from_shared=False,
            )

        return id_map

    @classmethod
    async def _copy_products(
        cls,
        db: AsyncSession,
        products: list[Product],
        target_store_id: int,
        cat_map: dict[int, int],
        group_map: dict[int, int],
        on_conflict: ConflictMode,
        summary: dict,
    ) -> dict[int, int]:
        if not products:
            return {}
        from app.services.option_code import (
            format_product_no,
            group_logical_key,
            name_to_logical_code,
            normalize_store_prefix,
        )

        target_store = await db.get(Store, int(target_store_id))
        target_prefix = normalize_store_prefix(getattr(target_store, "store_no", None) if target_store else None)
        existing_nos = {
            str(r)
            for r in (
                await db.execute(
                    select(Product.product_no).where(Product.store_id == target_store_id)
                )
            )
            .scalars()
            .all()
        }
        id_map: dict[int, int] = {}

        for src in products:
            new_cat = cat_map.get(int(src.category_id))
            if not new_cat:
                raise AppException(40003, f"商品「{src.name}」所属分类未能映射到目标店")
            dst_cat = await db.get(ProductCategory, int(new_cat))
            cat_logical = (
                group_logical_key(getattr(dst_cat, "category_no", None) or "")
                if dst_cat
                else ""
            ) or name_to_logical_code(dst_cat.name if dst_cat else src.name)
            product_no = format_product_no(target_prefix, src.product_no, category_logical=cat_logical)
            renamed = False
            if product_no in existing_nos:
                if on_conflict == "skip":
                    summary["products"]["skipped"] += 1
                    summary["warnings"].append(
                        f"目标店已有编号「{product_no}」，已跳过商品「{src.name}」"
                    )
                    continue
                logical = group_logical_key(product_no)
                # 取商品段再加后缀
                prod_stem = logical.split("-")[-1] if logical else "Item"
                n = 1
                while True:
                    candidate = format_product_no(
                        target_prefix, f"{prod_stem}C{n}", category_logical=cat_logical
                    )
                    if candidate not in existing_nos:
                        product_no = candidate
                        renamed = True
                        break
                    n += 1
                    if n > 99:
                        raise AppException(40003, f"无法为商品编号「{src.product_no}」生成副本名")

            require_cats = None
            if src.require_category_ids is not None:
                require_cats = []
                for x in src.require_category_ids:
                    try:
                        oid = int(x)
                    except (TypeError, ValueError):
                        continue
                    if oid in cat_map:
                        require_cats.append(cat_map[oid])

            row = cls._with_id(
                Product,
                await cls._next_id(db, Product),
                store_id=target_store_id,
                category_id=new_cat,
                product_no=product_no,
                name=src.name,
                description=src.description,
                main_image=src.main_image,
                price=src.price,
                original_price=src.original_price,
                status=src.status,
                is_recommended=bool(src.is_recommended),
                is_hot=bool(src.is_hot),
                no_member_discount=bool(src.no_member_discount),
                no_cooking=bool(src.no_cooking),
                visibility_rule=str(getattr(src, "visibility_rule", None) or "ALWAYS"),
                visibility_calendar=str(
                    getattr(src, "visibility_calendar", None) or "SOLAR"
                ),
                visible_from=getattr(src, "visible_from", None),
                visible_to=getattr(src, "visible_to", None),
                sort_order=src.sort_order,
                sales_count=0,
                stock=src.stock,
                box_price=src.box_price,
                min_order_count=src.min_order_count,
                require_category_ids=require_cats if src.require_category_ids is not None else None,
                require_product_ids=None,  # 二次映射
            )
            db.add(row)
            await db.flush()

            links = await cls._product_group_links(db, int(src.id))
            for link in links:
                gid = group_map.get(int(link.group_id), int(link.group_id))
                db.add(
                    cls._with_id(
                        ProductOptionGroup,
                        await cls._next_id(db, ProductOptionGroup),
                        product_id=row.id,
                        group_id=gid,
                        sort_order=link.sort_order,
                    )
                )
                await db.flush()

            imgs = list(
                (
                    await db.execute(
                        select(ProductImage)
                        .where(ProductImage.product_id == src.id)
                        .order_by(ProductImage.sort_order, ProductImage.id)
                    )
                )
                .scalars()
                .all()
            )
            for img in imgs:
                db.add(
                    cls._with_id(
                        ProductImage,
                        await cls._next_id(db, ProductImage),
                        product_id=row.id,
                        image_url=img.image_url,
                        image_type=img.image_type,
                        sort_order=img.sort_order,
                    )
                )
                await db.flush()
            id_map[int(src.id)] = int(row.id)
            existing_nos.add(product_no)
            summary["products"]["created"] += 1
            if renamed:
                summary["products"]["renamed"] += 1

        return id_map

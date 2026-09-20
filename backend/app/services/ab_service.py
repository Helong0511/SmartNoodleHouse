"""A/B 实验服务：稳定哈希分桶（幂等落库）、实验校验、效果报表聚合。"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.models import AbAssignment, AbExperiment, CustomerBehaviorEvent, OrderItem

logger = logging.getLogger(__name__)

DEFAULT_EXPERIMENT_KEY = "reco_algo"
STATUSES = {"DRAFT", "RUNNING", "PAUSED"}
ALLOWED_PARAM_KEYS = {"reco_weights"}
WEIGHT_KEYS = ("content", "cf", "popular", "novel")
_REPORT_EVENTS = ("EXPOSE", "CLICK", "CART_ADD", "ORDER_PAID")
_BUCKET_CODE_RE = re.compile(r"^[a-z0-9_]{1,40}$")
# Task 11：推荐位归因漏斗只统计携带桶位的推荐场景事件
RECO_SCENES = ("recommend", "settle_addon")
FUNNEL_WINDOW_HOURS = 24

DEFAULT_EXPERIMENT = {
    "experiment_key": DEFAULT_EXPERIMENT_KEY,
    "name": "推荐算法对照实验",
    "status": "DRAFT",
    "traffic_percent": 0,
    "default_bucket": "v1",
    "description": "v1=现行混合权重对照；v2=提高内容画像权重。运营在后台启用并配置流量。",
    "buckets": [
        {"code": "v1", "name": "对照组（现行权重）", "traffic": 50, "params": {}},
        {
            "code": "v2",
            "name": "实验组（口味内容加权）",
            "traffic": 50,
            "params": {"reco_weights": {"content": 0.62, "cf": 0.2, "popular": 0.1, "novel": 0.08}},
        },
    ],
}


def _hash_pct(*parts: Any) -> int:
    raw = "|".join(str(p) for p in parts)
    return int(hashlib.md5(raw.encode("utf-8")).hexdigest()[:8], 16) % 100


def validate_payload(data: dict) -> None:
    """校验实验 upsert 载荷，非法直接 ValueError（路由转 40001）。"""
    status = data.get("status", "DRAFT")
    if status not in STATUSES:
        raise ValueError("实验状态非法")
    traffic = int(data.get("traffic_percent", 0))
    if not 0 <= traffic <= 100:
        raise ValueError("流量百分比需在 0-100 之间")

    buckets = data.get("buckets") or []
    if not isinstance(buckets, list) or not 1 <= len(buckets) <= 4:
        raise ValueError("至少 1 个、至多 4 个桶")
    codes = []
    total_traffic = 0
    for b in buckets:
        if not isinstance(b, dict):
            raise ValueError("桶定义格式错误")
        code = str(b.get("code") or "").strip()
        if not _BUCKET_CODE_RE.match(code):
            raise ValueError("桶 code 仅支持小写字母数字下划线（≤40）")
        if code in codes:
            raise ValueError(f"桶 code 重复：{code}")
        codes.append(code)
        bt = int(b.get("traffic", 0))
        if bt < 0:
            raise ValueError("桶流量不能为负")
        total_traffic += bt
        params = b.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError("桶参数格式错误")
        for pk, pv in params.items():
            if pk not in ALLOWED_PARAM_KEYS:
                raise ValueError(f"不支持的实验参数：{pk}")
            if pk == "reco_weights":
                if not isinstance(pv, dict):
                    raise ValueError("reco_weights 必须是对象")
                for wk, wv in pv.items():
                    if wk not in WEIGHT_KEYS:
                        raise ValueError(f"非法权重键：{wk}")
                    if not isinstance(wv, (int, float)) or not 0 <= float(wv) <= 1:
                        raise ValueError(f"权重 {wk} 需在 0-1 之间")
    if total_traffic != 100:
        raise ValueError("桶内 traffic 总和必须为 100")
    if str(data.get("default_bucket") or "") not in codes:
        raise ValueError("默认桶必须是已定义桶之一")


def _pick_bucket(customer_id: int, key: str, buckets: list[dict]) -> dict:
    """哈希落入累积流量区间（bucket traffic 为桶内比例）。"""
    point = _hash_pct("bucket", customer_id, key)
    cumulative = 0
    for b in buckets:
        cumulative += int(b.get("traffic", 0))
        if point < cumulative:
            return b
    return buckets[-1]


class AbService:
    # ---------- 种子 ----------

    @staticmethod
    async def ensure_default_experiment(db) -> None:
        existing = await db.scalar(
            select(AbExperiment.id).where(
                AbExperiment.experiment_key == DEFAULT_EXPERIMENT_KEY
            ).limit(1)
        )
        if existing:
            return
        db.add(AbExperiment(**DEFAULT_EXPERIMENT))
        await db.flush()

    # ---------- 分桶 ----------

    @staticmethod
    async def assign(
        db, customer_id: int, experiment_key: str = DEFAULT_EXPERIMENT_KEY
    ) -> tuple[str, dict, AbExperiment | None]:
        """返回 (bucket_code, bucket_params, experiment)。

        DRAFT/PAUSED/流量外 → default 桶（不落库）；RUNNING 且落入门禁后幂等落库，桶位恒定。
        """
        exp = await db.scalar(
            select(AbExperiment).where(AbExperiment.experiment_key == experiment_key).limit(1)
        )
        if exp is None:
            return "v1", {}, None

        default_code = exp.default_bucket or "v1"
        buckets = exp.buckets or []
        default_params = next(
            (b.get("params") or {} for b in buckets if b.get("code") == default_code), {}
        )
        if exp.status != "RUNNING" or not int(exp.traffic_percent or 0):
            return default_code, default_params, exp
        if _hash_pct("traffic", customer_id, experiment_key) >= int(exp.traffic_percent):
            return default_code, default_params, exp

        row = await db.scalar(
            select(AbAssignment).where(
                AbAssignment.experiment_key == experiment_key,
                AbAssignment.customer_id == int(customer_id),
            ).limit(1)
        )
        if row is not None:
            params = next(
                (b.get("params") or {} for b in buckets if b.get("code") == row.bucket), None
            )
            # 桶被运营删除则回退默认
            return row.bucket, default_params if params is None else params, exp

        chosen = _pick_bucket(int(customer_id), experiment_key, buckets)
        row = AbAssignment(
            experiment_key=experiment_key,
            customer_id=int(customer_id),
            bucket=chosen["code"],
        )
        db.add(row)
        try:
            await db.flush()
        except IntegrityError:
            # 并发首次分桶：唯一约束兜底，重查既有桶位
            await db.rollback()
            row = await db.scalar(
                select(AbAssignment).where(
                    AbAssignment.experiment_key == experiment_key,
                    AbAssignment.customer_id == int(customer_id),
                ).limit(1)
            )
            code = row.bucket if row else chosen["code"]
            params = next(
                (b.get("params") or {} for b in buckets if b.get("code") == code),
                default_params,
            )
            return code, params, exp
        return chosen["code"], chosen.get("params") or {}, exp

    # ---------- CRUD ----------

    @staticmethod
    async def list_experiments(db) -> list[AbExperiment]:
        return list((await db.execute(
            select(AbExperiment).order_by(AbExperiment.id)
        )).scalars().all())

    @staticmethod
    async def get_experiment(db, key: str) -> AbExperiment | None:
        return await db.scalar(
            select(AbExperiment).where(AbExperiment.experiment_key == key).limit(1)
        )

    @staticmethod
    async def upsert_experiment(db, key: str, data: dict) -> AbExperiment:
        validate_payload(data)
        exp = await AbService.get_experiment(db, key)
        fields = ("name", "status", "traffic_percent", "default_bucket", "buckets", "description")
        if exp is None:
            exp = AbExperiment(experiment_key=key, **{k: data[k] for k in fields if k in data})
            db.add(exp)
        else:
            for k in fields:
                if k in data:
                    setattr(exp, k, data[k])
        await db.flush()
        await db.refresh(exp)
        return exp

    @staticmethod
    async def delete_experiment(db, key: str) -> bool:
        exp = await AbService.get_experiment(db, key)
        if exp is None:
            return False
        assignments = (await db.execute(
            select(AbAssignment).where(AbAssignment.experiment_key == key)
        )).scalars().all()
        for a in assignments:
            await db.delete(a)
        await db.delete(exp)
        await db.flush()
        return True

    # ---------- 报表 ----------

    @staticmethod
    async def experiment_report(
        db, key: str, date_from: datetime | None = None, date_to: datetime | None = None
    ) -> dict[str, Any]:
        """按事件 payload.ab_bucket 聚合（埋点由 Task 10 携带 ab_experiment/ab_bucket）。

        下单转化口径：窗口内对该桶有 EXPOSE 的顾客中，窗口内发生 ORDER_PAID 的顾客占比。
        """
        exp = await AbService.get_experiment(db, key)
        if exp is None:
            raise ValueError("实验不存在")
        buckets = exp.buckets or []
        stats: dict[str, dict[str, Any]] = {
            b["code"]: {
                "code": b["code"],
                "name": b.get("name", b["code"]),
                "exposed": 0,
                "clicks": 0,
                "cart_adds": 0,
                "exposed_users": set(),
                "click_users": set(),
                "cart_users": set(),
            }
            for b in buckets
        }

        assigned_counts: dict[str, int] = {}
        for a in (await db.execute(
            select(AbAssignment.bucket, AbAssignment.customer_id).where(
                AbAssignment.experiment_key == key
            )
        )).all():
            assigned_counts[a[0]] = assigned_counts.get(a[0], 0) + 1

        base_filters = [CustomerBehaviorEvent.created_at >= date_from] if date_from else []
        if date_to:
            base_filters.append(CustomerBehaviorEvent.created_at <= date_to)

        # 事件计数按 ab_bucket 列聚合（仅推荐位埋点携带桶位）
        count_q = (
            select(
                CustomerBehaviorEvent.event_type,
                CustomerBehaviorEvent.ab_bucket,
                func.count(),
            )
            .where(
                CustomerBehaviorEvent.event_type.in_(("EXPOSE", "CLICK", "CART_ADD")),
                CustomerBehaviorEvent.ab_bucket.is_not(None),
                *base_filters,
            )
            .group_by(CustomerBehaviorEvent.event_type, CustomerBehaviorEvent.ab_bucket)
        )
        for event_type, bucket, cnt in (await db.execute(count_q)).all():
            st = stats.get(bucket)
            if st is None:
                continue
            if event_type == "EXPOSE":
                st["exposed"] = int(cnt)
            elif event_type == "CLICK":
                st["clicks"] = int(cnt)
            elif event_type == "CART_ADD":
                st["cart_adds"] = int(cnt)

        # 桶 → 窗口内曝光顾客
        expose_user_q = (
            select(CustomerBehaviorEvent.ab_bucket, CustomerBehaviorEvent.customer_id)
            .where(
                CustomerBehaviorEvent.event_type == "EXPOSE",
                CustomerBehaviorEvent.ab_bucket.is_not(None),
                CustomerBehaviorEvent.customer_id.is_not(None),
                *base_filters,
            )
            .distinct()
        )
        for bucket, cid in (await db.execute(expose_user_q)).all():
            st = stats.get(bucket)
            if st is not None:
                st["exposed_users"].add(int(cid))

        # 窗口内付费顾客集合（ORDER_PAID 合成事件不带桶，按顾客与曝光桶关联）
        paid_q = (
            select(CustomerBehaviorEvent.customer_id)
            .where(
                CustomerBehaviorEvent.event_type == "ORDER_PAID",
                CustomerBehaviorEvent.customer_id.is_not(None),
                *base_filters,
            )
            .distinct()
        )
        paid_customers = {int(r[0]) for r in (await db.execute(paid_q)).all()}

        # Task 11：24h 同顾客同商品有序漏斗（EXPOSE→CLICK→CART_ADD→ORDER_PAID）与归因 GMV
        funnel_map = await AbService._attribution_funnel(
            db, set(stats.keys()), date_from, date_to
        )

        result_buckets = []
        for code, st in stats.items():
            exposed = st["exposed"]
            exposed_users = len(st["exposed_users"])
            paid_users = len(st["exposed_users"] & paid_customers)
            result_buckets.append(
                {
                    "code": code,
                    "name": st["name"],
                    "assigned": assigned_counts.get(code, 0),
                    "exposed": exposed,
                    "clicks": st["clicks"],
                    "cart_adds": st["cart_adds"],
                    "exposed_users": exposed_users,
                    "paid_users": paid_users,
                    "ctr": round(st["clicks"] / exposed, 4) if exposed else None,
                    "cart_rate": round(st["cart_adds"] / exposed, 4) if exposed else None,
                    "order_rate": round(paid_users / exposed_users, 4) if exposed_users else None,
                    "funnel": funnel_map.get(code),
                }
            )

        return {
            "experiment": {
                "key": exp.experiment_key,
                "name": exp.name,
                "status": exp.status,
                "traffic_percent": exp.traffic_percent,
                "default_bucket": exp.default_bucket,
            },
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
            "funnel_window_hours": FUNNEL_WINDOW_HOURS,
            "scene_scope": list(RECO_SCENES),
            "attribution_note": (
                f"漏斗按同顾客同商品有序匹配：曝光→点击→加购各步须在前一步后"
                f"{FUNNEL_WINDOW_HOURS}小时内发生；支付在加购后{FUNNEL_WINDOW_HOURS}小时内完成计为转化。"
                f"归因GMV=归因订单中该商品明细小计合计（未扣订单级优惠）。场景范围：{ '、'.join(RECO_SCENES) }。"
            ),
            "buckets": result_buckets,
        }

    # ---------- Task 11：推荐位 24h 有序漏斗 + GMV ----------

    @staticmethod
    async def _attribution_funnel(
        db, bucket_codes: set[str], date_from: datetime | None, date_to: datetime | None
    ) -> dict[str, dict[str, Any]]:
        """对每个桶按 (顾客, 商品) 维度做有序漏斗匹配并汇总归因 GMV。

        起点 EXPOSE 必须落在查询窗口 [date_from, date_to] 内；后续 CLICK/CART_ADD
        允许越出窗口右端一个 24h 步长，ORDER_PAID 允许两个步长，保证边界链路可归因。
        """
        window = timedelta(hours=FUNNEL_WINDOW_HOURS)
        if not bucket_codes:
            return {}

        ev_filters = [
            CustomerBehaviorEvent.event_type.in_(("EXPOSE", "CLICK", "CART_ADD")),
            CustomerBehaviorEvent.ab_bucket.in_(bucket_codes),
            CustomerBehaviorEvent.scene.in_(RECO_SCENES),
            CustomerBehaviorEvent.customer_id.is_not(None),
            CustomerBehaviorEvent.product_id.is_not(None),
        ]
        if date_from:
            ev_filters.append(CustomerBehaviorEvent.created_at >= date_from)
        if date_to:
            ev_filters.append(CustomerBehaviorEvent.created_at <= date_to + window)
        ev_rows = (
            await db.execute(
                select(
                    CustomerBehaviorEvent.ab_bucket,
                    CustomerBehaviorEvent.customer_id,
                    CustomerBehaviorEvent.product_id,
                    CustomerBehaviorEvent.event_type,
                    CustomerBehaviorEvent.created_at,
                )
                .where(*ev_filters)
                .order_by(
                    CustomerBehaviorEvent.ab_bucket,
                    CustomerBehaviorEvent.customer_id,
                    CustomerBehaviorEvent.product_id,
                    CustomerBehaviorEvent.created_at,
                )
            )
        ).all()

        grouped: dict[str, dict[int, dict[int, dict[str, list]]]] = {
            b: {} for b in bucket_codes
        }
        for bucket, cid, pid, etype, ts in ev_rows:
            cmap = grouped[bucket]
            slot = cmap.setdefault(int(cid), {}).setdefault(
                int(pid), {"EXPOSE": [], "CLICK": [], "CART_ADD": []}
            )
            slot[etype].append(ts)

        metrics: dict[str, dict[str, Any]] = {
            b: {"exposed_pairs": 0, "click_pairs": 0, "cart_pairs": 0,
                "paid_users": set(), "attr_items": set()}
            for b in bucket_codes
        }
        # (customer_id, product_id, cart_ts, bucket) 等待支付匹配
        pending: list[tuple[int, int, datetime, str]] = []
        for bucket, cmap in grouped.items():
            m = metrics[bucket]
            for cid, pmap in cmap.items():
                for pid, slot in pmap.items():
                    if not slot["EXPOSE"]:
                        continue
                    m["exposed_pairs"] += 1
                    t0 = min(slot["EXPOSE"])
                    t1 = next((t for t in sorted(slot["CLICK"]) if t0 <= t <= t0 + window), None)
                    if t1 is None:
                        continue
                    m["click_pairs"] += 1
                    t2 = next((t for t in sorted(slot["CART_ADD"]) if t1 <= t <= t1 + window), None)
                    if t2 is None:
                        continue
                    m["cart_pairs"] += 1
                    pending.append((cid, pid, t2, bucket))

        if pending:
            cids = {p[0] for p in pending}
            paid_filters = [
                CustomerBehaviorEvent.event_type == "ORDER_PAID",
                CustomerBehaviorEvent.customer_id.in_(cids),
                CustomerBehaviorEvent.product_id.is_not(None),
            ]
            if date_from:
                paid_filters.append(CustomerBehaviorEvent.created_at >= date_from)
            if date_to:
                paid_filters.append(CustomerBehaviorEvent.created_at <= date_to + window * 2)
            paid_rows = (
                await db.execute(
                    select(
                        CustomerBehaviorEvent.customer_id,
                        CustomerBehaviorEvent.product_id,
                        CustomerBehaviorEvent.created_at,
                        CustomerBehaviorEvent.payload,
                    ).where(*paid_filters)
                )
            ).all()
            paid_index: dict[tuple[int, int], list[tuple[datetime, Any]]] = {}
            for cid, pid, ts, payload in paid_rows:
                paid_index.setdefault((int(cid), int(pid)), []).append(
                    (ts, (payload or {}).get("order_id"))
                )
            for cid, pid, t2, bucket in pending:
                for ts, order_id in sorted(paid_index.get((cid, pid), []), key=lambda x: x[0]):
                    if order_id is None or not (t2 <= ts <= t2 + window):
                        continue
                    m = metrics[bucket]
                    item_key = (int(order_id), pid)
                    if item_key in m["attr_items"]:
                        break
                    m["attr_items"].add(item_key)
                    m["paid_users"].add(cid)
                    break

        all_items: set[tuple[int, int]] = set()
        for m in metrics.values():
            all_items |= m["attr_items"]
        gmv_lookup: dict[tuple[int, int], Decimal] = {}
        if all_items:
            oi_rows = (
                await db.execute(
                    select(OrderItem.order_id, OrderItem.product_id, OrderItem.subtotal).where(
                        or_(
                            *[
                                and_(OrderItem.order_id == oid, OrderItem.product_id == pid)
                                for oid, pid in all_items
                            ]
                        )
                    )
                )
            ).all()
            gmv_lookup = {(int(oid), int(pid)): Decimal(st or 0) for oid, pid, st in oi_rows}

        result: dict[str, dict[str, Any]] = {}
        for bucket, m in metrics.items():
            gmv = sum((gmv_lookup.get(k, Decimal(0)) for k in m["attr_items"]), Decimal(0))
            paid_user_count = len(m["paid_users"])
            exposed_pairs = m["exposed_pairs"]
            result[bucket] = {
                "exposed_pairs": exposed_pairs,
                "click_pairs": m["click_pairs"],
                "cart_pairs": m["cart_pairs"],
                "order_pairs": len(m["attr_items"]),
                "click_rate": round(m["click_pairs"] / exposed_pairs, 4) if exposed_pairs else None,
                "adopt_rate": round(m["cart_pairs"] / exposed_pairs, 4) if exposed_pairs else None,
                "order_conv": round(len(m["attr_items"]) / exposed_pairs, 4) if exposed_pairs else None,
                "attributed_paid_users": paid_user_count,
                "attributed_gmv": f"{gmv:.2f}",
                "gmv_per_paid_user": (
                    f"{(gmv / paid_user_count):.2f}" if paid_user_count else None
                ),
            }
        return result

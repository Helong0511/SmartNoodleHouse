"""产品级混合推荐服务（智能推荐 Task 7）。

四路召回：内容画像 / 订单共现 CF / 门店热销 / 新品运营；
三态：COLD（无事件或关闭个性化）、WARM（1-4 事件）、HOT（≥5 事件）；
多样性：同分类连续不超过 2 个；近期已购按分类窗口降权；每条结果带 reason。

架构约束：打分/排序/理由全部为纯函数（rank_candidates），IO 只在 RecommendationService，
离线评估（Task 14）直接构造输入调用纯函数。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.hot_cache import HotCache
from app.core.response import money, now_local
from app.models import (
    Customer,
    CustomerTasteProfile,
    Order,
    OrderItem,
    Product,
    ProductCategory,
    ProductTasteProfile,
    StoreProductStock,
)
from app.utils.birthday import is_birthday_today, normalize_calendar
from app.utils.product_visibility import is_product_visible

# ---------- 常量 ----------

HOT_EVENT_THRESHOLD = 5
DEFAULT_LIMIT = 10
MAX_SAME_CATEGORY_RUN = 2
RECENT_PURCHASE_LOOKBACK_DAYS = 30
CF_WINDOW_DAYS = 90
CF_TOP_K_PER_ITEM = 20
CF_SEED_LIMIT = 10
NOVEL_FRESH_DAYS = 30
# 活动 RUNNING 时对 boost_product_ids 的分数乘子（约 +25% 曝光倾向）
CAMPAIGN_BOOST_FACTOR = 1.25

# 三态默认路权（content/cf/popular/novel）
STATE_WEIGHTS: dict[str, dict[str, float]] = {
    "COLD": {"content": 0.0, "cf": 0.0, "popular": 0.7, "novel": 0.3},
    "WARM": {"content": 0.55, "cf": 0.15, "popular": 0.2, "novel": 0.1},
    "HOT": {"content": 0.5, "cf": 0.25, "popular": 0.15, "novel": 0.1},
}

# 维度键（dim:k=v）的理由文案：value 达到阈值才用
DIM_REASON: dict[str, tuple[str, int]] = {
    "spicy": ("您偏爱的辣口", 1),
    "sweetness": ("合您甜口", 1),
    "sourness": ("开胃酸香", 1),
    "numbing": ("川味麻香", 1),
}
# temperature 特殊：0 冰 / 1 凉 / 3 热
TEMP_REASON = {0: "冰爽冷饮", 1: "清凉口感", 3: "暖心热饮"}
TAG_REASON_MAP = {
    "麻辣": "您偏爱的麻辣口",
    "酸辣": "开胃酸辣口",
    "香辣": "您爱的香辣口",
    "牛肉": "牛肉爱好者",
    "红烧": "浓油赤酱红烧味",
    "番茄": "番茄浓汤风味",
    "藤椒": "藤椒鲜麻风味",
    "蒜蓉": "蒜香爱好者",
    "劲道": "劲道合您口感",
}

# 场景（季节/天气）对标签/温度的小幅乘子，作用于最终分，克制在 ±8%
# 天气 condition 与 weather_client / preference_scene 一致：hot/cold/rainy/mild
_SCENE_TAG_MULT = {
    "summer": {"ice": 1.08, "cool": 1.05, "hot_drink": 0.96, "mild": 1.04, "spicy": 0.97},
    "winter": {"ice": 0.95, "cool": 0.97, "hot_drink": 1.08, "soup": 1.05, "spicy": 1.05},
    "spring": {"cool": 1.03, "mild": 1.02},
    "autumn": {"soup": 1.03, "hot_drink": 1.03, "spicy": 1.03},
}
# 天气对餐品标签/温度维度；condition 须与 weather_client 一致（小写）
_WEATHER_TAG_MULT = {
    "rainy": {"soup": 1.06, "hot_drink": 1.05, "spicy": 1.04},
    "cold": {"hot_drink": 1.08, "soup": 1.06, "ice": 0.94, "spicy": 1.06},
    "hot": {"ice": 1.08, "cool": 1.05, "hot_drink": 0.95, "mild": 1.05, "spicy": 0.97},
    "mild": {},
}
_WEATHER_REASON = {
    "rainy": "雨天暖胃更合适",
    "cold": "天冷来点热的",
    "hot": "天气炎热更清爽",
}


# ---------- 纯数据结构 ----------


@dataclass
class PoolProduct:
    id: int
    name: str
    price: Decimal
    main_image: str | None
    category_id: int
    category_name: str
    is_hot: bool
    is_recommended: bool
    sales_count: int
    created_at: datetime | None
    stock: int
    tags: list[str] = field(default_factory=list)
    dimensions: dict[str, int] = field(default_factory=dict)
    visibility_rule: str = "ALWAYS"
    visibility_calendar: str = "SOLAR"
    visible_from: date | None = None
    visible_to: date | None = None

    @property
    def taste_keys(self) -> list[str]:
        keys = [f"tag:{t}" for t in self.tags]
        keys.extend(f"dim:{k}={v}" for k, v in self.dimensions.items())
        return keys


@dataclass
class ProfilePoint:
    score: float
    raw_weight: float
    event_count: int
    target_name: str | None = None
    last_event_at: datetime | None = None


@dataclass
class RankedItem:
    product: PoolProduct
    total: float
    reason: str
    state: str
    scores: dict[str, float]


# ---------- 纯函数核心 ----------


def customer_state(total_events: int, opt_in: bool) -> str:
    """三态：关闭个性化/无事件=COLD；1-4=WARM；≥HOT_EVENT_THRESHOLD=HOT。"""
    if not opt_in or total_events <= 0:
        return "COLD"
    if total_events >= HOT_EVENT_THRESHOLD:
        return "HOT"
    return "WARM"


def dedup_window_days(category_name: str, config: dict | None) -> int:
    """分类已购去重窗口：recommend_config 显式配置优先，否则按品类名推断。"""
    if config and isinstance(config.get("dedup_window_days"), int):
        return max(0, int(config["dedup_window_days"]))
    name = category_name or ""
    if any(w in name for w in ("饮", "茶", "咖啡", "奶茶", "果切")):
        return 1
    if any(w in name for w in ("面", "粉", "饭", "米线", "饺", "包")):
        return 7
    return 3  # 烘焙/零食/小食等


def merge_weights(
    state: str, config: dict | None, experiment_override: dict | None = None
) -> dict[str, float]:
    """三态默认路权 ← 分类 recommend_config.weights ← A/B 实验参数，归一化。"""
    w = dict(STATE_WEIGHTS.get(state, STATE_WEIGHTS["WARM"]))
    if config and isinstance(config.get("weights"), dict):
        for k in ("content", "cf", "popular", "novel"):
            v = config["weights"].get(k)
            if isinstance(v, (int, float)) and v >= 0:
                w[k] = float(v)
    # A/B 实验桶参数优先级最高，保证实验变量隔离干净
    if experiment_override:
        for k in ("content", "cf", "popular", "novel"):
            v = experiment_override.get(k)
            if isinstance(v, (int, float)) and v >= 0:
                w[k] = float(v)
    total = sum(w.values())
    if total > 0:
        w = {k: round(v / total, 6) for k, v in w.items()}
    return w


def _popular_score(p: PoolProduct, max_sales: int) -> float:
    base = math.log1p(max(0, p.sales_count)) / math.log1p(max(1, max_sales))
    if p.is_hot:
        base = min(1.0, base + 0.15)
    return round(min(1.0, max(0.0, base)), 4)


def _novel_score(p: PoolProduct, now: datetime) -> float:
    score = 0.6 if p.is_recommended else 0.0
    if p.created_at is not None:
        age_days = max(0.0, (now - _naive(p.created_at)).total_seconds() / 86400)
        if age_days <= NOVEL_FRESH_DAYS:
            score = max(score, 0.6 + 0.4 * (1 - age_days / NOVEL_FRESH_DAYS))
    return round(min(1.0, score), 4)


def _content_components(
    p: PoolProduct, profiles: dict[tuple[str, str], ProfilePoint]
) -> tuple[float, float, float, float, tuple[str, float] | None]:
    """返回 (category, tag, product, penalty, best_tag_signal)。无信号按中性 0.5。"""
    cat_pt = profiles.get(("CATEGORY", str(p.category_id)))
    cat = cat_pt.score if cat_pt else 0.5

    matched = [
        profiles[("TASTE_TAG", k)].score
        for k in p.taste_keys
        if ("TASTE_TAG", k) in profiles
    ]
    tag = sum(matched) / len(matched) if matched else 0.5

    prod_pt = profiles.get(("PRODUCT", str(p.id)))
    prod = prod_pt.score if prod_pt else 0.5

    penalty = 0.0
    if prod_pt and prod_pt.score < 0.4:
        penalty += 0.35
    strong_neg = [s for s in matched if s < 0.35]
    if strong_neg:
        penalty += 0.25 * (len(strong_neg) / max(1, len(matched)))
    penalty = min(0.6, penalty)

    # 最强正向标签信号（供 reason 使用）；best[0] 为 taste_key 字符串（tag:/dim:）
    best: tuple[str, float] | None = None
    for k in p.taste_keys:
        pt = profiles.get(("TASTE_TAG", k))
        if pt and pt.score > 0.5 and (best is None or pt.score > best[1]):
            best = (k, pt.score)
    return cat, tag, prod, penalty, best


def _tag_reason(key: str, target_name: str | None) -> str | None:
    if key.startswith("tag:"):
        tag = key[4:]
        if tag in TAG_REASON_MAP:
            return TAG_REASON_MAP[tag]
        if target_name:
            return f"合您喜欢的{target_name}"
        return None
    if key.startswith("dim:"):
        body = key[4:]
        if "=" not in body:
            return None
        dim, raw_v = body.split("=", 1)
        try:
            v = int(raw_v)
        except ValueError:
            return None
        if dim == "temperature":
            return TEMP_REASON.get(v)
        text, threshold = DIM_REASON.get(dim, (None, 0))
        if text and v >= threshold:
            return text
    return None


def _product_scene_flags(p: PoolProduct) -> dict[str, bool]:
    """餐品场景旗标：温度维 + 辣度维 + 汤类标签/品名。"""
    temp = p.dimensions.get("temperature")
    spicy = p.dimensions.get("spicy")
    is_ice = temp is not None and temp <= 0
    is_cool = temp == 1
    is_hot_drink = temp == 3
    # spicy: 0 不辣 / 1 微辣 / 2 中辣 / 3 特辣（与 taste_profile_service 一致）
    is_spicy = (spicy is not None and spicy >= 2) or any(
        t in (p.tags or []) for t in ("麻辣", "香辣", "重辣", "特辣")
    ) or any(w in (p.name or "") for w in ("麻辣", "香辣", "水煮", "冒椒"))
    is_mild = (spicy is not None and spicy <= 1) or any(
        t in (p.tags or []) for t in ("清淡", "不辣", "微辣")
    ) or any(w in (p.name or "") for w in ("清汤", "番茄", "原味"))
    return {
        "ice": is_ice,
        "cool": is_cool,
        "hot_drink": is_hot_drink,
        "soup": any(t in p.tags for t in ("汤", "浓汤", "汤面")) or "汤" in (p.name or ""),
        "spicy": bool(is_spicy),
        "mild": bool(is_mild) and not bool(is_spicy),
    }


def _scene_multiplier(p: PoolProduct, scene_ctx: dict[str, Any]) -> float:
    # 口味场景总开关关闭时不做季节/天气乘子
    if scene_ctx.get("scene_enabled") is False:
        return 1.0
    mult = 1.0
    flags = _product_scene_flags(p)
    season = scene_ctx.get("season")
    cond = str(scene_ctx.get("condition") or "").strip().lower()
    for flag, active in flags.items():
        if active:
            mult *= _SCENE_TAG_MULT.get(season, {}).get(flag, 1.0)
            mult *= _WEATHER_TAG_MULT.get(cond, {}).get(flag, 1.0)
    return max(0.9, min(1.12, mult))


def _weather_reason_for(p: PoolProduct, scene_ctx: dict[str, Any]) -> str | None:
    """天气乘子真正生效时返回可读理由，否则 None。"""
    if scene_ctx.get("scene_enabled") is False:
        return None
    if scene_ctx.get("weather_active") is False and not scene_ctx.get("condition"):
        return None
    cond = str(scene_ctx.get("condition") or "").strip().lower()
    if not cond or cond not in _WEATHER_REASON:
        return None
    weather_map = _WEATHER_TAG_MULT.get(cond) or {}
    if not weather_map:
        return None
    flags = _product_scene_flags(p)
    for flag, active in flags.items():
        if active and weather_map.get(flag, 1.0) != 1.0:
            return _WEATHER_REASON[cond]
    return None


def _pick_reason(
    p: PoolProduct,
    state: str,
    comps: tuple,
    scores: dict[str, float],
    profiles: dict[tuple[str, str], ProfilePoint],
    scene: str,
    scene_ctx: dict[str, Any] | None = None,
) -> str:
    cat_s, _tag_s, _prod_s, _penalty, best_tag = comps
    cat_pt = profiles.get(("CATEGORY", str(p.category_id)))

    # settle 搭配场景：跨品类优先讲搭配
    if scene == "settle_addon":
        if best_tag and best_tag[1] >= 0.62:
            pt = profiles.get(("TASTE_TAG", best_tag[0]))
            txt = _tag_reason(best_tag[0], pt.target_name if pt else None)
            if txt:
                return txt
        return "搭配加点"

    weather_txt = _weather_reason_for(p, scene_ctx or {})

    if state == "COLD":
        if weather_txt:
            return weather_txt
        if scores["novel"] >= 0.6:
            return "店长推荐" if p.is_recommended else "新品上市"
        if p.is_hot or scores["popular"] >= 0.6:
            return "本店热销"
        return "为您推荐"

    candidates: list[tuple[float, str]] = []
    prod_pt = profiles.get(("PRODUCT", str(p.id)))
    if prod_pt and prod_pt.score >= 0.6 and prod_pt.event_count >= 2:
        candidates.append((0.95, "您常点的"))
    if best_tag and best_tag[1] >= 0.6:
        pt = profiles.get(("TASTE_TAG", best_tag[0]))
        txt = _tag_reason(best_tag[0], pt.target_name if pt else None)
        if txt:
            candidates.append((0.9, txt))
    if weather_txt:
        candidates.append((0.85, weather_txt))
    if cat_pt and cat_pt.score >= 0.6:
        candidates.append((0.8, f"您常点的{cat_pt.target_name or p.category_name}"))
    if scores["cf"] >= 0.4:
        candidates.append((0.55, "点过的人也常点"))
    if p.is_hot or scores["popular"] >= 0.65:
        candidates.append((0.45, "本店热销"))
    if scores["novel"] >= 0.6:
        candidates.append((0.4, "店长推荐" if p.is_recommended else "新品上市"))
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]
    return "为您推荐"


def rank_candidates(
    pool: list[PoolProduct],
    *,
    profiles: dict[tuple[str, str], ProfilePoint],
    cf_scores: dict[int, float],
    recent_purchases: dict[int, datetime],
    configs_by_category: dict[int, dict | None],
    state: str,
    limit: int = DEFAULT_LIMIT,
    scene: str = "recommend_menu",
    exclude_ids: set[int] | None = None,
    scene_ctx: dict[str, Any] | None = None,
    now: datetime | None = None,
    cart_category_ids: set[int] | None = None,
    weight_override: dict[str, float] | None = None,
    boost_product_ids: set[int] | None = None,
    boost_factor: float = CAMPAIGN_BOOST_FACTOR,
) -> list[RankedItem]:
    """纯函数：打分 → 去重降权 → 排序 → 多样性重排 → 热销补齐。"""
    now_dt = _naive(now or datetime.utcnow())
    scene_ctx = scene_ctx or {}
    exclude = set(exclude_ids or [])
    cart_cats = set(cart_category_ids or [])
    boost_ids = set(boost_product_ids or [])
    candidates = [p for p in pool if p.id not in exclude]
    if not candidates or limit <= 0:
        return []

    max_sales = max((p.sales_count for p in candidates), default=0)
    max_cf = max(cf_scores.values(), default=1.0) or 1.0

    scored: list[RankedItem] = []
    for p in candidates:
        cfg = configs_by_category.get(p.category_id)
        weights = merge_weights(state, cfg, weight_override)
        comps = _content_components(p, profiles)
        s_content = (0.45 * comps[0] + 0.4 * comps[1] + 0.15 * comps[2]) * (1 - comps[3])
        s_cf = cf_scores.get(p.id, 0.0) / max_cf if cf_scores else 0.0
        s_popular = _popular_score(p, max_sales)
        s_novel = _novel_score(p, now_dt)
        total = (
            weights["content"] * s_content
            + weights["cf"] * s_cf
            + weights["popular"] * s_popular
            + weights["novel"] * s_novel
        )
        # 场景乘子（季节/天气，±8% 以内）
        total *= _scene_multiplier(p, scene_ctx)
        # settle_addon：跨购物车品类的搭配向加权，同品类降权
        if scene == "settle_addon" and cart_cats:
            total *= 0.7 if p.category_id in cart_cats else 1.15
        # 营销活动 RUNNING：目标 SKU 加权（活动结束后 boost 为空即消失）
        if p.id in boost_ids and boost_factor > 1.0:
            total *= float(boost_factor)
        # 近期已购：窗口内降权（候选不足时仍保留）
        last_buy = recent_purchases.get(p.id)
        in_window = False
        if last_buy is not None:
            window = dedup_window_days(p.category_name, cfg)
            in_window = (now_dt - _naive(last_buy)).days < window
        deprio = in_window
        if in_window:
            total *= 0.25
        scores = {
            "content": round(s_content, 4),
            "cf": round(s_cf, 4),
            "popular": round(s_popular, 4),
            "novel": round(s_novel, 4),
        }
        reason = _pick_reason(p, state, comps, scores, profiles, scene, scene_ctx)
        scored.append(RankedItem(p, round(total, 6), reason, state, scores))

    scored.sort(key=lambda r: r.total, reverse=True)

    # 窗口内已购：候选充足时直接剔除，不足则保留降权结果补位
    fresh = [r for r in scored if not _is_deprio(r, recent_purchases, configs_by_category, now_dt)]
    if len(fresh) >= limit:
        scored = fresh
    else:
        fresh_ids = {r.product.id for r in fresh}
        scored = fresh + [r for r in scored if r.product.id not in fresh_ids]

    # 多样性重排：同分类连续 ≤ 2
    picked = _diversify(scored, limit, MAX_SAME_CATEGORY_RUN)
    return picked


def _is_deprio(ranked: RankedItem, recent, configs, now_dt) -> bool:
    last_buy = recent.get(ranked.product.id)
    if last_buy is None:
        return False
    window = dedup_window_days(ranked.product.category_name, configs.get(ranked.product.category_id))
    return (now_dt - _naive(last_buy)).days < window


def _diversify(scored: list[RankedItem], limit: int, max_run: int) -> list[RankedItem]:
    remaining = list(scored)
    picked: list[RankedItem] = []
    while remaining and len(picked) < limit:
        chosen_idx = 0
        if picked:
            last_cat = picked[-1].product.category_id
            run = 0
            for r in reversed(picked):
                if r.product.category_id == last_cat:
                    run += 1
                else:
                    break
            if run >= max_run:
                for i, r in enumerate(remaining):
                    if r.product.category_id != last_cat:
                        chosen_idx = i
                        break
        picked.append(remaining.pop(chosen_idx))
    return picked


def _naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


# ---------- IO 层 ----------

POOL_TTL = 60.0
CF_TTL = 300.0
# 进程内热缓存（跨 worker 不共享；写路径主动 delete，其余靠 TTL）
_POOL_CACHE: HotCache[int, tuple[list[PoolProduct], dict[int, ProductCategory]]] = HotCache(
    "reco.pool", max_size=64, ttl_seconds=POOL_TTL
)
_CF_CACHE: HotCache[int, dict[int, dict[int, int]]] = HotCache(
    "reco.cf", max_size=64, ttl_seconds=CF_TTL
)


def invalidate_store_local_caches(store_id: int) -> None:
    """立即清理当前进程的推荐/菜单缓存；不访问 Redis，适用于事务内写路径。"""
    sid = int(store_id)
    _POOL_CACHE.delete(sid)
    _CF_CACHE.delete(sid)
    from app.services.menu_service import invalidate_local_menu_cache

    invalidate_local_menu_cache(sid)


async def invalidate_store_caches(store_id: int) -> bool:
    """清理本地推荐/菜单缓存并递增共享菜单版本。"""
    sid = int(store_id)
    invalidate_store_local_caches(sid)
    from app.core.menu_cache import bump_menu_version

    version = await bump_menu_version(sid)
    return version > 0


class RecommendationService:
    # ---------- 餐品池 ----------

    @staticmethod
    async def load_pool(
        db: AsyncSession, store_id: int, *, use_cache: bool = True
    ) -> tuple[list[PoolProduct], dict[int, ProductCategory]]:
        if use_cache:
            hit = _POOL_CACHE.get(int(store_id))
            if hit is not None:
                return hit[0], hit[1]

        stopped = StoreProductStock.__table__.alias("sps_stop")
        rows = await db.execute(
            select(Product, StoreProductStock.stock)
            .outerjoin(
                StoreProductStock,
                and_(
                    StoreProductStock.product_id == Product.id,
                    StoreProductStock.store_id == int(store_id),
                ),
            )
            .where(
                Product.store_id == int(store_id),
                Product.status == "ON_SALE",
                ~select(1)
                .select_from(stopped)
                .where(
                    stopped.c.product_id == Product.id,
                    stopped.c.store_id == int(store_id),
                    stopped.c.status != 1,
                )
                .exists(),
                func.coalesce(StoreProductStock.stock, Product.stock) > 0,
            )
            .order_by(Product.sort_order, Product.id)
        )
        products = [(p, ov) for p, ov in rows.all()]

        cat_rows = await db.execute(
            select(ProductCategory).where(ProductCategory.store_id == int(store_id))
        )
        cat_map = {c.id: c for c in cat_rows.scalars().all()}

        tp_rows = await db.execute(
            select(ProductTasteProfile).where(ProductTasteProfile.store_id == int(store_id))
        )
        tp_map = {t.product_id: t for t in tp_rows.scalars().all()}

        pool: list[PoolProduct] = []
        for p, stock_override in products:
            tp = tp_map.get(p.id)
            cat = cat_map.get(p.category_id)
            effective_stock = int(stock_override if stock_override is not None else (p.stock or 0))
            pool.append(
                PoolProduct(
                    id=int(p.id),
                    name=p.name,
                    price=money(p.price or 0),
                    main_image=p.main_image,
                    category_id=int(p.category_id),
                    category_name=cat.name if cat else "",
                    is_hot=bool(p.is_hot),
                    is_recommended=bool(p.is_recommended),
                    sales_count=int(p.sales_count or 0),
                    created_at=p.created_at,
                    stock=effective_stock,
                    tags=list(tp.tags or []) if tp else [],
                    dimensions=dict(tp.dimensions or {}) if tp else {},
                    visibility_rule=str(getattr(p, "visibility_rule", None) or "ALWAYS"),
                    visibility_calendar=str(
                        getattr(p, "visibility_calendar", None) or "SOLAR"
                    ),
                    visible_from=getattr(p, "visible_from", None),
                    visible_to=getattr(p, "visible_to", None),
                )
            )
        if use_cache:
            _POOL_CACHE.put(int(store_id), (pool, cat_map))
        return pool, cat_map

    # ---------- 顾客画像 ----------

    @staticmethod
    async def load_profiles(
        db: AsyncSession, customer_id: int
    ) -> tuple[dict[tuple[str, str], ProfilePoint], int]:
        rows = await db.execute(
            select(CustomerTasteProfile).where(
                CustomerTasteProfile.customer_id == int(customer_id)
            )
        )
        profiles: dict[tuple[str, str], ProfilePoint] = {}
        total_events = 0
        for r in rows.scalars().all():
            profiles[(r.dim_type, r.target_key)] = ProfilePoint(
                score=float(r.score or 0),
                raw_weight=float(r.raw_weight or 0),
                event_count=int(r.event_count or 0),
                target_name=r.target_name,
                last_event_at=r.last_event_at,
            )
            total_events += int(r.event_count or 0)
        return profiles, total_events

    # ---------- 共现 CF ----------

    @staticmethod
    async def _load_cooccurrence(
        db: AsyncSession, store_id: int, *, use_cache: bool = True
    ) -> dict[int, dict[int, int]]:
        """门店近 90 天已付订单 item 共现计数（双向、每 item 截断 top K）。"""
        if use_cache:
            hit = _CF_CACHE.get(int(store_id))
            if hit is not None:
                return hit
        since = datetime.utcnow() - timedelta(days=CF_WINDOW_DAYS)
        a = OrderItem.__table__.alias("oi_a")
        b = OrderItem.__table__.alias("oi_b")
        rows = await db.execute(
            select(a.c.product_id, b.c.product_id, func.count().label("c"))
            .join(b, and_(a.c.order_id == b.c.order_id, a.c.product_id < b.c.product_id))
            .join(Order, Order.id == a.c.order_id)
            .where(
                Order.store_id == int(store_id),
                Order.payment_status.in_(["PAID", "PARTIAL_REFUND"]),
                Order.paid_at >= since,
            )
            .group_by(a.c.product_id, b.c.product_id)
        )
        co: dict[int, dict[int, int]] = {}
        for qa, qb, cnt in rows.all():
            co.setdefault(int(qa), {})[int(qb)] = int(cnt)
            co.setdefault(int(qb), {})[int(qa)] = int(cnt)
        # 每 item 保留 top K 共现伙伴，控制内存
        trimmed = {
            q: dict(sorted(neighbors.items(), key=lambda x: x[1], reverse=True)[:CF_TOP_K_PER_ITEM])
            for q, neighbors in co.items()
        }
        if use_cache:
            _CF_CACHE.put(int(store_id), trimmed)
        return trimmed

    @staticmethod
    def cf_scores_from_seeds(
        seeds: list[int], co: dict[int, dict[int, int]], pool_ids: set[int]
    ) -> dict[int, float]:
        scores: dict[int, float] = {}
        for seed in seeds:
            for pid, cnt in co.get(int(seed), {}).items():
                if pid in pool_ids and pid not in seeds:
                    scores[pid] = scores.get(pid, 0.0) + float(cnt)
        return scores

    # ---------- 近期已购 ----------

    @staticmethod
    async def load_recent_purchases(
        db: AsyncSession, customer_id: int, store_id: int
    ) -> dict[int, datetime]:
        since = datetime.utcnow() - timedelta(days=RECENT_PURCHASE_LOOKBACK_DAYS)
        rows = await db.execute(
            select(OrderItem.product_id, func.max(Order.paid_at))
            .join(Order, Order.id == OrderItem.order_id)
            .where(
                Order.customer_id == int(customer_id),
                Order.store_id == int(store_id),
                Order.payment_status.in_(["PAID", "PARTIAL_REFUND"]),
                Order.paid_at >= since,
            )
            .group_by(OrderItem.product_id)
        )
        return {int(pid): paid for pid, paid in rows.all() if paid is not None}

    # ---------- 场景上下文 ----------

    @staticmethod
    async def load_scene_context(db: AsyncSession, store_id: int) -> dict[str, Any]:
        try:
            from app.services.preference_scene import build_scene_context

            ctx = await build_scene_context(db, store_id)
            return ctx if isinstance(ctx, dict) else {}
        except Exception:
            return {}

    @staticmethod
    async def load_campaign_boost_ids(db: AsyncSession, store_id: int) -> set[int]:
        """本店 RUNNING 活动按 code 门店覆盖总部后，合并 boost_product_ids。"""
        from app.services.marketing_service import MarketingService

        # 本店关闭营销活动时，不应用任何推荐加权（含总部活动）
        if not await MarketingService.is_enabled(db, int(store_id)):
            return set()
        camps = await MarketingService.resolve_running_campaigns(db, int(store_id))
        out: set[int] = set()
        for camp in camps:
            raw = getattr(camp, "boost_product_ids", None)
            if not raw:
                continue
            items = raw if isinstance(raw, list) else []
            for x in items:
                try:
                    out.add(int(x))
                except (TypeError, ValueError):
                    continue
        return out

    # ---------- 顶层入口 ----------

    @staticmethod
    async def recommend(
        db: AsyncSession,
        store_id: int,
        *,
        customer_id: int | None = None,
        scene: str = "recommend_menu",
        limit: int = DEFAULT_LIMIT,
        exclude_ids: list[int] | set[int] | None = None,
        use_cache: bool = True,
        weight_override: dict[str, float] | None = None,
    ) -> tuple[list[RankedItem], str, dict[str, Any]]:
        """返回 (推荐列表, 三态, meta)。未登录/opt_out 走 COLD 热销+新品。"""
        limit = max(1, min(int(limit or DEFAULT_LIMIT), 20))
        pool, cat_map = await RecommendationService.load_pool(db, store_id, use_cache=use_cache)
        if not pool:
            return [], "COLD", {"pool_size": 0}

        profiles: dict[tuple[str, str], ProfilePoint] = {}
        total_events = 0
        opt_in = False
        is_bday = False
        customer = None
        if customer_id:
            customer = await db.get(Customer, int(customer_id))
            opt_in = customer is not None and int(getattr(customer, "recommend_opt_in", 1) or 0) == 1
            if customer is not None:
                is_bday = is_birthday_today(
                    customer.birthday,
                    normalize_calendar(getattr(customer, "birthday_calendar", None)),
                )
            if opt_in:
                profiles, total_events = await RecommendationService.load_profiles(db, customer_id)

        # 与菜单一致：限时/年节/非今日生日不进推荐池（防推荐条偷露出）
        from app.utils.birthday import today_beijing

        today = today_beijing()
        pool = [
            p
            for p in pool
            if is_product_visible(p, today=today, is_birthday_today=is_bday)
        ]
        if not pool:
            return [], "COLD", {"pool_size": 0}
        pool_ids = {p.id for p in pool}

        state = customer_state(total_events, opt_in)
        cf_scores: dict[int, float] = {}
        recent: dict[int, datetime] = {}
        if customer_id and opt_in and state in ("WARM", "HOT"):
            # CF 种子：PRODUCT 画像中最强的若干正向单品
            seeds = [
                int(k[1])
                for k, pt in sorted(profiles.items(), key=lambda x: x[1].score, reverse=True)
                if k[0] == "PRODUCT" and pt.score > 0.5
            ][:CF_SEED_LIMIT]
            if seeds:
                co = await RecommendationService._load_cooccurrence(db, store_id, use_cache=use_cache)
                cf_scores = RecommendationService.cf_scores_from_seeds(seeds, co, pool_ids)
            recent = await RecommendationService.load_recent_purchases(db, customer_id, store_id)

        configs = {cid: (cat.recommend_config if cat else None) for cid, cat in cat_map.items()}
        scene_ctx = await RecommendationService.load_scene_context(db, store_id)
        boost_ids = await RecommendationService.load_campaign_boost_ids(db, store_id)

        cart_cats: set[int] = set()
        if scene == "settle_addon" and exclude_ids:
            cart_cats = {p.category_id for p in pool if p.id in set(exclude_ids)}

        ranked = rank_candidates(
            pool,
            profiles=profiles,
            cf_scores=cf_scores,
            recent_purchases=recent,
            configs_by_category=configs,
            state=state,
            limit=limit,
            scene=scene,
            exclude_ids=set(exclude_ids or []),
            scene_ctx=scene_ctx,
            cart_category_ids=cart_cats,
            weight_override=weight_override,
            boost_product_ids=boost_ids,
        )
        from app.services.preference_scene import scene_summary_label

        scene_on = scene_ctx.get("scene_enabled") is not False
        scene_view = {
            "season": scene_ctx.get("season") if scene_on else None,
            "condition": scene_ctx.get("condition") if scene_on else None,
            "temp_c": scene_ctx.get("temp_c") if scene_on else None,
            "condition_label": scene_ctx.get("condition_label") if scene_on else None,
            "weather_active": bool(scene_ctx.get("weather_active")) if scene_on else False,
            "weather_reason": scene_ctx.get("weather_reason"),
            "weather_affinity_hint": scene_ctx.get("weather_affinity_hint") if scene_on else None,
            "scene_enabled": scene_on,
            "weather_pref_enabled": bool(scene_ctx.get("weather_pref_enabled", True)),
            "enabled": scene_on,
        }
        scene_view["label"] = scene_ctx.get("label") or scene_summary_label(scene_view)
        return ranked, state, {
            "pool_size": len(pool),
            "total_events": total_events,
            "boost_product_ids": sorted(boost_ids),
            "scene": scene_view,
        }

"""Task 8 验收：推荐接口瘦字段、可选登录冷启动、场景校验、settle 排除、
Redis 缓存命中/失效/降级、下单后主动失效。"""
import fnmatch

import pytest

from app.api.miniprogram import get_recommendations
from app.core.exceptions import AppException
from app.models import CustomerTasteProfile
from app.services.recommendation_service import invalidate_store_caches
from app.services.taste_learning_service import TasteLearningService
from reco_factories import (
    make_category,
    make_customer,
    make_paid_order,
    make_product,
    make_store,
)

SLIM_FIELDS = {
    "id",
    "name",
    "price",
    "main_image",
    "category_id",
    "category_name",
    "taste_tags",
    "badges",
    "reason",
}


class FakeRedis:
    """最小内存 Redis：get/set/delete/scan_iter，足够 reco_cache 使用。"""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None, nx=False, xx=False, px=None):
        if nx and key in self.store:
            return None
        if xx and key not in self.store:
            return None
        self.store[key] = str(value)
        return True

    async def eval(self, script, numkeys, *args):
        keys = list(args[:numkeys])
        argv = list(args[numkeys:])
        if "redis.call('INCR'" in script and len(keys) == 1:
            generation = int(self.store.get(keys[0]) or 0) + 1
            self.store[keys[0]] = str(generation)
            return generation
        if "redis.call('SET', KEYS[2]" in script and len(keys) == 3:
            lock_key, cache_key, generation_key = keys
            token, raw, _ttl, expected_generation = argv
            current_generation = self.store.get(generation_key) or "0"
            if self.store.get(lock_key) == str(token) and current_generation == str(expected_generation):
                self.store[cache_key] = str(raw)
                return "OK"
            return None
        if "redis.call('GET', KEYS[1])" in script and len(keys) == 2:
            generation_key, cache_key = keys
            expected_generation = str(argv[0])
            if (self.store.get(generation_key) or "0") == expected_generation:
                return self.store.get(cache_key)
            return None
        if "redis.call('del'" in script.lower() and len(keys) == 1:
            if self.store.get(keys[0]) == str(argv[0]):
                return await self.delete(keys[0])
            return 0
        if "redis.call('expire'" in script.lower() and len(keys) == 1:
            return 1 if self.store.get(keys[0]) == str(argv[0]) else 0
        raise NotImplementedError("FakeRedis.eval 未覆盖该 Lua 脚本")

    async def delete(self, *keys):
        n = 0
        for k in keys:
            if k in self.store:
                del self.store[k]
                n += 1
        return n

    async def scan_iter(self, match, count=None):
        for k in list(self.store.keys()):
            if fnmatch.fnmatchcase(k, match):
                yield k


class BrokenRedis(FakeRedis):
    async def get(self, key):
        raise RuntimeError("redis down")

    async def set(self, key, value, ex=None, nx=False, xx=False, px=None):
        raise RuntimeError("redis down")

    async def scan_iter(self, match, count=None):
        raise RuntimeError("redis down")
        yield  # noqa: unreachable - 声明为 async generator


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()

    async def _get():
        return fake

    monkeypatch.setattr("app.services.reco_cache.get_redis", _get)
    return fake


async def _profile(db, cid, dim_type, key, score, event_count, target_name=None):
    db.add(CustomerTasteProfile(
        customer_id=cid,
        dim_type=dim_type,
        target_key=key,
        score=score,
        raw_weight=9.0,
        event_count=event_count,
        target_name=target_name,
    ))
    await db.flush()


async def _call(db, store, *, customer=None, scene="recommend_menu", limit=10, cart=None):
    resp = await get_recommendations(
        request=None,
        db=db,
        customer=customer,
        store_id=store.id,
        scene=scene,
        limit=limit,
        cart_item_ids=",".join(str(i) for i in cart) if cart else None,
    )
    return resp["data"]


# ---------- TR-8.1 接口形态 ----------

@pytest.mark.asyncio
async def test_anonymous_cold_start_slim_payload(db_session, fake_redis):
    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id, name="招牌面")
    await make_product(db, store.id, cat.id, name="麻辣小面", sales_count=120, is_hot=True)

    data = await _call(db, store)
    assert data["state"] == "COLD"
    assert data["ab"]["experiment"] == "reco_algo"
    assert data["ab"]["bucket"] == "v1"
    assert data["cache_hit"] is False
    assert data["items"], "冷启动也应有热销/新品兜底"
    for item in data["items"]:
        assert set(item.keys()) == SLIM_FIELDS
        assert item["reason"]
        assert isinstance(item["price"], str)


@pytest.mark.asyncio
async def test_logged_in_hot_customer_personalized(db_session, fake_redis):
    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id, name="招牌面")
    await make_product(db, store.id, cat.id, name="麻辣小面", sales_count=80)
    cust = await make_customer(db)
    await _profile(db, cust.id, "CATEGORY", str(cat.id), 0.9, 4, target_name="招牌面")
    await _profile(db, cust.id, "PRODUCT", "700001", 0.8, 3)
    await invalidate_store_caches(store.id)

    data = await _call(db, store, customer=cust)
    assert data["state"] == "HOT"


@pytest.mark.asyncio
async def test_invalid_scene_rejected(db_session, fake_redis):
    db = db_session
    store = await make_store(db)
    with pytest.raises(AppException) as exc:
        await get_recommendations(
            request=None, db=db, customer=None, store_id=store.id, scene="bogus"
        )
    assert exc.value.code == 40001


@pytest.mark.asyncio
async def test_settle_addon_excludes_cart_items(db_session, fake_redis):
    db = db_session
    store = await make_store(db)
    cat_n = await make_category(db, store.id, name="招牌面")
    cat_d = await make_category(db, store.id, name="饮品")
    p_cart = await make_product(db, store.id, cat_n.id, name="购物车中的面", sales_count=200, is_hot=True)
    p_other_noodle = await make_product(db, store.id, cat_n.id, name="另一款面", sales_count=100, is_hot=True)
    p_drink = await make_product(db, store.id, cat_d.id, name="柠檬茶", sales_count=60)

    data = await _call(
        db, store, scene="settle_addon", limit=10,
        cart=[p_cart.id, p_other_noodle.id],
    )
    ids = {i["id"] for i in data["items"]}
    assert p_cart.id not in ids
    assert p_other_noodle.id not in ids
    assert p_drink.id in ids


@pytest.mark.asyncio
async def test_limit_clamped(db_session, fake_redis):
    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id, name="招牌面")
    for i in range(6):
        await make_product(db, store.id, cat.id, name=f"面{i}", sales_count=10 + i)
    data = await _call(db, store, limit=999)
    assert len(data["items"]) <= 20


# ---------- TR-8.1 缓存命中/隔离/降级 ----------

@pytest.mark.asyncio
async def test_second_call_cache_hit(db_session, fake_redis):
    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id, name="招牌面")
    await make_product(db, store.id, cat.id, name="小面", sales_count=10)

    first = await _call(db, store)
    assert first["cache_hit"] is False
    assert fake_redis.store, "首次请求应写入 Redis"

    second = await _call(db, store)
    assert second["cache_hit"] is True
    assert second["items"] == first["items"]


@pytest.mark.asyncio
async def test_cart_fingerprint_separates_cache(db_session, fake_redis):
    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id, name="招牌面")
    p1 = await make_product(db, store.id, cat.id, name="面A", sales_count=30)
    p2 = await make_product(db, store.id, cat.id, name="面B", sales_count=20)

    d1 = await _call(db, store, scene="settle_addon", cart=[p1.id])
    assert d1["cache_hit"] is False
    # 不同购物车集合 → 不同指纹，不共享缓存
    d2 = await _call(db, store, scene="settle_addon", cart=[p2.id])
    assert d2["cache_hit"] is False
    # 同集合（顺序无关）→ 命中
    d3 = await _call(db, store, scene="settle_addon", cart=[p1.id])
    assert d3["cache_hit"] is True


@pytest.mark.asyncio
async def test_anonymous_vs_customer_cache_separated(db_session, fake_redis):
    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id, name="招牌面")
    await make_product(db, store.id, cat.id, name="小面", sales_count=10)
    cust = await make_customer(db)

    await _call(db, store)
    anon_hit = await _call(db, store)
    assert anon_hit["cache_hit"] is True

    cust_first = await _call(db, store, customer=cust)
    assert cust_first["cache_hit"] is False


@pytest.mark.asyncio
async def test_redis_failure_without_cache_fails_closed_to_prevent_db_stampede(db_session, monkeypatch):
    async def _get():
        return BrokenRedis()

    monkeypatch.setattr("app.services.reco_cache.get_redis", _get)
    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id, name="招牌面")
    await make_product(db, store.id, cat.id, name="小面", sales_count=10)

    # 推荐是非关键展示能力；Redis 不可用且本地无缓存时返回可重试 503，
    # 不允许每个 worker 都绕过分布式锁并发回源查询画像/订单。
    with pytest.raises(AppException) as exc:
        await _call(db, store)
    assert exc.value.code == 50301
    assert exc.value.status_code == 503


# ---------- TR-8.2 写后主动失效 ----------

@pytest.mark.asyncio
async def test_paid_order_invalidates_customer_cache(db_session, fake_redis):
    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id, name="招牌面")
    p = await make_product(db, store.id, cat.id, name="麻辣小面", sales_count=50)
    cust = await make_customer(db)

    first = await _call(db, store, customer=cust)
    assert first["cache_hit"] is False
    second = await _call(db, store, customer=cust)
    assert second["cache_hit"] is True

    # 下单支付 → record_paid_order_events 学习 + 主动失效
    order = await make_paid_order(db, cust.id, store.id, [(p, 1, [])])
    await TasteLearningService.record_paid_order_events(db, order.id)
    # 个性化缓存失效在业务事务提交后执行；未提交前不能推进共享代际。
    await db.commit()

    keys_left = list(fake_redis.store.keys())
    assert not any(f":c{cust.id}:" in k for k in keys_left), "支付后该顾客缓存应被清除"

    third = await _call(db, store, customer=cust)
    assert third["cache_hit"] is False

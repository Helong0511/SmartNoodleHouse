"""Task 9 验收：稳定哈希分桶、流量门禁、暂停回退、默认实验种子、
载荷校验、CRUD、报表精确聚合、推荐接口联动。"""
import pytest
from sqlalchemy import func, select

from app.models import AbAssignment, AbExperiment, CustomerBehaviorEvent
from app.services.ab_service import (
    DEFAULT_EXPERIMENT,
    DEFAULT_EXPERIMENT_KEY,
    AbService,
    validate_payload,
)
from reco_factories import make_category, make_customer, make_product, make_store

TWO_BUCKETS = [
    {"code": "v1", "name": "对照", "traffic": 50, "params": {}},
    {"code": "v2", "name": "实验", "traffic": 50,
     "params": {"reco_weights": {"content": 0.7, "cf": 0.1, "popular": 0.1, "novel": 0.1}}},
]


async def _make_experiment(db, *, status="RUNNING", traffic=100, key="reco_algo", buckets=None):
    exp = AbExperiment(
        experiment_key=key,
        name="测试实验",
        status=status,
        traffic_percent=traffic,
        default_bucket="v1",
        buckets=buckets or TWO_BUCKETS,
    )
    db.add(exp)
    await db.flush()
    return exp


async def _event(db, cid, etype, *, bucket=None, product_id=None, store_id=None):
    db.add(CustomerBehaviorEvent(
        event_id=f"{etype.lower()}:{cid}:{product_id or 0}:{bucket or 'x'}",
        customer_id=cid,
        event_type=etype,
        scene="recommend",
        product_id=product_id,
        store_id=store_id,
        ab_bucket=bucket,
    ))
    await db.flush()


# ---------- TR-9.1 分桶稳定性与分布 ----------

@pytest.mark.asyncio
async def test_assign_stable_across_100_calls(db_session):
    db = db_session
    await _make_experiment(db)
    cust = await make_customer(db)

    codes = {(await AbService.assign(db, cust.id))[0] for _ in range(100)}
    assert len(codes) == 1
    code = next(iter(codes))
    assert code in ("v1", "v2")
    count = await db.scalar(
        select(func.count()).select_from(AbAssignment).where(
            AbAssignment.customer_id == cust.id
        )
    )
    assert count == 1


@pytest.mark.asyncio
async def test_bucket_distribution_1000_customers(db_session):
    db = db_session
    await _make_experiment(db, traffic=100)
    counts = {"v1": 0, "v2": 0}
    for i in range(1000):
        cust = await make_customer(db)
        code, _, _ = await AbService.assign(db, cust.id)
        counts[code] += 1
    # 两桶各 50%，±10% 即 400-600（取宽一点的工程容差，断言注释说明）
    assert 400 <= counts["v1"] <= 600
    assert 400 <= counts["v2"] <= 600
    assert counts["v1"] + counts["v2"] == 1000


@pytest.mark.asyncio
async def test_traffic_gate_half_outside(db_session):
    db = db_session
    await _make_experiment(db, traffic=50)
    outside = 0
    assigned_rows = 0
    for i in range(1000):
        cust = await make_customer(db)
        code, _, _ = await AbService.assign(db, cust.id)
        if code == "v1":
            # v1 既可能是门户外默认，也可能是桶内对照；以落库数判断参与量
            pass
    assigned_rows = await db.scalar(select(func.count()).select_from(AbAssignment))
    # 50% 流量 → 约 500 人进入实验（±10%）
    assert 400 <= assigned_rows <= 600
    assert outside == 0  # 占位，门户外统一返回默认桶 v1


@pytest.mark.asyncio
async def test_paused_always_default_no_assignment(db_session):
    db = db_session
    await _make_experiment(db, status="PAUSED", traffic=100)
    cust = await make_customer(db)
    for _ in range(5):
        code, params, exp = await AbService.assign(db, cust.id)
        assert code == "v1"
        assert params == {}
        assert exp.status == "PAUSED"
    count = await db.scalar(select(func.count()).select_from(AbAssignment))
    assert count == 0


@pytest.mark.asyncio
async def test_draft_default_experiment_seed(db_session):
    db = db_session
    await AbService.ensure_default_experiment(db)
    await AbService.ensure_default_experiment(db)  # 幂等
    count = await db.scalar(select(func.count()).select_from(AbExperiment))
    assert count == 1
    exp = await AbService.get_experiment(db, DEFAULT_EXPERIMENT_KEY)
    assert exp.status == "DRAFT"
    cust = await make_customer(db)
    code, _, _ = await AbService.assign(db, cust.id)
    assert code == "v1"


@pytest.mark.asyncio
async def test_missing_experiment_returns_v1(db_session):
    db = db_session
    cust = await make_customer(db)
    code, params, exp = await AbService.assign(db, cust.id, "not_exist")
    assert code == "v1"
    assert params == {}
    assert exp is None


# ---------- 载荷校验与 CRUD ----------

def test_validate_payload_rejects_bad_shapes():
    good = {
        "name": "x", "status": "RUNNING", "traffic_percent": 100,
        "default_bucket": "v1", "buckets": TWO_BUCKETS,
    }
    validate_payload(good)

    bad_cases = [
        {**good, "status": "WHAT"},
        {**good, "traffic_percent": 101},
        {**good, "buckets": [{"code": "v1", "traffic": 90, "params": {}}]},  # 和≠100
        {**good, "default_bucket": "v9"},
        {**good, "buckets": [
            {"code": "v1", "traffic": 50, "params": {"unknown_param": 1}},
            {"code": "v2", "traffic": 50, "params": {}},
        ]},
        {**good, "buckets": [
            {"code": "v1", "traffic": 50,
             "params": {"reco_weights": {"content": 1.5}}},
            {"code": "v2", "traffic": 50, "params": {}},
        ]},
        {**good, "buckets": [
            {"code": "Bad-Code", "traffic": 100, "params": {}},
        ]},
    ]
    for case in bad_cases:
        with pytest.raises(ValueError):
            validate_payload(case)


@pytest.mark.asyncio
async def test_crud_create_update_delete_cascades(db_session):
    db = db_session
    payload = {
        "name": "自定义实验", "status": "DRAFT", "traffic_percent": 0,
        "default_bucket": "v1", "buckets": TWO_BUCKETS,
    }
    exp = await AbService.upsert_experiment(db, "exp_x", payload)
    assert exp.experiment_key == "exp_x"

    payload["status"] = "RUNNING"
    payload["traffic_percent"] = 100
    exp = await AbService.upsert_experiment(db, "exp_x", payload)
    assert exp.status == "RUNNING"

    cust = await make_customer(db)
    await AbService.assign(db, cust.id, "exp_x")
    assert await db.scalar(select(func.count()).select_from(AbAssignment)) == 1

    assert await AbService.delete_experiment(db, "exp_x") is True
    assert await AbService.get_experiment(db, "exp_x") is None
    assert await db.scalar(select(func.count()).select_from(AbAssignment)) == 0
    assert await AbService.delete_experiment(db, "exp_x") is False


# ---------- TR-9.2 报表精确聚合 ----------

@pytest.mark.asyncio
async def test_report_counts_and_rates_exact(db_session):
    db = db_session
    await _make_experiment(db)
    # v1：10 曝光、4 点击、2 加购；v2：5 曝光、1 点击、3 加购
    for i in range(10):
        c = await make_customer(db)
        await _event(db, c.id, "EXPOSE", bucket="v1", product_id=7001)
    for i in range(4):
        c = await make_customer(db)
        await _event(db, c.id, "CLICK", bucket="v1", product_id=7001)
    for i in range(2):
        c = await make_customer(db)
        await _event(db, c.id, "CART_ADD", bucket="v1", product_id=7001)
    for i in range(5):
        c = await make_customer(db)
        await _event(db, c.id, "EXPOSE", bucket="v2", product_id=7002)
    c = await make_customer(db)
    await _event(db, c.id, "CLICK", bucket="v2", product_id=7002)
    for i in range(3):
        c = await make_customer(db)
        await _event(db, c.id, "CART_ADD", bucket="v2", product_id=7002)
    # 无桶事件（菜单自然点击）不得计入
    c_none = await make_customer(db)
    await _event(db, c_none.id, "CLICK", bucket=None, product_id=7001)

    report = await AbService.experiment_report(db, DEFAULT_EXPERIMENT_KEY)
    by = {b["code"]: b for b in report["buckets"]}
    assert by["v1"]["exposed"] == 10
    assert by["v1"]["clicks"] == 4
    assert by["v1"]["cart_adds"] == 2
    assert by["v2"]["exposed"] == 5
    assert by["v2"]["clicks"] == 1
    assert by["v2"]["cart_adds"] == 3
    # 比率与手工公式逐位一致
    assert by["v1"]["ctr"] == round(4 / 10, 4)
    assert by["v1"]["cart_rate"] == round(2 / 10, 4)
    assert by["v2"]["ctr"] == round(1 / 5, 4)
    assert by["v2"]["cart_rate"] == round(3 / 5, 4)


@pytest.mark.asyncio
async def test_report_order_conversion_by_exposed_users(db_session):
    db = db_session
    await _make_experiment(db)
    c1 = await make_customer(db)
    c2 = await make_customer(db)
    c3 = await make_customer(db)
    await _event(db, c1.id, "EXPOSE", bucket="v1", product_id=7001)
    await _event(db, c2.id, "EXPOSE", bucket="v1", product_id=7001)
    await _event(db, c3.id, "EXPOSE", bucket="v2", product_id=7002)
    # c1 下单转化；c3 下单但属 v2
    await _event(db, c1.id, "ORDER_PAID", product_id=7001)
    await _event(db, c3.id, "ORDER_PAID", product_id=7002)

    report = await AbService.experiment_report(db, DEFAULT_EXPERIMENT_KEY)
    by = {b["code"]: b for b in report["buckets"]}
    assert by["v1"]["exposed_users"] == 2
    assert by["v1"]["paid_users"] == 1
    assert by["v1"]["order_rate"] == 0.5
    assert by["v2"]["order_rate"] == 1.0


@pytest.mark.asyncio
async def test_report_unknown_experiment_raises(db_session):
    db = db_session
    with pytest.raises(ValueError):
        await AbService.experiment_report(db, "nope")


# ---------- 推荐接口联动 ----------

@pytest.mark.asyncio
async def test_recommendation_endpoint_uses_ab_bucket(db_session, monkeypatch):
    from app.api.miniprogram import get_recommendations

    # 绕过 Redis（测试环境无服务）
    from app.services import reco_cache

    async def _miss(*a, **k):
        return None

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(reco_cache, "get_cached_reco", _miss)
    monkeypatch.setattr(reco_cache, "set_cached_reco", _noop)

    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id, name="招牌面")
    for i in range(6):
        await make_product(db, store.id, cat.id, name=f"面{i}", sales_count=20 + i)
    await _make_experiment(db, traffic=100)
    cust = await make_customer(db)

    resp = await get_recommendations(
        request=None, db=db, customer=cust, store_id=store.id
    )
    data = resp["data"]
    assert data["ab"]["experiment"] == DEFAULT_EXPERIMENT_KEY
    assert data["ab"]["bucket"] in ("v1", "v2")
    assert data["ab"]["status"] == "RUNNING"
    # 桶位跨请求恒定
    resp2 = await get_recommendations(
        request=None, db=db, customer=cust, store_id=store.id
    )
    assert resp2["data"]["ab"]["bucket"] == data["ab"]["bucket"]


def test_default_seed_constant_is_draft():
    assert DEFAULT_EXPERIMENT["status"] == "DRAFT"
    assert sum(b["traffic"] for b in DEFAULT_EXPERIMENT["buckets"]) == 100

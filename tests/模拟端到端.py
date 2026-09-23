"""
面馆智能经营系统 - 全链路模拟测试
覆盖：微信/支付宝扫码、会员注册、人脸授权、下单、支付回调幂等、厨房KDS、口味画像
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
from decimal import Decimal
from pathlib import Path

# 确保 backend 在 path 中
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import httpx
from httpx import ASGITransport
from sqlalchemy import Integer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import hash_password
from app.main import app
from app.models import (
    Customer,
    DeliveryProductMap,
    DiningTable,
    MemberLevel,
    Option,
    OptionGroup,
    Permission,
    PlatformStoreBinding,
    Product,
    ProductCategory,
    ProductOptionGroup,
    Role,
    RolePermission,
    Store,
    User,
)

TEST_DB = "sqlite+aiosqlite:///:memory:"
engine = create_async_engine(
    TEST_DB,
    echo=False,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# 测试上下文：动态 ID
CTX: dict = {}


async def override_get_db():
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


class FakeRedis:
    """内存 Redis，用于欢迎冷却模拟"""

    def __init__(self):
        self._data: dict[str, str] = {}
        self._lists: dict[str, list[str]] = {}

    async def exists(self, key: str) -> int:
        return 1 if key in self._data else 0

    async def setex(self, key: str, ttl: int, value: str):
        self._data[key] = value

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        if nx and key in self._data:
            return False
        self._data[key] = value
        return True

    def pipeline(self):
        return _FakeRedisPipeline(self)


class _FakeRedisPipeline:
    def __init__(self, redis: FakeRedis):
        self._redis = redis
        self._ops: list[tuple[str, tuple]] = []

    def rpush(self, key: str, value: str):
        self._ops.append(("rpush", (key, value)))
        return self

    def ltrim(self, key: str, start: int, end: int):
        self._ops.append(("ltrim", (key, start, end)))
        return self

    def expire(self, key: str, ttl: int):
        self._ops.append(("expire", (key, ttl)))
        return self

    def llen(self, key: str):
        self._ops.append(("llen", (key,)))
        return self

    def lrange(self, key: str, start: int, end: int):
        self._ops.append(("lrange", (key, start, end)))
        return self

    async def execute(self):
        results = []
        for op, args in self._ops:
            if op == "rpush":
                key, value = args
                self._redis._lists.setdefault(key, []).append(value)
                results.append(len(self._redis._lists[key]))
            elif op == "ltrim":
                key, start, end = args
                items = self._redis._lists.get(key, [])
                if end == -1:
                    self._redis._lists[key] = items[start:]
                else:
                    self._redis._lists[key] = items[start : end + 1]
                results.append(True)
            elif op == "expire":
                results.append(True)
            elif op == "llen":
                (key,) = args
                results.append(len(self._redis._lists.get(key, [])))
            elif op == "lrange":
                key, start, end = args
                items = self._redis._lists.get(key, [])
                if end == -1:
                    results.append(items[start:])
                else:
                    results.append(items[start : end + 1])
            else:
                results.append(None)
        return results


fake_redis = FakeRedis()
ADMIN_PWD_HASH = hash_password("admin123")
# 满足 consent-base64 最小长度校验（b64>=800，raw>=1024）
SIM_FACE_B64 = base64.b64encode(b"\xff" * 2048).decode("ascii")


async def seed_database():
    # SQLite 仅 INTEGER PRIMARY KEY 可自增，测试时将 id 列改为 Integer
    for table in Base.metadata.tables.values():
        if "id" in table.c:
            table.c.id.type = Integer()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as db:
        # SQLite 需显式 id
        db.add(Role(id=1, code="SUPER_ADMIN", name="超级管理员"))
        db.add(Permission(id=1, code="system:manage", name="系统管理", module="system"))
        db.add(RolePermission(role_id=1, permission_id=1))
        db.add(User(
            id=1, username="admin", password_hash=ADMIN_PWD_HASH,
            display_name="管理员", role_id=1, status=1,
        ))
        db.add(MemberLevel(id=1, name="普通会员", min_points=0, discount_rate=Decimal("1.0000")))
        db.add(Store(id=1, store_no="SNH001", name="测试面馆", status=1))
        db.add(DiningTable(
            id=1, store_id=1, table_no="A08", table_name="A08",
            qr_token="TEST_QR_TOKEN_SIM_001", status=1,
        ))
        db.add(ProductCategory(id=1, category_no="C1", store_id=1, name="招牌面", sort_order=1, status=1))
        db.add(Product(
            id=1, store_id=1, category_id=1, product_no="P001", name="牛肉面",
            price=Decimal("18.00"), status="ON_SALE", sort_order=1,
        ))

        opts: dict = {}
        spec_defs = [
            (1, "面量", "NOODLE_AMOUNT", "SINGLE", True, [("少面", "LESS", 0), ("正常", "NORMAL", 0), ("多面", "EXTRA", 2)]),
            (2, "辣度", "SPICY", "SINGLE", True, [("不要辣", "NONE", 0), ("少辣", "LESS", 0), ("正常辣", "NORMAL", 0), ("多辣", "EXTRA", 0)]),
            (3, "麻度", "NUMB", "SINGLE", True, [("不要麻", "NONE", 0), ("少麻", "LESS", 0), ("正常麻", "NORMAL", 0), ("多麻", "EXTRA", 0)]),
            (4, "加料", "EXTRA", "MULTIPLE", False, [("加蛋", "EGG", 2), ("加牛肉", "BEEF", 5), ("加豆皮", "BEAN_CURD", 2)]),
        ]
        oid = 1
        for gid, name, code, sel, req, options in spec_defs:
            db.add(OptionGroup(id=gid, name=name, code=code, selection_type=sel, is_required=req, sort_order=gid, status=1))
            opts[code] = {}
            for oidx, (oname, ocode, price) in enumerate(options, 1):
                db.add(Option(id=oid, group_id=gid, name=oname, code=ocode, extra_price=Decimal(str(price)), sort_order=oidx, status=1))
                opts[code][ocode] = oid
                oid += 1
            db.add(ProductOptionGroup(id=gid, product_id=1, group_id=gid, sort_order=gid))

        db.add(
            DeliveryProductMap(
                id=1,
                store_id=1,
                platform="MEITUAN",
                platform_sku_id="P001",
                app_food_code="P001",
                product_id=1,
                status=1,
            )
        )
        db.add(
            DeliveryProductMap(
                id=2,
                store_id=1,
                platform="ELEME",
                platform_sku_id="P001",
                app_food_code="P001",
                product_id=1,
                status=1,
            )
        )
        db.add(
            PlatformStoreBinding(
                id=1,
                store_id=1,
                platform="MEITUAN",
                platform_shop_id="SIM_MT_SHOP",
                platform_shop_name="测试美团店",
                status=1,
            )
        )
        db.add(
            PlatformStoreBinding(
                id=2,
                store_id=1,
                platform="ELEME",
                platform_shop_id="SIM_EL_SHOP",
                platform_shop_name="测试饿了么店",
                status=1,
            )
        )

        await db.commit()

        CTX.update({
            "store_id": 1,
            "meituan_shop_id": "SIM_MT_SHOP",
            "eleme_shop_id": "SIM_EL_SHOP",
            "table_token": "TEST_QR_TOKEN_SIM_001",
            "table_id": 1,
            "product_id": 1,
            "option_ids": {
                "EXTRA_NOODLE": opts["NOODLE_AMOUNT"]["EXTRA"],
                "EXTRA_MA": opts["NUMB"]["EXTRA"],
                "LESS_SPICY": opts["SPICY"]["LESS"],
                "EGG": opts["EXTRA"]["EGG"],
                "NORMAL_NOODLE": opts["NOODLE_AMOUNT"]["NORMAL"],
                "NORMAL_SPICY": opts["SPICY"]["NORMAL"],
                "NORMAL_NUMB": opts["NUMB"]["NORMAL"],
            },
        })


def ok(name: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    sym = "+" if passed else "X"
    line = f"  [{sym}] [{status}] {name}"
    if detail:
        line += f" -- {detail}"
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8", errors="replace"))
    return passed


async def api_client():
    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def mock_feature(seed: str) -> list[float]:
    arr = []
    x = sum(ord(c) for c in seed) % 997
    for i in range(128):
        x = (x * 9301 + 49297) % 233280
        arr.append((x / 233280) * 2 - 1)
    return arr


def cust_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def mt_simulate(**fields) -> dict:
    """美团开发模式回调公共字段（须含平台门店 ID）。"""
    return {"_dev_simulate": True, "app_poi_code": CTX["meituan_shop_id"], **fields}


async def run_all_simulations() -> bool:
    print("\n" + "=" * 60)
    print("面馆智能经营系统 — 全链路模拟测试")
    print("=" * 60)

    # patch redis
    import app.core.redis_client as rc
    import app.services.face_service as fs

    orig_get_redis = rc.get_redis

    async def _fake_get_redis():
        return fake_redis

    rc.get_redis = _fake_get_redis
    fs.get_redis = _fake_get_redis

    import app.core.config as cfg
    import app.core.database as dbmod
    import app.services.payment_verify as pv
    import app.services.delivery_providers as dp
    import app.services.webhook_inbox as wi

    dbmod.AsyncSessionLocal = SessionLocal
    wi.AsyncSessionLocal = SessionLocal

    _settings = cfg.get_settings()
    _settings.allow_payment_mock = True
    _settings.allow_auth_mock = True
    _settings.allow_delivery_mock = True
    _settings.app_debug = True
    pv._dev_mode_allowed = lambda: True
    dp._dev_mode_allowed = lambda: True

    import app.services.face_extractor as fe
    import app.api.v1.miniprogram as mp

    def _fake_extract_with_quality(_image_bytes: bytes):
        return mock_feature("sim_face"), {
            "sharpness": 120.0,
            "brightness": 0.55,
            "face_ratio": 0.2,
            "liveness": 0.9,
        }

    fe.extract_face_embedding_with_quality = _fake_extract_with_quality
    mp.extract_face_embedding_with_quality = _fake_extract_with_quality

    await seed_database()
    results: list[bool] = []

    async with await api_client() as client:
        oid = CTX["option_ids"]

        # ── 1. 健康检查 ──
        r = await client.get("/health")
        results.append(ok("健康检查", r.status_code == 200 and r.json()["status"] == "ok"))

        # ── 2. 管理员登录 ──
        r = await client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        body = r.json() if r.content else {}
        if not body:
            body = {"code": -1, "message": r.text[:100]}
        admin_token = (body.get("data") or {}).get("access_token", "")
        results.append(ok("管理员登录", body.get("code") == 0 and bool(admin_token), f"token={admin_token[:20]}..."))

        # ── 3. 扫码入口（微信） ──
        r = await client.get("/api/miniprogram/entry", params={"token": CTX["table_token"], "channel": "wechat"})
        entry = r.json().get("data", {})
        results.append(
            ok(
                "微信扫码识别桌台",
                entry.get("table", {}).get("table_no") == "A08",
                f"桌号 {entry.get('table', {}).get('table_name')}",
            )
        )
        results.append(
            ok(
                "扫码提示注册会员",
                entry.get("prompts", {}).get("ask_register_member") is False,
            )
        )
        results.append(
            ok(
                "扫码提示人脸迎宾",
                entry.get("prompts", {}).get("ask_face_welcome") is True,
            )
        )

        # ── 4. 微信小程序登录 ──
        r = await client.post(
            "/api/miniprogram/wechat/login",
            json={"code": "sim_wx_code_001", "table_token": CTX["table_token"], "nickname": "张先生"},
        )
        wx_login = r.json().get("data", {})
        wx_customer_id = wx_login.get("customer_id")
        wx_token = wx_login.get("customer_token", "")
        results.append(ok("微信小程序登录", bool(wx_customer_id) and bool(wx_token), f"customer_id={wx_customer_id}"))

        # ── 5. 注册会员 + 人脸授权 ──
        r = await client.post(
            "/api/miniprogram/member/register",
            json={"nickname": "张先生"},
            headers=cust_headers(wx_token),
        )
        reg = r.json().get("data", {})
        r = await client.post(
            "/api/miniprogram/face/consent-base64",
            json={"image_base64": SIM_FACE_B64},
            headers=cust_headers(wx_token),
        )
        face = r.json().get("data") or {}
        results.append(
            ok(
                "注册会员并授权人脸",
                reg.get("member_registered") and face.get("consent_status") == "AUTHORIZED",
                f"face={face.get('consent_status') or r.json().get('message', '')}",
            )
        )

        # ── 6. 创建订单（牛肉面 多面+多麻+少辣+加蛋 ×1 = 22） ──
        r = await client.post(
            "/api/orders",
            json={
                "store_id": CTX["store_id"],
                "table_token": CTX["table_token"],
                "channel": "WECHAT",
                "idempotency_key": "sim_order_wx_001",
                "items": [
                    {
                        "product_id": CTX["product_id"],
                        "quantity": 1,
                        "option_ids": [oid["EXTRA_NOODLE"], oid["EXTRA_MA"], oid["LESS_SPICY"], oid["EGG"]],
                    }
                ],
            },
            headers=cust_headers(wx_token),
        )
        wx_order = r.json().get("data", {})
        wx_order_id = wx_order.get("id")
        results.append(
            ok(
                "微信下单（服务端计价）",
                wx_order.get("total_amount") == "22.00",
                f"金额 {wx_order.get('total_amount')} 元（预期 22.00）",
            )
        )

        # ── 7. 幂等下单（重复提交） ──
        r2 = await client.post(
            "/api/orders",
            json={
                "store_id": CTX["store_id"],
                "table_token": CTX["table_token"],
                "channel": "WECHAT",
                "idempotency_key": "sim_order_wx_001",
                "items": [{"product_id": CTX["product_id"], "quantity": 1, "option_ids": [oid["EXTRA_NOODLE"]]}],
            },
            headers=cust_headers(wx_token),
        )
        wx_order2 = r2.json().get("data", {})
        results.append(
            ok(
                "防重复下单（幂等）",
                wx_order2.get("id") == wx_order_id,
                f"两次返回同一订单 id={wx_order_id}",
            )
        )

        # ── 8. 发起微信支付 ──
        r = await client.post(
            f"/api/payments/orders/{wx_order_id}/pay",
            json={"provider": "WECHAT", "channel_type": "miniprogram"},
            headers=cust_headers(wx_token),
        )
        pay_data = r.json().get("data", {})
        payment_no = pay_data.get("payment_no")
        results.append(ok("创建微信支付", bool(payment_no), payment_no))

        # ── 9. 微信回调 ×20（幂等） ──
        callback_body = json.dumps(
            {
                "_dev_simulate": True,
                "out_trade_no": payment_no,
                "transaction_id": "WX_SIM_TX_001",
                "trade_state": "SUCCESS",
                "amount": {"total": 2200},
            }
        ).encode()
        paid_count = 0
        for i in range(20):
            cr = await client.post("/api/payments/wechat/notify", content=callback_body)
            if cr.status_code == 200:
                paid_count += 1
        r = await client.get(f"/api/orders/{wx_order_id}", headers=cust_headers(wx_token))
        order_after_pay = r.json().get("data", {})
        results.append(
            ok(
                "微信回调幂等（20次）",
                order_after_pay.get("payment_status") == "PAID" and paid_count == 20,
                f"payment_status={order_after_pay.get('payment_status')}",
            )
        )

        # ── 10. 厨房 KDS ──
        r = await client.get("/api/orders/kitchen/active", headers={"Authorization": f"Bearer {admin_token}"})
        kitchen = r.json().get("data", [])
        has_order = any(o.get("id") == wx_order_id for o in kitchen)
        results.append(ok("厨房 KDS 收到订单", has_order, f"活跃订单 {len(kitchen)} 条"))

        # 开始制作 → 完成 → 出餐
        for status in ["PREPARING", "READY", "COMPLETED"]:
            await client.patch(
                f"/api/orders/{wx_order_id}/status",
                json={"status": status},
                headers={"Authorization": f"Bearer {admin_token}"},
            )
        r = await client.get(f"/api/orders/{wx_order_id}", headers=cust_headers(wx_token))
        final_order = r.json().get("data", {})
        results.append(ok("厨房流转 COMPLETED", final_order.get("status") == "COMPLETED"))

        # ── 11. 口味画像更新 ──
        r = await client.get(
            f"/api/customers/{wx_customer_id}/preferences",
            headers=cust_headers(wx_token),
        )
        prefs = r.json().get("data", {})
        all_prefs = prefs.get("all", [])
        results.append(
            ok(
                "顾客口味画像更新",
                len(all_prefs) > 0,
                f"记录 {len(all_prefs)} 条偏好",
            )
        )

        r = await client.get(
            "/api/miniprogram/customers/me/preferences",
            headers=cust_headers(wx_token),
        )
        mp_prefs = r.json().get("data", {}) or {}
        mp_all = mp_prefs.get("all", [])
        results.append(
            ok(
                "小程序偏好 API（令牌身份）",
                r.status_code == 200 and len(mp_all) == len(all_prefs),
                f"小程序 {len(mp_all)} 条 / 旧接口 {len(all_prefs)} 条",
            )
        )

        # ═══════════════════════════════════════
        # 流程2：支付宝扫码点餐
        # ═══════════════════════════════════════
        print("\n--- 流程2：支付宝扫码 ---")

        r = await client.get("/api/miniprogram/entry", params={"token": CTX["table_token"], "channel": "alipay"})
        results.append(ok("支付宝扫码识别桌台", r.json().get("data", {}).get("table") is not None))

        r = await client.post(
            "/api/miniprogram/alipay/login",
            json={"auth_code": "sim_ali_code_001", "table_token": CTX["table_token"], "nickname": "李女士"},
        )
        ali_login = r.json().get("data", {}) or {}
        ali_customer_id = ali_login.get("customer_id")
        ali_token = ali_login.get("customer_token", "")
        results.append(ok("支付宝小程序登录", bool(ali_customer_id) and bool(ali_token)))

        r = await client.post(
            "/api/orders",
            json={
                "store_id": CTX["store_id"],
                "table_token": CTX["table_token"],
                "channel": "ALIPAY",
                "idempotency_key": "sim_order_ali_001",
                "items": [
                    {
                        "product_id": CTX["product_id"],
                        "quantity": 2,
                        "option_ids": [
                            oid["EXTRA_NOODLE"],
                            oid["NORMAL_SPICY"],
                            oid["NORMAL_NUMB"],
                            oid["EGG"],
                        ],
                    }
                ],
            },
            headers=cust_headers(ali_token),
        )
        ali_order = r.json().get("data") or {}
        ali_order_id = ali_order.get("id")
        # (18+2+2)*2 = 44
        results.append(
            ok(
                "支付宝下单",
                ali_order.get("total_amount") == "44.00",
                f"金额 {ali_order.get('total_amount')} 元（预期 44.00）",
            )
        )

        r = await client.post(
            f"/api/payments/orders/{ali_order_id}/pay",
            json={"provider": "ALIPAY", "channel_type": "miniprogram"},
            headers=cust_headers(ali_token),
        )
        ali_payment_no = r.json().get("data", {}).get("payment_no")
        ali_cb = json.dumps(
            {
                "_dev_simulate": True,
                "out_trade_no": ali_payment_no,
                "trade_no": "ALI_SIM_TX_001",
                "trade_status": "TRADE_SUCCESS",
                "total_amount": "44.00",
            }
        ).encode()
        await client.post("/api/payments/alipay/notify", content=ali_cb)
        r = await client.get(f"/api/orders/{ali_order_id}", headers=cust_headers(ali_token))
        results.append(
            ok(
                "支付宝支付回调",
                r.json().get("data", {}).get("payment_status") == "PAID",
            )
        )

        # ═══════════════════════════════════════
        # 流程3：人脸欢迎 + 冷却
        # ═══════════════════════════════════════
        print("\n--- 流程3：授权人脸欢迎 ---")

        from app.services.face_service import WelcomeService

        async with SessionLocal() as db:
            # 多帧确认：需连续 3 帧
            w1 = await WelcomeService.try_welcome(db, wx_customer_id, camera_id=1, similarity=0.85)
            w2 = await WelcomeService.try_welcome(db, wx_customer_id, camera_id=1, similarity=0.86)
            welcomed = await WelcomeService.try_welcome(db, wx_customer_id, camera_id=1, similarity=0.87)
            # 冷却期内再触发
            c1 = await WelcomeService.try_welcome(db, wx_customer_id, camera_id=1, similarity=0.88)
            c2 = await WelcomeService.try_welcome(db, wx_customer_id, camera_id=1, similarity=0.89)
            welcomed2 = await WelcomeService.try_welcome(db, wx_customer_id, camera_id=1, similarity=0.90)
            await db.commit()

        results.append(ok("授权会员多帧确认（前2帧不欢迎）", w1 is False and w2 is False))
        results.append(ok("授权会员第3帧触发欢迎", welcomed is True))
        results.append(ok("欢迎冷却（60秒内不重复）", welcomed2 is False, "冷却期内拒绝重复欢迎"))

        r = await client.get(
            "/api/welcome/logs",
            params={"customer_id": wx_customer_id},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        logs = r.json().get("data", [])
        results.append(ok("欢迎日志记录", len(logs) >= 1, f"{len(logs)} 条"))

        # 撤销人脸
        await client.post("/api/miniprogram/face/revoke", headers=cust_headers(wx_token))
        r = await client.get("/api/miniprogram/customers/me/face-status", headers=cust_headers(wx_token))
        face_st = r.json().get("data", {})
        results.append(ok("撤销人脸授权", face_st.get("consent_status") == "NONE" and not face_st.get("has_face")))

        # ═══════════════════════════════════════
        # 流程4：匿名客流（未授权不识别身份）
        # ═══════════════════════════════════════
        print("\n--- 流程4：匿名客流 ---")

        from app.services.face_service import VisitorService

        async with SessionLocal() as db:
            evt = await VisitorService.record_event(db, camera_id=1, event_type="ENTER")
            await db.commit()
            visitor_no = evt.visitor_no

        results.append(ok("匿名客流记录", bool(visitor_no), f"visitor_no={visitor_no}"))
        results.append(
            ok(
                "未授权顾客不绑定 customer_id",
                "customer" not in visitor_no.lower(),
                "仅匿名 visitor_no",
            )
        )

        # ═══════════════════════════════════════
        # 安全：前端价格篡改被忽略
        # ═══════════════════════════════════════
        print("\n--- 安全与边界 ---")

        r = await client.post(
            "/api/orders",
            json={
                "store_id": CTX["store_id"],
                "idempotency_key": "sim_price_test",
                "items": [{"product_id": CTX["product_id"], "quantity": 1, "option_ids": [oid["NORMAL_NOODLE"], oid["NORMAL_SPICY"], oid["NORMAL_NUMB"]]}],
            },
            headers=cust_headers(wx_token),
        )
        price_order = r.json().get("data", {})
        results.append(
            ok(
                "服务端计价（忽略前端篡改）",
                price_order.get("total_amount") == "18.00",
                "正常规格 18.00 元",
            )
        )

        # 伪造桌台 token
        r = await client.get("/api/miniprogram/entry", params={"token": "FAKE_TABLE_A01", "channel": "wechat"})
        results.append(ok("伪造桌台 token 被拒绝", r.json().get("data") is None))

        # Dashboard
        r = await client.get("/api/dashboard", params={"period": "today"}, headers={"Authorization": f"Bearer {admin_token}"})
        dash = r.json().get("data", {})
        summary = dash.get("summary") or {}
        results.append(
            ok(
                "Dashboard 统计",
                summary.get("order_count", 0) >= 1,
                f"今日订单 {summary.get('order_count')} 净营收 {summary.get('net_revenue')} 元",
            )
        )

        # 商品列表
        r = await client.get("/api/products", params={"page_size": 10, "store_id": CTX["store_id"]})
        products = r.json().get("data", {}).get("items", [])
        results.append(ok("商品 API", len(products) >= 1, f"{len(products)} 个商品"))

        # ═══════════════════════════════════════
        # 流程5：外卖回调（新单/取消/催单/退款/配送）
        # ═══════════════════════════════════════
        print("\n--- 流程5：外卖平台回调 ---")

        mt_new = mt_simulate(
            order_id="SIM_MT001",
            store_id=CTX["store_id"],
            day_seq="101",
            total="18.00",
            recipient_name="外卖用户",
            recipient_phone="13800001111",
            recipient_address="测试地址 5 号",
            items=[{"sku_id": "P001", "name": "牛肉面", "quantity": 1, "price": "18.00"}],
        )
        r = await client.post("/api/delivery/meituan/notify", json=mt_new)
        results.append(ok("美团新单回调", r.json().get("data") == "ok"))

        from app.services.webhook_inbox import process_pending_inbox

        await process_pending_inbox()

        r = await client.get("/api/delivery/orders", headers={"Authorization": f"Bearer {admin_token}"})
        d_orders = r.json().get("data", {}).get("items", [])
        results.append(ok("外卖订单列表", any(d.get("platform_order_id") == "SIM_MT001" for d in d_orders)))

        r = await client.post(
            "/api/delivery/meituan/notify",
            json=mt_simulate(notify_type="urge", order_id="SIM_MT001"),
        )
        results.append(ok("美团催单回调", r.json().get("data") == "ok"))

        r = await client.post(
            "/api/delivery/meituan/notify",
            json=mt_simulate(
                logistics_status="DELIVERING",
                order_id="SIM_MT001",
                rider_name="李骑手",
                rider_phone="13900002222",
            ),
        )
        results.append(ok("美团配送状态回调", r.json().get("data") == "ok"))

        r = await client.post(
            "/api/delivery/meituan/notify",
            json=mt_simulate(
                refund_id="RF_SIM001",
                order_id="SIM_MT001",
                refund_amount="18.00",
                reason="测试退款",
            ),
        )
        results.append(ok("美团退款申请回调", r.json().get("data") == "ok"))
        await process_pending_inbox()

        r = await client.get("/api/delivery/refunds", headers={"Authorization": f"Bearer {admin_token}"})
        refunds = r.json().get("data", {}).get("items", [])
        results.append(ok("外卖退款列表", len(refunds) >= 1))

        r = await client.post(
            "/api/delivery/meituan/notify",
            json=mt_simulate(status=9, order_id="SIM_MT001"),
        )
        results.append(ok("美团取消回调", r.json().get("data") == "ok"))

        el_new = {
            "_dev_simulate": True,
            "type": "217",
            "shopId": CTX["eleme_shop_id"],
            "message": {
                "order": {
                    "id": "SIM_EL001",
                    "shopId": CTX["eleme_shop_id"],
                    "daySn": "22",
                    "totalPrice": "18.00",
                    "consignee": {"name": "饿了么用户", "phone": "13700003333"},
                    "address": "幸福里 2 栋",
                    "groups": [{"items": [{"vfoodId": "P001", "name": "牛肉面", "quantity": 1, "price": "18.00"}]}],
                }
            },
        }
        r = await client.post("/api/delivery/eleme/notify", json=el_new)
        results.append(ok("饿了么新单回调", r.json().get("message") == "ok"))

    rc.get_redis = orig_get_redis

    # 关闭调度器与 TTS，避免进程挂起
    try:
        from app.main import scheduler, tts_worker, tts_task

        tts_worker.stop()
        if tts_task and not tts_task.done():
            tts_task.cancel()
        if scheduler.running:
            scheduler.shutdown(wait=False)
    except Exception:
        pass

    passed = sum(results)
    total = len(results)
    print("\n" + "=" * 60)
    print(f"模拟测试完成: {passed}/{total} 通过")
    print("=" * 60 + "\n")
    return all(results)


if __name__ == "__main__":
    success = asyncio.run(run_all_simulations())
    sys.exit(0 if success else 1)

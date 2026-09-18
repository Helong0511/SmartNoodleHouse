import asyncio
import base64
import hashlib
import json
import logging
from datetime import datetime, timedelta

import numpy as np
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.biz_no import BIZ_VI, generate_no
from app.core.exceptions import AppException
from app.core.redis_client import get_redis
from app.core.response import now_local
from app.models import Camera, Customer, FaceProfile, Store, VisitorEvent, WelcomeLog
from app.services.face_extractor import EMBEDDING_DIM

logger = logging.getLogger(__name__)


def _fernet_from_material(material: str, *, legacy: bool = False) -> Fernet:
    raw = (material or "").encode("utf-8")
    if legacy:
        key = raw[:32].ljust(32, b"0")
    else:
        key = hashlib.sha256(raw).digest()
    return Fernet(base64.urlsafe_b64encode(key))


class FaceEncryption:
    """人脸向量加密。新数据用 SHA256 派生；解密兼容历史零填充密钥与 previous 密钥。"""

    CURRENT_VERSION_PREFIX = "enc1"

    def __init__(self):
        settings = get_settings()
        material = settings.face_encryption_key or ""
        self._material = material
        self.fernet = _fernet_from_material(material, legacy=False)
        previous_value = getattr(settings, "face_encryption_key_previous", None)
        prev = previous_value.strip() if isinstance(previous_value, str) else ""
        self._previous = _fernet_from_material(prev, legacy=False) if prev else None

    def encrypt(self, data: bytes) -> bytes:
        return self.fernet.encrypt(data)

    def decrypt(self, data: bytes) -> bytes:
        try:
            return self.fernet.decrypt(data)
        except InvalidToken:
            if self._previous is not None:
                try:
                    return self._previous.decrypt(data)
                except InvalidToken:
                    pass
            legacy = _fernet_from_material(self._material, legacy=True)
            return legacy.decrypt(data)

    @classmethod
    def key_version_label(cls) -> str:
        """含当前密钥指纹，轮换后旧档案 version 不匹配即可批量重加密。"""
        material = get_settings().face_encryption_key or ""
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:8]
        return f"{cls.CURRENT_VERSION_PREFIX}:{digest}"


class FaceEmbeddingCache:
    """授权人脸向量内存缓存：按 updated_at 增量刷新，避免识别循环全表扫解密。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._by_customer: dict[int, tuple[int, np.ndarray]] = {}  # customer_id -> (profile_id, vec)
        self._last_sync: datetime | None = None
        self._full_resync_every = 300.0  # 定期全量校正
        self._last_full_at = 0.0

    def invalidate(self) -> None:
        self._last_sync = None

    async def get_profiles(self, db: AsyncSession) -> list[tuple[int, np.ndarray]]:
        async with self._lock:
            await self._refresh(db)
            return [(cid, vec.copy()) for cid, (_pid, vec) in self._by_customer.items()]

    async def _refresh(self, db: AsyncSession) -> None:
        import time

        now_mono = time.monotonic()
        force_full = self._last_sync is None or (now_mono - self._last_full_at) >= self._full_resync_every
        enc = FaceEncryption()

        if force_full:
            # P0-18: join Customer 校验会员状态，仅缓存活跃会员的授权人脸
            result = await db.execute(
                select(FaceProfile)
                .join(Customer, Customer.id == FaceProfile.customer_id)
                .where(
                    FaceProfile.consent_status == "AUTHORIZED",
                    Customer.status == 1,
                    Customer.account_status == "ACTIVE",
                    Customer.member_status == "MEMBER",
                )
            )
            rows = list(result.scalars().all())
            fresh: dict[int, tuple[int, np.ndarray]] = {}
            newest: datetime | None = None
            for p in rows:
                vec = self._decrypt_vec(enc, p)
                if vec is None:
                    continue
                fresh[int(p.customer_id)] = (int(p.id), vec)
                ts = p.updated_at or p.consent_at
                if ts and (newest is None or ts > newest):
                    newest = ts
            self._by_customer = fresh
            self._last_sync = newest or now_local()
            self._last_full_at = now_mono
            return

        # 增量：updated_at 晚于等于上次同步的行（含撤销）；用 coalesce 兜底 NULL，
        # 避免 updated_at 为 NULL 的行在增量阶段永远刷不进缓存
        from sqlalchemy import func as sa_func

        result = await db.execute(
            select(FaceProfile).where(
                sa_func.coalesce(FaceProfile.updated_at, FaceProfile.created_at) >= self._last_sync  # type: ignore[operator]
            )
        )
        newest = self._last_sync
        # P0-18: 批量查这批 customer_id 对应的 Customer 状态
        changed_rows = list(result.scalars().all())
        cust_ids = {int(p.customer_id) for p in changed_rows}
        active_cust_ids: set[int] = set()
        if cust_ids:
            cust_rows = await db.execute(
                select(Customer.id).where(
                    Customer.id.in_(cust_ids),
                    Customer.status == 1,
                    Customer.account_status == "ACTIVE",
                    Customer.member_status == "MEMBER",
                )
            )
            active_cust_ids = {int(r[0]) for r in cust_rows.all()}

        for p in changed_rows:
            ts = p.updated_at or p.consent_at
            if ts and (newest is None or ts > newest):
                newest = ts
            cid = int(p.customer_id)
            # P0-18: 非活跃会员或已撤销授权 → 从缓存移除
            if p.consent_status != "AUTHORIZED" or not p.feature_data or cid not in active_cust_ids:
                self._by_customer.pop(cid, None)
                continue
            vec = self._decrypt_vec(enc, p)
            if vec is None:
                self._by_customer.pop(cid, None)
                continue
            self._by_customer[cid] = (int(p.id), vec)
        self._last_sync = newest

    @staticmethod
    def _decrypt_vec(enc: FaceEncryption, p: FaceProfile) -> np.ndarray | None:
        if not p.feature_data:
            return None
        try:
            vec = json.loads(enc.decrypt(p.feature_data).decode())
            arr = np.array(vec, dtype=np.float32)
            if arr.size != EMBEDDING_DIM:
                logger.warning(
                    "跳过维度不匹配的人脸档案 profile_id=%s dim=%s",
                    getattr(p, "id", None),
                    arr.size,
                )
                return None
            return arr
        except Exception:
            return None


face_embedding_cache = FaceEmbeddingCache()


class FaceService:
    """仅授权会员人脸匹配 - 禁止陌生顾客身份识别；禁止客户端直传 embedding。"""

    @staticmethod
    def _assert_embedding_dim(feature_vector: list[float]) -> None:
        if not isinstance(feature_vector, (list, tuple)) or len(feature_vector) != EMBEDDING_DIM:
            raise AppException(
                40031,
                f"人脸特征维度不匹配：期望 {EMBEDDING_DIM}（禁止 padding / 客户端伪造向量）",
            )

    @staticmethod
    async def _assert_customer_member(db: AsyncSession, customer_id: int) -> Customer:
        """P0-17: 人脸注册前校验顾客为活跃会员。

        要求：status==1 AND account_status=='ACTIVE' AND member_status=='MEMBER'
        非会员/禁用顾客禁止注册人脸。
        """
        cust = (
            await db.execute(
                select(Customer).where(Customer.id == customer_id).with_for_update()
            )
        ).scalar_one_or_none()
        if not cust:
            raise AppException(40031, "顾客不存在，禁止注册人脸")
        if cust.status != 1:
            raise AppException(40031, "顾客已禁用，禁止注册人脸")
        if (cust.account_status or "ACTIVE") != "ACTIVE":
            raise AppException(40031, "顾客账号非活跃状态，禁止注册人脸")
        if (cust.member_status or "GUEST") != "MEMBER":
            raise AppException(40031, "仅会员可注册人脸，非会员禁止")
        return cust

    @staticmethod
    async def register_face(db: AsyncSession, customer_id: int, feature_vector: list[float]) -> FaceProfile:
        FaceService._assert_embedding_dim(feature_vector)
        # P0-17: 校验顾客为活跃会员
        await FaceService._assert_customer_member(db, customer_id)
        # P0-19: SELECT FOR UPDATE 防并发注册（_assert_customer_member 已锁 Customer 行）
        # 撤销旧授权
        result = await db.execute(
            select(FaceProfile).where(
                FaceProfile.customer_id == customer_id, FaceProfile.consent_status == "AUTHORIZED"
            )
        )
        for old in result.scalars().all():
            old.consent_status = "REVOKED"
            old.revoked_at = now_local()
            old.feature_data = b""

        enc = FaceEncryption()
        feature_bytes = enc.encrypt(json.dumps(feature_vector).encode())
        profile = FaceProfile(
            customer_id=customer_id,
            feature_data=feature_bytes,
            feature_version=FaceEncryption.key_version_label(),
            consent_status="AUTHORIZED",
            consent_at=now_local(),
        )
        db.add(profile)
        await db.flush()
        face_embedding_cache.invalidate()
        return profile

    @staticmethod
    async def revoke_face(
        db: AsyncSession,
        customer_id: int,
        *,
        request=None,
        operator_id: int | None = None,
    ) -> int:
        result = await db.execute(
            select(FaceProfile).where(
                FaceProfile.customer_id == customer_id, FaceProfile.consent_status == "AUTHORIZED"
            )
        )
        profiles = list(result.scalars().all())
        ids: list[int] = []
        for profile in profiles:
            ids.append(int(profile.id))
            profile.consent_status = "REVOKED"
            profile.revoked_at = now_local()
            profile.feature_data = b""
        face_embedding_cache.invalidate()
        if ids:
            from app.services.audit_service import AuditService

            await AuditService.log_face_revoke(
                db,
                customer_id=customer_id,
                profile_ids=ids,
                request=request,
                operator_id=operator_id,
            )
        return len(ids)

    @staticmethod
    async def reencrypt_stale_profiles(db: AsyncSession, *, limit: int = 50) -> int:
        """将旧密钥/旧 version 的授权人脸重加密到当前密钥（密钥轮换后批量迁移）。"""
        target = FaceEncryption.key_version_label()
        result = await db.execute(
            select(FaceProfile)
            .where(
                FaceProfile.consent_status == "AUTHORIZED",
                FaceProfile.feature_data.isnot(None),
                FaceProfile.feature_version != target,
            )
            .order_by(FaceProfile.id.asc())
            .limit(max(1, min(int(limit), 200)))
        )
        rows = list(result.scalars().all())
        if not rows:
            return 0
        enc = FaceEncryption()
        done = 0
        for profile in rows:
            raw = profile.feature_data or b""
            if not raw:
                continue
            try:
                plain = enc.decrypt(raw)
                profile.feature_data = enc.encrypt(plain)
                profile.feature_version = target
                done += 1
            except Exception:
                logger.exception("人脸重加密失败 profile_id=%s", getattr(profile, "id", None))
        if done:
            face_embedding_cache.invalidate()
            await db.flush()
        return done

    @staticmethod
    async def get_authorized_profiles(db: AsyncSession) -> list[tuple[int, np.ndarray]]:
        return await face_embedding_cache.get_profiles(db)

    @staticmethod
    def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        if a.shape != b.shape or a.size != EMBEDDING_DIM:
            return 0.0
        na = np.linalg.norm(a)
        nb = np.linalg.norm(b)
        if na == 0 or nb == 0:
            return 0.0
        return float(np.dot(a, b) / (na * nb))


class VisitorService:
    @staticmethod
    async def record_event(
        db: AsyncSession,
        camera_id: int,
        event_type: str = "ENTER",
        snapshot_path: str | None = None,
    ) -> VisitorEvent:
        settings = get_settings()
        from app.services.system_settings_service import SystemSettingsService

        retention = await SystemSettingsService.get_int(db, "visitor_data_retention_days", settings.visitor_data_retention_days)
        expires = now_local() + timedelta(days=retention)
        cam = await db.get(Camera, camera_id)
        sno = None
        if cam and cam.store_id:
            store = await db.get(Store, cam.store_id)
            sno = store.store_no if store else None
        event = VisitorEvent(
            visitor_no=generate_no(BIZ_VI, sno, None),
            camera_id=camera_id,
            event_type=event_type,
            event_time=now_local(),
            snapshot_path=snapshot_path,
            expires_at=expires,
        )
        db.add(event)
        await db.flush()
        return event


class WelcomeService:
    _queue: asyncio.Queue | None = None

    @classmethod
    def get_queue(cls) -> asyncio.Queue:
        # P1-59: 有界队列防止 OOM — 最多积压 100 条迎宾任务
        if cls._queue is None:
            cls._queue = asyncio.Queue(maxsize=100)
        return cls._queue

    @staticmethod
    async def is_cooled_down(customer_id: int) -> bool:
        redis = await get_redis()
        key = f"snh:welcome:cooldown:{customer_id}"
        return await redis.exists(key) > 0

    @staticmethod
    async def _cooldown_seconds(
        db: AsyncSession | None = None, store_id: int | None = None
    ) -> int:
        settings = get_settings()
        from app.services.system_settings_service import SystemSettingsService

        seconds = settings.welcome_cooldown_seconds
        if db is not None:
            seconds = await SystemSettingsService.effective_int(
                db, "welcome_cooldown_seconds", seconds, store_id=store_id
            )
        return max(0, int(seconds))

    @staticmethod
    async def try_acquire_cooldown(
        customer_id: int,
        db: AsyncSession | None = None,
        store_id: int | None = None,
    ) -> bool:
        """原子冷却：SET key NX EX。抢到返回 True，已在冷却中返回 False。"""
        seconds = await WelcomeService._cooldown_seconds(db, store_id=store_id)
        if seconds <= 0:
            return True
        redis = await get_redis()
        key = f"snh:welcome:cooldown:{customer_id}"
        ok = await redis.set(key, "1", nx=True, ex=seconds)
        return bool(ok)

    @staticmethod
    async def set_cooldown(
        customer_id: int, db: AsyncSession | None = None, store_id: int | None = None
    ) -> None:
        """强制写入/刷新冷却（非原子抢占场景）。新迎宾路径请用 try_acquire_cooldown。"""
        seconds = await WelcomeService._cooldown_seconds(db, store_id=store_id)
        if seconds <= 0:
            return
        redis = await get_redis()
        await redis.set(f"snh:welcome:cooldown:{customer_id}", "1", ex=seconds)

    @staticmethod
    async def push_frame_score(
        customer_id: int,
        camera_id: int | None,
        similarity: float,
        db: AsyncSession | None = None,
        store_id: int | None = None,
    ) -> tuple[bool, float]:
        """Redis 多帧确认，跨进程共享。返回 (ready, avg_score)。"""
        settings = get_settings()
        from app.services.system_settings_service import SystemSettingsService

        confirm_frames = settings.face_confirm_frames
        if db is not None:
            confirm_frames = await SystemSettingsService.effective_int(
                db, "face_confirm_frames", confirm_frames, store_id=store_id
            )
        redis = await get_redis()
        key = f"snh:welcome:frames:{camera_id or 0}:{customer_id}"
        pipe = redis.pipeline()
        pipe.rpush(key, f"{similarity:.6f}")
        pipe.ltrim(key, -confirm_frames, -1)
        pipe.expire(key, 5)
        pipe.llen(key)
        pipe.lrange(key, 0, -1)
        _, _, _, length, values = await pipe.execute()
        scores = [float(v) for v in values]
        avg = sum(scores) / len(scores) if scores else 0.0
        return length >= confirm_frames, avg

    @classmethod
    async def try_welcome(
        cls,
        db: AsyncSession,
        customer_id: int,
        camera_id: int | None,
        similarity: float,
    ) -> bool:
        settings = get_settings()
        from app.services.system_settings_service import SystemSettingsService

        store_id: int | None = None
        if camera_id:
            cam = await db.get(Camera, camera_id)
            if cam:
                store_id = int(cam.store_id) if cam.store_id else None

        threshold = await SystemSettingsService.effective_float(
            db, "face_match_threshold", settings.face_match_threshold, store_id=store_id
        )
        ready, avg_sim = await cls.push_frame_score(
            customer_id, camera_id, similarity, db, store_id=store_id
        )
        if not ready:
            return False
        if avg_sim < threshold:
            return False
        # 先原子抢冷却，禁止 exists()+setex() 双步竞态导致重复迎宾
        if not await cls.try_acquire_cooldown(customer_id, db, store_id=store_id):
            return False

        customer = await db.get(Customer, customer_id)
        if not customer:
            return False

        name = customer.display_name or customer.nickname or "会员"
        text = f"欢迎回来，{name}。"
        try:
            from app.services.customer_service import PreferenceService

            rec = await PreferenceService.get_recommendations(db, customer_id, store_id=store_id)
            prefs = (rec or {}).get("preferences") or []
            if prefs:
                bits = []
                for p in prefs[:2]:
                    label = (p.get("label") or "").strip()
                    if not label:
                        cat = (p.get("category") or "").strip()
                        name = (p.get("option_name") or p.get("option_code") or "").strip()
                        label = f"{cat}·{name}" if cat and name else (name or cat)
                    if label:
                        bits.append(label)
                if bits:
                    text = f"欢迎回来，{name}。今天还是老样子：{'、'.join(bits)}？"
        except Exception:
            pass

        await cls.get_queue().put({"customer_id": customer_id, "text": text, "camera_id": camera_id})

        db.add(WelcomeLog(customer_id=customer_id, camera_id=camera_id, welcome_text=text))
        try:
            redis = await get_redis()
            await redis.delete(f"snh:welcome:frames:{camera_id or 0}:{customer_id}")
        except Exception:
            pass
        await db.flush()
        try:
            from app.services.websocket_manager import ws_manager

            await ws_manager.broadcast(
                "cameras",
                "WELCOME",
                {
                    "customer_id": customer_id,
                    "camera_id": camera_id,
                    "text": text,
                    "name": name,
                    "store_id": store_id,
                },
                store_id=store_id,
            )
        except Exception:
            pass
        return True

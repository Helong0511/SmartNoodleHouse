import asyncio
import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta

import numpy as np
from cryptography.fernet import InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.biz_no import BIZ_VI, generate_no
from app.core.cache_keys import (
    TTS_WELCOME_QUEUE_KEY,
    welcome_cooldown_key,
    welcome_frames_key,
)
from app.core.config import get_settings
from app.core.exceptions import AppException
from app.core.redis_client import get_redis
from app.core.response import now_local
from app.core.warmup_budget import face_cache_max_profiles
from app.models import (
    Camera,
    Customer,
    CustomerLifecycle,
    FaceProfile,
    MemberLevel,
    Store,
    VisitorEvent,
    WelcomeLog,
)
from app.services.face_extractor import EMBEDDING_DIM
from app.services.secret_encryption import _fernet_from_material

logger = logging.getLogger(__name__)


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
    """授权人脸向量热集（专用热缓存，非 HotCache LRU）。

    按消费活跃度 Top-N + 可用内存定容；增量按 updated_at；冷会员未命中时有界二次比对。
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._by_customer: dict[int, tuple[int, np.ndarray]] = {}  # customer_id -> (profile_id, vec)
        self._touch_at: dict[int, float] = {}  # monotonic；淘汰优先踢沉默者
        self._last_sync: datetime | None = None
        self._full_resync_every = 300.0
        self._last_full_at = 0.0
        self._cold_cooldown_until: dict[int, float] = {}  # camera_id -> monotonic
        self._max_profiles: int = 800

    def invalidate(self) -> None:
        self._last_sync = None

    def _resolve_max(self) -> int:
        s = get_settings()
        self._max_profiles = face_cache_max_profiles(
            override=int(getattr(s, "face_cache_max_profiles", 0) or 0)
        )
        return self._max_profiles

    @staticmethod
    def _authorized_base():
        return (
            select(FaceProfile)
            .join(Customer, Customer.id == FaceProfile.customer_id)
            .outerjoin(CustomerLifecycle, CustomerLifecycle.customer_id == Customer.id)
            .where(
                FaceProfile.consent_status == "AUTHORIZED",
                FaceProfile.feature_data.isnot(None),
                Customer.status == 1,
                Customer.account_status == "ACTIVE",
                Customer.member_status == "MEMBER",
            )
        )

    @staticmethod
    def _activity_score_expr():
        """近期消费阶梯 + 90 天频次 + RFM 微调（SQL 侧排序，避免全表进 Python）。"""
        from sqlalchemy import case, func, literal

        s = get_settings()
        recent_days = max(1, int(getattr(s, "face_cache_recent_days", 90) or 90))
        now = now_local()
        d7 = now - timedelta(days=7)
        d30 = now - timedelta(days=30)
        d_recent = now - timedelta(days=recent_days)
        recency = case(
            (Customer.last_order_at >= d7, literal(1000)),
            (Customer.last_order_at >= d30, literal(500)),
            (Customer.last_order_at >= d_recent, literal(200)),
            else_=literal(0),
        )
        freq = func.coalesce(CustomerLifecycle.order_count_90d, 0) * 10
        rfm = func.coalesce(CustomerLifecycle.f_score, 0) * 5 + func.coalesce(
            CustomerLifecycle.r_score, 0
        ) * 3
        return recency + freq + rfm

    async def warmup(self, db: AsyncSession) -> int:
        """启动按定容加载 Top-N 授权人脸；返回热集人数。"""
        async with self._lock:
            self._last_sync = None
            await self._refresh(db)
            n = len(self._by_customer)
            logger.info(
                "人脸向量缓存预热完成 profiles=%s max=%s",
                n,
                self._max_profiles,
            )
            return n

    async def get_profiles(
        self, db: AsyncSession, *, copy: bool = True
    ) -> list[tuple[int, np.ndarray]]:
        async with self._lock:
            await self._refresh(db)
            if copy:
                return [(cid, vec.copy()) for cid, (_pid, vec) in self._by_customer.items()]
            return [(cid, vec) for cid, (_pid, vec) in self._by_customer.items()]

    def touch(self, customer_id: int) -> None:
        import time

        cid = int(customer_id)
        if cid in self._by_customer:
            self._touch_at[cid] = time.monotonic()

    async def promote(self, db: AsyncSession, customer_id: int) -> bool:
        """将指定顾客授权人脸拉入热集（支付成功/冷命中）；超 cap 淘汰沉默者。"""
        cid = int(customer_id)
        async with self._lock:
            if cid in self._by_customer:
                self.touch(cid)
                return True
            row = (
                await db.execute(
                    self._authorized_base().where(FaceProfile.customer_id == cid).limit(1)
                )
            ).scalar_one_or_none()
            if not row:
                return False
            vec = self._decrypt_vec(FaceEncryption(), row)
            if vec is None:
                return False
            self._by_customer[cid] = (int(row.id), vec)
            self.touch(cid)
            self._evict_overflow()
            logger.info(
                "face_cache promote customer_id=%s hot=%s max=%s",
                cid,
                len(self._by_customer),
                self._max_profiles,
            )
            return True

    async def best_match(
        self,
        db: AsyncSession,
        embedding: np.ndarray,
        threshold: float,
        *,
        camera_id: int | None = None,
    ) -> tuple[int | None, float]:
        """热集比对；未达阈值且质量路径允许时做有界冷库二次匹配。"""
        import time

        emb = np.asarray(embedding, dtype=np.float32)
        async with self._lock:
            await self._refresh(db)
            best_id, best_sim = self._match_hot(emb)
            if best_id is not None and best_sim >= threshold:
                self.touch(best_id)
                return best_id, best_sim

            cam = int(camera_id) if camera_id is not None else None
            now_m = time.monotonic()
            if cam is not None:
                until = self._cold_cooldown_until.get(cam, 0.0)
                if now_m < until:
                    return best_id, best_sim
            s = get_settings()
            cooldown = float(getattr(s, "face_cache_cold_cooldown_seconds", 2.5) or 2.5)
            if cam is not None:
                self._cold_cooldown_until[cam] = now_m + max(0.5, cooldown)

            hot_ids = set(self._by_customer.keys())
            cold_id, cold_sim = await self._cold_match(db, emb, threshold, hot_ids)
            if cold_id is not None and cold_sim >= threshold:
                # promote 内部会再抢锁；此处已持锁，直接写入
                row = (
                    await db.execute(
                        self._authorized_base().where(FaceProfile.customer_id == cold_id).limit(1)
                    )
                ).scalar_one_or_none()
                if row is not None:
                    vec = self._decrypt_vec(FaceEncryption(), row)
                    if vec is not None:
                        self._by_customer[int(cold_id)] = (int(row.id), vec)
                        self.touch(int(cold_id))
                        self._evict_overflow()
                logger.info(
                    "face_cache cold_hit=1 customer_id=%s sim=%.4f hot=%s max=%s",
                    cold_id,
                    cold_sim,
                    len(self._by_customer),
                    self._max_profiles,
                )
                return int(cold_id), float(cold_sim)
            return best_id, best_sim

    def _match_hot(self, emb: np.ndarray) -> tuple[int | None, float]:
        best_id, best_sim = None, 0.0
        for cid, (_pid, vec) in self._by_customer.items():
            if vec.shape != emb.shape:
                continue
            sim = FaceService.cosine_similarity(emb, vec)
            if sim > best_sim:
                best_sim, best_id = sim, cid
        return best_id, best_sim

    async def _cold_match(
        self,
        db: AsyncSession,
        emb: np.ndarray,
        threshold: float,
        hot_ids: set[int],
    ) -> tuple[int | None, float]:
        s = get_settings()
        batch = max(50, int(getattr(s, "face_cache_cold_batch_size", 200) or 200))
        score = self._activity_score_expr()
        enc = FaceEncryption()
        best_id, best_sim = None, 0.0
        offset = 0
        # 最多扫 20 批，避免极端会员量打爆单帧
        for _ in range(20):
            q = self._authorized_base()
            if hot_ids:
                q = q.where(FaceProfile.customer_id.notin_(list(hot_ids)))
            q = q.order_by(score.desc(), Customer.last_order_at.desc(), FaceProfile.id.asc())
            q = q.offset(offset).limit(batch)
            rows = list((await db.execute(q)).scalars().all())
            if not rows:
                break
            for p in rows:
                vec = self._decrypt_vec(enc, p)
                if vec is None or vec.shape != emb.shape:
                    continue
                sim = FaceService.cosine_similarity(emb, vec)
                if sim > best_sim:
                    best_sim, best_id = sim, int(p.customer_id)
                if best_sim >= threshold:
                    return best_id, best_sim
            if len(rows) < batch:
                break
            offset += batch
        return best_id, best_sim

    def _evict_overflow(self) -> None:
        cap = self._resolve_max()
        while len(self._by_customer) > cap:
            # 无 touch 视为最旧；同 touch 任意踢一个
            victim = min(
                self._by_customer.keys(),
                key=lambda cid: self._touch_at.get(cid, 0.0),
            )
            self._by_customer.pop(victim, None)
            self._touch_at.pop(victim, None)

    async def _refresh(self, db: AsyncSession) -> None:
        import time

        now_mono = time.monotonic()
        force_full = self._last_sync is None or (now_mono - self._last_full_at) >= self._full_resync_every
        enc = FaceEncryption()
        cap = self._resolve_max()

        if force_full:
            score = self._activity_score_expr()
            result = await db.execute(
                self._authorized_base()
                .order_by(score.desc(), Customer.last_order_at.desc(), FaceProfile.id.asc())
                .limit(cap)
            )
            rows = list(result.scalars().all())
            fresh: dict[int, tuple[int, np.ndarray]] = {}
            newest: datetime | None = None
            for p in rows:
                vec = self._decrypt_vec(enc, p)
                if vec is None:
                    continue
                cid = int(p.customer_id)
                fresh[cid] = (int(p.id), vec)
                ts = p.updated_at or p.consent_at
                if ts and (newest is None or ts > newest):
                    newest = ts
            # 保留仍在热集内的 touch
            self._touch_at = {cid: t for cid, t in self._touch_at.items() if cid in fresh}
            self._by_customer = fresh
            self._last_sync = newest or now_local()
            self._last_full_at = now_mono
            logger.debug(
                "face_cache full_refresh hot=%s max=%s",
                len(self._by_customer),
                cap,
            )
            return

        from sqlalchemy import func as sa_func

        result = await db.execute(
            select(FaceProfile).where(
                sa_func.coalesce(FaceProfile.updated_at, FaceProfile.created_at) >= self._last_sync  # type: ignore[operator]
            )
        )
        newest = self._last_sync
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
            if p.consent_status != "AUTHORIZED" or not p.feature_data or cid not in active_cust_ids:
                self._by_customer.pop(cid, None)
                self._touch_at.pop(cid, None)
                continue
            # 增量：仅更新已在热集中的；新人靠 promote / 下次全量 / 冷命中进入
            if cid not in self._by_customer and len(self._by_customer) >= cap:
                continue
            vec = self._decrypt_vec(enc, p)
            if vec is None:
                self._by_customer.pop(cid, None)
                self._touch_at.pop(cid, None)
                continue
            self._by_customer[cid] = (int(p.id), vec)
            if cid not in self._touch_at:
                self._touch_at[cid] = now_mono
        self._evict_overflow()
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
        """将旧密钥/旧 version 的授权人脸重加密到当前密钥（密钥轮换后批量迁移）。

        先捞 id，再逐条短锁重加密，避免一次锁住多行。
        """
        target = FaceEncryption.key_version_label()
        ids = list(
            (
                await db.execute(
                    select(FaceProfile.id)
                    .where(
                        FaceProfile.consent_status == "AUTHORIZED",
                        FaceProfile.feature_data.isnot(None),
                        FaceProfile.feature_version != target,
                    )
                    .order_by(FaceProfile.id.asc())
                    .limit(max(1, min(int(limit), 200)))
                )
            )
            .scalars()
            .all()
        )
        if not ids:
            return 0
        enc = FaceEncryption()
        done = 0
        for pid in ids:
            try:
                profile = (
                    await db.execute(
                        select(FaceProfile)
                        .where(
                            FaceProfile.id == int(pid),
                            FaceProfile.consent_status == "AUTHORIZED",
                            FaceProfile.feature_data.isnot(None),
                            FaceProfile.feature_version != target,
                        )
                        .with_for_update(skip_locked=True)
                    )
                ).scalar_one_or_none()
                if not profile:
                    await db.commit()
                    continue
                raw = profile.feature_data or b""
                if not raw:
                    await db.commit()
                    continue
                plain = enc.decrypt(raw)
                profile.feature_data = enc.encrypt(plain)
                profile.feature_version = target
                await db.commit()
                done += 1
            except Exception:
                await db.rollback()
                logger.exception("人脸重加密失败 profile_id=%s", pid)
        if done:
            face_embedding_cache.invalidate()
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
    # 同摄像头陌生客短时复用访客号（进程内；多 worker 不共享，可接受）
    _UNKNOWN_TRACK_TTL_SEC = 14400.0  # 4 小时：同人临时客尽量同一访客号
    _UNKNOWN_REUSE_MIN_SIM = 0.42
    _unknown_tracks: dict[int, list[dict]] = {}
    _last_unknown_welcome_at: dict[int, float] = {}
    _last_unknown_cd_log_at: dict[int, float] = {}

    @staticmethod
    def uploads_url_to_key(url: str | None) -> str | None:
        s = str(url or "").strip().replace("\\", "/")
        if not s:
            return None
        # 完整 URL：http(s)://host/uploads/visitors/...
        if "://" in s:
            marker = "/uploads/"
            idx = s.find(marker)
            if idx >= 0:
                s = s[idx + len(marker) :]
            else:
                return None
        elif s.startswith("/uploads/"):
            s = s[len("/uploads/") :]
        elif s.startswith("uploads/"):
            s = s[len("uploads/") :]
        # 已是相对对象键
        if s.startswith("visitors/"):
            return s
        return None

    @staticmethod
    def make_snapshot_key(camera_id: int, *, ext: str = "jpg") -> str:
        """按日期分目录：visitors/YYYY/MM/DD/cam{id}_HHMMSS_xxxx.jpg，便于按天清理。"""
        from app.core.response import now_local

        now = now_local()
        stamp = now.strftime("%H%M%S")
        day = now.strftime("%Y/%m/%d")
        return f"visitors/{day}/cam{int(camera_id)}_{stamp}_{uuid.uuid4().hex[:8]}.{ext.lstrip('.')}"

    @staticmethod
    def encode_frame_jpeg(frame, *, quality: int = 85) -> bytes:
        import cv2

        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
        if not ok or buf is None:
            raise ValueError("抓拍帧编码失败")
        return buf.tobytes()

    @classmethod
    async def save_frame_snapshot(cls, frame, camera_id: int) -> str:
        """保存摄像头帧为 JPEG，返回 /uploads/visitors/年/月/日/... 路径。"""
        import asyncio

        from app.services.object_storage import get_storage

        data = await asyncio.to_thread(cls.encode_frame_jpeg, frame)
        key = cls.make_snapshot_key(camera_id, ext="jpg")
        return await get_storage().put_bytes(key, data, content_type="image/jpeg")

    @classmethod
    def _prune_unknown_tracks(cls, camera_id: int, now: float) -> list[dict]:
        tracks = cls._unknown_tracks.get(int(camera_id)) or []
        kept = [t for t in tracks if now - float(t.get("ts") or 0) <= cls._UNKNOWN_TRACK_TTL_SEC]
        cls._unknown_tracks[int(camera_id)] = kept
        return kept

    @classmethod
    def resolve_visitor_no(
        cls,
        camera_id: int,
        *,
        store_no: str | None,
        embedding: np.ndarray | None = None,
        reuse_min_sim: float | None = None,
    ) -> str:
        """同摄像头短时内相似人脸复用访客号；否则新号。"""
        import time as _time

        now = _time.time()
        min_sim = float(reuse_min_sim if reuse_min_sim is not None else cls._UNKNOWN_REUSE_MIN_SIM)
        min_sim = max(0.35, min(0.95, min_sim))
        if embedding is not None:
            emb = np.asarray(embedding, dtype=np.float32)
            tracks = cls._prune_unknown_tracks(camera_id, now)
            best_no, best_sim = None, 0.0
            for t in tracks:
                vec = t.get("emb")
                if vec is None:
                    continue
                sim = FaceService.cosine_similarity(emb, np.asarray(vec, dtype=np.float32))
                if sim > best_sim:
                    best_sim, best_no = sim, t.get("visitor_no")
            if best_no and best_sim >= min_sim:
                # 刷新该轨迹时间戳，保留最新特征
                tracks[:] = [t for t in tracks if t.get("visitor_no") != best_no]
                tracks.append({"emb": emb, "visitor_no": str(best_no), "ts": now})
                cls._unknown_tracks[int(camera_id)] = tracks[-20:]
                return str(best_no)

        visitor_no = generate_no(BIZ_VI, store_no, None)
        if embedding is not None:
            emb = np.asarray(embedding, dtype=np.float32)
            tracks = cls._prune_unknown_tracks(camera_id, now)
            tracks.append({"emb": emb, "visitor_no": visitor_no, "ts": now})
            cls._unknown_tracks[int(camera_id)] = tracks[-20:]
        return visitor_no

    @classmethod
    async def record_event(
        cls,
        db: AsyncSession,
        camera_id: int,
        event_type: str = "ENTER",
        snapshot_path: str | None = None,
        *,
        embedding: np.ndarray | None = None,
        reuse_min_sim: float | None = None,
        customer_id: int | None = None,
    ) -> VisitorEvent:
        settings = get_settings()
        from app.services.system_settings_service import SystemSettingsService

        retention = await SystemSettingsService.get_int(
            db, "visitor_data_retention_days", settings.visitor_data_retention_days
        )
        expires = now_local() + timedelta(days=retention)
        cam = await db.get(Camera, camera_id)
        sno = None
        if cam and cam.store_id:
            store = await db.get(Store, cam.store_id)
            sno = store.store_no if store else None

        cid = int(customer_id) if customer_id else None
        visitor_no: str
        if cid:
            # 会员：访客号固定为顾客编号，同一人始终同一号
            cust = await db.get(Customer, cid)
            visitor_no = (cust.customer_no if cust and cust.customer_no else None) or generate_no(
                BIZ_VI, sno, None
            )
            event_type = (event_type or "MATCH").strip().upper() or "MATCH"
        else:
            visitor_no = cls.resolve_visitor_no(
                camera_id,
                store_no=sno,
                embedding=embedding,
                reuse_min_sim=reuse_min_sim,
            )
            event_type = (event_type or "ENTER").strip().upper() or "ENTER"

        event = VisitorEvent(
            visitor_no=visitor_no,
            camera_id=camera_id,
            customer_id=cid,
            event_type=event_type,
            event_time=now_local(),
            snapshot_path=snapshot_path,
            expires_at=expires,
        )
        db.add(event)
        await db.flush()
        return event

    @classmethod
    async def maybe_welcome_unknown(
        cls,
        db: AsyncSession,
        camera_id: int,
        *,
        store_id: int | None,
    ) -> bool:
        """陌生访客语音迎宾：门店开关 + 冷却；入队后由 TTSWorker 播报。"""
        import time as _time

        from app.services.system_settings_service import SystemSettingsService

        settings = get_settings()
        enabled = await SystemSettingsService.effective_bool(
            db,
            "unknown_visitor_welcome_enabled",
            getattr(settings, "unknown_visitor_welcome_enabled", True),
            store_id=store_id,
        )
        if not enabled:
            logger.info(
                "陌生访客迎宾已关闭 camera_id=%s store_id=%s",
                camera_id,
                store_id,
            )
            return False
        cooldown = await SystemSettingsService.effective_int(
            db,
            "unknown_visitor_welcome_cooldown_seconds",
            getattr(settings, "unknown_visitor_welcome_cooldown_seconds", 60),
            store_id=store_id,
        )
        now = _time.time()
        last = cls._last_unknown_welcome_at.get(int(camera_id), 0.0)
        if cooldown > 0 and now - last < float(cooldown):
            remain = float(cooldown) - (now - last)
            # 人一直站门口时每帧都会走到这里，节流避免刷屏
            if now - cls._last_unknown_cd_log_at.get(int(camera_id), 0.0) >= 15.0:
                cls._last_unknown_cd_log_at[int(camera_id)] = now
                logger.info(
                    "陌生访客迎宾冷却中 camera_id=%s remain=%.1fs cooldown=%ss",
                    camera_id,
                    remain,
                    cooldown,
                )
            return False
        text = (
            await SystemSettingsService.effective_setting(
                db,
                "unknown_visitor_welcome_text",
                store_id=store_id,
                fallback=getattr(settings, "unknown_visitor_welcome_text", None) or "欢迎光临",
            )
            or "欢迎光临"
        ).strip()
        if not text:
            logger.warning("陌生访客迎宾文案为空，跳过 camera_id=%s", camera_id)
            return False
        cls._last_unknown_welcome_at[int(camera_id)] = now
        logger.info(
            "陌生访客迎宾入队 camera_id=%s store_id=%s text=%s",
            camera_id,
            store_id,
            text[:40],
        )
        await WelcomeService.enqueue(
            {"customer_id": None, "text": text, "camera_id": camera_id, "kind": "unknown_visitor"}
        )
        try:
            from app.services.websocket_manager import ws_manager

            await ws_manager.broadcast(
                "cameras",
                "WELCOME_UNKNOWN",
                {"camera_id": camera_id, "text": text, "store_id": store_id},
                store_id=store_id,
            )
        except Exception:
            pass
        return True

    @classmethod
    async def clear_events(
        cls,
        db: AsyncSession,
        *,
        camera_ids: list[int],
    ) -> dict:
        """清除指定摄像头范围内的访客事件及抓拍图。

        Windows / Ubuntu 均走 object_storage（本地 Path.unlink 或 S3 deleteObject），
        路径键用正斜杠；本地根目录相对路径按进程 cwd 解析（与 StaticFiles 一致）。
        """
        from sqlalchemy import delete, select

        from app.services.object_storage import get_storage

        ids = [int(x) for x in camera_ids if x is not None]
        if not ids:
            return {"deleted_rows": 0, "deleted_files": 0, "orphan_files": 0}
        rows = list(
            (
                await db.execute(select(VisitorEvent).where(VisitorEvent.camera_id.in_(ids)))
            ).scalars().all()
        )
        storage = get_storage()
        deleted_files = 0
        for row in rows:
            key = cls.uploads_url_to_key(getattr(row, "snapshot_path", None))
            if not key:
                if getattr(row, "snapshot_path", None):
                    logger.warning(
                        "清空访客无法解析抓拍键 path=%s",
                        row.snapshot_path,
                    )
                continue
            try:
                if await storage.delete(key):
                    deleted_files += 1
            except Exception:
                logger.warning("清空访客时删除抓拍失败 path=%s", row.snapshot_path, exc_info=True)
        result = await db.execute(delete(VisitorEvent).where(VisitorEvent.camera_id.in_(ids)))
        deleted_rows = int(result.rowcount or 0)
        # 再扫本地 visitors/ 下 cam{id}_* 孤儿文件（DB 已无记录但磁盘残留）
        orphan_files = await cls._purge_local_camera_snapshots(ids)
        for cid in ids:
            cls._unknown_tracks.pop(int(cid), None)
            cls._last_unknown_welcome_at.pop(int(cid), None)
        return {
            "deleted_rows": deleted_rows,
            "deleted_files": deleted_files + orphan_files,
            "orphan_files": orphan_files,
        }

    @classmethod
    async def _purge_local_camera_snapshots(cls, camera_ids: list[int]) -> int:
        """删除本地 uploads/visitors/**/cam{id}_* 文件（Win/Linux Path 通用）。"""
        import asyncio
        from pathlib import Path

        from app.core.config import get_settings

        settings = get_settings()
        if (getattr(settings, "storage_backend", "local") or "local").lower() != "local":
            return 0
        root = Path(settings.upload_dir).expanduser().resolve() / "visitors"
        if not root.is_dir():
            return 0
        id_set = {int(x) for x in camera_ids}

        def _walk() -> int:
            removed = 0
            for path in root.rglob("cam*_*.*"):
                if not path.is_file():
                    continue
                name = path.name
                # cam12_HHMMSS_xxxx.jpg
                if not name.startswith("cam"):
                    continue
                try:
                    cid = int(name.split("_", 1)[0][3:])
                except ValueError:
                    continue
                if cid not in id_set:
                    continue
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    logger.warning("删除孤儿抓拍失败 path=%s", path, exc_info=True)
            # 清理空日期目录
            for day_dir in sorted(root.glob("*/*/*"), reverse=True):
                if day_dir.is_dir() and not any(day_dir.iterdir()):
                    try:
                        day_dir.rmdir()
                    except OSError:
                        pass
            for month_dir in sorted(root.glob("*/*"), reverse=True):
                if month_dir.is_dir() and not any(month_dir.iterdir()):
                    try:
                        month_dir.rmdir()
                    except OSError:
                        pass
            for year_dir in sorted(root.glob("*"), reverse=True):
                if year_dir.is_dir() and not any(year_dir.iterdir()):
                    try:
                        year_dir.rmdir()
                    except OSError:
                        pass
            return removed

        return await asyncio.to_thread(_walk)

    @classmethod
    async def cleanup_expired(cls, db: AsyncSession) -> dict:
        """删除过期访客事件及其抓拍图；并按保留天数修剪本地 visitors/年/月/日 空目录。"""
        from sqlalchemy import delete, select

        from app.core.response import naive_local_now, now_local
        from app.services.object_storage import get_storage
        from app.services.system_settings_service import SystemSettingsService

        settings = get_settings()
        now = naive_local_now()
        rows = list(
            (
                await db.execute(select(VisitorEvent).where(VisitorEvent.expires_at <= now))
            ).scalars().all()
        )
        storage = get_storage()
        deleted_files = 0
        for row in rows:
            key = cls.uploads_url_to_key(getattr(row, "snapshot_path", None))
            if not key:
                continue
            try:
                if await storage.delete(key):
                    deleted_files += 1
            except Exception:
                logger.warning("删除访客抓拍失败 path=%s", row.snapshot_path, exc_info=True)

        result = await db.execute(delete(VisitorEvent).where(VisitorEvent.expires_at <= now))
        deleted_rows = int(result.rowcount or 0)
        await db.commit()

        retention = await SystemSettingsService.get_int(
            db, "visitor_data_retention_days", settings.visitor_data_retention_days
        )
        pruned_dirs = await cls._prune_local_visitor_dirs(retention_days=int(retention))
        return {
            "deleted_rows": deleted_rows,
            "deleted_files": deleted_files,
            "pruned_dirs": pruned_dirs,
        }

    @classmethod
    async def _prune_local_visitor_dirs(cls, *, retention_days: int) -> int:
        """本地存储：删除早于保留期的 visitors/YYYY/MM/DD 目录（按目录日期）。"""
        import asyncio
        import shutil
        from datetime import date, timedelta
        from pathlib import Path

        settings = get_settings()
        if (getattr(settings, "storage_backend", "local") or "local").lower() != "local":
            return 0
        root = Path(settings.upload_dir).resolve() / "visitors"
        if not root.is_dir():
            return 0
        cutoff = (now_local().date() - timedelta(days=max(1, int(retention_days))))

        def _walk() -> int:
            removed = 0
            # visitors/YYYY/MM/DD
            for day_dir in sorted(root.glob("*/*/*")):
                if not day_dir.is_dir():
                    continue
                parts = day_dir.relative_to(root).parts
                if len(parts) != 3:
                    continue
                try:
                    d = date(int(parts[0]), int(parts[1]), int(parts[2]))
                except ValueError:
                    continue
                if d < cutoff:
                    shutil.rmtree(day_dir, ignore_errors=True)
                    removed += 1
            # 清理空的月/年目录
            for month_dir in sorted(root.glob("*/*"), reverse=True):
                if month_dir.is_dir() and not any(month_dir.iterdir()):
                    month_dir.rmdir()
            for year_dir in sorted(root.glob("*"), reverse=True):
                if year_dir.is_dir() and not any(year_dir.iterdir()):
                    year_dir.rmdir()
            return removed

        return await asyncio.to_thread(_walk)


class WelcomeService:
    """迎宾 TTS 队列：优先 Redis，避免 uvicorn --reload 后内存队列孤儿导致无声。"""

    TTS_QUEUE_KEY = TTS_WELCOME_QUEUE_KEY
    TTS_QUEUE_MAX = 100
    _queue: asyncio.Queue | None = None
    _last_member_cd_log_at: dict[str, float] = {}

    @classmethod
    def get_queue(cls) -> asyncio.Queue:
        # 进程内后备队列（单测 / Redis 不可用时）
        if cls._queue is None:
            cls._queue = asyncio.Queue(maxsize=cls.TTS_QUEUE_MAX)
        return cls._queue

    @classmethod
    async def enqueue(cls, item: dict) -> None:
        """入队迎宾播报；跨热重载仍可由 Leader TTS 消费。"""
        import json

        payload = dict(item or {})
        text = str(payload.get("text") or "").strip()
        if not text:
            return
        payload["text"] = text
        kind = str(payload.get("kind") or "")
        raw = json.dumps(payload, ensure_ascii=False)
        try:
            redis = await get_redis()
            # 试听连点：去掉队列里同摄像头同 kind 的待播项，再入队（保留最后一次；
            # 正在播的已 BRPOP 出队，不受影响，播完后继续播本条）
            if kind.startswith("test_speak"):
                try:
                    items = await redis.lrange(cls.TTS_QUEUE_KEY, 0, -1)
                    kept: list[str] = []
                    dropped = 0
                    for it in items or []:
                        s = it.decode("utf-8", errors="replace") if isinstance(it, bytes) else str(it)
                        try:
                            obj = json.loads(s)
                        except Exception:
                            kept.append(s)
                            continue
                        if (
                            str(obj.get("kind") or "") == kind
                            and str(obj.get("camera_id")) == str(payload.get("camera_id"))
                        ):
                            dropped += 1
                            continue
                        kept.append(s)
                    pipe = redis.pipeline()
                    pipe.delete(cls.TTS_QUEUE_KEY)
                    if kept:
                        pipe.rpush(cls.TTS_QUEUE_KEY, *kept)
                    pipe.rpush(cls.TTS_QUEUE_KEY, raw)
                    pipe.ltrim(cls.TTS_QUEUE_KEY, -cls.TTS_QUEUE_MAX, -1)
                    await pipe.execute()
                    if dropped:
                        logger.info(
                            "试听入队替换 pending=%s kind=%s camera_id=%s",
                            dropped,
                            kind,
                            payload.get("camera_id"),
                        )
                    cls._schedule_g711_warmup(text)
                    return
                except Exception:
                    logger.exception("试听队列替换失败，回退普通入队")
            pipe = redis.pipeline()
            pipe.rpush(cls.TTS_QUEUE_KEY, raw)
            pipe.ltrim(cls.TTS_QUEUE_KEY, -cls.TTS_QUEUE_MAX, -1)
            await pipe.execute()
            # 后台预热本句 G.711：首句未命中时边播边写入，下一遍直接命中缓存
            cls._schedule_g711_warmup(text)
            return
        except Exception:
            logger.exception("迎宾 TTS Redis 入队失败，回退进程内队列")
        q = cls.get_queue()
        try:
            q.put_nowait(payload)
            cls._schedule_g711_warmup(text)
        except asyncio.QueueFull:
            logger.warning("迎宾 TTS 进程内队列已满，丢弃 text=%s", text[:40])

    @classmethod
    def _schedule_g711_warmup(cls, text: str) -> None:
        """入队后异步预热 WAV/G.711，避免会员长句首次合成拖慢摄像头播报。"""
        t = (text or "").strip()
        if not t:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _run() -> None:
            try:
                from app.workers.tts_worker import ensure_g711_cached

                # 常见音量都预热；codec 以 alaw 为主（海康本地机）
                for vol in (100, 80):
                    await asyncio.to_thread(
                        ensure_g711_cached, t, codec="alaw", volume_percent=vol
                    )
            except Exception:
                logger.debug("迎宾 G.711 预热失败 text=%s", t[:40], exc_info=True)

        loop.create_task(_run())

    @classmethod
    async def dequeue(cls, timeout: float = 1.0) -> dict | None:
        """取出一条迎宾任务；超时返回 None。"""
        import json

        try:
            redis = await get_redis()
            # BRPOP 超时至少 1 秒（redis-py 整数秒）
            wait = max(1, int(timeout))
            got = await redis.brpop(cls.TTS_QUEUE_KEY, timeout=wait)
            if got:
                _key, raw = got
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                data = json.loads(raw)
                return data if isinstance(data, dict) else None
        except Exception:
            logger.exception("迎宾 TTS Redis 出队失败，回退进程内队列")
        q = cls.get_queue()
        try:
            return await asyncio.wait_for(q.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    @staticmethod
    async def is_cooled_down(customer_id: int) -> bool:
        redis = await get_redis()
        key = welcome_cooldown_key(customer_id)
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
        key = welcome_cooldown_key(customer_id)
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
        await redis.set(welcome_cooldown_key(customer_id), "1", ex=seconds)

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
        key = welcome_frames_key(int(camera_id or 0), int(customer_id))
        pipe = redis.pipeline()
        pipe.rpush(key, f"{similarity:.6f}")
        pipe.ltrim(key, -confirm_frames, -1)
        pipe.expire(key, 10)
        pipe.llen(key)
        pipe.lrange(key, 0, -1)
        _, _, _, length, values = await pipe.execute()
        scores = [float(v) for v in values]
        avg = sum(scores) / len(scores) if scores else 0.0
        return length >= confirm_frames, avg

    # 迎宾语模板占位符（中英均可）
    WELCOME_PLACEHOLDERS = (
        ("称呼", "name"),
        ("会员等级", "level"),
        ("店名", "store"),
        ("口味", "taste"),
        ("餐品", "product"),
    )
    DEFAULT_GREETING_TEMPLATE = "欢迎回来，{称呼}。"
    DEFAULT_PREFERENCE_TEMPLATE = "今天还是老样子：{口味}？"
    # 默认不自动追加餐品句，避免播报过长；需要时在主模板写 {餐品} 或配置本项
    DEFAULT_PRODUCT_TEMPLATE = ""

    @staticmethod
    def _collapse_welcome_spaces(text: str) -> str:
        """压缩多余空白，并清理空占位留下的尴尬标点。"""
        import re

        s = re.sub(r"[ \t\u3000]+", " ", (text or "").strip())
        # 「尊贵的  金卡」→「尊贵的 金卡」；去掉空口味导致的「：？」
        s = re.sub(r"[：:][？?。.！!]*$", "", s)
        s = re.sub(r"[：:]\s*(?=[，,。.！!？?]|$)", "", s)
        s = re.sub(r"[，,]\s*(?=[，,。.！!？?]|$)", "", s)
        return s.strip()

    @classmethod
    def render_welcome_template(cls, template: str, values: dict[str, str]) -> str:
        """把 {称呼}/{会员等级}/{店名}/{口味}（及英文别名）替换为实际值。"""
        text = template or ""
        for cn, en in cls.WELCOME_PLACEHOLDERS:
            val = str(values.get(cn) or values.get(en) or "")
            text = text.replace("{" + cn + "}", val)
            text = text.replace("{" + en + "}", val)
        return cls._collapse_welcome_spaces(text)

    @classmethod
    def template_has_placeholder(cls, template: str, *names: str) -> bool:
        t = template or ""
        for n in names:
            if "{" + n + "}" in t:
                return True
        return False

    @classmethod
    def build_welcome_text(
        cls,
        *,
        call_as: str,
        level_name: str = "",
        store_name: str = "",
        greeting_template: str | None = None,
        preference_template: str | None = None,
        product_template: str | None = None,
        preference_bits: list[str] | None = None,
        product_bits: list[str] | None = None,
    ) -> str:
        """拼迎宾语。

        - 新模板：含 `{称呼}` 等占位符时按模板渲染
        - 旧前缀：仅「欢迎回来」这类无占位符文本时，拼成「前缀，称呼。」
        - 有口味且主模板未写 `{口味}` 时，追加口味副句（默认可配）
        - 有餐品且主模板未写 `{餐品}` 时，仅当配置了餐品附句才追加
        """
        bits = [b.strip() for b in (preference_bits or []) if (b or "").strip()]
        products = [b.strip() for b in (product_bits or []) if (b or "").strip()]
        taste = "、".join(bits)
        product = "、".join(products)
        level = (level_name or "").strip()
        store = (store_name or "").strip()
        raw_call = (call_as or "").strip()
        tpl = (greeting_template or "").strip() or cls.DEFAULT_GREETING_TEMPLATE

        # 称呼兜底：主模板若未单独写会员等级，无展示名/昵称时用「尊贵的{等级}」
        has_level_ph = cls.template_has_placeholder(tpl, "会员等级", "level")
        if raw_call:
            name = raw_call
        elif has_level_ph:
            name = ""
        elif level:
            name = f"尊贵的{level}" if level.endswith("会员") else f"尊贵的{level}会员"
        else:
            name = "尊贵的会员"

        values = {
            "称呼": name,
            "name": name,
            "会员等级": level,
            "level": level,
            "店名": store,
            "store": store,
            "口味": taste,
            "taste": taste,
            "餐品": product,
            "product": product,
        }

        ph_names = (
            "称呼",
            "name",
            "会员等级",
            "level",
            "店名",
            "store",
            "口味",
            "taste",
            "餐品",
            "product",
        )
        if cls.template_has_placeholder(tpl, *ph_names):
            text = cls.render_welcome_template(tpl, values)
        else:
            # 兼容旧「仅前缀」配置
            prefix = tpl.rstrip("，,。.！!？?、；;：: ")
            text = f"{prefix}，{name or (level and (f'尊贵的{level}' if level.endswith('会员') else f'尊贵的{level}会员')) or '尊贵的会员'}。"

        # 主模板已含 {口味}/{餐品}：无数据时清掉空占位残留；有数据已在上面填入
        if bits and not cls.template_has_placeholder(tpl, "口味", "taste"):
            pref_tpl = (preference_template or "").strip() or cls.DEFAULT_PREFERENCE_TEMPLATE
            pref_sentence = cls.render_welcome_template(pref_tpl, values)
            if pref_sentence:
                text = f"{text.rstrip('。.!！')}。{pref_sentence}"
        if products and not cls.template_has_placeholder(tpl, "餐品", "product"):
            prod_tpl = (product_template if product_template is not None else cls.DEFAULT_PRODUCT_TEMPLATE) or ""
            prod_tpl = prod_tpl.strip()
            if prod_tpl:
                prod_sentence = cls.render_welcome_template(prod_tpl, values)
                if prod_sentence:
                    text = f"{text.rstrip('。.!！')}。{prod_sentence}"
        return cls._collapse_welcome_spaces(text)

    @staticmethod
    async def resolve_welcome_identity(
        db: AsyncSession, customer: Customer
    ) -> tuple[str, str]:
        """返回 (称呼, 会员等级名)。

        称呼 = 展示名 → 昵称 → 空（由模板/等级兜底决定怎么说）
        会员等级 = member_levels.name
        """
        from app.services.customer_service import CustomerService

        call_as = ""
        display = (customer.display_name or "").strip()
        if CustomerService.is_meaningful_nickname(display):
            call_as = display
        else:
            nick = (customer.nickname or "").strip()
            if CustomerService.is_meaningful_nickname(nick):
                call_as = nick

        level_name = ""
        if customer.member_level_id:
            level = await db.get(MemberLevel, int(customer.member_level_id))
            if level and (level.name or "").strip():
                level_name = str(level.name).strip()
        return call_as, level_name

    @staticmethod
    async def resolve_welcome_name(db: AsyncSession, customer: Customer) -> str:
        """兼容旧调用：单字段称呼（有名用名，否则尊贵的等级）。"""
        call_as, level_name = await WelcomeService.resolve_welcome_identity(db, customer)
        if call_as:
            return call_as
        if level_name:
            return (
                f"尊贵的{level_name}"
                if level_name.endswith("会员")
                else f"尊贵的{level_name}会员"
            )
        return "尊贵的会员"

    @classmethod
    async def try_welcome(
        cls,
        db: AsyncSession,
        customer_id: int,
        camera_id: int | None,
        similarity: float,
        *,
        preconfirmed: bool = False,
    ) -> bool:
        """尝试会员迎宾。

        preconfirmed=True：识别 worker 已用 MultiFrameTracker 确认，勿再 Redis 叠一层确认
        （否则需约 2×confirm_frames 才播报，体感极慢且成功率低）。
        """
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
        if preconfirmed:
            avg_sim = float(similarity)
        else:
            ready, avg_sim = await cls.push_frame_score(
                customer_id, camera_id, similarity, db, store_id=store_id
            )
            if not ready:
                return False
        if avg_sim < threshold:
            return False
        # 先原子抢冷却，禁止 exists()+setex() 双步竞态导致重复迎宾
        if not await cls.try_acquire_cooldown(customer_id, db, store_id=store_id):
            import time as _time

            now = _time.time()
            cd_key = f"{int(camera_id or 0)}:{int(customer_id)}"
            if now - cls._last_member_cd_log_at.get(cd_key, 0.0) >= 15.0:
                cls._last_member_cd_log_at[cd_key] = now
                logger.info(
                    "会员迎宾冷却中 customer_id=%s camera_id=%s",
                    customer_id,
                    camera_id,
                )
            return False

        customer = await db.get(Customer, customer_id)
        if not customer:
            logger.warning("会员迎宾顾客不存在 customer_id=%s", customer_id)
            return False

        call_as, level_name = await cls.resolve_welcome_identity(db, customer)
        store_name = ""
        if store_id:
            store = await db.get(Store, int(store_id))
            if store and (store.name or "").strip():
                store_name = str(store.name).strip()

        greeting_template = await SystemSettingsService.effective_setting(
            db,
            "welcome_greeting_prefix",
            store_id=store_id,
            fallback=cls.DEFAULT_GREETING_TEMPLATE,
        )
        preference_template = await SystemSettingsService.effective_setting(
            db,
            "welcome_preference_template",
            store_id=store_id,
            fallback=cls.DEFAULT_PREFERENCE_TEMPLATE,
        )
        product_template = await SystemSettingsService.effective_setting(
            db,
            "welcome_product_template",
            store_id=store_id,
            fallback=cls.DEFAULT_PRODUCT_TEMPLATE,
        )
        # 仅主模板占位才查库：默认副句含 {口味}，一并查会拖慢入队且拉长摄像头音频
        need_taste = cls.template_has_placeholder(
            greeting_template or "", "口味", "taste"
        )
        need_product = cls.template_has_placeholder(
            greeting_template or "", "餐品", "product"
        )

        preference_bits: list[str] = []
        if need_taste:
            try:
                from app.services.customer_service import PreferenceService

                # 口味推荐偶发数秒；超时则跳过，避免认出后卡在入队前
                rec = await asyncio.wait_for(
                    PreferenceService.get_recommendations(
                        db, customer_id, store_id=store_id
                    ),
                    timeout=0.25,
                )
                prefs = (rec or {}).get("preferences") or []
                if prefs:
                    for p in prefs[:2]:
                        label = (p.get("label") or "").strip()
                        if not label:
                            cat = (p.get("category") or "").strip()
                            opt = (p.get("option_name") or p.get("option_code") or "").strip()
                            label = f"{cat}·{opt}" if cat and opt else (opt or cat)
                        if label:
                            preference_bits.append(label)
            except Exception:
                preference_bits = []

        product_bits: list[str] = []
        if need_product and store_id:
            try:
                from app.services.recommendation_service import RecommendationService

                ranked, _state, _meta = await asyncio.wait_for(
                    RecommendationService.recommend(
                        db,
                        int(store_id),
                        customer_id=customer_id,
                        scene="recommend_menu",
                        limit=2,
                        use_cache=True,
                    ),
                    timeout=0.25,
                )
                for item in ranked[:2]:
                    pname = str(getattr(getattr(item, "product", None), "name", "") or "").strip()
                    if pname:
                        product_bits.append(pname)
            except Exception:
                product_bits = []

        text = cls.build_welcome_text(
            call_as=call_as,
            level_name=level_name,
            store_name=store_name,
            greeting_template=greeting_template,
            preference_template=preference_template,
            product_template=product_template,
            preference_bits=preference_bits,
            product_bits=product_bits,
        )
        # WebSocket / TTS 展示用简称
        name = call_as or (
            f"尊贵的{level_name}"
            if level_name.endswith("会员")
            else (f"尊贵的{level_name}会员" if level_name else "尊贵的会员")
        )

        logger.info(
            "会员迎宾入队 customer_id=%s camera_id=%s store_id=%s text=%s",
            customer_id,
            camera_id,
            store_id,
            text[:60],
        )
        await cls.enqueue(
            {
                "customer_id": customer_id,
                "text": text,
                "camera_id": camera_id,
                "kind": "member",
            }
        )

        db.add(WelcomeLog(customer_id=customer_id, camera_id=camera_id, welcome_text=text))
        try:
            redis = await get_redis()
            await redis.delete(welcome_frames_key(int(camera_id or 0), int(customer_id)))
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

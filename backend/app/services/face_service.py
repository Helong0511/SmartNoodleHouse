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
    SPEAK_TASK_TTL_SECONDS,
    TTS_WELCOME_QUEUE_KEY,
    speak_generation_key,
    speak_task_key,
    unknown_welcome_cooldown_key,
    welcome_cooldown_key,
    welcome_frames_key,
)
from app.core.config import get_settings
from app.core.exceptions import AppException
from app.core.redis_client import get_redis, release_owned_lock
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
        # 预归一化矩阵：避免每次匹配 Python 循环 + 重复 L2
        self._matrix: np.ndarray | None = None
        self._matrix_ids: list[int] = []
        self._matrix_dirty: bool = True

    def invalidate(self) -> None:
        self._last_sync = None
        self._matrix_dirty = True

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
            self._matrix_dirty = True
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
        """热集比对；未达阈值且质量路径允许时做有界冷库二次匹配。

        冷库 DB 扫描不持全局锁，避免多摄像头识别互相堵死。
        """
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
            async with self._lock:
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
                        self._matrix_dirty = True
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

    def _rebuild_matrix(self) -> None:
        if not self._by_customer:
            self._matrix = np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
            self._matrix_ids = []
            self._matrix_dirty = False
            return
        ids = list(self._by_customer.keys())
        mat = np.stack([self._by_customer[cid][1] for cid in ids], axis=0).astype(np.float32)
        norms = np.linalg.norm(mat, axis=1, keepdims=True) + 1e-8
        self._matrix = mat / norms
        self._matrix_ids = ids
        self._matrix_dirty = False

    def _match_hot(self, emb: np.ndarray) -> tuple[int | None, float]:
        if self._matrix_dirty or self._matrix is None:
            self._rebuild_matrix()
        if self._matrix is None or not self._matrix_ids:
            return None, 0.0
        q = np.asarray(emb, dtype=np.float32).reshape(-1)
        if q.size != EMBEDDING_DIM:
            return None, 0.0
        q = q / (np.linalg.norm(q) + 1e-8)
        sims = self._matrix @ q
        bi = int(np.argmax(sims))
        return self._matrix_ids[bi], float(sims[bi])

    async def _cold_match(
        self,
        db: AsyncSession,
        emb: np.ndarray,
        threshold: float,
        hot_ids: set[int],
    ) -> tuple[int | None, float]:
        """有界冷库扫描：预算内取最高相似度，禁止「首个达标即返回」。

        历史缺陷：按活跃度排序后遇到第一个 >=threshold 就 return，
        会导致前面的弱匹配盖过后面的强匹配（例：A=0.47 先于 B=0.78）。
        """
        s = get_settings()
        batch = max(50, int(getattr(s, "face_cache_cold_batch_size", 200) or 200))
        score = self._activity_score_expr()
        enc = FaceEncryption()
        best_id, best_sim = None, 0.0
        second_sim = 0.0
        scanned = 0
        offset = 0
        # 最多扫 20 批，避免极端会员量打爆单帧；整段预算内比完再决出 top-1
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
                scanned += 1
                vec = self._decrypt_vec(enc, p)
                if vec is None or vec.shape != emb.shape:
                    continue
                sim = FaceService.cosine_similarity(emb, vec)
                if sim > best_sim:
                    second_sim = best_sim
                    best_sim, best_id = float(sim), int(p.customer_id)
                elif sim > second_sim:
                    second_sim = float(sim)
            if len(rows) < batch:
                break
            offset += batch
        if best_id is not None:
            logger.info(
                "face_cache cold_scan scanned=%s top1_id=%s top1=%.4f top2=%.4f "
                "delta=%.4f threshold=%.4f hit=%s",
                scanned,
                best_id,
                best_sim,
                second_sim,
                best_sim - second_sim,
                float(threshold),
                1 if best_sim >= float(threshold) else 0,
            )
        return best_id, best_sim

    def _evict_overflow(self) -> None:
        cap = self._resolve_max()
        changed = False
        while len(self._by_customer) > cap:
            # 无 touch 视为最旧；同 touch 任意踢一个
            victim = min(
                self._by_customer.keys(),
                key=lambda cid: self._touch_at.get(cid, 0.0),
            )
            self._by_customer.pop(victim, None)
            self._touch_at.pop(victim, None)
            changed = True
        if changed:
            self._matrix_dirty = True

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
            self._matrix_dirty = True
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
                if cid in self._by_customer:
                    self._by_customer.pop(cid, None)
                    self._touch_at.pop(cid, None)
                    self._matrix_dirty = True
                continue
            # 增量：仅更新已在热集中的；新人靠 promote / 下次全量 / 冷命中进入
            if cid not in self._by_customer and len(self._by_customer) >= cap:
                continue
            vec = self._decrypt_vec(enc, p)
            if vec is None:
                if cid in self._by_customer:
                    self._by_customer.pop(cid, None)
                    self._touch_at.pop(cid, None)
                    self._matrix_dirty = True
                continue
            self._by_customer[cid] = (int(p.id), vec)
            if cid not in self._touch_at:
                self._touch_at[cid] = now_mono
            self._matrix_dirty = True
        self._evict_overflow()
        self._last_sync = newest

    @staticmethod
    def _decrypt_vec(enc: FaceEncryption, p: FaceProfile) -> np.ndarray | None:
        if not p.feature_data:
            return None
        try:
            vec = json.loads(enc.decrypt(p.feature_data).decode())
            arr = np.array(vec, dtype=np.float32).reshape(-1)
            if arr.size != EMBEDDING_DIM:
                logger.warning(
                    "跳过维度不匹配的人脸档案 profile_id=%s dim=%s",
                    getattr(p, "id", None),
                    arr.size,
                )
                return None
            # 与 live/enroll 提取路径一致：入库历史向量统一 L2，供矩阵点积匹配
            return arr / (np.linalg.norm(arr) + 1e-8)
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
        """将历史公开 URL、S3 URL 或 private:// 对象引用安全归一为 visitors/ 对象键。"""
        from urllib.parse import urlparse

        s = str(url or "").strip().replace("\\", "/")
        if not s:
            return None
        if s.startswith("private://"):
            s = s[len("private://") :]
        elif s.startswith("s3://"):
            # s3://bucket/visitors/...
            parsed = urlparse(s)
            s = parsed.path.lstrip("/")
        elif "://" in s:
            parsed = urlparse(s)
            s = parsed.path.lstrip("/")
        elif s.startswith("/uploads/"):
            s = s[len("/uploads/") :]
        elif s.startswith("uploads/"):
            s = s[len("uploads/") :]
        # 兼容 endpoint/bucket/key 形式，仅接受路径中明确的 visitors/ 前缀。
        if not s.startswith("visitors/") and "/visitors/" in s:
            s = s[s.index("visitors/") :]
        parts = s.split("/")
        if (
            not s.startswith("visitors/")
            or any(part in ("", ".", "..") for part in parts)
            or "\x00" in s
        ):
            return None
        return s

    @staticmethod
    def make_snapshot_key(camera_id: int, *, ext: str = "jpg") -> str:
        """按日期分目录：visitors/YYYY/MM/DD/cam{id}_HHMMSS_xxxx.jpg，便于按天清理。"""
        from app.core.response import now_local

        now = now_local()
        stamp = now.strftime("%H%M%S")
        day = now.strftime("%Y/%m/%d")
        return f"visitors/{day}/cam{int(camera_id)}_{stamp}_{uuid.uuid4().hex[:8]}.{ext.lstrip('.')}"

    @staticmethod
    def encode_frame_jpeg(frame, *, quality: int = 85, max_side: int | None = 1280) -> bytes:
        """编码抓拍 JPEG；访客事件默认长边压到 1280，避免主码流 2560 原图占盘。"""
        import cv2

        img = frame
        if max_side and img is not None and getattr(img, "ndim", 0) >= 2:
            h, w = int(img.shape[0]), int(img.shape[1])
            side = max(h, w)
            if side > int(max_side):
                scale = float(max_side) / float(side)
                nw = max(1, int(round(w * scale)))
                nh = max(1, int(round(h * scale)))
                img = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
        if not ok or buf is None:
            raise ValueError("抓拍帧编码失败")
        return buf.tobytes()

    @classmethod
    async def save_frame_snapshot(cls, frame, camera_id: int) -> str:
        """保存摄像头访客抓拍为私有对象；数据库仅保存 private:// 对象引用。"""
        import asyncio

        from app.services.object_storage import get_storage

        data = await asyncio.to_thread(cls.encode_frame_jpeg, frame)
        key = cls.make_snapshot_key(camera_id, ext="jpg")
        await get_storage().put_bytes(key, data, content_type="image/jpeg")
        return f"private://{key}"

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

        # 进程内轨迹 TTL 用 monotonic，避免墙上时钟回拨把复用窗拉成永久
        now = _time.monotonic()
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
        # 进程内冷却/日志节流用 monotonic；Redis NX EX 仍走服务端墙钟 TTL
        now_mono = _time.monotonic()
        acquired = True
        cooldown_token = uuid.uuid4().hex
        cooldown_key = unknown_welcome_cooldown_key(int(camera_id))
        if cooldown > 0:
            try:
                redis = await get_redis()
                ok = await redis.set(
                    cooldown_key,
                    cooldown_token,
                    nx=True,
                    ex=int(cooldown),
                )
                acquired = bool(ok)
            except Exception:
                # Redis 不可用时回退进程内字典（多 worker 不共享，明确降级）
                logger.debug("unknown welcome redis cooldown failed, fallback memory", exc_info=True)
                last = cls._last_unknown_welcome_at.get(int(camera_id), 0.0)
                if now_mono - last < float(cooldown):
                    acquired = False
                else:
                    cls._last_unknown_welcome_at[int(camera_id)] = now_mono
        if not acquired:
            if now_mono - cls._last_unknown_cd_log_at.get(int(camera_id), 0.0) >= 15.0:
                cls._last_unknown_cd_log_at[int(camera_id)] = now_mono
                logger.info(
                    "陌生访客迎宾冷却中 camera_id=%s cooldown=%ss",
                    camera_id,
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
            if cooldown > 0:
                try:
                    redis = await get_redis()
                    await release_owned_lock(redis, cooldown_key, cooldown_token)
                except Exception:
                    cls._last_unknown_welcome_at.pop(int(camera_id), None)
            return False
        logger.info(
            "陌生访客迎宾入队 camera_id=%s store_id=%s text=%s",
            camera_id,
            store_id,
            text[:40],
        )
        tid = None
        try:
            tid = await WelcomeService.enqueue(
                {"customer_id": None, "text": text, "camera_id": camera_id, "kind": "unknown_visitor"}
            )
        except Exception:
            if cooldown > 0:
                try:
                    redis = await get_redis()
                    await release_owned_lock(redis, cooldown_key, cooldown_token)
                except Exception:
                    cls._last_unknown_welcome_at.pop(int(camera_id), None)
            raise
        if not tid:
            # member_pending 等取消：释放冷却，避免无声却占窗
            if cooldown > 0:
                try:
                    redis = await get_redis()
                    await release_owned_lock(redis, cooldown_key, cooldown_token)
                except Exception:
                    cls._last_unknown_welcome_at.pop(int(camera_id), None)
            return False
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
        deletable_ids: list[int] = []
        for row in rows:
            snapshot_path = getattr(row, "snapshot_path", None)
            key = cls.uploads_url_to_key(snapshot_path)
            if not snapshot_path:
                # 没有关联抓拍文件，可以直接清除事件记录。
                deletable_ids.append(int(row.id))
                continue
            if not key:
                # 无法安全解析对象键时保留记录，避免丢失后续人工定位/清理依据。
                logger.warning("清空访客无法解析抓拍键，保留事件记录 id=%s path=%s", row.id, snapshot_path)
                continue
            try:
                # False 表示本地对象已不存在；删除目标已经满足，可清理数据库引用。
                removed = await storage.delete(key)
                deletable_ids.append(int(row.id))
                if removed:
                    deleted_files += 1
            except Exception:
                # 对象删除失败时保留 DB 行，供下一轮任务重试，禁止制造孤儿文件。
                logger.warning("清空访客时删除抓拍失败，保留事件记录 id=%s path=%s", row.id, snapshot_path, exc_info=True)
        if deletable_ids:
            result = await db.execute(delete(VisitorEvent).where(VisitorEvent.id.in_(deletable_ids)))
            deleted_rows = int(result.rowcount or 0)
        else:
            deleted_rows = 0
        # 再扫本地 visitors/ 下 cam{id}_* 孤儿文件（DB 已无记录但磁盘残留）
        orphan_files = await cls._purge_local_camera_snapshots(ids)
        for cid in ids:
            cls._unknown_tracks.pop(int(cid), None)
            cls._last_unknown_welcome_at.pop(int(cid), None)
            try:
                redis = await get_redis()
                await redis.delete(unknown_welcome_cooldown_key(int(cid)))
            except Exception:
                pass
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
        deletable_ids: list[int] = []
        for row in rows:
            snapshot_path = getattr(row, "snapshot_path", None)
            key = cls.uploads_url_to_key(snapshot_path)
            if not snapshot_path:
                deletable_ids.append(int(row.id))
                continue
            if not key:
                logger.warning("过期访客抓拍键无法解析，保留事件记录 id=%s path=%s", row.id, snapshot_path)
                continue
            try:
                # 对象已不存在也可安全清除 DB 引用；适配器抛异常才视为删除失败。
                await storage.delete(key)
                deletable_ids.append(int(row.id))
                deleted_files += 1
            except Exception:
                # 失败记录保留，下一轮 cleanup_expired 会再次尝试删除对象。
                logger.warning("删除访客抓拍失败，保留事件记录 id=%s path=%s", row.id, snapshot_path, exc_info=True)

        if deletable_ids:
            result = await db.execute(delete(VisitorEvent).where(VisitorEvent.id.in_(deletable_ids)))
            deleted_rows = int(result.rowcount or 0)
        else:
            deleted_rows = 0
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
    # 人已离开视野后仍播旧句 → 丢弃排队过久的任务（秒）
    TTS_ITEM_MAX_AGE_SEC = 8.0
    _queue: asyncio.Queue | None = None
    _last_member_cd_log_at: dict[str, float] = {}
    # 同摄像头播报世代：会员入队时 +1，打断正在播的陌生迎宾
    # 进程内缓存；跨 Worker 以 Redis 为协调真源（见 bump/sync）
    _speak_gen: dict[int, int] = {}
    _SPEAK_GEN_TTL_SEC = 180
    # Redis 键过期后 INCR 会从 1 重计；用 max(redis,local)+1 保证打断令牌单调抬升
    _SPEAK_GEN_BUMP_LUA = (
        "local cur = tonumber(redis.call('GET', KEYS[1]) or '0') "
        "local floor = tonumber(ARGV[1]) "
        "local ttl = tonumber(ARGV[2]) "
        "local nxt = math.max(cur, floor) + 1 "
        "redis.call('SET', KEYS[1], nxt, 'EX', ttl) "
        "return nxt"
    )
    # 仅抬升到至少 want，禁止非原子 GET+SET 把并发 bump 结果压低
    _SPEAK_GEN_ENSURE_LUA = (
        "local cur = tonumber(redis.call('GET', KEYS[1]) or '0') "
        "local want = tonumber(ARGV[1]) "
        "local ttl = tonumber(ARGV[2]) "
        "local nxt = math.max(cur, want) "
        "redis.call('SET', KEYS[1], nxt, 'EX', ttl) "
        "return nxt"
    )

    @classmethod
    def get_queue(cls) -> asyncio.Queue:
        # 进程内后备队列（单测 / Redis 不可用时）
        if cls._queue is None:
            cls._queue = asyncio.Queue(maxsize=cls.TTS_QUEUE_MAX)
        return cls._queue

    @classmethod
    def speak_generation(cls, camera_id: int | None) -> int:
        """同步读本地缓存（推流 abort 回调必须同步）。"""
        return int(cls._speak_gen.get(int(camera_id or 0), 0))

    @classmethod
    def bump_speak_generation(cls, camera_id: int | None) -> int:
        """同步本地 +1，并后台把至少该值写入 Redis（单测 / 同步调用方）。"""
        cid = int(camera_id or 0)
        nxt = int(cls._speak_gen.get(cid, 0)) + 1
        cls._speak_gen[cid] = nxt
        try:
            from app.core.async_utils import spawn_bg

            spawn_bg(cls._redis_ensure_speak_gen_at_least(cid, nxt))
        except Exception:
            pass
        return nxt

    @classmethod
    async def bump_speak_generation_async(cls, camera_id: int | None) -> int:
        """入队路径优先：Redis 单调 +1（相对 max(redis,本地)），键过期后仍能打断。"""
        cid = int(camera_id or 0)
        floor = int(cls._speak_gen.get(cid, 0))
        try:
            redis = await get_redis()
            key = speak_generation_key(cid)
            val = await redis.eval(
                cls._SPEAK_GEN_BUMP_LUA,
                1,
                key,
                str(floor),
                str(int(cls._SPEAK_GEN_TTL_SEC)),
            )
            nxt = int(val)
            cls._speak_gen[cid] = nxt
            return nxt
        except Exception:
            logger.debug("speak_generation redis bump failed, local bump", exc_info=True)
            return cls.bump_speak_generation(cid)

    @classmethod
    async def sync_speak_generation_from_redis(cls, camera_id: int | None) -> int:
        """把 Redis 世代拉入本地（TTS 播陌生句时轮询，供同步 abort 可见跨进程 bump）。"""
        cid = int(camera_id or 0)
        local = int(cls._speak_gen.get(cid, 0))
        try:
            redis = await get_redis()
            raw = await redis.get(speak_generation_key(cid))
            if raw is None:
                return local
            remote = int(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
            if remote > local:
                cls._speak_gen[cid] = remote
                return remote
        except Exception:
            logger.debug("speak_generation redis sync failed", exc_info=True)
        return local

    @classmethod
    async def _redis_ensure_speak_gen_at_least(cls, camera_id: int, min_val: int) -> None:
        cid = int(camera_id)
        want = max(int(min_val), int(cls._speak_gen.get(cid, 0)))
        try:
            redis = await get_redis()
            key = speak_generation_key(cid)
            val = await redis.eval(
                cls._SPEAK_GEN_ENSURE_LUA,
                1,
                key,
                str(want),
                str(int(cls._SPEAK_GEN_TTL_SEC)),
            )
            nxt = int(val)
            local = int(cls._speak_gen.get(cid, 0))
            if nxt > local:
                cls._speak_gen[cid] = nxt
        except Exception:
            logger.debug("speak_generation redis ensure failed", exc_info=True)

    @classmethod
    def _parse_queue_item(cls, raw: str | bytes) -> tuple[str, dict | None]:
        import json

        s = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        try:
            obj = json.loads(s)
        except Exception:
            return s, None
        return s, obj if isinstance(obj, dict) else None

    @classmethod
    def _pending_drop_reason(cls, new_kind: str, old_kind: str, same_cam: bool) -> str:
        """同机位队列整理：返回 cancelled reason；空串表示保留。"""
        if not same_cam:
            return ""
        nk = str(new_kind or "")
        ok = str(old_kind or "")
        if nk.startswith("test_speak"):
            # both/camera 都走摄像头推流：同机位互视为同一试听族
            cam_test = ("test_speak", "test_speak_camera")
            if nk in cam_test and ok in cam_test:
                return "replaced"
            if ok == nk:
                return "replaced"
            # 摄像头侧试听与陌生「见试听则跳过」对称：踢掉同机位待播陌生句
            if nk in cam_test and ok == "unknown_visitor":
                return "test_speak_preempt"
            return ""
        if nk == "member":
            if ok == "unknown_visitor":
                return "member_preempt"
            # 待播试听/旧会员句挡门迎宾：一并作废
            if ok in ("test_speak", "test_speak_camera", "member"):
                return "member_preempt" if ok.startswith("test_speak") else "replaced"
            return ""
        return ""

    @classmethod
    async def _bump_speak_gen_after_enqueue(
        cls, kind: str, camera_id: int | None
    ) -> None:
        """入队成功后再 bump：会员/摄像头试听打断在播；电脑试听不 bump。"""
        if camera_id is None:
            return
        k = str(kind or "")
        if k in ("member", "test_speak_camera", "test_speak"):
            await cls.bump_speak_generation_async(camera_id)

    # Redis 不可用时仍可供试听轮询（有 TTL，防泄漏）
    _speak_task_local: dict[str, dict] = {}

    @classmethod
    def _prune_speak_task_local(cls, now_mono: float | None = None) -> None:
        import time as _time

        # 本地 TTL 优先 monotonic；仅有旧字段 updated_at 时回退墙钟，勿混比
        mono = float(now_mono if now_mono is not None else _time.monotonic())
        wall = _time.time()
        ttl = float(SPEAK_TASK_TTL_SECONDS)
        dead: list[str] = []
        for k, v in cls._speak_task_local.items():
            row = v or {}
            if "updated_mono" in row:
                age = mono - float(row.get("updated_mono") or 0)
            else:
                age = wall - float(row.get("updated_at") or 0)
            if age > ttl:
                dead.append(k)
        for k in dead:
            cls._speak_task_local.pop(k, None)

    @classmethod
    async def set_speak_task_status(
        cls, task_id: str | None, status: str, **extra
    ) -> None:
        """写入播报任务状态（queued/synthesizing/sending/completed/cancelled/failed）。"""
        tid = str(task_id or "").strip()
        if not tid:
            return
        import json
        import time as _time

        now_wall = _time.time()
        now_mono = _time.monotonic()
        payload = {
            "task_id": tid,
            "status": str(status or "unknown"),
            "updated_at": now_wall,
            **{k: v for k, v in extra.items() if v is not None},
        }
        local_payload = {**payload, "updated_mono": now_mono}
        cls._prune_speak_task_local(now_mono)
        cls._speak_task_local[tid] = local_payload
        try:
            redis = await get_redis()
            # Redis 侧不写 updated_mono（跨进程无意义）；EX 仍用墙钟 TTL
            await redis.set(
                speak_task_key(tid),
                json.dumps(payload, ensure_ascii=False),
                ex=int(SPEAK_TASK_TTL_SECONDS),
            )
        except Exception:
            logger.debug("speak_task status write failed task_id=%s", tid, exc_info=True)

    @classmethod
    async def get_speak_task(cls, task_id: str | None) -> dict | None:
        tid = str(task_id or "").strip()
        if not tid:
            return None
        import json
        import time as _time

        try:
            redis = await get_redis()
            raw = await redis.get(speak_task_key(tid))
            if raw:
                s = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                obj = json.loads(s)
                if isinstance(obj, dict):
                    return obj
        except Exception:
            logger.debug("speak_task status read failed", exc_info=True)
        cls._prune_speak_task_local()
        local = cls._speak_task_local.get(tid)
        if not local:
            return None
        age_base = float(local.get("updated_mono") or local.get("updated_at") or 0)
        now_m = _time.monotonic()
        # 旧条目仅有 updated_at（墙钟）：回退比较墙钟，避免误杀
        if "updated_mono" in local:
            expired = now_m - age_base > float(SPEAK_TASK_TTL_SECONDS)
        else:
            expired = _time.time() - age_base > float(SPEAK_TASK_TTL_SECONDS)
        if expired:
            cls._speak_task_local.pop(tid, None)
            return None
        out = dict(local)
        out.pop("updated_mono", None)
        return out

    @classmethod
    async def _finalize_redis_enqueue(
        cls,
        *,
        task_id: str,
        kind: str,
        payload: dict,
        text: str,
        raw: str,
        cam_s: str,
        redis,
    ) -> str | None:
        """RPUSH 已成功：写状态/bump；失败只记日志，禁止再入队。陌生句做 TOCTOU 复检。"""
        if kind == "unknown_visitor" and cam_s:
            try:
                items = await redis.lrange(cls.TTS_QUEUE_KEY, 0, -1)
                block_kind: str | None = None
                for it in items or []:
                    _s, obj = cls._parse_queue_item(it)
                    if not obj:
                        continue
                    if str(obj.get("task_id") or "") == task_id:
                        continue
                    o_kind = str(obj.get("kind") or "")
                    o_cam = str(
                        obj.get("camera_id") if obj.get("camera_id") is not None else ""
                    )
                    if o_cam != cam_s:
                        continue
                    if o_kind == "member" or o_kind in (
                        "test_speak_camera",
                        "test_speak",
                    ):
                        block_kind = o_kind
                        break
                if block_kind:
                    try:
                        await redis.lrem(cls.TTS_QUEUE_KEY, 1, raw)
                    except Exception:
                        logger.debug("陌生迎宾 TOCTOU LREM 失败", exc_info=True)
                    skip_reason = (
                        "member_pending"
                        if block_kind == "member"
                        else "test_speak_pending"
                    )
                    logger.info(
                        "跳过陌生迎宾（入队后发现 %s） camera_id=%s",
                        block_kind,
                        payload.get("camera_id"),
                    )
                    await cls.set_speak_task_status(
                        task_id,
                        "cancelled",
                        reason=skip_reason,
                        camera_id=payload.get("camera_id"),
                    )
                    return None
            except Exception:
                logger.debug("陌生迎宾 TOCTOU 复检失败，保留入队", exc_info=True)
        # 并发试听/会员：两路都 RPUSH 后互相看不见；入队后再扫同机位，只留本 task
        if cam_s and (kind.startswith("test_speak") or kind == "member"):
            try:
                items = await redis.lrange(cls.TTS_QUEUE_KEY, 0, -1)
                dropped = 0
                for it in items or []:
                    s, obj = cls._parse_queue_item(it)
                    if not obj:
                        continue
                    if str(obj.get("task_id") or "") == task_id:
                        continue
                    o_kind = str(obj.get("kind") or "")
                    o_cam = str(
                        obj.get("camera_id") if obj.get("camera_id") is not None else ""
                    )
                    drop_reason = cls._pending_drop_reason(
                        kind, o_kind, o_cam == cam_s
                    )
                    if not drop_reason:
                        continue
                    try:
                        await redis.lrem(cls.TTS_QUEUE_KEY, 1, s)
                    except Exception:
                        logger.debug(
                            "试听/会员 keep-latest LREM 失败", exc_info=True
                        )
                        continue
                    dropped += 1
                    await cls.set_speak_task_status(
                        obj.get("task_id"),
                        "cancelled",
                        reason=drop_reason,
                        kind=o_kind,
                        camera_id=obj.get("camera_id"),
                    )
                if dropped:
                    logger.info(
                        "入队后 keep-latest 作废同机位旧项 kind=%s camera_id=%s dropped=%s",
                        kind,
                        payload.get("camera_id"),
                        dropped,
                    )
            except Exception:
                logger.debug("试听/会员入队后 keep-latest 失败", exc_info=True)
        try:
            await cls.set_speak_task_status(
                task_id,
                "queued",
                kind=kind,
                camera_id=payload.get("camera_id"),
            )
        except Exception:
            logger.exception(
                "迎宾入队后写 speak_task 失败 task_id=%s（队列已有项，勿再入队）",
                task_id[:12],
            )
        try:
            await cls._bump_speak_gen_after_enqueue(kind, payload.get("camera_id"))
        except Exception:
            logger.exception(
                "迎宾入队后 bump 失败 task_id=%s（队列已有项，勿再入队）",
                task_id[:12],
            )
        cls._schedule_g711_warmup(text)
        return task_id

    @classmethod
    async def enqueue(cls, item: dict) -> str | None:
        """入队迎宾播报；跨热重载仍可由 Leader TTS 消费。返回 task_id（空文本返回 None）。"""
        import json
        import time as _time
        import uuid

        payload = dict(item or {})
        text = str(payload.get("text") or "").strip()
        if not text:
            return None
        payload["text"] = text
        kind = str(payload.get("kind") or "")
        cam_s = str(payload.get("camera_id") if payload.get("camera_id") is not None else "")
        payload.setdefault("enqueued_at", _time.time())
        payload.setdefault("enqueued_mono", _time.monotonic())
        task_id = str(payload.get("task_id") or "").strip() or uuid.uuid4().hex
        payload["task_id"] = task_id
        # bump 须在入队成功后：避免 queue_full/跳过仍抬世代打断在播却无替补
        raw = json.dumps(payload, ensure_ascii=False)
        redis_pushed = False
        try:
            redis = await get_redis()
            # 试听连点 / 会员入队：整理 pending 再写入
            if kind.startswith("test_speak") or kind == "member":
                try:
                    items = await redis.lrange(cls.TTS_QUEUE_KEY, 0, -1)
                    dropped = 0
                    drop_raws: list[str] = []
                    for it in items or []:
                        s, obj = cls._parse_queue_item(it)
                        if obj is None:
                            continue
                        o_kind = str(obj.get("kind") or "")
                        o_cam = str(obj.get("camera_id") if obj.get("camera_id") is not None else "")
                        drop_reason = cls._pending_drop_reason(
                            kind, o_kind, o_cam == cam_s and bool(cam_s)
                        )
                        if drop_reason:
                            dropped += 1
                            drop_raws.append(s)
                            await cls.set_speak_task_status(
                                obj.get("task_id"),
                                "cancelled",
                                reason=drop_reason,
                                kind=o_kind,
                                camera_id=obj.get("camera_id"),
                            )
                    # LREM 精确删待替换项，避免 DELETE 整表抹掉并发入队；队列 FIFO=RPUSH+BLPOP
                    pipe = redis.pipeline()
                    for s in drop_raws:
                        pipe.lrem(cls.TTS_QUEUE_KEY, 1, s)
                    pipe.rpush(cls.TTS_QUEUE_KEY, raw)
                    pipe.ltrim(cls.TTS_QUEUE_KEY, -cls.TTS_QUEUE_MAX, -1)
                    await pipe.execute()
                    redis_pushed = True
                    if dropped:
                        logger.info(
                            "迎宾入队替换 pending=%s kind=%s camera_id=%s",
                            dropped,
                            kind,
                            payload.get("camera_id"),
                        )
                    return await cls._finalize_redis_enqueue(
                        task_id=task_id,
                        kind=kind,
                        payload=payload,
                        text=text,
                        raw=raw,
                        cam_s=cam_s,
                        redis=redis,
                    )
                except Exception:
                    if redis_pushed:
                        logger.exception(
                            "迎宾整理入队后收尾失败，队列已有项勿再入队 task_id=%s",
                            task_id[:12],
                        )
                        return await cls._finalize_redis_enqueue(
                            task_id=task_id,
                            kind=kind,
                            payload=payload,
                            text=text,
                            raw=raw,
                            cam_s=cam_s,
                            redis=redis,
                        )
                    logger.exception("迎宾队列整理失败，回退普通入队前尽力抢占")
                    # 整理失败时仍尽量去掉同机位低优先级句，避免会员排在试听/欢迎光临后
                    if kind == "member" and cam_s:
                        try:
                            items = await redis.lrange(cls.TTS_QUEUE_KEY, 0, -1)
                            for it in items or []:
                                s, obj = cls._parse_queue_item(it)
                                if not obj:
                                    continue
                                o_kind = str(obj.get("kind") or "")
                                o_cam = str(
                                    obj.get("camera_id")
                                    if obj.get("camera_id") is not None
                                    else ""
                                )
                                reason = cls._pending_drop_reason(
                                    "member", o_kind, o_cam == cam_s
                                )
                                if not reason:
                                    continue
                                try:
                                    await redis.lrem(cls.TTS_QUEUE_KEY, 1, s)
                                except Exception:
                                    pass
                                await cls.set_speak_task_status(
                                    obj.get("task_id"),
                                    "cancelled",
                                    reason=reason,
                                    kind=o_kind,
                                    camera_id=obj.get("camera_id"),
                                )
                        except Exception:
                            logger.debug("会员回退抢占失败", exc_info=True)
            if kind == "unknown_visitor" and cam_s:
                # 已有同机位会员或摄像头试听待播则不再塞陌生句
                try:
                    items = await redis.lrange(cls.TTS_QUEUE_KEY, 0, -1)
                    for it in items or []:
                        _s, obj = cls._parse_queue_item(it)
                        if not obj:
                            continue
                        o_kind = str(obj.get("kind") or "")
                        o_cam = str(
                            obj.get("camera_id") if obj.get("camera_id") is not None else ""
                        )
                        if o_cam != cam_s:
                            continue
                        if o_kind == "member" or o_kind in (
                            "test_speak_camera",
                            "test_speak",
                        ):
                            skip_reason = (
                                "member_pending"
                                if o_kind == "member"
                                else "test_speak_pending"
                            )
                            logger.info(
                                "跳过陌生迎宾（队列已有 %s） camera_id=%s",
                                o_kind,
                                payload.get("camera_id"),
                            )
                            await cls.set_speak_task_status(
                                task_id,
                                "cancelled",
                                reason=skip_reason,
                                camera_id=payload.get("camera_id"),
                            )
                            return None
                except Exception:
                    logger.debug("检查会员/试听待播失败，继续陌生入队", exc_info=True)
            pipe = redis.pipeline()
            pipe.rpush(cls.TTS_QUEUE_KEY, raw)
            pipe.ltrim(cls.TTS_QUEUE_KEY, -cls.TTS_QUEUE_MAX, -1)
            await pipe.execute()
            redis_pushed = True
            return await cls._finalize_redis_enqueue(
                task_id=task_id,
                kind=kind,
                payload=payload,
                text=text,
                raw=raw,
                cam_s=cam_s,
                redis=redis,
            )
        except Exception:
            if redis_pushed:
                logger.exception(
                    "迎宾 Redis 入队已成功但收尾异常，禁止内存再入队 task_id=%s",
                    task_id[:12],
                )
                # 与整理路径对称：再试一次 finalize（keep-latest / queued / bump）
                try:
                    return await cls._finalize_redis_enqueue(
                        task_id=task_id,
                        kind=kind,
                        payload=payload,
                        text=text,
                        raw=raw,
                        cam_s=cam_s,
                        redis=redis,
                    )
                except Exception:
                    logger.exception(
                        "迎宾 Redis finalize 重试仍失败 task_id=%s", task_id[:12]
                    )
                    return task_id
            logger.exception("迎宾 TTS Redis 入队失败，回退进程内队列")
        q = cls.get_queue()
        # 内存降级：与 Redis 路径对齐 member_pending / 试听替换 / 会员抢占（drain 再回填，不碰私有 _queue）
        try:
            pending_items: list = []
            while True:
                try:
                    pending_items.append(q.get_nowait())
                except asyncio.QueueEmpty:
                    break
            if kind == "unknown_visitor" and cam_s:
                block = next(
                    (
                        p
                        for p in pending_items
                        if isinstance(p, dict)
                        and str(p.get("camera_id") if p.get("camera_id") is not None else "")
                        == cam_s
                        and str(p.get("kind") or "")
                        in ("member", "test_speak_camera", "test_speak")
                    ),
                    None,
                )
                if block is not None:
                    for item in pending_items:
                        try:
                            q.put_nowait(item)
                        except asyncio.QueueFull:
                            break
                    b_kind = str(block.get("kind") or "")
                    await cls.set_speak_task_status(
                        task_id,
                        "cancelled",
                        reason=(
                            "member_pending"
                            if b_kind == "member"
                            else "test_speak_pending"
                        ),
                        camera_id=payload.get("camera_id"),
                        transport="memory",
                    )
                    return None
            kept: list = []
            for pending in pending_items:
                if not isinstance(pending, dict):
                    kept.append(pending)
                    continue
                o_kind = str(pending.get("kind") or "")
                o_cam = str(
                    pending.get("camera_id") if pending.get("camera_id") is not None else ""
                )
                drop_reason = cls._pending_drop_reason(
                    kind, o_kind, o_cam == cam_s and bool(cam_s)
                )
                if drop_reason:
                    await cls.set_speak_task_status(
                        pending.get("task_id"),
                        "cancelled",
                        reason=drop_reason,
                        kind=o_kind,
                        camera_id=pending.get("camera_id"),
                        transport="memory",
                    )
                else:
                    kept.append(pending)
            for item in kept:
                try:
                    q.put_nowait(item)
                except asyncio.QueueFull:
                    logger.warning("迎宾内存队列整理回填满，丢弃一项")
                    break
        except Exception:
            logger.debug("迎宾内存队列整理失败，继续入队", exc_info=True)
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            logger.warning("迎宾 TTS 进程内队列已满，丢弃 text=%s", text[:40])
            await cls.set_speak_task_status(
                task_id, "failed", error="queue_full", camera_id=payload.get("camera_id")
            )
            # 返回 None：调用方须释放冷却，避免无声占窗；且勿 bump
            return None
        # 对齐 Redis T2：put 后再扫，并发试听/会员只留本 task
        if cam_s and (kind.startswith("test_speak") or kind == "member"):
            try:
                after: list = []
                while True:
                    try:
                        after.append(q.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                kept_latest: list = []
                dropped = 0
                for p in after:
                    if not isinstance(p, dict):
                        kept_latest.append(p)
                        continue
                    if str(p.get("task_id") or "") == task_id:
                        kept_latest.append(p)
                        continue
                    o_kind = str(p.get("kind") or "")
                    o_cam = str(
                        p.get("camera_id") if p.get("camera_id") is not None else ""
                    )
                    drop_reason = cls._pending_drop_reason(
                        kind, o_kind, o_cam == cam_s
                    )
                    if drop_reason:
                        dropped += 1
                        await cls.set_speak_task_status(
                            p.get("task_id"),
                            "cancelled",
                            reason=drop_reason,
                            kind=o_kind,
                            camera_id=p.get("camera_id"),
                            transport="memory",
                        )
                    else:
                        kept_latest.append(p)
                for p in kept_latest:
                    try:
                        q.put_nowait(p)
                    except asyncio.QueueFull:
                        logger.warning("迎宾内存 keep-latest 回填满，丢弃一项")
                        break
                if dropped:
                    logger.info(
                        "内存入队后 keep-latest 作废同机位旧项 kind=%s camera_id=%s dropped=%s",
                        kind,
                        payload.get("camera_id"),
                        dropped,
                    )
            except Exception:
                logger.debug("内存试听/会员 keep-latest 失败", exc_info=True)
        # 对齐 Redis R3：入队后再扫，避免 TOCTOU 把陌生句插在并发会员/试听后
        if kind == "unknown_visitor" and cam_s:
            try:
                after: list = []
                while True:
                    try:
                        after.append(q.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                block_kind: str | None = None
                for p in after:
                    if not isinstance(p, dict):
                        continue
                    if str(p.get("task_id") or "") == task_id:
                        continue
                    if (
                        str(p.get("camera_id") if p.get("camera_id") is not None else "")
                        != cam_s
                    ):
                        continue
                    o_kind = str(p.get("kind") or "")
                    if o_kind == "member" or o_kind in (
                        "test_speak_camera",
                        "test_speak",
                    ):
                        block_kind = o_kind
                        break
                if block_kind:
                    for p in after:
                        if isinstance(p, dict) and str(p.get("task_id") or "") == task_id:
                            continue
                        try:
                            q.put_nowait(p)
                        except asyncio.QueueFull:
                            break
                    skip_reason = (
                        "member_pending"
                        if block_kind == "member"
                        else "test_speak_pending"
                    )
                    logger.info(
                        "跳过陌生迎宾（内存入队后发现 %s） camera_id=%s",
                        block_kind,
                        payload.get("camera_id"),
                    )
                    await cls.set_speak_task_status(
                        task_id,
                        "cancelled",
                        reason=skip_reason,
                        camera_id=payload.get("camera_id"),
                        transport="memory",
                    )
                    return None
                for p in after:
                    try:
                        q.put_nowait(p)
                    except asyncio.QueueFull:
                        logger.warning("迎宾内存队列 TOCTOU 回填满，丢弃一项")
                        break
            except Exception:
                logger.debug("内存陌生迎宾 TOCTOU 复检失败，保留入队", exc_info=True)
        await cls.set_speak_task_status(
            task_id,
            "queued",
            kind=kind,
            camera_id=payload.get("camera_id"),
            transport="memory",
        )
        await cls._bump_speak_gen_after_enqueue(kind, payload.get("camera_id"))
        cls._schedule_g711_warmup(text)
        return task_id

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
    def is_stale_item(cls, item: dict | None) -> bool:
        """排队过久的迎宾（人多半已离开）不再播。优先 monotonic，兼容旧墙钟字段。"""
        import time as _time

        if not item:
            return True
        kind = str(item.get("kind") or "")
        # 试听不按过期丢弃
        if kind.startswith("test_speak"):
            return False
        age: float | None = None
        try:
            mono = item.get("enqueued_mono")
            if mono is not None:
                age = _time.monotonic() - float(mono)
        except (TypeError, ValueError):
            age = None
        if age is None:
            try:
                enq = float(item.get("enqueued_at") or 0.0)
            except (TypeError, ValueError):
                enq = 0.0
            if enq <= 0:
                return False
            age = _time.time() - enq
        return age > float(cls.TTS_ITEM_MAX_AGE_SEC)

    @classmethod
    async def _take_memory_item_nowait(cls) -> dict | None:
        """非阻塞取一条内存待播；过期则作废并继续，空则 None。"""
        q = cls.get_queue()
        while True:
            try:
                item = q.get_nowait()
            except asyncio.QueueEmpty:
                return None
            if isinstance(item, dict) and cls.is_stale_item(item):
                logger.info(
                    "丢弃过期迎宾(内存) kind=%s camera_id=%s",
                    item.get("kind"),
                    item.get("camera_id"),
                )
                await cls.set_speak_task_status(
                    item.get("task_id"),
                    "cancelled",
                    reason="stale",
                    kind=item.get("kind"),
                    camera_id=item.get("camera_id"),
                )
                continue
            return item if isinstance(item, dict) else None

    @classmethod
    async def dequeue(cls, timeout: float = 1.0) -> dict | None:
        """取出一条迎宾任务；超时返回 None。过期项直接丢弃并继续取。

        Redis 恢复后仍须排空故障窗口写入的内存待播，避免「入队成功却永不出声」。
        """
        import json
        import time as _time

        deadline = _time.monotonic() + max(0.2, float(timeout))
        try:
            redis = await get_redis()
            while _time.monotonic() < deadline:
                # 先排空内存 backlog（Redis 宕机窗口入队），再 BLPOP
                mem = await cls._take_memory_item_nowait()
                if mem is not None:
                    return mem
                wait = max(1, int(min(1.0, deadline - _time.monotonic() + 0.01)))
                # FIFO：与入队 RPUSH 配对用 BLPOP（取最旧）；BRPOP 会变成 LIFO 饿死旧任务
                got = await redis.blpop(cls.TTS_QUEUE_KEY, timeout=wait)
                if not got:
                    # 空等后勿直接放弃：再扫一次内存（并发入队可能刚 put）
                    mem = await cls._take_memory_item_nowait()
                    if mem is not None:
                        return mem
                    continue
                _key, raw = got
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                data = json.loads(raw)
                if not isinstance(data, dict):
                    continue
                if cls.is_stale_item(data):
                    logger.info(
                        "丢弃过期迎宾 kind=%s camera_id=%s age=%.1fs text=%s",
                        data.get("kind"),
                        data.get("camera_id"),
                        _time.time() - float(data.get("enqueued_at") or 0),
                        str(data.get("text") or "")[:40],
                    )
                    await cls.set_speak_task_status(
                        data.get("task_id"),
                        "cancelled",
                        reason="stale",
                        kind=data.get("kind"),
                        camera_id=data.get("camera_id"),
                    )
                    continue
                return data
            return await cls._take_memory_item_nowait()
        except Exception:
            logger.exception("迎宾 TTS Redis 出队失败，回退进程内队列")
        q = cls.get_queue()
        # 与 Redis 路径一致：过期项丢弃后继续取，勿因一条 stale 直接空返回饿死后项
        deadline = _time.monotonic() + max(0.2, float(timeout))
        while _time.monotonic() < deadline:
            remain = deadline - _time.monotonic()
            try:
                item = await asyncio.wait_for(q.get(), timeout=max(0.05, remain))
            except asyncio.TimeoutError:
                return None
            if isinstance(item, dict) and cls.is_stale_item(item):
                logger.info(
                    "丢弃过期迎宾(内存) kind=%s camera_id=%s",
                    item.get("kind"),
                    item.get("camera_id"),
                )
                await cls.set_speak_task_status(
                    item.get("task_id"),
                    "cancelled",
                    reason="stale",
                    kind=item.get("kind"),
                    camera_id=item.get("camera_id"),
                )
                continue
            return item
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
    async def release_cooldown(customer_id: int) -> None:
        """入队失败或顾客无效时回滚冷却，避免长时间误冷却。"""
        try:
            redis = await get_redis()
            await redis.delete(welcome_cooldown_key(customer_id))
        except Exception:
            logger.debug("release welcome cooldown failed customer_id=%s", customer_id, exc_info=True)

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

            now_mono = _time.monotonic()
            cd_key = f"{int(camera_id or 0)}:{int(customer_id)}"
            if now_mono - cls._last_member_cd_log_at.get(cd_key, 0.0) >= 15.0:
                cls._last_member_cd_log_at[cd_key] = now_mono
                logger.info(
                    "会员迎宾冷却中 customer_id=%s camera_id=%s",
                    customer_id,
                    camera_id,
                )
            return False

        customer = await db.get(Customer, customer_id)
        if not customer:
            logger.warning("会员迎宾顾客不存在 customer_id=%s，回滚冷却", customer_id)
            await cls.release_cooldown(customer_id)
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
        # 主模板或附句模板含 {口味}/{餐品} 才查库（默认副句含口味，须纳入否则永远不生效）
        need_taste = cls.template_has_placeholder(
            greeting_template or "", "口味", "taste"
        ) or cls.template_has_placeholder(
            preference_template or "", "口味", "taste"
        )
        need_product = cls.template_has_placeholder(
            greeting_template or "", "餐品", "product"
        ) or cls.template_has_placeholder(
            product_template or "", "餐品", "product"
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
        tid = None
        try:
            tid = await cls.enqueue(
                {
                    "customer_id": customer_id,
                    "text": text,
                    "camera_id": camera_id,
                    "kind": "member",
                }
            )
        except Exception:
            logger.exception(
                "会员迎宾入队失败，回滚冷却 customer_id=%s camera_id=%s",
                customer_id,
                camera_id,
            )
            await cls.release_cooldown(customer_id)
            raise
        if not tid:
            # queue_full / 空文本等：释放冷却，避免无声占窗
            logger.warning(
                "会员迎宾未入队，回滚冷却 customer_id=%s camera_id=%s",
                customer_id,
                camera_id,
            )
            await cls.release_cooldown(customer_id)
            return False

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

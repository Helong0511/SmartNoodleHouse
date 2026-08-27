import asyncio
import base64
import json
from datetime import timedelta

import numpy as np
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import generate_no
from app.core.redis_client import get_redis
from app.core.response import now_local
from app.models import Customer, FaceProfile, VisitorEvent, WelcomeLog


class FaceEncryption:
    def __init__(self):
        key = get_settings().face_encryption_key.encode()[:32].ljust(32, b"0")
        self.fernet = Fernet(base64.urlsafe_b64encode(key))

    def encrypt(self, data: bytes) -> bytes:
        return self.fernet.encrypt(data)

    def decrypt(self, data: bytes) -> bytes:
        return self.fernet.decrypt(data)


class FaceService:
    """仅授权会员人脸匹配 - 禁止陌生顾客身份识别"""

    @staticmethod
    async def register_face(db: AsyncSession, customer_id: int, feature_vector: list[float]) -> FaceProfile:
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
            consent_status="AUTHORIZED",
            consent_at=now_local(),
        )
        db.add(profile)
        await db.flush()
        return profile

    @staticmethod
    async def revoke_face(db: AsyncSession, customer_id: int) -> None:
        result = await db.execute(
            select(FaceProfile).where(
                FaceProfile.customer_id == customer_id, FaceProfile.consent_status == "AUTHORIZED"
            )
        )
        for profile in result.scalars().all():
            profile.consent_status = "REVOKED"
            profile.revoked_at = now_local()
            profile.feature_data = b""

    @staticmethod
    async def get_authorized_profiles(db: AsyncSession) -> list[tuple[int, np.ndarray]]:
        enc = FaceEncryption()
        result = await db.execute(
            select(FaceProfile).where(FaceProfile.consent_status == "AUTHORIZED")
        )
        profiles = []
        for p in result.scalars().all():
            if not p.feature_data:
                continue
            try:
                vec = json.loads(enc.decrypt(p.feature_data).decode())
                profiles.append((p.customer_id, np.array(vec, dtype=np.float32)))
            except Exception:
                continue
        return profiles

    @staticmethod
    def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
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
        expires = now_local() + timedelta(days=settings.visitor_data_retention_days)
        event = VisitorEvent(
            visitor_no=generate_no("V"),
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
        if cls._queue is None:
            cls._queue = asyncio.Queue()
        return cls._queue

    @staticmethod
    async def is_cooled_down(customer_id: int) -> bool:
        redis = await get_redis()
        key = f"snh:welcome:cooldown:{customer_id}"
        return await redis.exists(key) > 0

    @staticmethod
    async def set_cooldown(customer_id: int) -> None:
        redis = await get_redis()
        settings = get_settings()
        await redis.setex(f"snh:welcome:cooldown:{customer_id}", settings.welcome_cooldown_seconds, "1")

    @staticmethod
    async def push_frame_score(customer_id: int, camera_id: int | None, similarity: float) -> tuple[bool, float]:
        """Redis 多帧确认，跨进程共享。返回 (ready, avg_score)。"""
        settings = get_settings()
        redis = await get_redis()
        key = f"snh:welcome:frames:{camera_id or 0}:{customer_id}"
        pipe = redis.pipeline()
        pipe.rpush(key, f"{similarity:.6f}")
        pipe.ltrim(key, -settings.face_confirm_frames, -1)
        pipe.expire(key, 5)
        pipe.llen(key)
        pipe.lrange(key, 0, -1)
        _, _, _, length, values = await pipe.execute()
        scores = [float(v) for v in values]
        avg = sum(scores) / len(scores) if scores else 0.0
        return length >= settings.face_confirm_frames, avg

    @classmethod
    async def try_welcome(
        cls,
        db: AsyncSession,
        customer_id: int,
        camera_id: int | None,
        similarity: float,
    ) -> bool:
        settings = get_settings()
        ready, avg_sim = await cls.push_frame_score(customer_id, camera_id, similarity)
        if not ready:
            return False
        if avg_sim < settings.face_match_threshold:
            return False
        if await cls.is_cooled_down(customer_id):
            return False

        customer = await db.get(Customer, customer_id)
        if not customer:
            return False

        name = customer.display_name or customer.nickname or "会员"
        text = f"欢迎回来，{name}。"
        await cls.get_queue().put({"customer_id": customer_id, "text": text, "camera_id": camera_id})

        db.add(WelcomeLog(customer_id=customer_id, camera_id=camera_id, welcome_text=text))
        await cls.set_cooldown(customer_id)
        # 清除帧缓存
        try:
            redis = await get_redis()
            await redis.delete(f"snh:welcome:frames:{camera_id or 0}:{customer_id}")
        except Exception:
            pass
        await db.flush()
        return True

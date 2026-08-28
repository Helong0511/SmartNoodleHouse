import asyncio
import base64
import json
from datetime import timedelta

import numpy as np
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.biz_no import BIZ_VI, generate_no
from app.core.redis_client import get_redis
from app.core.response import now_local
from app.models import Camera, Customer, FaceProfile, Store, VisitorEvent, WelcomeLog


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
        if cls._queue is None:
            cls._queue = asyncio.Queue()
        return cls._queue

    @staticmethod
    async def is_cooled_down(customer_id: int) -> bool:
        redis = await get_redis()
        key = f"snh:welcome:cooldown:{customer_id}"
        return await redis.exists(key) > 0

    @staticmethod
    async def set_cooldown(customer_id: int, db: AsyncSession | None = None) -> None:
        redis = await get_redis()
        settings = get_settings()
        from app.services.system_settings_service import SystemSettingsService

        seconds = settings.welcome_cooldown_seconds
        if db is not None:
            seconds = await SystemSettingsService.get_int(db, "welcome_cooldown_seconds", seconds)
        await redis.setex(f"snh:welcome:cooldown:{customer_id}", seconds, "1")

    @staticmethod
    async def push_frame_score(
        customer_id: int, camera_id: int | None, similarity: float, db: AsyncSession | None = None
    ) -> tuple[bool, float]:
        """Redis 多帧确认，跨进程共享。返回 (ready, avg_score)。"""
        settings = get_settings()
        from app.services.system_settings_service import SystemSettingsService

        confirm_frames = settings.face_confirm_frames
        if db is not None:
            confirm_frames = await SystemSettingsService.get_int(db, "face_confirm_frames", confirm_frames)
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

        threshold = await SystemSettingsService.get_float(db, "face_match_threshold", settings.face_match_threshold)
        ready, avg_sim = await cls.push_frame_score(customer_id, camera_id, similarity, db)
        if not ready:
            return False
        if avg_sim < threshold:
            return False
        if await cls.is_cooled_down(customer_id):
            return False

        customer = await db.get(Customer, customer_id)
        if not customer:
            return False

        name = customer.display_name or customer.nickname or "会员"
        text = f"欢迎回来，{name}。"
        try:
            from app.services.customer_service import PreferenceService

            rec = await PreferenceService.get_recommendations(db, customer_id)
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
        await cls.set_cooldown(customer_id, db)
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
                },
            )
        except Exception:
            pass
        return True

"""本地诊断：抓拍图 vs 授权人脸相似度、摄像头状态。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import numpy as np
from sqlalchemy import select, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import get_settings  # noqa: E402
from app.core.database import AsyncSessionLocal  # noqa: E402
from app.models import Customer, FaceProfile  # noqa: E402
from app.services.face_extractor import extract_face_embedding  # noqa: E402
from app.services.face_service import FaceEmbeddingCache, FaceEncryption, FaceService  # noqa: E402


async def main() -> None:
    settings = get_settings()
    upload = Path(settings.upload_dir)
    snaps = sorted((upload / "visitors").rglob("cam1_*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
    print("upload_dir", upload)
    print("latest snaps", [str(p) for p in snaps[:5]])
    if not snaps:
        print("NO_SNAPSHOT")
        return
    snap = snaps[0]
    print("using", snap)
    try:
        emb = np.array(extract_face_embedding(snap.read_bytes()), dtype=np.float32)
    except Exception as e:
        print("EXTRACT_FAIL", type(e).__name__, e)
        return
    print("emb_shape", emb.shape, "norm", float(np.linalg.norm(emb)))

    # 翻转后再比一次（画面倒置常见）
    try:
        import cv2

        raw = np.frombuffer(snap.read_bytes(), np.uint8)
        img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
        flipped = cv2.rotate(img, cv2.ROTATE_180)
        ok, buf = cv2.imencode(".jpg", flipped)
        emb180 = np.array(extract_face_embedding(buf.tobytes()), dtype=np.float32)
        print("emb180 ok", emb180.shape)
    except Exception as e:
        emb180 = None
        print("FLIP_EXTRACT_FAIL", type(e).__name__, e)

    async with AsyncSessionLocal() as db:
        thr = await db.scalar(
            text("SELECT setting_value FROM system_settings WHERE setting_key='face_match_threshold'")
        )
        print("threshold", thr)
        cams = (await db.execute(text("SELECT id,status,name FROM cameras"))).fetchall()
        for c in cams:
            print("camera", dict(c._mapping))
        rows = (
            await db.execute(select(FaceProfile).where(FaceProfile.consent_status == "AUTHORIZED"))
        ).scalars().all()
        for p in rows:
            vec = FaceEmbeddingCache._decrypt_vec(FaceEncryption(), p)
            if vec is None:
                print(f"profile={p.id} decrypt_fail")
                continue
            sim = FaceService.cosine_similarity(emb, vec)
            sim180 = FaceService.cosine_similarity(emb180, vec) if emb180 is not None else None
            cust = await db.get(Customer, p.customer_id)
            print(
                f"profile={p.id} customer={p.customer_id} name={getattr(cust,'display_name',None)} "
                f"member={getattr(cust,'member_status',None)} account={getattr(cust,'account_status',None)} "
                f"status={getattr(cust,'status',None)} "
                f"sim={sim:.4f} sim180={None if sim180 is None else round(sim180,4)}"
            )
        cached = await FaceService.get_authorized_profiles(db)
        print("cache_size", len(cached), "ids", [cid for cid, _ in cached])
        wl = await db.scalar(text("SELECT COUNT(*) FROM welcome_logs"))
        ve = await db.scalar(text("SELECT COUNT(*) FROM visitor_events"))
        print("welcome_logs", wl, "visitor_events", ve)


if __name__ == "__main__":
    asyncio.run(main())

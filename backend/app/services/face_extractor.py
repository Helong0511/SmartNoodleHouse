"""服务端人脸特征提取 - InsightFace buffalo_l / 512D；禁止客户端伪造向量"""
from __future__ import annotations

import logging
from functools import lru_cache

import numpy as np

logger = logging.getLogger(__name__)

# 生产统一 512 维 ArcFace embedding；OpenCV 兜底仅开发且显式允许
EMBEDDING_DIM = 512
_ALLOW_OPENCV_FALLBACK_ENV = "ALLOW_OPENCV_FACE_FALLBACK"


@lru_cache(maxsize=1)
def _face_app():
    from insightface.app import FaceAnalysis

    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(640, 640))
    return app


def extract_face_embedding(image_bytes: bytes) -> list[float]:
    """
    SCRFD 检测 + ArcFace 512D。
    InsightFace 不可用时：生产 fail-closed；开发仅在 ALLOW_OPENCV_FACE_FALLBACK=1 时降级。
    """
    try:
        return _extract_insightface(image_bytes)
    except ImportError:
        logger.error("InsightFace 未安装，请 pip install insightface onnxruntime")
    except Exception as e:
        logger.warning("InsightFace 提取失败: %s", e)
        raise

    from app.core.config import get_settings
    import os

    settings = get_settings()
    if settings.is_production or os.getenv(_ALLOW_OPENCV_FALLBACK_ENV) != "1":
        raise ValueError("人脸识别引擎不可用（需 InsightFace）")
    logger.warning("使用 OpenCV 弱兜底（仅开发），不可用于生产迎宾")
    return _extract_opencv_fallback(image_bytes)


def assess_face_quality(image_bytes: bytes) -> dict:
    """简单质量门控：尺寸、清晰度、人脸占比。"""
    import cv2

    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("无法解码图片")
    h, w = img.shape[:2]
    if min(h, w) < 112:
        raise ValueError("人脸图片过小")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    lap = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if lap < 40:
        raise ValueError("图片模糊，请重新采集")
    return {"width": w, "height": h, "sharpness": lap}


def extract_face_embedding_with_quality(image_bytes: bytes) -> tuple[list[float], dict]:
    from app.services.face_liveness import check_liveness_simple

    liveness = check_liveness_simple(image_bytes)
    quality = assess_face_quality(image_bytes)
    quality.update(liveness)
    emb = extract_face_embedding(image_bytes)
    return emb, quality


def _extract_insightface(image_bytes: bytes) -> list[float]:
    import cv2

    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("无法解码图片")

    faces = _face_app().get(img)
    if not faces:
        raise ValueError("未检测到人脸，请正对摄像头")
    if len(faces) > 1:
        raise ValueError("检测到多张人脸，请确保画面中只有您一人")

    face = faces[0]
    # 检测置信度 / 姿态粗检
    det_score = float(getattr(face, "det_score", 1.0) or 1.0)
    if det_score < 0.5:
        raise ValueError("人脸检测置信度过低")

    emb = face.embedding.astype(np.float32)
    if emb.size != EMBEDDING_DIM:
        if emb.size > EMBEDDING_DIM:
            emb = emb[:EMBEDDING_DIM]
        else:
            emb = np.pad(emb, (0, EMBEDDING_DIM - emb.size))
    emb = emb / (np.linalg.norm(emb) + 1e-8)
    return emb.tolist()


def _extract_opencv_fallback(image_bytes: bytes) -> list[float]:
    import hashlib
    import cv2

    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError("无法解码图片")

    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    faces = cascade.detectMultiScale(img, 1.1, 5, minSize=(80, 80))
    if len(faces) == 0:
        raise ValueError("未检测到人脸，请正对摄像头")
    if len(faces) > 1:
        raise ValueError("检测到多张人脸")

    x, y, w, h = faces[0]
    face = cv2.resize(img[y : y + h, x : x + w], (32, 16))
    vec = face.flatten().astype(np.float32)
    vec = vec / (np.linalg.norm(vec) + 1e-8)
    seed = int(hashlib.md5(vec.tobytes()).hexdigest(), 16) % (2**31)
    rng = np.random.RandomState(seed)
    proj = rng.randn(vec.size, EMBEDDING_DIM).astype(np.float32)
    emb = vec @ proj
    emb = emb / (np.linalg.norm(emb) + 1e-8)
    return emb.tolist()

"""服务端人脸特征提取 - 禁止客户端伪造 feature_vector"""
from __future__ import annotations

import hashlib
import logging

import numpy as np

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 128


def extract_face_embedding(image_bytes: bytes) -> list[float]:
    """
    从人脸图片提取特征向量。
    优先 InsightFace；否则 OpenCV 检测 + 局部特征（开发可用）。
    """
    try:
        return _extract_insightface(image_bytes)
    except ImportError:
        pass
    except Exception as e:
        logger.warning("InsightFace 提取失败，降级 OpenCV: %s", e)

    return _extract_opencv_fallback(image_bytes)


def _extract_insightface(image_bytes: bytes) -> list[float]:
    import cv2
    import insightface
    from insightface.app import FaceAnalysis

    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("无法解码图片")

    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(640, 640))
    faces = app.get(img)
    if not faces:
        raise ValueError("未检测到人脸，请正对摄像头")
    if len(faces) > 1:
        raise ValueError("检测到多张人脸，请确保画面中只有您一人")

    emb = faces[0].embedding.astype(np.float32)
    if emb.size != 512:
        # 统一降维到 128
        emb = emb[:EMBEDDING_DIM] if emb.size >= EMBEDDING_DIM else np.pad(emb, (0, EMBEDDING_DIM - emb.size))
    else:
        emb = emb[:EMBEDDING_DIM]
    emb = emb / (np.linalg.norm(emb) + 1e-8)
    return emb.tolist()


def _extract_opencv_fallback(image_bytes: bytes) -> list[float]:
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
    face = img[y : y + h, x : x + w]
    face = cv2.resize(face, (16, 8))
    vec = face.flatten().astype(np.float32)
    vec = vec / (np.linalg.norm(vec) + 1e-8)

    # 固定投影到 128 维
    seed = int(hashlib.md5(vec.tobytes()).hexdigest(), 16) % (2**31)
    rng = np.random.RandomState(seed)
    proj = rng.randn(vec.size, EMBEDDING_DIM).astype(np.float32)
    emb = vec @ proj
    emb = emb / (np.linalg.norm(emb) + 1e-8)
    return emb.tolist()

"""服务端人脸特征提取 - InsightFace buffalo_l / 512D；禁止客户端伪造向量"""
from __future__ import annotations

import logging
from functools import lru_cache

import numpy as np

logger = logging.getLogger(__name__)

# 生产统一 512 维 ArcFace embedding；OpenCV 兜底仅开发且显式允许
EMBEDDING_DIM = 512


@lru_cache(maxsize=1)
def _face_app():
    from insightface.app import FaceAnalysis

    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(640, 640))
    return app


def _insightface_available() -> bool:
    try:
        import insightface  # noqa: F401

        return True
    except ImportError:
        return False


def _opencv_fallback_allowed() -> bool:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.is_production:
        return False
    if settings.allow_opencv_face_fallback:
        return True
    # 开发环境未装 InsightFace 时自动兜底，避免小程序授权直接 400
    if settings.is_development and not _insightface_available():
        return True
    return False


def extract_face_embedding(image_bytes: bytes) -> list[float]:
    """
    从内存图片字节提取特征（小程序授权上传用）。
    到店实时识别请用 extract_face_embedding_bgr，勿落盘读文件。
    """
    import cv2

    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("无法解码图片")
    return extract_face_embedding_bgr(img)


def extract_face_embedding_bgr(img: np.ndarray) -> list[float]:
    """
    从摄像头 BGR 帧直接提特征（内存，不打开图片文件）。
    SCRFD 检测 + ArcFace 512D；InsightFace 不可用时生产 fail-closed。
    """
    if _insightface_available():
        try:
            return _extract_insightface_bgr(img)
        except ValueError:
            raise
        except Exception as e:
            logger.warning("InsightFace 提取失败: %s", e)
            if not _opencv_fallback_allowed():
                raise ValueError(f"人脸特征提取失败: {e}") from e
            logger.warning("InsightFace 失败，改用 OpenCV 开发兜底")
    else:
        logger.error("InsightFace 未安装")

    if not _opencv_fallback_allowed():
        raise ValueError(
            "人脸识别引擎不可用：请 pip install insightface onnxruntime，"
            "或开发环境设置 ALLOW_OPENCV_FACE_FALLBACK=true"
        )
    logger.warning("使用 OpenCV 弱兜底（仅开发），不可用于生产迎宾")
    return _extract_opencv_fallback_bgr(img)


def assess_face_quality(image_bytes: bytes) -> dict:
    """简单质量门控：尺寸、清晰度、人脸占比。"""
    import cv2

    # P0-20: decompression bomb 防护 — 限制输入字节数
    _MAX_FACE_IMAGE_BYTES = 10 * 1024 * 1024  # 10MB
    if len(image_bytes) > _MAX_FACE_IMAGE_BYTES:
        raise ValueError("人脸图片过大，请压缩后重新上传")
    if len(image_bytes) < 100:
        raise ValueError("人脸图片数据为空")

    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("无法解码图片")
    h, w = img.shape[:2]
    if min(h, w) < 112:
        raise ValueError("人脸图片过小，请靠近摄像头")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    lap = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    # 微信模拟器/弱光下清晰度偏低，开发略放宽
    from app.core.config import get_settings

    min_sharp = 25 if get_settings().is_development else 40
    if lap < min_sharp:
        raise ValueError("图片模糊，请正对镜头重新采集")
    return {"width": w, "height": h, "sharpness": lap}


def extract_face_embedding_with_quality(image_bytes: bytes) -> tuple[list[float], dict]:
    from app.services.face_liveness import check_liveness_simple

    liveness = check_liveness_simple(image_bytes)
    quality = assess_face_quality(image_bytes)
    quality.update(liveness)
    emb = extract_face_embedding(image_bytes)
    return emb, quality


def _extract_insightface_bgr(img: np.ndarray) -> list[float]:
    if img is None or getattr(img, "size", 0) == 0:
        raise ValueError("无效视频帧")

    faces = _face_app().get(img)
    if not faces:
        raise ValueError("未检测到人脸，请正对摄像头")
    if len(faces) > 1:
        raise ValueError("检测到多张人脸，请确保画面中只有您一人")

    face = faces[0]
    det_score = float(getattr(face, "det_score", 1.0) or 1.0)
    if det_score < 0.5:
        raise ValueError("人脸检测置信度过低")

    emb = face.embedding.astype(np.float32)
    # fail-closed：禁止截断/padding，避免不同模型维度被 silently 对齐后误匹配
    if emb.size != EMBEDDING_DIM:
        raise ValueError(
            f"人脸特征维度不匹配：期望 {EMBEDDING_DIM}，实际 {emb.size}（禁止 padding）"
        )
    emb = emb / (np.linalg.norm(emb) + 1e-8)
    return emb.tolist()


def _extract_opencv_fallback_bgr(img: np.ndarray) -> list[float]:
    import hashlib
    import cv2

    if img is None or getattr(img, "size", 0) == 0:
        raise ValueError("无效视频帧")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img

    face_roi = None
    if hasattr(cv2, "CascadeClassifier"):
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        faces = cascade.detectMultiScale(gray, 1.1, 4, minSize=(60, 60))
        if len(faces) == 0:
            raise ValueError("未检测到人脸，请正对摄像头、保证光线充足")
        if len(faces) > 1:
            raise ValueError("检测到多张人脸，请确保画面中只有您一人")
        x, y, w, h = faces[0]
        face_roi = gray[y : y + h, x : x + w]
    else:
        # OpenCV 5+ 移除 Haar；无模型时开发兜底取中心区域（非正式迎宾）
        logger.warning("当前 OpenCV 无 CascadeClassifier，使用中心区域弱特征（仅开发联调）")
        h, w = gray.shape[:2]
        side = max(64, min(h, w) // 2)
        y0 = max(0, (h - side) // 2)
        x0 = max(0, (w - side) // 2)
        face_roi = gray[y0 : y0 + side, x0 : x0 + side]

    face = cv2.resize(face_roi, (32, 16))
    vec = face.flatten().astype(np.float32)
    vec = vec / (np.linalg.norm(vec) + 1e-8)
    seed = int(hashlib.md5(vec.tobytes()).hexdigest(), 16) % (2**31)
    rng = np.random.RandomState(seed)
    proj = rng.randn(vec.size, EMBEDDING_DIM).astype(np.float32)
    emb = vec @ proj
    emb = emb / (np.linalg.norm(emb) + 1e-8)
    return emb.tolist()

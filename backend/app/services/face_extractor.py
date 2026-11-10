"""服务端人脸特征提取 - InsightFace buffalo_l / 512D；禁止客户端伪造向量"""
from __future__ import annotations

import logging
import threading
from functools import lru_cache

import numpy as np

logger = logging.getLogger(__name__)

# 生产统一 512 维 ArcFace embedding；OpenCV 兜底仅开发且显式允许
EMBEDDING_DIM = 512
# onnxruntime / InsightFace 非完全线程安全；识别 to_thread 与预热并发时需串行
_INFER_LOCK = threading.Lock()


def _onnx_providers() -> list[str]:
    """优先 CUDA，否则 CPU（本机无 GPU 时自动回落）。"""
    try:
        import onnxruntime as ort

        available = set(ort.get_available_providers())
        ordered = [
            "CUDAExecutionProvider",
            "CPUExecutionProvider",
        ]
        return [p for p in ordered if p in available] or ["CPUExecutionProvider"]
    except Exception:
        return ["CPUExecutionProvider"]


def _assert_face_model_commercial_license() -> None:
    """生产加载 InsightFace 预训练模型前必须确认商业授权；代码 MIT 不代表模型可商用。"""
    from app.core.config import get_settings

    settings = get_settings()
    if settings.is_production and not getattr(settings, "face_model_commercial_license_confirmed", False):
        raise RuntimeError(
            "生产环境禁止加载未确认商业授权的 InsightFace 预训练模型；"
            "取得模型商业授权并设置 FACE_MODEL_COMMERCIAL_LICENSE_CONFIRMED=true 后再启用"
        )


@lru_cache(maxsize=1)
def _face_app():
    """授权入库用：较高检测分辨率，偏准。"""
    _assert_face_model_commercial_license()
    from insightface.app import FaceAnalysis

    app = FaceAnalysis(name="buffalo_l", providers=_onnx_providers())
    app.prepare(ctx_id=0, det_size=(640, 640))
    return app


@lru_cache(maxsize=1)
def _face_app_live():
    """到店实时识别：偏召回（门口远景/侧脸/弱光）。"""
    _assert_face_model_commercial_license()
    from insightface.app import FaceAnalysis

    app = FaceAnalysis(name="buffalo_l", providers=_onnx_providers())
    # det_thresh 默认 0.5 对门口远景过严；0.28 提高召回，误检由匹配阈值兜底
    app.prepare(ctx_id=0, det_size=(640, 640), det_thresh=0.28)
    return app


def ensure_live_face_app() -> None:
    """阻塞加载 live 模型（供启动预热 / RecognitionWorker 就绪门闩）。"""
    _face_app_live()


def is_live_face_ready() -> bool:
    """live 模型是否已在进程内缓存。"""
    return _face_app_live.cache_info().currsize > 0


def _enhance_for_detect(img: np.ndarray) -> np.ndarray:
    """弱光/低对比度帧增强，供检测失败时二次尝试。"""
    import cv2

    if img is None or getattr(img, "ndim", 0) != 3:
        return img
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l2 = clahe.apply(l)
    return cv2.cvtColor(cv2.merge([l2, a, b]), cv2.COLOR_LAB2BGR)


def _downscale_for_live(img: np.ndarray, *, max_side: int = 960) -> np.ndarray:
    """主码流偶发 2560x1440：全分辨率跑 buffalo+det640 又慢又易漏远景脸。

    长边压到 max_side（960：比 1280 更快出首检，门口脸仍够大）；入库不走此缩放。
    """
    import cv2

    if img is None or getattr(img, "ndim", 0) < 2:
        return img
    h, w = int(img.shape[0]), int(img.shape[1])
    side = max(h, w)
    if side <= max_side:
        return img
    scale = float(max_side) / float(side)
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    return cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)


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


def extract_face_embedding_bgr(
    img: np.ndarray,
    *,
    live: bool = False,
) -> list[float]:
    _assert_face_model_commercial_license()
    """
    从摄像头 BGR 帧直接提特征（内存，不打开图片文件）。
    SCRFD 检测 + ArcFace 512D；InsightFace 不可用时生产 fail-closed。

    live=True：到店实时路径——用较小 det_size，多人时取最大脸（门口常有陪同）。
    live=False：授权入库——要求画面仅一人，检测更严。
    """
    if _insightface_available():
        try:
            return _extract_insightface_bgr(img, live=live)
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


def extract_live_face_hit(img: np.ndarray, *, light: bool = False):
    _assert_face_model_commercial_license()
    """到店实时：返回 embedding + 脸框占比/清晰度/置信度，供短时窗口择优。

    light=True：窗口收集期用更小缩放、跳过 CLAHE 二次检测，降 CPU；
    定稿匹配前应对 best_frame 再跑 light=False 完整提特征。
    """
    from app.services.face_best_frame import LiveFaceHit

    src_h = int(img.shape[0]) if getattr(img, "ndim", 0) >= 2 else 0
    src_w = int(img.shape[1]) if getattr(img, "ndim", 0) >= 2 else 0
    if not _insightface_available():
        emb = extract_face_embedding_bgr(img, live=True)
        return LiveFaceHit(
            embedding=np.asarray(emb, dtype=np.float32),
            det_score=0.5,
            face_area_ratio=0.05,
            sharpness=40.0,
            face_side_px=0,
            frame_w=src_w,
            frame_h=src_h,
        )
    try:
        return _extract_insightface_live_hit(img, light=light)
    except ValueError:
        raise
    except Exception as e:
        logger.warning("InsightFace live hit 失败: %s", e)
        if not _opencv_fallback_allowed():
            raise ValueError(f"人脸特征提取失败: {e}") from e
        emb = _extract_opencv_fallback_bgr(img)
        return LiveFaceHit(
            embedding=np.asarray(emb, dtype=np.float32),
            det_score=0.4,
            face_area_ratio=0.05,
            sharpness=30.0,
            face_side_px=0,
            frame_w=src_w,
            frame_h=src_h,
        )


def _extract_insightface_bgr(img: np.ndarray, *, live: bool = False) -> list[float]:
    if live:
        return extract_live_face_hit(img).embedding.tolist()
    return _extract_insightface_enroll(img).tolist()


def _face_embedding_from_face(face) -> np.ndarray:
    raw = getattr(face, "normed_embedding", None)
    if raw is None:
        raw = face.embedding
    emb = np.asarray(raw, dtype=np.float32)
    if emb.size != EMBEDDING_DIM:
        raise ValueError(
            f"人脸特征维度不匹配：期望 {EMBEDDING_DIM}，实际 {emb.size}（禁止 padding）"
        )
    return emb / (np.linalg.norm(emb) + 1e-8)


def _pick_largest_face(faces):
    """多脸择优：面积 × det_score，避免大框低置信广告脸压过近处真人脸。"""
    return max(
        faces,
        key=lambda f: float(
            max(0.0, (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
            * max(0.05, float(getattr(f, "det_score", 1.0) or 1.0))
        ),
    )


def _face_roi_sharpness(work: np.ndarray, bbox) -> float:
    import cv2

    h, w = int(work.shape[0]), int(work.shape[1])
    x1 = max(0, int(bbox[0]))
    y1 = max(0, int(bbox[1]))
    x2 = min(w, int(bbox[2]))
    y2 = min(h, int(bbox[3]))
    if x2 <= x1 + 2 or y2 <= y1 + 2:
        return 0.0
    roi = work[y1:y2, x1:x2]
    if roi.size == 0:
        return 0.0
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if roi.ndim == 3 else roi
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _extract_insightface_live_hit(img: np.ndarray, *, light: bool = False):
    from app.services.face_best_frame import LiveFaceHit

    if img is None or getattr(img, "size", 0) == 0:
        raise ValueError("无效视频帧")
    src_h = int(img.shape[0]) if getattr(img, "ndim", 0) >= 2 else 0
    src_w = int(img.shape[1]) if getattr(img, "ndim", 0) >= 2 else 0
    # 收集期 720：略降像素量；定稿/首检仍 960
    work = _downscale_for_live(img, max_side=720 if light else 960)
    app = _face_app_live()
    with _INFER_LOCK:
        faces = app.get(work)
        if not faces and not light:
            enhanced = _enhance_for_detect(work)
            if enhanced is not None and enhanced is not work:
                faces = app.get(enhanced)
    if not faces:
        mean = float(np.mean(work)) if getattr(work, "size", 0) else 0.0
        raise ValueError(
            f"未检测到人脸，请正对摄像头（{src_w}x{src_h} mean={mean:.0f}）"
        )
    face = _pick_largest_face(faces)
    det_score = float(getattr(face, "det_score", 1.0) or 1.0)
    if det_score < 0.22:
        raise ValueError(f"人脸检测置信度过低 det_score={det_score:.3f}")
    bbox = face.bbox
    fw = max(0.0, float(bbox[2] - bbox[0]))
    fh = max(0.0, float(bbox[3] - bbox[1]))
    work_h = int(work.shape[0])
    work_w = int(work.shape[1])
    area = float(work_h * work_w) or 1.0
    ratio = (fw * fh) / area
    sharp = _face_roi_sharpness(work, bbox)
    emb = _face_embedding_from_face(face)
    # bbox 在 work 坐标系；短边映射回源分辨率，便于对照「门口距离 vs 像素」
    sx = float(src_w) / float(work_w) if work_w else 1.0
    sy = float(src_h) / float(work_h) if work_h else 1.0
    face_side_px = int(round(min(fw * sx, fh * sy)))
    return LiveFaceHit(
        embedding=emb,
        det_score=det_score,
        face_area_ratio=float(ratio),
        sharpness=sharp,
        face_side_px=face_side_px,
        frame_w=src_w,
        frame_h=src_h,
    )


def _extract_insightface_enroll(img: np.ndarray) -> np.ndarray:
    if img is None or getattr(img, "size", 0) == 0:
        raise ValueError("无效视频帧")
    src_h = int(img.shape[0]) if getattr(img, "ndim", 0) >= 2 else 0
    src_w = int(img.shape[1]) if getattr(img, "ndim", 0) >= 2 else 0
    work = img
    app = _face_app()
    with _INFER_LOCK:
        faces = app.get(work)
    if not faces:
        mean = float(np.mean(work)) if getattr(work, "size", 0) else 0.0
        raise ValueError(
            f"未检测到人脸，请正对摄像头（{src_w}x{src_h} mean={mean:.0f}）"
        )
    if len(faces) > 1:
        raise ValueError("检测到多张人脸，请确保画面中只有您一人")
    face = faces[0]
    det_score = float(getattr(face, "det_score", 1.0) or 1.0)
    if det_score < 0.5:
        raise ValueError(f"人脸检测置信度过低 det_score={det_score:.3f}")
    return _face_embedding_from_face(face)


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

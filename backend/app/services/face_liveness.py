"""人脸活体粗检 + 多帧跟踪（生产可换真活体 SDK）"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)

# ---------- 活体检测阈值常量（经验值，生产建议按实际环境标定） ----------
_MIN_SHARP_DEV = 20       # 开发环境最低清晰度（Laplacian 方差）
_MIN_SHARP_PROD = 35      # 生产环境最低清晰度
_MIN_BRIGHTNESS = 30      # 最低亮度（0-255 灰度均值）
_MAX_BRIGHTNESS = 230     # 最高亮度
_MIN_FACE_RATIO = 0.04    # 人脸占画面最小比例（过小则无法识别）
_MAX_FACE_RATIO = 0.90    # 人脸占画面最大比例（过近则畸变）
_MAX_YAW = 40             # 最大偏航角（度）
_MAX_PITCH = 35           # 最大俯仰角（度）


@dataclass
class TrackState:
    customer_id: int | None
    embeddings: list[np.ndarray] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)
    last_seen: float = 0.0
    frames: int = 0


class MultiFrameTracker:
    """摄像头维度的短时跟踪：连续 N 帧高相似才确认身份。

    近重复 embedding（与上一帧余弦 ≥ duplicate_cos）不计入确认进度，
    避免同一静止帧/缓冲复用被当成足够独立的证据。
    """

    def __init__(
        self,
        confirm_frames: int = 2,
        ttl_seconds: float = 2.5,
        *,
        duplicate_cos: float = 0.995,
    ):
        self.confirm_frames = confirm_frames
        self.ttl_seconds = ttl_seconds
        self.duplicate_cos = float(duplicate_cos)
        self._tracks: dict[str, TrackState] = {}

    def _key(self, camera_id: int, track_id: str = "default") -> str:
        return f"{camera_id}:{track_id}"

    def push(
        self,
        camera_id: int,
        embedding: np.ndarray,
        candidate_id: int | None,
        similarity: float,
        track_id: str = "default",
    ) -> tuple[bool, int | None, float]:
        """
        返回 (confirmed, customer_id, avg_similarity)
        """
        now = time.time()
        key = self._key(camera_id, track_id)
        st = self._tracks.get(key)
        if st is None or now - st.last_seen > self.ttl_seconds or (
            candidate_id is not None and st.customer_id is not None and candidate_id != st.customer_id
        ):
            st = TrackState(customer_id=candidate_id)
            self._tracks[key] = st

        st.last_seen = now
        emb = np.asarray(embedding, dtype=np.float32).reshape(-1)
        emb = emb / (np.linalg.norm(emb) + 1e-8)
        if st.embeddings:
            prev = np.asarray(st.embeddings[-1], dtype=np.float32).reshape(-1)
            prev = prev / (np.linalg.norm(prev) + 1e-8)
            if float(np.dot(emb, prev)) >= self.duplicate_cos:
                # 近重复：刷新时间但不增加确认帧数
                if st.scores:
                    return False, st.customer_id, float(np.mean(st.scores))
                return False, st.customer_id, float(similarity)

        st.frames += 1
        st.embeddings.append(emb)
        st.scores.append(similarity)
        if candidate_id is not None:
            st.customer_id = candidate_id

        if len(st.scores) < self.confirm_frames:
            return False, st.customer_id, float(np.mean(st.scores))

        avg = float(np.mean(st.scores[-self.confirm_frames :]))
        cid = st.customer_id
        # 确认后清空，避免重复触发
        self._tracks.pop(key, None)
        return True, cid, avg

    def cleanup(self) -> None:
        now = time.time()
        dead = [k for k, v in self._tracks.items() if now - v.last_seen > self.ttl_seconds * 3]
        for k in dead:
            self._tracks.pop(k, None)


def check_liveness_simple(image_bytes: bytes) -> dict:
    """
    轻量活体门控（basic anti-spoof，非对抗级）：
    - 解码成功
    - 拉普拉斯清晰度
    - 亮度合理
    - 人脸区域占比（若 InsightFace 可用则用 det bbox）
    当前**不是**真活体/挑战应答；生产建议接入厂商活体 SDK。
    UI / API 应标注 anti_spoof_level=basic（非 challenge / 非厂商 SDK）。
    """
    import cv2

    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("活体检测：无法解码图片")
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(np.mean(gray))
    from app.core.config import get_settings

    is_dev = get_settings().is_development
    min_sharp = _MIN_SHARP_DEV if is_dev else _MIN_SHARP_PROD
    if sharpness < min_sharp:
        raise ValueError("活体检测失败：画面模糊，请正对镜头、保证光线")
    if brightness < _MIN_BRIGHTNESS or brightness > _MAX_BRIGHTNESS:
        raise ValueError("活体检测失败：光线过暗或过曝")

    face_ratio = 0.0
    try:
        from app.services.face_extractor import _face_app, _insightface_available

        if not _insightface_available():
            raise ImportError("insightface missing")
        faces = _face_app().get(img)
        if not faces:
            raise ValueError("活体检测失败：未检测到人脸")
        if len(faces) > 1:
            raise ValueError("活体检测失败：多人脸")
        bbox = faces[0].bbox  # x1,y1,x2,y2
        fw = max(1.0, float(bbox[2] - bbox[0]))
        fh = max(1.0, float(bbox[3] - bbox[1]))
        face_ratio = (fw * fh) / float(w * h)
        if face_ratio < _MIN_FACE_RATIO:
            raise ValueError("活体检测失败：人脸过小，请靠近")
        if face_ratio > _MAX_FACE_RATIO:
            raise ValueError("活体检测失败：距离过近")
        pose = getattr(faces[0], "pose", None)
        if pose is not None and len(pose) >= 2:
            yaw, pitch = float(pose[0]), float(pose[1])
            if abs(yaw) > _MAX_YAW or abs(pitch) > _MAX_PITCH:
                raise ValueError("活体检测失败：请正对摄像头")
    except ImportError:
        logger.warning("InsightFace 不可用，活体仅做清晰度/亮度检查")
    except ValueError:
        raise
    except Exception as e:
        logger.warning("活体高级检查失败，降级基础检查: %s", e)

    return {
        "ok": True,
        "sharpness": sharpness,
        "brightness": brightness,
        "face_ratio": face_ratio,
        "liveness": "basic_pass",
        "anti_spoof_level": "basic",
        "anti_spoof_note": "basic anti-spoof（清晰度/亮度/姿态粗检，非真活体）",
    }


# 进程内全局跟踪器（多 worker 时每进程独立；确认仍靠 Redis cooldown 防刷）
global_tracker = MultiFrameTracker()

"""门口迎宾：短时窗口内选「更近、更清晰」的人脸帧，避免一进画面就用远景小人脸定稿。"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class LiveFaceHit:
    """单帧 live 检出结果（embedding 已 L2 归一）。"""

    embedding: np.ndarray
    det_score: float
    face_area_ratio: float
    sharpness: float


@dataclass
class _WindowEntry:
    t: float
    hit: LiveFaceHit
    frame: np.ndarray | None = None


# 定稿时附带的候补命中（同一窗口内），供会员一次确认，避免再等第二轮窗口
DecideResult = tuple[LiveFaceHit, np.ndarray | None, list[LiveFaceHit]]


@dataclass
class BestFaceWindow:
    """按摄像头聚合数秒内的检出，到期或足够近/质量平台期时交出最优帧。

    评分侧重脸框占比（走近更大），远景小人脸即使等满窗口也不轻易定稿。
    """

    window_sec: float = 1.8
    max_wait_sec: float = 3.2
    # 由远走近：远景小人脸宁可多等，也不要在 ratio≈0.0002 时定稿去撞陌生迎宾
    hard_abandon_sec: float = 9.0
    # 门口正对清晰脸（日志里 ratio≈0.01 det≈0.86）应立刻定稿，勿再等满窗
    early_ratio: float = 0.008
    early_sharpness: float = 80.0
    early_det_score: float = 0.55
    min_decide_ratio: float = 0.006
    plateau_sec: float = 0.45
    plateau_min_ratio: float = 0.007
    max_keep: int = 16
    # 推理慢（CPU 上常见 2～4s/帧）时条目会被 prune 掉，若只用「现存 oldest」计时，
    # max_wait 会永远滑不到，远景小人脸可拖十几秒。会话起点独立计时。
    _entries: list[_WindowEntry] = field(default_factory=list)
    _best_score: float = 0.0
    _best_score_at: float = 0.0
    # 勿用 0.0 作哨兵：墙上时钟可为 0，会被误判成「未开窗」而反复重置
    _session_started_at: float | None = None

    def clear(self) -> None:
        self._entries.clear()
        self._best_score = 0.0
        self._best_score_at = 0.0
        self._session_started_at = None

    def empty(self) -> bool:
        return not self._entries and self._session_started_at is None

    def collecting(self) -> bool:
        return bool(self._entries) or self._session_started_at is not None

    def prune_now(self, now: float) -> DecideResult | None:
        """漏检时推进过期清理；墙钟超时且仍有样本则强制定稿。"""
        self._prune(now)
        if self._session_started_at is None:
            return None
        waited = now - self._session_started_at
        if waited >= self.hard_abandon_sec:
            # 漏检过久：整窗作废，不定稿
            self.clear()
            return None
        if waited >= self.max_wait_sec and self._entries:
            ranked = sorted(self._entries, key=lambda e: self.score(e.hit), reverse=True)
            best = ranked[0]
            # 仍远/仍弱：继续等走近，勿定稿
            if best.hit.face_area_ratio < self.min_decide_ratio:
                return None
            if (
                best.hit.face_area_ratio < self.plateau_min_ratio
                and best.hit.det_score < self.early_det_score
            ):
                return None
            support = [e.hit for e in ranked[:4]]
            out = (best.hit, best.frame, support)
            self.clear()
            return out
        if waited > self.hard_abandon_sec and not self._entries:
            self.clear()
        return None

    @staticmethod
    def score(hit: LiveFaceHit) -> float:
        """越大越好。脸框占比权重最高，并非线性压制远景小人脸。"""
        ratio = max(0.0, float(hit.face_area_ratio))
        size_lin = min(1.35, ratio / 0.055)
        size = min(1.0, size_lin * size_lin)
        sharp = min(1.0, max(0.0, float(hit.sharpness)) / 85.0)
        det = min(1.0, max(0.0, float(hit.det_score)))
        return 0.62 * size + 0.25 * sharp + 0.13 * det

    def push(
        self,
        hit: LiveFaceHit,
        *,
        frame: np.ndarray | None,
        now: float,
    ) -> DecideResult | None:
        """塞入一帧；定稿时返回 (最佳, 帧, 同窗 Top 候补命中)。"""
        self._prune(now)
        if self._session_started_at is None:
            self._session_started_at = now
        sc = self.score(hit)
        frame_copy = None
        is_best_so_far = not self._entries or sc >= max(
            self.score(e.hit) for e in self._entries
        )
        if is_best_so_far and frame is not None:
            try:
                frame_copy = frame.copy()
            except Exception:
                frame_copy = frame
            for e in self._entries:
                e.frame = None

        self._entries.append(_WindowEntry(t=now, hit=hit, frame=frame_copy))
        if sc > self._best_score + 0.02 or self._best_score_at <= 0:
            self._best_score = sc
            self._best_score_at = now

        if len(self._entries) > self.max_keep:
            self._entries.sort(key=lambda e: self.score(e.hit), reverse=True)
            dropped = self._entries[self.max_keep :]
            self._entries = self._entries[: self.max_keep]
            for d in dropped:
                d.frame = None

        best = max(self._entries, key=lambda e: self.score(e.hit))
        # 会话墙钟：不受 prune / 慢推理间隔影响
        waited = now - self._session_started_at
        plateau = (
            self._best_score_at > 0
            and (now - self._best_score_at) >= self.plateau_sec
            and best.hit.face_area_ratio >= self.plateau_min_ratio
            and best.hit.sharpness >= self.early_sharpness * 0.85
        )
        early = (
            best.hit.face_area_ratio >= self.early_ratio
            and best.hit.sharpness >= self.early_sharpness
            and best.hit.det_score >= self.early_det_score
        )
        too_far = best.hit.face_area_ratio < self.min_decide_ratio
        # 远景：一直等到走近（early/plateau），或 hard_abandon 后静默丢弃——禁止 max_wait 强制定稿
        if too_far and not early and not plateau:
            if waited >= self.hard_abandon_sec:
                self.clear()
            return None
        if early or plateau or waited >= self.window_sec or waited >= self.max_wait_sec:
            if (
                not early
                and not plateau
                and waited < self.max_wait_sec
                and best.hit.face_area_ratio < self.plateau_min_ratio
            ):
                return None
            ranked = sorted(self._entries, key=lambda e: self.score(e.hit), reverse=True)
            support = [e.hit for e in ranked[:4]]
            out_hit = best.hit
            out_frame = best.frame
            self.clear()
            return out_hit, out_frame, support
        return None

    def _prune(self, now: float) -> None:
        # 保留稍长于 max_wait，供多样本确认；墙钟超时由 _session_started_at 负责定稿
        keep_horizon = max(self.max_wait_sec * 1.5, self.window_sec + 1.0)
        keep: list[_WindowEntry] = []
        for e in self._entries:
            if now - e.t <= keep_horizon:
                keep.append(e)
            else:
                e.frame = None
        self._entries = keep

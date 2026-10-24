"""进程内热缓存统一原语。

约定（与 Redis 分工）：
- 热缓存 = 本进程内存，跨 reload/worker 不共享；适合 TTS WAV/G.711、摄像头 codec、人脸向量等「同进程复用」。
- Redis = 跨进程加速与协调（菜单/推荐/冷却/限流/Leader）；不可用须降级，业务真源仍是 MySQL。
- 新热缓存须用 HotCache，禁止再散落手写 dict + FIFO pop。
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Generic, Hashable, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


@dataclass(frozen=True)
class HotCacheStats:
    name: str
    size: int
    max_size: int
    hits: int
    misses: int
    ttl_seconds: float | None


class HotCache(Generic[K, V]):
    """有界进程内缓存：可选 TTL，超出 max_size 时按 LRU 淘汰。线程安全。"""

    def __init__(
        self,
        name: str,
        *,
        max_size: int = 64,
        ttl_seconds: float | None = None,
    ) -> None:
        if max_size < 1:
            raise ValueError("max_size must be >= 1")
        self.name = name
        self.max_size = int(max_size)
        self.ttl_seconds = float(ttl_seconds) if ttl_seconds is not None else None
        self._data: OrderedDict[K, tuple[V, float]] = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        _REGISTRY[name] = self

    def get(self, key: K) -> V | None:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self._misses += 1
                return None
            value, ts = item
            if self.ttl_seconds is not None and (time.monotonic() - ts) > self.ttl_seconds:
                self._data.pop(key, None)
                self._misses += 1
                return None
            self._data.move_to_end(key)
            self._hits += 1
            return value

    def put(self, key: K, value: V) -> V:
        with self._lock:
            self._data[key] = (value, time.monotonic())
            self._data.move_to_end(key)
            while len(self._data) > self.max_size:
                self._data.popitem(last=False)
            return value

    def get_or_set(self, key: K, factory: Callable[[], V]) -> V:
        hit = self.get(key)
        if hit is not None:
            return hit
        value = factory()
        return self.put(key, value)

    def delete(self, key: K) -> None:
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, Hashable):
            return False
        return self.get(key) is not None  # type: ignore[arg-type]

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def stats(self) -> HotCacheStats:
        with self._lock:
            return HotCacheStats(
                name=self.name,
                size=len(self._data),
                max_size=self.max_size,
                hits=self._hits,
                misses=self._misses,
                ttl_seconds=self.ttl_seconds,
            )


_REGISTRY: dict[str, HotCache[Any, Any]] = {}


def list_hot_caches() -> list[HotCacheStats]:
    return [c.stats() for c in _REGISTRY.values()]


def clear_all_hot_caches() -> None:
    """测试用：清空本进程全部已登记热缓存。"""
    for c in list(_REGISTRY.values()):
        c.clear()

"""按来源分流的文件日志：滚动、压缩、月打包、保留期。

策略：
- 单文件上限 5MB，超限滚动为 app1.log / app2.log …
- 近 7 天保留明文；超过 7 天的滚动文件 gzip
- 满自然月（或超过约 30 天）打成 archive/{source}/{YYYY-MM}.tar.gz
- 归档与滚动文件最多保留 90 天（约 3 个月）
"""
from __future__ import annotations

import gzip
import logging
import re
import shutil
import tarfile
import threading
import time
from datetime import datetime
from logging.handlers import BaseRotatingHandler
from pathlib import Path
from typing import Iterable, Literal

from app.core.config import get_settings
from app.core.response import now_local

LogSource = Literal["backend", "frontend", "wechat", "alipay"]

SOURCES: tuple[LogSource, ...] = ("backend", "frontend", "wechat", "alipay")

LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_PLAIN_DAYS = 7
LOG_MONTHLY_PACK_DAYS = 30
LOG_MAX_KEEP_DAYS = 90
LOG_BACKUP_COUNT = 40

# 当前写入 app.log；滚动备份 app1.log … appN.log（亦兼容旧名 app.log.N）
_ROLLED_PLAIN = re.compile(r"^app\d+\.log$")
_LEGACY_PLAIN = re.compile(r"^app\.log\.\d+$")
_ROLLED_GZ = re.compile(r"^app\d+\.log\.gz$")
_LEGACY_GZ = re.compile(r"^app\.log\.\d+\.gz$")

_lock = threading.RLock()
_handlers: dict[str, logging.Handler] = {}
logger = logging.getLogger(__name__)


class NumberedRotatingFileHandler(BaseRotatingHandler):
    """app.log 满后滚动为 app1.log、app2.log …（与 README 一致）。"""

    def __init__(
        self,
        filename: str | Path,
        max_bytes: int = LOG_MAX_BYTES,
        backup_count: int = LOG_BACKUP_COUNT,
        encoding: str | None = "utf-8",
    ) -> None:
        self.max_bytes = max(0, int(max_bytes))
        self.backup_count = max(0, int(backup_count))
        super().__init__(str(filename), mode="a", encoding=encoding, delay=False)

    def namer(self, name: str) -> str:  # type: ignore[override]
        return name

    def rotator(self, source: str, dest: str) -> None:  # type: ignore[override]
        if Path(source).exists():
            Path(dest).unlink(missing_ok=True)
            Path(source).rename(dest)

    def shouldRollover(self, record: logging.LogRecord) -> bool:  # noqa: N802
        if self.max_bytes <= 0:
            return False
        if self.stream is None:
            self.stream = self._open()
        msg = f"{self.format(record)}\n"
        self.stream.seek(0, 2)
        return self.stream.tell() + len(msg.encode(self.encoding or "utf-8")) >= self.max_bytes

    def doRollover(self) -> None:  # noqa: N802
        if self.stream:
            self.stream.close()
            self.stream = None
        base = Path(self.baseFilename)
        directory = base.parent
        stem_prefix = "app"
        if self.backup_count > 0:
            last = directory / f"{stem_prefix}{self.backup_count}.log"
            last.unlink(missing_ok=True)
            for i in range(self.backup_count - 1, 0, -1):
                src = directory / f"{stem_prefix}{i}.log"
                dst = directory / f"{stem_prefix}{i + 1}.log"
                if src.exists():
                    dst.unlink(missing_ok=True)
                    src.rename(dst)
            first = directory / f"{stem_prefix}1.log"
            if base.exists():
                first.unlink(missing_ok=True)
                base.rename(first)
        else:
            base.unlink(missing_ok=True)
        if not self.delay:
            self.stream = self._open()


def reset_file_log_handlers_for_tests() -> None:
    """测试用：关闭并清空已注册的文件 handler。"""
    with _lock:
        for h in _handlers.values():
            try:
                h.close()
            except Exception:
                pass
            for lg in list(logging.Logger.manager.loggerDict.values()):
                if isinstance(lg, logging.Logger) and h in lg.handlers:
                    lg.removeHandler(h)
            root = logging.getLogger()
            if h in root.handlers:
                root.removeHandler(h)
        _handlers.clear()


def resolve_log_root() -> Path:
    settings = get_settings()
    raw = Path(getattr(settings, "log_dir", "logs") or "logs")
    if not raw.is_absolute():
        # backend/app/services -> 仓库根
        raw = Path(__file__).resolve().parents[3] / raw
    return raw


def ensure_log_dirs(root: Path | None = None) -> Path:
    root = root or resolve_log_root()
    root.mkdir(parents=True, exist_ok=True)
    for src in SOURCES:
        (root / src).mkdir(parents=True, exist_ok=True)
    (root / "archive").mkdir(parents=True, exist_ok=True)
    return root


def _plain_formatter() -> logging.Formatter:
    return logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")


def _make_handler(path: Path) -> NumberedRotatingFileHandler:
    handler = NumberedRotatingFileHandler(
        path,
        max_bytes=LOG_MAX_BYTES,
        backup_count=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(_plain_formatter())
    return handler


def get_source_logger(source: LogSource) -> logging.Logger:
    """获取写入 logs/{source}/app.log 的 logger（幂等）。"""
    ensure_log_dirs()
    name = f"snh.filelog.{source}"
    with _lock:
        lg = logging.getLogger(name)
        if name in _handlers:
            return lg
        path = resolve_log_root() / source / "app.log"
        handler = _make_handler(path)
        lg.setLevel(logging.DEBUG)
        lg.propagate = False
        lg.addHandler(handler)
        _handlers[name] = handler
        return lg


def attach_backend_file_handler(root_logger: logging.Logger | None = None) -> Path:
    """给根 logger 挂上 backend 文件 handler，并确保目录存在。"""
    root_path = ensure_log_dirs()
    lg = root_logger or logging.getLogger()
    with _lock:
        key = "snh.filelog.backend.root"
        if key in _handlers:
            return root_path
        path = root_path / "backend" / "app.log"
        handler = _make_handler(path)
        lg.addHandler(handler)
        _handlers[key] = handler
    return root_path


def _is_rollable_plain(name: str) -> bool:
    """滚动明文备份（不含当前 app.log）。"""
    return bool(_ROLLED_PLAIN.match(name) or _LEGACY_PLAIN.match(name))


def _is_rollable_gz(name: str) -> bool:
    return bool(_ROLLED_GZ.match(name) or _LEGACY_GZ.match(name))


def write_client_entries(
    source: LogSource,
    entries: Iterable[dict],
    *,
    device: str | None = None,
    app_version: str | None = None,
) -> int:
    """写入客户端批量日志，返回成功条数。"""
    if source == "backend":
        raise ValueError("backend 日志由服务端直接写入，不可经客户端上报")
    lg = get_source_logger(source)
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARN": logging.WARNING,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "FATAL": logging.CRITICAL,
        "CRITICAL": logging.CRITICAL,
    }
    n = 0
    meta_bits = []
    if device:
        meta_bits.append(f"device={device}")
    if app_version:
        meta_bits.append(f"ver={app_version}")
    meta = (" " + " ".join(meta_bits)) if meta_bits else ""
    for item in entries:
        msg = str(item.get("message") or "").strip()
        if not msg:
            continue
        if len(msg) > 4000:
            msg = msg[:4000] + "…"
        level = level_map.get(str(item.get("level") or "INFO").upper(), logging.INFO)
        page = item.get("page")
        ts = item.get("ts")
        extra = item.get("extra")
        parts = [msg]
        if page:
            parts.append(f"page={page}")
        if ts:
            parts.append(f"client_ts={ts}")
        if extra is not None:
            parts.append(f"extra={extra}")
        lg.log(level, "%s%s", " | ".join(parts), meta)
        n += 1
    return n


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return time.time()


def _gzip_file(path: Path) -> Path | None:
    if not path.is_file() or path.name.endswith(".gz"):
        return None
    dest = path.with_name(path.name + ".gz")
    if dest.exists():
        path.unlink(missing_ok=True)
        return dest
    with path.open("rb") as src, gzip.open(dest, "wb") as out:
        shutil.copyfileobj(src, out)
    path.unlink(missing_ok=True)
    return dest


def _month_key(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m")


def _pack_month(archive: Path, files: list[Path]) -> None:
    """将 files 打进月度 tar.gz；若归档已存在则合并成员后替换。"""
    archive.parent.mkdir(parents=True, exist_ok=True)
    tmp = archive.with_name(archive.name + ".packing")
    tmp.unlink(missing_ok=True)
    seen: set[str] = set()
    with tarfile.open(tmp, "w:gz") as neu:
        if archive.exists():
            try:
                with tarfile.open(archive, "r:gz") as old:
                    for m in old.getmembers():
                        if m.name in seen:
                            continue
                        fobj = old.extractfile(m)
                        if fobj is None:
                            continue
                        neu.addfile(m, fobj)
                        seen.add(m.name)
            except tarfile.TarError:
                logger.warning("读取旧归档失败，将重建: %s", archive)
        for path in files:
            if path.name in seen:
                continue
            neu.add(path, arcname=path.name)
            seen.add(path.name)
    tmp.replace(archive)
    for path in files:
        path.unlink(missing_ok=True)


def maintain_logs(*, now: datetime | None = None) -> dict:
    """执行压缩 / 月打包 / 过期删除。返回统计。"""
    root = ensure_log_dirs()
    now = now or now_local()
    now_ts = now.timestamp()
    plain_cutoff = now_ts - LOG_PLAIN_DAYS * 86400
    pack_cutoff = now_ts - LOG_MONTHLY_PACK_DAYS * 86400
    keep_cutoff = now_ts - LOG_MAX_KEEP_DAYS * 86400
    current_month = now.strftime("%Y-%m")

    stats = {"gzipped": 0, "packed": 0, "deleted": 0, "archives_deleted": 0}

    with _lock:
        for source in SOURCES:
            src_dir = root / source
            if not src_dir.is_dir():
                continue

            for path in sorted(src_dir.iterdir()):
                if not path.is_file() or path.name == "app.log":
                    continue
                if not _is_rollable_plain(path.name):
                    continue
                if _mtime(path) < plain_cutoff:
                    if _gzip_file(path):
                        stats["gzipped"] += 1

            by_month: dict[str, list[Path]] = {}
            for path in sorted(src_dir.iterdir()):
                if not path.is_file() or not _is_rollable_gz(path.name):
                    continue
                mt = _mtime(path)
                mk = _month_key(mt)
                if mk == current_month and mt >= pack_cutoff:
                    continue
                by_month.setdefault(mk, []).append(path)

            archive_root = root / "archive" / source
            for month, files in by_month.items():
                if not files:
                    continue
                try:
                    _pack_month(archive_root / f"{month}.tar.gz", files)
                    stats["packed"] += len(files)
                except Exception:
                    logger.exception("月打包失败 source=%s month=%s", source, month)

            for path in list(src_dir.iterdir()):
                if not path.is_file() or path.name == "app.log":
                    continue
                if _mtime(path) < keep_cutoff:
                    path.unlink(missing_ok=True)
                    stats["deleted"] += 1

        archive_root = root / "archive"
        if archive_root.is_dir():
            for path in archive_root.rglob("*.tar.gz"):
                if path.is_file() and _mtime(path) < keep_cutoff:
                    path.unlink(missing_ok=True)
                    stats["archives_deleted"] += 1

    return stats

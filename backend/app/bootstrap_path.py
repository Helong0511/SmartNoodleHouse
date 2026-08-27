"""将仓库根目录加入 sys.path，使 `import camera` 在 backend/ 下启动时可用。"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]  # backend/app -> 仓库根目录
_ROOT_STR = str(_ROOT)
if _ROOT_STR not in sys.path:
    sys.path.insert(0, _ROOT_STR)

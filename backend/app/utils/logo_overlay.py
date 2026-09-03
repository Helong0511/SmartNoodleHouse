"""在图片中心叠加品牌 Logo（用于微信小程序码等）。"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw


def _project_root() -> Path:
    # backend/app/utils/logo_overlay.py -> repo root
    return Path(__file__).resolve().parents[3]


def resolve_brand_logo_path() -> Path | None:
    root = _project_root()
    for p in (
        root / "uploads" / "logo" / "logo.png",
        root / "frontend" / "public" / "logo.png",
    ):
        if p.is_file():
            return p
    return None


def _resize_cover(img: Image.Image, size: int) -> Image.Image:
    """等比放大后居中裁切为正方形（填充目标区域）。"""
    src = img.convert("RGBA")
    w, h = src.size
    scale = max(size / w, size / h)
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    resized = src.resize((nw, nh), Image.Resampling.LANCZOS)
    left = (nw - size) // 2
    top = (nh - size) // 2
    return resized.crop((left, top, left + size, top + size))


def _fit_logo_geometry(
    width: int,
    height: int,
    *,
    ratio: float = 0.30,
    pad_ratio: float = 0.10,
    min_diameter: int = 24,
) -> tuple[int, int, int] | None:
    """计算可贴入图内的 (diameter, pad, outer)；装不下 min_diameter 则返回 None。"""
    side = min(int(width), int(height))
    if side < 8:
        return None

    min_d = max(1, int(min_diameter))
    if side < min_d:
        return None

    ratio = max(0.05, min(float(ratio), 0.6))
    pad_ratio = max(0.0, min(float(pad_ratio), 0.4))

    # 外接圆盘必须整图装得下，避免 (w-outer)//2 为负导致偏裁
    max_outer = side
    diameter = max(min_d, int(side * ratio))
    pad = max(0, int(diameter * pad_ratio)) if pad_ratio > 0 else 0
    # 至少留一点白边可读；过小图允许 pad=0
    if pad_ratio > 0 and side >= 32:
        pad = max(2, pad)
    outer = diameter + pad * 2

    if outer > max_outer:
        outer = max_outer
        # 在 outer 约束下反推 diameter / pad，但不得小于 min_diameter
        if pad_ratio <= 0:
            diameter = outer
            pad = 0
        else:
            diameter = max(1, int(outer / (1 + 2 * pad_ratio)))
            pad = max(0, (outer - diameter) // 2)
            diameter = outer - pad * 2
            if diameter < min_d:
                # 优先保证最小直径：挤掉白边；仍不够则放弃叠加
                if outer < min_d:
                    return None
                diameter = min_d
                pad = (outer - diameter) // 2
                # 奇数 outer 时保证 diameter+2*pad == outer 且 diameter>=min_d
                diameter = outer - pad * 2
                if diameter < min_d:
                    return None
            if diameter < 1:
                return None

    if diameter < min_d or outer > side or diameter + pad * 2 != outer:
        return None
    return diameter, pad, outer


def overlay_center_logo(
    png_bytes: bytes,
    *,
    logo_path: Path | None = None,
    ratio: float = 0.30,
    pad_ratio: float = 0.10,
) -> bytes:
    """在方形/圆形码中心盖上 Logo（cover 填充圆区，白底圆边）。"""
    path = logo_path or resolve_brand_logo_path()
    if not path or not path.is_file():
        return png_bytes

    base = Image.open(BytesIO(png_bytes)).convert("RGBA")
    logo = Image.open(path).convert("RGBA")
    w, h = base.size
    geom = _fit_logo_geometry(w, h, ratio=ratio, pad_ratio=pad_ratio)
    if not geom:
        return png_bytes
    diameter, pad, outer = geom

    logo_fill = _resize_cover(logo, diameter)

    plate = Image.new("RGBA", (outer, outer), (0, 0, 0, 0))
    ImageDraw.Draw(plate).ellipse((0, 0, outer - 1, outer - 1), fill=(255, 255, 255, 255))

    # 圆形裁切后的 Logo 贴到白盘中心
    circle = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    mask = Image.new("L", (diameter, diameter), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diameter - 1, diameter - 1), fill=255)
    circle.paste(logo_fill, (0, 0), mask)
    plate.alpha_composite(circle, (pad, pad))

    ox = max(0, (w - outer) // 2)
    oy = max(0, (h - outer) // 2)
    # 再保险：若仍溢出（极端奇数尺寸），右下裁到图内
    if ox + outer > w:
        ox = max(0, w - outer)
    if oy + outer > h:
        oy = max(0, h - outer)

    composed = base.copy()
    composed.alpha_composite(plate, (ox, oy))

    buf = BytesIO()
    composed.save(buf, format="PNG", optimize=True)
    return buf.getvalue()

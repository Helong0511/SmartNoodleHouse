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
    side = min(w, h)
    diameter = max(24, int(side * float(ratio)))
    pad = max(4, int(diameter * float(pad_ratio)))
    outer = diameter + pad * 2

    logo_fill = _resize_cover(logo, diameter)

    plate = Image.new("RGBA", (outer, outer), (0, 0, 0, 0))
    ImageDraw.Draw(plate).ellipse((0, 0, outer - 1, outer - 1), fill=(255, 255, 255, 255))

    # 圆形裁切后的 Logo 贴到白盘中心
    circle = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    mask = Image.new("L", (diameter, diameter), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diameter - 1, diameter - 1), fill=255)
    circle.paste(logo_fill, (0, 0), mask)
    plate.alpha_composite(circle, (pad, pad))

    composed = base.copy()
    composed.alpha_composite(plate, ((w - outer) // 2, (h - outer) // 2))

    buf = BytesIO()
    composed.save(buf, format="PNG", optimize=True)
    return buf.getvalue()

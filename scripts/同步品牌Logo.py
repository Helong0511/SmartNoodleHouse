"""从 uploads/logo/logo.png 生成各端用的正方形品牌图（缩放+居中裁切填充）。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "uploads" / "logo" / "logo.png"


def resize_cover(img: Image.Image, size: int) -> Image.Image:
    """等比放大后居中裁切为正方形 size×size（object-fit: cover）。"""
    src = img.convert("RGBA")
    w, h = src.size
    scale = max(size / w, size / h)
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    resized = src.resize((nw, nh), Image.Resampling.LANCZOS)
    left = (nw - size) // 2
    top = (nh - size) // 2
    return resized.crop((left, top, left + size, top + size))


def main() -> None:
    if not SRC.is_file():
        raise SystemExit(f"缺少源 Logo: {SRC}")
    im = Image.open(SRC)
    print("source", im.size, SRC.stat().st_size)

    targets = [
        (ROOT / "frontend" / "public" / "logo.png", 800),
        (ROOT / "frontend" / "public" / "favicon-32.png", 32),
        (ROOT / "frontend" / "public" / "apple-touch-icon.png", 180),
        (ROOT / "miniprogram-wechat" / "assets" / "logo.png", 256),
        (ROOT / "miniprogram-alipay" / "assets" / "logo.png", 256),
    ]
    for path, size in targets:
        out = resize_cover(im, size)
        path.parent.mkdir(parents=True, exist_ok=True)
        out.save(path, format="PNG", optimize=True)
        print("wrote", path.relative_to(ROOT), out.size, path.stat().st_size)

    # 浏览器标签栏：多尺寸 ICO（16/32/48）
    ico_path = ROOT / "frontend" / "public" / "favicon.ico"
    ico_sizes = [(16, 16), (32, 32), (48, 48)]
    ico_base = resize_cover(im, 48).convert("RGBA")
    ico_base.save(ico_path, format="ICO", sizes=ico_sizes)
    print("wrote", ico_path.relative_to(ROOT), ico_sizes, ico_path.stat().st_size)


if __name__ == "__main__":
    main()

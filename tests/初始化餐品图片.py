"""生成开发用餐品预览图（1200x1200）并写入 products.main_image。"""
from __future__ import annotations

import pathlib
import re
from urllib.parse import unquote, urlparse

from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parents[1]
UPLOAD = ROOT / "backend" / "uploads" / "products"
UPLOAD.mkdir(parents=True, exist_ok=True)

# 若 uvicorn 从 backend 目录启动，upload_dir 相对为 backend/uploads
ALT_UPLOAD = ROOT / "uploads" / "products"
ALT_UPLOAD.mkdir(parents=True, exist_ok=True)

COLORS = [
    ((255, 107, 53), (255, 232, 223), "牛"),
    ((22, 119, 255), (230, 244, 255), "排"),
    ((46, 160, 67), (232, 245, 233), "卤"),
]


def make_image(out: pathlib.Path, bg, fg, label: str, title: str) -> None:
    size = 1200
    img = Image.new("RGB", (size, size), bg)
    draw = ImageDraw.Draw(img)
    # 中心圆盘
    margin = 180
    draw.ellipse([margin, margin, size - margin, size - margin], fill=fg)
    try:
        font_big = ImageFont.truetype("msyh.ttc", 220)
        font_sm = ImageFont.truetype("msyh.ttc", 56)
    except Exception:
        font_big = ImageFont.load_default()
        font_sm = font_big
    bbox = draw.textbbox((0, 0), label, font=font_big)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - tw) / 2, (size - th) / 2 - 40), label, fill=fg, font=font_big)
    bbox2 = draw.textbbox((0, 0), title, font=font_sm)
    tw2 = bbox2[2] - bbox2[0]
    draw.text(((size - tw2) / 2, size - 160), title, fill=fg, font=font_sm)
    img.save(out, "JPEG", quality=88)


def main() -> None:
    env = (ROOT / ".env").read_text(encoding="utf-8")
    raw = re.search(r"^DATABASE_URL=(.+)$", env, re.M).group(1).strip()
    u = urlparse(raw.replace("mysql+aiomysql://", "mysql://"))
    import pymysql

    conn = pymysql.connect(
        host=u.hostname,
        user=u.username,
        password=unquote(u.password or ""),
        database=u.path.lstrip("/"),
        port=u.port or 3306,
        charset="utf8mb4",
        autocommit=True,
    )
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM products ORDER BY id")
    rows = cur.fetchall()
    for i, (pid, name) in enumerate(rows):
        fg, bg, ch = COLORS[i % len(COLORS)]
        fname = f"demo_product_{pid}.jpg"
        for folder in (UPLOAD, ALT_UPLOAD):
            make_image(folder / fname, bg, fg, ch, str(name)[:12])
        url = f"/uploads/products/{fname}"
        cur.execute("UPDATE products SET main_image=%s WHERE id=%s", (url, pid))
        # 开发餐盒费示例：前两个餐品 1 元，第三个 0.5
        box = "1.00" if i < 2 else "0.50"
        cur.execute("UPDATE products SET box_price=%s WHERE id=%s AND (box_price IS NULL OR box_price=0)", (box, pid))
        print(f"product {pid} -> {url} box={box}")
    cur.close()
    conn.close()
    print("done")


if __name__ == "__main__":
    main()

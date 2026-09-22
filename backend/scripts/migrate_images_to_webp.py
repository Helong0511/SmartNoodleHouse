"""已存在图片批量迁移为 WebP 格式。

扫描 upload_dir 下的 .jpg/.jpeg/.png 文件，用 Pillow 转换为 .webp，
并更新数据库中所有引用旧 URL 的记录。

用法：
    cd backend
    python scripts/migrate_images_to_webp.py --dry-run    # 预览不执行
    python scripts/migrate_images_to_webp.py              # 执行迁移
    python scripts/migrate_images_to_webp.py --delete     # 迁移后删除原图
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

# 确保 backend 目录在 sys.path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main():
    parser = argparse.ArgumentParser(description="批量迁移图片为 WebP")
    parser.add_argument("--dry-run", action="store_true", help="只预览不执行")
    parser.add_argument("--delete", action="store_true", help="迁移后删除原文件")
    args = parser.parse_args()

    from PIL import Image
    from sqlalchemy import text

    from app.core.config import get_settings
    from app.core.database import engine as async_engine

    settings = get_settings()
    upload_dir = Path(settings.upload_dir).resolve()

    if not upload_dir.exists():
        print(f"上传目录不存在: {upload_dir}")
        return

    # 收集需要转换的文件
    targets: list[Path] = []
    for ext in ("*.jpg", "*.jpeg", "*.png"):
        targets.extend(upload_dir.rglob(ext))

    # 排除已经是 webp 的
    targets = [t for t in targets if t.suffix.lower() != ".webp"]
    print(f"发现 {len(targets)} 个待转换图片（{upload_dir}）")
    if not targets:
        print("无需迁移")
        return

    if args.dry_run:
        for t in targets[:50]:
            print(f"  [DRY-RUN] {t.relative_to(upload_dir)} ({t.stat().st_size} bytes)")
        if len(targets) > 50:
            print(f"  ... 还有 {len(targets) - 50} 个")
        print("使用不带 --dry-run 运行以实际执行迁移")
        return

    # 需要更新图片 URL 的表和列
    url_columns = [
        ("products", "main_image"),
        ("product_images", "image_url"),
        ("customers", "avatar_url"),
        ("staff", "avatar_url"),
        ("stores", "logo_url"),
        ("stores", "avatar_url"),
        ("product_categories", "icon_url"),
        ("product_categories", "cover_url"),
    ]

    converted = 0
    failed = 0
    db_updated = 0

    async with async_engine.begin() as conn:
        for src in targets:
            try:
                # 转换图片
                img = Image.open(src)
                if img.mode not in ("RGB", "RGBA", "L", "LA"):
                    img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
                buf = BytesIO()
                img.save(buf, format="WEBP", quality=85, method=4)
                webp_data = buf.getvalue()

                # 保存 WebP 文件
                dst = src.with_suffix(".webp")
                dst.write_bytes(webp_data)

                # 计算旧/新 URL
                rel_src = src.relative_to(upload_dir).as_posix()
                rel_dst = dst.relative_to(upload_dir).as_posix()
                old_url = f"/uploads/{rel_src}"
                new_url = f"/uploads/{rel_dst}"

                # 更新数据库引用
                for table, column in url_columns:
                    try:
                        result = await conn.execute(
                            text(f"UPDATE `{table}` SET `{column}` = :new WHERE `{column}` = :old"),
                            {"new": new_url, "old": old_url},
                        )
                        if result.rowcount > 0:
                            db_updated += result.rowcount
                            print(f"  DB: {table}.{column} {old_url} → {new_url} ({result.rowcount} rows)")
                    except Exception:
                        # 表或列可能不存在，跳过
                        pass

                orig_size = src.stat().st_size
                ratio = len(webp_data) * 100.0 / max(1, orig_size)
                print(f"  OK: {rel_src} → {rel_dst} ({orig_size} → {len(webp_data)} bytes, {ratio:.0f}%)")
                converted += 1

                # 删除原文件
                if args.delete:
                    src.unlink()
                    print(f"  DEL: {rel_src}")

            except Exception as e:
                print(f"  FAIL: {src.name} - {e}")
                failed += 1

    print(f"\n迁移完成: 转换 {converted} 个, 失败 {failed} 个, DB 更新 {db_updated} 条记录")
    if not args.delete and converted > 0:
        print("原文件保留中。确认无误后可加 --delete 重新运行清理原文件。")


if __name__ == "__main__":
    asyncio.run(main())

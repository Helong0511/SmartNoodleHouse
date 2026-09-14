#!/usr/bin/env python3
"""生成 FACE_ENCRYPTION_KEY / SECRET_ENCRYPTION_KEY / JWT_SECRET 高熵随机串。

用法:
  python scripts/gen_secrets.py
  python scripts/gen_secrets.py --nbytes 48
"""
from __future__ import annotations

import argparse
import secrets


def main() -> int:
    parser = argparse.ArgumentParser(description="生成生产用高熵密钥")
    parser.add_argument(
        "--nbytes",
        "--bytes",  # 兼容旧参数名
        dest="nbytes",
        type=int,
        default=32,
        help="随机字节数（token_urlsafe 编码后更长；至少 24）",
    )
    args = parser.parse_args()
    n = max(24, int(args.nbytes))
    print("# 写入项目根 .env（勿提交 Git）")
    print(f"JWT_SECRET={secrets.token_urlsafe(n)}")
    print(f"FACE_ENCRYPTION_KEY={secrets.token_urlsafe(n)}")
    print(f"SECRET_ENCRYPTION_KEY={secrets.token_urlsafe(n)}")
    print(f"DISPLAY_SCREEN_TOKEN={secrets.token_urlsafe(24)}")
    print("# 以下为固定配置（按需保留）")
    print("JWT_AUDIENCE=smart-noodle-house")
    print("CUSTOMER_JWT_EXPIRE_MINUTES=1440")
    print("PROCESS_ROLE=auto")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

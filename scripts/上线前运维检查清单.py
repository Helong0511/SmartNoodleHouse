"""打印审计阻塞项（BLK）人工动作清单，不访问外网。"""
from __future__ import annotations

CHECKS = [
    ("BLK-001", "微信/支付宝/美团/饿了么真实沙箱联调", "用官方凭证跑通支付下单/回调/退款/外卖推单"),
    ("BLK-002", "密钥轮换", "轮换历史曾泄露的 AppSecret/私钥/AES/库口令，并更新生产 .env"),
    ("BLK-003", "生产 schema 对比", "alembic upgrade head --sql 与预生产演练在线 DDL 窗口"),
    ("BLK-004", "监控告警", "Prometheus/Grafana 接入 /api 性能与 5xx、队列积压、支付失败率"),
    ("BLK-005", "小程序上架", "按 docs/小程序上架检查清单.md 提交审核"),
    ("P1-R2", "备份演练", "python scripts/备份恢复演练.py；隔离库加 --restore-drill"),
]


def main() -> int:
    print("=== 上线前运维检查（代码无法代劳）===")
    for code, title, action in CHECKS:
        print(f"[{code}] {title}")
        print(f"  → {action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

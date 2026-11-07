"""打印审计阻塞项（BLK）人工动作清单，不访问外网。

默认以退出码 2 标记为 BLOCKED，避免把“列出了人工事项”误认为“已经验收通过”。
操作员逐项完成并核验所有事项后，可显式传入 --acknowledge 确认；该参数不代替外部证据。
"""
from __future__ import annotations

import argparse

CHECKS = [
    ("BLK-001", "微信/支付宝/美团/饿了么真实沙箱联调", "用官方凭证跑通支付下单/回调/退款/外卖推单"),
    ("BLK-002", "密钥轮换", "轮换历史曾泄露的 AppSecret/私钥/AES/库口令，并更新生产 .env"),
    ("BLK-003", "生产 schema 对比", "alembic upgrade head --sql 与预生产演练在线 DDL 窗口"),
    ("BLK-004", "监控告警", "Prometheus/Grafana 接入 /api 性能与 5xx、队列积压、支付失败率"),
    ("BLK-006", "Redis 生产容量与持久化", "核验 maxmemory 已设置、maxmemory-policy=noeviction、vm.overcommit_memory=1、AOF/RDB 状态与磁盘告警；按实际主机 RAM 调整示例 512mb"),
    ("BLK-007", "人脸识别生产合规门禁", "如启用人脸处理，须先完成个人信息保护影响评估、显著告知、单独同意与撤回、非人脸替代方式、最短保存期限及存储/传输合规审查；否则保持 enable_face_welcome=false 且 FACE_RECOGNITION_LEGAL_REVIEWED=false"),
    ("BLK-005", "小程序上架", "按 docs/小程序上架检查清单.md 提交审核"),
    ("P1-R2", "备份演练", "python scripts/备份恢复演练.py；隔离库加 --restore-drill"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="列出 SmartNoodleHouse 生产上线前人工阻塞项")
    parser.add_argument(
        "--acknowledge",
        action="store_true",
        help="操作员确认已逐项完成并核验证据；本参数本身不会执行或验证这些事项",
    )
    args = parser.parse_args()

    print("=== 上线前运维检查（本脚本不自动访问生产系统）===")
    for code, title, action in CHECKS:
        print(f"[MANUAL] {code} {title}")
        print(f"  → {action}")

    if not args.acknowledge:
        print("\n[BLOCKED] 人工阻塞项尚未由操作员确认；完成核验后显式传入 --acknowledge。")
        return 2

    print("\n[ACKNOWLEDGED] 操作员声明以上人工事项已逐项完成并核验。")
    print("注意：此结果是人工确认，不是系统自动验证或第三方平台验收证明。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

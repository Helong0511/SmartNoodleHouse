"""打印审计阻塞项（BLK）人工动作清单，不访问外网。

默认以退出码 2 标记为 BLOCKED，避免把“列出了人工事项”误认为“已经验收通过”。
操作员逐项完成并核验所有事项后，可显式传入 --acknowledge 确认；该参数不代替外部证据。
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

CHECKS = [
    ("BLK-001", "微信/支付宝/美团/饿了么真实沙箱联调", "用官方凭证跑通支付下单/回调/退款/外卖推单"),
    ("BLK-002", "密钥轮换", "轮换历史曾泄露的 AppSecret/私钥/AES/库口令，并更新生产 .env"),
    ("P0-LOG", "历史日志凭据扫描", "清理/封存历史日志；脚本扫描只报告文件与行号，不输出凭据值"),
    ("BLK-003", "生产 schema 对比", "alembic upgrade head --sql 与预生产演练在线 DDL 窗口"),
    ("BLK-004", "监控告警", "Prometheus/Grafana 接入 /api 性能与 5xx、队列积压、支付失败率"),
    ("BLK-006", "Redis 生产容量与持久化", "核验 maxmemory 已设置、maxmemory-policy=noeviction、vm.overcommit_memory=1、AOF/RDB 状态与磁盘告警；按实际主机 RAM 调整示例 512mb"),
    ("BLK-007", "人脸识别个人信息合规门禁", "如启用人脸处理，须先完成个人信息保护影响评估、显著告知、单独同意与撤回、非人脸替代方式、最短保存期限及存储/传输合规审查；否则保持 ENABLE_FACE_WELCOME=false 且 FACE_RECOGNITION_LEGAL_REVIEWED=false"),
    ("BLK-008", "InsightFace 模型商业授权与推理验收", "取得 buffalo_l 等实际部署模型/权重的书面商业许可并归档模型版本与 SHA256；完成当前 FaceAnalysis API 兼容性、真实目标硬件/摄像头推理、性能与失败回退验收；未完成前保持 FACE_MODEL_COMMERCIAL_LICENSE_CONFIRMED=false 且人脸迎宾关闭"),
    ("BLK-005", "小程序上架", "按 docs/小程序上架检查清单.md 提交审核"),
    ("P1-R2", "备份演练", "python scripts/备份恢复演练.py；隔离库加 --restore-drill"),
]


_SENSITIVE_LOG_RE = re.compile(
    r"(?i)(?:[?&]|\b)(?:secret|access_token|refresh_token|api[_-]?key|private[_-]?key|password)=([^&\s\"']+)"
)


def scan_sensitive_logs() -> int:
    """扫描历史日志中的敏感查询参数；只打印位置，绝不回显凭据值。"""
    log_root = Path(__file__).resolve().parents[1] / "logs" / "backend"
    if not log_root.exists():
        print(
            "[SKIP] 未发现仓库内 logs/backend 目录；"
            "这不代表生产主机、外部日志平台或历史归档已完成扫描。"
        )
        return 0

    findings = 0
    for path in sorted(log_root.rglob("*.log")):
        try:
            with path.open("r", encoding="utf-8", errors="replace") as stream:
                for line_no, line in enumerate(stream, 1):
                    for match in _SENSITIVE_LOG_RE.finditer(line):
                        value = match.group(1).strip().strip(",;")
                        if value.upper() in {"[REDACTED]", "REDACTED"} or value.upper().startswith(("REPLACE_WITH", "YOUR_")):
                            continue
                        findings += 1
                        print(
                            f"[FAIL] {path.relative_to(log_root)}:{line_no}: "
                            "检测到敏感凭据参数（值已隐藏）"
                        )
                        break
        except OSError:
            findings += 1
            print(f"[FAIL] {path}: 无法读取日志文件，需人工检查")
    if findings:
        print(f"[BLOCKED] 历史日志发现 {findings} 处敏感凭据或不可读文件。")
        return 1
    print("[PASS] 未发现未脱敏的敏感查询参数。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="列出 SmartNoodleHouse 生产上线前人工阻塞项")
    parser.add_argument(
        "--acknowledge",
        action="store_true",
        help="操作员确认已逐项完成并核验证据；本参数本身不会执行或验证这些事项",
    )
    args = parser.parse_args()

    print("=== 上线前运维检查（本脚本不自动访问生产系统）===")
    log_scan_failed = scan_sensitive_logs()
    for code, title, action in CHECKS:
        print(f"[MANUAL] {code} {title}")
        print(f"  → {action}")

    if not args.acknowledge:
        print("\n[BLOCKED] 人工阻塞项尚未由操作员确认；完成核验后显式传入 --acknowledge。")
        return 2
    if log_scan_failed:
        print("\n[BLOCKED] 日志敏感信息扫描未通过；清理历史日志并轮换泄露凭据后再确认。")
        return 2

    print("\n[ACKNOWLEDGED] 操作员声明以上人工事项已逐项完成并核验。")
    print("注意：此结果是人工确认，不是系统自动验证或第三方平台验收证明。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

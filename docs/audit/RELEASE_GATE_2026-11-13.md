# 生产发布门禁复核（2026-11-13）

> 本文记录仓库证据与外部生产验收边界。它不是生产部署证明，也不替代各平台控制台、生产服务器或数据库的真实验收。不得将未执行事项标记为完成。

## 1. 仓库基线

- 仓库：Helong0511/SmartNoodleHouse
- 默认分支：main
- 复核时 main HEAD：0cd545f56ba7f681f3a31c989b8af08c709cb309（PR #185，备份完整性与保留策略回归测试）
- PR #185 主分支 CI：[Actions run 43371711020](https://github.com/Helong0511/SmartNoodleHouse/actions/runs/43371711020)
- 该 SHA 对应 12 个 GitHub Actions check runs，全部 completed/success。
- 当前没有待合并 PR（复核时）。

## 2. main 分支保护

Ruleset main-protection（ID 27176504）经 GitHub API 复核：

- enforcement：active；目标：~DEFAULT_BRANCH（当前默认分支 main）。
- 要求 PR；必需审批数为 **0**，适用于单人维护。
- 禁止分支删除和 non-fast-forward 更新。
- 启用 strict required status checks。
- 必需检查共 12 项：backend-tests、backend-mysql、frontend-build (24)、frontend-build (26)、frontend-lockfile-consistency、dependency-lock-consistency、dependency-security-audit、miniprogram-static-check、rust-ad-tracking (1.89.0)、rust-ad-tracking (stable)、windows-rust-dll、windows-sapi-dependency。
- bypass_actors=[]，当前用户不能绕过规则。
- 当前仍允许 merge、squash、rebase；require_extra_approval_for_unattributed_changes=true。单人维护者可在 GitHub Rulesets UI 评估是否调整这两个选项。
- 尚未完成受控的负向验证：需要在隔离测试 PR 上证明失败/缺失的必需检查确实阻止合并。不得为了测试而故意破坏 main 或生产代码。

规则页面：[main-protection](https://github.com/Helong0511/SmartNoodleHouse/rules/27176504)。

## 3. 生产发布阻塞项

以下项目不能由仓库 CI 或静态代码检查代替：

| 门禁 | 当前证据/状态 | 上线前必须完成 |
|---|---|---|
| 历史泄露密钥 | 仓库审计记录 BLK-002；未取得平台轮换凭证 | 在微信/支付宝/外卖平台及相关服务控制台轮换，更新受控生产密钥，并验证旧凭据失效 |
| 生产配置 | 未取得生产环境配置或部署环境 | 在目标主机执行生产启动配置校验；确认 Mock/debug 关闭、强密钥、CORS、代理信任、支付证书与回调配置 |
| MySQL/Redis | CI 有真实 MySQL 集成测试；无生产实例证据 | 最小权限账号、网络隔离、Redis ACL/noeviction、连接池与容量预算实测 |
| 数据库迁移 | CI 有迁移测试；无生产 schema 对比 | 对生产 schema/Alembic 版本进行只读盘点，离线生成 SQL，预生产演练后安排低峰迁移 |
| 备份与恢复 | 备份脚本和 gzip 完整性/保留策略测试已合并；无真实隔离库恢复证据 | 定时全量备份、binlog/PITR、异地加密副本、权限审计、隔离实例恢复演练；记录 RPO/RTO |
| TLS/反向代理 | Nginx/systemd 配置及文档已维护；无生产域名证书验收 | 实际执行 nginx -t、TLS/证书链检查、HSTS、代理来源与防火墙验证 |
| 监控告警 | 指标端点存在；无生产 Prometheus/Grafana/通知证据 | 接入抓取、仪表盘、错误率/延迟/资源/队列/备份失败告警，并做通知演练 |
| 对象存储 | CI 有 MinIO 集成测试；无生产桶策略证据 | 私有桶、最小 IAM、加密、生命周期、跨区/异地备份与恢复验证 |
| 支付及外卖 | 有回调与并发回归测试；无真实商户沙箱验收记录 | 微信/支付宝/美团/饿了么逐项完成成功、失败、超时、退款、重复/乱序回调、验签失败及对账 |
| 小程序 | 静态检查通过；无平台审核/正式发布凭证 | 隐私政策、类目资质、支付权限、合法域名、真机回归、平台审核与正式发布 |
| 容量与回滚 | 无生产压测/回滚报告 | 压测 API、DB/Redis、Outbox、WebSocket/KDS；验证应用回滚与数据库向前修复流程 |

## 4. 人脸识别功能门禁

InsightFace / 人脸身份识别继续保持全局 hard-stop。不得因 CI 通过而启用。Issue #109 仍开放，必须先取得模型商业授权证据、完成个人信息保护影响评估与必要法务审查、落实单独同意/撤回/删除和保留期限，并通过真实 CPU 部署端到端测试。未满足前不得用于生产迎宾、身份匹配或人脸档案读取。

## 5. 单人维护与变更规则

- 不要求不存在的协作者或第二人审批；PR + 12 项 CI + 0 approvals。
- 不绕过 main Ruleset，不向 main 直接推送代码。
- AOCI 与 AGENTS.md 不属于当前项目要求，不得重新引入。
- 任何业务逻辑变更必须有可复现缺陷或明确需求证据，并添加回归测试。
- 不得把“CI 全绿”“PR 已合并”描述为“生产已上线”。

## 6. 上线判定

只有在上述生产环境门禁有可审计的真实证据、支付渠道和小程序平台验收完成、备份恢复演练成功、监控告警与回滚演练通过后，才可将发布状态更新为生产验收通过。当前判定：**代码仓库 CI 基线通过；生产部署与外部验收尚未完成。**
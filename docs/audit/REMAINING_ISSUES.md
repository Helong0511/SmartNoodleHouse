# Cwm9 遗留问题与改进清单

> 生成日期：2026-09-29  
> 最近代码推进：2026-10-19  
> 本清单只记录**真实存在、尚未由责任方闭环**的事项；代码侧已落地的项移入第 5 节。

---

## 1. 阻塞项（必须外部资源 / 人工，代码无法完成）

| 编号 | 事项 | 风险 | 责任方 | 建议动作 | 代码侧辅助 |
|---|---|---|---|---|---|
| BLK-001 | 微信/支付宝/美团/饿了么真实沙箱联调 | 中 | 运维 + 业务 | 官方真实凭证沙箱跑通 | 契约测试已通过 |
| BLK-002 | git 历史曾出现真实密钥 | **高** | 运维/安全 | 全量轮换密钥与库口令 | `python scripts/上线前运维检查清单.py` |
| BLK-003 | 生产 MySQL schema 与本地迁移差异 | 中 | DBA | `alembic upgrade head --sql` 预生产演练 | 本地已修 0019/0025 阻断并升到 `0036`；生产仍须预演 |
| BLK-004 | 监控告警接入 | 中 | 运维 | Prometheus/Grafana 配告警 | `/api` 性能指标端点已就绪 |
| BLK-005 | 小程序上架审核 | 低 | 业务 | 按上架检查清单提交 | — |

---

## 2. P1 级遗留

### P1-R1 限流 fail-closed 开关 — **代码已闭环**
- `RATE_LIMIT_FAIL_CLOSED_PATHS`：敏感前缀 Redis 宕机返回 503；其余 fail-open。
- 测试：`tests/测试_限流关闭失败.py`、`测试_消息幂等与域模块.py`。

### P1-R2 数据库自动备份与恢复演练 — **脚本已提供，运维需排期**
- `scripts/备份数据库.py` / `恢复数据库.py` / `备份恢复演练.py`
- 责任方：配置 cron 全量+binlog，季度在隔离库 `--restore-drill`。

### P1-R3 真实 MySQL 持续集成 — **代码已闭环**
- CI job `backend-mysql` + `@pytest.mark.mysql`；`TEST_DATABASE_URL` 注入。

### P1-R4 支付渠道 T+1 自动对账 — **骨架已闭环，账单源需运维**
- 表：`payment_reconcile_batches` / `payment_reconcile_diffs`
- API：`POST /api/reconcile/payments/import`（精简 CSV）
- 后续：对接微信/支付宝官方账单下载（需商户证书，属 BLK 范畴）。

### P1-R5 全链路压测与容量基线 — **脚本已提供，压测需排期**
- `python scripts/压测_核心链路.py --base http://127.0.0.1:8000`

### P1-R6 历史订单冷库归档 — **快照已闭环，物理删热表二期**
- 表：`archived_order_snapshots`；任务 `archive_old_orders`
- API：`/api/reconcile/orders/archive`、归档查询
- 说明：因 payments/ledger FK，当前仅写冷库快照，不删热表。

---

## 3. P2 级遗留

### P2-R1 前端自动化测试 — **Vitest 已接入**
- `frontend`：`npm test`（`bizCode.spec.ts`）；CI frontend-build 会跑。
- Playwright E2E 仍待排期。

### P2-R2 大表在线 DDL 工具 — **手册已补充**
- 见 `scripts/生产部署-ubuntu.txt`「大表在线 DDL」节（pt-osc / gh-ost 指引）。

### P2-R3 字段级权限 — **代码已闭环**
- `field_policy`：成本价 `product:cost:read`；手机号脱敏需 `customer:pii`。
- `products.cost_price` 列已加入 schema。

### P2-R4 角色可视化编辑 — **已增强**
- 原有权限矩阵保留；新增「新建角色」与 `POST /api/users/roles`。

### P2-R5 临时授权 / 委派 — **代码已闭环**
- 表 `role_delegations`；API `/api/users/delegations*`；调度回收过期授权。

### P2-R6 分库分表 / 读写分离预案 — **文档已补充**
- 见生产部署手册「扩展预案」节。

### P2-R7 JWT 算法演进 — **配置已预留**
- `JWT_ALGORITHM` + `JWT_PRIVATE_KEY` / `JWT_PUBLIC_KEY`；默认仍 HS256。

### P2-R8 测试桩 warning 治理 — **部分闭环**
- `测试_智能推荐_12性能治理.py` 已迁 `httpx.ASGITransport`。
- `测试_支付退款.py` outbox mock 告警若复现再收紧。

---

## 4. P3 级遗留（体验 / 文档 / 小优化）

- Playwright E2E 固化分页/筛选：待排期（Vitest 已起步）。
- 蓝绿/金丝雀：仍依赖人工；手册可继续补强。
- 推荐效果看板运营文档：择机。
- 巡检脚本定时化：可用 cron 调用 `scripts/验证数据库.py` 与冒烟脚本。

---

## 5. 本轮及历史已闭环（避免重复立项）

1. sql/struct.sql 口令占位、测试桩修复、限流默认 fail-open 理由固化（见旧记录）。
2. **2026-10-19**：P1-R1/R3/R4骨架/R6快照、P2-R1起步/R3/R4增强/R5/R7预留/R8部分、备份恢复与压测脚本、运维检查清单脚本。

---

## 6. 优先级建议汇总

| 顺序 | 事项 | 理由 |
|---|---|---|
| 立即 | BLK-002 密钥轮换 | 历史泄露 |
| 上线前 | BLK-001/003/004/005、P1-R2 备份演练 | 生产稳定前提 |
| 近期 | 官方账单自动拉取、订单热表物理归档、Playwright | 资金与数据生命周期 |
| 择机 | P3 | 体验增强 |

---

## 7. 结论

代码层面可独立完成的审计遗留项已落地并通过针对性测试；**上线阻断仍集中在运维与外部凭证**（密钥轮换、沙箱、监控、备份演练）。运行 `python scripts/上线前运维检查清单.py` 可打印人工待办。

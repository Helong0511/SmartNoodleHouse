> **2026-11-08 复核规则**：本文件包含 2026-09/10 的历史阻塞记录。每条必须重新核对当前 main 后才可视为仍然有效；历史记录不得自动当成当前遗留。外部凭证、平台审核、生产密钥轮换、监控、备份恢复、生产迁移和真实设备联调属于需要责任人提供证据的外部/运维验收。当前本机验证详见 [TEST_REPORT.md](TEST_REPORT.md)。
>
> ---
>
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

### P2-R7 JWT 算法演进 — **代码已支持，生产密钥仍需配置**
- 启动校验与实际验签集合已对齐，支持 HS256 / RS256 / EdDSA；生产环境启用非对称算法仍须配置有效私钥、公钥并完成轮换演练。

### P2-R8 测试桩 warning 治理 — **当前 CI 已通过严格 warning 门禁**
- 推荐测试已迁 `httpx.ASGITransport`。
- 最新后端 CI 使用 RuntimeWarning / PytestUnraisableExceptionWarning 严格门禁，2020 条通过；历史 outbox mock warning 未在当前套件复现，后续继续保持严格门禁。

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

---

## 2026-11-09 增量复核：当前代码状态与真正剩余项

本节更新历史台账，不抹去 2026-09 / 2026-10 的原始记录。代码证据以当前 PR #115 分支为准。

### 已由当前代码与 CI 重新验证

- POS 退款执行采用数据库 fencing：单调递增 execution_epoch、随机 owner token 和首笔退款单绑定前租约；退款单持久化后由查单/回调收敛，Redis 不参与资金所有权判断。
- 0051_pos_refund_execution_fencing Alembic 迁移、ORM、sql/struct.sql 基准结构一致；MySQL 8 空库升级、降级再升级和结构检查通过。
- Redis 7.4 真实服务测试覆盖 generation、owner 锁、旧 owner 写入拒绝、缓存失效和旧代际读取拒绝。
- 全局 ASGI RequestBodyLimitMiddleware 已注册，普通请求体 7MiB；支付/外卖 webhook 另有 512KiB 限制；声明长度与 chunked 超限均有测试。
- 支付主动查单的 Redis 冷却在 Redis 瞬时断连时 fail-closed 返回 503，不再暴露未处理 500。
- 微信/支付宝充值幂等缓存按顾客/档位/门店保存多组 key，A→B→A 切换复用原 key，成功按本次 key 精确清理；已移除本地 24h TTL 自动轮换，避免待确认支付生成第二笔充值单；缓存异常时拒绝创建新充值单。
- JWT 算法启动校验与实际验签能力一致，支持 HS256 / RS256 / EdDSA。
- DbSession 使用 Depends(get_db, scope=function)，commit/rollback 在响应发送前执行；隐式提交仍要求路由不得吞写入异常后返回成功。

### 当前代码 CI 证据

代码提交 443c1e201212cb6044ebea5038b359e439a18ebd 对应 Actions run 42590160163：12/12 jobs success；后端 2020 passed、16 skipped，覆盖率 60.35%；MySQL 8、Redis 7.4、前端双 Node 版本构建、依赖锁一致性/安全审计、双端小程序专项、Windows Rust DLL 均通过。该 CI 结果只覆盖此 SHA；PR 后续文档提交仍需检查最新 head 的 CI。

### 仍需人工或真实平台完成（不是 CI 可替代项）

1. BLK-001：微信/支付宝支付、退款、外卖平台官方沙箱凭证联调。
2. BLK-002：历史日志曾暴露的微信 AppSecret 必须在微信公众平台轮换，并清理含旧值的日志/备份/快照。
3. BLK-003：预生产执行生产 schema 差异检查、备份后迁移演练；CI 空库迁移通过不代表已有生产库可直接升级。
4. BLK-004：Prometheus/Grafana 告警规则与通知渠道接入并做告警演练。
5. BLK-005：微信/支付宝隐私政策、手机号、订阅、支付回调的真机和平台审核。
6. 两端 PROD_API_BASE 仍为占位符；配置真实 HTTPS 域名并完成合法域名白名单前不得发布正式版。
7. 人脸 action_completed 仍是客户端自报，当前仅能作为低风险迎宾挑战，不是视觉活体证明；不得用于登录/支付/高风险认证。
8. 支付宝 my.openPrivacyContract 需目标基础库真机确认。
9. 备份恢复演练、真实门店高并发压测（扫码/支付/退款/库存/KDS/Outbox 联合）仍需预生产执行。
10. Playwright 完整 E2E、生产容量基线和外卖平台账单自动拉取仍未闭环。

结论：代码层已完成本轮资金 fencing、Redis 真实集成、充值幂等和请求体限制修复，且代码 CI 通过；正式上线仍受外部凭证、域名、真机合规、备份/监控/压测验收约束。

### 追加复核：退款并发快照一致性（2026-11-09）

- 发现 MySQL `REPEATABLE READ` 下，普通定位查询可能提前建立快照；等待 Order/Payment 锁后再用普通聚合统计退款，会漏掉等待期间刚提交的退款。
- 已将关键退款判断改为 Order→Payment→PaymentRefund 锁序下的 `FOR UPDATE` 当前读，并刷新 ORM identity map；增加单测覆盖退款当前读、订单净额当前读、锁定 Payment 刷新及在途退款拒绝重复提交。
- 最新代码 SHA `4a98702eff3205c7685ba0c412ff1def5179efd3` 的 CI 尚在排队/等待，不能沿用旧 SHA 的 CI 结果。

### 2026-11-10 增量复核：渠道关单终态确认

- 复核 PaymentService.close_channel_payments() 与 close_channel_payments_by_id() 时发现：锁外关单路径在渠道关单失败、查单返回 UNKNOWN 或查单异常后，仍可能继续把本地 PENDING 支付单改为 CLOSED。该状态无法证明渠道侧未扣款，可能造成已付款订单被本地误关。
- 已新增 _channel_trade_is_terminal_unpaid()：微信仅 CLOSED/REVOKED/PAYERROR 可作为未支付终态；支付宝必须同时满足 trade_status=TRADE_CLOSED 且撤销响应 action=close。支付宝 action=refund 代表渠道已触发退款，不能当作未支付关单；action 缺失也保留 PENDING。NOTPAY/USERPAYING/WAIT_BUYER_PAY/UNKNOWN/NOT_CONFIGURED 等状态同样保留 PENDING。
- 两条关单路径均已增加终态门禁；锁外路径在关单失败或查单异常时不再继续本地 CLOSED。
- 回归测试新增覆盖渠道关单超时 + UNKNOWN、关单超时 + 查单异常、处理中状态，以及渠道明确 CLOSED 后仍可安全完成本地关单。
- 官方状态依据：微信支付交易状态枚举明确区分 CLOSED、REVOKED、PAYERROR 与 NOTPAY、USERPAYING；支付宝 TRADE_CLOSED 可能表示未付款关闭，也可能表示付款后全额退款，撤销接口 action=close 表示无退款关闭、action=refund 表示已触发退款。见微信支付查询订单文档及支付宝 alipay.trade.cancel / 交易状态说明。
- 关联代码提交：226ef93ef407b15bbd391c302042cf4844e07355、9cdf9a29bf175f1769bf6d16d4dc592446ee0553；测试提交：5c4303a16fc6a3517ecaf2b5aa26288e0893161f、545c129d03b1ed78769682d74dae5fdacfe99dbd、f00135be0213bd408a67e66237fe728b07b69368。最终 HEAD 的 CI 仍待执行完成，未提前标记测试通过。


### 2026-11-10 增量复核：支付宝 OpenAPI 同步响应验签

- 发现 alipay_gateway_request() 只解析 HTTPS JSON，alipay_client._gateway_call() 直接信任 alipay_trade_query/cancel/refund 的响应字段，没有验证响应顶层 sign。仅有 HTTPS 不能替代支付宝响应签名校验。
- 已新增 verify_alipay_response_signature()：使用配置的支付宝公钥对接口响应节点做 RSA2/SHA-256 验签；支持标准紧凑 JSON、键排序、URL 斜杠转义及 Unicode 转义序列化变体。
- _gateway_call() 在检查业务 code 和读取交易状态之前先验签；响应节点缺失、sign 缺失、签名不匹配或公钥解析失败均 fail-closed，抛出 50022，不会将未验签的交易状态用于入账/退款/关单判断。
- 回归测试覆盖有效签名、转义斜杠、中文嵌套字段、篡改响应、缺少签名，以及客户端对未签名响应拒绝处理。
- 官方依据：支付宝开放平台同步返回验签说明（https://developer.alibaba.com/docs/doc.htm?articleId=105975&docType=1&treeId=293）；标准网关响应签名说明（https://jaq-doc.alibaba.com/docs/doc.htm?articleId=104613&docType=1&treeId=140）；交易查询响应示例（https://jaq-doc.alibaba.com/docs/api.htm?apiId=757&docType=4）。
- 关联提交：d351b3ec5298ab6f2c7cb8a7ebff0da3d69c35d4、3fa6d301a3a3d4f73e5ba0db5c6aa1d0da3afeb7、c9374ef076b219106bd3db5d2789768a13879a81、9ec373d2047789761657d5d2e606c05448187c66、56c4e61953aa7062bcb2ebbd8aed9bb7a7c61367、aed8f1240e3c8ae9334da72c34c9191e549244b9。最新 HEAD 的 CI 尚未完成，验签实现必须以最新 CI 结果验收。

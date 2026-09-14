# P0/P1 全量修复计划（23 项）

## Context

用户列出 7 项 P0 + 16 项 P1 问题，要求全部修复（"直接修改代码"，非仅建议）。我已逐一读源码验证现状，确认：

- **2 项 P0 已修复**（#1 跨门店商品、#5 已支付订单人工取消）—— 有测试覆盖，仅做加固/复核。
- **5 项 P0 真实存在缺陷**（#2/#3/#4/#6/#7），需代码 + `sql/tools/` 补丁 + 测试。
- **3 项 P1 已部分缓解**（定时任务 leader lock、智能口味 weekday/scene、自动化测试体系）—— 需验证 + 小幅加固，不重建。
- **13 项 P1 真实存在**，按 small/medium/large 分批。

**Schema 约定**：项目无 Alembic，`sql/smart_noodle_house.sql` 是唯一基线（用户要求不动）。`sql/tools/` 目录目前**不存在**（README 提到的 003-010 实际缺失）—— 本计划新建该目录并建立幂等补丁约定。

## 验证现状速查

| # | 问题 | 现状 | 关键代码 |
|---|------|------|---------|
| P0-1 | 跨门店商品 | 已修复（核心） | `order_service.py` L201-L238 强制 `Product.store_id`，抛 `40002`；`test_cross_store_product.py` 存在 |
| P0-2 | 支付回调 Redis 防重 | 部分修复 | `payment_service.py` L1124-L1231：`PaymentCallbackReceipt` UNIQUE 是最终源；`except Exception` 过宽；Redis-false 未回查 DB |
| P0-3 | 退款分布式事务 | 真实缺陷 | `_finalize_refund` L1481-L1493：积分扣减/口味重算 `except: logger` 静默吞；同步退款 provider 调用先于本地 commit，无补偿 |
| P0-4 | Webhook stale PROCESSING | 部分修复 | `webhook_inbox.py` L106-L141 原子 claim 良好；**无 stale 回收**；`process_pending_inbox` L228 计调用数非 claim 数 |
| P0-5 | 已支付订单取消 | 已修复 | `order_state_machine.py` L6-L20 禁 PAID→CANCELLED；`test_paid_cancel_forbidden.py` 存在 |
| P0-6 | 优惠券并发重复 | 真实缺陷 | `coupon_service.py` L164-L202：无 `FOR UPDATE`、无原子扣减 `remaining`、无锁。L199 注释确认 (customer,template) 重复是**业务允许**（年度券），故不加 UNIQUE |
| P0-7 | 支付副作用吞掉 | 真实缺陷 | `paid_side_effects.py` 用 **Redis list** 队列；`enqueue_paid_side_effect` L14-L23 吞 enqueue 失败；**全仓无 `OutboxEvent`** |

---

## 执行批次

### Batch 0 — 共享基础设施（最先做，阻塞 #3/#7）

**0a. 新建 `OutboxEvent` 模型 + `sql/tools/001_outbox_events.sql`**（幂等 `CREATE TABLE IF NOT EXISTS`）
- 列：`id` PK、`aggregate_type` VARCHAR(32)（`refund`/`paid_side_effect`）、`aggregate_id` BIGINT、`event_kind` VARCHAR(64)（`refund_finalize`/`points_award`/`coupon_mark_used`/`pref_update`/`points_deduct_refund`）、`payload` JSON、`status` VARCHAR(16) DEFAULT `PENDING`（PENDING/PROCESSING/DONE/FAILED）、`attempt_count` INT DEFAULT 0、`max_attempts` INT DEFAULT 8、`next_retry_at` DATETIME NULL、`locked_at` DATETIME NULL、`locked_by` VARCHAR(64) NULL、`last_error` TEXT NULL、`created_at`/`updated_at`/`processed_at`
- **UNIQUE(aggregate_type, aggregate_id, event_kind)** — 幂等键
- 索引：`INDEX(status, next_retry_at)`、`INDEX(status, locked_at)`

**0b. `backend/app/services/outbox_worker.py`**（新建，仿 `webhook_inbox.py` 原子 claim）
- `enqueue_outbox(db, agg_type, agg_id, kind, payload)` —— 同一 DB 事务写入，无 Redis
- `claim_outbox_item(db, worker_id, stale_after=300s)` —— `UPDATE...WHERE status IN('PENDING', retry-eligible 'FAILED') ... LIMIT 1` 原子
- `reclaim_stale_processing(db, stale_after=300s)` —— `UPDATE...WHERE status='PROCESSING' AND locked_at < now - stale_after` → 复位 PENDING（同时供 #4 webhook_inbox 复用）
- `process_pending_outbox(limit=30)` —— 真实统计 claim 成功数；handler 注册表 `OUTBOX_HANDLERS: dict[(agg_type, kind), Callable]`
- 指数退避：`next_retry_at = now + min(2**attempt, 600)`

**0c. `backend/app/tasks/scheduler.py`** 注册 `process_pending_outbox`（30s）+ `reclaim_stale_processing`（5min），自动被 `_leader_job` fence

**0d. `backend/app/core/leader_lock.py` Lua 原子化**（0c 前置）
- `release_leader`：`GET`-then-`DELETE` → 改为 `EVAL` compare-and-delete（L77-L78）
- `try_acquire_leader` 续期分支：裸 `EXPIRE`（L58）→ 改为 `EVAL` compare-and-EXPIRE
- `renew_leader_loop` 调用上述原子续期
- 新测试 `tests/test_leader_lock_atomicity.py`：A 持锁过期后 B 获锁，A release 不删 B 的锁；A renew 不续 B 的锁

**0e. `sql/tools/` 目录约定**：`001_outbox_events.sql` 头注释模板（幂等/可回滚/范围）

### Batch 1 — 依赖 outbox 的 P0（#3、#7）

**#3 退款补偿**
- `payment_service.py` 同步 `refund()`：provider API 成功后、本地 commit 前，**同一事务**内 `enqueue_outbox('refund', refund_id, 'refund_finalize', payload)`。commit 失败后 outbox worker 重驱
- `_finalize_refund` L1481-L1493：`except: logger` 吞积分扣减 → 改为 `enqueue_outbox('paid_side_effect', order_id, 'points_deduct_refund')` + `enqueue_outbox(...,'pref_recompute')`，loss 可恢复而非静默
- worker handler 容错：`refund.status=='PENDING'` 时 no-op 等下次重试
- 新测试 `tests/test_refund_compensation.py`：provider 成功 + 本地 rollback → outbox 重驱恢复

**#7 支付副作用 outbox 化**
- `paid_side_effects.py` 重写：`enqueue_paid_side_effect` → 调 `enqueue_outbox('paid_side_effect', order_id, kind)` 为 points/coupon/pref 各写一条，**与支付 PAID 同事务 commit**
- 保留 `run_paid_side_effects` 作为 outbox handler；移除 Redis `QUEUE_KEY` 与 `process_pending_side_effects` Redis 消费者
- **前置验证**：`award_points_for_paid_order` / `mark_used` / `update_from_order` 必须真正幂等（按 `order_id`）；若不幂等先补幂等键
- `scheduler.py` 移除 `retry_paid_side_effects` job（被 outbox 替代）
- 新测试 `tests/test_paid_side_effects_outbox.py`：Redis 宕机时副作用不丢

### Batch 2 — 独立 P0（与 Batch 1 并行）

**#2 支付回调幂等收紧**（`payment_service.py` L1209-L1231）
- `except Exception` → `except IntegrityError`；其他 DB 异常 rollback+raise 让 provider 重试
- Redis `claim_once` 返回 False 时：先 `SELECT PaymentCallbackReceipt WHERE provider=? AND receipt_key=?`，存在且 DONE 才短路，不存在则继续处理（修复 crash-between-Redis-claim-and-receipt-commit）
- 扩展 `tests/test_payment_webhook.py`：`test_callback_integrity_error_is_duplicate`、`test_callback_db_operational_error_is_not_duplicate`、`test_callback_redis_claim_false_but_db_missing_retries`

**#4 Webhook Inbox stale 回收**
- `webhook_inbox.py` 复用 0b 的 `reclaim_stale_processing`（或独立 `reclaim_stale_webhook` 同模式）
- `process_pending_inbox` L228：claim 后重查行状态确认 claim 成功才计 `done`
- `scheduler.py` 注册 stale 回收 job（5min）
- 扩展 `tests/test_payment_webhook.py`：`test_webhook_processing_recovery`、`test_webhook_concurrent_claim`、`test_webhook_done_not_reprocessed`

**#6 优惠券并发**（`coupon_service.py` L164-L202 + `sql/tools/002_coupon_quota_atomic.sql`）
- **不加 UNIQUE(customer_id, template_id)**（L199 注释确认业务允许年度券等重复领）
- 改为原子扣减模板库存：`UPDATE coupon_templates SET remaining = remaining - qty WHERE id=? AND remaining >= qty`，rowcount=0 → `40069 已领完`
- 客户端可选 idempotency key：`CouponIssueRequest` 增 `client_request_id`，有则 Redis 锁 `snh:coupon:issue:{customer_id}:{template_id}:{client_request_id}` 30s + DB 唯一约束（client_request_id 全局唯一）
- 扩展 `tests/test_coupon_multi_issue.py`：100 并发同 customer+template（无 client_request_id）→ 成功发放数 ≤ `remaining`；100 并发同 client_request_id → 仅成功 1 次

**#1 跨门店商品加固**（小）
- 验证 option_value 经 product 反查 store_id；admin 商品/规格/选项 API list/detail 加 `store_id` 过滤
- 新测试 `tests/test_cross_store_product_admin.py`

### Batch 3 — 已修复 P0 复核（仅测试运行）

- **#5** 跑 `tests/test_paid_cancel_forbidden.py` 确认通过

### Batch 4 — P1 修复

**Small（SQL/config/secret-only）：**
- **外卖 Token 明文** → `secret_encryption.py` 复用；`delivery_service` 读写加密；`sql/tools/003_delivery_token_encrypted.sql` 加 `token_encrypted`/`refresh_token_encrypted` 列
- **摄像头密码明文回退** → `camera_credentials.py` 解密失败时不回退明文，抛错；`sql/tools/004_camera_password_strict.sql`
- **生产 admin 账号/密码 hash** → `sql/smart_noodle_house.sql` 已注释说明用 `init_admin.py`；新增 `sql/tools/005_neutralize_seed_admin.sql` 删除/禁用 seed admin（幂等），强制首次改密
- **订单幂等 key** → `order_service` idempotency key 纳入 `store_id + channel + amount + customer_id`；扩展 `tests/test_order_idempotency_scope.py`
- **DB migration baseline** → `sql/tools/000_baseline_snapshot.sql` 仅文档注释当前 schema 版本（建立迁移序号锚点）

**Medium：**
- **Redis 限流 fail-open** → 跑 `tests/test_rate_limit_fail_closed.py`，补 Redis-down fail-closed 路径
- **后台顾客详情 IDOR** → `customers.py` 详情 API 加 ownership/store scope 校验；新测试 `tests/test_customer_detail_idor.py`
- **商品/规格 API 门店隔离** → 扩展 #1 的 admin isolation 到 spec/option endpoint
- **人脸授权闭环** → 小程序 face consent 前端真正调摄像头 API（`wx.createCameraContext` / `my.camera`），禁相册冒充；后端校验 `customer.member_status==MEMBER`
- **外卖 PII 清理** → 定时任务对已完结 N 天订单的手机号/地址做脱敏/清空；`sql/tools/006_delivery_pii_redaction.sql` 加 `pii_redacted_at` 列
- **支付对账** → 定时任务对 provider 账单 vs `FinancialLedgerService` 差异告警
- **H5 customer token 401 清理** → `frontend/src/api/index.ts` 区分 customer/staff 401 分别清 token

**Large：**
- **摄像头 URL SSRF/DNS Rebinding** → `cameras.py` RTSP URL 校验：禁私网 IP、DNS 解析后比对、禁 redirect；新测试 `tests/test_camera_url_ssrf.py`
- **人脸活体方案不足** → `face_liveness.py` + config `FACE_ANTISPOOF_MODE`（basic/vendor/disabled），production basic 时告警；UI 明确"非金融级活体"
- **CI/测试体系** → 检查 `.github/workflows/ci.yml`，补 pytest + coverage gate + frontend build

### Batch 5 — P1 已缓解项（验证 + 小加固，不重建）

1. **定时任务 leader lock**：`leader_lock.py` 存在，`SET NX EX` 原子获取（L47 良好）；release/renew 有 TOCTOU（L77-L78 / L58）→ Batch 0d Lua 升级已覆盖。验证后跑 `test_leader_lock_atomicity.py`
2. **智能口味 weekday/time/scene**：`preference_scene.py` L98-L129 已用 `weekday()`/`_in_meal_hours()`(11-14/17-22)/`season`/`weather_condition`。验证 `test_preference_scene_and_option_scope.py` 覆盖度；补 `distinct_order_count >= 2` 门槛（防 quantity=2 误判）+ `days_since_order` 半衰期
3. **自动化测试体系**：`tests/` 110+ 文件存在。验证 `.github/workflows/`；Batch 4 large 补 coverage gate

---

## 关键文件清单

- `e:\SmartNoodleHouse\backend\app\services\outbox_worker.py`（新建，Batch 0b）
- `e:\SmartNoodleHouse\backend\app\models\__init__.py`（加 `OutboxEvent` 模型）
- `e:\SmartNoodleHouse\backend\app\services\payment_service.py`（Batch 1 #3 L1386-L1504；Batch 2 #2 L1209-L1231）
- `e:\SmartNoodleHouse\backend\app\services\paid_side_effects.py`（Batch 1 #7 重写 Redis→outbox）
- `e:\SmartNoodleHouse\backend\app\services\coupon_service.py`（Batch 2 #6 L164-L202 原子扣减 + idempotency）
- `e:\SmartNoodleHouse\backend\app\core\leader_lock.py`（Batch 0d Lua 升级 L57-L82）
- `e:\SmartNoodleHouse\backend\app\services\webhook_inbox.py`（Batch 2 #4 stale 回收 + count 修正）
- `e:\SmartNoodleHouse\backend\app\services\order_service.py`（Batch 2 #1 option_value store scope）
- `e:\SmartNoodleHouse\backend\app\tasks\scheduler.py`（注册 outbox worker + 移除旧 Redis 副作用 job）
- `e:\SmartNoodleHouse\backend\app\api\v1\customers.py`（Batch 4 IDOR）
- `e:\SmartNoodleHouse\backend\app\api\v1\cameras.py`（Batch 4 SSRF）
- `e:\SmartNoodleHouse\backend\app\services\delivery_service.py` + `platform_token_service.py`（Batch 4 token 加密）
- `e:\SmartNoodleHouse\backend\app\services\camera_credentials.py`（Batch 4 禁明文回退）
- `e:\SmartNoodleHouse\backend\app\services\face_liveness.py`（Batch 4 antispoof mode）
- `e:\SmartNoodleHouse\miniprogram-wechat\pages\face\` + `miniprogram-alipay\pages\face\`（Batch 4 摄像头闭环）
- `e:\SmartNoodleHouse\frontend\src\api\index.ts`（Batch 4 H5 401 清理）

## sql/tools/ 补丁清单（全部幂等）

1. `001_outbox_events.sql` — OutboxEvent 表 + 索引（Batch 0a）
2. `002_coupon_quota_atomic.sql` — `coupon_templates` 增 `remaining`/`issued_count` 列（若缺）+ 索引（Batch 2 #6）
3. `003_delivery_token_encrypted.sql` — `delivery_store_bindings`/`platform_tokens` 加密列（Batch 4）
4. `004_camera_password_strict.sql` — `cameras.password_encrypted` NOT NULL 强化（Batch 4）
5. `005_neutralize_seed_admin.sql` — 禁用/删除 seed admin（Batch 4）
6. `006_delivery_pii_redaction.sql` — `delivery_orders` 加 `pii_redacted_at` 列（Batch 4）
7. `000_baseline_snapshot.sql` — 仅文档注释，锚定基线版本（Batch 4）

## 测试清单

新建/扩展：
- `tests/test_outbox_worker.py` — enqueue/claim/stale 回收/幂等（Batch 0b）
- `tests/test_leader_lock_atomicity.py` — Lua 原子性 + 并发（Batch 0d）
- `tests/test_refund_compensation.py` — provider 成功 + 本地 rollback（Batch 1 #3）
- `tests/test_paid_side_effects_outbox.py` — Redis 宕机副作用不丢（Batch 1 #7）
- 扩展 `tests/test_payment_webhook.py` — IntegrityError vs OperationalError；stale PROCESSING 回收（Batch 2 #2/#4）
- 扩展 `tests/test_coupon_multi_issue.py` — 100 并发模板库存原子扣减；client_request_id 幂等（Batch 2 #6）
- `tests/test_cross_store_product_admin.py`（Batch 2 #1）
- `tests/test_customer_detail_idor.py`（Batch 4）
- `tests/test_camera_url_ssrf.py`（Batch 4）

## 横切风险

1. **scheduler 槽位**：outbox worker + 现有 `drain_webhook_inbox`/`poll_payment_refunds`/`auto_complete_kitchen_orders` 等同 1-2min，均 leader-fenced。#7 迁移后移除 `retry_paid_side_effects`，净 job 数下降
2. **outbox handler 幂等性**：`run_paid_side_effects` 注释自陈"幂等"，但 `award_points_for_paid_order`/`mark_used`/`update_from_order` 必须先验证按 `order_id` 真幂等，否则补幂等键
3. **leader lock TOCTOU**：outbox worker 依赖单 leader，0d Lua 升级必须在 0c 上线前完成
4. **#3 补偿时序**：provider API 调用须在 outbox enqueue + 本地 commit 之前；outbox 行与 refund 行必须同事务；handler 容忍 `refund.status=='PENDING'` no-op
5. **#6 业务约束**：不加 UNIQUE(customer_id, template_id)，避免破坏年度券重复领取；用模板级原子扣减 + 可选 client_request_id 幂等
6. **`sql/tools/` 目录新建**：README 提及的 003-010 实际缺失，本计划新建目录并确立约定

## 部分完成 / 推迟项

- **#5 已支付订单取消**：已修复，仅跑测试确认
- **face liveness vendor SDK 接入**：依赖外部供应商，本计划仅做 mode 配置 + UI 标注，真正 vendor SDK 接入留作后续
- **完整 CI coverage gate**：本计划补 pytest + 基础 coverage 报告，严格 gate threshold 视团队偏好后续调
- **H5 真正 customer session**：审计文档提及但用户列表未列，本计划不展开

## 验证

每个 Batch 完成后：
1. `python -m compileall backend`
2. `pytest tests/test_<相关>.py -v`
3. 对应 `sql/tools/NNN_*.sql` 在测试库执行确认幂等
4. Batch 4 完成后：`cd frontend && npm run build`
5. 全量收尾：`pytest` + `python tests/simulation_e2e.py` + `git diff` 复查无 secret/debug/TODO 残留

## 执行顺序

Batch 0 → Batch 1（依赖 0）+ Batch 2（与 1 并行）→ Batch 3 → Batch 4 small → Batch 4 medium → Batch 4 large → Batch 5 验证。用 TodoWrite 跟踪每项。

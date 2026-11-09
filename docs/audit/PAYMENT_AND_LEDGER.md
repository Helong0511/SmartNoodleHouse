> **文档维护说明（2026-11-08）**：支付与账本审计需同时核验渠道流水、支付回调收据、退款状态、组合支付分摊和账本分录；金额、幂等和事务边界必须通过并发测试。 当前事实以 main 代码、迁移、CI 和真实执行证据为准；历史快照不自动代表当前状态。禁止写入有效密钥、密码或 token。
>
> ---
>
# Cwm9 支付、退款与财务台账审计报告

> 审计日期：2026-09-29
> 审计范围：微信支付（JSAPI/Native/付款码）、支付宝（JSAPI/当面付）、POS 组合支付、退款、财务台账、外卖平台收款
> 审计方式：真实代码走读 + 资金链路测试（791 passed, 8 skipped）

---

## 1. 资金链路总览

```
顾客小程序/POS
   │
   ├─ 下单（订单状态 PENDING_PAYMENT）
   │     └─ 服务端 calculate_items_batch 重算金额
   │     └─ 幂等键：dining_type + table_id + idempotency_key UNIQUE
   │
   ├─ 发起支付（Payment 状态 PENDING）
   │     ├─ 微信：统一下单 → prepay_id → 小程序调起
   │     ├─ 支付宝：alipay.trade.create → tradeNO
   │     └─ 付款码：被扫 → barcode_pay_service
   │
   ├─ 平台异步回调
   │     ├─ Redis claim_once(provider, receipt_key)
   │     ├─ RSA2/SHA256 验签 + appid/mchid 校验
   │     ├─ DB PaymentCallbackReceipt UNIQUE 兜底
   │     ├─ 订单状态机：PENDING_PAYMENT → PAID
   │     │             CONFIRMED + 成功支付 → PAID（安全兜底）
   │     └─ paid_side_effects（Outbox 事务发件箱）
   │           ├─ 发券核销
   │           ├─ 积分/余额
   │           ├─ KDS 任务
   │           └─ 打印任务
   │
   ├─ 同步查询（前端兜底）
   │     └─ 主动调平台 orderquery，回调丢失时补单
   │
   └─ 退款（PosRefundRequest 工作流）
         ├─ 收银员发起 PENDING
         ├─ 店长/超管/支付退款权限审批
         ├─ FOR UPDATE 锁定申请单
         ├─ 组合支付按"现金优先 → 扫码"分摊
         ├─ 调平台退款（支付宝/微信）
         ├─ PaymentRefund 幂等键 UNIQUE
         ├─ 状态 PROCESSING / SUCCESS / FAILED / PENDING
         └─ 副作用（积分扣减、等级降级）走 Outbox 重试
```

---

## 2. 订单状态机（[backend/app/services/order_state_machine.py](file:///e:/SmartNoodleHouse/backend/app/services/order_state_machine.py)）

### 2.1 三类转换矩阵

| 矩阵 | 触发方 | 用途 |
|---|---|---|
| STAFF_TRANSITIONS | 员工 | 接单/出餐/完成/取消 |
| PAYMENT_TRANSITIONS | 支付回调 | PAID/REFUND_PENDING 解决 |
| SYSTEM_TRANSITIONS | 定时任务/系统 | 超时关单/自动完成 |

### 2.2 关键禁止项

- **禁止** `PENDING_PAYMENT → CONFIRMED`：未支付订单不可人工推进厨房（P0-008）。
- **禁止** 员工直接置 `PAID/REFUNDED/CANCELLED`：资金状态只能由支付回调/退款工作流驱动。
- **允许** `CONFIRMED → PAID`：异常路径下厨房已接单但支付回调晚到，回调到达后安全兜底（P0-010）。
- **REFUND_PENDING 多出口**：可去 REFUNDED / PAID / CONFIRMED / PREPARING / COOKING / READY / COMPLETED，避免卡死中间态（P0-009）。

### 2.3 KDS 协同

- [kitchen_auto_service.py](file:///e:/SmartNoodleHouse/backend/app/services/kitchen_auto_service.py) 服务端按 `payment_status` 筛选，未支付订单不下发厨房、不显示"接单制作"按钮（P0-011）。
- 厨房端即使绕过前端调接口，状态机也会拒绝 PENDING_PAYMENT → CONFIRMED。

---

## 3. 支付下单

### 3.1 金额计算

- 后端 `calculate_items_batch`：
  - 校验 OptionGroup.store_id 与订单门店一致（P1-109）。
  - 重算餐品价格、规格加价、优惠券、会员折扣、打包费。
  - 客户端传入金额仅作展示，不参与扣款。
- 金额统一通过 `app.core.response.money` 转 Decimal(scale=2)，避免浮点误差。

### 3.2 幂等

- 小程序下单：`idempotency_key` 必填，DB UNIQUE(dining_type, table_id, idempotency_key)。
- POS 下单：同样强制 idempotency_key（P1-115）。
- Payment 表：`payment_no` UNIQUE；渠道返回的 `transaction_id` 也建唯一索引。

### 3.3 支付渠道

| 渠道 | 文件 | 关键控制 |
|---|---|---|
| 微信 JSAPI | [wechat_pay_client.py](file:///e:/SmartNoodleHouse/backend/app/services/wechat_pay_client.py) | V3 RSA2 签名、平台证书自动下载 |
| 微信 V2 | [wechat_pay_v2_client.py](file:///e:/SmartNoodleHouse/backend/app/services/wechat_pay_v2_client.py) | 仅兼容老商户，V2/V3 key 独立 |
| 微信小程序登录 | [wechat_mini_client.py](file:///e:/SmartNoodleHouse/backend/app/services/wechat_mini_client.py) | code2session 服务端调用 |
| 支付宝 | [alipay_client.py](file:///e:/SmartNoodleHouse/backend/app/services/alipay_client.py) | RSA2，alipay.trade.* |
| 支付宝小程序 | [alipay_mini_client.py](file:///e:/SmartNoodleHouse/backend/app/services/alipay_mini_client.py) | oauth.token 服务端调用 |
| 付款码（被扫） | [barcode_pay_service.py](file:///e:/SmartNoodleHouse/backend/app/services/barcode_pay_service.py) / [payment_barcode.py](file:///e:/SmartNoodleHouse/backend/app/services/payment_barcode.py) | 18 位微信/25 位支付宝路由，轮询查单 |

### 3.4 沙箱与 Mock

- `ALLOW_PAYMENT_MOCK=true` 仅允许开发环境；生产启动校验拒绝（[startup_checks.py](file:///e:/SmartNoodleHouse/backend/app/core/startup_checks.py)）。
- 测试中不伪造签名算法，仅用平台官方 SDK 或契约 mock。

---

## 4. 支付回调

### 4.1 验签（[payment_verify.py](file:///e:/SmartNoodleHouse/backend/app/services/payment_verify.py)）

| 平台 | 算法 | 必校字段 |
|---|---|---|
| 微信 V3 | RSA2 + SHA256 | timestamp 偏差 ≤ 300s、serial（证书序列号）、mchid、appid |
| 支付宝 | RSA2 | app_id；验签前移除 sign/sign_type |

- 缺少/不匹配 mchid、app_id 一律拒绝（P0-023）。
- 证书序列号不在信任列表拒绝（防伪造平台证书）。

### 4.2 幂等三层

1. **Redis claim_once**：`SET NX PX`，跨进程快速互斥，TTL 覆盖处理窗口。
2. **DB 唯一约束**：`PaymentCallbackReceipt UNIQUE(provider, receipt_key)`（[models/__init__.py:1016-1024](file:///e:/SmartNoodleHouse/backend/app/models/__init__.py)）。
3. **业务状态机**：仅在期望状态才推进，重复回调不再触发副作用。

### 4.3 锁释放

- Redis claim 处理完成后释放；若 DB receipt 不存在（异常路径），必须释放锁避免永久占用（项目硬约束）。
- DB 唯一约束冲突时走 `savepoint`，不破坏外层事务。

### 4.4 副作用事务发件箱

[paid_side_effects.py](file:///e:/SmartNoodleHouse/backend/app/services/paid_side_effects.py) 在支付事务内写 `OutboxEvent`：

| 事件 | handler |
|---|---|
| 券核销 | coupon_service.use_for_order |
| 积分/余额 | wallet / balance service |
| KDS 任务 | kitchen_claim 幂等生成 |
| 打印任务 | print_tasks 入队 |

Outbox worker（[outbox_worker.py](file:///e:/SmartNoodleHouse/backend/app/services/outbox_worker.py)）：
- enqueue → claim（FOR UPDATE SKIP LOCKED）→ process → DONE
- 失败指数退避，超过 max_attempts 置 FAILED 等待人工
- 原子释放 `UPDATE ... WHERE locked_by = wid`，防止 reclaim 后误覆盖
- 旁路幂等：MySQL 先落库（同事务），commit 成功后再标 Redis，避免回滚后 Redis 脏键

### 4.5 同步补单

- 前端在回调丢失/网络异常时主动调 `/payments/sync`。
- 后端调平台 `orderquery`，按官方状态推进，不接受客户端传入状态。

---

## 5. 退款

### 5.1 POS 退款工作流（[pos_refund_service.py](file:///e:/SmartNoodleHouse/backend/app/services/pos_refund_service.py)）

```
收银员 PosRefundRequest(status=PENDING)
   │
   ├─ 校验订单归属（assert_order_store_access）
   ├─ 校验支付单 operator_id（收银员不可退他人收款）
   ├─ 整单可退合计 = PAID/PARTIAL_REFUND 支付单总和
   └─ 写申请单
        │
店长审批（FOR UPDATE）
   ├─ _can_approve：SUPER_ADMIN / STORE_ADMIN / payment:refund
   ├─ 状态非 PENDING/FAILED → 40014
   ├─ 组合支付分摊（现金优先 → 扫码）
   ├─ 逐笔调 PaymentService.refund
   │    ├─ SUCCESS：累加 executed_amount，flush
   │    ├─ PENDING/PROCESSING/UNKNOWN：申请单置 PROCESSING，不预记
   │    └─ FAILED：申请单 FAILED + fail_reason（正确列）
   └─ 全部成功 → SUCCESS
```

### 5.2 关键控制

- **金额解析失败硬拒绝**（P0-018）：支付宝/微信返回金额解析异常直接 raise，不 warning 后按 0 处理。
- **退款幂等键必填**（P0-019）：PaymentRefund 表 `refund_no` UNIQUE，渠道 `refund_id` 唯一索引。
- **行锁互斥**：审批/拒绝/撤销均 `with_for_update()`，并发操作不会覆写结果。
- **审批后撤销拒绝**：行锁等到 SUCCESS 后，撤销请求 40014。
- **fail_reason 列正确**：项目曾出现误写到其他列，已修复（P1-114）。
- **可退余额不足**：40052，申请单置 FAILED 并写原因，不调渠道。

### 5.3 退款副作用

- 积分扣减、会员等级降级通过 Outbox 异步执行（P1-108）。
- 退款渠道成功但本地副作用失败 → 事件入 outbox，worker 重试，不影响资金。
- 等级降级链路：金 → 银 → 普通，按累计消费实时重算（[测试_退款等级降级实时.py](file:///e:/SmartNoodleHouse/tests/测试_退款等级降级实时.py)）。

### 5.4 外卖退款

- 美团/饿了么退款回调按 `refund_id` 去重，savepoint 隔离 IntegrityError。
- 平台返回拒绝时不标记 APPROVED，保持 PENDING 并抛异常（[测试_P2认领修复.py](file:///e:/SmartNoodleHouse/tests/测试_P2认领修复.py)）。
- 门店锁：退款处理绑定门店，防跨店误操作。

---

## 6. 财务台账

### 6.1 PaymentLedger

- 每笔支付/退款写流水，`payment_id + entry_type` 唯一约束。
- 与 Payment 状态分离，允许一笔支付多条台账（部分退款）。
- 财务台账查询按门店隔离，仅财务/超管角色可访问。

### 6.2 余额账（BalanceLedger）

- 充值（RECHARGE）、消费（PAY）、退款（REFUND）、调整（ADJUST）。
- `order_id + entry_type=PAY` 唯一，幂等扣款。
- 扣款顺序：赠送金优先 → 本金，保留赠送金消耗审计。
- 非会员（GUEST）使用余额支付抛 40320（[测试_生产级审计P0.py](file:///e:/SmartNoodleHouse/tests/测试_生产级审计P0.py)）。
- `with_for_update` 行锁防并发透支。

### 6.3 积分账（MemberPointChange）

- 积分获取/消耗/过期/调整独立流水。
- 退款实时扣减积分；积分不足时按 0 兜底并记录。
- 等级按累计消费实时重算。

### 6.4 营收统计

- [revenue_stats.py](file:///e:/SmartNoodleHouse/backend/app/services/revenue_stats.py) 按门店/时间维度聚合。
- 仅统计 PAID/COMPLETED 订单，退款冲减计入净额。
- 测试：测试_营收统计.py、测试_财务台账.py、测试_仪表盘统计.py。

---

## 7. 库存与优惠券防超卖

### 7.1 库存（[stock_service.py](file:///e:/SmartNoodleHouse/backend/app/services/stock_service.py)）

- 原子扣减：`UPDATE stock SET qty = qty - :n WHERE id = :id AND qty >= :n`，rowcount=0 即库存不足。
- StockMovement 流水记录每次变动。
- 外卖镜像事件走 Outbox，平台推送失败可重试，不静默丢库存（P1-102）。

### 7.2 优惠券发放

- 原子扣减：`UPDATE coupon_template SET remaining = remaining - :qty WHERE id = :id AND remaining >= :qty`。
- `client_request_id` UNIQUE，发放接口幂等（P1-103）。
- 高并发测试：[测试_券并发.py](file:///e:/SmartNoodleHouse/tests/测试_券并发.py)。

### 7.3 券核销

- `reserve_for_order`：行锁 + 状态机 AVAILABLE → RESERVED。
- `release_for_order`：订单取消时 RESERVED/USED → AVAILABLE，按 `used_order_id` 反查兜底。
- 支付成功后 RESERVED → USED。

---

## 8. 超时关单与晚到账防护

[order_lifecycle.py](file:///e:/SmartNoodleHouse/backend/app/services/order_lifecycle.py)：

1. 定时扫描 `PENDING_PAYMENT` 且超 TTL 的订单。
2. 先调支付渠道 `trade_cancel`（微信）/ `alipay.trade.close`（支付宝）。
3. 渠道确认关闭后，本地订单置 CANCELLED。
4. 若渠道返回已支付，转入支付成功流程，不丢单。
5. 已释放的库存/券通过 Outbox 补偿。

**测试**：测试_订单生命周期.py、测试_已支付禁取消.py。

---

## 9. 资金相关测试矩阵

| 主题 | 测试文件 | 用例数（约） |
|---|---|---|
| 微信/支付宝回调验签 | 测试_支付Webhook.py、测试_支付宝JSAPI支付.py、测试_支付宝密钥.py | 30+ |
| 退款工作流 | 测试_支付退款.py、测试_POS退款.py、测试_多支付方式退款.py | 25+ |
| 付款码 | 测试_付款码支付服务.py、测试_支付付款码.py | 15+ |
| 订单幂等/状态机 | 测试_订单幂等范围.py、测试_P0未支付进厨房修复.py、测试_订单生命周期.py、测试_已支付禁取消.py | 20+ |
| 库存/券防超卖 | 测试_并发库存.py、测试_券并发.py、测试_外卖防超卖镜像.py、测试_订单创建券.py | 15+ |
| 财务/余额/积分 | 测试_财务台账.py、测试_会员积分.py、测试_退款扣积分实时.py、测试_退款等级降级实时.py | 20+ |
| POS | 测试_POS订单.py、测试_POS拆单支付.py、测试_POS加菜与抹零.py、测试_POS班次.py | 25+ |
| 外卖收款 | 测试_外卖.py、测试_外卖退款门店锁.py、测试_外卖凭证范围.py、测试_P2认领修复.py | 20+ |
| Outbox 可靠性 | 测试_Outbox工作者.py、测试_消息幂等与域模块.py、测试_Webhook收件箱回收.py | 15+ |

---

## 10. 遗留风险（资金类）

1. **真实沙箱联调待人工**（BLK-001）：所有平台适配层通过契约测试，但真实证书轮换、回调时序、金额边界（如 0.01 元退款）需在沙箱验证。
2. **对账自动化**：当前有 PaymentLedger 与财务台账，但银行/支付渠道自动对账文件解析未实现，建议下一阶段补 T+1 对账 job（详见 REMAINING_ISSUES）。
3. **现金交易无平台二次校验**：POS 现金收款完全依赖收银员操作，建议结合班次交接（已实现 POS 班次）与现金抽屉日志做双人复核。
4. **历史密钥**：git 历史中曾出现支付密钥，必须在微信商户平台/支付宝开放平台轮换并更新生产环境（BLK-002）。

---

## 11. 结论

资金链路核心控制点（验签、幂等、状态机、行锁、Outbox、超时关单）均有真实代码与对应测试，全套 791 个测试通过。未发现可导致重复入账、超额退款、跨店扣款或未支付进厨房的资金安全缺陷。遗留项均为运维侧（密钥轮换、沙箱联调、对账文件）而非代码缺陷。

## 2026-11-09 Outbox 推荐学习重试边界复核

- 支付成功后的口味偏好更新（`pref_update`）与推荐行为学习（`taste_learning`）使用独立 Outbox 事件，互不阻塞。
- 推荐学习异常必须向 Outbox worker 传播，进入指数退避重试；达到最大尝试次数后进入失败/死信告警，不得仅记录日志后把事件标记为 DONE。
- `tests/test_paid_side_effects_retry.py` 覆盖事件独立入队、偏好更新不再隐式触发推荐学习、推荐学习异常向 worker 传播。
- 此项为代码级回归防护；仍需以最新提交对应的 CI 结果作为验收证据。


### 2026-11-09 退款推荐学习独立重试边界复核

- 发现退款后的推荐负向学习原先嵌在 `pref_recompute` handler 中，并捕获所有异常只记录日志；Outbox worker 会误将该事件标记 DONE，造成退款推荐画像漏更新。
- 修复：退款成功事务同步写入独立 `taste_learning_refund` Outbox 事件；偏好重算与推荐学习分离。推荐学习 handler 不吞异常，由 Outbox 退避重试和死信机制负责恢复。
- `record_full_refund_events` 内部仍以订单 `payment_status=REFUNDED` 为门槛，部分退款事件安全 no-op；其 `order_refund:{order_id}:{product_id}` 事件 ID 保证重试幂等。
- 回归测试覆盖偏好 handler 不再隐式执行推荐学习，以及退款推荐学习异常必须传播。以该提交对应的 CI 为最终验收依据。


### 2026-11-09 POS 退款审批重复请求窗口

- 发现审批流程先提交 `PosRefundRequest=PROCESSING`，随后才创建渠道退款单并绑定 `refund_id`。在两步之间重复审批请求可能读到 `PROCESSING + refund_id=NULL`，旧逻辑会继续分配退款并可能重复调用渠道。
- 修复：`PROCESSING` 且尚无 `refund_id` 时直接返回当前申请，不再开始第二次执行。渠道请求前，`PaymentService.refund` 会先在同一事务中持久化退款单并绑定申请，因此此保护覆盖了真正的外部副作用窗口。
- 增加回归测试，确保该状态不触发退款分配或渠道调用。若进程在认领提交后、创建退款单前崩溃，申请会保持处理中并需后续运维恢复；此路径应由后续认领超时恢复任务处理，不能通过并发重试冒险重复退款。

## POS 退款审批执行权 fencing（2026-11-09）

- `pos_refund_requests.execution_epoch` 是数据库单调递增的执行代际；`execution_token` 是本次审批 worker 的随机 owner token；`execution_lease_until` 仅用于首笔 `PaymentRefund` 创建并绑定前的短租约。
- 新审批或过期租约接管时，在 POS 申请行锁内递增 epoch、生成新 token 并持久化租约。旧 worker 恢复后必须在 `PaymentService.refund` 的同一数据库事务中重新锁定 POS 申请，并同时匹配 token、epoch、状态及未绑定时的租约有效期；不匹配则拒绝创建退款单。
- 首笔退款单与 POS `refund_id` 在调用微信/支付宝之前同事务提交。绑定后租约清空，后续多支付方式分摊继续使用同一 token/epoch；若渠道状态仍为 PENDING/PROCESSING/UNKNOWN/ABNORMAL，不允许新 worker 再发起一笔。
- 旧数据 `PROCESSING + refund_id=NULL + execution_epoch=0` 不具备可证明的租约所有权，继续 fail-closed，不自动接管。必须先人工查渠道退款单/流水，确认未发起退款后再执行受控恢复。
- 如果进程在退款单绑定事务提交后、渠道请求发送前崩溃，数据库中的退款单仍是 PENDING；该状态不能证明请求未发送。必须通过渠道查单/对账恢复，不得仅凭超时重新创建退款单。
- 异常收尾先 rollback 并重新读取持久化退款事实：已绑定且仍在途的退款保留 POS PROCESSING；只有未创建/绑定退款单或渠道明确终态失败时才可收敛为 FAILED。旧 epoch worker 不得覆盖新 owner 的状态。
- 本机制的租约接管安全性依赖 MySQL 行锁与事务提交；Redis 锁仅用于缓存/worker 协调，不参与资金 fencing。


## 2026-11-09 退款聚合的 MySQL RR 快照复核

- 发现退款入口的锁序定位查询会先执行普通 SELECT；在 MySQL `REPEATABLE READ` 下，这可能提前建立事务快照。若随后等待 Order/Payment 行锁，普通 `SUM/COUNT` 仍可能读取锁等待前的旧快照，漏掉刚刚提交的退款状态。
- 修复：`_lock_order_then_payment` 与退款父行锁查询增加 `populate_existing=True`，确保锁定读刷新当前 Session 中的 ORM 对象；退款创建的在途/已成功金额改为 Order→Payment 锁之后对 `PaymentRefund` 执行 `FOR UPDATE` 当前读并在 Python 汇总。
- 退款成功收敛同样以当前锁定读汇总同一支付单退款事实；订单净已收在资金状态收敛路径使用 Payment 与 PaymentRefund 当前锁定读，避免旧快照误判部分/全额退款。进一步复核发现收款创建、现金收款和支付成功后的订单结清判断也依赖净已收金额，现一并在持有 Order 锁后启用当前读，并刷新锁定的 Order ORM 对象。
- 新增回归测试验证退款行查询、订单净已收聚合均使用 `FOR UPDATE + populate_existing`，并验证父行锁后刷新 Payment。
- 锁序保持 Order→Payment→PaymentRefund；不在持有退款行锁后再等待父行锁。普通展示用的 `refundable_amount` 仍是只读估算，最终退款金额由持锁的退款执行路径再次校验。

## 2026-11-09 即时收款返回与异步回调竞态复核

- 发现付款码即时收款在渠道 HTTP 返回后直接使用请求前 ORM 对象执行 `_settle_immediate_payment`；若异步 SUCCESS 回调先一步提交，旧对象仍可能是 PENDING，再次执行 SALE/订单收敛。
- 修复：即时收款与开发 mock 结算在渠道响应后重新按 Order→Payment 当前读锁定，并用 `populate_existing=True` 刷新订单和支付对象；若回调已将支付单置 PAID/PARTIAL_REFUND/REFUNDED，则即时路径直接退出，不重复执行资金副作用，也不把退款终态降级回 PAID。
- 即时渠道金额缺失/不匹配时，不再直接改写旧 ORM 对象。改为重新锁定并刷新当前支付单；仅在尚未 PAID/退款终态时转 UNKNOWN，写状态历史并先提交，再向调用方返回金额错误，避免请求异常回滚掉 UNKNOWN，也避免覆盖并发成功回调。
- 充值即时金额不匹配路径按 RechargeOrder→Payment 当前读锁定；两者任一已 PAID 时不做降级，状态分叉则 fail-closed 报人工核对；未支付时持久化 UNKNOWN 和状态历史。充值最终入账本身也移除锁前状态短路，并在 RechargeOrder→Payment 锁后刷新 ORM，再判断双边 PAID/终态，避免旧对象触发重复充值或把退款终态覆盖成 PAID。即时渠道返回的 `provider_trade_no` 也延后到持锁后的充值结算函数中写入，避免外部请求返回期间迟到回调已更新字段、旧对象再覆盖。
- 新增测试覆盖回调先成功时不降级、不重复记账，以及金额不匹配时 UNKNOWN 必须持久化。

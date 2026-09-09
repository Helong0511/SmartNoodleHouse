# 退款说明

退款分两条路径：持有 `payment:refund` 的管理端直连退款，以及收银员提交、店长审批的 POS 退款申请。

## 支付退款（管理端）

1. 校验订单 / 支付单可退余额。
2. 写入 `payment_refunds`（含 `idempotency_key`），调用微信 / 支付宝退款 API。
3. 异步结果：退款回调或调度轮询（`retry_count` / `next_retry_at` / `last_error`）。
4. 成功后回写订单支付状态、积分扣回、券回滚（按业务规则）、`financial_ledger` 记 REFUND。

## POS 退款申请

- 收银员对本班订单创建 `pos_refund_requests`（`PENDING`）。
- 店长 / 超管审批：通过则走真实退款；拒绝 / 取消更新状态。
- 组合支付按 tender 分摊；`executed_amount` 记录已成功金额。

## 安全注意

- 生产关闭 `ALLOW_PAYMENT_MOCK`。
- 退款回调与支付回调同样验签；可配置 `WECHAT_REFUND_NOTIFY_URL`（空则复用支付通知 URL）。
- 敏感操作写入 `operation_logs`。

## 相关配置（名称级）

- `WECHAT_REFUND_NOTIFY_URL` / `WECHAT_API_V3_KEY` / 平台证书
- `ALIPAY_*` 退款与验签公钥
- 权限码：`payment:refund`

详见 [payment.md](payment.md)。

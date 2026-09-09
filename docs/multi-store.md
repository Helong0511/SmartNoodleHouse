# 多门店

## 数据隔离模型

- `stores`：门店主数据（`store_no` 参与业务单号前缀）。
- 员工 `users.store_id`：非空则仅能操作该店；`NULL` 表示超管可跨店。
- 业务表普遍带 `store_id`（订单、支付班次、库存、摄像头、外卖映射等）。
- 桌台 `tables` 在店内唯一 `(store_id, table_no)`；扫码令牌 `qr_token` 全局唯一。

## 角色与门店

| 角色 | 典型 store_id | 能力摘要 |
|------|---------------|----------|
| SUPER_ADMIN | NULL | 全店；系统配置 |
| STORE_ADMIN | 本店 ID | 本店运营与退款 |
| CASHIER / KITCHEN / OPERATOR | 本店 ID | 按权限矩阵收敛 |

种子见 `scripts/seed_roles.py`、`scripts/seed_permissions.py` 与 `sql/002_seed_prod.sql`。

## 运营注意

- 新增门店：插入 `stores`，再配桌台、库存、外卖 `platform_store_bindings`、员工绑定。
- 顾客 `registered_store_id` / `registered_table_id` 仅记录首次扫码注册门店 / 桌台。
- 统计与财务分录（`financial_ledger.store_id`）按店过滤；跨店报表仅超管。
- 小程序 / H5 通过桌台或门店上下文解析 `store_id`，服务端必须以落库关联为准，勿信任客户端随意传店。

## 配置

门店级参数放 `store_settings`；全局参数放 `system_settings` 或环境变量。生产多实例时各实例共享同一库，用 `PROCESS_ROLE` 区分 leader / worker，而不是按店拆进程（除非刻意分库，当前版本按单库多店设计）。

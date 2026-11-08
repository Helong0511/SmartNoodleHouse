# Cwm9 数据库与迁移审计报告

> 审计日期：2026-09-29
> 审计范围：SQLAlchemy 模型、Alembic 迁移、sql/struct.sql 基线、索引、约束、生产数据安全
> 审计方式：模型/迁移代码走读 + 模型对齐测试 + 全套测试（791 passed, 8 skipped）

---

## 1. 技术栈与配置

- ORM：SQLAlchemy 2.x async（Mapped / mapped_column 类型注解风格）
- 驱动：aiomysql（生产 MySQL 8）、aiosqlite（测试 in-memory）
- 迁移：Alembic，配置 [backend/alembic.ini](file:///e:/SmartNoodleHouse/backend/alembic.ini)
- 迁移目录：[backend/alembic/versions/](file:///e:/SmartNoodleHouse/backend/alembic/versions/)，共 24 个版本文件
- 结构真源：[sql/struct.sql](file:///e:/SmartNoodleHouse/sql/struct.sql)，种子数据 [sql/data_sys.sql](file:///e:/SmartNoodleHouse/sql/data_sys.sql) + [sql/data_custom.sql](file:///e:/SmartNoodleHouse/sql/data_custom.sql)
- 字符集：utf8mb4 / utf8mb4_unicode_ci
- 时区：业务时间 Asia/Shanghai；DB 连接不依赖会话时区

---

## 2. 迁移版本链

| 版本 | 文件 | 主要内容 |
|---|---|---|
| 0001_baseline | [0001_baseline.py](file:///e:/SmartNoodleHouse/backend/alembic/versions/0001_baseline.py) | 从 sql/struct.sql 的 `BEGIN/END CANONICAL SCHEMA` 段读取 DDL，幂等忽略 1007/1050/1060/1061/1062/1022/1826 |
| 0002_p0_hardening | 0002 | P0 安全约束（回调唯一索引、状态机相关列） |
| 0003_p1_hardening | 0003 | P1 加固 |
| 0004_p1_shift_open | 0004 | POS 班次 |
| 0005_p1_merge_print | 0005 | 打印合并 |
| 0006_print_jobs | 0006 | PrintJob 表 + 幂等约束 + 状态索引 |
| 0007_prod_indexes | [0007_prod_indexes.py](file:///e:/SmartNoodleHouse/backend/alembic/versions/0007_prod_indexes.py) | 生产索引增量（先查 information_schema 再 ADD，防锁表/重复执行） |
| 0008_coupon_template_store | 0008 | 优惠券模板门店字段 |
| 0009_product_checks | 0009 | 餐品 CHECK 约束 |
| 0010_coupon_ledger_idempotency | 0010 | 券流水幂等键 |
| 0011_store_geo_option_scope | 0011 | 门店地理/规格作用域 |
| 0012_guest_member_dining_session | 0012 | 游客/会员/用餐会话 |
| 0013_customer_identity_phone_security | 0013 | 顾客身份与手机号安全（verified 标记、合并日志） |
| 0014_coupon_template_store_whitelist | 0014 | 券模板门店白名单 |
| 0015_delivery_menu_ops | 0015 | 外卖菜单运营 |
| 0016_audit_log_category_store | 0016 | 审计日志类别 + 门店索引 |
| 0017_financial_ledger_idempotency | 0017 | 财务台账幂等 |
| 0018_taste_recommendation | 0018 | 口味推荐 |
| 0019_three_way_alignment | 0019 | 模型/struct.sql/迁移三方对齐 |
| 0020_option_group_store_scope_not_null | 0020 | OptionGroup.store_id NOT NULL（先回填再加约束） |
| 0021_discount_scale_2 | 0021 | 折扣金额 scale=2 |
| 0022_category_schedule_auto_sort | 0022 | 分类时段自动排序 |
| 0023_store_logo | 0023 | 门店 Logo |
| 0024_order_milestone_rewards | 0024 | 成单里程碑奖励 |

---

## 3. Baseline 设计要点（0001）

### 3.1 仅执行 CANONICAL SCHEMA 段

[0001_baseline.py:44-52](file:///e:/SmartNoodleHouse/backend/alembic/versions/0001_baseline.py) 通过 `-- BEGIN CANONICAL SCHEMA` / `-- END CANONICAL SCHEMA` 标记裁剪 sql/struct.sql：

- **不执行** `DROP DATABASE` / `CREATE DATABASE`（避免迁移误删库）。
- **不执行** `CREATE USER` / `GRANT` / `IDENTIFIED BY`（DBA 职责，不进迁移）。
- **不执行** 生产种子数据（种子由 sql/data_sys.sql + sql/data_custom.sql 或应用 ensure_seeded 处理）。
- 测试断言：`CREATE TABLE` 在段内；`DROP DATABASE`、`IDENTIFIED BY` 不在段内（测试_生产级审计P0.py）。

### 3.2 幂等错误忽略

```python
_IGNORE_MYSQL_ERRNOS = {1007, 1050, 1060, 1061, 1062, 1022, 1826}
```

允许 baseline 在部分表已手工建过的库上重跑，不会因重复对象整体失败。

### 3.3 语句切分

`_split_sql_statements` 去掉整行注释后按分号切分，DDL 场景足够；不支持存储过程体（项目无存储过程）。

---

## 4. 主键与类型约定

### 4.1 BigInteger 主键

项目硬约定：新增表主键使用

```python
id: Mapped[int] = mapped_column(
    BigInteger().with_variant(Integer, "sqlite"),
    primary_key=True, autoincrement=True,
)
```

- 生产 MySQL 用 BIGINT，避免餐饮长期运营后订单/流水溢出 INT。
- 测试 SQLite 不支持 BIGINT autoincrement，variant 回退 Integer。
- 与 OutboxEvent / PrintJob 等既有表保持一致。

### 4.2 金额

- `Numeric(10, 2)`，应用层统一 `Decimal`，禁浮点。
- 0021 迁移把折扣字段统一为 scale=2。
- `money()` 工具函数规范化输入。

### 4.3 时间

- `DateTime(timezone=False)`，存 naive Asia/Shanghai 本地时间。
- 应用层 `now_local()` 统一生成；定时任务调度器时区 Asia/Shanghai。
- 避免 MySQL TIMESTAMP 2038 问题与 UTC 转换歧义。

### 4.4 字符串

- 业务编号 `String(32/64)`，唯一索引。
- 状态 `String(16/32)`，应用层枚举常量，不用 DB ENUM（便于迁移）。
- 长文本/JSON 使用 `Text` / `JSON`。

---

## 5. 关键约束与索引

### 5.1 资金安全约束

| 表 | 约束 | 防护 |
|---|---|---|
| PaymentCallbackReceipt | UNIQUE(provider, receipt_key) | 回调重复入账 |
| Payment | UNIQUE(payment_no)、UNIQUE(transaction_id)（部分） | 重复支付单 |
| PaymentRefund | UNIQUE(refund_no)、幂等键 NOT NULL | 并发超额退款 |
| PaymentLedger | UNIQUE(payment_id, entry_type) | 台账重复 |
| Order | UNIQUE(dining_type, table_id, idempotency_key) | 重复下单 |
| PosRefundRequest | 状态 + reviewed_by 索引 | 审批查询 |

### 5.2 券/库存约束

| 表 | 约束 |
|---|---|
| CouponTemplate | remaining 非负 CHECK；client_request_id 唯一索引（发放幂等） |
| CustomerCoupon | UNIQUE(customer_id, template_id, 发放批次) |
| Stock | qty 非负 CHECK；原子 UPDATE WHERE qty >= n 防超卖 |
| StockMovement | 流水只追加，不更新 |

### 5.3 多门店约束

- OptionGroup.store_id NOT NULL（0020 先回填再加 NOT NULL）。
- PlatformStoreBinding UNIQUE(platform, platform_shop_id)。
- DeliveryOrderMapping UNIQUE(platform, platform_order_id)。
- 审计日志、打印任务、厨房工单均有 store_id 索引。

### 5.4 生产索引（0007）

- 增量 ADD INDEX，先查 `information_schema.statistics` 判断是否已存在。
- 避免单次 migration 长时间锁表；所有新索引在低峰期执行。
- 覆盖高频查询：订单(store_id, status, created_at)、支付状态、券状态、打印任务状态。

### 5.5 外键

- 核心资金表使用外键 + ON DELETE 限制（防误删顾客/订单导致孤儿流水）。
- 高频写入表（审计日志、Outbox 事件）不加外键，靠应用层一致性，减少锁竞争。
- 所有外键显式命名，便于回滚。

---

## 6. 三方对齐机制

### 6.1 三个真源

1. SQLAlchemy 模型（[backend/app/models/__init__.py](file:///e:/SmartNoodleHouse/backend/app/models/__init__.py)）
2. sql/struct.sql（baseline DDL 真源）
3. Alembic 迁移链（增量演进）

### 6.2 对齐迁移

- 0019_three_way_alignment 专门修复三者漂移。
- 0020 对 OptionGroup.store_id 回填后加 NOT NULL。
- [测试_模型对齐.py](file:///e:/SmartNoodleHouse/tests/测试_模型对齐.py) 自动比对模型 metadata 与 struct.sql 段。
- CI 应在每次模型变更时跑该测试（当前本地通过）。

### 6.3 新增字段流程

约定（来自项目工程实践）：

1. 改 SQLAlchemy 模型。
2. 同步更新 sql/struct.sql（新部署用）。
3. 新建 Alembic 迁移（已部署库升级用）。
4. 跑测试_模型对齐.py 验证三方一致。
5. 大表加索引走 0007 风格的 information_schema 检查。

---

## 7. 数据安全

### 7.1 连接

- 生产 DATABASE_URL 禁止 sqlite/localhost/127.0.0.1（startup_checks）。
- 账号最小权限：sql/struct.sql 中应用账号仅授权本库 DML + 必要 DDL，不含 GRANT OPTION。
- 部署口令占位符 `REPLACE_WITH_STRONG_DB_PASSWORD`，不得提交真实密码（本次审计已将 struct.sql 中残留的真实样式口令替换为占位符）。

### 7.2 连接池

[config.py](file:///e:/SmartNoodleHouse/backend/app/core/config.py)：
- `pool_size` + `max_overflow` 显式配置，注释标明 gunicorn -w × pool 必须 ≤ MySQL max_connections。
- `pool_recycle` 防止 wait_timeout 断连。
- `pool_pre_ping=True` 剔除失效连接。

### 7.3 PII 脱敏

- [pii_cleanup_service.py](file:///e:/SmartNoodleHouse/backend/app/services/pii_cleanup_service.py)：完成 30 天以上订单的手机号/地址替换为不可逆占位。
- 定时任务夜间执行，分批处理，不锁全表。

### 7.4 软删除

- 核心业务对象（订单/支付/顾客）不物理删除，使用状态字段。
- 餐品/分类可停用，保留历史订单引用完整性。
- 顾客合并走 CustomerMergeLog，不删除源顾客。

---

## 8. 迁移执行与回滚

### 8.1 升级

- `alembic upgrade head` 顺序执行。
- baseline 幂等；增量迁移通过唯一约束/information_schema 检查保证可重入。
- 启动时应用不自动跑迁移，由部署脚本显式执行，避免多 worker 并发升级。

### 8.2 回滚

- 每个迁移提供 `downgrade()`，但**资金/状态机相关迁移回滚需人工评审**。
- baseline 不可逆（drop_all 风险），文档明确说明。
- 生产回滚策略：备份 → 在备库验证 downgrade → 维护窗口执行。

### 8.3 离线比对

- 运维可用 `alembic upgrade head --sql` 生成离线 SQL，与生产 schema 对比后再执行（BLK-003）。

---

## 9. 测试覆盖

| 主题 | 测试文件 |
|---|---|
| 模型/struct.sql 对齐 | 测试_模型对齐.py、测试_生产级审计P0.py（baseline 段裁剪） |
| 迁移设置 | 测试_业务设置数据库.py、测试_设置并发.py |
| 结构校验 | 审计_三方结构对齐.py、审计_数据库全面检查.py、校验_审计结构修复.py |
| 资金约束 | 测试_支付退款.py、测试_财务台账.py、测试_订单幂等范围.py |
| 券/库存约束 | 测试_券并发.py、测试_并发库存.py、测试_外卖防超卖镜像.py |
| 多门店字段 | 测试_跨门店规格.py、测试_门店隔离防护.py |
| 推荐模型 | 测试_智能推荐_01模型迁移.py |

测试均使用 SQLite in-memory + 模型 `create_all`，保证表结构/约束可建；MySQL 特有语法（如 SKIP LOCKED）通过条件判断或服务层 mock 覆盖。

---

## 10. 已知限制

1. **无在线 DDL 工具集成**：当前大表加索引依赖 MySQL 8 在线 DDL，超大型表建议运维使用 pt-online-schema-change / gh-ost（REMAINING_ISSUES P2）。
2. **无自动分库分表**：单库设计，适合当前连锁规模；未来门店数/订单量显著增长时需评估分库分表或归档。
3. **历史订单归档**：30 天脱敏但不归档到冷库，长期会影响查询；建议增加年度归档 job。
4. **测试与 MySQL 方言差异**：SQLite 不支持部分 MySQL 约束（如部分 CHECK、SKIP LOCKED），相关路径靠服务层单测覆盖，未做真实 MySQL CI（建议未来加 MySQL service container）。

---

## 11. 结论

数据库设计遵守"结构真源 + 增量迁移 + 三方对齐"原则，资金/幂等/多门店关键约束齐全，主键类型与金额类型统一。baseline 安全裁剪 DDL（不删库不授权），生产索引增量可重入。模型对齐测试与全套业务测试通过，未发现缺失关键约束或迁移不可逆的高危问题。遗留项为运维侧（在线 DDL、归档、MySQL CI）而非设计缺陷。

---

## 2026-11-08 数据库安全与文档一致性复核

新一轮审查发现生产部署文档的初始化命令可能对已有数据库执行 DROP DATABASE 与 TRUNCATE。struct.sql 仅用于全新空库初始化；data_sys.sql 含破坏性种子重置；生产已有库只允许使用 Alembic 迁移。README 与部署指南已在文档修订分支改写，仍需对 SQL 脚本增加显式确认/目标库保护，并以隔离 MySQL 8 演练验证。

本机 compileall 通过不代表数据库迁移已验证；SQLite 测试不能代替 MySQL 行锁、SKIP LOCKED、索引、外键和 DDL 测试。2026-11-08 审查的具体风险和关闭要求见 [FULL_CODE_REVIEW_2026-11-08.md](FULL_CODE_REVIEW_2026-11-08.md)。


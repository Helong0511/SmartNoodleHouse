# Cwm9 项目基线与仓库地图

> 历史基线扫描：2026-09-29；生产配置与发布控制定点复核：2026-11-06。不含密钥值。
> 本文档的目录/模块地图仍可作导航，但启动校验和部署行为以当前 `backend/app/core/startup_checks.py`、`redis.conf.example`、`scripts/生产部署-ubuntu.txt` 及最新 CI 为准；不得依据历史审计表格假设代码状态。

---

## 1. 仓库顶层结构

| 路径 | 角色 | 说明 |
|---|---|---|
| `backend/` | Python FastAPI 后端 | API、服务层、模型、迁移、定时任务、Worker |
| `frontend/` | Vue3 + Vite 管理后台 | TypeScript + Element Plus（依赖锁定） |
| `miniprogram-wechat/` | 微信小程序 | 点餐、支付、会员、桌台码 |
| `miniprogram-alipay/` | 支付宝小程序 | 同上，平台差异独立实现 |
| `camera/` | 人脸识别摄像头模块 | 独立 Python 包，被后端 import |
| `deploy/` | 部署脚本 | Docker / systemd / nginx 模板 |
| `nginx/` | 反向代理配置 | 限流、WebSocket、上传大小 |
| `systemd/` | 进程管理 | gunicorn/uvicorn 服务单元 |
| `sql/` | SQL 脚本 | 初始化、索引、对账 |
| `tests/` | 测试目录 | 所有测试均在此目录，禁止散落 |
| `scripts/` | 运维脚本 | 初始化、迁移、密钥生成 |
| `uploads/` | 本地上传 | 开发环境直出，生产应转 S3 |
| `logs/` | 日志根 | backend/frontend/wechat/alipay 子目录 |
| `docs/` | 文档目录 | 架构、安全、支付、隔离、审计等 |
| `.env.example`/`.env.development.example`/`.env.production.example` | 配置模板 | 仅占位符，不得含真实密钥 |
| `.env` | 真实配置 | 已 `.gitignore`，不入仓库 |

---

## 2. 后端技术栈与入口

### 2.1 启动入口
- ASGI 入口：[backend/app/main.py](file:///e:/SmartNoodleHouse/backend/app/main.py) `create_app()` → `app`
- 启动校验：[backend/app/core/startup_checks.py](file:///e:/SmartNoodleHouse/backend/app/core/startup_checks.py) `validate_settings()`
- 路径修正：[backend/app/bootstrap_path.py](file:///e:/SmartNoodleHouse/backend/app/bootstrap_path.py)
- 部署命令（参考 `deploy/`、`systemd/`、`sc.bat`）

### 2.2 技术栈（基于 [backend/requirements.txt](file:///e:/SmartNoodleHouse/backend/requirements.txt) 与代码 import）
- Python 3.11+ / FastAPI / Starlette
- SQLAlchemy 2.x（async）+ aiomysql
- Alembic（迁移，25 个版本文件）
- pydantic v2 + pydantic-settings
- python-jose（JWT HS256，强制 aud/iss/typ/ver）
- passlib[bcrypt]
- apscheduler（AsyncIO，Asia/Shanghai 时区）
- Redis（aioredis / redis-py async）：限流、Leader 锁、活体挑战、Pub/Sub
- cryptography / Fernet（人脸向量加密）
- httpx（外部 HTTP 调用）
- Pillow（图片处理 + decompression bomb 防护）
- uvicorn / gunicorn

### 2.3 中间件链（按注册顺序，外层 → 内层）
1. `JsonGZipMiddleware` / `PerformanceMetricsMiddleware`（性能计时 + GZip）
2. `WebhookAllowlistMiddleware`（IP 白名单 + 反代信任）
3. `RateLimitMiddleware`（限流）
4. `RequestIDMiddleware`（request_id / trace_id）
5. `intrusion_detection_middleware`（SQL 注入 / 路径遍历 / 命令注入 / 漏洞探针 → 403 + audit）
6. `security_headers_middleware`（HSTS / X-Frame-Options / nosniff / Referrer-Policy / Permissions-Policy；Webhook 413 限体）
7. `CORSMiddleware`（凭配置 `CORS_ORIGINS`）

### 2.4 异常处理
- `AppException` → `app_exception_handler`
- `HTTPException` → `http_exception_handler`
- `IntegrityError` → `sqlalchemy_integrity_handler`
- `OperationalError` → `sqlalchemy_operational_handler`

### 2.5 静态资源
- 开发：`/uploads` → `StaticFiles(directory=upload_dir)`
- 生产：建议 `STORAGE_BACKEND=s3`，启动校验会拒绝 s3 未配齐

---

## 3. 后端 API 路由（[backend/app/api/v1/__init__.py](file:///e:/SmartNoodleHouse/backend/app/api/v1/__init__.py)）

| 路由模块 | 文件 | 主要职责 |
|---|---|---|
| health | [health.py](file:///e:/SmartNoodleHouse/backend/app/api/v1/health.py) | /api/health、/api/ready（DB+Redis 探针） |
| auth | [auth.py](file:///e:/SmartNoodleHouse/backend/app/api/v1/auth.py) | 员工登录、退出、刷新 |
| users | [users.py](file:///e:/SmartNoodleHouse/backend/app/api/v1/users.py) | 员工/角色/权限 |
| products | [products.py](file:///e:/SmartNoodleHouse/backend/app/api/v1/products.py) | 菜品/分类/规格/库存（公开 GET 限流） |
| orders | [orders.py](file:///e:/SmartNoodleHouse/backend/app/api/v1/orders.py) | 订单创建/查询/取消/状态机 |
| payments | [payments.py](file:///e:/SmartNoodleHouse/backend/app/api/v1/payments.py) | 微信/支付宝支付、回调、退款 |
| wallet | [wallet.py](file:///e:/SmartNoodleHouse/backend/app/api/v1/wallet.py) | 会员余额/积分 |
| financial_ledger | [financial_ledger.py](file:///e:/SmartNoodleHouse/backend/app/api/v1/financial_ledger.py) | 财务台账 |
| print_jobs | [print_jobs.py](file:///e:/SmartNoodleHouse/backend/app/api/v1/print_jobs.py) | 打印任务 |
| pos | [pos.py](file:///e:/SmartNoodleHouse/backend/app/api/v1/pos.py) | 收银点餐/班次/退款/抹零 |
| customers | [customers.py](file:///e:/SmartNoodleHouse/backend/app/api/v1/customers.py) | 顾客/会员/合并 |
| member_levels | [member_levels.py](file:///e:/SmartNoodleHouse/backend/app/api/v1/member_levels.py) | 会员等级 |
| coupons | [coupons.py](file:///e:/SmartNoodleHouse/backend/app/api/v1/coupons.py) | 优惠券模板/发放/核销 |
| tables | [tables.py](file:///e:/SmartNoodleHouse/backend/app/api/v1/tables.py) | 桌台/qr_token |
| cameras | [cameras.py](file:///e:/SmartNoodleHouse/backend/app/api/v1/cameras.py) | 摄像头/人脸/迎宾 |
| welcome | [welcome.py](file:///e:/SmartNoodleHouse/backend/app/api/v1/welcome.py) | 迎宾语音 |
| dashboard | [dashboard.py](file:///e:/SmartNoodleHouse/backend/app/api/v1/dashboard.py) | 仪表盘 |
| miniprogram | [miniprogram.py](file:///e:/SmartNoodleHouse/backend/app/api/v1/miniprogram.py) | 微信/支付宝小程序统一入口 |
| delivery | [delivery.py](file:///e:/SmartNoodleHouse/backend/app/api/v1/delivery.py) | 美团/饿了么外卖 |
| settings | [settings.py](file:///e:/SmartNoodleHouse/backend/app/api/v1/settings.py) | 业务参数/门店设置 |
| client_logs | [client_logs.py](file:///e:/SmartNoodleHouse/backend/app/api/v1/client_logs.py) | 前端日志上报 |
| websocket | [websocket.py](file:///e:/SmartNoodleHouse/backend/app/api/v1/websocket.py) | KDS/叫号屏实时推送 |
| audit_logs | [audit_logs.py](file:///e:/SmartNoodleHouse/backend/app/api/v1/audit_logs.py) | 操作审计日志 |
| ab_experiments | [ab_experiments.py](file:///e:/SmartNoodleHouse/backend/app/api/v1/ab_experiments.py) | A/B 实验 |
| performance | [performance.py](file:///e:/SmartNoodleHouse/backend/app/api/v1/performance.py) | 性能指标查询 |

### 3.1 认证依赖（[backend/app/api/deps.py](file:///e:/SmartNoodleHouse/backend/app/api/deps.py)）
- `CurrentUser` = 员工 JWT（typ=staff，强制 ver 校验 token_version 撤销机制）
- `OptionalStaffUser` = 可选员工身份（公开餐品列表用）
- `CurrentCustomer` = 顾客 JWT（typ=customer，独立 TTL，强制 ver 校验）
- `OptionalCustomer` = 公开接口顾客身份（冷启动）
- `StaffOrCustomer` = 三端共用接口（拒绝 stream 短票据混用）
- `require_permission(code)` / `require_kitchen()` / `require_pos()` 装饰器

### 3.2 门店隔离（[backend/app/services/store_scope.py](file:///e:/SmartNoodleHouse/backend/app/services/store_scope.py)）
- `require_bound_store(user, store_id)` → `effective_store`（非超管强制覆盖）
- `assert_store_access(user, store_id)` → 单条记录归属校验
- `assert_order_store_access(user, order)` → 订单归属校验
- 顾客端：`assert_customer_owns_order(order, customer_id)`
- 检查清单：[docs/门店隔离检查清单.md](file:///e:/SmartNoodleHouse/docs/门店隔离检查清单.md)

---

## 4. 后端服务层（[backend/app/services/](file:///e:/SmartNoodleHouse/backend/app/services/)）

按业务域分组：

### 4.1 订单与支付
- [order_service.py](file:///e:/SmartNoodleHouse/backend/app/services/order_service.py) — 订单 CRUD
- [order_state_machine.py](file:///e:/SmartNoodleHouse/backend/app/services/order_state_machine.py) — 状态转换
- [order_lifecycle.py](file:///e:/SmartNoodleHouse/backend/app/services/order_lifecycle.py) — 生命周期/超时
- [order_access.py](file:///e:/SmartNoodleHouse/backend/app/services/order_access.py) — 订单归属
- [payment_service.py](file:///e:/SmartNoodleHouse/backend/app/services/payment_service.py) — 支付/退款主流程（115KB，最大单文件）
- [payment_verify.py](file:///e:/SmartNoodleHouse/backend/app/services/payment_verify.py) — 验签
- [payment_barcode.py](file:///e:/SmartNoodleHouse/backend/app/services/payment_barcode.py) / [barcode_pay_service.py](file:///e:/SmartNoodleHouse/backend/app/services/barcode_pay_service.py) — 付款码支付
- [wechat_pay_client.py](file:///e:/SmartNoodleHouse/backend/app/services/wechat_pay_client.py) / [wechat_pay_v2_client.py](file:///e:/SmartNoodleHouse/backend/app/services/wechat_pay_v2_client.py) / [wechat_mini_client.py](file:///e:/SmartNoodleHouse/backend/app/services/wechat_mini_client.py)
- [alipay_client.py](file:///e:/SmartNoodleHouse/backend/app/services/alipay_client.py) / [alipay_mini_client.py](file:///e:/SmartNoodleHouse/backend/app/services/alipay_mini_client.py)
- [paid_side_effects.py](file:///e:/SmartNoodleHouse/backend/app/services/paid_side_effects.py) — 支付成功副作用（券/积分/厨房/打印）
- [financial_ledger_service.py](file:///e:/SmartNoodleHouse/backend/app/services/financial_ledger_service.py) — 财务台账

### 4.2 顾客与会员
- [customer_service.py](file:///e:/SmartNoodleHouse/backend/app/services/customer_service.py) — 顾客/订单创建（78KB）
- [customer_merge_service.py](file:///e:/SmartNoodleHouse/backend/app/services/customer_merge_service.py) — 手机号合并
- [balance_service.py](file:///e:/SmartNoodleHouse/backend/app/services/balance_service.py) — 余额
- [miniprogram_auth.py](file:///e:/SmartNoodleHouse/backend/app/services/miniprogram_auth.py) — 小程序登录
- [dining_session_service.py](file:///e:/SmartNoodleHouse/backend/app/services/dining_session_service.py) — 桌台会话

### 4.3 优惠券与积分
- [coupon_service.py](file:///e:/SmartNoodleHouse/backend/app/services/coupon_service.py) — 券模板/发放/核销
- [pos_refund_service.py](file:///e:/SmartNoodleHouse/backend/app/services/pos_refund_service.py) — POS 退款
- [pos_shift_service.py](file:///e:/SmartNoodleHouse/backend/app/services/pos_shift_service.py) — 班次

### 4.4 菜单与规格
- [menu_service.py](file:///e:/SmartNoodleHouse/backend/app/services/menu_service.py) / [menu_copy_service.py](file:///e:/SmartNoodleHouse/backend/app/services/menu_copy_service.py)
- [option_code.py](file:///e:/SmartNoodleHouse/backend/app/services/option_code.py) — 规格编码
- [category_require.py](file:///e:/SmartNoodleHouse/backend/app/services/category_require.py) / [category_schedule.py](file:///e:/SmartNoodleHouse/backend/app/services/category_schedule.py) / [category_schedule_auto.py](file:///e:/SmartNoodleHouse/backend/app/services/category_schedule_auto.py)
- [stock_service.py](file:///e:/SmartNoodleHouse/backend/app/services/stock_service.py) — 库存

### 4.5 外卖平台
- [delivery_service.py](file:///e:/SmartNoodleHouse/backend/app/services/delivery_service.py) — 主服务（39KB）
- [delivery_providers.py](file:///e:/SmartNoodleHouse/backend/app/services/delivery_providers.py) — 美团/饿了么适配（35KB）
- [delivery_sign.py](file:///e:/SmartNoodleHouse/backend/app/services/delivery_sign.py) — 验签
- [delivery_context.py](file:///e:/SmartNoodleHouse/backend/app/services/delivery_context.py) / [delivery_events.py](file:///e:/SmartNoodleHouse/backend/app/services/delivery_events.py) / [delivery_sync_handlers.py](file:///e:/SmartNoodleHouse/backend/app/services/delivery_sync_handlers.py) / [delivery_tasks.py](file:///e:/SmartNoodleHouse/backend/app/services/delivery_tasks.py)
- [platform_token_service.py](file:///e:/SmartNoodleHouse/backend/app/services/platform_token_service.py) — 平台 Token 加密存储

### 4.6 厨房与打印
- [kitchen_claim.py](file:///e:/SmartNoodleHouse/backend/app/services/kitchen_claim.py) / [kitchen_auto_service.py](file:///e:/SmartNoodleHouse/backend/app/services/kitchen_auto_service.py)
- [print_service.py](file:///e:/SmartNoodleHouse/backend/app/services/print_service.py) — 打印主服务（39KB）
- [print_tasks.py](file:///e:/SmartNoodleHouse/backend/app/services/print_tasks.py) / [escpos_client.py](file:///e:/SmartNoodleHouse/backend/app/services/escpos_client.py) / [xpyun_client.py](file:///e:/SmartNoodleHouse/backend/app/services/xpyun_client.py)

### 4.7 人脸与摄像头
- [face_service.py](file:///e:/SmartNoodleHouse/backend/app/services/face_service.py) — 识别/迎宾（25KB）
- [face_extractor.py](file:///e:/SmartNoodleHouse/backend/app/services/face_extractor.py) / [face_liveness.py](file:///e:/SmartNoodleHouse/backend/app/services/face_liveness.py) / [face_liveness_service.py](file:///e:/SmartNoodleHouse/backend/app/services/face_liveness_service.py)
- [camera_credentials.py](file:///e:/SmartNoodleHouse/backend/app/services/camera_credentials.py) — 摄像头凭证
- [privacy_service.py](file:///e:/SmartNoodleHouse/backend/app/services/privacy_service.py) / [pii_cleanup_service.py](file:///e:/SmartNoodleHouse/backend/app/services/pii_cleanup_service.py)

### 4.8 后台任务与可靠性
- [outbox_worker.py](file:///e:/SmartNoodleHouse/backend/app/services/outbox_worker.py) — Outbox 事件处理（13KB）
- [webhook_inbox.py](file:///e:/SmartNoodleHouse/backend/app/services/webhook_inbox.py) / [webhook_replay.py](file:///e:/SmartNoodleHouse/backend/app/services/webhook_replay.py)
- [night_batch_service.py](file:///e:/SmartNoodleHouse/backend/app/services/night_batch_service.py) — 夜间批处理
- [scheduler_admin.py](file:///e:/SmartNoodleHouse/backend/app/services/scheduler_admin.py) — 调度管理
- [secret_encryption.py](file:///e:/SmartNoodleHouse/backend/app/services/secret_encryption.py) — Fernet 密钥派生

### 4.9 其他
- [recommendation_service.py](file:///e:/SmartNoodleHouse/backend/app/services/recommendation_service.py) / [taste_profile_service.py](file:///e:/SmartNoodleHouse/backend/app/services/taste_profile_service.py) / [taste_learning_service.py](file:///e:/SmartNoodleHouse/backend/app/services/taste_learning_service.py) / [reco_cache.py](file:///e:/SmartNoodleHouse/backend/app/services/reco_cache.py) / [behavior_event_service.py](file:///e:/SmartNoodleHouse/backend/app/services/behavior_event_service.py)
- [revenue_stats.py](file:///e:/SmartNoodleHouse/backend/app/services/revenue_stats.py) / [feedback_service.py](file:///e:/SmartNoodleHouse/backend/app/services/feedback_service.py) / [qr_links.py](file:///e:/SmartNoodleHouse/backend/app/services/qr_links.py) / [table_resolve.py](file:///e:/SmartNoodleHouse/backend/app/services/table_resolve.py) / [store_hours_service.py](file:///e:/SmartNoodleHouse/backend/app/services/store_hours_service.py) / [system_settings_service.py](file:///e:/SmartNoodleHouse/backend/app/services/system_settings_service.py) / [weather_client.py](file:///e:/SmartNoodleHouse/backend/app/services/weather_client.py) / [websocket_manager.py](file:///e:/SmartNoodleHouse/backend/app/services/websocket_manager.py) / [ws_pubsub.py](file:///e:/SmartNoodleHouse/backend/app/services/ws_pubsub.py) / [ws_conn_quota.py](file:///e:/SmartNoodleHouse/backend/app/services/ws_conn_quota.py) / [object_storage.py](file:///e:/SmartNoodleHouse/backend/app/services/object_storage.py) / [file_log_service.py](file:///e:/SmartNoodleHouse/backend/app/services/file_log_service.py) / [customer_display.py](file:///e:/SmartNoodleHouse/backend/app/services/customer_display.py) / [status_history.py](file:///e:/SmartNoodleHouse/backend/app/services/status_history.py) / [ab_service.py](file:///e:/SmartNoodleHouse/backend/app/services/ab_service.py) / [audit_service.py](file:///e:/SmartNoodleHouse/backend/app/services/audit_service.py) / [barcode_pay_service.py](file:///e:/SmartNoodleHouse/backend/app/services/barcode_pay_service.py) / [preference_scene.py](file:///e:/SmartNoodleHouse/backend/app/services/preference_scene.py) / [product_sync_service.py](file:///e:/SmartNoodleHouse/backend/app/services/product_sync_service.py)

### 4.10 领域服务（[backend/app/domains/](file:///e:/SmartNoodleHouse/backend/app/domains/)）
按 DDD 分包：`auth/`、`messaging/`、`order/`、`product/`、`user/`

---

## 5. 核心模型（[backend/app/models/__init__.py](file:///e:/SmartNoodleHouse/backend/app/models/__init__.py)）

聚合根（72KB 单文件，统一导出）：

- **认证**：User、Role、Permission、RolePermission、UserApiKey
- **顾客**：Customer、CustomerChannel（多平台身份）、CustomerMergeLog
- **门店**：Store、StoreHours、StoreSettings
- **菜单**：Category、Product、ProductImage、OptionGroup、OptionValue、ProductOption
- **订单**：Order、OrderItem、OrderItemOption、OrderStatusHistory、DiningSession、Table
- **支付**：Payment、PaymentRefund、PaymentLedger、PaymentReceipt
- **优惠券**：CouponTemplate、Coupon、CouponLock
- **会员**：MemberLevel、MemberBalanceChange、MemberPointChange
- **库存**：Stock、StockMovement
- **外卖**：PlatformStoreBinding、DeliveryOrderMapping、DeliverySyncLog
- **厨房/打印**：KitchenTicket、KitchenTicketItem、PrintJob
- **人脸**：FaceRecord、FaceEvent、VisitorCapture、Camera、CameraAuth
- **审计/任务**：AuditLog、OperationLog、OutboxEvent、WebhookInbox、AbExperiment、SystemSetting

---

## 6. 数据库迁移

- Alembic 配置：[backend/alembic.ini](file:///e:/SmartNoodleHouse/backend/alembic.ini)
- 迁移目录：[backend/alembic/versions/](file:///e:/SmartNoodleHouse/backend/alembic/versions/)（25 个版本文件）
- 数据库设计文档：[docs/数据库设计.md](file:///e:/SmartNoodleHouse/docs/数据库设计.md)

---

## 7. 配置与秘密管理基线

### 7.1 配置入口
- [backend/app/core/config.py](file:///e:/SmartNoodleHouse/backend/app/core/config.py) `Settings(BaseSettings)`
- env_file 候选：项目根 `.env` → `backend/.env` → 当前目录 `.env`

### 7.2 启动校验覆盖项（[startup_checks.py](file:///e:/SmartNoodleHouse/backend/app/core/startup_checks.py)）

| 类别 | 校验项 | 生产拒绝启动 | 开发警告 |
|---|---|---|---|
| APP_ENV | 非法值 | ✓ | - |
| JWT_SECRET | 空/弱/默认/熵低/长度<32 | ✓ | ✓ |
| JWT_ALGORITHM | 非 HS256 | ✓ | ✓ |
| FACE_ENCRYPTION_KEY | 空/弱/默认/长度<32 | ✓ | ✓ |
| APP_DEBUG=true | 生产 | ✓ | - |
| ALLOW_PAYMENT_MOCK / ALLOW_AUTH_MOCK / ALLOW_DELIVERY_MOCK / ALLOW_PRINT_MOCK / ALLOW_OPENCV_FACE_FALLBACK | 生产 | ✓ | - |
| ENABLE_RATE_LIMIT=false | 生产 | ✓ | - |
| SECRET_ENCRYPTION_KEY | 生产缺失或 <32 | ✓ | - |
| DISPLAY_SCREEN_TOKEN | 已开叫号屏但缺 token | ✓ | - |
| CORS_ORIGINS | localhost/127.0.0.1 | ✓ | - |
| H5_PUBLIC_BASE_URL | 空/localhost | ✓ | - |
| STORAGE_BACKEND=s3 | 未配齐 | ✓ | - |
| WEBHOOK_IP_ALLOWLIST | `WEBHOOK_REQUIRE_ALLOWLIST=true` 但缺少有效白名单 | ✓ | - |
| DATABASE_URL | sqlite；loopback 未显式 opt-in | ✓ | - |
| REDIS_URL | 无效 scheme/无密码；loopback 未显式 opt-in | ✓ | - |
| RATE_LIMIT_FAIL_CLOSED_PATHS | 缺少生产必需敏感路径 | ✓ | - |
| PROCESS_ROLE | 非 leader/worker/auto | ✓ | - |
| 微信支付 | 占位符/缺平台证书 | ✓ | - |
| 支付宝 | 占位符 | ✓ | - |
| Notify URL | 占位符 | ✓ | - |
| 至少一种支付通道 | 微信或支付宝 | ✓ | - |
| 打印 | 开 XPYUN 但未配齐 | ✓ | - |

### 7.3 秘密隔离
- `JWT_SECRET` — 会话签名
- `FACE_ENCRYPTION_KEY` / `FACE_ENCRYPTION_KEY_PREVIOUS` — 人脸向量加密（轮换）
- `SECRET_ENCRYPTION_KEY` — 平台 Token 等业务密钥加密（生产独立）
- `WECHAT_API_V3_KEY` / `WECHAT_API_V2_KEY` — V2/V3 独立密钥（不共用）
- `WECHAT_PLATFORM_PUBLIC_KEY` / `WECHAT_PLATFORM_CERT_PATH` — 平台证书
- `.env` / `.env.development` / `.env.production` 已 `.gitignore`
- `.env.example` 等仅含 `replace-with-*` / `USER:PASSWORD` 占位符
- 安全说明：[docs/安全说明.md](file:///e:/SmartNoodleHouse/docs/安全说明.md)

---

## 8. 前端 Vue 后台基线

- 入口：[frontend/src/main.ts](file:///e:/SmartNoodleHouse/frontend/src/main.ts) → [App.vue](file:///e:/SmartNoodleHouse/frontend/src/App.vue)
- 目录：`api/`（封装）、`components/`、`layouts/`、`router/`、`stores/`、`styles/`、`utils/`、`views/`
- 构建：[frontend/vite.config.ts](file:///e:/SmartNoodleHouse/frontend/vite.config.ts) + [tsconfig.json](file:///e:/SmartNoodleHouse/frontend/tsconfig.json)
- 依赖锁定：[frontend/package-lock.json](file:///e:/SmartNoodleHouse/frontend/package-lock.json)

---

## 9. 小程序基线

### 9.1 微信小程序 [miniprogram-wechat/](file:///e:/SmartNoodleHouse/miniprogram-wechat/)
- 平台：微信小程序原生
- 后端入口：[miniprogram.py](file:///e:/SmartNoodleHouse/backend/app/api/v1/miniprogram.py)

### 9.2 支付宝小程序 [miniprogram-alipay/](file:///e:/SmartNoodleHouse/miniprogram-alipay/)
- 平台：支付宝小程序原生
- 平台差异由后端 `miniprogram.py` 按 `channel` 分流

### 9.3 上架检查清单
- [docs/小程序上架检查清单.md](file:///e:/SmartNoodleHouse/docs/小程序上架检查清单.md)

---

## 10. 部署基线

- Docker / systemd / nginx 模板：[deploy/](file:///e:/SmartNoodleHouse/deploy/)、[systemd/](file:///e:/SmartNoodleHouse/systemd/)、[nginx/](file:///e:/SmartNoodleHouse/nginx/)
- 部署说明：[docs/部署说明.md](file:///e:/SmartNoodleHouse/docs/部署说明.md)
- Leader 锁机制（Redis Lua 原子 compare-and-delete/expire）确保多 worker 单调度
- TTS 队列：`asyncio.Queue(maxsize=100)` 有界
- 摄像头操作仅 Leader worker

---

## 11. 测试基线

### 11.1 测试目录
- 所有测试在 [tests/](file:///e:/SmartNoodleHouse/tests/)（项目硬约束，禁止散落）
- 配置：[pytest.ini](file:///e:/SmartNoodleHouse/pytest.ini) + [tests/conftest.py](file:///e:/SmartNoodleHouse/tests/conftest.py)
- 沙箱脚手架：[tests/测试_沙箱脚手架.py](file:///e:/SmartNoodleHouse/tests/测试_沙箱脚手架.py)

### 11.2 已覆盖测试（按主题归类，130+ 文件）
- **P0 安全**：测试_安全P0/P1/加固第二轮/P0资金JWT隔离/摄像头SSRF与活体/客户端IP与退款防护
- **支付/退款**：测试_支付/支付Webhook/支付同步/支付退款/支付宝JSAPI支付/支付宝密钥/多支付方式退款/付款码支付/POS退款/退款扣积分实时/退款等级降级实时
- **订单**：测试_订单/订单幂等范围/订单生命周期/订单渠道伪造/订单续单服务/订单详情规格批量/订单访问安全/订单创建券/业务单号/已支付禁取消/免烹饪订单/POS订单/POS拆单支付/POS加菜与抹零/POS班次/成单里程碑/我的订单续单
- **多门店隔离**：测试_多门店桌台/多门店续单/多门店角色隔离/多租户并发隔离/跨门店餐品/跨门店规格/门店隔离防护/门店Logo/门店编辑权限/门店范围/注册门店
- **优惠券/积分/会员**：测试_券并发/券批量发放/券服务/券模板门店/券顾客搜索/会员积分/会员等级管理/会员折扣/会员头像上传/渠道档案与手机号合并/手机号仅官方授权/身份手机号支付加固/游客会员状态/顾客合并/顾客微信OpenID
- **库存/外卖**：测试_外卖/外卖凭证范围/外卖营业时间/外卖退款门店锁/外卖防超卖镜像/并发库存/客户端IP信任代理
- **打印/KDS**：测试_打印任务/打印任务API/打印服务/启动打印防护/厨房认领/厨房自动完成
- **人脸/摄像头**：测试_摄像头SSRF与活体/摄像头密码严格模式/人脸活体接线/迎宾
- **小程序**：测试_小程序/小程序桌台登录/支付宝小程序客户端/支付宝Mock登录/小程序实时冒烟
- **数据库/迁移**：测试_模型对齐/智能推荐_01模型迁移/设置并发/业务设置数据库/校验_审计结构修复/检查数据库/检查默认列
- **推荐/偏好**：测试_智能推荐_02~13/偏好评分/偏好场景与规格范围/推荐系统性能基准/推荐系统离线评估
- **审计/权限**：测试_审计日志/审计日志可见范围/权限/用户创建/认证登录/认证锁定与日志/P0审计跟进/P0未支付进厨房修复/P2认领修复/生产级审计P0/客户端IP与退款防护
- **后台/性能**：测试_分页稳定性/筛选条件/夜间批处理/定时任务管理/定时任务跨进程锁/Leader锁原子性/Outbox工作者/消息幂等与域模块/Webhook收件箱回收/PII清理/财务台账/营收统计/仪表盘统计/健康检查就绪/安全加固回归/加固第二轮/限流关闭失败/生产种子与平台/模拟端到端/实时冒烟/应用_*（种子数据）
- **基础设施**：测试_二维码链接/对象存储/文件日志服务/日期时间本地/Logo叠加/单品数量上限/餐品分页与台账/餐品主图/餐品图片URL/餐品分页与台账/分类必选/分类时段/分类时段自动排序/规格/规格编码/规格默认值/解析桌台Token/菜单超量/菜单复制/查询规格默认值/桌台/桌台订单解析/顾客叫号屏

### 11.3 审计/冒烟脚本
- 审计_三方结构对齐.py、审计_数据库全面检查.py、校验_审计结构修复.py
- 模拟端到端.py、实时冒烟.py、小程序实时冒烟.py

---

## 12. 已有审计文档（避免重复劳动）

- [docs/数据隔离修复.md](file:///e:/SmartNoodleHouse/docs/数据隔离修复.md) — 多门店隔离加固方案
- [docs/门店隔离检查清单.md](file:///e:/SmartNoodleHouse/docs/门店隔离检查清单.md) — API 隔离门禁
- [docs/安全说明.md](file:///e:/SmartNoodleHouse/docs/安全说明.md) — 密钥轮换清单
- [docs/支付对接.md](file:///e:/SmartNoodleHouse/docs/支付对接.md)
- [docs/退款流程.md](file:///e:/SmartNoodleHouse/docs/退款流程.md)
- [docs/厨房KDS与取餐叫号.md](file:///e:/SmartNoodleHouse/docs/厨房KDS与取餐叫号.md)
- [docs/人脸识别.md](file:///e:/SmartNoodleHouse/docs/人脸识别.md)
- [docs/多门店管理.md](file:///e:/SmartNoodleHouse/docs/多门店管理.md)
- [docs/外卖对接.md](file:///e:/SmartNoodleHouse/docs/外卖对接.md)
- [docs/数据库设计.md](file:///e:/SmartNoodleHouse/docs/数据库设计.md)
- [docs/部署说明.md](file:///e:/SmartNoodleHouse/docs/部署说明.md)
- [docs/架构设计.md](file:///e:/SmartNoodleHouse/docs/架构设计.md)
- [docs/故障排查.md](file:///e:/SmartNoodleHouse/docs/故障排查.md)

---

## 13. 项目硬约束与已落实的工程约定（来自项目记忆）

- **所有测试代码必须放在 `tests/` 目录**，禁止其他目录新建测试文件
- **列表页分页必须真分页**，排序加 `id` 兜底防翻页重复/丢行，分页 sizes 统一 `[20,50,100]`，页末删空自动回退
- **筛选条件联动**：门店变更→清空并重新加载依赖门店的筛选项；其余筛选条件变更即回第 1 页
- **工具栏表单样式**：使用后代选择器 `.toolbar .el-form-item`，不用并列选择器
- **操作日志**：通过 `AuditService.log_staff/log_intrusion/log_danger` 记录，敏感字段自动脱敏
- **危险请求中间件**：检测 SQL 注入/路径遍历/命令注入/漏洞探针
- **Outbox 事件**：enqueue/claim/stale reclaim/process 指数退避
- **Leader 锁**：Lua 脚本原子 compare-and-delete/compare-and-expire
- **PII 清理**：30 天以上已完成订单脱敏
- **H5 401**：区分顾客/员工，清除对应 token
- **订单幂等键**：包含 `dining_type` 和 `table_id`
- **PrintJob PROCESSING**：超阈值回收
- **TTS 队列**：`asyncio.Queue(maxsize=100)` 有界
- **新表主键**：`BigInteger().with_variant(Integer,"sqlite")` 与 OutboxEvent/PrintJob 对齐
- **JWT**：HS256 强制 aud/iss/typ/ver，token_version 撤销
- **顾客 JWT**：独立 TTL，typ=customer 不可冒充 staff
- **微信支付回调**：校验 appid + 证书序列号
- **退款金额解析失败**：硬拒绝，不警告
- **退款幂等键**：必填
- **Payment callback**：Redis 锁释放需考虑 DB receipt 是否存在
- **REFUND_PENDING**：必须有解决路径
- **CONFIRMED 订单收到成功支付**：安全 fallback 改 PAID
- **超时关单**：调用支付平台 trade_cancel/close 防晚到账
- **桌台 qr_token**：不可远程枚举获取永久凭证
- **PrintJob**：DB 幂等约束防重复打印
- **购物车**：按门店隔离 storage key
- **Camera URL**：阻断 metadata 端点、loopback、link-local
- **Face liveness**：随机动作挑战 + Redis token 防重放
- **Camera 操作**：仅 Leader worker
- **Payment callback**：拒绝 mchid/app_id 缺失或不匹配
- **Face 注册**：仅允许 active 状态会员
- **Webhook 请求头**：脱敏 Authorization/Cookie/token
- **生产环境**：DATABASE_URL/REDIS_URL 必须有效且使用最小权限凭证；单机 loopback 仅在 `ALLOW_LOOPBACK_PRODUCTION_SERVICES=true` 时允许，Redis 必须配置密码。生产 Redis 需显式 `maxmemory` + `noeviction`，并按主机内存预算调整示例值。
- **Webhook**：512KB body 限制 + 413
- **Face 图片**：10MB + 最小尺寸防 decompression bomb
- **订单状态机**：禁止 PENDING_PAYMENT → CONFIRMED
- **KDS**：不显示 PENDING_PAYMENT 订单与"接单制作"按钮

---

## 14. 无法立即确认事项（待人工/外部凭证）

1. **真实生产密钥/证书**：`.env`/证书文件不入仓库，需生产运维确认是否已轮换（仓库历史曾出现微信 AppSecret / 支付宝应用私钥 / AES Key，视为已泄露，须平台轮换）
2. **微信/支付宝/美团/饿了么沙箱联调**：本地无法访问官方沙箱时，相关适配层使用 mock/契约测试，真实联调需人工用官方凭证验证
3. **生产数据库结构对比**：本地 MySQL schema 与生产差异需运维用 `alembic upgrade head --sql` 离线比对
4. **生产监控告警接入**：Prometheus/Grafana/异常上报通道需运维确认
5. **小程序上架审核**：需小程序账号持有人提交审核

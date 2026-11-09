> **当前状态补充（2026-11-08）**：PR #113 已 squash 合并至 main，合并提交 442beb4761b29f5b415830e91e06c97241f74ef3；该 PR 的 12/12 CI 检查在合并记录中已确认通过。此结论只覆盖该提交/PR 的 CI，不等于外部平台和生产环境验收。后续本机 Windows 验证结果见 [TEST_REPORT.md](TEST_REPORT.md)。问题关闭仍须有当前 commit 的代码证据和对应测试；历史台账中的“已修复”不可自动视为当前重新验证。
>
> ---
>
# Cwm9 项目问题台账与修复状态

> 基于 2026-09-29 真实仓库代码审计。本台账每项均给出真实文件路径/函数名与实际状态，未完成事项绝不标记为通过。
> 维护规则：每项修复后必须更新本台账状态与测试关联；不得跳过测试或放宽断言以宣称通过。

---

## 优先级图例

- **P0**：资金安全、认证绕过、跨租户越权、敏感数据泄露、重大数据破坏
- **P1**：订单/库存/账务一致性、核心业务中断、支付退款错误
- **P2**：重要功能缺陷、性能、可维护性、异常恢复
- **P3**：体验、文档、非关键优化

## 状态图例

- **已修复+测试通过**：代码已落地、对应测试存在且通过
- **已修复+测试待跑**：代码已落地，待运行测试套件
- **待修复**：确认真实问题，未落地
- **阻塞**：需外部凭证/沙箱/人工
- **不适用**：经核实非问题

---

## 阶段一：基线与 P0

| 编号 | 模块 | 优先级 | 当前代码证据 | 触发条件/影响 | 修复方案 | 关联测试 | 状态 |
|---|---|---|---|---|---|---|---|
| P0-001 | 数据库/Redis 生产默认配置 | P0 | 默认配置仅供开发；生产启动校验检查 DATABASE_URL/REDIS_URL、凭证和 loopback 策略 | 生产误用 root/弱口令、未认证 Redis 或错误开放本机依赖 | 生产须使用最小权限 MySQL 用户、Redis 强密码/ACL；loopback 仅允许 `ALLOW_LOOPBACK_PRODUCTION_SERVICES=true` 显式 opt-in，并确保依赖只绑定 loopback、端口不对公网开放；`.env` 已 gitignore | 测试_生产种子与平台.py、生产配置测试 | 已修复+测试通过（生产环境凭证仍需人工配置） |
| P0-002 | 启动校验覆盖 | P0 | [startup_checks.py:48-249](file:///e:/SmartNoodleHouse/backend/app/core/startup_checks.py) 覆盖 JWT/FACE/SECRET/MOCK/CORS/H5/STORAGE/WEBHOOK/DB/REDIS/PROCESS_ROLE/支付/打印 | 缺失任何关键配置时生产静默启动 | 13 类校验，errors→SystemExit(1) | 测试_启动打印防护.py | 已修复+测试通过 |
| P0-003 | JWT 安全 | P0 | [security.py:22-76](file:///e:/SmartNoodleHouse/backend/app/core/security.py) `_JWT_RESERVED_CLAIMS` 防伪造 aud/iss/typ/ver；[deps.py:38-39,98-99,164-165](file:///e:/SmartNoodleHouse/backend/app/api/deps.py) `ver` 校验 `token_version` 撤销 | 客户端伪造 sub/role 提权 | 保留声明不可被 extra 覆盖；撤销机制 | 测试_P0资金JWT隔离.py | 已修复+测试通过 |
| P0-004 | 顾客 JWT 独立 TTL | P0 | [security.py:103-135](file:///e:/SmartNoodleHouse/backend/app/core/security.py) `create_customer_token` typ=customer 独立 TTL；[deps.py:90-101](file:///e:/SmartNoodleHouse/backend/app/api/deps.py) 拒绝非 customer typ | 顾客令牌冒充员工 | 强制 typ 校验 + token_version | 测试_P0资金JWT隔离.py | 已修复+测试通过 |
| P0-005 | 微信支付回调验签 | P0 | [payment_verify.py:54-118](file:///e:/SmartNoodleHouse/backend/app/services/payment_verify.py) RSA2/SHA256 平台公钥验签；时间戳 300s 偏差；商户号校验；[payment_service.py:1486-1617](file:///e:/SmartNoodleHouse/backend/app/services/payment_service.py) | 伪造回调确认收款 | 验签 + appid/mchid 校验 + DB 唯一约束 | 测试_支付Webhook.py | 已修复+测试通过 |
| P0-006 | 支付宝回调验签 | P0 | [payment_verify.py:130-186](file:///e:/SmartNoodleHouse/backend/app/services/payment_verify.py) RSA2 + app_id 校验 + sign/sign_type 移除 | 伪造支付宝回调 | 验签 + app_id 校验 | 测试_支付宝JSAPI支付.py | 已修复+测试通过 |
| P0-007 | 回调幂等 DB 约束 | P0 | [models/__init__.py:1016-1024](file:///e:/SmartNoodleHouse/backend/app/models/__init__.py) `PaymentCallbackReceipt` `UniqueConstraint("provider", "receipt_key")`；[payment_service.py:1486-1617](file:///e:/SmartNoodleHouse/backend/app/services/payment_service.py) Redis `claim_once` + DB 唯一约束 | 重复回调重复记账/发券/积分 | DB 唯一约束 + Redis claim | 测试_支付Webhook.py | 已修复+测试通过 |
| P0-008 | 订单状态机 | P0 | [order_state_machine.py:10-83](file:///e:/SmartNoodleHouse/backend/app/services/order_state_machine.py) STAFF/PAYMENT/SYSTEM 三类转换矩阵；禁止 PENDING_PAYMENT→CONFIRMED；禁止人工 PAID/REFUNDED/CANCELLED | 任意接口直接写状态 | 三类转换 + assert 函数 | 测试_P0未支付进厨房修复.py | 已修复+测试通过 |
| P0-009 | REFUND_PENDING 解决路径 | P0 | [order_state_machine.py:18,40](file:///e:/SmartNoodleHouse/backend/app/services/order_state_machine.py) REFUND_PENDING 可去 REFUNDED/PAID/CONFIRMED/PREPARING/COOKING/READY/COMPLETED | 退款卡死中间态 | 多路径解决 | 测试_支付退款.py | 已修复+测试通过 |
| P0-010 | CONFIRMED+成功支付兜底 PAID | P0 | [order_state_machine.py:26-29](file:///e:/SmartNoodleHouse/backend/app/services/order_state_machine.py) `PAYMENT_TRANSITIONS["CONFIRMED"]={"PAID"}` | 异常推进后回调无法入账 | 兜底 PAID | 测试_P0未支付进厨房修复.py | 已修复+测试通过 |
| P0-011 | KDS 不显示 PENDING_PAYMENT | P0 | [kitchen_auto_service.py:29-97](file:///e:/SmartNoodleHouse/backend/app/services/kitchen_auto_service.py) 按支付状态/管道筛选 | 未支付单进厨房 | 服务端筛选 | 测试_P0未支付进厨房修复.py | 已修复+测试通过 |
| P0-012 | 超时关单调用平台 close | P0 | [order_lifecycle.py:35-154](file:///e:/SmartNoodleHouse/backend/app/services/order_lifecycle.py) 超时关单调用 trade_cancel/close | 晚到账资金风险 | 平台 close | 测试_订单生命周期.py | 已修复+测试通过 |
| P0-013 | 桌台 qr_token 不可枚举 | P0 | [miniprogram.py:285-362](file:///e:/SmartNoodleHouse/backend/app/api/v1/miniprogram.py) 扫码 qr_token 校验桌台一致；[qr_links.py](file:///e:/SmartNoodleHouse/backend/app/services/qr_links.py) | 远程枚举拿永久凭证 | qr_token 校验 | 测试_二维码链接.py | 已修复+测试通过 |
| P0-014 | 购物车按门店隔离 | P0 | 购物车 storage key 含 store_id | 跨店混单 | key 含 store_id | 测试_多门店续单.py | 已修复+测试通过 |
| P0-015 | 摄像头 URL SSRF 防护 | P0 | 摄像头 URL 阻断 metadata/loopback/link-local | SSRF 内网探测 | URL 阻断 | 测试_摄像头SSRF与活体.py | 已修复+测试通过 |
| P0-016 | Face 活体随机挑战 | P0 | [face_liveness.py](file:///e:/SmartNoodleHouse/backend/app/services/face_liveness.py) 随机动作挑战 + Redis token 防重放 | 照片/视频重放 | 随机 challenge + token | 测试_人脸活体接线.py | 已修复+测试通过 |
| P0-017 | Camera 仅 Leader 操作 | P0 | [main.py:170-172](file:///e:/SmartNoodleHouse/backend/app/main.py) `CameraManager.set_leader_check` | 多 worker 重复采集 | Leader 锁回调 | 测试_Leader锁原子性.py | 已修复+测试通过 |
| P0-018 | 退款金额解析失败硬拒 | P0 | 退款金额解析失败 raise，不 warning | 退款金额错误降级 | 硬拒绝 | 测试_POS退款.py | 已修复+测试通过 |
| P0-019 | 退款幂等键必填 | P0 | 退款单号 DB 唯一约束 + 幂等键必填 | 并发退款超额 | DB 唯一约束 | 测试_支付退款.py | 已修复+测试通过 |
| P0-020 | Webhook 413 限体 | P0 | [main.py:333-343](file:///e:/SmartNoodleHouse/backend/app/main.py) 512KB 限制 + 413 | 大 body DoS | 中间件拦截 | 测试_安全P0.py | 已修复+测试通过 |
| P0-021 | Webhook 请求头脱敏 | P0 | Authorization/Cookie/token 字段脱敏 | 日志泄露 token | 头部脱敏 | 测试_审计日志.py | 已修复+测试通过 |
| P0-022 | Face 注册仅 active 会员 | P0 | [face_service.py:187-249](file:///e:/SmartNoodleHouse/backend/app/services/face_service.py) 仅 active 状态会员 | 禁用会员继续注册 | 状态校验 | 测试_摄像头SSRF与活体.py | 已修复+测试通过 |
| P0-023 | Payment callback mchid/appid 校验 | P0 | [payment_service.py:1486-1617](file:///e:/SmartNoodleHouse/backend/app/services/payment_service.py) | 商户/应用伪造 | mchid/appid 校验 | 测试_支付Webhook.py | 已修复+测试通过 |
| P0-024 | Face 图片 decompression bomb | P0 | Pillow 10MB + 最小尺寸 | 恶意图片炸服务 | Pillow 限制 | 测试_摄像头SSRF与活体.py | 已修复+测试通过 |

## 阶段二：数据库与核心业务

| 编号 | 模块 | 优先级 | 当前代码证据 | 修复方案 | 关联测试 | 状态 |
|---|---|---|---|---|---|---|
| P1-101 | 库存原子扣减 | P1 | [stock_service.py:84-168](file:///e:/SmartNoodleHouse/backend/app/services/stock_service.py) `UPDATE ... WHERE` 条件约束 | 原子扣减 + DB 约束 | 测试_并发库存.py | 已修复+测试通过 |
| P1-102 | 库存流水记录 | P1 | [stock_service.py:17-57](file:///e:/SmartNoodleHouse/backend/app/services/stock_service.py) OutboxEvent 镜像事件 | 流水 + Outbox 重试 | 测试_外卖防超卖镜像.py | 已修复+测试通过 |
| P1-103 | 优惠券并发原子发放 | P1 | `UPDATE ... WHERE remaining >= qty` + `client_request_id` UNIQUE | 原子扣减 + 唯一键 | 测试_券并发.py | 已修复+测试通过 |
| P1-104 | 支付副作用 Outbox | P1 | [paid_side_effects.py](file:///e:/SmartNoodleHouse/backend/app/services/paid_side_effects.py) DB transactional outbox | 幂等处理副作用 | 测试_消息幂等与域模块.py | 已修复+测试通过 |
| P1-105 | Webhook 收件箱回收 | P1 | [webhook_inbox.py](file:///e:/SmartNoodleHouse/backend/app/services/webhook_inbox.py) 5 分钟 stale 阈值 | PROCESSING 回收 | 测试_Webhook收件箱回收.py | 已修复+测试通过 |
| P1-106 | PrintJob 幂等约束 | P1 | `PrintJob` DB 唯一约束防重复打印 | DB 唯一约束 | 测试_打印任务.py | 已修复+测试通过 |
| P1-107 | PrintJob PROCESSING 回收 | P1 | PROCESSING 超阈值回收 | 防卡死 | 测试_打印任务.py | 已修复+测试通过 |
| P1-108 | 退款本地失败 Outbox 重试 | P1 | 退款成功本地失败 → enqueue 积分扣减/偏好重算 | Outbox 异步重试 | 测试_退款扣积分实时.py | 已修复+测试通过 |
| P1-109 | 多门店 OptionGroup store_id 校验 | P1 | `calculate_items_batch` 强制 OptionGroup.store_id 与订单门店一致 | 防跨店规格 | 测试_跨门店规格.py | 已修复+测试通过 |
| P1-110 | 客户端 IP 信任代理 | P1 | [ip_allowlist.py](file:///e:/SmartNoodleHouse/backend/app/core/ip_allowlist.py) 仅信任 TRUSTED_PROXY_IPS | 防 X-Forwarded-For 伪造 | 测试_客户端IP信任代理.py | 已修复+测试通过 |
| P1-111 | PII 30 天脱敏 | P1 | [pii_cleanup_service.py](file:///e:/SmartNoodleHouse/backend/app/services/pii_cleanup_service.py) | 历史订单脱敏 | 测试_PII清理.py | 已修复+测试通过 |
| P1-112 | H5 401 区分顾客/员工 | P1 | [frontend/src/api/index.ts:87-153](file:///e:/SmartNoodleHouse/frontend/src/api/index.ts) 按 URL/X-Client 区分 | 清对应 token | 测试_小程序.py | 已修复+测试通过 |
| P1-113 | 订单幂等键含 dining_type+table_id | P1 | [orders.py:64-113](file:///e:/SmartNoodleHouse/backend/app/api/v1/orders.py) `idempotency_key` 必填 + DB UNIQUE | 防重复下单 | 测试_订单幂等范围.py | 已修复+测试通过 |
| P1-114 | 失败原因写入正确列 | P1 | `fail_reason` 列正确 | 防误写 | 测试_支付退款.py | 已修复+测试通过 |
| P1-115 | POS 订单幂等键强制 | P1 | POS 创建订单 idempotency_key 必填 | 防重复 POS 单 | 测试_POS订单.py | 已修复+测试通过 |

## 阶段三：客户端

| 编号 | 模块 | 优先级 | 当前代码证据 | 修复方案 | 关联测试 | 状态 |
|---|---|---|---|---|---|---|
| P1-201 | 微信 code2session 服务端验证 | P1 | [miniprogram_auth.py:63-96](file:///e:/SmartNoodleHouse/backend/app/services/miniprogram_auth.py) 调 `sns/jscode2session`；[miniprogram.py:490-537](file:///e:/SmartNoodleHouse/backend/app/api/v1/miniprogram.py) | 不信任客户端 openid | 测试_小程序桌台登录.py | 已修复+测试通过 |
| P1-202 | 支付宝 auth_code 服务端验证 | P1 | [miniprogram_auth.py:99-156](file:///e:/SmartNoodleHouse/backend/app/services/miniprogram_auth.py) 调 `alipay.system.oauth.token`；优先 `open_id` | 不信任客户端 user_id | 测试_支付宝Mock登录.py | 已修复+测试通过 |
| P1-203 | 下单 channel 由 JWT 强制 | P1 | [orders.py:64-113](file:///e:/SmartNoodleHouse/backend/app/api/v1/orders.py) channel 由 JWT 决定 | 客户端不可伪造 channel | 测试_订单渠道伪造.py | 已修复+测试通过 |
| P1-204 | 金额由后端重新计算 | P1 | 后端 calculate_items_batch 重算 | 客户端金额仅展示 | 测试_订单幂等范围.py | 已修复+测试通过 |
| P1-205 | Vue 路由守卫 | P1 | [router/index.ts:78-138](file:///e:/SmartNoodleHouse/frontend/src/router/index.ts) meta.permission/anyPermission/denyCashier | 前端体验层 | 测试_权限.py | 已修复+测试通过 |
| P1-206 | Axios 拦截器 401 处理 | P1 | [api/index.ts:87-153](file:///e:/SmartNoodleHouse/frontend/src/api/index.ts) 401 区分 staff/customer 清 token | 跳登录/扫码 | 测试_小程序.py | 已修复+测试通过 |
| P1-207 | 登出清空状态 | P1 | [stores/auth.ts:59-88](file:///e:/SmartNoodleHouse/frontend/src/stores/auth.ts) logout 清 token/user/session | 切账号/门店清理 | 测试_认证登录.py | 已修复+测试通过 |

## 阶段四：人脸、外卖、KDS、打印

| 编号 | 模块 | 优先级 | 当前代码证据 | 修复方案 | 关联测试 | 状态 |
|---|---|---|---|---|---|---|
| P1-301 | 人脸功能开关拆分 | P1 | [face_service.py](file:///e:/SmartNoodleHouse/backend/app/services/face_service.py) 多开关分离 | 启用一项不自动启其他 | 测试_摄像头SSRF与活体.py | 已修复+测试通过 |
| P1-302 | 访客保留期清理任务 | P1 | [pii_cleanup_service.py](file:///e:/SmartNoodleHouse/backend/app/services/pii_cleanup_service.py) 真执行清理 | 不只是配置项 | 测试_PII清理.py | 已修复+测试通过 |
| P1-303 | 加密 Fernet + nonce 防重 | P1 | [secret_encryption.py](file:///e:/SmartNoodleHouse/backend/app/services/secret_encryption.py) Fernet + AAD | nonce 重用拒绝 | 测试_P0资金JWT隔离.py | 已修复+测试通过 |
| P1-304 | 密钥轮换旧密钥解密 | P1 | [config.py:117-120](file:///e:/SmartNoodleHouse/backend/app/core/config.py) `face_encryption_key_previous` | 旧密文可解密 | 测试_P0资金JWT隔离.py | 已修复+测试通过 |
| P1-305 | 摄像头凭证加密存储 | P1 | [camera_credentials.py](file:///e:/SmartNoodleHouse/backend/app/services/camera_credentials.py) + [platform_token_service.py](file:///e:/SmartNoodleHouse/backend/app/services/platform_token_service.py) | 凭证不暴露客户端 | 测试_摄像头密码严格模式.py | 已修复+测试通过 |
| P1-306 | 外卖平台店铺绑定 | P1 | [delivery_service.py:63-87](file:///e:/SmartNoodleHouse/backend/app/services/delivery_service.py) 唯一绑定 + 凭证 | 唯一可审计绑定 | 测试_外卖凭证范围.py | 已修复+测试通过 |
| P1-307 | 外卖回调验签 | P1 | [delivery_sign.py](file:///e:/SmartNoodleHouse/backend/app/services/delivery_sign.py) + [delivery_service.py:63-87](file:///e:/SmartNoodleHouse/backend/app/services/delivery_service.py) | 验签 + 事件 ID 防重放 | 测试_外卖.py | 已修复+测试通过 |
| P1-308 | 外卖订单号唯一约束 | P1 | 外部订单号平台维度唯一约束 | 重复推送幂等 | 测试_外卖防超卖镜像.py | 已修复+测试通过 |
| P1-309 | 未知外卖状态不默认成功 | P1 | 显式状态映射 | 未知不默认成功 | 测试_外卖.py | 已修复+测试通过 |
| P1-310 | 外卖同步 Outbox 重试 | P1 | [stock_service.py:17-57](file:///e:/SmartNoodleHouse/backend/app/services/stock_service.py) + [delivery_sync_handlers.py](file:///e:/SmartNoodleHouse/backend/app/services/delivery_sync_handlers.py) | 失败不静默丢失 | 测试_外卖防超卖镜像.py | 已修复+测试通过 |
| P1-311 | KDS 任务生成幂等 | P1 | [paid_side_effects.py](file:///e:/SmartNoodleHouse/backend/app/services/paid_side_effects.py) Outbox 幂等 | 回调重试不重复生成 | 测试_厨房认领.py | 已修复+测试通过 |
| P1-312 | KDS WebSocket 断线补拉 | P1 | [websocket_manager.py](file:///e:/SmartNoodleHouse/backend/app/services/websocket_manager.py) 服务端可靠补拉 | 不依赖内存事件 | 测试_顾客叫号屏.py | 已修复+测试通过 |
| P1-313 | KDS 超时自动出餐 | P1 | [kitchen_auto_service.py:29-97](file:///e:/SmartNoodleHouse/backend/app/services/kitchen_auto_service.py) | 防卡制作中 | 测试_厨房自动完成.py | 已修复+测试通过 |
| P1-314 | 打印 ESC/GS 注入防护 | P1 | [print_service.py:121-157](file:///e:/SmartNoodleHouse/backend/app/services/print_service.py) 控制字符清理/转义 | 防打印注入 | 测试_打印服务.py | 已修复+测试通过 |
| P1-315 | 打印任务唯一 ID | P1 | [print_tasks.py](file:///e:/SmartNoodleHouse/backend/app/services/print_tasks.py) 唯一任务 ID + 幂等 + 状态 | 防重复出单 | 测试_打印任务.py | 已修复+测试通过 |

## 阶段五：性能、部署、最终验收

| 编号 | 模块 | 优先级 | 当前代码证据 | 修复方案 | 关联测试 | 状态 |
|---|---|---|---|---|---|---|
| P1-401 | 连接池上限核算 | P1 | [config.py:33-35](file:///e:/SmartNoodleHouse/backend/app/core/config.py) pool_size+max_overflow 注释 gunicorn -w 总连接 | 防 worker×pool 耗尽 DB | 测试_设置并发.py | 已修复+测试通过 |
| P1-402 | TTS 队列有界 | P1 | `asyncio.Queue(maxsize=100)` | 防 TTS 队列爆 | 测试_迎宾.py | 已修复+测试通过 |
| P1-403 | 危险请求检测中间件 | P1 | [intrusion_detection.py](file:///e:/SmartNoodleHouse/backend/app/core/intrusion_detection.py) SQL/路径/命令/探针 | 防攻击 | 测试_安全加固回归.py | 已修复+测试通过 |
| P1-404 | Security headers | P1 | [main.py:330-351](file:///e:/SmartNoodleHouse/backend/app/main.py) HSTS/X-Frame-Options/nosniff/Referrer/Permissions | 防点击劫持 | 测试_安全P1.py | 已修复+测试通过 |
| P1-405 | 生产关闭 docs/redoc/openapi | P1 | [main.py:313-315](file:///e:/SmartNoodleHouse/backend/app/main.py) is_development 才开启 | 防接口泄露 | 测试_启动打印防护.py | 已修复+测试通过 |
| P1-406 | 限流中间件 | P1 | [rate_limit.py](file:///e:/SmartNoodleHouse/backend/app/core/rate_limit.py) + [main.py:386](file:///e:/SmartNoodleHouse/backend/app/main.py) | 防 API 滥用 | 测试_限流关闭失败.py | 已修复+测试通过 |
| P1-407 | WebSocket 连接配额 | P1 | [ws_conn_quota.py](file:///e:/SmartNoodleHouse/backend/app/services/ws_conn_quota.py) 连接数/订阅频率限制 | 防 WS DoS | 测试_顾客叫号屏.py | 已修复+测试通过 |
| P1-408 | Outbox 指数退避 | P1 | [outbox_worker.py](file:///e:/SmartNoodleHouse/backend/app/services/outbox_worker.py) enqueue/claim/stale reclaim | 失败可重试 | 测试_Outbox工作者.py | 已修复+测试通过 |
| P1-409 | Leader 锁 Lua 原子 | P1 | [leader_lock.py](file:///e:/SmartNoodleHouse/backend/app/core/leader_lock.py) compare-and-delete/expire | 防多 Leader | 测试_Leader锁原子性.py | 已修复+测试通过 |
| P1-410 | 定时任务跨进程锁 | P1 | [main.py:99-122](file:///e:/SmartNoodleHouse/backend/app/main.py) + scheduler coalesce/max_instances=1 | 防重复执行 | 测试_定时任务跨进程锁.py | 已修复+测试通过 |
| P1-411 | 跨日营业/时区 | P1 | [main.py:38-45](file:///e:/SmartNoodleHouse/backend/app/main.py) Asia/Shanghai + 夜间批处理 | 防 UTC 星期错 | 测试_夜间批处理.py | 已修复+测试通过 |
| P1-412 | 列表分页稳定性 | P1 | 真 pagination + id 兜底 + sizes=[20,50,100] + 页末删空回退 | 防翻页重复/丢行 | 测试_分页稳定性.py | 已修复+测试通过 |
| P1-413 | 筛选条件联动 | P1 | 门店变更→清空依赖项；其余→回第1页 | 防混查 | 测试_筛选条件.py | 已修复+测试通过 |
| P2-501 | 推荐算法可关闭/重置 | P2 | [recommendation_service.py](file:///e:/SmartNoodleHouse/backend/app/services/recommendation_service.py) | 可解释/可关闭 | 测试_智能推荐_08推荐接口.py | 已修复+测试通过 |
| P2-502 | 推荐冷启动 fallback | P2 | 新用户回退逻辑 | 防无推荐 | 测试_智能推荐_07混合推荐.py | 已修复+测试通过 |
| P2-503 | 推荐时区/星期 | P2 | 按门店/用户 IANA 时区 | 防 UTC 星期错 | 测试_智能推荐_12性能治理.py | 已修复+测试通过 |
| P2-504 | 推荐隐私合规 | P2 | [privacy_service.py](file:///e:/SmartNoodleHouse/backend/app/services/privacy_service.py) | 防未授权画像 | 测试_智能推荐_13隐私合规.py | 已修复+测试通过 |
| P2-505 | 数据库迁移基线 | P2 | [alembic/versions/0001_baseline.py:85-106](file:///e:/SmartNoodleHouse/backend/alembic/versions/0001_baseline.py) 从 sql/struct.sql 读取 DDL | 不可逆有说明 | 测试_模型对齐.py | 已修复+测试通过 |
| P2-506 | 生产索引增量迁移 | P2 | [alembic/versions/0007_prod_indexes.py:35-68](file:///e:/SmartNoodleHouse/backend/alembic/versions/0007_prod_indexes.py) 检查已存在后增量加 | 防锁表 | 测试_模型对齐.py | 已修复+测试通过 |

---

## 阻塞项（需外部凭证/沙箱/人工）

| 编号 | 事项 | 阻塞原因 | 建议下一步 |
|---|---|---|---|
| BLK-001 | 微信/支付宝/美团/饿了么沙箱真实联调 | 本地无官方沙箱访问 | 运维用真实凭证在沙箱环境跑测试_支付宝JSAPI支付.py、测试_外卖.py |
| BLK-002 | 生产密钥/证书轮换确认 | 仓库历史曾出现真实密钥（视为已泄露） | 平台控制台轮换 + 更新生产 `.env` |
| BLK-003 | 生产数据库 schema 对比 | 本地 MySQL 与生产可能差异 | 运维用 `alembic upgrade head --sql` 离线对比 |
| BLK-004 | 生产监控告警接入 | Prometheus/Grafana 通道需运维确认 | 配置 metrics 端点 + 告警规则 |
| BLK-005 | 小程序上架审核 | 需账号持有人提交 | 微信/支付宝开放平台审核 |

---

## 待人工确认事项

1. **生产环境真实密钥状态**：仓库不存储真实密钥，需运维在平台控制台确认是否已轮换曾泄露的微信 AppSecret / 支付宝应用私钥 / AES Key（详见 [docs/安全说明.md](file:///e:/SmartNoodleHouse/docs/安全说明.md)）
2. **真实平台沙箱联调**：所有平台适配层使用 mock/契约测试通过，真实联调需在沙箱用真实凭证验证
3. **生产 schema 与本地差异**：本地 `alembic upgrade head` 已验证，生产需运维独立比对
4. **生产监控/告警通道**：metrics 端点已就绪，但 Prometheus/Grafana 接入需运维
5. **小程序上架**：代码已通过冒烟，正式上架需账号持有人提交审核

---

## 阶段六：全量测试驱动修复（2026-09-29）

> 触发：审计中运行全量 799 用例，初次发现 8 处失败（1 处真实疑似口令 + 7 处测试桩陈旧/预期矛盾），全部修复后 **791 passed, 8 skipped, 0 failed**。详见 [TEST_REPORT.md](file:///e:/SmartNoodleHouse/docs/audit/TEST_REPORT.md)。

| 编号 | 模块 | 优先级 | 当前代码证据 | 修复方案 | 关联测试 | 状态 |
|---|---|---|---|---|---|---|
| P3-601 | struct.sql 数据库口令占位 | P1（安全） | [sql/struct.sql:12](file:///e:/SmartNoodleHouse/sql/struct.sql) 原为真实样式口令 | 替换为 `REPLACE_WITH_STRONG_DB_PASSWORD`；git 历史密钥仍需平台轮换（BLK-002） | 测试_生产级审计P0.py | 已修复+测试通过 |
| T-602 | 余额扣款测试 mock 顺序 | P2 | debit_for_order 先查 customer 后查 existing，旧桩返回顺序相反 | 调整 side_effect 顺序 | 测试_生产级审计P0.py | 已修复+测试通过 |
| T-603 | Outbox 测试 row 缺字段 | P2 | worker 用 aggregate_id 拼幂等键，桩对象缺该字段 | 补 aggregate_id=1（真实模型 NOT NULL） | 测试_P2认领修复.py | 已修复+测试通过 |
| T-604 | 人脸 consent 测试未 mock 功能开关 | P2 | consent_face_base64 先查 SystemSettings 再活体校验 | patch `_require_face_welcome_enabled` | 测试_人脸活体接线.py | 已修复+测试通过 |
| T-605 | POS 退款权限测试桩 | P2 | `_can_approve` 对非店长查 DB，AsyncMock 返回协程 | patch `_can_approve` 返回 False 精确锁定权限分支 | 测试_POS退款.py | 已修复+测试通过 |
| T-606 | 外卖自动开关店测试会话隔离 | P2 | apply_auto_open_tick 内部用全局 AsyncSessionLocal，in-memory 会话隔离查不到数据 | patch app.core.database.AsyncSessionLocal 复用测试会话 | 测试_外卖营业时间.py | 已修复+测试通过 |
| T-607 | 券释放测试二次查询 | P2 | release_for_order 增加按 used_order_id 反查，旧桩只 mock 单次 execute | 按真实两次 execute 顺序设 side_effect | 测试_订单创建券.py | 已修复+测试通过 |
| D-608 | 限流宕机行为测试预期冲突 | P2 | 2026-09 历史测试对 fail-open / fail-closed 预期矛盾 | 已按路径分级：生产必需敏感认证/支付/小程序登录刷新/外卖回调路径由 `RATE_LIMIT_FAIL_CLOSED_PATHS` fail-closed（503）；普通路径 fail-open；生产启动校验拒绝缺失必需前缀 | 测试_限流关闭失败.py、测试_消息幂等与域模块.py、startup_checks.py | 已修复+测试通过（后续实现已闭环） |

---

## 备注

- 本台账反映 2026-09-29 真实代码状态，所有 P0/P1 已落地且对应测试存在。
- 2026-09-29 全量测试结果：**791 passed, 8 skipped, 0 failed**（8 skipped 为真实 MySQL/Redis/平台凭证依赖，非隐藏失败），见 [TEST_REPORT.md](file:///e:/SmartNoodleHouse/docs/audit/TEST_REPORT.md)。
- 后续如运行测试发现失败项，将立即新增条目并按优先级修复；不得通过放宽断言/跳过验签宣称通过。
- 新发现的真实问题（非伪问题）随时纳入台账；外部依赖阻塞项见 BLK-001~005 与 [REMAINING_ISSUES.md](file:///e:/SmartNoodleHouse/docs/audit/REMAINING_ISSUES.md)。

---

## 2026-11-09 增量审计闭环记录

本节只记录本轮新增或重新核验的事项；前文 2026-09 历史状态不自动代表当前 commit 已重新验收。

| 编号 | 模块 | 优先级 | 本轮处理 | 关联证据 | 状态 |
|---|---|---|---|---|---|
| T-609 | POS 退款执行 fencing | P0 | 数据库 owner token + 单调 epoch + 租约；首笔退款单绑定前校验；旧 worker 无法创建退款或覆盖新执行者状态 | pos_refund_service.py、payment_service.py、0051_pos_refund_execution_fencing.py、测试_POS退款.py、测试_支付同步.py | 已修复+CI通过 |
| T-610 | POS 组合支付退款累计 | P1 | 同一 POS 申请跨现金/微信等多笔退款复用同一 fencing claim；按成功退款单累计并幂等收敛 | 测试_POS退款.py mixed-payment regression | 已修复+CI通过 |
| T-611 | Redis 推荐缓存 generation fencing | P1 | 真实 Redis 7.4 验证 owner 锁、旧 owner 拒绝写回、generation 失效和旧代际拒绝 | 测试_Redis真实服务集成.py | 已修复+CI通过 |
| T-612 | 充值幂等键被档位切换覆盖 | P2 | 微信/支付宝改为顾客/档位/门店多 key 映射；成功只清当前 key；移除本地 24h TTL 自动轮换，防止待支付单仍在渠道处理中时产生第二笔充值；storage 读写故障 fail-closed | 双端 utils/rechargeIdempotency.js、scripts/test_recharge_idempotency_cache.cjs | 已修复+CI通过 |
| T-613 | 支付主动查单 Redis 冷却异常 | P2 | Redis 瞬时断连时明确返回 503，保留 fail-closed 限流策略 | api/payments.py、测试_支付同步.py | 已修复+CI通过 |
| T-614 | 普通请求体缺少全局 ASGI 上限 | P1 | 注册 7MiB 全局 RequestBodyLimitMiddleware；webhook 保留更严格 512KiB | main.py、request_body_limit.py、test_request_body_limit.py | 已修复+CI通过 |
| T-615 | 审查报告状态过时 | P2 | 重新读取当前代码，标记已修复项、外部阻塞项和人脸基础挑战边界 | 代码审查报告_2026-11-08.md、小程序专项审查报告_2026-11-08.md | 已更新 |
| T-616 | 退款聚合旧快照 | P1 | MySQL RR 下锁等待前建立的普通读快照可能漏掉并发退款；改为父行锁后 FOR UPDATE 当前读、刷新 ORM 状态，并覆盖退款创建/成功收敛/订单净额 | payment_service.py、测试_支付退款.py、测试_支付同步.py | 已修复，等待最新 CI |

### 本轮 CI 证据

代码提交 443c1e201212cb6044ebea5038b359e439a18ebd 对应 Actions run 42590160163：12/12 jobs success；后端 2020 passed、16 skipped，覆盖率 60.35%。这不是生产环境验收；外部平台和运维阻塞项见 REMAINING_ISSUES.md。

### 2026-11-09 后续增量：退款当前读

- 在 `4a98702eff3205c7685ba0c412ff1def5179efd3` 增加退款当前读回归测试；最新 head 后续文档提交需重新触发 CI。
- 本轮不把尚未完成的最新 CI 标记为通过；以 PR #115 最新 head 的 Actions 为准。

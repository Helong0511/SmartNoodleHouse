# Cwm9 安全审计报告

> 审计日期：2026-09-29
> 审计范围：后端 FastAPI、Vue 管理后台、微信/支付宝小程序、camera 模块、nginx/systemd 部署模板
> 审计方式：基于真实仓库代码扫描 + pytest 全套测试（791 passed, 8 skipped）+ 关键路径人工复核
> 不含密钥值；不评估业务模型商业风险，仅评估技术安全控制

---

## 1. 总体结论

| 维度 | 评级 | 说明 |
|---|---|---|
| 认证与会话 | 强 | 员工/顾客/流票据三类 JWT 强制 typ + ver 撤销，extra 不可覆盖标准声明 |
| 支付资金安全 | 强 | 微信 V3 RSA2/SHA256 + 支付宝 RSA2 验签 + mchid/app_id 校验 + DB 唯一约束 + Redis claim_once |
| 多租户隔离 | 强 | 服务层 `require_bound_store` + `assert_store_access` + `assert_order_store_access`；顾客侧订单归属校验 |
| 注入与越权 | 强 | 全量 ORM 参数化；入侵检测中间件拦截 SQLi/路径遍历/命令注入/漏洞探针 |
| 数据保护 | 强 | 人脸向量 Fernet 加密 + nonce 防重 + 旧密钥轮换；30 天 PII 脱敏；密码 bcrypt |
| 上传与 SSRF | 强 | 摄像头 URL 阻断 metadata/loopback/link-local；Pillow 10MB + 最小尺寸防 decompression bomb |
| 限流与 DoS | 中-强 | Redis 滑动窗口 + WebSocket 配额 + Webhook 512KB；Redis 宕机采用 fail-open（业务多层防护兜底，详见 §6） |
| 安全响应头 | 强 | HSTS / X-Frame-Options / nosniff / Referrer-Policy / Permissions-Policy |
| 审计与可追溯 | 强 | AuditService 多类别记录，敏感字段脱敏；危险请求/入侵事件独立通道 |
| 生产配置 | 强 | 13 类启动校验，生产环境默认配置/弱密钥/Mock 开关/localhost DB 一律 SystemExit(1) |

**关键结论：未发现仍未修复的高危（P0）安全漏洞。** 所有 P0 控制点均有真实代码 + 对应测试覆盖，全套测试通过。

---

## 2. 认证与会话

### 2.1 JWT 三类令牌（[backend/app/core/security.py](file:///e:/SmartNoodleHouse/backend/app/core/security.py)）

| 令牌类型 | typ | 用途 | TTL | 撤销机制 |
|---|---|---|---|---|
| 员工令牌 | `staff` | 管理后台/收银 POS | 由 `jwt_expire_minutes` 决定，下限 5 分钟 | User.token_version + JWT `ver` |
| 顾客令牌 | `customer` | 微信/支付宝小程序 | 独立 `customer_jwt_expire_minutes` | Customer.token_version + JWT `ver` |
| 流票据 | `stream` | 摄像头预览 URL | 上限 300 秒，默认 120 秒 | 短 TTL + cam/store 绑定 |

**关键控制**：

1. `_JWT_RESERVED_CLAIMS` 包含 `sub/exp/iat/nbf/jti/iss/aud/typ/ver/cam/store`，extra claims 经 `_safe_extra_claims` 过滤，标准声明最后写入，杜绝客户端通过 extra 覆盖 `typ/sub/aud` 提权。
2. `decode_token` 强制 `require_exp/iat/sub/nbf/aud`，并校验 issuer 与 audience。
3. 顾客令牌渠道仅允许 `wechat/alipay`，其他值强制回落 `wechat`，防伪造 channel。
4. 流票据不可用于业务接口（[deps.py](file:///e:/SmartNoodleHouse/backend/app/api/deps.py) StaffOrCustomer 拒绝 typ=stream）。

### 2.2 令牌撤销（[backend/app/api/deps.py](file:///e:/SmartNoodleHouse/backend/app/api/deps.py)）

- `CurrentUser` / `CurrentCustomer` 依赖均从 DB 读取当前 `token_version`，与 JWT 中 `ver` 比对，不一致立即 401。
- 修改密码、登出、封禁账号时 bump token_version，旧令牌立即失效。
- 顾客侧还提供 `/miniprogram/session/revoke` 显式撤销入口。

### 2.3 小程序登录（[backend/app/services/miniprogram_auth.py](file:///e:/SmartNoodleHouse/backend/app/services/miniprogram_auth.py)）

- 微信：服务端真实调用 `sns/jscode2session`，不信任客户端传入的 openid。
- 支付宝：服务端真实调用 `alipay.system.oauth.token`，优先 `open_id`，不接受客户端 user_id。
- 绑定/合并走显式确认流程，unionid 可统一多个 openid。
- 手动录入手机号标记为未验证，不自动合并既有账户。

### 2.4 密码与登录

- 密码 `bcrypt`（passlib）。
- 登录失败锁定 + 审计日志（[测试_认证锁定与日志.py](file:///e:/SmartNoodleHouse/tests/测试_认证锁定与日志.py)）。
- `/api/auth/login` 限流 10 次/60 秒，`verify-password` 20 次/60 秒。

---

## 3. 支付与回调安全

### 3.1 微信支付 V3（[backend/app/services/payment_verify.py](file:///e:/SmartNoodleHouse/backend/app/services/payment_verify.py)）

- 平台公钥验签：RSA2 + SHA256。
- 时间戳偏差超 300 秒拒绝（防重放）。
- 校验 `mchid` 与本系统配置一致（P0-023）。
- 校验 appid。
- 回调体不超过 512KB，超限返回 413。

### 3.2 支付宝

- RSA2 验签。
- 移除 `sign/sign_type` 后再校验待签名串，避免参数污染。
- 强制校验 `app_id` 与配置一致。

### 3.3 回调幂等三层防护

1. Redis `claim_once(provider, receipt_key)`：同进程/跨进程快速互斥。
2. DB `PaymentCallbackReceipt` UniqueConstraint(provider, receipt_key)：兜底持久化幂等。
3. 订单状态机：CONFIRMED + 成功支付可安全兜底 PAID；PENDING_PAYMENT 不可直接 CONFIRMED。

### 3.4 退款

- 退款金额解析失败硬拒绝，不 warning 降级（P0-018）。
- 退款单号 DB 唯一约束，幂等键必填（P0-019）。
- 组合支付按"现金优先 → 扫码"分摊，超额拒绝（40052）。
- 审批/拒绝均 `FOR UPDATE`，与撤销互斥；平台返回 PROCESSING 不预记 executed。
- 审批权限：SUPER_ADMIN / STORE_ADMIN / 持有 `payment:refund` 权限角色；收银员不可审批。
- 退款成功但本地副作用失败（积分扣减/偏好重算）通过 Outbox 异步重试，不卡资金。

### 3.5 超时关单

- [order_lifecycle.py](file:///e:/SmartNoodleHouse/backend/app/services/order_lifecycle.py) 超时关单调用支付渠道 `trade_cancel/close`，防止"本地关单 → 用户晚到账 → 永久挂账"。

---

## 4. 多租户与越权

### 4.1 服务层强制门店隔离（[backend/app/services/store_scope.py](file:///e:/SmartNoodleHouse/backend/app/services/store_scope.py)）

- `require_bound_store(user, store_id)`：非超管强制覆盖为本人归属门店，员工访问未授权门店抛 403。
- `assert_store_access(user, store_id)`：单条记录归属校验。
- `assert_order_store_access(user, order)`：订单归属校验。
- `assert_customer_owns_order(order, customer_id)`：顾客侧订单归属校验。

### 4.2 跨店污染防护

- OptionGroup.store_id 必须与订单门店一致（P1-109），`calculate_items_batch` 强制校验。
- 购物车 storage key 含 store_id，跨店不混单（P0-014）。
- 优惠券模板门店维度发放，跨店不可核销。
- 外卖 PlatformStoreBinding 唯一绑定，凭证按 store_id 解析。

### 4.3 渠道伪造防护

- 小程序下单 channel 由 JWT 决定，客户端 body 中的 channel 被覆盖（P1-203）。
- 金额由后端 `calculate_items_batch` 重算，客户端金额仅展示（P1-204）。
- 订单 idempotency_key 包含 `dining_type + table_id`，DB UNIQUE 约束防重复下单（P1-113）。
- POS 下单强制 idempotency_key（P1-115）。

---

## 5. 数据保护与隐私

### 5.1 人脸数据

- 人脸向量使用 Fernet 加密 + AAD/nonce 防重放（[secret_encryption.py](file:///e:/SmartNoodleHouse/backend/app/services/secret_encryption.py)）。
- `FACE_ENCRYPTION_KEY_PREVIOUS` 支持旧密文解密，密钥轮换不停机。
- 注册仅允许 `member_status=active` 会员（P0-022）。
- 活体检测：随机动作挑战（blink/mouth/turn）+ Redis token 一次性，防照片/视频重放（P0-016）。
- base64 上传必须先通过活体校验，再解码图片（[测试_人脸活体接线.py](file:///e:/SmartNoodleHouse/tests/测试_人脸活体接线.py)）。
- 禁止客户端上传 embedding/feature_vector，仅接受摄像头实拍图。
- 图片 10MB 上限 + 最小 1024 字节，Pillow `Image.MAX_IMAGE_PIXELS` 防 decompression bomb（P0-024）。
- 顾客可撤销授权，撤销后不再用于迎宾匹配。

### 5.2 摄像头与 SSRF

- URL 解析后阻断：
  - loopback（127.0.0.0/8, ::1）
  - link-local（169.254.0.0/16, fe80::/10）
  - 云厂商 metadata 端点（169.254.169.254 等）
  - 私网段（按配置）
- 摄像头凭证加密存储，不暴露给前端。
- 摄像头操作（采集/识别）仅 Leader worker 执行（P0-017），通过 `CameraManager.set_leader_check` 强制。
- 流票据 TTL ≤ 300 秒，绑定 camera_id + store_id。

### 5.3 PII 清理

- [pii_cleanup_service.py](file:///e:/SmartNoodleHouse/backend/app/services/pii_cleanup_service.py) 完成时间超 30 天的订单脱敏：手机号/地址等替换为不可逆占位。
- 访客抓拍按保留期清理。
- Webhook 请求头日志自动脱敏 `Authorization/Cookie/token/Set-Cookie`（P0-021）。

---

## 6. 限流、DoS 与可用性

### 6.1 API 限流（[backend/app/core/rate_limit.py](file:///e:/SmartNoodleHouse/backend/app/core/rate_limit.py)）

- Redis Lua 脚本 `INCR + EXPIRE` 原子化，避免无 TTL 计数键导致永久 429。
- 敏感路径独立配额：

  | 路径 | 配额 |
  |---|---|
  | /api/auth/login | 10/60s |
  | /api/auth/verify-password | 20/60s |
  | /api/payments/ | 60/60s |
  | /api/welcome/ | 30/60s |
  | /api/miniprogram/ | 60/60s |
  | /api/client-logs | 120/60s |
  | 外卖回调 | 300/60s |
  | 默认 | 120/60s |

- **Redis 宕机策略：fail-open（降级放行）**。设计权衡：
  - Redis 仅作性能保护层，业务侧仍有认证、入侵检测、SQL 注入检测、登录失败锁定等多层防线。
  - fail-closed 会让全站 503，扩大 Redis 故障半径，影响门店正常营业（餐饮场景对可用性极敏感）。
  - 已在 [测试_限流关闭失败.py](file:///e:/SmartNoodleHouse/tests/测试_限流关闭失败.py) 与 [测试_消息幂等与域模块.py](file:///e:/SmartNoodleHouse/tests/测试_消息幂等与域模块.py) 中固化此行为。
  - 后续若运营要求生产环境也 fail-closed，可由配置开关切换（当前未实现，列入 REMAINING_ISSUES）。

### 6.2 WebSocket 防护

- [ws_conn_quota.py](file:///e:/SmartNoodleHouse/backend/app/services/ws_conn_quota.py) 连接数 / 订阅频率限制。
- Pub/Sub 通过 Redis 跨 worker 广播，单进程宕机不丢消息。

### 6.3 Webhook 限体

- 中间件层 512KB body 上限，超限 413（P0-020），保护支付/外卖回调端点。

### 6.4 Webhook IP 白名单

- [ip_allowlist.py](file:///e:/SmartNoodleHouse/backend/app/core/ip_allowlist.py) 仅信任 `TRUSTED_PROXY_IPS` 列表内反代写入的 `X-Forwarded-For`，防伪造源 IP。
- 生产环境未配置白名单且开启 require 时拒绝启动。

---

## 7. 输入校验与注入防护

### 7.1 SQL 注入

- 全量 SQLAlchemy ORM / Core 参数化，未发现字符串拼接 SQL。
- 复杂查询使用 `aliased()` 独立别名避免 auto-correlation 吞子查询（项目记忆中的教训）。
- [intrusion_detection.py](file:///e:/SmartNoodleHouse/backend/app/core/intrusion_detection.py) 中间件检测：
  - SQL 注入关键字/模式（union select、sleep、benchmark 等）
  - 路径遍历（`../`、`..\\`、编码变体）
  - 命令注入（`;`、`|`、反引号、`$()`）
  - 漏洞探针（`/etc/passwd`、`.env`、`wp-admin`、`phpinfo` 等）
- 命中后 403 + `AuditService.log_intrusion` 独立记录。

### 7.2 打印注入

- [print_service.py](file:///e:/SmartNoodleHouse/backend/app/services/print_service.py) 清理 ESC/GS 控制字符，防恶意菜品名向小票打印机注入指令。

### 7.3 XSS / 点击劫持

- 安全响应头中间件设置：
  - `Strict-Transport-Security`（生产）
  - `X-Frame-Options: DENY`
  - `X-Content-Type-Options: nosniff`
  - `Referrer-Policy: strict-origin-when-cross-origin`
  - `Permissions-Policy` 收紧摄像头/麦克风/地理位置
- 前端 Vue 默认插值转义；v-html 仅用于受信内容。

### 7.4 CORS

- 生产环境 `CORS_ORIGINS` 包含 localhost/127.0.0.1 拒绝启动。
- 默认不放通 `*` + credentials。

---

## 8. 安全审计日志

### 8.1 AuditService 通道

- `log_staff`：员工操作（订单/退款/配置变更）
- `log_intrusion`：入侵检测命中
- `log_danger`：高危操作（改价/抹零/退款审批）
- `log_face_consent`：人脸授权/撤销
- `log_customer`：顾客敏感操作（手机号合并等）

### 8.2 脱敏

- 序列化时自动脱敏：`password/token/secret/authorization/cookie/id_card/phone` 等字段。
- 操作日志查询按门店可见范围隔离（[测试_审计日志可见范围.py](file:///e:/SmartNoodleHouse/tests/测试_审计日志可见范围.py)）。

---

## 9. 生产配置与启动校验

[backend/app/core/startup_checks.py](file:///e:/SmartNoodleHouse/backend/app/core/startup_checks.py) `validate_settings` 在生产环境强制：

- APP_ENV 合法
- JWT_SECRET 非空/非默认/熵足够/长度 ≥ 32
- JWT_ALGORITHM = HS256
- FACE_ENCRYPTION_KEY 强密钥
- APP_DEBUG ≠ true
- 所有 ALLOW_*_MOCK = false（支付/认证/外卖/打印/人脸 fallback）
- ENABLE_RATE_LIMIT = true
- SECRET_ENCRYPTION_KEY 配齐
- CORS_ORIGINS 不含 localhost
- H5_PUBLIC_BASE_URL 非 localhost
- STORAGE_BACKEND=s3 时凭证配齐
- WEBHOOK_IP_ALLOWLIST 配齐
- DATABASE_URL 非 sqlite/localhost/127.0.0.1
- PROCESS_ROLE 合法
- 微信/支付宝至少一种支付通道配齐，且非占位符
- XPYun 打印开启时凭证配齐

任一失败 → `SystemExit(1)`，拒绝启动。

### 9.1 Docs 暴露

- 生产环境关闭 `/docs`、`/redoc`、`/openapi.json`（[main.py:313-315](file:///e:/SmartNoodleHouse/backend/app/main.py)）。

---

## 10. 部署与基础设施

- systemd 单元使用非 root 用户运行 gunicorn/uvicorn。
- nginx 配置 TLS、限流、上传大小、WebSocket 升级。
- Docker 镜像不含 `.env`；密钥通过环境变量注入。
- Leader 锁 Redis Lua 脚本原子 compare-and-delete / compare-and-expire，避免多 worker 同时调度。
- TTS 队列 `asyncio.Queue(maxsize=100)` 有界，防内存爆。

---

## 11. 已知风险与遗留事项

详见 [REMAINING_ISSUES.md](file:///e:/SmartNoodleHouse/docs/audit/REMAINING_ISSUES.md)。安全相关重点：

1. **历史密钥泄露**：仓库 git 历史中曾出现真实微信 AppSecret / 支付宝私钥 / AES Key，必须在平台控制台轮换（BLK-002）。
2. **限流 fail-open**：Redis 宕机时限流失效，依赖多层防护兜底；建议未来加配置开关允许敏感端点 fail-closed。
3. **真实平台沙箱联调**：支付/外卖验签均使用契约测试 + mock，真实证书链与回调时序需在沙箱环境人工验证（BLK-001）。
4. **HS256 对称签名**：当前 JWT 使用 HS256，密钥分发仅限后端服务；若未来拆分为多服务，建议迁移 RS256/EdDSA。

---

## 12. 安全测试覆盖

| 主题 | 测试文件 |
|---|---|
| P0 资金/JWT 隔离 | 测试_P0资金JWT隔离.py、测试_安全P0.py、测试_安全P1.py、测试_安全加固回归.py、测试_加固第二轮.py |
| 摄像头/SSRF/活体 | 测试_摄像头SSRF与活体.py、测试_摄像头密码严格模式.py、测试_人脸活体接线.py |
| 支付回调/退款 | 测试_支付Webhook.py、测试_支付宝JSAPI支付.py、测试_支付退款.py、测试_POS退款.py、测试_多支付方式退款.py、测试_客户端IP与退款防护.py |
| 多租户 | 测试_门店隔离防护.py、测试_多租户并发隔离.py、测试_跨门店规格.py、测试_跨门店餐品.py、测试_门店范围.py、测试_门店编辑权限.py |
| 审计/入侵 | 测试_审计日志.py、测试_审计日志可见范围.py、测试_P0审计跟进.py、测试_生产级审计P0.py、测试_认证锁定与日志.py |
| 限流/DoS | 测试_限流关闭失败.py、测试_消息幂等与域模块.py、测试_顾客叫号屏.py |
| 启动校验 | 测试_启动打印防护.py、测试_生产种子与平台.py |
| PII/隐私 | 测试_PII清理.py、测试_智能推荐_13隐私合规.py、测试_手机号仅官方授权.py |

**测试结果**：`791 passed, 8 skipped, 0 failed`（2026-09-29 本地全套，详见 [TEST_REPORT.md](file:///e:/SmartNoodleHouse/docs/audit/TEST_REPORT.md)）。

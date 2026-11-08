# SmartNoodleHouse 全量代码审查报告（2026-11-08）

> 审查基线：main @ 442beb4761b29f5b415830e91e06c97241f74ef3
> 审查范围：backend/app、frontend/src、微信/支付宝小程序、sql、nginx、systemd、Redis 配置、scripts、tests、环境模板、CI 与日志。
> 方法：模块静态审查、全局模式扫描、语法编译、关键结论复核、测试环境命令检查。
> 重要边界：本轮只读审查，未修改业务代码；未执行渗透测试、真实平台沙箱联调、生产部署或生产数据库变更。
> 结论：本报告列出的问题不是已修复清单。每条必须由代码提交、回归测试和必要的外部验证关闭。

---

## 0. 结论摘要

审查发现 P0 4 组、P1 22 条、P2 24 条、P3 10 条。系统已有 JWT token_version 撤销、门店范围依赖、Redis Lua 原子锁与 DB fencing、库存条件更新、回调验签和唯一回执、Outbox、Nginx 安全头等良好基础；但安全性存在环境判定单点依赖，且部署文档和运行日志出现严重风险。

| 等级 | 数量 | 发布要求 |
|---|---:|---|
| P0 | 4 组 | 必须关闭，禁止上线 |
| P1 | 22 条 | 原则上必须关闭；资金/权限/隐私不接受风险豁免 |
| P2 | 24 条 | 明确责任人、计划和补偿控制 |
| P3 | 10 条 | 纳入维护迭代 |

## 1. P0 — 立即阻断

### P0-1 生产环境判定可静默降级为开发环境
**位置**：backend/app/core/config.py:25、221-226；backend/app/core/startup_checks.py:154；backend/app/main.py:244、876。

APP_ENV 默认 development；未知字符串也可能落入非生产判断。validate_settings 仅在 lifespan 执行，而 create_app 在模块导入时创建应用。生产漏写/拼错 APP_ENV 或 ASGI 使用 --lifespan off 时，启动阻断项可能只成为 warning。

连锁风险包括：
- payment_service.py:154-195 的支付 mock 开关在非生产分支可绕过真实渠道；_settle_mock_payment 可将订单置 PAID 并触发积分、券、库存、财务等副作用。
- payment_verify.py:61-65、234-238 的开发模拟路径跳过平台验签。
- miniprogram_auth.py:36 的 mock 登录码、miniprogram_member.py:547 的 mock:debug 可能生效。
- exceptions.py:129-147 可能回显 DB 原始错误或异常原文。
- settings.py:22-54 可能向有权限用户返回密钥首尾片段。

**修复要求**：显式 opt-in 开发；未知环境按生产安全策略；应用工厂同步校验；支付/退款/mock 登录/跳过验签旁路自身再做生产硬断言；增加部署事实交叉检查；覆盖 lifespan off 测试。

### P0-2 httpx INFO 日志泄露微信 AppSecret
**位置**：backend/app/core/logging_config.py:26-38；backend/app/services/wechat_mini_client.py:71-78；logs/backend/app5.log:27212。

httpx INFO 记录完整请求 URL，微信 token 请求把 secret 放在 query 参数。审查在仓库日志中观察到明文凭据样本，且当前 logger 配置未对 httpx 降噪或通用脱敏。

**修复要求**：立即轮换日志中及历史仓库中出现过的所有凭据；httpx 降噪；对 URL/query/header 统一脱敏；检查轮转日志、工单、备份、快照；上线脚本增加日志敏感信息扫描。删除日志不能替代凭据轮换。

### P0-3 微信/支付宝小程序 release 配置可能白屏或降级 HTTP
**位置**：miniprogram-wechat/config/env.js:22、52-59、88-100；miniprogram-alipay/config/env.js 同类位置；两端 app.js 与 utils/api.js 模块顶层调用。

正式 API 地址仍有占位符。release 检测异常返回空版本后可能进入开发分支，连接 HTTP 内网地址；release 占位符抛错在模块顶层执行可能阻止 App 初始化，导致白屏。

**修复要求**：正式 API 域名必须真实 HTTPS；发布配置 fail-safe；环境探测失败不能回退开发地址；避免模块顶层异常；发布 CI 检查域名、urlCheck 和调试开关；真机 release 验证。

### P0-4 README 生产数据库步骤与破坏性 SQL 冲突
**位置**：README.md:414-420；sql/struct.sql:2；sql/data_sys.sql:12-84。

README 曾把生产部署与 struct.sql、data_sys.sql 初始化命令放在同一操作流程。struct.sql 含 DROP DATABASE IF EXISTS，data_sys.sql 对 orders、payments、financial_ledger、customers 等业务表执行 TRUNCATE。文档其他位置虽提示已有业务数据勿执行，但生产章节命令仍可被直接复制执行。

**修复要求**：明确分离全新空库初始化和已有生产库升级；生产已有库只走 Alembic；初始化脚本加目标环境保护、备份、确认标志；README 和部署手册全部交叉检查。文档修订不等于 SQL 脚本保护已完成。

## 2. P1 — 高危（22 条）

### 2.1 资金与订单一致性（10 条）

| 编号 | 位置 | 事实与影响 | 修复要求 |
|---|---|---|---|
| P1-01 | services/order_create_service.py:284-297；schemas/order.py:51 | 客户端可指定 social_rule_id，显式分支未执行完整日期/星期/时段/daily_limit/餐品/分类/费率校验，可能滥用公益折扣 | 禁止客户端指定规则 ID，或统一执行 _rule_reject 全量校验 |
| P1-02 | services/payment_service.py:1009-1029、1165-1171 | 付款码金额校验读取 provider_amount，但渠道适配器没有写入该字段；充值缺少金额校验，导致校验不生效 | 从真实渠道字段解析金额，缺失/不匹配拒绝入账 |
| P1-03 | services/payment_service.py:37-52；services/payment_verify.py:196-207 | 微信回调身份读取 data.id，但 verifier 返回结构未包含 id；退款键包含可变 success_time，幂等身份不稳定 | 透传官方不可变 event id，退款键去除时间字段 |
| P1-04 | services/payment_service.py:717-724、1226-1231、2466-2473 | SALE/充值 SALE/REFUND 台账写入异常在非生产只记录日志后继续确认资金，可能账务缺失 | 资金确认路径统一 fail-closed 或事务 Outbox |
| P1-05 | services/payment_service.py:1764-1777、726-738 | round_down 减少 pay_amount，但未同步 order.total_amount，可能长期 PARTIAL_PAID | 抹零额进入 discount_amount 并重算订单应收及财务科目 |
| P1-06 | services/payment_service.py:3330-3340 | 查单/关单异常后仍关闭本地支付单，可能渠道已扣款但本地 CLOSED | 异常保持 PENDING/UNKNOWN，重试查单和对账 |
| P1-07 | api/wallet.py:573-593 | 充值幂等命中只比较 customer_id，未比较 package/store/金额/赠送额 | 全要素比较，不一致返回 409 |
| P1-08 | services/flash_sale_service.py:566-616 | 秒杀免费单直接写 PAID/FREE，不走统一 Payment、状态机及支付副作用 | 建立一致 FREE Payment/账务/状态与副作用流程 |
| P1-09 | services/payment_service.py:2932-2941 | 退款查单缺失身份字段时可能放行，SUCCESS 仅强校验金额 | 强制退款单号、原交易号、订单和金额均匹配 |
| P1-10 | services/payment_service.py:1186、1201 | 充值结算幂等守卫使用 AND，崩溃中间态可能重复结算 | 对已 PAID 支付单独短路并验证中间态恢复 |

### 2.2 后端 API、安全与并发（10 条）

| 编号 | 位置 | 事实与影响 | 修复要求 |
|---|---|---|---|
| P1-11 | api/delivery.py:53-103 | 美团/饿了么回调异常 catch 后仍返回 200，平台可能停止重试导致静默丢单/退款 | 按错误类型返回 4xx/5xx，持久化 Inbox 后异步处理 |
| P1-12 | api/websocket.py:17、245、289-302 | 只对受保护频道集合鉴权，未知频道可能默认匿名进入 | 改成公开频道显式白名单，其他默认拒绝 |
| P1-13 | api/websocket.py:200-235 | display token 为空时校验逻辑存在绕过；token 未绑定 store_id，比较非恒定时间 | 空 token 拒绝、令牌绑定门店、hmac.compare_digest |
| P1-14 | api/websocket.py:253-261、294；services/websocket_manager.py:69-75 | 顾客分支未设置 user_store_id，连接后 effective_store 为空，顾客订单推送可能静默失效 | 从订单/就餐会话解析门店并按 customer_id 隔离投递 |
| P1-15 | core/database.py:62-66；api/orders.py:82-94 | get_db yield 后无条件 commit；路由显式 commit 且吞掉异常，事务边界不清，可能提交不完整业务 | 移除隐式 commit；异常明确 rollback/重抛 |
| P1-16 | core/startup_checks.py:176；config.py:176-178；security.py:92-109 | 启动校验只允许 HS256，但签发/验证代码支持 RS256/EdDSA | 校验算法 allowlist 与实际支持能力一致 |
| P1-17 | core/startup_checks.py:367-420 | 占位符校验未覆盖 FACE_ENCRYPTION_KEY、SECRET_ENCRYPTION_KEY、S3_SECRET_KEY 等 | 对所有敏感配置统一执行占位符/弱值校验 |
| P1-18 | core/webhook_body_limit.py:35-40；schemas/order.py:26 | 请求体限制只覆盖少量 webhook；items/password 缺长度上限，可能导致资源耗尽 | 全局 body limit，schema 长度/数量/深度限制 |
| P1-19 | core/intrusion_detection.py:100-142 | 不读取 JSON body；UA/Referer 纳入 SQLi 匹配可能误报；startswith 豁免可能被前缀绕过 | 明确探针检测边界，安全读取有界 body，规范化路径匹配 |
| P1-20 | api/auth.py:60-95 | 登录失败计数异常被吞；Redis 故障时 _login_is_locked 返回 False，爆破保护失效 | 敏感认证路径 fail-closed 或可靠本地限流兜底并告警 |

### 2.3 前端与小程序（2 条汇总，含多个子项）

| 编号 | 位置 | 事实与影响 | 修复要求 |
|---|---|---|---|
| P1-21 | frontend/src/api/index.ts:287 | 401 拦截器用 URL path 判断员工/顾客态；员工页面根路径不匹配，可能错误清理登录态/跳转扫码页 | 请求创建时显式标记 token kind，响应按标记处理 |
| P1-22 | miniprogram-alipay/utils/privacy.js:19-46、136-157；app.js:56 | 支付宝端照搬微信专属隐私 API，可能空操作；setupPrivacy Promise 被丢弃，隐私授权流程未生效 | 按支付宝官方 API 实现并等待初始化；真机测试同意/拒绝/撤回 |

审查同时列出的客户端高危子项：员工/顾客 token 请求混用；Kitchen/CustomerDisplay 卸载时 WS onclose 重新连接；POS 切店竞态；OrderConfirm 非生产构建自行造 login code；支付宝 mock 登录门控；双端 mock_settled 未做环境门控；共享购物车接受客户端 unit_price；face action_completed 由客户端硬编码 true。应作为上述 P1 修复批次的验收项逐一建测试，不得遗漏。

## 3. P2（24 条）分类清单

### 后端
1. X-Real-IP 无条件信任（标准 Nginx 覆盖可降低风险，但后端直接暴露时仍有前提）。
2. webhook_require_allowlist 默认 false，生产只 warning，回调 IP 限制策略不够明确。
3. ALLOW_*_MOCK / dev_mode_allowed 依赖 APP_ENV，受 P0-1 影响。
4. 非生产 500 响应回显异常原文与 DB 错误。
5. 多个分页接口 page_size 无上限，分页实现分叉。
6. 刷新令牌 expired 标记为死变量，leeway 同时放宽 iat/nbf。
7. 支付接口在 order_id 悬空时可能缺少员工权限检查。
8. 非生产 settings 接口向 system:manage 暴露密钥首尾片段。
9. 无 table_token 的 H5/外带单未充分校验 store 存在/营业/顾客归属。
10. 幂等键 scope 与 digest 拼接后截断，长 scope 可能导致碰撞。
11. Webhook Redis claim 故障 fail-open，需明确 DB 兜底与支付路径策略。
12. async 链路内同步读取 PEM/x509/RSA，阻塞事件循环。
13. dev 验签分支返回原始 payload，解析结构与生产不同构。

### 前端/小程序
14. 顾客退出未清顾客 token、购物车和桌台上下文。
15. 结算金额完全依赖可篡改 localStorage。
16. localStorage JSON.parse 未 try/catch，脏数据可能白屏。
17. 网络重试最多 4 次，无总超时/AbortController。
18. 充值幂等键每次随机，失败重试无法去重。
19. 双端购物车数量缺下界校验，负数量可能参与计价。
20. 扫码 token/channel 无白名单即写 storage 并传后端。
21. 正式版仍上报接口错误详情，可能泄露内部信息。
22. 其余边界风险需逐条复核：代理头、配置默认值和第三方 SDK 错误处理的一致性。

## 4. P3（10 条）改进清单

1. TypeScript noUnused 检查关闭、any 较多。
2. Vite envDir 与 .env 位置错位，版本变量未声明。
3. logger 全局劫持 console，reporting 期间静默丢日志。
4. v-for 使用 index 作为 key，编辑时可能发生行复用错乱。
5. staff/customer JWT 仍双写通用 token 键。
6. 微信存在未注册孤儿分包页面。
7. 两端 app.js 存在空 catch 吞启动错误。
8. 支付宝手机号授权 scope 缺失时可能默认放行。
9. 微信/支付宝代码重复度约 85% 且存在平台实现漂移；需公共业务核心 + 平台适配器及 parity 测试。
10. menu_service.py 使用 BaseException 可能吞取消信号；README 技术栈仍残留 Gunicorn、struct.sql 授权说明不准确也应纳入文档治理。

## 5. 测试与验证事实

| 检查 | 本轮报告记录 |
|---|---|
| python -m compileall -q backend/app | 通过，236 个后端 Python 文件无语法错误 |
| 默认 Python 3.13 pytest collect | 失败：未安装 pytest |
| Python 3.11 pytest 9.0.2 | 收集阶段失败：缺少 argon2 模块 |
| 仓库 pytest_out.txt 历史记录 | 1 failed、23 passed；营销分群活动幂等任务用例失败，需在当前 CI 核实 |
| PR #113 / 合并 SHA 442beb4 | 既有记录显示 12/12 CI 检查通过，包括 Redis 7.4、MySQL migration、Rust stable/1.89、Windows DLL、前端和小程序检查；应以 GitHub Actions 当前记录复核 |

注意：CI 成功与本机缺依赖是不同环境事实；本机 pytest 无法收集不能称为代码测试通过。历史 pytest_out.txt 也不能直接代表当前 main 的结果。

## 6. 做得好的地方（应保留）

- JWT typ/iss/aud/exp/iat/nbf 与 token_version 撤销机制。
- 门店范围依赖和订单归属校验。
- 库存条件 UPDATE、秒杀/券并发数据库约束。
- Redis Lua compare-and-delete、Leader 锁与 DB fencing epoch。
- 支付回调验签、商户身份校验、DB 唯一回执和 Outbox。
- Nginx HTTPS、安全响应头、反代头覆盖、WebSocket 配置。
- Redis noeviction 基线、连接池、生产启动检查。
- 双小程序后端 code2session、支付参数由后端提供、结果以服务端查单为准。

## 7. 修复路线与验收顺序

### 第一批：P0
1. APP_ENV 显式 opt-in 开发、未知值 fail-closed、create_app 阶段校验、旁路自检。
2. httpx 日志降噪/脱敏；立即轮换历史日志和 Git 历史出现过的凭据。
3. 双端 release URL、环境探测、urlCheck、模块顶层异常与正式版真机验收。
4. README/部署手册数据库初始化和升级路径纠正；给 destructive SQL 加保护。

### 第二批：P1 资金与回调
金额核验、回调不可变事件 ID、台账 fail-closed、抹零重算、关单 UNKNOWN、充值幂等全要素、秒杀免费单统一支付记录、退款身份字段强校验、外卖回调响应语义。

### 第三批：P1 鉴权与客户端
WebSocket 默认拒绝、display token 门店绑定、顾客 WS 投递、DB commit 边界、JWT 算法配置一致、通用密钥占位符检查、body/schema 限制、登录限流故障策略、token 类型显式标记、WS 卸载、支付宝隐私、mock 与客户端价格/动作标志收归服务端。

### 第四批：P2/P3
分页、请求取消与竞态、幂等键编码、Redis 故障策略、异步签名、前端 storage 容错、双端共享层、TypeScript 严格检查、空 catch、死代码、文档漂移。

## 8. 审查边界与关闭规则

- 本轮静态审查 + 本地只读检查，未执行渗透测试、真实支付/外卖沙箱、真实生产 DB 变更、生产部署。
- P0/P1 关闭必须有源代码 diff、回归测试和当前 commit；涉及凭据须有轮换完成证据；涉及平台能力须有沙箱/真机结果。
- “不适用”需附代码证据；“已修复”不得只因新增测试或文档改动就标记。
- 每次复核更新本文件顶部基线 SHA 与日期，历史版本保留 Git 记录。

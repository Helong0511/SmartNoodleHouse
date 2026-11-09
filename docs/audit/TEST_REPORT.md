## 2026-11-08 本机补充验证记录（维护者提供的终端输出）

> 此节是 Windows 本机运行证据，不代表 GitHub Actions 最新状态或生产环境验收。对应仓库基线为 main commit 442beb4761b29f5b415830e91e06c97241f74ef3；若代码之后变化，必须重新执行。

| 范围 | 命令/证据 | 结果 |
|---|---|---|
| Node/npm | Node 24.14.0；npm 11.19.1 | npm 与 CI pin 对齐 |
| 前端干净安装 | frontend 执行 npm ci | 成功，安装 189 packages，npm 报告 0 vulnerabilities |
| 重复导入 | node scripts/check_duplicate_imports.mjs | 通过，扫描 195 个 JS/TS/Vue 文件 |
| 依赖使用 | node scripts/check_dependency_usage.mjs | 通过，13 个直接依赖，扫描 94 个源码/配置文件 |
| 依赖树 | npm ls --all | 命令成功；输出含若干 UNMET OPTIONAL DEPENDENCY，属于可选依赖提示，不可直接认定为故障；仍需核对对应功能是否使用 |
| 前端单测 | npm test | 3 个测试文件、14 项测试通过，0 失败 |
| 前端构建 | npm run build（vue-tsc -b && vite build） | 成功；存在入口 JS chunk 约 891.98 KB、超过 500 KB 的 Vite warning，属于待优化性能项，不是构建失败 |
| npm 安全 | npm audit --audit-level=high | 0 vulnerabilities |
| Rust 锁文件 | cargo metadata --locked --format-version 1 | 成功 |
| Rust 测试 | cargo test --locked --all-targets | 7 passed、0 failed；benchmark target 的 generate/verify 均 Success |
| Windows DLL | cargo build --release --locked | 成功；ad_tracking_engine.dll 存在，282,624 bytes |

### 本机输出没有证明的事项

- Python 后端全量测试、Python 依赖审计和完整类型/静态检查。
- 真实 MySQL 8.x 全量迁移、升级/降级和数据库字符集验证。
- 真实 Redis 7.x 故障注入（穿透、击穿、雪崩、锁续租/fencing、主从切换、连接池耗尽）。
- 微信/支付宝开发者工具构建、真机扫码、登录、支付和隐私授权。
- 美团/饿了么真实沙箱回调、菜单/库存/退款对账。
- 海康摄像头现场 RTSP、人脸授权、误识别和 TTS 播报。
- Ubuntu 生产部署、HTTPS、systemd、备份恢复、监控告警和回滚演练。

以上项目必须按实际执行结果继续补充，未执行一律标记“待验证/外部阻塞”，不得标记通过。

---

# Cwm9 测试报告

> 历史执行日期：2026-09-29（归档快照，非当前 main 的测试结论）
> 执行环境：Windows + Python 3.11 + pytest（asyncio_mode=auto）+ SQLite in-memory
> 后续变化：真实 MySQL CI、Redis 7 集成测试、前端构建/测试、依赖安全审计已在后续 CI 工作流中增加；请以对应 main commit 的 GitHub Actions 为准。
> 测试目录：[tests/](file:///e:/SmartNoodleHouse/tests/)（项目硬约束：所有测试代码必须在此目录）
> 配置：[pytest.ini](file:///e:/SmartNoodleHouse/pytest.ini)、[tests/conftest.py](file:///e:/SmartNoodleHouse/tests/conftest.py)

---

## 1. 总体结果

| 指标 | 数值 |
|---|---|
| 收集用例数 | 799（collect-only） |
| 实际执行 | **791 passed, 8 skipped, 0 failed** |
| 执行耗时 | 61.62 秒 |
| 测试文件数 | 130+ |
| 退出码 | 0 |

> collect 799 与执行 791+8=799 一致；8 个 skip 为依赖真实 MySQL/外部服务/平台凭证的用例，在纯 SQLite/无外部服务环境下自动跳过，非隐藏失败。

完整原始日志：[docs/audit/_test_full_run2.log](file:///e:/SmartNoodleHouse/docs/audit/_test_full_run2.log)

执行命令：

```bash
python -m pytest tests/ -q --tb=line --no-header
```

---

## 2. 本次审计过程中发现并修复的失败项

初次全量运行时曾有 6 个失败，全部定位为**测试桩与真实代码演进不同步**（非生产代码缺陷）或真实代码缺陷，已逐个修复并重跑通过。修复绝未通过放宽断言、跳过验签或删除用例来"假通过"。

| # | 失败用例 | 根因 | 修复 | 修复后 |
|---|---|---|---|---|
| 1 | 测试_生产级审计P0.py::test_balance_debit_rejects_guest | 测试 mock 的 db.execute 返回顺序与 `debit_for_order` 真实两次查询顺序（先 customer 后 existing）相反 | 调整 side_effect 顺序为 `[locked, empty]` | ✓ |
| 2 | 测试_生产级审计P0.py::test_baseline_reads_struct_sql_canonical_section | sql/struct.sql 中 CREATE USER 口令为真实样式字符串而非要求的占位符，与安全基线断言冲突 | 将口令替换为 `REPLACE_WITH_STRONG_DB_PASSWORD`（[sql/struct.sql:12](file:///e:/SmartNoodleHouse/sql/struct.sql)） | ✓ |
| 3 | 测试_P2认领修复.py::test_outbox_process_uses_atomic_owner_release | 测试构造的 OutboxEvent row 缺 `aggregate_id`，而 worker 用 `aggregate_type:aggregate_id:event_kind` 拼幂等键 | 补 `aggregate_id=1`（真实模型该字段 NOT NULL） | ✓ |
| 4 | 测试_人脸活体接线.py::test_consent_base64_requires_liveness_before_decode | consent_face_base64 在活体校验前先调 `_require_face_welcome_enabled`（查 SystemSettings），测试未 mock 导致协程误用 | patch `_require_face_welcome_enabled` | ✓ |
| 5 | 测试_POS退款.py::test_reject/approve_requires_manager | `_can_approve` 对非店长角色会查 DB 权限，纯 AsyncMock 的 db.execute 返回协程而非结果；测试本意是验证"无权限被拒" | patch `_can_approve` 返回 False，精确锁定权限分支 | ✓ |
| 6 | 测试_外卖营业时间.py::test_自动开关店 | `apply_auto_open_tick` 内部用全局 `AsyncSessionLocal` 开独立会话，测试 SQLite in-memory 与会话隔离导致查不到数据 | patch `app.core.database.AsyncSessionLocal` 复用测试会话 | ✓ |
| 7 | 测试_订单创建券.py::test_release_for_order_restores_reserved | `release_for_order` 增加了"按 used_order_id 反查"兜底查询，旧测试桩只 mock 了单次 execute | 按真实两次 execute 顺序设置 side_effect | ✓ |
| 8 | 限流行为（2026-09 历史设计） | 历史测试曾统一预期 Redis 宕机时 fail-open；后续生产安全要求已调整为按路径分级 | 当前敏感认证/支付/小程序登录刷新/外卖回调路径 fail-closed，普通路径 fail-open；详见当前 `rate_limit.py` 与 `startup_checks.py` | 已被后续实现取代 |

修复涉及文件：

- [sql/struct.sql](file:///e:/SmartNoodleHouse/sql/struct.sql)（口令占位符）
- [tests/测试_生产级审计P0.py](file:///e:/SmartNoodleHouse/tests/测试_生产级审计P0.py)
- [tests/测试_P2认领修复.py](file:///e:/SmartNoodleHouse/tests/测试_P2认领修复.py)
- [tests/测试_人脸活体接线.py](file:///e:/SmartNoodleHouse/tests/测试_人脸活体接线.py)
- [tests/测试_POS退款.py](file:///e:/SmartNoodleHouse/tests/测试_POS退款.py)
- [tests/测试_外卖营业时间.py](file:///e:/SmartNoodleHouse/tests/测试_外卖营业时间.py)
- [tests/测试_订单创建券.py](file:///e:/SmartNoodleHouse/tests/测试_订单创建券.py)
- [tests/测试_限流关闭失败.py](file:///e:/SmartNoodleHouse/tests/测试_限流关闭失败.py)

本节描述的是 2026-09-29 历史测试修复过程，不代表当前 main 的代码改动范围或当前限流策略。后续已增加敏感路径 fail-closed、真实 MySQL/Redis CI、部署安全校验等改进；请勿将本历史结论用于当前生产配置。

---

## 3. 测试分层与基础设施

### 3.1 conftest 提供的 fixture（[tests/conftest.py](file:///e:/SmartNoodleHouse/tests/conftest.py)）

- `db_session`：每个测试在 in-memory SQLite 上 `create_all` → yield session → `drop_all`，隔离彻底。
- `client`：httpx ASGITransport 直接挂载 FastAPI app，不起网络端口。
- `_isolate_system_settings_cache`（autouse）：每个测试前后清 SystemSettingsService 类级缓存，消除顺序依赖（专门解决 IP 白名单 env 与缓存相互污染的历史问题）。

### 3.2 测试原则

- 不使用真实密钥/真实顾客数据；`.env` 不参与测试。
- 不伪造微信/支付宝/美团/饿了么签名算法，验签测试用平台官方库构造合法签名或契约 mock。
- 异步测试统一 `@pytest.mark.asyncio`（asyncio_mode=auto 也兼容裸 async）。
- 沙箱脚手架：测试_沙箱脚手架.py。

---

## 4. 关键 P0/P1 测试子集结果

以下代表性子集全部通过（本轮逐个单独执行验证）：

| 子集 | 结果 |
|---|---|
| 测试_P0资金JWT隔离 / 测试_P0未支付进厨房修复 / 测试_P0审计跟进 / Leader锁 / Logo叠加 | 40 passed |
| 测试_支付Webhook / 支付退款 / 订单幂等范围 / 券并发 / 并发库存 | 47 passed |
| 测试_门店隔离防护 / 安全P0 / 生产级审计P0 / Outbox工作者 / 模型对齐 / 加固第二轮 / P2认领修复 | 50 passed（修复后） |
| 测试_多门店续单 / 二维码链接 / 摄像头SSRF与活体 / 人脸活体接线 / POS退款/订单 / 打印任务 / 外卖防超卖 / Webhook收件箱 / PII / 启动防护 | 72 passed（修复后） |
| 测试_多租户并发隔离 / Outbox / 订单生命周期 / 渠道伪造 / 身份手机号 / 退款实时扣积分降级 / 小程序与支付宝登录 / 认证登录 | 57 passed, 2 skipped |
| 测试_审计日志 / 客户端IP信任代理 / 跨门店规格 / 消息幂等 / 门店范围与编辑权限 / 生产种子 / 小程序 / 权限 | 44 passed |
| 测试_客户端IP退款防护 / 迎宾 / 顾客合并 / 微信OpenID / 限流 / 叫号屏 / 门店Logo / 身份手机号 | 32 passed（修复后） |
| 测试_外卖营业时间 / 订单创建券 | 10 passed（修复后） |

---

## 5. 跳过用例说明（8 skipped）

跳过发生在需要真实外部依赖的用例，由测试内条件判断主动 skip（而非 error）：

- 需要真实 MySQL 方言的 DDL/锁行为（SKIP LOCKED、部分 CHECK）。
- 需要真实 Redis 连接的连接级行为（多数 Redis 交互已用 fakeredis/mock 覆盖，故跳过数很少）。
- 需要平台真实凭证/沙箱的端到端联调。

这些路径在单元/服务层已通过 mock 与契约测试覆盖逻辑正确性；真实环境验证列入阻塞项（见 REPAIR_TRACKER BLK-001/003）。

---

## 6. 历史限流设计决策（2026-09-29，已被后续实现取代）

本节只保留历史背景。当前 Redis 故障策略已调整为按路径分级：

- `RATE_LIMIT_FAIL_CLOSED_PATHS` 覆盖认证、支付、小程序登录/刷新、外卖回调等敏感路径；Redis 不可用时返回 503。
- 普通点餐/查询等路径仍 fail-open，避免限流 Redis 故障导致全站不可用。
- 生产启动校验会拒绝缺少必需敏感路径的配置。
- 当前行为由 `backend/app/core/rate_limit.py`、`backend/app/core/startup_checks.py` 和 `tests/测试_限流关闭失败.py` 定义；不要按本报告 2026-09 的旧结论操作。

---

## 7. 警告（warnings）分析

最终运行仅剩 2 类 warning，均不影响正确性：

1. `StarletteDeprecationWarning: Using httpx with starlette.testclient` — 测试_智能推荐_12性能治理.py 使用了旧的 TestClient 导入，建议后续迁移 httpx2（P3）。
2. `RuntimeWarning: coroutine AsyncMock ... was never awaited` — 测试_支付退款.py 中 outbox `begin_nested` mock 的一处协程未 await，属测试桩清理告警，断言结果正确；建议后续收紧 mock。

---

## 8. 覆盖主题清单（与台账对应）

- **P0 安全**：JWT 隔离、支付验签、状态机、SSRF、活体、413、PII、启动校验
- **资金**：支付/退款/组合支付/付款码/幂等/台账/余额/积分/等级降级
- **订单**：幂等、生命周期、超时关单、渠道伪造、KDS 准入、POS
- **多租户**：门店范围、跨店规格/餐品、并发隔离、桌台、审计可见范围
- **库存/券**：原子扣减防超卖、券并发发放、核销/释放、外卖镜像
- **可靠性**：Outbox 认领/退避/原子释放、Webhook 收件箱回收、Leader Lua 锁、定时任务跨进程锁
- **人脸/摄像头**：SSRF、活体随机挑战、凭证严格模式、功能开关、迎宾
- **客户端**：小程序登录、支付宝登录、401 分流、权限路由、门店 Logo/图片上传
- **性能/治理**：分页稳定性、筛选联动、夜间批处理、推荐性能、隐私合规

---

## 9. 未覆盖 / 覆盖薄弱项（诚实记录）

1. **真实 MySQL CI**：本历史报告日期时缺失；后续已增加 `backend-mysql` MySQL 8 service-container job，但生产 schema 对比仍需预生产执行（BLK-003）。
2. **真实平台端到端**：支付/外卖/小程序仍需用官方沙箱凭证跑全链路（BLK-001）。
3. **前端自动化**：本历史报告日期时缺少；后续已接入前端构建/单元测试 CI，完整 Playwright E2E 仍待排期。
4. **压测**：仓库提供核心链路压测脚本，但生产容量基线和摄像头/KDS/Outbox 联合压测仍需在预生产执行。
5. **告警链路**：metrics 端点存在，Prometheus/Grafana 规则与告警通知实测仍需运维接入（BLK-004）。

---

## 10. 结论

以上 **791 passed / 8 skipped / 0 failed** 仅为 2026-09-29 的历史 SQLite 测试记录。后续 CI 已加入 MySQL 8、Redis 7、前端构建/测试和依赖安全审计；当前 main 的实际通过情况必须按对应 commit 的 Actions 检查读取。生产沙箱联调、生产 schema 演练、备份恢复、告警接入与全链路容量验收仍属外部上线阻塞项。

---

## 2026-11-09 GitHub Actions 代码增量验证

### 本轮代码提交

- SHA：443c1e201212cb6044ebea5038b359e439a18ebd
- Actions run：42590160163
- 结果：12/12 jobs success
- 后端：2020 passed, 16 skipped in 678.90s
- 覆盖率：60.35%（CI 门槛 35%）
- 双端小程序：JS 语法、正式环境 fail-closed、订阅授权手势、支付宝隐私 API fail-closed，以及充值幂等 A→B→A / 精确清理 / 本地时间前进 30 天仍复用待处理键 / storage failure 专项测试通过。
- MySQL 8：空库 Alembic upgrade、downgrade 再 upgrade、schema/字符集检查通过。
- Redis 7.4：真实 Lua owner 锁、generation fencing、失效和旧缓存拒绝测试通过。
- 其他：前端 Node 24/26 构建、依赖锁一致性、Python/Rust 依赖安全审计、Windows Rust DLL 构建通过。

### 结果边界

以上结果只证明该 SHA 在 CI 环境中的自动化验证通过。16 个 skipped 仍须结合测试名称确认其是否为外部平台/真实凭证依赖；生产 MySQL 现有数据迁移、微信/支付宝官方沙箱、外卖平台回调、备份恢复、监控告警和预生产容量压测仍需人工验收。后续文档-only 提交也必须查看最新 PR head 的 Actions，不能沿用旧 SHA 的绿灯。

## 2026-11-09 后续测试增量（待 CI）

- 充值幂等回归已取消本地 24 小时 TTL 自动轮换断言，改为模拟本地时间前进 30 天仍必须复用原待处理键；原因是本地时钟不能证明渠道支付已经终态。
- 退款一致性新增当前读测试：`PaymentRefund` 汇总要求 `FOR UPDATE + populate_existing`；订单净已收聚合锁定 Payment 与 PaymentRefund；父行锁后刷新 Payment；在途 UNKNOWN 退款阻止再次创建；收款剩余金额计算可强制当前读。
- 关联提交：`4a98702eff3205c7685ba0c412ff1def5179efd3`。最新 CI 尚未完成，测试结果不得提前登记为通过。

- 即时付款码结算增加回调先成功或已进入部分/全额退款终态时不降级、不重复记账的回归；金额异常路径增加不降级终态、持久化 UNKNOWN/状态历史及充值锁序当前读测试；充值结算会刷新锁定后的支付/充值单状态，且拒绝把 REFUNDED 降级为 PAID。关联提交：`83388d8aaa6cc72f0e84dcb6ca0e5016e2200ae1`；等待最新 CI。

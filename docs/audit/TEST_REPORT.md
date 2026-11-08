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

## 2026-11-08 当前审查环境测试补充（不是全量通过报告）

审查基线：main @ 442beb4761b29f5b415830e91e06c97241f74ef3。

| 检查项 | 结果 | 解释 |
|---|---|---|
| python -m compileall -q backend/app | 通过 | 236 个后端 Python 文件未发现语法错误 |
| pytest --collect-only -q（默认 Python 3.13） | 未运行成功 | 当前解释器环境缺少 pytest |
| pytest -q --no-header（Python 3.11 / pytest 9.0.2） | 收集失败 | tests/conftest.py 导入 backend security 时缺少 argon2，尚未进入用例执行 |
| 仓库 pytest_out.txt | 历史失败记录 | 1 failed、23 passed；test_draft_publish_idempotent_pause 失败，需当前 main CI 复核 |
| PR #113 CI 历史记录 | 12/12 通过 | 包含 Redis 7.4、MySQL migration、Rust stable/1.89、Windows DLL、前端/小程序等检查；此为既有 PR 结果，不等同于本轮所有问题已通过 |

### 当前结论

- 本机 pytest 未能完成收集，不可表述为“测试全部通过”。
- 缺少 argon2 属于测试环境依赖未安装，但不能据此推断测试本身无问题。
- 历史 pytest_out.txt 的营销任务失败必须在当前 commit 的 CI 中复核；如已修复，应标注旧日志基线，不可静默删除失败历史。
- P0/P1 新发现项需补充独立回归测试，尤其是 APP_ENV/lifespan、日志脱敏、金额校验、Webhook 重试、WebSocket 授权、支付宝隐私授权和 destructive SQL 防护。
- 当前 CI 的真实执行结果以 GitHub Actions 对应 commit 的 job 日志为准；报告必须记录 SHA、时间、运行环境、命令、passed/failed/skipped。



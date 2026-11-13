> **文档维护说明（2026-11-08）**：部署和运维说明必须与当前 systemd、Nginx、生产脚本、启动校验和环境模板一致。本项目原生 Ubuntu 部署，不使用 Docker；历史配置或旧服务器状态不能视为当前生产状态。 当前事实以 main 代码、迁移、CI 和真实执行证据为准；历史快照不自动代表当前状态。禁止写入有效密钥、密码或 token。
>
> **人脸识别状态（2026-11-11）**：后端 `face_feature_gate.py` 的全局 hard-stop 当前固定为暂停。生产环境不得运行 InsightFace、人脸身份匹配或读取人脸档案；摄像头采集/视频流不代表识别功能已开放。任何恢复识别的变更都必须先完成模型商业授权确认、个人信息保护与法务审核、端到端测试，并经明确批准。
>
> ---
>
# Cwm9 部署与运维审计报告

> 审计日期：2026-11-06（本次部署与备份恢复条目复核）
> 审计范围：进程模型、Leader 选举、定时任务、反向代理、systemd、健康探针、日志、备份恢复、监控指标、发布回滚
> 审计方式：部署脚本与运维文档逐项复核；CI 结果以对应提交的 GitHub Actions 检查为准，不沿用历史测试计数

---

## 1. 总体架构

```
                    ┌──────────────┐
   HTTPS 客户端 ───▶ │   Nginx      │  TLS / 限流 / 上传大小 / WS 升级 / 静态资源
                    └──────┬───────┘
                           │ 127.0.0.1:8000
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
  Uvicorn worker#1   Uvicorn worker#2    （可按容量扩 worker）
  原生 spawn 进程      原生 spawn 进程
   FastAPI app        FastAPI app
        │                  │
        ├──── Redis ◀──────┘   Leader 锁 / 限流 / WS PubSub / 活体 token / 缓存
        ├──── MySQL ────────── 业务数据（连接池）
        └──── Leader 独占 ──── APScheduler / TTS worker / 摄像头采集
```

- ASGI 入口：[backend/app/main.py](../../backend/app/main.py) `create_app()`。
- 多 worker 通过 Redis Leader 锁保证单实例调度，避免重复任务/重复摄像头采集。

---

## 2. 进程与 Leader 选举

### 2.1 PROCESS_ROLE（[main.py:97-122](../../backend/app/main.py)）

| 角色 | 行为 |
|---|---|
| `auto`（默认/推荐） | 启动时抢 Redis Leader 锁；抢到跑调度，未抢到持续待命，Leader 宕机自动接管 |
| `leader` | 必须抢到锁才跑调度；抢不到告警并重试（不静默降级为 worker 语义） |
| `worker` | 永不跑调度，只服务 HTTP |

### 2.2 Leader 锁（[leader_lock.py](../../backend/app/core/leader_lock.py)）

- 获取：`SET key wid NX PX ttl`。
- 续期：Lua 脚本原子 compare-and-expire（仅 holder 可续）。
- 释放：Lua 脚本原子 compare-and-delete（仅 holder 可删，防误删新 Leader 的锁）。
- worker_id：`make_worker_id()`（host:pid:随机）。
- 非持锁进程起 standby promote 协程持续争抢，Leader 崩溃 TTL 过期后自动接管。
- 测试：测试_Leader锁原子性.py（释放/续期 Lua、不删他人锁）。

### 2.3 Leader 独占资源

- APScheduler（定时任务）。
- TTS worker（`asyncio.Queue(maxsize=100)` 有界）。
- 摄像头采集/识别（`CameraManager.set_leader_check`，P0-017）。
- 避免多 worker 重复叫号、重复人脸采集、重复定时任务。

---

## 3. 定时任务（[backend/tasks/scheduler.py](../../backend/app/tasks/scheduler.py)）

### 3.1 调度器配置

- `AsyncIOScheduler`，时区强制 `Asia/Shanghai`（防 UTC 星期/跨日错误，P1-411）。
- `coalesce=True`：积压多次触发合并为一次。
- `max_instances=1`：同进程内同任务不重叠。
- `misfire_grace_time=60`。

### 3.2 关键任务

| 任务 | 职责 |
|---|---|
| 超时未支付关单 | 调渠道 trade_cancel/close，释放库存券 |
| 夜间批处理 | 营业日结转、统计聚合（Asia/Shanghai 跨日） |
| PII 清理 | 30 天以上完成订单脱敏、访客抓拍清理 |
| 自动开关店 | 按 StoreHours 调外卖平台营业状态（FOR UPDATE SKIP LOCKED 逐店独立事务） |
| Outbox 重试 | 认领 PENDING/stale PROCESSING，指数退避 |
| Webhook 收件箱回收 | PROCESSING 超 5 分钟阈值回收（P1-105） |
| PrintJob 回收 | 卡死 PROCESSING 重新入队 |
| 分类时段自动排序 | 营业时段切换自动调整分类展示 |

### 3.3 跨进程/跨节点

- 即使 Leader 锁失效，任务内部也用 Redis 锁 / DB SKIP LOCKED 做二次互斥（测试_定时任务跨进程锁.py）。
- 逐店/逐单独立事务，单点失败不回滚整批。

---

## 4. 启动与就绪

### 4.1 启动顺序（lifespan）

1. `validate_settings(settings)`：13 类生产校验，失败 `SystemExit(1)`。
2. DB 探针 `SELECT 1`；失败生产 `SystemExit(1)`，开发仅报错继续。
3. system_settings 种子 + 预热进程缓存 + 默认 A/B 实验（失败仅 warning，可后台补）。
4. Leader 选举。
5. 启动 WS Redis subscriber。
6. 启动调度器 / TTS / 摄像头（仅 Leader）。

### 4.2 健康探针（[health.py](../../backend/app/api/health.py)）

- `/api/health`：liveness，进程存活即 200。
- `/api/ready`：readiness，探 DB + Redis，均可用才 200，供 nginx/负载均衡摘挂流量。
- 测试：测试_健康检查就绪.py。

### 4.3 systemd（[systemd/smart-noodle.service](../../systemd/smart-noodle.service)）

- `User/Group=www-data`，非 root。
- 绑定 `127.0.0.1:8000`，仅 nginx 反代对外。
- `PROCESS_ROLE=auto` 在单元层强制覆盖，防止 .env 误写 leader 导致多实例冲突。
- Uvicorn 原生多进程 `--workers 2`，`--timeout-graceful-shutdown 30`；不依赖 Gunicorn worker 插件。
- `NoNewPrivileges=true`、`PrivateTmp=true`、`ReadWritePaths` 限定 uploads/logs。
- `Restart=on-failure`，StartLimitBurst=5/60s 防崩溃循环。
- 占位符 `REPLACE_DEPLOY_ROOT` 部署前替换。

---

## 5. Nginx（[nginx/smart-noodle.conf](../../nginx/smart-noodle.conf)）

- HTTPS：TLS 参数抽离到 nginx/snippets/ssl-params.conf（TLS 1.2/1.3、强加密套件）。
- 代理通用参数：snippets/proxy-common.conf（X-Forwarded-*、超时）。
- 上传大小限制（与 Webhook 512KB / 图片上传独立配置区分）。
- WebSocket：`/api/ws` 路径升级头 + 更长 read timeout 支撑长连接。
- 静态：前端构建产物与 uploads 直发，不经过 Python。
- 反代 IP 与后端 `TRUSTED_PROXY_IPS` 联动，后端仅信任该列表写入的 XFF。
- 限流：可在 nginx 层叠加全局限流，后端按路径细分。

---

## 6. 配置与密钥管理

### 6.1 配置优先级

[config.py](../../backend/app/core/config.py) pydantic-settings，env_file 候选：项目根 `.env` → `backend/.env` → 当前目录；环境变量最高优先。

### 6.2 启动校验（生产强制）

详见 [BASELINE.md §7.2](../../docs/audit/BASELINE.md)，关键项：

- 强 JWT/FACE/SECRET 密钥（非默认、熵足够、长度≥32）。
- 禁止所有 MOCK 开关、APP_DEBUG；CORS/H5 不得指向 localhost。
- DATABASE_URL / REDIS_URL 默认禁止 loopback；单机同机部署可显式设置 `ALLOW_LOOPBACK_PRODUCTION_SERVICES=true`，但 MySQL/Redis 必须仅监听 loopback、启用认证、使用最小权限账号，且安全组/防火墙不得开放 3306/6379。
- Redis URL 必须配置密码；敏感认证/支付/回调路径必须保留 `RATE_LIMIT_FAIL_CLOSED_PATHS`，Redis 故障时这些路径返回 503。
- Webhook IP allowlist 可按 `WEBHOOK_REQUIRE_ALLOWLIST` 显式决定是否启用；不启用 IP allowlist 不代表可跳过平台验签、商户身份、金额/订单校验和幂等。
- 支付通道与平台证书非占位符。不合规直接拒绝启动（SystemExit(1)），不留“带病上线”窗口。

### 6.3 密钥清单与轮换

- `JWT_SECRET`：会话签名（HS256）。
- `FACE_ENCRYPTION_KEY` / `_PREVIOUS`：人脸向量加密，支持新旧双密钥轮换。
- `SECRET_ENCRYPTION_KEY`：平台 Token 加密。
- `WECHAT_API_V3_KEY` / `WECHAT_API_V2_KEY`：V2/V3 独立。
- `WECHAT_PLATFORM_PUBLIC_KEY` / 证书路径。
- `.env*` 全部 gitignore；example 仅占位符。
- 轮换指南：[docs/安全说明.md](../../docs/安全说明.md)。
- **BLK-002**：git 历史曾出现真实密钥，上线前必须在平台控制台轮换。

---

## 7. 连接池与容量

- DB：`pool_size + max_overflow` 显式配置；要求 `Uvicorn worker 数 × (pool_size+overflow) ≤ MySQL max_connections`（P1-401，config 注释明示）。
- `pool_pre_ping=True` + `pool_recycle` 剔除失效连接。
- Redis：每个 API Worker 独立使用 `BlockingConnectionPool`，默认每进程上限 50、池耗尽最多等待 0.75 秒；总连接上限约 `Uvicorn Worker 数 × REDIS_MAX_CONNECTIONS`，另需预留运维/监控/其他服务连接。WebSocket Pub/Sub 从对应 Worker 的连接池借用连接；不得按“全应用单连接”估算。
- 当前 Redis 混合承载缓存、限流、一次性凭证、活体挑战和分布式锁：生产基线使用显式 `maxmemory` + `maxmemory-policy noeviction`，避免淘汰协调/安全键；达到上限时写入会返回 OOM，关键路径必须按各自策略 fail-closed 或安全降级。纯缓存如需 LRU，应拆分独立实例。
- Redis 主机需设置 `vm.overcommit_memory=1`，关闭 Transparent Huge Pages，并监控 `INFO memory`、`INFO stats`、`INFO persistence`、磁盘空间及 AOF/RDB 状态。`redis.conf.example` 的 512mb 仅为小型单店示例，部署前必须按主机 RAM、MySQL/OS 占用和 fork/COW 峰值重新核算。
- 摄像头/支付外部调用统一走 [http_client.py](../../backend/app/core/http_client.py) 共享 httpx client，lifespan 关闭，防连接泄漏。
- TTS 队列有界（100）防内存膨胀（P1-402）。

---

## 8. 可靠性机制

### 8.1 Transactional Outbox（[outbox_worker.py](../../backend/app/services/outbox_worker.py)）

- 支付副作用与业务同事务写 OutboxEvent。
- claim：条件 UPDATE 原子认领并分配唯一 owner token；处理期间独立短事务续租；超 max_attempts → FAILED 人工介入。
- claim 统计按实际原子认领结果计算，不依赖处理前后状态推断。
- handler 必须幂等；外部 HTTP 副作用仍需平台侧幂等键/状态对账，DB 租约不能撤销已发出的网络请求。
- 释放/失败标记均为原子 `UPDATE ... WHERE locked_by=wid`，reclaim 后不会覆盖新 owner。
- 旁路幂等：MySQL 同事务落库 → commit → 再标 Redis，避免回滚产生 Redis 脏键。
- 测试：测试_Outbox工作者.py、测试_P2认领修复.py、测试_消息幂等与域模块.py。

### 8.2 Webhook / PrintJob 回收

- WebhookInbox PROCESSING 超 5 分钟视为 stale 可重新认领（P1-105）。
- PrintJob PROCESSING 超时回收 + DB 幂等约束防重复打印。

### 8.3 幂等与补偿

- 支付回调：Redis claim_once + DB 唯一约束 + 状态机。
- 退款：幂等键 + 行锁 + Outbox 补偿积分/等级。
- 外卖：refund_id 去重 + savepoint；平台拒绝不置 APPROVED。

### 8.4 WebSocket 可靠性

- 跨 worker 经 Redis Pub/Sub 广播（[ws_pubsub.py](../../backend/app/services/ws_pubsub.py)）。
- KDS 断线重连可补拉，不仅依赖进程内存事件（P1-312）。
- 连接数/订阅频率配额防 DoS（ws_conn_quota.py）。

---

## 9. 日志与可观测性

### 9.1 日志

- [logging_config.py](../../backend/app/core/logging_config.py)：控制台 + 按端分目录文件（backend/frontend/wechat/alipay）。
- request_id / trace_id 贯穿（RequestIDMiddleware）。
- Webhook 请求头脱敏 Authorization/Cookie/token（P0-021）。
- 前端日志上报 /client-logs；页面卸载用 sendBeacon，HMR dispose 清理，401/403/中断不重试，避免错误风暴（项目历史教训）。

### 9.2 指标

- PerformanceMetricsMiddleware 记录耗时/状态。
- `/api/performance` 查询性能指标（[performance.py](../../backend/app/api/performance.py)）。
- metrics 端点已就绪，但 Prometheus 抓取/告警规则需运维接入（BLK-004）。

### 9.3 审计

- AuditService 多通道（staff/intrusion/danger/face/customer），敏感字段脱敏。
- 审计日志按门店可见范围隔离。

---

## 10. 存储与上传

- 开发：本地 `uploads/` 保存普通公开上传文件；顾客头像与访客抓拍不得通过静态目录匿名直出，头像统一经短时签名媒体路由读取。
- 生产：`STORAGE_BACKEND=s3`（`backend/app/services/object_storage.py`），必须配置 `S3_BUCKET`；MinIO/兼容服务通常配置 `S3_ENDPOINT`。AWS S3 可留空 Endpoint，按 `S3_REGION` 使用 SDK 默认端点；`S3_ACCESS_KEY` 与 `S3_SECRET_KEY` 必须同时填写或同时留空，后者使用 AWS 标准凭证链（建议实例/任务 IAM Role），不得把长期密钥硬编码到部署文件。
- 依赖：当前 `boto3` 同时列在 `backend/requirements.txt` 与完整生产锁文件中，因此默认运行时安装也会安装 boto3；不要将其描述为可选依赖。若未来拆分 S3 extra，必须同步更新锁文件生成、Ubuntu 部署安装命令和 CI。
- 私有媒体：顾客头像不能使用公开 CDN/匿名媒体路由；短时 URL 不得写入数据库，持久化稳定对象路径，响应时再签发。访客抓拍/人脸图像仍默认禁止公开访问。
- 集成测试：CI 启动临时 MinIO 并运行 `tests/test_s3_object_storage_minio.py`；本地可先启动 MinIO，再设置 `S3_TEST_ENDPOINT`、`S3_TEST_ACCESS_KEY`、`S3_TEST_SECRET_KEY` 并执行 `PYTHONPATH=backend python -m pytest -q tests/test_s3_object_storage_minio.py`。
  - CI 镜像固定为 `dappros/minio@sha256:a1a8bd4ac40ad7881a245bab97323e18f971e4d4cba2c2007ec1bedd21cbaba2`（对应 MinIO `RELEASE.2025-09-07T16-13-09Z` 的重新发布副本），仅用于一次性集成测试；生产环境应使用经组织审核、固定 digest 并纳入镜像供应链管理的 S3 服务镜像，不直接复用该 CI 镜像。
- 图片：门店 Logo 上传转 WebP、拒绝外链/ GIF/过小（测试_门店Logo.py）；餐品图 URL 白名单。
- 人脸：仅摄像头实拍，base64 大小+像素双限，decompression bomb 防护。

---

## 11. 发布与回滚建议

### 11.1 发布流程（建议固化）

1. 备份数据库（mysqldump 或快照）。
2. `alembic upgrade head --sql` 离线比对生产 schema（BLK-003）。
3. 低峰期执行 `alembic upgrade head`（大表索引走 0007 幂等风格）。
4. 灰度：先升级 1 个 worker（PROCESS_ROLE=auto），观察 `/api/ready` 与日志。
5. 滚动重启其余 worker；Leader 切换自动发生。
6. 验证关键路径：登录/下单/支付回调/退款/厨房/打印。

### 11.2 回滚

- 应用回滚：systemd 切回旧版本，`Restart=on-failure` 与 start limit 保护。
- DB 回滚：资金类迁移 downgrade 需人工评审；优先采用"向前修复"。
- 配置回滚：保留上一版 `.env`（不入 git，由运维密钥管理系统保管）。

### 11.3 数据库备份恢复（脚本已提供，真实演练待完成）

仓库已提供以下工具：
- `scripts/备份数据库.py`：调用 mysqldump 生成 gzip 备份，并按保留天数清理旧备份。
- `scripts/恢复数据库.py`：将 SQL/gzip 备份恢复到当前 `DATABASE_URL` 指定的数据库；必须显式传入 `--yes`。
- `scripts/验证数据库.py`：检查关键表、字段、索引和 Alembic 版本。
- `scripts/备份恢复演练.py`：先备份，再通过独立 `DRILL_DATABASE_URL` 恢复并验证演练库。

备份账号权限边界（与 `scripts/备份数据库.py` 当前参数一致）：
- `--single-transaction` 对 InnoDB 一致性备份通常不要求 `LOCK TABLES`；`--no-tablespaces` 避免为 tablespace 元数据额外要求全局 `PROCESS`；`--set-gtid-purged=OFF` 不请求 GTID 复制元数据。
- 表数据需要目标 schema 的 `SELECT`；视图需要 `SHOW VIEW`；触发器需要对应表的 `TRIGGER`。
- 当前脚本保留 `--events`，因此还需要目标 schema 级 `EVENT` 权限（例如 `GRANT EVENT ON \\`数据库名\\`.* TO '备份账号'@'来源主机';`）。不要授予 `EVENT ON *.*` 或 `ALL PRIVILEGES`。只有在确认目标 schema 不包含任何需要恢复的 Event Scheduler 事件，并明确接受不备份这些事件后，才可移除 `--events`；代码/迁移未定义事件不能证明生产库没有 DBA 手工创建的事件。
- 上述是按当前 mysqldump 选项列出的对象读取权限边界；若未来增加选项（例如 `--flush-logs`、`--master-data`、`--routines`），必须重新核对其额外权限，不能沿用此清单。

注意：
- `--restore-drill` 必须配置独立演练库凭证；不得指向生产库。脚本的 host/port/database 比较只能阻止明显误配置，不能证明两个 DNS 地址背后不是同一数据库实例。生产操作前还必须从数据库侧确认目标实例身份、账号权限和网络隔离。
- 恢复脚本具有覆盖目标库的破坏性，生产恢复必须经过审批并确认目标。
- 脚本存在不等于备份策略已经投产。仍需配置定时全量备份、binlog/PITR、异地加密存储、备份完整性校验、保留策略和定期真实恢复演练。

---

## 12. 部署方式边界

- 当前生产部署目标为原生 Ubuntu + Nginx + systemd + MySQL + Redis，不使用 Docker；`deploy/nginx.conf` 仅为迁移提示，不是容器编排或可直接部署的 Docker 配置。
- 生产密钥通过受控环境文件注入，`.env` 不得进入版本库、构建产物或公开镜像。
- 多 worker 由 systemd 启动 Uvicorn 原生多进程管理器，所有进程使用 `PROCESS_ROLE=auto`，通过 Redis Leader 锁选出单一调度 Leader；这不是 Docker 多副本部署说明。
- 摄像头硬件能力仅应在实际连接摄像头的门店主机按 Leader 角色验收；纯云端部署应关闭相关硬件功能并按 `PROCESS_ROLE=worker` 运行。
- 如未来引入 Docker/容器编排，必须单独完成镜像供应链、非 root 用户、密钥注入、持久化存储、健康探针、Leader 锁和摄像头设备映射审查，不得将其视为当前已支持的生产部署方式。

---

## 13. 运维检查清单（上线前）

- [ ] 平台控制台轮换历史泄露密钥并更新生产 `.env`（BLK-002）
- [ ] `validate_settings` 在生产配置上通过（无 SystemExit）
- [ ] DATABASE_URL/REDIS_URL 使用受控私网地址且账号最小权限；单机 loopback 须显式 opt-in，MySQL/Redis 仅绑定 loopback、Redis 启用认证、云安全组/防火墙不开放 3306/6379
- [ ] Redis `maxmemory` / `noeviction`、`vm.overcommit_memory=1`、THP、AOF/RDB 状态、磁盘告警及内存峰值预算已实测
- [ ] `alembic upgrade head` 在预生产演练通过
- [ ] nginx TLS 证书有效、HSTS 开启、仅反代 127.0.0.1:8000
- [ ] `/api/health`、`/api/ready` 接入负载均衡/监控
- [ ] TRUSTED_PROXY_IPS 配置为真实 nginx 地址
- [ ] S3 凭证/桶/CDN 配齐（如使用对象存储）
- [ ] 数据库定时备份、binlog/PITR、异地加密副本、完整性校验和独立隔离库恢复演练；记录 RTO/RPO
- [ ] 生产 schema 对比：执行 alembic upgrade head --sql，与生产实际 schema/Alembic 版本逐项核对（BLK-003）
- [ ] Prometheus 抓取配置、Grafana 面板、关键告警阈值和通知通道实测（BLK-004）
- [ ] 微信/支付宝/美团/饿了么沙箱真实联调：支付成功/失败/超时、退款、重复/乱序回调、签名失败、平台限流及结果未知人工对账（BLK-001）
- [ ] 小程序隐私合规、类目/支付能力、微信/支付宝小程序审核和正式发布（BLK-005）
- [ ] 全链路压测并记录容量基线：API Worker、DB/Redis 连接池、Outbox 积压、KDS/WebSocket、摄像头/TTS 队列

---

## 14. 遗留运维事项

1. 备份/恢复/结构验证/隔离库演练脚本已内建；生产定时备份、binlog/PITR、异地加密副本、真实恢复演练与 RTO/RPO 验证仍待运维完成。
2. metrics 端点缺配套 Prometheus 规则与告警（BLK-004）。
3. 无真实生产 schema 对比报告（BLK-003）。
4. 全链路压测与容量基线（worker 数/连接池/DB 连接数）未形成报告。
5. 蓝绿/金丝雀发布流程依赖人工，未提供自动化脚本。

---

## 15. 结论

进程模型（Leader 锁 + worker 角色）、调度互斥、Outbox/收件箱回收、健康探针、非 root systemd、nginx TLS/WS/限流等部署控制点在代码与模板中落实，并由相应可靠性测试覆盖。代码层面的发布控制已通过 CI；但“已合并 main”不等于“已完成生产上线”。生产启用前仍须完成 BLK-001 沙箱真实联调、BLK-002 历史密钥轮换、BLK-003 生产 schema 对比、BLK-004 监控告警接入、BLK-005 小程序提审，以及真实备份恢复演练和容量压测。以上依赖平台账号、生产环境或运维操作，不能由仓库 CI 代替；任一资金/支付/回调链路未完成沙箱验证时，不得开放真实交易。

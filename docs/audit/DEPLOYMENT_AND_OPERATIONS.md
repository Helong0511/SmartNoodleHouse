# Cwm9 部署与运维审计报告

> 审计日期：2026-09-29
> 审计范围：进程模型、Leader 选举、定时任务、反向代理、systemd、健康探针、日志、备份恢复、监控指标、发布回滚
> 审计方式：部署模板/进程模型代码走读 + 可靠性测试（791 passed, 8 skipped）

---

## 1. 总体架构

```
                    ┌──────────────┐
   HTTPS 客户端 ───▶ │   Nginx      │  TLS / 限流 / 上传大小 / WS 升级 / 静态资源
                    └──────┬───────┘
                           │ 127.0.0.1:8000
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
  gunicorn worker#1  gunicorn worker#2   （可水平扩 worker）
  UvicornWorker      UvicornWorker
   FastAPI app        FastAPI app
        │                  │
        ├──── Redis ◀──────┘   Leader 锁 / 限流 / WS PubSub / 活体 token / 缓存
        ├──── MySQL ────────── 业务数据（连接池）
        └──── Leader 独占 ──── APScheduler / TTS worker / 摄像头采集
```

- ASGI 入口：[backend/app/main.py](file:///e:/SmartNoodleHouse/backend/app/main.py) `create_app()`。
- 多 worker 通过 Redis Leader 锁保证单实例调度，避免重复任务/重复摄像头采集。

---

## 2. 进程与 Leader 选举

### 2.1 PROCESS_ROLE（[main.py:97-122](file:///e:/SmartNoodleHouse/backend/app/main.py)）

| 角色 | 行为 |
|---|---|
| `auto`（默认/推荐） | 启动时抢 Redis Leader 锁；抢到跑调度，未抢到持续待命，Leader 宕机自动接管 |
| `leader` | 必须抢到锁才跑调度；抢不到告警并重试（不静默降级为 worker 语义） |
| `worker` | 永不跑调度，只服务 HTTP |

### 2.2 Leader 锁（[leader_lock.py](file:///e:/SmartNoodleHouse/backend/app/core/leader_lock.py)）

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

## 3. 定时任务（[backend/tasks/scheduler.py](file:///e:/SmartNoodleHouse/backend/tasks/scheduler.py)）

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

### 4.2 健康探针（[health.py](file:///e:/SmartNoodleHouse/backend/app/api/v1/health.py)）

- `/api/health`：liveness，进程存活即 200。
- `/api/ready`：readiness，探 DB + Redis，均可用才 200，供 nginx/负载均衡摘挂流量。
- 测试：测试_健康检查就绪.py。

### 4.3 systemd（[systemd/smart-noodle.service](file:///e:/SmartNoodleHouse/systemd/smart-noodle.service)）

- `User/Group=www-data`，非 root。
- 绑定 `127.0.0.1:8000`，仅 nginx 反代对外。
- `PROCESS_ROLE=auto` 在单元层强制覆盖，防止 .env 误写 leader 导致多实例冲突。
- gunicorn `-w 2`，`uvicorn.workers.UvicornWorker`，timeout 120s，graceful 30s。
- `NoNewPrivileges=true`、`PrivateTmp=true`、`ReadWritePaths` 限定 uploads/logs。
- `Restart=on-failure`，StartLimitBurst=5/60s 防崩溃循环。
- 占位符 `REPLACE_DEPLOY_ROOT` 部署前替换。

---

## 5. Nginx（[nginx/smart-noodle.conf](file:///e:/SmartNoodleHouse/nginx/smart-noodle.conf)）

- HTTPS：TLS 参数抽离到 snippets/ssl-common.conf（TLS 1.2/1.3、强加密套件）。
- 代理通用参数：snippets/proxy-common.conf（X-Forwarded-*、超时）。
- 上传大小限制（与 Webhook 512KB / 图片上传独立配置区分）。
- WebSocket：`/api/ws` 路径升级头 + 更长 read timeout 支撑长连接。
- 静态：前端构建产物与 uploads 直发，不经过 Python。
- 反代 IP 与后端 `TRUSTED_PROXY_IPS` 联动，后端仅信任该列表写入的 XFF。
- 限流：可在 nginx 层叠加全局限流，后端按路径细分。

---

## 6. 配置与密钥管理

### 6.1 配置优先级

[config.py](file:///e:/SmartNoodleHouse/backend/app/core/config.py) pydantic-settings，env_file 候选：项目根 `.env` → `backend/.env` → 当前目录；环境变量最高优先。

### 6.2 启动校验（生产强制）

详见 [BASELINE.md §7.2](file:///e:/SmartNoodleHouse/docs/audit/BASELINE.md)，关键项：

- 强 JWT/FACE/SECRET 密钥（非默认、熵足够、长度≥32）。
- 禁止所有 MOCK 开关、APP_DEBUG、localhost DB/CORS/H5。
- 支付通道与平台证书非占位符。
- 不合规直接拒绝启动（SystemExit(1)），不留"带病上线"窗口。

### 6.3 密钥清单与轮换

- `JWT_SECRET`：会话签名（HS256）。
- `FACE_ENCRYPTION_KEY` / `_PREVIOUS`：人脸向量加密，支持新旧双密钥轮换。
- `SECRET_ENCRYPTION_KEY`：平台 Token 加密。
- `WECHAT_API_V3_KEY` / `WECHAT_API_V2_KEY`：V2/V3 独立。
- `WECHAT_PLATFORM_PUBLIC_KEY` / 证书路径。
- `.env*` 全部 gitignore；example 仅占位符。
- 轮换指南：[docs/安全说明.md](file:///e:/SmartNoodleHouse/docs/安全说明.md)。
- **BLK-002**：git 历史曾出现真实密钥，上线前必须在平台控制台轮换。

---

## 7. 连接池与容量

- DB：`pool_size + max_overflow` 显式配置；要求 `gunicorn -w × (pool_size+overflow) ≤ MySQL max_connections`（P1-401，config 注释明示）。
- `pool_pre_ping=True` + `pool_recycle` 剔除失效连接。
- Redis：每个 API Worker 独立使用 `BlockingConnectionPool`，默认每进程上限 50、池耗尽最多等待 0.75 秒；总连接上限约 `Gunicorn Worker 数 × REDIS_MAX_CONNECTIONS`，另需预留运维/监控/其他服务连接。WebSocket Pub/Sub 从对应 Worker 的连接池借用连接；不得按“全应用单连接”估算。
- 摄像头/支付外部调用统一走 [http_client.py](file:///e:/SmartNoodleHouse/backend/app/core/http_client.py) 共享 httpx client，lifespan 关闭，防连接泄漏。
- TTS 队列有界（100）防内存膨胀（P1-402）。

---

## 8. 可靠性机制

### 8.1 Transactional Outbox（[outbox_worker.py](file:///e:/SmartNoodleHouse/backend/app/services/outbox_worker.py)）

- 支付副作用与业务同事务写 OutboxEvent。
- claim：`FOR UPDATE SKIP LOCKED`；处理：指数退避；超 max_attempts → FAILED 人工介入。
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

- 跨 worker 经 Redis Pub/Sub 广播（[ws_pubsub.py](file:///e:/SmartNoodleHouse/backend/app/services/ws_pubsub.py)）。
- KDS 断线重连可补拉，不仅依赖进程内存事件（P1-312）。
- 连接数/订阅频率配额防 DoS（ws_conn_quota.py）。

---

## 9. 日志与可观测性

### 9.1 日志

- [logging_config.py](file:///e:/SmartNoodleHouse/backend/app/core/logging_config.py)：控制台 + 按端分目录文件（backend/frontend/wechat/alipay）。
- request_id / trace_id 贯穿（RequestIDMiddleware）。
- Webhook 请求头脱敏 Authorization/Cookie/token（P0-021）。
- 前端日志上报 /client-logs；页面卸载用 sendBeacon，HMR dispose 清理，401/403/中断不重试，避免错误风暴（项目历史教训）。

### 9.2 指标

- PerformanceMetricsMiddleware 记录耗时/状态。
- `/api/performance` 查询性能指标（[performance.py](file:///e:/SmartNoodleHouse/backend/app/api/v1/performance.py)）。
- metrics 端点已就绪，但 Prometheus 抓取/告警规则需运维接入（BLK-004）。

### 9.3 审计

- AuditService 多通道（staff/intrusion/danger/face/customer），敏感字段脱敏。
- 审计日志按门店可见范围隔离。

---

## 10. 存储与上传

- 开发：本地 `uploads/` StaticFiles 直发。
- 生产：`STORAGE_BACKEND=s3`（[object_storage.py](file:///e:/SmartNoodleHouse/backend/app/services/object_storage.py)），启动校验要求凭证/桶配齐，否则拒绝启动。
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

### 11.3 数据库备份恢复（待运维完善）

- 当前 sql/ 提供初始化与对账脚本，未内置自动备份脚本。
- 建议：每日全量 + binlog 增量，定期在隔离环境演练恢复（REMAINING_ISSUES P1）。

---

## 12. 容器化说明

- 仓库存在部署模板与 Dockerfile 引用（BASELINE §1），生产密钥通过环境变量注入，镜像不含 `.env`。
- 多副本部署时全部副本 `PROCESS_ROLE=auto`，由 Redis 锁选出单 Leader。
- 摄像头硬件相关能力仅适合在带摄像头的门店主机以 Leader 运行；纯云端副本应 `PROCESS_ROLE=worker` 且不配置摄像头。

---

## 13. 运维检查清单（上线前）

- [ ] 平台控制台轮换历史泄露密钥并更新生产 `.env`（BLK-002）
- [ ] `validate_settings` 在生产配置上通过（无 SystemExit）
- [ ] DATABASE_URL/REDIS_URL 指向非 localhost 且账号最小权限
- [ ] `alembic upgrade head` 在预生产演练通过
- [ ] nginx TLS 证书有效、HSTS 开启、仅反代 127.0.0.1:8000
- [ ] `/api/health`、`/api/ready` 接入负载均衡/监控
- [ ] TRUSTED_PROXY_IPS 配置为真实 nginx 地址
- [ ] S3 凭证/桶/CDN 配齐（如使用对象存储）
- [ ] 数据库备份 + 恢复演练
- [ ] Prometheus/Grafana/告警通道接入（BLK-004）
- [ ] 微信/支付宝/美团/饿了么沙箱真实联调（BLK-001）
- [ ] 小程序提审（BLK-005）

---

## 14. 遗留运维事项

1. 自动备份/恢复脚本与演练流程未内建（P1）。
2. metrics 端点缺配套 Prometheus 规则与告警（BLK-004）。
3. 无真实生产 schema 对比报告（BLK-003）。
4. 全链路压测与容量基线（worker 数/连接池/DB 连接数）未形成报告。
5. 蓝绿/金丝雀发布流程依赖人工，未提供自动化脚本。

---

## 15. 结论

进程模型（Leader 锁 + worker 角色）、调度互斥、Outbox/收件箱回收、健康探针、非 root systemd、nginx TLS/WS/限流等部署控制点在代码与模板中落实，并由相应可靠性测试覆盖。主要缺口在运维侧：密钥轮换确认、备份恢复演练、监控告警接入与真实沙箱联调，均已列入阻塞/遗留清单，不影响代码层面的部署正确性结论。

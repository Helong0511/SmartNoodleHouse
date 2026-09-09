# 部署指南

本文说明从空库到可服务的推荐步骤。真实密钥只放在本地 `.env`（已 gitignore），勿写入仓库。

## 前置

- MySQL 8.0+、Redis、Python 3.11+、Node.js 18+
- 生产建议 Nginx + HTTPS；API 仅本机监听（如 `APP_HOST=127.0.0.1`）

## 1. 配置环境

```bash
cp .env.production.example .env   # 或开发用 .env.development.example
# 编辑 .env：DATABASE_URL、REDIS_URL、JWT_SECRET、FACE_ENCRYPTION_KEY、
# WECHAT_* / ALIPAY_*、CORS_ORIGINS、H5_PUBLIC_BASE_URL 等
```

关键生产项：

| 变量 | 说明 |
|------|------|
| `APP_ENV=production` | 启用启动强校验 |
| `PROCESS_ROLE` | `leader` 跑定时任务 / TTS / 识别；其余实例必须 `worker`。全机仅一个 leader |
| `TRUSTED_PROXY_IPS` | 反代直连 IP（逗号分隔，支持 CIDR）。仅这些 peer 的 `X-Real-IP` / `X-Forwarded-For` 会被信任 |
| `WEBHOOK_REQUIRE_ALLOWLIST` | 生产建议 `true`，并配置平台出口 IP |
| `ALLOW_*_MOCK` | 生产全部 `false` |

生成随机密钥示例（勿把输出提交到仓库）：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## 2. 数据库 Schema（推荐 Alembic）

在已创建空库（或已授权的库）上：

```bash
cd backend
# 使用与运行时相同的 DATABASE_URL（.env）
alembic upgrade head
```

- `0001_baseline`：执行仓库根目录 `sql/001_schema.sql`（跳过 `CREATE DATABASE` / `USE`）
- `0002_p0_hardening`：补齐 `orders.operator_id`、webhook / 退款重试字段、`financial_ledger` / `point_ledger`（已存在则跳过）
- `0003_p1_hardening`：欢迎券 `UNIQUE(customer_id, template_id)`
- `0004_p1_shift_open`：收银班次每人仅一个 OPEN
- `0005_p1_merge_print`：`customer_coupons.status_reason`（合并冲突标记）
- `0006_print_jobs`：厨房打印任务表 `print_jobs`（失败指数退避重试）

等价手工路径（不推荐与 Alembic 混用同一库反复执行）：

```bash
mysql ... < sql/001_schema.sql
# 再 stamp / upgrade 使 alembic_version 与真实结构一致
```

## 3. 种子数据与超管

```bash
# 生产整库种子（会 TRUNCATE 业务表，勿对有真实数据的库执行）
mysql ... < sql/002_seed_prod.sql

# 或按模块幂等种子（可叠加）
python scripts/seed_roles.py
python scripts/seed_permissions.py
python scripts/seed_member_levels.py
python scripts/seed_coupon_templates.py

# 创建 / 重置超管密码（须显式设置强密码）
set ADMIN_INITIAL_PASSWORD=YourStrongPass123!   # Windows
# export ADMIN_INITIAL_PASSWORD='...'           # Linux
python scripts/init_admin.py
```

校验：

```bash
python scripts/verify_database.py
```

## 4. 后端与前端

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
# 开发: uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
# 生产: gunicorn 等（见 README）

cd ../frontend
npm ci && npm run build
```

## 5. Nginx / 多实例注意

- 反代到本机 API；上传目录与静态 `frontend/dist` 分离挂载。
- 多 Gunicorn worker 时：**只有一个进程 / 一台机** 设 `PROCESS_ROLE=leader`。
- `TRUSTED_PROXY_IPS` 填 Nginx 所在网卡地址（常见 `127.0.0.1`）；若 API 与反代跨机，填反代出口 IP，勿填 `0.0.0.0/0`。

## 6. 上线检查清单

- [ ] `alembic upgrade head` 成功，`verify_database.py` 通过
- [ ] `init_admin.py` 已设强密码
- [ ] 支付 / 小程序 / 外卖密钥为占位符已替换，且曾泄露密钥已在平台控制台轮换（见 [security.md](security.md)）
- [ ] 回调 URL 为 HTTPS，且白名单生效
- [ ] 仅一个 `PROCESS_ROLE=leader`

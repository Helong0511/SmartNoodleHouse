# 面馆智能经营系统

微信 / 支付宝小程序扫码点餐 + 统一订单 + 厨房 KDS + 会员 / 人脸迎宾 + 美团 / 饿了么外卖 + 后台 RBAC

## 技术栈

- 后端: Python 3.11+, FastAPI, SQLAlchemy 2.x, MySQL 8, Redis 7, APScheduler
- 后台前端: Vue 3, TypeScript, Vite, Element Plus
- 微信小程序: `miniprogram-wechat/`
- 支付宝小程序: `miniprogram-alipay/`
- 摄像头: OpenCV（海康 RTSP）
- 实时: WebSocket（厨房 / 订单）

> **不使用 Docker**。请在本机安装 MySQL、Redis、Python、Node.js 后按下方步骤启动。

## 功能概览

| 模块 | 能力 |
|------|------|
| 堂食 | 微信 / 支付宝扫码点餐、服务端计价、支付回调验签 |
| 厨房 KDS | 实时 WebSocket、外卖催单 / 退款高亮、出餐回传平台 |
| 会员 | 注册、口味画像、一键复购 |
| 人脸迎宾 | 仅主动授权、多帧确认、冷却、TTS 播报 |
| 外卖 | 美团 / 饿了么：新单、取消、退款、催单、配送、隐私号、商品同步 |
| 后台 | 商品 / 规格 / 订单 / 桌台 / 支付 / 摄像头 / **外卖管理** / RBAC |

## 本地安装与启动

### 1. 准备环境

- Python 3.11+
- Node.js 18+
- MySQL 8
- Redis 7
- 微信 / 支付宝小程序开发者工具（可选）

### 2. 数据库

按顺序执行：

```bash
mysql -u root -p < sql/000_create_db_grant.sql   # 可选：建库+授权
mysql -u root -p < sql/001_schema.sql
mysql -u root -p < sql/002_seed_test.sql
python scripts/init_admin.py
```

| 脚本 | 说明 |
|------|------|
| `000_create_db_grant.sql` | 建库与授权（需 root） |
| `001_schema.sql` | 全量表结构（最终态 DDL） |
| `002_seed_test.sql` | 测试/开发种子数据 |
| `tools/003_capacity_audit.sql` | 容量审计（运维工具，非迁移） |
| `archive/` | 历史增量脚本归档 |

默认管理员: `admin` / `admin123`

### 3. 后端

```bash
cd backend
pip install -r requirements.txt
copy ..\.env.example ..\.env   # Windows
# cp ../.env.example ../.env   # Linux/macOS
uvicorn app.main:app --reload --app-dir .
```

API 文档: http://127.0.0.1:8000/docs

### 4. 后台管理前端

```bash
cd frontend
npm install
npm run dev
```

浏览器打开: http://127.0.0.1:5173

侧边栏含：**厨房 KDS**、**外卖管理**（订单 / 退款 / 门店绑定 / 商品映射 / 回调日志）。

### 5. 微信小程序

1. 用微信开发者工具打开 `miniprogram-wechat`
2. 填写 AppID（或测试号），开发阶段可关闭域名校验
3. 修改 `app.js` 中 `apiBase` 为后端地址
4. 扫桌台二维码，或编译参数: `token=桌台token&channel=wechat`

### 6. 支付宝小程序

1. 打开 `miniprogram-alipay`，填写 AppID
2. 修改 `app.js` 中 `apiBase`
3. 扫码参数: `token=桌台token&channel=alipay`

## 堂食流程

```
顾客扫桌台二维码
  → 微信 / 支付宝小程序
  → 识别 qr_token（不可伪造桌号）
  → 提示注册会员 / 可选人脸迎宾
  → 选规格下单支付
  → 厨房 KDS 出餐
```

- **注册会员**：历史订单、口味偏好、一键复购
- **人脸迎宾**：仅主动授权后生效，可随时关闭；未授权只做匿名客流

## 外卖接入（美团 / 饿了么）

### 能力

- 推单落库 → 统一 `Order` → 厨房 KDS（自动接单，平台 confirm 异步回传）
- 回调事件：新单 / 取消 / 退款 / 催单 / 配送状态 / 完成 / 隐私号降级
- 商家：同意 / 拒绝退款、主动取消、出餐回传、菜单 / 价格 / 库存同步
- 门店绑定：`platform_store_bindings`（门店级 Token 优先，`.env` 全局兜底）
- OAuth：饿了么 Token 定时刷新（约每 6 小时检查）

### 回调地址（生产需 HTTPS）

| 平台 | 路径 | 成功响应 |
|------|------|----------|
| 美团 | `POST /api/v1/delivery/meituan/notify` | `{"data":"ok"}`（须 2s 内） |
| 饿了么 | `POST /api/v1/delivery/eleme/notify` | `{"message":"ok"}` |

开发模式：`APP_ENV=development` 且 `APP_DEBUG=true` 时，回调 JSON 可带 `"_dev_simulate": true` 跳过验签。

### 配置步骤

1. 填写 `.env` 中 `MEITUAN_*` / `ELEME_*`
2. 后台 → **外卖管理** → 门店绑定（`app_poi_code` / `shopId`，饿了么建议填 access + refresh token）
3. 配置商品映射后，可同步菜单 / 价格 / 库存
4. 开放平台配置上述 notify URL

## 环境变量

参见 `.env.example`。真实密钥不可提交 Git。

关键项：

- `WECHAT_*` / `ALIPAY_*` — 小程序登录与支付
- `MEITUAN_*` / `ELEME_*` — 外卖开放平台
- `FACE_ENCRYPTION_KEY` — 人脸特征加密
- `JWT_SECRET` — 鉴权密钥

未配置第三方密钥时，相关接口进入开发 mock，便于本地联调。

## 测试

```bash
pip install -r tests/requirements.txt
pytest                          # 单元测试（约 29 项）
python tests/simulation_e2e.py  # 全链路模拟（堂食 + 外卖回调）
python -m compileall backend
```

数据库脚本在 `sql/`，测试程序在 `tests/`。

## 生产部署（非 Docker）

1. 安装 MySQL / Redis，按顺序执行全部 `sql/*.sql`
2. `uvicorn` / `gunicorn` 跑后端（建议 systemd）
3. `npm run build`，Nginx 托管 `frontend/dist`
4. Nginx 反代 `/api` 到后端（参考 `deploy/nginx.conf`，改为 `127.0.0.1`）
5. 小程序后台配置合法 request 域名（HTTPS）
6. 美团 / 饿了么配置回调域名与验签密钥；关闭生产环境 `_dev_simulate`

## 安全说明

- 顾客端 JWT 鉴权；废弃不安全的 openid 直传登录
- 人脸特征由服务端提取，禁止客户端传 `feature_vector`
- 支付 / 外卖回调验签（开发模式可模拟）
- 支付金额由服务端从数据库重新计算
- 桌台二维码使用不可预测 token
- 厨房与外卖 WebSocket 需鉴权
- 摄像头密码加密存储；操作日志定期清理

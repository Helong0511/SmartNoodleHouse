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

**开发环境**按顺序执行：

```bash
mysql -u root -p < sql/001_schema.sql
mysql -u root -p < sql/002_seed_test.sql
python scripts/init_admin.py
```

**生产环境**请勿执行 `002_seed_test.sql`（含弱口令与测试数据），改用：

```bash
mysql -u root -p < sql/001_schema.sql
mysql -u root -p < sql/002_seed_prod.sql
set ADMIN_INITIAL_PASSWORD=YourStrongPass123!
python scripts/init_admin.py
```

| 脚本 | 说明 |
|------|------|
| `000_create_db_grant.sql` | 可选：建库 + 应用账号授权 |
| `001_schema.sql` | 全量表结构（40 表） |
| `002_seed_prod.sql` | **生产**：角色/权限/会员/券/门店/系统参数 |
| `002_seed_test.sql` | **仅开发**：样例商品、订单、admin/cashier/manager |
| `sql/tools/003_capacity_audit.sql` | 容量审计（运维工具，非迁移） |
| `sql/tools/004_fix_delivery_dining_type.sql` | 历史外卖订单 dining_type 修复（幂等） |
| `sql/tools/005_backfill_member_level.sql` | 已注册顾客补挂普通会员等级（幂等） |
| `sql/tools/006_customer_token_version.sql` | 已有库补齐 customers.token_version（幂等） |
| `sql/tools/008_member_level_manage.sql` | 会员等级管理权限（幂等） |

开发种子默认管理员: `admin` / `admin123`（生产请用 `init_admin.py` 设置强密码）

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
pytest                          # 单元测试（约 120+ 项）
python tests/simulation_e2e.py  # 全链路模拟（堂食 + 外卖回调）
python -m compileall backend
```

数据库脚本在 `sql/`，测试程序在 `tests/`。

## 生产部署硬件建议

目标环境：**Ubuntu 22.04 LTS**，不使用 Docker。组件包括 Nginx、FastAPI、MySQL 8、Redis 7，可选 InsightFace 人脸 + 海康 RTSP 拉流。

### 单店 + 4 路摄像头（推荐配置）

4 路同时拉流 + 人脸识别，CPU 是主要瓶颈，**不建议 4核8G**。

| 项目 | 规格 | 说明 |
|------|------|------|
| CPU | **8 核** | 4 路解码 + 识别 |
| 内存 | **16 GB** | MySQL + Redis + Python + 模型 |
| 系统盘 | 40～60 GB SSD | Ubuntu + 应用 |
| 数据盘 | **200 GB SSD** | MySQL、日志、上传、模型 |
| 公网带宽 | **10 Mbps 固定** | 小程序、外卖回调、HTTPS |
| 内网 | 千兆交换机 | 摄像头与服务器同网段，RTSP 不走公网 |

同机部署：Nginx + gunicorn（2～3 worker，`PROCESS_ROLE=leader` 仅 1 实例）+ MySQL + Redis。

### 配置与 4 路摄像头

| 配置 | 表现 | 结论 |
|------|------|------|
| 4C8G | 拉流勉强，识别易满载 | 不推荐 |
| 4C16G | 降 `detect_fps` 到 2 可能勉强 | 预算极紧可考虑 |
| **8C16G** | 4 路 @3fps 较稳 | **单店 4 路推荐** |
| 8C16G + GPU | 延迟更低 | 单店 4 路通常不必 |

### 参考价格（2026，人民币）

**云主机（8核16G + 200G 盘 + 10M 带宽）**

| 计费 | 约计 |
|------|------|
| 包年（含活动） | **5300～7400 元/年**（约 440～620 元/月） |
| 按月（常规） | **610～810 元/月** |

经济型实例更便宜但高负载可能卡顿；生产建议通用型 u1 / 计算型 c 系列。腾讯云、华为云同档价格接近。

**店内物理机（一次投入）**

| 项目 | 参考价 |
|------|--------|
| 8核16G + 512G NVMe 主机 | 3500～4500 元 |
| 千兆 8 口交换机 | 150～300 元 |
| UPS 600VA | 300～600 元 |
| 企业宽带 100M | 约 100～200 元/月 |
| 域名 | 约 50～80 元/年 |

**首年合计约 6500～7500 元**（含宽带）；3 年以上长期运行通常比云主机更省。

更完整命令见 `scripts/生产部署-ubuntu.txt`。

---

## 店内物理机部署

适用于 **单店、4 路摄像头、服务器放店内** 的场景。

### 架构

```
[店内局域网]
  4×海康摄像头 ──RTSP──┐
  厨房KDS / 收银电脑 ──┼── 千兆交换机
                       │
              [物理服务器 8C16G]
                Ubuntu 22.04
                Nginx :443
                Python API :8000（本机）
                MySQL + Redis
                       │
              [企业宽带 / 固定IP或DDNS]
                       │
         微信小程序 / 外卖平台回调 / 远程后台
```

要点：摄像头只走店内局域网；公网仅暴露 HTTPS（443）；全机只跑 **一个** `PROCESS_ROLE=leader` 实例。

### 1. 网络（装机前）

1. 服务器固定内网 IP（如 `192.168.1.10`），与摄像头同网段
2. 路由器 **443 端口映射** → 服务器
3. 域名解析到门店公网 IP（或 DDNS）；小程序必须 HTTPS
4. 建议接 UPS

### 2. 安装系统与依赖

```bash
sudo apt update && sudo apt upgrade -y

sudo apt install -y git curl wget vim nginx certbot python3-certbot-nginx \
  mysql-server redis-server build-essential python3.11 python3.11-venv python3-pip

curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
sudo apt install -y nodejs
```

### 3. 初始化 MySQL

```bash
sudo mysql_secure_installation

sudo mysql -e "
CREATE DATABASE smart_noodle_house CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'snh'@'127.0.0.1' IDENTIFIED BY '你的强密码';
GRANT ALL ON smart_noodle_house.* TO 'snh'@'127.0.0.1';
FLUSH PRIVILEGES;
"

sudo systemctl enable redis-server && sudo systemctl start redis-server
```

### 4. 部署代码

```bash
sudo mkdir -p /var/www/smart-noodle
sudo chown $USER:$USER /var/www/smart-noodle
cd /var/www/smart-noodle
git clone <仓库地址> .

cp .env.production.example .env
nano .env
```

`.env` 必改项：

```bash
APP_ENV=production
APP_DEBUG=false
APP_HOST=127.0.0.1
APP_PORT=8000
DATABASE_URL=mysql+aiomysql://snh:密码@127.0.0.1:3306/smart_noodle_house?charset=utf8mb4
REDIS_URL=redis://127.0.0.1:6379/0
JWT_SECRET=随机32位以上
FACE_ENCRYPTION_KEY=随机32位以上
ALLOW_PAYMENT_MOCK=false
ALLOW_AUTH_MOCK=false
ALLOW_DELIVERY_MOCK=false
CORS_ORIGINS=https://你的域名.com
H5_PUBLIC_BASE_URL=https://你的域名.com
UPLOAD_DIR=/var/www/smart-noodle/uploads
PROCESS_ROLE=leader
ENABLE_RECOGNITION_WORKER=1
# 微信/支付宝/外卖回调 URL 改为 https://你的域名.com/...
```

生成随机密钥：`python3 -c "import secrets; print(secrets.token_urlsafe(48))"`

### 5. 数据库

```bash
cd /var/www/smart-noodle
mysql -u snh -p smart_noodle_house < sql/001_schema.sql
mysql -u snh -p smart_noodle_house < sql/002_seed_prod.sql
mysql -u snh -p smart_noodle_house < sql/tools/008_member_level_manage.sql

export ADMIN_INITIAL_PASSWORD='超管强密码'
python3 scripts/init_admin.py
```

### 6. 后端与前端

```bash
cd /var/www/smart-noodle/backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -U pip && pip install -r requirements.txt

mkdir -p /var/www/smart-noodle/uploads /etc/smart-noodle/certs
alembic upgrade head   # 若项目使用 Alembic

cd /var/www/smart-noodle/frontend
npm ci && VITE_API_BASE_URL=/api/v1 npm run build
```

### 7. Nginx + HTTPS

```bash
sudo cp /var/www/smart-noodle/deploy/nginx.conf /etc/nginx/sites-available/smart-noodle
sudo nano /etc/nginx/sites-available/smart-noodle
# 修改 server_name、root、uploads alias 路径

sudo ln -sf /etc/nginx/sites-available/smart-noodle /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo certbot --nginx -d 你的域名.com
sudo systemctl enable nginx && sudo systemctl reload nginx
```

### 8. systemd 开机自启

创建 `/etc/systemd/system/smart-noodle.service`：

```ini
[Unit]
Description=SmartNoodleHouse API
After=network.target mysql.service redis-server.service

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/var/www/smart-noodle/backend
EnvironmentFile=/var/www/smart-noodle/.env
Environment=PYTHONPATH=/var/www/smart-noodle/backend:/var/www/smart-noodle
ExecStart=/var/www/smart-noodle/backend/.venv/bin/gunicorn app.main:app \
  -k uvicorn.workers.UvicornWorker \
  -w 2 \
  -b 127.0.0.1:8000 \
  --timeout 120 \
  --access-logfile - \
  --error-logfile -
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo chown -R www-data:www-data /var/www/smart-noodle/uploads
sudo chmod 640 /var/www/smart-noodle/.env
sudo systemctl daemon-reload
sudo systemctl enable --now smart-noodle
```

### 9. 防火墙

```bash
sudo ufw allow OpenSSH
sudo ufw allow 443/tcp
sudo ufw enable
```

MySQL / Redis 仅监听 `127.0.0.1`，勿对公网开放。

### 10. 4 路摄像头

1. 海康摄像头设固定内网 IP（如 `.101`～`.104`）
2. 后台 → **摄像头** 添加 RTSP，示例：

   `rtsp://admin:密码@192.168.1.101:554/Streaming/Channels/101`

3. 每路 **detect_fps 建议 2～3**
4. 测试拉流：`ffprobe -rtsp_transport tcp "rtsp://..."`
5. 查看日志：`sudo journalctl -u smart-noodle -f`（应出现 `Restored 4 cameras`）

### 11. 小程序与外卖

| 项目 | 操作 |
|------|------|
| 微信/支付宝小程序 | `miniprogram-*/config/env.js` → `PROD_API_BASE=https://你的域名.com/api/v1` |
| 公众平台 | 配置 request 合法域名 |
| 美团/饿了么 | 回调 URL 填 `https://你的域名.com/api/v1/delivery/.../notify` |
| 微信支付 | PEM 证书放 `/etc/smart-noodle/certs/`，`.env` 配置路径 |

### 12. 日常运维

```bash
sudo systemctl status smart-noodle nginx mysql redis-server
sudo journalctl -u smart-noodle -f

# 更新发布
cd /var/www/smart-noodle && git pull
source backend/.venv/bin/activate && pip install -r backend/requirements.txt
cd frontend && npm ci && VITE_API_BASE_URL=/api/v1 npm run build
sudo systemctl restart smart-noodle

# 每日备份（建议 crontab 凌晨 3 点）
mysqldump -u snh -p smart_noodle_house | gzip > /backup/snh_$(date +%F).sql.gz
```

### 13. 常见问题

| 现象 | 处理 |
|------|------|
| 摄像头 OFFLINE | 查 RTSP 账号、同网段；`ffprobe` 测连通 |
| CPU 100% | 降低 `detect_fps`；确认仅 1 个 leader |
| 小程序连不上 | HTTPS、合法域名、路由器 443 映射 |
| 外卖回调失败 | 配置 `WEBHOOK_IP_ALLOWLIST`；Nginx 2s 内响应 |

健康检查：

```bash
curl -s http://127.0.0.1:8000/health
curl -sI https://你的域名.com/
```

---

## 生产部署（云主机 / 简要）

与店内物理机步骤相同，差异主要在：

1. 云盘选 **200 GB ESSD**，带宽 **10 Mbps**
2. 无需路由器端口映射，安全组放行 **443**
3. 摄像头仍建议在店内局域网，RTSP 通过 **VPN/专线** 或 **店内物理机** 拉流（不建议 RTSP 走公网）

通用步骤：

1. 安装 MySQL / Redis，执行 `sql/001_schema.sql` 与 `sql/002_seed_prod.sql`，再运行 `python scripts/init_admin.py`
2. 复制 `.env.production.example` 为 `.env`，设置 `APP_ENV=production`、`JWT_SECRET`、`WEBHOOK_REQUIRE_ALLOWLIST=true` 等
3. `gunicorn` 跑后端（systemd，`PROCESS_ROLE=leader` 仅一个实例）
4. 前端构建：`VITE_API_BASE_URL=/api/v1 npm run build`，Nginx 托管 `frontend/dist`
5. Nginx 反代 `/api` 到后端（参考 `deploy/nginx.conf`）
6. 小程序配置合法 request 域名（HTTPS）
7. 美团 / 饿了么配置回调与验签；关闭 `_dev_simulate`


## 安全说明

- 顾客端 JWT 鉴权；废弃不安全的 openid 直传登录
- 人脸特征由服务端提取，禁止客户端传 `feature_vector`
- 支付 / 外卖回调验签（开发模式可模拟）
- 支付金额由服务端从数据库重新计算
- 桌台二维码使用不可预测 token
- 厨房与外卖 WebSocket 需鉴权
- 摄像头密码加密存储；操作日志定期清理

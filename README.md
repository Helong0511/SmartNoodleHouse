# 面馆智能经营系统

微信 / 支付宝小程序扫码点餐 + 统一订单 + 厨房 KDS + 会员 / 人脸迎宾 + 美团 / 饿了么外卖 + 后台 RBAC

## 技术栈

- 后端: Python 3.11+, FastAPI, SQLAlchemy 2.x, MySQL 8, Redis 7, APScheduler
- 后台前端: Vue 3, TypeScript, Vite, Element Plus
- 微信小程序: `miniprogram-wechat/`
- 支付宝小程序: `miniprogram-alipay/`
- 摄像头: OpenCV（海康 RTSP）
- 实时: WebSocket（厨房 / 订单 / 顾客叫号屏）

> **不使用 Docker**。请在本机安装 MySQL、Redis、Python、Node.js 后按下方步骤启动。

## 功能概览

| 模块 | 能力 |
|------|------|
| 堂食 | 微信 / 支付宝扫码点餐、服务端计价、支付回调验签 |
| 厨房 KDS | 实时 WebSocket、状态流转、外卖催单 / 退款高亮、出餐回传平台 |
| 顾客叫号屏 | 大厅电视只读展示「制作中 / 请取餐」，堂食桌号 + 打包取餐号 |
| 免烹饪直出 | 卤味等现成品可跳过制作流程，KDS 一键「直接出餐」 |
| 会员 | 注册、口味画像、一键复购；订单页醒目显示桌号 / 取餐号 |
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

**全新环境**执行唯一数据库脚本（会删除并重建同名库，已有业务数据勿执行）：

```bash
mysql -u root -p < sql/smart_noodle_house.sql
python scripts/init_admin.py
```

生产部署前必须修改脚本内数据库账号密码占位符，并设置管理员强密码：

```bash
mysql -u root -p < sql/smart_noodle_house.sql
set ADMIN_INITIAL_PASSWORD=YourStrongPass123!
python scripts/init_admin.py
```

| 脚本 | 说明 |
|------|------|
| `smart_noodle_house.sql` | **唯一真源**：建库授权、完整结构与生产基础数据 |
| `002_seed_test.sql` | **仅开发**：样例商品、订单、admin/cashier/manager |
| `sql/tools/003_capacity_audit.sql` | 容量审计（运维工具，非迁移） |
| `sql/tools/004_fix_delivery_dining_type.sql` | 历史外卖订单 dining_type 修复（幂等） |
| `sql/tools/005_backfill_member_level.sql` | 已注册顾客补挂普通会员等级（幂等） |
| `sql/tools/006_customer_token_version.sql` | 已有库补齐 customers.token_version（幂等） |
| `sql/tools/008_member_level_manage.sql` | 会员等级管理权限（幂等） |
| `sql/tools/010_product_no_cooking.sql` | 商品表增加 `no_cooking`（免烹饪 / 直出餐，幂等） |

开发种子默认管理员: `admin` / `admin123`（生产请用 `init_admin.py` 设置强密码）

### 3. 后端

```bash
cd backend
pip install -r requirements.txt
copy ..\.env.development.example ..\.env   # Windows 本地开发
# cp ../.env.production.example ../.env    # Linux 生产
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

大厅可另开浏览器标签页作为 **顾客叫号屏**（见下文「厨房 KDS 与顾客叫号屏」），无需登录。

### 5. 微信小程序

开放平台入口：

| 平台 | 网址 |
|------|------|
| 微信公众平台（小程序后台） | https://mp.weixin.qq.com |
| 微信开放平台 | https://open.weixin.qq.com |
| 微信小程序开发文档 | https://developers.weixin.qq.com/miniprogram/dev/framework/ |

1. 用微信开发者工具打开 `miniprogram-wechat`
2. 填写 AppID（或测试号），开发阶段可关闭域名校验
3. 修改 `miniprogram-wechat/config/env.js` 中 `DEV_LAN_HOST`（真机联调）；API 地址由该文件按环境自动选择
4. 扫桌台二维码，或编译参数: `token=桌台token&channel=wechat`
5. **取餐进度**（与大厅叫号屏同步，无需手填 `store_id`）：
   - 支付成功页、**订单** Tab 列表 / 详情会醒目显示 **桌号**（堂食）或 **取餐号**（打包，订单号末 4 位）
   - 状态：排队中 → 制作中 → **请取餐**（变绿后可对照大厅 `/display?store_id=门店ID` 屏自取）
   - 订单页每 8 秒自动刷新；打包单可点击取餐号卡片复制

### 6. 支付宝小程序

开放平台入口：

| 平台 | 网址 |
|------|------|
| 支付宝开放平台 | https://open.alipay.com |
| 支付宝开放文档 | https://opendocs.alipay.com |

1. 打开 `miniprogram-alipay`，填写 AppID
2. 修改 `miniprogram-alipay/config/env.js` 中 `DEV_LAN_HOST`（与项目根 `.env` 一致）
3. 扫码参数: `token=桌台token&channel=alipay`
4. **取餐进度**：与微信小程序相同（`pages/result`、`pages/orders` + `utils/pickup.js`）

## 堂食流程

```
顾客扫桌台二维码
  → 微信 / 支付宝小程序
  → 识别 qr_token（不可伪造桌号）
  → 提示注册会员 / 可选人脸迎宾
  → 选规格下单支付
  → 厨房 KDS 制作 → 顾客叫号屏 / 小程序同步状态
  → 送到桌 或 顾客自取
```

- **注册会员**：历史订单、口味偏好、一键复购
- **人脸迎宾**：仅主动授权后生效，可随时关闭；未授权只做匿名客流

## 厨房 KDS 与顾客叫号屏

店内通常有 **两块屏 + 顾客手机**，职责不同，勿混用：

| | 厨房 KDS | 顾客叫号屏 | 小程序订单页 |
|--|---------|-----------|-------------|
| **位置** | 厨房内 | 收银台 / 等位区 | 顾客手机 |
| **地址** | `/kitchen` | `/display?store_id=门店ID` | 订单详情 / 订单列表 |
| **登录** | 需厨房 / 店长账号 | 不需要（可选令牌） | 顾客登录 |
| **内容** | 菜品明细、备注、操作按钮 | 桌号或取餐号 + 制作中 / 请取餐 | 大号桌号 / 取餐号 + 进度 |
| **能否操作** | 是（改订单状态） | 否（只读） | 否 |

### 厨房 KDS

1. 浏览器打开后台 → 侧边栏 **厨房 KDS**，或直接访问 `http://127.0.0.1:5173/kitchen`（生产为 `https://你的域名/kitchen`）
2. 使用 **厨房 / 店长 / 超管** 账号登录
3. 触屏设备：用 Chrome / Edge 打开上述地址即可触摸操作，无需专用驱动；建议点 **全屏**
4. 状态按钮流程：**开始制作** → **进入烹饪** → **制作完成** → **出餐**（`READY` 后顾客侧显示「请取餐」）
5. 刷新：WebSocket 正常时为实时推送；断线后每 **8 秒** 轮询兜底

**免烹饪 / 卤味直出**（需先执行 `sql/tools/010_product_no_cooking.sql`，并在后台商品勾选「免烹饪」）：

- 订单内 **全部为** 免烹饪商品时，KDS 显示「现成品·直出」，可直接点 **直接出餐**（跳过制作流程）
- 含需烹饪商品的单禁止直出，须按正常流程操作

### 顾客叫号屏

面向等餐顾客，只展示 **本店堂食 / 打包自取** 订单（不含美团 / 饿了么外卖单）。

**打开方式**

```
http://127.0.0.1:5173/display?store_id=1          # 本地开发，1 换成实际门店 ID
https://你的域名/display?store_id=1               # 生产环境
```

生产环境建议在 `.env` 设置访问令牌，并在 URL 带上 `token` 参数：

```bash
DISPLAY_SCREEN_TOKEN=随机一串
# 访问：https://你的域名/display?store_id=1&token=随机一串
```

留空 `DISPLAY_SCREEN_TOKEN` 时不校验令牌（仅建议开发环境）。

**屏上展示**

- 左栏 **制作中**：排队中 / 制作中的单
- 右栏 **请取餐**：厨房点「制作完成」后进入；忙时顾客可 **自行到取餐台领取**，无需等人工叫号
- **堂食**：显示 **桌号**（如 `A01`）
- **打包自取**：显示 **取餐号**（订单号末 4 位，如 `K7M2`）
- 新进入「请取餐」时有提示音；WebSocket 实时更新，断线每 8 秒轮询

**部署建议**

- 厨房触屏一体机 → `/kitchen`（员工登录）
- 大厅电视 / 非触屏 + 电视棒 → `/display?store_id=…`（常亮全屏，无需触摸）

### 小程序取餐对照

支付成功页与 **订单列表 / 订单详情** 会醒目显示：

| 用餐方式 | 小程序显示 | 与叫号屏对照 |
|---------|-----------|-------------|
| 堂食（送到桌） | 大号 **桌号** | 叫号屏「制作中 / 请取餐」中的同一桌号 |
| 打包外带 | 大号 **取餐号**（订单号末 4 位） | 叫号屏「请取餐」中的同一号码 |

状态变为 **请取餐** 时，小程序卡片变绿，并提示可到取餐台自取。订单页每 8 秒自动刷新，与叫号屏、KDS 同步。

**送到桌**：顾客看小程序或大厅屏均可；服务员按桌号送餐。  
**前台自取**：顾客对照取餐号，看到「请取餐」即可自取，减轻忙时人工叫号压力。

### 相关 API（联调参考）

| 接口 | 说明 |
|------|------|
| `GET /api/orders/kitchen/active` | 厨房 KDS 数据（需厨房权限） |
| `GET /api/orders/display/active?store_id=` | 叫号屏数据（公开，可选 `token`） |
| `WS /api/ws/kitchen` | 厨房推送（需员工 token） |
| `WS /api/ws/display?store_id=` | 叫号屏推送（公开，可选 `token`） |
| `GET /api/orders/{id}`、`GET /api/orders/mine` | 含 `pickup_code`、`pickup_label`、`customer_status_label` 等字段 |

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
| 美团 | `POST /api/delivery/meituan/notify` | `{"data":"ok"}`（须 2s 内） |
| 饿了么 | `POST /api/delivery/eleme/notify` | `{"message":"ok"}` |

开发模式：`APP_ENV=development` 且 `APP_DEBUG=true` 时，回调 JSON 可带 `"_dev_simulate": true` 跳过验签。

### 配置步骤

1. 填写 `.env` 中 `MEITUAN_*` / `ELEME_*`
2. 后台 → **外卖管理** → 门店绑定（`app_poi_code` / `shopId`，饿了么建议填 access + refresh token）
3. 配置商品映射后，可同步菜单 / 价格 / 库存
4. 开放平台配置上述 notify URL

## 环境变量与配置文件

真实密钥不可提交 Git。按环境选择模板复制为项目根 `.env`：

| 环境 | 模板文件 | 用法 |
|------|----------|------|
| 本地 Windows 联调 | `.env.development.example` | `copy .env.development.example .env` |
| 生产 Ubuntu 部署 | `.env.production.example` | `cp .env.production.example .env` |
| 全量字段参考 | `.env.example` | 查阅所有可配置项 |

### 配置文件对照

| 用途 | 路径 | 说明 |
|------|------|------|
| 后端 API / 支付 / 桌台码 | 项目根 `.env` | 由 `backend/app/core/config.py` 读取 |
| 微信/支付宝小程序 API | `miniprogram-*/config/env.js` | `PROD_API_BASE` / `DEV_LAN_HOST` |
| 小程序环境说明 | `miniprogram-*/config/环境说明.txt` | 本地 vs 正式版对照表 |
| 微信域名校验 | `miniprogram-wechat/project.config.json` | `setting.urlCheck` |
| 微信隐私声明 | `miniprogram-wechat/app.json` | `__usePrivacyCheck__`；头像/手机号在公众平台隐私指引配置 |
| 支付宝隐私检查 | `miniprogram-alipay/app.json` | `usePrivacyCheck: true` |
| 前端开发代理 | `frontend/vite.config.ts` | 本地 `/api` 代理到 `127.0.0.1:8000` |
| Nginx 反代 | `nginx/smart-noodle.conf` | HTTPS、静态资源、WebSocket |
| systemd 服务 | `systemd/smart-noodle.service` | gunicorn 开机自启 |
| 启动命令（Windows） | `scripts/运行命令.txt` | 含正式版上线检查清单 |
| 部署命令（Ubuntu） | `scripts/生产部署-ubuntu.txt` | 生产环境速查 |

### 关键环境变量

| 变量 | 本地开发典型值 | 生产典型值 |
|------|---------------|-----------|
| `APP_ENV` | `development` | `production` |
| `APP_HOST` | `0.0.0.0`（真机） | `127.0.0.1`（Nginx 反代） |
| `H5_PUBLIC_BASE_URL` | `http://192.168.x.x:5173` | `https://你的域名` |
| `DEV_LAN_HOST` | `192.168.x.x` | 留空 |
| `MINI_PROGRAM_ENV_VERSION` | `develop` | `release` |
| `VITE_API_BASE_URL` | `/api` | `/api` |
| `ALLOW_*_MOCK` | `true` | 全部 `false` |
| `ALIPAY_AES_KEY` | 可留空（手输手机号） | **必填**（解密授权手机号） |
| `DISPLAY_SCREEN_TOKEN` | 可留空 | 建议设置 |
| `XPYUN_ENABLED` | `false` 或 `true` | 打印总开关 |
| `PRINTER_MODE` | `local`（XP-80T WiFi） | `local` 或 `xpyun` |
| `PRINTER_HOST` | 打印机局域网 IP | 与后端同网段 |
| `ALLOW_PRINT_MOCK` | 无打印机可 `true` | 必须 `false` |

未配置第三方密钥时，开发环境相关接口可进入 mock，便于本地联调；生产启动会拒绝 mock 开关。

## 测试

```bash
pip install -r tests/requirements.txt
pytest                          # 单元测试（约 120+ 项）
python tests/simulation_e2e.py  # 全链路模拟（堂食 + 外卖回调）
python -m compileall backend
```

数据库脚本在 `sql/`，测试程序在 `tests/`。

## 生产部署

目标环境：**Ubuntu 22.04 LTS**，不使用 Docker。对外仅暴露 **HTTPS 443**；后端 gunicorn 监听 `127.0.0.1:8000`，由 Nginx 反代。

更完整的命令速查见 `scripts/生产部署-ubuntu.txt`；小程序环境对照见 `miniprogram-*/config/环境说明.txt`。

### 国内上线：服务器与 ICP 备案（必读）

面向中国大陆顾客、使用**微信 / 支付宝小程序正式版**对外营业时，通常须同时满足：

| 项目 | 是否必须（生产） | 说明 |
|------|------------------|------|
| **服务器在境内** | 强烈建议 / 实质上必需 | 小程序 `request` 合法域名、H5 桌台码、支付与外卖回调均走公网 HTTPS；未备案域名常被云平台或平台方拦截 |
| **域名 ICP 备案** | **是** | 在工信部完成网站备案（常见名称：ICP 备案、网站备案）；`H5_PUBLIC_BASE_URL`、`PROD_API_BASE` 所用域名须已备案且解析到该服务器 |
| **主体资质** | **是** | 企业 / 个体工商户营业执照；个人主体无法使用微信 `getPhoneNumber` 等能力 |
| **公安备案（公网安备）** | 备案通过后按规定办理 | 各地网警平台，备案号需在网站底部展示（含 H5 落地页） |
| **本地局域网联调** | 不需要 | `192.168.x.x`、`127.0.0.1` 仅开发用，不能作为正式版小程序合法域名 |

**与本项目的对应关系：**

- 须备案的访问入口：`https://你的域名/`（后台、叫号屏）、`/scan`（桌台 H5）、`/api`（小程序 API）、支付/外卖回调 URL
- 微信 [mp.weixin.qq.com](https://mp.weixin.qq.com) → 开发设置 → **服务器域名**：只接受已备案且 HTTPS 的域名
- 支付宝开放平台 [open.alipay.com](https://open.alipay.com) → **服务器域名白名单**：同样要求可公网访问的合规域名
- 云厂商（阿里云 / 腾讯云 / 华为云等）购买**大陆地域**云主机时，未备案域名通常**无法**开通 80/443 或绑定证书

**建议上线顺序：**

1. 注册域名 → 购买**中国大陆地域**云服务器（或与备案服务商同一接入商）
2. 提交 ICP 备案（约 1～20 个工作日，各省不同）→ 获得备案号（如 `京ICP备xxxxxxxx号`）
3. 域名解析到服务器 → 部署本项目的 Nginx + systemd（见下文）
4. 配置微信 / 支付宝小程序合法域名、隐私政策、支付商户号
5. 小程序提交审核并发布 **release** 正式版

本地 Windows 联调（`DEV_LAN_HOST`、`MINI_PROGRAM_ENV_VERSION=develop`）**不需要**备案；切勿把未备案域名或未解析的 IP 填入正式版 `PROD_API_BASE` / `H5_PUBLIC_BASE_URL`。

### 部署架构

```
[店内局域网]                         [公网]
  海康摄像头 ──RTSP──┐
  厨房 KDS / 收银 ───┼── 服务器      HTTPS :443 ── Nginx
  大厅叫号屏 ────────┘    Ubuntu         │
                    MySQL + Redis        ├─ /          → frontend/dist（后台、叫号屏、H5 扫码页）
                    API :8000（本机）    ├─ /api       → gunicorn
                                         └─ /uploads   → 本地上传目录

  微信小程序 / 支付宝小程序 ──request──→ https://你的域名/api
  美团 / 饿了么 ──回调 POST──→ https://你的域名/api/delivery/.../notify
```

要点：

- 摄像头 RTSP **只走店内局域网**，勿将 RTSP 端口暴露公网
- 全机仅 **一个** `PROCESS_ROLE=leader` 实例（调度 / TTS / 人脸识别）
- 小程序、H5 桌台码、支付回调均须 **HTTPS 域名**

云主机与店内物理机部署步骤相同；差异仅在网络：云主机安全组放行 443，物理机需路由器端口映射 + DDNS。

### 本地调试 vs 生产环境

| 配置项 | 本地 Windows 联调 | 生产 Ubuntu |
|--------|------------------|-------------|
| 环境模板 | `.env.development.example` → `.env` | `.env.production.example` → `.env` |
| `APP_ENV` | `development` | `production` |
| `APP_HOST` | `0.0.0.0`（真机可连） | `127.0.0.1`（Nginx 反代） |
| `H5_PUBLIC_BASE_URL` | `http://192.168.x.x:5173` | `https://你的域名` |
| `DEV_LAN_HOST` | `192.168.x.x` | 留空 |
| `MINI_PROGRAM_ENV_VERSION` | `develop` | `release` |
| 小程序 `config/env.js` | `DEV_LAN_HOST` | `PROD_API_BASE` + `ENABLE_*_PRIVACY_APIS=true` |
| `ALLOW_*_MOCK` | `true` | 全部 `false` |
| 微信 `urlCheck` | `false` + 勾选不校验合法域名 | `true` + 配置服务器域名 |
| 前端 | `npm run dev`（Vite 代理） | `npm run build` + Nginx 托管 `dist` |

### 一、服务器准备

**推荐规格（单店 + 4 路摄像头）**：8 核 16G、200G SSD、10M 带宽。

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git curl wget vim nginx certbot python3-certbot-nginx \
  mysql-server redis-server build-essential python3.11 python3.11-venv python3-pip
curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
sudo apt install -y nodejs
```

### 二、数据库初始化

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

**生产**按顺序执行（勿执行 `002_seed_test.sql`）：

```bash
cd /var/www/smart-noodle
mysql -u root -p < sql/smart_noodle_house.sql

export ADMIN_INITIAL_PASSWORD='超管强密码'
python3 scripts/init_admin.py
```

### 三、部署代码与后端 `.env`

```bash
sudo mkdir -p /var/www/smart-noodle
sudo chown $USER:$USER /var/www/smart-noodle
cd /var/www/smart-noodle
git clone <仓库地址> .

cp .env.production.example .env
nano .env
```

**`.env` 生产必改项**（完整字段与中文注释见 `.env.production.example`）：

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
ALLOW_OPENCV_FACE_FALLBACK=false
ALLOW_DELIVERY_MOCK=false
ENABLE_RATE_LIMIT=true
CORS_ORIGINS=https://你的域名.com
H5_PUBLIC_BASE_URL=https://你的域名.com
MINI_PROGRAM_ENV_VERSION=release
VITE_API_BASE_URL=/api
UPLOAD_DIR=/var/www/smart-noodle/uploads
JSON_LOGS=true
WEBHOOK_REQUIRE_ALLOWLIST=true
DISPLAY_SCREEN_TOKEN=随机一串
PROCESS_ROLE=leader
ENABLE_RECOGNITION_WORKER=1
# WECHAT_* / ALIPAY_* / MEITUAN_* / ELEME_* 见 .env.production.example
ALIPAY_AES_KEY=                    # 支付宝授权手机号解密，生产必填
```

生成随机密钥：

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 四、后端与前端构建

```bash
cd /var/www/smart-noodle/backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -U pip && pip install -r requirements.txt
mkdir -p /var/www/smart-noodle/uploads /etc/smart-noodle/certs
alembic upgrade head

cd /var/www/smart-noodle/frontend
npm ci
npm run build
```

构建产物在 `frontend/dist`；API 使用相对路径 `/api`，由 Nginx 反代到后端。

### 五、Nginx + HTTPS

```bash
sudo cp /var/www/smart-noodle/nginx/smart-noodle.conf /etc/nginx/sites-available/smart-noodle.conf
sudo cp /var/www/smart-noodle/nginx/snippets/*.conf /etc/nginx/snippets/
sudo nano /etc/nginx/sites-available/smart-noodle.conf
```

须修改 `nginx/smart-noodle.conf` 中占位符：

| 占位符 | 改为 |
|--------|------|
| `REPLACE_DOMAIN` | `你的域名.com` |
| `REPLACE_WEB_ROOT` | `/var/www/smart-noodle/frontend/dist` |
| `REPLACE_UPLOADS_DIR` | `/var/www/smart-noodle/uploads` |

```bash
sudo ln -sf /etc/nginx/sites-available/smart-noodle.conf /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo certbot --nginx -d 你的域名.com
sudo systemctl enable nginx && sudo systemctl reload nginx
```

详细步骤见 `nginx/安装说明.txt`。

### 六、systemd 开机自启

```bash
# 将 systemd/smart-noodle.service 中 REPLACE_DEPLOY_ROOT 改为实际部署路径
sed -i 's|REPLACE_DEPLOY_ROOT|/var/www/smart-noodle|g' systemd/smart-noodle.service
sudo cp systemd/smart-noodle.service /etc/systemd/system/
sudo chown -R www-data:www-data /var/www/smart-noodle/uploads
sudo chmod 640 /var/www/smart-noodle/.env
sudo systemctl daemon-reload
sudo systemctl enable --now smart-noodle
```

完整单元说明见仓库内 `systemd/smart-noodle.service`；安装步骤见 `systemd/安装说明.txt`。

### 七、防火墙与健康检查

```bash
sudo ufw allow OpenSSH
sudo ufw allow 443/tcp
sudo ufw enable

curl -s http://127.0.0.1:8000/health
curl -sI https://你的域名.com/
```

MySQL / Redis 仅监听 `127.0.0.1`，勿对公网开放。

### 八、微信小程序发布

| 文件 | 生产配置 |
|------|----------|
| `miniprogram-wechat/config/env.js` | `PROD_API_BASE=https://你的域名.com/api`；`ENABLE_WX_PRIVACY_APIS=true` |
| `miniprogram-wechat/project.config.json` | `setting.urlCheck: true` |
| `miniprogram-wechat/app.json` | 已含 `__usePrivacyCheck__` |

**微信公众平台** https://mp.weixin.qq.com （开放平台 https://open.weixin.qq.com ）

| 配置项 | 说明 |
|--------|------|
| 开发设置 → 服务器域名 | request / upload / download 填 HTTPS 域名 |
| 用户隐私保护指引 | 勾选昵称头像、手机号并发布 |
| URL Link / 明文 Scheme | 桌台一码通扫所需 |
| 主体 | `getPhoneNumber` 须非个人主体且已认证 |

文档：[隐私协议开发指南](https://developers.weixin.qq.com/miniprogram/dev/framework/user-privacy/PrivacyAuthorize.html)

### 九、支付宝小程序发布

| 文件 | 生产配置 |
|------|----------|
| `miniprogram-alipay/config/env.js` | `PROD_API_BASE`；`ENABLE_ALIPAY_PRIVACY_APIS=true` |
| 项目根 `.env` | `ALIPAY_AES_KEY`（接口内容加密 AES） |
| `miniprogram-alipay/app.json` | 已设 `usePrivacyCheck: true` |

**支付宝开放平台** https://open.alipay.com （文档 https://opendocs.alipay.com ）

| 配置项 | 说明 |
|--------|------|
| 服务器域名白名单 | HTTPS |
| 小程序隐私政策 | 声明手机号等 |
| 能力申请 | 「获取会员手机号」 |
| 接口内容加密 | AES 同步到 `ALIPAY_AES_KEY` |

与微信差异：支付宝须**分两步**授权（先 `agreePrivacyAuthorization`，再 `getAuthorize`），不能 pipe 组合。

文档：[my.getPhoneNumber](https://opendocs.alipay.com/mini/api/getphonenumber)

### 十、桌台码与 H5 一码通扫

桌台二维码 URL 来自 `.env` 的 `H5_PUBLIC_BASE_URL`：

```
https://你的域名.com/scan?token=桌台token
```

- `MINI_PROGRAM_ENV_VERSION=release` → 跳转正式版小程序
- 修改域名后须在后台「桌台管理」**重新打印**二维码

### 十一、店内屏端与外卖

| 终端 | 地址 |
|------|------|
| 后台管理 | `https://你的域名.com/` |
| 厨房 KDS | `https://你的域名.com/kitchen` |
| 顾客叫号屏 | `https://你的域名.com/display?store_id=1&token=…` |
| 美团回调 | `POST /api/delivery/meituan/notify` |
| 饿了么回调 | `POST /api/delivery/eleme/notify` |

微信支付 PEM 放 `/etc/smart-noodle/certs/`，路径与 `.env` 一致。

### 十二、上线检查清单

**备案与合规（国内正式版）**

- [ ] 域名已完成 **ICP 备案**，备案号可查询
- [ ] 服务器在**中国大陆**（或与备案接入商一致）
- [ ] 域名已解析到生产服务器，443 可访问
- [ ] 主体为**企业 / 个体工商户**（微信手机号等能力）
- [ ] 网站底部展示备案号（H5 `/scan` 等对外页面）

**后端**

- [ ] `.env` 来自 `.env.production.example`，mock 全部为 `false`
- [ ] `JWT_SECRET`、`FACE_ENCRYPTION_KEY` 已换随机强密钥
- [ ] `H5_PUBLIC_BASE_URL`、`MINI_PROGRAM_ENV_VERSION=release`
- [ ] `ALIPAY_AES_KEY`、`DISPLAY_SCREEN_TOKEN` 已配置
- [ ] 厨房打印机：`XPYUN_ENABLED=true`、`PRINTER_MODE=local`、`PRINTER_HOST` 已填（XP-80T WiFi）
- [ ] `ALLOW_PRINT_MOCK=false`（生产禁止模拟打印）
- [ ] `systemctl status smart-noodle` 为 active

**微信小程序**

- [ ] `config/env.js` → `PROD_API_BASE`、`ENABLE_WX_PRIVACY_APIS=true`
- [ ] `project.config.json` → `urlCheck: true`
- [ ] 公众平台域名与隐私指引已发布

**支付宝小程序**

- [ ] `config/env.js` → `PROD_API_BASE`、`ENABLE_ALIPAY_PRIVACY_APIS=true`
- [ ] 开放平台隐私、手机号、AES 已配置

**业务**

- [ ] 桌台码已按新域名重新生成
- [ ] 外卖回调 URL 已在平台配置
- [ ] KDS、叫号屏店内可访问

### 十三、日常运维

```bash
sudo systemctl status smart-noodle nginx mysql redis-server
sudo journalctl -u smart-noodle -f

cd /var/www/smart-noodle && git pull
source backend/.venv/bin/activate && pip install -r backend/requirements.txt
cd frontend && npm ci && npm run build
sudo systemctl restart smart-noodle

mysqldump -u snh -p smart_noodle_house | gzip > /backup/snh_$(date +%F).sql.gz
```

### 十四、摄像头（可选）

1. 海康摄像头固定内网 IP，后台添加 RTSP（如 `rtsp://admin:密码@192.168.1.101:554/Streaming/Channels/101`）
2. 每路 `detect_fps` 建议 2～3
3. 测试：`ffprobe -rtsp_transport tcp "rtsp://..."`

### 十五、硬件与成本参考

| 项目 | 推荐 |
|------|------|
| CPU / 内存 | 8 核 16G |
| 磁盘 | 200G SSD |
| 带宽 | 10 Mbps |

云主机包年约 **5300～7400 元/年**；店内物理机首年约 **6500～7500 元**（含宽带）。

### 十六、常见问题

| 现象 | 处理 |
|------|------|
| 小程序连不上 API | HTTPS、合法域名、`PROD_API_BASE` 与域名一致 |
| 支付宝手机号失败 | `ALIPAY_AES_KEY`、两步授权流程 |
| 微信头像/手机号报错 | 隐私指引未发布；errno 112/104 |
| 扫桌台码显示 127.0.0.1 | 旧二维码；重打并检查 `H5_PUBLIC_BASE_URL` |
| 外卖回调失败 | `WEBHOOK_IP_ALLOWLIST`；Nginx 2s 内响应 |
| 摄像头 OFFLINE | RTSP 账号、同网段 |
| CPU 100% | 降低 `detect_fps`；仅 1 个 leader |

---

## 安全说明

- 顾客端 JWT 鉴权；废弃不安全的 openid 直传登录
- 人脸特征由服务端提取，禁止客户端传 `feature_vector`
- 支付 / 外卖回调验签（开发模式可模拟）
- 支付金额由服务端从数据库重新计算
- 桌台二维码使用不可预测 token
- 厨房与外卖 WebSocket 需鉴权；顾客叫号屏 WebSocket 为公开频道（建议配置 `DISPLAY_SCREEN_TOKEN`）
- 摄像头密码加密存储；操作日志定期清理

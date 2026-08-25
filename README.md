# 面馆智能经营系统

微信小程序 / 支付宝小程序扫码点餐 + 支付 + 厨房KDS + 顾客画像 + 授权人脸迎宾 + 后台管理

## 技术栈

- 后端: Python 3.11+, FastAPI, SQLAlchemy 2.x, MySQL 8, Redis 7
- 后台前端: Vue 3, TypeScript, Vite, Element Plus
- 微信小程序: `miniprogram-wechat/`
- 支付宝小程序: `miniprogram-alipay/`
- 摄像头: OpenCV
- 实时: WebSocket

> **不使用 Docker**。请在本机安装 MySQL、Redis、Python、Node.js 后按下方步骤启动。

## 本地安装与启动

### 1. 准备环境

- Python 3.11+
- Node.js 18+
- MySQL 8
- Redis 7
- 微信开发者工具（调试微信小程序）
- 支付宝小程序开发者工具（调试支付宝小程序）

### 2. 数据库

```bash
mysql -u root -p < sql/001_init_schema.sql
mysql -u root -p < sql/002_seed_data.sql
python scripts/init_admin.py
```

默认管理员: `admin` / `admin123`

### 3. 后端

```bash
cd backend
pip install -r requirements.txt
copy ..\.env.example ..\.env   # Windows
# cp ../.env.example ../.env   # Linux/macOS
uvicorn app.main:app --reload --app-dir .
```

### 4. 后台管理前端

```bash
cd frontend
npm install
npm run dev
```

浏览器打开: http://127.0.0.1:5173

### 5. 微信小程序

1. 用微信开发者工具打开目录 `miniprogram-wechat`
2. 填写你的小程序 AppID（或使用测试号）
3. 开发阶段关闭域名校验
4. 修改 `app.js` 中 `apiBase` 为你的后端地址
5. 扫桌台二维码或编译模式带参数: `token=桌台token&channel=wechat`

### 6. 支付宝小程序

1. 用支付宝小程序开发者工具打开目录 `miniprogram-alipay`
2. 填写 AppID
3. 修改 `app.js` 中 `apiBase`
4. 扫码参数: `token=桌台token&channel=alipay`

## 扫码点餐流程

```
顾客扫桌台二维码
  → 打开微信/支付宝小程序
  → 识别 qr_token（不可伪造桌号）
  → 提示注册会员
  → 可选开启人脸迎宾（主动授权）
  → 进入菜单选规格
  → 下单支付
  → 厨房 KDS 出餐
```

扫码后会明确提示：

- **注册会员**：历史订单、口味偏好、一键复购
- **人脸迎宾**：仅主动授权后生效，可随时关闭；未授权顾客只做匿名客流

## 环境变量

参见 `.env.example`。真实密钥不可提交 Git。

关键项：

- `WECHAT_APP_ID` / `WECHAT_APP_SECRET` — 微信小程序登录
- `ALIPAY_APP_ID` / `ALIPAY_PRIVATE_KEY` — 支付宝小程序登录
- 支付商户密钥（正式支付用）

未配置密钥时，后端进入开发 mock 登录模式，便于本地联调。

## 测试

```bash
pip install -r tests/requirements.txt
pytest
python -m compileall backend
```

## 生产部署（非 Docker）

1. 本机或服务器安装 MySQL / Redis
2. `uvicorn` / `gunicorn` 跑后端（建议 systemd 守护）
3. `npm run build` 后用 Nginx 托管 `frontend/dist`
4. Nginx 反代 `/api` 到后端（参考 `deploy/nginx.conf`，去掉 Docker 服务名，改为 `127.0.0.1`）
5. 小程序后台配置合法 request 域名（HTTPS）

## 安全说明

- 人脸功能仅支持顾客主动授权，未授权顾客仅匿名客流统计
- 支付金额由服务端从数据库重新计算
- 桌台二维码使用不可预测 token

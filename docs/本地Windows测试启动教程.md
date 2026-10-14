# 本地（Windows）测试启动教程

> **场景：本地调试**（Windows 10/11 + PowerShell）。  
> 与生产（Ubuntu）命令、路径、`.env` 模板均不同，请勿混用。  
> 本文面向：第一次在本机把「库 → 后端 → 后台前端 → 小程序 → pytest」跑通，并做营销/CRM 联调。

---

## 0. 你最终会启动什么

| 进程 | 地址 | 作用 |
|------|------|------|
| MySQL 8 | `127.0.0.1:3306` | 业务库 `smart_noodle_house` |
| Redis 7 | `127.0.0.1:6379` | 缓存 / Leader 锁 / WS PubSub |
| 后端 uvicorn | `http://127.0.0.1:8000` | API；文档 `/docs` |
| 后台 Vite | `http://127.0.0.1:5173` | 管理后台（代理 `/api` → 8000） |
| 微信/支付宝开发者工具 | — | 小程序联调（可选） |
| pytest | 内存 SQLite | 自动化单测（**可不启 MySQL**） |

推荐日常开 **3 个 PowerShell 窗口**：MySQL/Redis 用服务常驻即可，再各开「后端」「前端」。

---

## 1. 前置软件（一次性）

| 软件 | 版本建议 | 说明 |
|------|----------|------|
| Python | **3.11+** | `python --version` |
| Node.js | **≥20.19** 或 **≥22.12** | Vite 8 硬要求；`node -v` |
| MySQL | **8.x** | 字符集 utf8mb4 |
| Redis | **7.x** | Windows 可用 Memurai / WSL Redis / 官方 Windows 移植版 |
| Git | 任意近期版 | 拉代码 |
| 微信开发者工具 | 最新稳定版 | 可选 |
| 支付宝小程序开发者工具 | 最新稳定版 | 可选 |

本仓库 **不使用 Docker**。

在 **PowerShell** 中自检：

```powershell
python --version
node -v
npm -v
mysql --version
redis-cli ping
```

`redis-cli ping` 应返回 `PONG`。若 MySQL/Redis 未装成服务，请先在「服务」里启动，或按各自安装文档启动。

---

## 2. 拿到代码与 Python / Node 依赖

假设仓库在 `E:\SmartNoodleHouse`（按你本机路径改）：

```powershell
cd E:\SmartNoodleHouse

# 建议使用虚拟环境（可选但推荐）
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 后端依赖（若 Pillow 报 No matching distribution：已改为 >=11.3.0,<13，请 pull 最新 requirements）
pip install -r backend\requirements.txt
# 也可单独：pip install "Pillow>=11.3.0,<13"

# 自动化测试额外依赖（pytest 等）
pip install -r tests\requirements.txt

# 后台前端依赖
cd frontend
npm install
cd ..
```

若执行策略禁止脚本：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

---

## 3. 配置 `.env`（本地真源）

```powershell
cd E:\SmartNoodleHouse
copy .env.development.example .env
notepad .env
```

**至少改这几项**（其余本地模板已偏开发友好）：

| 键 | 示例 | 说明 |
|----|------|------|
| `DATABASE_URL` | `mysql+aiomysql://root:你的密码@127.0.0.1:3306/smart_noodle_house?charset=utf8mb4` | 密码含 `@#:` 等须 **URL 编码** |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | 有密码：`redis://:密码@127.0.0.1:6379/0` |
| `APP_HOST` | `0.0.0.0` | 真机调试小程序必须；仅本机可仍用 `127.0.0.1` |
| `APP_PORT` | `8000` | 与小程序、Vite 代理一致 |
| `ALLOW_*_MOCK` | 模板默认 `true` | 本地可 mock 支付/登录/外卖/打印 |
| `DEV_LAN_HOST` | 你的局域网 IP，如 `192.168.1.110` | 与两端 `miniprogram-*/config/env.js` 一致 |
| `JWT_SECRET` / `SECRET_ENCRYPTION_KEY` | 开发可用模板值 | 勿提交真实生产密钥 |

查本机局域网 IP（真机调试用）：

```powershell
ipconfig
# 看「无线局域网适配器」或「以太网」下的 IPv4 地址
```

> `.env` 已 gitignore，**不要提交**。字段说明真源：`backend/app/core/config.py`。

---

## 4. 初始化数据库

### 4.1 全新空库（最常见）

**警告**：`sql/struct.sql` 会 `DROP DATABASE` 再重建，**有业务数据切勿执行**。

在 PowerShell 中（把 `root` / 密码换成你的）：

```powershell
cd E:\SmartNoodleHouse

# 方式 A：交互输入密码
Get-Content sql\struct.sql -Raw -Encoding utf8 | mysql -u root -p --default-character-set=utf8mb4
Get-Content sql\data_sys.sql -Raw -Encoding utf8 | mysql -u root -p --default-character-set=utf8mb4
Get-Content sql\data_custom.sql -Raw -Encoding utf8 | mysql -u root -p --default-character-set=utf8mb4

# 本地联调建议再灌测试种子（禁止用于生产）
Get-Content sql\data_test.sql -Raw -Encoding utf8 | mysql -u root -p --default-character-set=utf8mb4
```

若 `mysql` 不在 PATH，用完整路径，例如：

```powershell
& "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" -u root -p --default-character-set=utf8mb4
```

**导入顺序固定**：

1. `struct.sql` — 结构真源  
2. `data_sys.sql` — 系统必要数据（RBAC、全局设置、平台券等）  
3. `data_custom.sql` — 门店经营种子（SH01 等）  
4. （可选）`data_test.sql` — 仅本地测试  

### 4.2 Alembic 版本号（可选对齐）

全新灌入 `struct.sql` 后，表已是最新结构。若要用 Alembic 记版本，可在结构已齐全时：

```powershell
cd E:\SmartNoodleHouse\backend
python -m alembic stamp head
python -m alembic current
# 期望：0030_customer_lifecycle (head) 或当前仓库最新 revision
```

已有旧库只升结构（不删库）：

```powershell
cd E:\SmartNoodleHouse\backend
python -m alembic upgrade head
```

> 说明：空库从 `0001_baseline` 一路 upgrade 时，若中间某版与「整份 struct 已灌满」冲突，可用 `stamp head`；日常增量以 `upgrade head` + 同步改 `sql/struct.sql` 为准。

### 4.3 管理员账号

开发种子默认常见为 `admin` / `admin123`（以 `data_sys.sql` 实际写入为准）。  
要重置强密码：

```powershell
cd E:\SmartNoodleHouse
$env:ADMIN_INITIAL_PASSWORD = "YourStrongPass123!"
python scripts\初始化管理员.py
```

密码规则：至少 10 位，含字母+数字，且不在弱口令黑名单。

---

## 5. 启动后端（窗口 1）

```powershell
cd E:\SmartNoodleHouse
.\.venv\Scripts\Activate.ps1   # 若用了 venv
cd backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

成功标志：

- 终端无启动校验致命错误  
- 浏览器打开：http://127.0.0.1:8000/docs  
- 健康检查：http://127.0.0.1:8000/api/health （路径以实际路由为准，可在 `/docs` 搜索 health）

改代码会自动 reload；改 `.env` 后需 **Ctrl+C 再启动**。

---

## 6. 启动后台前端（窗口 2）

```powershell
cd E:\SmartNoodleHouse\frontend
npm run dev
```

浏览器打开：http://127.0.0.1:5173  

- Vite 把 `/api`、`/uploads` 代理到 `http://127.0.0.1:8000`  
- 用管理员登录后可测：顾客管理（lifecycle 筛选）、社会贡献、客户分群、营销活动、营销报表  

**营销 CRM 冒烟建议：**

1. 会员 → 顾客管理 → 筛「沉睡 DORMANT」（需夜批或先跑过 lifecycle；无快照时列表可能为空属正常）  
2. 会员 → 营销活动 → 新建活动，填「推荐加权餐品」ID  
3. 会员 → 营销报表 → 刷新 / 导出 CSV  
4. 会员 → 社会贡献 → 本月看板  

---

## 7. 小程序本地调试（可选）

两端须对齐：`miniprogram-wechat` 与 `miniprogram-alipay`。

### 7.1 开发者工具模拟器（最简单）

1. 后端已按上面 `--host 0.0.0.0 --port 8000` 启动  
2. 微信开发者工具打开目录 `E:\SmartNoodleHouse\miniprogram-wechat`  
3. **详情 → 本地设置 → 勾选「不校验合法域名…」**  
4. 编译；API 应自动为 `http://127.0.0.1:8000/api`  

支付宝同理，打开 `miniprogram-alipay`。

### 7.2 真机调试

1. 改两端 `config/env.js` 的 `DEV_LAN_HOST` 为电脑局域网 IP  
2. 项目根 `.env` 的 `DEV_LAN_HOST` 保持一致  
3. 手机与电脑同一 WiFi；后端必须 `0.0.0.0:8000`  
4. 开发者工具「真机调试」；必要时在 `env.js` 设 `DEV_USE_LAN=true`  

更细步骤见：

- `miniprogram-wechat/config/环境说明.txt`  
- `miniprogram-alipay/config/` 下对应说明（若有）  

本地支付/登录可依赖 `.env` 中 `ALLOW_PAYMENT_MOCK` / `ALLOW_AUTH_MOCK`（生产禁止）。

---

## 8. 跑自动化测试（pytest）

单测默认 **内存 SQLite**，一般 **不必** 连本机 MySQL。

```powershell
cd E:\SmartNoodleHouse
.\.venv\Scripts\Activate.ps1
pip install -r tests\requirements.txt

# 全量（耗时较长）
python -m pytest tests\ -q --tb=line

# 营销 / CRM 相关子集（推荐日常）
python -m pytest `
  tests\测试_社会贡献.py `
  tests\测试_营销分群活动.py `
  tests\测试_营销归因与报表.py `
  tests\测试_服务卡.py `
  tests\测试_推荐活动加权与生命周期.py `
  -q --tb=short
```

说明：

- `pytest.ini` 已设 `pythonpath = backend`、`testpaths = tests`  
- 测试文件名形如 `测试_*.py`  
- 控制台中文乱码是 PowerShell/编码问题，**不要**据此判断文件损坏；以 PASSED/FAILED 为准  

---

## 9. 推荐「每天开工」最短路径

```text
1. 确认 MySQL、Redis 服务已启动
2. PowerShell A：
     cd E:\SmartNoodleHouse\backend
     python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
3. PowerShell B：
     cd E:\SmartNoodleHouse\frontend
     npm run dev
4. 浏览器 http://127.0.0.1:5173 登录 admin
5. （可选）微信/支付宝开发者工具打开对应目录
6. （可选）改完业务后跑第 8 节 CRM 子集 pytest
```

---

## 10. 常见问题

| 现象 | 处理 |
|------|------|
| `Can't connect to MySQL` | 检查服务、端口 3306、`DATABASE_URL` 用户密码、库名 |
| Redis 连接失败 / 启动卡住 | `redis-cli ping`；检查 `REDIS_URL` |
| 前端 5173 能开但接口 502 | 后端未起或未监听 8000 |
| 小程序请求失败 / 合法域名 | 勾选不校验域名；真机勿用 `127.0.0.1` |
| `Node` 版本过低无法 build | 升到 ≥20.19 或 ≥22.12 |
| alembic `bind parameter '50'` | 已修 `0001_baseline` 用 `exec_driver_sql`；请 pull 最新代码 |
| 空库 upgrade 卡在中间版本 | 优先用 `struct.sql` 灌库后 `alembic stamp head` |
| 改了系统设置不生效 | 部分走 DB `system_settings`；热更后清缓存/重登；改 `.env` 需重启 uvicorn |
| PowerShell 管道中文乱码 | SQL 文件本身是 UTF-8；用 `--default-character-set=utf8mb4`；勿用终端乱码判断文件坏了 |

---

## 11. 与生产的硬边界（避免踩坑）

| 项 | 本地 | 生产 |
|----|------|------|
| 系统 | Windows + PowerShell | Ubuntu 22.04 + bash |
| 环境模板 | `.env.development.example` | `.env.production.example` |
| Mock | 可开 | **禁止** |
| 对外 | 局域网 / 127.0.0.1 | HTTPS + Nginx |
| 部署文档 | 本文 | `docs/部署说明.md` |

---

## 12. 相关文件索引

| 路径 | 用途 |
|------|------|
| `.env.development.example` | 本地环境变量模板 |
| `sql/struct.sql` / `data_sys.sql` / `data_custom.sql` / `data_test.sql` | 库结构与种子 |
| `backend/requirements.txt` | 后端依赖 |
| `tests/requirements.txt` | pytest 依赖 |
| `frontend/package.json` | `npm run dev` / `build` |
| `miniprogram-*/config/env.js` | 小程序 API 地址 |
| `README.md` | 总览与功能说明 |
| `docs/部署说明.md` | **生产**部署（非本文） |

---

**验收清单（本地联调算成功）：**

- [ ] `/docs` 能开  
- [ ] `5173` 能登录后台  
- [ ] 顾客 / 营销活动 / 营销报表菜单可进  
- [ ] （可选）小程序模拟器能打到 `127.0.0.1:8000/api`  
- [ ] CRM 子集 pytest 全绿  

完成以上即可在本机持续开发与测试。

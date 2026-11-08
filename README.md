# SmartNoodleHouse｜智能面馆经营系统

微信 / 支付宝小程序扫码点餐、统一订单与支付、POS 收银、厨房 KDS、顾客叫号、会员营销、多门店、外卖平台对接及人脸迎宾。

> 项目尚处于上线前开发、集成与验收阶段。功能说明必须以当前 main 代码、数据库迁移、环境配置和测试证据为准；第三方真实商户联调、平台审核、生产凭据和部署验收不能仅凭代码构建通过而视为完成。
> 部署方式：原生 Ubuntu/systemd + Nginx + MySQL + Redis，不使用 Docker。

## 文档入口

| 文档 | 内容 |
|---|---|
| [项目说明文档](项目说明文档.md) | 系统全景、模块职责、开发和部署导航 |
| [架构设计](docs/架构设计.md) | 分层架构、组件边界、数据流 |
| [数据库设计](docs/数据库设计.md) | 表结构、初始化、迁移和数据约束 |
| [部署说明](docs/部署说明.md) | Ubuntu、Nginx、systemd、生产配置 |
| [后台操作手册](后台操作手册.md) | 门店、员工、菜品、订单、营销和运营操作 |
| [配置检查清单](配置检查清单.md) | 环境变量与发布前配置核验 |
| [支付对接](docs/支付对接.md) / [退款流程](docs/退款流程.md) | 支付、回调、退款与账务边界 |
| [多门店管理](docs/多门店管理.md) / [外卖对接](docs/外卖对接.md) | 租户隔离、总部覆盖、平台门店与 SKU 映射 |
| [KDS 与取餐叫号](docs/厨房KDS与取餐叫号.md) | 厨房工作站和顾客显示屏 |
| [安全说明](docs/安全说明.md) | 认证、授权、密钥、生产安全 |
| [审计基线](docs/audit/BASELINE.md) / [问题台账](docs/audit/REPAIR_TRACKER.md) | 仓库基线和修复追踪 |
| [测试报告](docs/audit/TEST_REPORT.md) | 历史测试与当前可追溯测试证据 |
| [依赖审计](docs/依赖包审计与升级基线.md) | 依赖、锁文件、重复引用和升级基线 |
| [通用审计与缺陷预防手册](docs/通用项目审计与缺陷预防手册.md) | 可复制到其他项目的工程审计标准 |
| [通用上线审计清单模板](docs/通用项目上线审计清单模板.md) | 新项目上线前逐项验收模板 |

## 技术栈与目录

- 后端：Python 3.11+、FastAPI、SQLAlchemy 2.x、Alembic。
- 数据库：MySQL 8.x；Redis 7.x 用于缓存、限流、锁和跨进程协调。
- 管理后台：Vue 3、TypeScript、Vite、Element Plus。
- 小程序：微信原生小程序目录 miniprogram-wechat/；支付宝原生小程序目录 miniprogram-alipay/。
- 广告追踪 DLL：Rust crate 位于 tests/广告追踪/ad_tracking_dll/，通过 Python FFI 测试。
- 摄像头：camera/，支持海康 RTSP 接入；设备型号和网络部署需按现场环境验收。
- 实时：WebSocket 用于厨房、订单和顾客显示场景。
- 部署：Ubuntu + Nginx + systemd；不使用 Docker。

主要目录：
- backend/：API、服务、模型、任务、迁移和启动校验。
- frontend/：管理后台。
- miniprogram-wechat/、miniprogram-alipay/：双端小程序。
- camera/：摄像头与人脸相关能力。
- sql/：初始化 DDL 与种子数据。
- tests/：自动化测试与 Rust DLL。
- scripts/、nginx/、systemd/：部署与运维。
- docs/：架构、业务、审计、操作和部署文档。

## 核心业务链路

1. 顾客通过门店/桌台二维码进入微信或支付宝小程序；服务端校验扫码 token 并解析门店上下文。
2. 服务端读取菜单、规格、库存和营销规则，重新计算订单金额；客户端金额不作为支付依据。
3. 订单进入统一订单状态机，调用对应支付渠道；支付回调验签、幂等处理后更新订单和账务状态。
4. 订单进入 KDS；厨房员工按权限推进制作状态；顾客端/叫号屏展示进度。
5. 会员、积分、优惠券和推荐按授权与门店隔离规则执行。
6. 外卖平台 webhook 经签名/来源检查和幂等 inbox 进入统一订单流；菜单、库存和状态同步需真实平台凭证验证。
7. 人脸迎宾属于敏感个人信息处理：必须主动授权、最少采集、可撤回；未授权用户不得建立可识别身份档案。

## 数据库初始化与升级

全新隔离环境初始化前，先检查 SQL 文件是否含清库操作，并确认目标数据库不是生产数据：

```bash
mysql -u root -p < sql/struct.sql
mysql -u root -p < sql/data_sys.sql
mysql -u root -p < sql/data_custom.sql
python scripts/初始化管理员.py
```

- sql/struct.sql：结构真源和 canonical schema。
- sql/data_sys.sql：系统必要种子；包含清理/重建种子数据操作，必须确认目标库。
- sql/data_custom.sql：门店经营种子。
- sql/data_test.sql：仅测试环境。
- 已有数据库升级使用 backend/Alembic 迁移；执行前备份并在隔离副本演练。禁止对生产库直接运行全新初始化脚本。

## 本地开发

Windows PowerShell：

```powershell
# 项目根目录
python --version
node --version
npm --version

# 后端（按当前锁文件和开发说明安装）
cd backend
python -m pip install -r requirements.txt
python -m alembic upgrade head
python -m uvicorn app.main:app --reload --app-dir .

# 管理后台（新终端）
cd frontend
npm ci
npm run dev
```

小程序分别使用微信开发者工具打开 miniprogram-wechat/，支付宝开发者工具打开 miniprogram-alipay/。环境地址按各自 config/env.js 和环境说明配置；正式版必须使用 HTTPS 合法域名，不能把本机 IP 或开发 Mock 用于生产。

## 测试与质量门禁

按改动范围执行对应测试，并在合并前执行 CI 等价命令。典型检查：
- 前端：npm ci、重复导入脚本、依赖使用审计、npm ls、npm test、npm run build、npm audit。
- Python：锁文件一致性、格式/静态检查、pytest、真实 MySQL/Redis 集成和 Alembic 升降级演练。
- Rust：cargo metadata --locked、cargo test --locked --all-targets、cargo build --release --locked、RustSec 审计。
- 微信/支付宝：JS 语法检查、开发者工具构建、真机扫码/授权/支付验收。
- 生产：配置启动校验、备份恢复、HTTPS、回调验签、监控告警和回滚演练。

测试报告必须写明 commit SHA、环境、命令、通过/失败/跳过数量。历史测试结果不可冒充当前 main 的结果。

## 安全底线

- 不提交 .env、真实密码、API Secret、商户私钥、平台证书或人脸数据。
- 不在文档中写可用默认管理员密码或有效访问 token；首次管理员密码通过安全流程初始化。
- 生产关闭所有 Mock/debug/reload，使用最小权限数据库账号、Redis ACL/强认证和安全网络绑定。
- 生产公网仅开放必要 HTTPS 入口；MySQL、Redis、RTSP 和内部管理端口不直接暴露公网。
- 支付回调必须验签、校验金额和商户身份、幂等落库；退款和库存变更必须可审计。
- 多门店每个查询、缓存键、后台任务、WebSocket 和导出均实施服务端门店隔离。
- 人脸数据遵循主动授权、目的限制、最少采集、加密、审计、保留期限和删除机制。

## 当前验证证据（2026-11-08，本机 Windows）

以下为项目维护者提供的本机执行输出，不等同于 GitHub CI 或生产验收：
- Node.js 24.14.0；npm 已对齐到 11.19.1。
- frontend npm ci 成功；npm audit 报告 0 vulnerabilities。
- 重复导入检查扫描 195 个 JS/TS/Vue 文件，通过。
- 依赖使用审计扫描 94 个源码/配置文件，通过（13 个直接依赖）。
- 前端 Vitest：3 个测试文件、14 项测试全部通过。
- vue-tsc + Vite 生产构建成功；仍有一个约 892 KB 的入口 JS chunk 超过 500 KB warning，需要后续做拆包与体积优化；这是 warning，不是构建失败。
- Rust cargo metadata --locked 成功；7 项单元测试通过；benchmark target 成功；Windows release DLL 构建成功，文件大小 282,624 bytes。
- 上述结果尚不能替代 Python 全量测试、真实 MySQL/Redis 集成、双端小程序平台构建、第三方沙箱和生产部署验收。

## 发布状态

本仓库仍需以 main 最新 commit 的 CI、真实数据库/Redis 测试、第三方平台沙箱和生产配置验收为准。任何未执行项目都应明确列为待验证或外部阻塞，不能写成“已上线”。

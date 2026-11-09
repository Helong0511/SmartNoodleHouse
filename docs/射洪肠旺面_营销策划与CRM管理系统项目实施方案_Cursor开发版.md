> **文档维护说明（2026-11-08）**：**项目状态勘误**：SmartNoodleHouse 当前仍是上线前开发/集成项目，不能描述为“已上线能力”。本文属于营销/CRM 规划与实施方案，只有经当前代码、迁移、API、后台页面和测试验证的条目才可标记已实现；促销活动、门店参与范围、抢购并发、微信/支付宝通用二维码和通知播报等需求需逐项对应当前实现及验收证据。文中 Canvas 路径、工作包顺序和旧版本号仅在仓库实际存在时有效。 当前实现以 main 代码、CI、数据库迁移和 README 为准。专题设计不等于已生产验收；未验证项标记待验证。禁止记录有效密钥、密码或访问 token。
>
> ---
>
# 射洪肠旺面 · 营销策划与 CRM 管理系统项目实施方案（Cursor 开发版）

> **文档版本**：v3.2（可执行工作包 + 作战 Canvas 对齐）  
> **配套 Canvas**：Cursor 旁路作战板 `canvases/crm-exec-playbook.canvas.tsx`（摘要与勾选；细则与验收以本文 §23 为准）。  
> **执行入口**：先完成 §23 WP-A0（零开发），再按 WP-B1→B3→B2→B4→C1→C2 用 §20 提示词逐包开工。  
> **文档定位**：在 **SmartNoodleHouse 当前主线实现**之上建设营销闭环；禁止从零重建多租户 / 订单 / 支付。  
> **现网校对**：以 `sql/struct.sql`、`sql/data_sys.sql`、`backend/app/tasks/scheduler.py`、`backend/app/services/retention_service.py`、`frontend` 路由与 `AdminLayout` 菜单为准。  
> **关联文档**：`docs/架构设计.md`、`docs/多门店管理.md`、`docs/面馆管理系统_提示词库.md`、`docs/数据库设计.md`、菜单落地版 v4。  
> **结构真源**：`struct.sql` → `data_sys.sql` → `data_custom.sql`（可选 `data_test.sql`）。**禁止 `tenant_id`。**  
> **状态图例**：✅ 已实现可用 · 🔧 已有需增强 · 🆕 待新建 · ❌ 明确不做

------------------------------------------------------------------------

## 目录

0. [文档约定](#0-文档约定)  
1. [项目目标与 KPI](#1-项目目标与-kpi)  
2. [现网全量能力清单（真源）](#2-现网全量能力清单真源)  
3. [差距、优先级与不做清单](#3-差距优先级与不做清单)  
4. [品牌营销定位](#4-品牌营销定位)  
5. [目标客群与时段矩阵](#5-目标客群与时段矩阵)  
6. [社会贡献营销](#6-社会贡献营销完整落地)  
7. [节日与活动引擎](#7-节日与活动引擎)  
8. [新客获取](#8-新客获取)  
9. [老客经营与留存](#9-老客经营与留存)  
10. [储值 / 早餐卡 / 积分](#10-储值--早餐卡--积分)  
11. [多门店与营销治理](#11-多门店与营销治理)  
12. [功能模块详设 M1–M7](#12-功能模块详设-m1m7)  
13. [支撑模块（订单/POS/迎宾/外卖/菜单）](#13-支撑模块订单pos迎宾外卖菜单)  
14. [增量数据库设计](#14-增量数据库设计)  
15. [API / 后台 / 小程序改造](#15-api--后台--小程序改造)  
16. [KPI 与归因](#16-kpi-与归因)  
17. [端到端旅程总图](#17-端到端旅程总图)  
18. [分阶段实施与验收](#18-分阶段实施与验收)  
19. [配置项与调度任务清单](#19-配置项与调度任务清单)  
20. [Cursor 增量提示词](#20-cursor-增量提示词)  
21. [风险、合规与铁律](#21-风险合规与铁律)  
22. [附录：概念迁移对照](#22-附录概念迁移对照)  
23. [可执行工作包（按此直接开工）](#23-可执行工作包按此直接开工)

------------------------------------------------------------------------

# 0. 文档约定

| 角色 | 优先阅读 |
|------|----------|
| 店长/运营 | **23（WP-A0）**、1、4、5、6、7、8、9、10、17、18、19、21 |
| 产品 | **23**、2、3、6–16、18 |
| 研发/Cursor | **23 + 20**、2、3、6、11–15、19 |

**工程铁律**

1. 增量改造：不破坏订单、支付、退款、外卖、KDS、门店隔离。  
2. 单品牌多门店：`store_id` + `store_scope`；❌ `tenant_id`。  
3. 券/会员/储值/推荐/留存任务已存在 → **编排与增强**，禁止平行重建。  
4. 微信/支付宝小程序改动两端对齐（`.cursor/rules/miniprogram-wechat-alipay-sync.mdc`）。  
5. 测试在 `tests/`；种子禁止硬编码自增 `id`。  
6. 本文「✅」以代码为准；改前先读对应源文件。

------------------------------------------------------------------------

# 1. 项目目标与 KPI

## 1.1 业务目标

将射洪肠旺面从「能数字化点餐的面馆」升级为：

- 社区型特色餐饮品牌（味道稳 + 温度 + 公益口碑）  
- 可运营的会员资产（入会 → 复购 → 储值 → 召回）  
- 可配置营销中心（活动、分群、任务、公益台账、报表）  
- 可多店复制（先直营多店；加盟结算后置）

闭环：`产品拉新 → 营销复购 → CRM 沉淀 → 数据辅助 → 多店扩张`

## 1.2 90 日盯盘 KPI

| 指标 | 目标 | 现网取数基础 |
|------|------|--------------|
| 扫码入会率 | ≥30% | MEMBER 新增 / 扫码相关（UV 可后续补） |
| 会员销售占比 | ≥50% | orders/payments × member_status |
| 7 日二购率 | ≥25% | 订单序列 / last_order_at |
| 30 日复购率 | ≥35% | 同上 |
| 券核销率 | ≥40% | customer_coupons |
| 储值转化率 | ≥15% | recharge_orders |
| 加料率 | ≥35% | order_items + options |
| 公益留痕率 | 100% | 🆕 台账（上线后） |

## 1.3 业界原则 → 本仓库

| 原则 | 落点 |
|------|------|
| 扫码/支付入会 | 小程序 + POS + 欢迎券 ✅ |
| 身份稳再自动化 | customers/identities ✅；活动引擎 🆕 |
| 总部账本+门店活动 | 积分储值中央；券/活动 store_id |
| 四类 SOP | 新客/生日/沉睡/余额 ✅ 骨架；旅程 UI 🔧 |
| 储值锁频次 | recharge_packages ✅ |

------------------------------------------------------------------------

# 2. 现网全量能力清单（真源）

> 后续章节所有「✅」均以本章为准。

## 2.1 端与目录

| 端 | 路径 |
|----|------|
| 管理后台 | `frontend/`（Vue3） |
| 微信/支付宝小程序 | `miniprogram-wechat/` `miniprogram-alipay/` |
| H5 | `/scan` `/order*` `/member` |
| API | `backend/app/` |
| SQL | `sql/struct.sql` + data_* |
| 调度 | `scheduler.py` + Redis Leader |

## 2.2 后台菜单 → 营销角色

| 分组 | 菜单 | 路由 | 状态 |
|------|------|------|------|
| — | 数据概览 | `/dashboard` | ✅ 营收；🔧 另建营销报表 |
| 会员 | 顾客管理 | `/customers` | ✅；🔧 分群/公益/生命周期 |
| 会员 | 会员等级 | `/member-levels` | ✅ |
| 会员 | 会员储值 | `/wallet` | ✅ |
| 会员 | 优惠券 | `/coupons` | ✅ 模板/发放/里程碑 |
| 营业 | 推荐效果 | `/recommend-report` | ✅；🔧 活动维 |
| 营业 | 收银点餐 | `/pos` | ✅；🔧 服务卡/公益核验 |
| 营业 | 订单/支付/账本/日结/退款 | 对应路由 | ✅ 交易底座 |
| 菜品 | 分类时段排序 | `/category-schedules` | ✅ 早餐/闲时 |
| 迎宾 | 摄像头/迎宾日志 | `/cameras` `/welcome-logs` | ✅；🔧 话术 |
| 外卖 | 订单/映射/同步… | `/delivery/*` | ✅ 公域引流 |
| 系统 | 设置/权限/审计 | `/settings` 等 | ✅ 欢迎/生日等键 |
| — | 营销中心 | — | 🆕 |

## 2.3 小程序（双端）

扫码登录、菜单+recommend-strip、结算用券、结果页反馈、会员中心（券/储值/人脸/偏好）、充值、订单、订阅取餐 ✅；🔧 公益入口与营销订阅模板。

## 2.4 已有表（会员营销相关）

`customers`、`customer_identities`、微信/支付宝账号、`member_levels`、`point_ledger`、`coupon_templates`、`customer_coupons`、`order_milestone_rewards`、`recharge_packages`、`recharge_orders`、`balance_ledger`、`customer_preferences`、`customer_taste_profiles`、`customer_behavior_events`、`customer_favorite_products`、`ab_experiments`、`ab_assignments`、`face_profiles`、`welcome_logs`、`stores`、`store_settings`、`system_settings`、`financial_ledger`、`orders`…

🆕 见第 14 章：公益/分群/活动等。

## 2.5 种子数据（data_sys）

- 等级：普通/银卡200/金卡500/钻石1000（折扣 1/0.98/0.95/0.90）  
- 券：`新客满20减3`（仅注册/新客活动）、场景专属「××满20减3」（会员日/节日等勿复用新客名）、`满50享打9折`、`10元代金券`、里程碑满 10/20/50/100/200 单减免  
- 储值：充 50 得 55、100 得 120、200 得 250  
- 设置：`welcome_coupon_name`、`birthday_coupon_name`

## 2.6 已实现自动营销（文档易漏）

| 能力 | 代码 | 说明 |
|------|------|------|
| 欢迎券 | `CouponService.issue_welcome_coupon` | ✅ |
| 生日券 | `issue_birthday_coupon` | ✅ |
| 里程碑 | `issue_milestones_after_paid` + 退款回收 | ✅ |
| 沉睡唤醒 | `retention_service.run_sleep_wake`；任务 `sleep_member_wake` 每日 10:05 | ✅ 30/90 档；模板名空=关 |
| 余额提醒 | `run_balance_low_remind`；`balance_low_remind` 每日 11:05 | 🔧 现仅日志+Redis 防重，订阅消息待接 |
| 口味/夜批 | `learn_taste_profiles`、`night_data_batch` | ✅ |
| 支付副作用 | `paid_side_effects` outbox | ✅ |

留存键：`sleep_wake_coupon_name_30`、`sleep_wake_coupon_name_90`、`balance_low_threshold`

## 2.7 关键服务

`coupon_service`、`retention_service`、`recommendation_service`、`OrderPriceCalculator`、`store_scope`、`customer_merge_service`、`balance_service`

------------------------------------------------------------------------

# 3. 差距、优先级与不做清单

| 优先级 | 缺口 | 依赖 |
|--------|------|------|
| P0 | 公益身份+规则+台账+POS | customers/coupons/计价/store_scope |
| P0 | 活动+分群最小发券闭环 | CouponService |
| P0 | 新人礼包多券 | welcome 增强 |
| P0 | Settings 暴露留存三键并配齐模板 | retention 已存在 |
| P1 | 订阅消息营销（含余额真推送） | subscribe + message_logs |
| P1 | RFM/生命周期筛选与报表 | last_order_at/订单 |
| P1 | 推荐活动加权 | recommendation_service |
| P2 | 次卡、企微、审批流、加盟 | — |

❌ `tenant_id`；重建券/顾客/推荐；全场打折；测试数据进生产种子；本阶段加盟分账。

------------------------------------------------------------------------

# 4. 品牌营销定位

**定位语**：一碗射洪老味道，一家有温度的社区面馆。  
标签：招牌肠旺面 · 家庭友好 · 社区公益。  
策略：8/11 元锚点托底；出品+会员+公益护城河；禁极限词与全场打折（对齐提示词库）。

------------------------------------------------------------------------

# 5. 目标客群与时段矩阵

| 客群 | 时段 | 抓手 | 现网 | 待补 |
|------|------|------|------|------|
| 早餐 | 06:30–09:00 | 套餐/储值/券 | ✅ 时段排序 | 🔧 券时段 |
| 午餐 | 11:00–14:00 | 欢迎券/加料推荐/里程碑 | ✅ | 🔧 礼包多券 |
| 下午 | 14:00–17:00 | 下午茶/骑手窗 | ✅ 时段 | 🆕 公益 |
| 家庭 | 晚市周末 | 套餐券 | ✅ 餐品 | 🔧 活动 |
| 沉睡 | — | 唤醒券 | ✅ sleep_wake | 🔧 UI/推送 |
| 公益身份 | 规则窗 | 第 6 章 | — | 🆕 |

------------------------------------------------------------------------

# 6. 社会贡献营销（完整落地）

> 定位：公益是品牌差异化与同城口碑引擎，**不是无边界长期补贴**。  
> 原则：凭证核验 → 身份打标 → 权益命中 → 限次核销 → 台账可查 → 成本可控。  
> 与现网对齐：复用 `customers` / `coupon_templates` / `customer_coupons` / 门店 `store_id`；**不引入 `tenant_id`**。  
> 口径对齐：`docs/面馆管理系统_提示词库.md` 公益立场与边界条款。

------------------------------------------------------------------------

## 6.1 业务目标与边界

### 目标

1. 让环卫工、外卖骑手、老人、教师、学生等群体「被看见、被优待」。  
2. 形成可传播的社区故事（爱心日、爱心食堂挂牌），拉动自然到店。  
3. 所有优惠可审计：谁、何时、何店、何凭证、享受了什么、成本多少。

### 边界（必须写进规则与 UI）

| 边界项 | 要求 |
|--------|------|
| 法律属性 | 自愿经营优惠，非法定义务 |
| 核验凭证 | 工牌/工作服/老年证/学生证/教师证/准考证等，规则写死 |
| 每日限次 | 默认每人每店每日 1 次主权益（可按身份覆盖） |
| 产品范围 | 写明适用 SKU / 分类；禁「全场任意」无上限 |
| 叠加规则 | 默认：公益折扣 **不可与** 会员折、优惠券同享（可配置） |
| 留痕 | 每次核销写公益台账；可导出月报 |
| 成本 | 单次补贴上限 + 月预算告警 |

### 官方挂牌优先（运营动作）

- 优先申请「外卖骑手爱心食堂」等官方挂牌。  
- 软件预留挂牌门店标记与对外文案位。

------------------------------------------------------------------------

## 6.2 身份类型与权益矩阵（可配置）

| 身份 code | 名称 | 建议核验凭证 | 常驻权益（示例） | 限次 | 备注 |
|-----------|------|--------------|------------------|------|------|
| SANITATION | 环卫工 | 环卫工牌 / 工作服 | 全天 **8 折**（指定清单） | 1 次/人/店/日 | 每月 10 日发豆浆券 |
| RIDER | 外卖骑手 | 工服 / 平台工牌 | 闲时 **8.5 折** + 歇脚服务 | 1 次/人/店/日 | 爱心食堂话术 |
| ELDER | 老人(≥60) | 身份证 / 老年证 | **8.5 折** + 免费加面（煮软） | 1 次/人/店/日 | 重阳可叠加活动券 |
| TEACHER | 教师 | 教师证 / 工牌 | 教师节免费指定面 | 节日优先 | 平日弱权益防刷 |
| STUDENT | 学生 | 学生证 | 点主食送卤蛋 | 1 次/人/店/日 | — |
| EXAMINEE | 考生 | 准考证 | 考季免费指定面 | 窗口内限次 | 有起止日期 |
| SOLDIER | 军人/退役 | 相关证件 | 八一免费指定面 | 节日 | 可选 |
| MEDICAL | 医护 | 工作证 | 可选闲时折扣 | 可选 | 后期 |

权益落地形态（只允许 3 类，避免第四套计价）：

1. **身份折扣**：`social_benefit_rules` → 独立折扣通道（不改 `member_levels.discount_rate`）。  
2. **公益券**：复用 `coupon_templates` + `customer_coupons`。  
3. **赠品行**：0 元加料（卤蛋、加面），台账 `GIFT_ITEM`。

------------------------------------------------------------------------

## 6.3 端到端业务流程

### A. 身份认定

```text
顾客到店 / 小程序
  → POS「公益核验」或小程序「申请公益身份」
  → customer_social_identities = PENDING
  → 店长审核 → ACTIVE（可设有效期）
  → 拒绝 REJECTED / 过期 EXPIRED
```

- 证件图仅店长/超管可读；日志脱敏。  
- 同一 `customer + identity_code` 仅一条 ACTIVE。

### B. 权益核销

```text
结算前 preview（身份 × 时段 × 门店 × 餐品 × 限次）
  → 确认使用
  → 计价：公益折扣 XOR 会员折 XOR 券（按 stack_policy）
  → 支付成功写 social_benefit_redemptions
  → 需要时 CouponService.issue_by_template_id（幂等键）
```

### C. 爱心日 / 节日

- 每月 10 日调度：环卫 ACTIVE 发豆浆券。  
- 节日窗口：启用规则或批量发券。

### D. 公示与月报

- 后台公益看板：人次、补贴、身份分布、门店对比。  
- 对外仅聚合数，不展示个人。

------------------------------------------------------------------------

## 6.4 软件实现总览

| 层 | 路径 | 职责 |
|----|------|------|
| 数据 | `sql/struct.sql` + 种子 | 公益表；Alembic 仅派生 |
| 服务 | `social_benefit_service.py`（新） | 打标、审核、命中、限次、台账 |
| 计价 | `OrderPriceCalculator`（扩展） | `social_rule_id` / auto |
| 发券 | `CouponService.issue_by_template_id` | 爱心券幂等 |
| API | `/api/social-benefits` | 申请/审核/预览/台账 |
| 后台 | 顾客详情 + `SocialBenefits.vue` | 规则/待审/台账/看板 |
| POS | `PosOrder.vue` | 10 秒核验路径 |
| 小程序双端 | 会员中心入口 + 结算提示 | 行为对齐 |
| 调度 | `social_love_day_issue` 等 | 发券/过期/预算告警 |
| 测试 | `tests/测试_社会贡献*.py` | 限次/互斥/隔离/幂等 |

------------------------------------------------------------------------

## 6.5 数据库（公益增量）

### `social_identity_types`

`code UNIQUE, name, sort_order, status, default_verify_hints JSON`

### `customer_social_identities`

`customer_id, identity_code, store_id, status(PENDING/ACTIVE/REJECTED/EXPIRED/REVOKED), verify_method, credential_note, credential_media_url, valid_from/to, reviewed_by, reject_reason`

### `social_benefit_rules`

`store_id NULL=总部, identity_code, time_windows, weekdays, date_from/to, benefit_type(ORDER_DISCOUNT|COUPON|GIFT_ITEM|FREE_PRODUCT), discount_rate, coupon_template_id, gift_product_id, applicable_* JSON, daily_limit, monthly_budget, stack_policy, max_subsidy_amount, priority, status`

### `social_benefit_redemptions`

`customer_id, store_id, order_id, identity_code, rule_id, benefit_type, subsidy_amount, coupon_id, operator_user_id, idempotency_key UNIQUE, status(NORMAL/REVERSED), redeemed_at, meta JSON`

**复用**：券模板、发券幂等、`store_scope`、`operation_logs`。  
**禁止**：在 `customers` 上堆单一 `social_type` 字段。

------------------------------------------------------------------------

## 6.6 规则引擎要点

1. 预览：ACTIVE 身份 → 窗口匹配 → 餐品命中 → 限次 → 预算 → 叠加策略。  
2. 计价顺序：小计 →（公益 **或** 会员折）→ 券 → 储值支付。  
3. 订单快照 `pricing_snapshot.social`；退款将 redemption 标 `REVERSED` 并回滚当日次数。  
4. 默认 **分店分别限次**。

------------------------------------------------------------------------

## 6.7 API（建议）

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| POST | `/identities/apply` | 顾客 | 申请 |
| GET | `/identities/mine` | 顾客 | 我的身份 |
| GET | `/identities` | `customer:read` | 待审列表 |
| POST | `/identities/{id}/review` | `customer:manage` | 审核 |
| POST | `/identities/pos-verify` | POS | 当场认定 |
| POST | `/preview` | 顾客/收银 | 预览 |
| GET/PUT | `/rules` | 店长/超管 | 规则 |
| GET | `/redemptions` | `customer:read` | 台账 |
| GET | `/dashboard` | 店长 | KPI |
| POST | `/admin/issue-love-day` | 超管/系统 | 补跑发券 |

------------------------------------------------------------------------

## 6.8 风控与成本

- 限次、证件保留期清理、员工冒领审计、服务端强制叠加策略。  
- 成本示例：15 元面 8 折 → 单次补贴约 3 元；日 20 人 ≈ 60 元；月约 1800 元（未计豆浆）。  
- `monthly_budget`：超限 `BLOCK` 或 `WARN`。

## 6.9 公益分阶段

- **P0**：表 + POS 认定 + 环卫 8 折一条规则 + 台账 + 测试。  
- **P1**：骑手窗、老人、学生赠品、小程序审核、爱心日发券、看板。  
- **P2**：考生/节日窗、预算告警、公示页、提示词库 `welfare_stats` 打通。

## 6.10 公益专用 Cursor 提示词

```text
在 SmartNoodleHouse 增量实现「社会贡献营销」，禁止重建多租户/订单/支付。
1. 只改 sql/struct.sql + 幂等种子；禁止硬编码自增 id。
2. 复用 CouponService.issue_by_template_id 与 store_scope。
3. 新增 social_identity_types、customer_social_identities、social_benefit_rules、social_benefit_redemptions。
4. 扩展 OrderPriceCalculator：公益折扣通道，默认与会员折/券互斥可配置。
5. 后台 + POS + 微信/支付宝小程序同步。
6. scheduler 爱心日发券，幂等键防重。
7. tests/ 覆盖限次、互斥、退款回滚、门店隔离。
8. 不引入 tenant_id；不推翻 customers/coupons。
```

------------------------------------------------------------------------

------------------------------------------------------------------------

# 7. 节日与活动引擎

## 7.1 与现网关系

| 能力 | 状态 | 说明 |
|------|------|------|
| 欢迎/生日/里程碑/沉睡唤醒发券 | ✅ | 固定逻辑，分散在 CouponService / retention_service |
| 可配置节日活动、规则、任务、预算、频控、归因 | 🆕 | 活动引擎 |
| 爱心日豆浆 | 🆕 | 可先单独 cron，再纳入活动 |

**原则**：引擎只调用 `CouponService.issue_by_template_id` / 现网副作用，**禁止**复制发券实现。

## 7.2 节日日历（运营内容 → 活动模板种子）

| 节点 | 主题 | 策略 | 权益锚点 | campaign_type |
|------|------|------|----------|---------------|
| 春节 | 回家吃碗家乡面 | 返乡券包 | 豆浆/卤蛋 | FESTIVAL |
| 妇女节/母亲节 | 有温度的甜 | 女性/家庭 | 红糖小汤圆 | FESTIVAL |
| 儿童节 | 小份快乐 | 儿童小份抄手 | 小份 SKU | FESTIVAL |
| 高考/中考 | 考生加油 | 准考证免费面 | 联动 EXAMINEE | FESTIVAL+公益 |
| 教师节 | 感念师恩 | 教师免费/券 | TEACHER | 同上 |
| 八一 | 拥军 | 军人免费面 | SOLDIER | 同上 |
| 重阳 | 长寿面 | 老人长寿面 | 粥/汤圆 | 同上 |
| 冬至 | 饺/抄手节 | 套餐促销 | 套餐 | TIME_SLOT |
| 每月 10 日 | 环卫爱心日 | 豆浆券 | ≈0.8 元 | SOCIAL |
| 会员日 | 老客回访 | 轻券 | 卤蛋 | MEMBER_DAY |

## 7.3 领域模型与状态机（🆕）

```text
marketing_campaigns
  DRAFT → SCHEDULED → RUNNING → PAUSED → ENDED / CANCELLED
  ├─ marketing_rules（SEGMENT|EVENT|CRON|WINDOW|SOCIAL_LINK）
  ├─ marketing_tasks（ISSUE_COUPON|ISSUE_BUNDLE|SEND_SUBSCRIBE|TOGGLE_SOCIAL_RULE|BOOST_RECO_SKU|TAG_CUSTOMER）
  └─ campaign_attributions
```

| 状态 | 行为 |
|------|------|
| DRAFT | 可编辑，不对客 |
| SCHEDULED | 到 start_at → RUNNING |
| RUNNING | 规则生效，任务可消费 |
| PAUSED | 停新发券/推送；已发卡券仍可用 |
| ENDED/CANCELLED | 只读复盘；未发任务取消 |

## 7.4 规则 / 任务 / 频控

| rule_type | 示例 |
|-----------|------|
| SEGMENT | 命中分群入队 |
| EVENT | MEMBER_ACTIVATED / ORDER_PAID / BIRTHDAY |
| CRON | 每月 10 日 06:00 |
| WINDOW | 冬至三天角标 |
| SOCIAL_LINK | 启用某公益规则 |

| action_type | 幂等键建议 |
|-------------|------------|
| ISSUE_COUPON | `mkt:{campaign}:{customer}:{template}` |
| ISSUE_BUNDLE | `mkt:{campaign}:{customer}:bundle:{ver}` |
| SEND_SUBSCRIBE | `msg:{campaign}:{customer}:{tpl}` |

频控：`marketing_weekly_cap` 默认 3；取餐订阅 `tx=1` 不占额度。  
预算：发券按面额×预计核销率预占，核销实扣。

## 7.5 现网固定任务演进

| 现网任务 | 演进 |
|----------|------|
| sleep_member_wake | P0 保留代码；P1 标为系统旅程「沉睡唤醒」 |
| balance_low_remind | P1 接订阅后纳入旅程 |
| welcome / birthday | 保持服务内触发；营销中心只读卡片 |

## 7.6 运营 SOP

T-14 建活动+券+预算 → T-7 物料话术 → T-1 测试客预演 → T+0 RUNNING → T+3 复盘核销/增量/成本。

## 7.7 验收

1. DRAFT 不对客。 2. 到点 RUNNING/过期 ENDED。 3. 同幂等键不重复发券。  
4. 超频控 SKIPPED。 5. PAUSED 不再 ISSUE。 6. 与 WELCOME 幂等命名空间不冲突。

------------------------------------------------------------------------

# 8. 新客获取

## 8.1 ✅ 现网能力

| 能力 | 位置 |
|------|------|
| GUEST→MEMBER | customers.member_status |
| 欢迎券 | welcome_coupon_name →「新客满20减3」 |
| 扫码点餐全链路 | 双端小程序 |
| POS 绑客下单 | PosOrder |
| 支付副作用 | paid_side_effects outbox |

## 8.2 漏斗与指标

```text
扫码 → 浏览菜单 → 开通会员 → 领礼包 → 首单支付 → 7 日二单
```

| 指标 | 公式 |
|------|------|
| 入会率 | 新 MEMBER / 扫码相关人次 |
| 礼包核销率 | USED / 发放 |
| 7 日复购 | 首单后 7 日再购 / 首单人数 |

## 8.3 🔧 增强设计

| 项 | 做法 |
|----|------|
| 新人礼包多券 | `welcome_bundle_template_ids=[id…]`；循环发券；幂等 `welcome_pack:{cid}:{tid}:v1` |
| P1 coupon_bundles | bundle + items 表 |
| 引导 UI | 菜单顶栏/结果页三行权益；设置可关 |
| 开业/储值礼 | 储值支付成功 side-effect 发券包；禁纯送 |
| 外卖到店 | 堂食专用入会券，避免平台佣金上叠深折 |

## 8.4 验收

重复开通不双发；门店 A 礼包不影响 B；过期券进过期率不进核销。

------------------------------------------------------------------------

# 9. 老客经营与留存

## 9.1 ✅ 现网（先配置再开发）

| 能力 | 说明 |
|------|------|
| 偏好/口味/收藏 | preferences / taste / favorite |
| 人脸迎宾 | face_profiles / welcome_logs |
| 生日券 | birthday_coupon_name |
| 沉睡唤醒 | sleep_member_wake：30 档=(30,90]，90 档=(90,∞)；模板名空=关 |
| 余额提醒 | balance_low_remind：阈值>0；Redis 30 天防重；**现仅日志** |
| 等级折扣 | 银/金/钻 |
| 顾客合并 | CustomerMergeService |

## 9.2 画像字典（映射现网）

昵称/手机/生日/等级/积分/储值 → customers；常点 → 订单 TopN+favorite；辣度忌口 → preferences/taste；公益 → 🆕 identities；RFM/lifecycle → 🆕 夜批。

## 9.3 王叔场景

```text
人脸/手机号 → GET /customers/{id}/service-card（🆕）
  → 称呼、常点、忌口、等级、可用券、公益身份、今日公益否
  → POS 侧栏；可选 TTS（store_settings 模板）
```

## 9.4 生命周期 ↔ 现网任务

| 状态 | 条件 | 现网动作 |
|------|------|----------|
| NEW | 开通≤7 天 | 欢迎券 ✅ |
| ACTIVE | 30 日内有单 | 推荐/会员折 ✅ |
| AT_RISK | 约 31–90 日无单 | sleep 30 档 ✅ |
| DORMANT | >90 日无单 | sleep 90 档 ✅ |
| LOST | >120 且低频 | 少触达 |

P0：Settings 暴露三键并配模板。P1：`customer_lifecycle`+RFM 筛选 UI。

## 9.5 RFM 预设分群（🆕 种子）

champions / loyal / new_hope / need_love / winback / hibernating（阈值可配）。

## 9.6 验收

配 30 档后次日有发券；余额提醒 30 天不重复；服务卡无证件图；合并后券归属正确。

------------------------------------------------------------------------

# 10. 储值 / 早餐卡 / 积分

## 10.1 储值 ✅ + 🔧

| 项 | 内容 |
|----|------|
| 种子档 | 充50得55、100得120、200得250 |
| 账本 | balance_principal / gift；赠金优先扣 |
| 端 | Wallet.vue + 小程序 recharge |
| 提醒 | balance_low_remind 🔧 接订阅 |
| 增强 | 档位绑券包；结算引导；退本金合规文案 |

合规：可查余额、可退本金、七日无理由退本金；禁不退不换/到期清零/最终解释权。

## 10.2 早餐卡

- **P0**：充值发 N 张早餐时段券（需券适用时段 🔧）。  
- **P1**：`benefit_cards` + redemptions 次卡实体。

## 10.3 积分 ✅

实付积分、退款扣回、等级升级 ✅；积分明细查询（EARN/REFUND_DEDUCT/EXCHANGE/EXPIRE 四类型 + 中文标签 + 真分页）✅；积分兑换优惠券（`point_exchange_options` 配置选项，`PTS:{cid}:{oid}:{YYYYMM}` 幂等键，扣点+发券同事务）✅；积分过期清零（`points_expire_days` 配置，默认 365 天，0=永不过期，每日 02:30 扫描超期 ACTIVE 会员整体清零 + 写 EXPIRE 流水）✅。

## 10.4 验收

充值幂等；赠金优先；早餐券非早餐不可用；积分兑换原子性。

------------------------------------------------------------------------

# 11. 多门店与营销治理

## 11.1 现网 ✅

总部 SUPER_ADMIN（store_id NULL）跨店；门店账号绑定 store_id；券总部模板+白名单；详见 `docs/多门店管理.md`。

## 11.2 营销权限矩阵

| 对象 | 超管 | 店长 | 收银 |
|------|------|------|------|
| 顾客/发券 | 全店 | 本店 | 服务卡+核验 |
| 总部券模板 | 管理 | 使用 | — |
| 品牌活动 | 管理 | 只读参与 | — |
| 本店活动 | 可 | 可（预算上限） | — |
| 公益规则上限 | 定 | 收紧/停用 | POS 核验 |
| 留存设置键 | 管理 | 只读或本店覆盖 | — |
| 营销报表 | 全店 | 本店 | — |

## 11.3 跨店规则

身份/积分/储值中央账本；earn/burn/order 带 store_id；公益日限默认**分店分别计数**；❌ tenant_id。

------------------------------------------------------------------------

# 12. 功能模块详设 M1–M7

## 12.0 信息架构

```text
后台侧栏
├── 会员✅ 顾客 / 等级 / 储值 / 券
├── 营销中心🆕
│   ├── 活动管理 M3
│   ├── 客户分群 M2
│   ├── 自动任务/旅程 M4
│   ├── 社会贡献/公益（第6章）
│   └── 营销报表 M7
├── 营业✅ … 推荐效果 M6🔧
└── 系统✅ 设置（暴露留存键）
```

权限：P0 复用 `customer:*`/`coupon:*`；P1 增 `marketing:*`/`social:*`。

## 12.1 M1 客户中心 🔧

**列表筛**：门店、GUEST/MEMBER、lifecycle、RFM 预置、公益身份、沉默天数、标签。  
**详情 Tab**：概览服务卡、订单、券、储值/积分、偏好、公益、触达日志、合并（现网 API）。  
**API**：增强 list query；`service-card`；`POST tags`。

## 12.2 M2 客户分群 🆕

`customer_segments`：STATIC|DYNAMIC + rules_json 条件树；estimate/materialize；`customer_segment_members` 可选。  
界面：条件建造器、估人数、脱敏样本 20 条。  
预置分群对齐：sleep30/90、新会员、低余额、champions。

## 12.3 M3 活动管理 🆕

四步向导：基本信息 → 圈人 → 动作（券/推送/公益/推荐加权）→ 预览发布。  
详情：状态按钮、规则、任务监控（成功/失败/跳过）、归因、补跑 FAILED。

## 12.4 M4 自动任务/旅程 🔧→🆕

| 预置旅程 | 触发 | 步骤 |
|----------|------|------|
| journey_new_member | 开通 | 礼包→未核销提醒→未二单加料券 |
| journey_birthday | 生日 | 现网生日券+可选推送 |
| journey_winback_* | AT_RISK/DORMANT | 对齐 sleep 档 |
| journey_love_day | 每月 10 日 | 环卫豆浆 |
| journey_balance_low | 余额低 | 接订阅后 |

P0：Settings 管理现网四键。P1：任务表+worker（SKIP LOCKED）。

## 12.5 M5 权益中心 ✅+🆕

聚合：Coupons（含里程碑）、Wallet、MemberLevels、🆕 SocialBenefits。  
叠加默认：公益 XOR 会员折 → 券 → 储值支付；`pricing_stack_policy` 可配。

## 12.6 M6 智能推荐 🔧

不重建模型。增强：活动 boost_product_ids；settle_addon 与加料率联动；报表按 campaign 过滤；保持 recommend_opt_in。

## 12.7 M7 数据中心 🆕

`MarketingReport.vue`：总览 KPI、活动排行、分群转化、公益成本、CSV 导出（门店隔离、无明文手机）。

## 12.8 依赖

```text
分群M2 → 活动M3 → 任务M4 → 权益M5
              ↘ 推荐加权M6
客户M1 查询/人工入口；归因 → 报表M7
```

------------------------------------------------------------------------

# 13. 支撑模块（订单/POS/迎宾/外卖/菜单）

| 模块 | 路径/入口 | 状态 | 营销接入点 |
|------|-----------|------|------------|
| 订单/支付/退款 | orders/payments API | ✅ | outbox 副作用；退款回滚里程碑/公益 |
| POS | PosOrder.vue | ✅ | 服务卡、公益核验、入会 |
| KDS/叫号 | Kitchen/Display | ✅ | 取餐订阅已有 |
| 迎宾人脸 | cameras/welcome | ✅ | 话术+偏好播报开关 |
| 外卖 | delivery/* | ✅ | 到店入会券；不强打通平台会员 |
| 分类时段 | CategorySchedules | ✅ | 早餐/闲时与活动窗对齐 |
| 排队/发票 | queue/invoices | ✅ | 弱相关 |
| 财务账本 | FinancialLedger | ✅ | ROI 实收口径 |
| 桌台扫码 | tables/qr | ✅ | 入会主入口 |

------------------------------------------------------------------------

# 14. 增量数据库设计

## 14.1 禁止重复建设

customers、coupon_templates、customer_coupons、member_levels、balance_*、point_ledger、behavior、taste、preferences、stores、order_milestone_rewards、recharge_*、ab_*、face_* …

## 14.2 净新增（落地进 struct.sql）

| 表 | 用途 |
|----|------|
| social_identity_types / customer_social_identities / social_benefit_rules / social_benefit_redemptions | 公益 |
| customer_tags / customer_tag_bindings | 运营标签 |
| customer_segments / customer_segment_members | 分群 |
| customer_lifecycle | RFM+生命周期 |
| marketing_campaigns / marketing_rules / marketing_tasks | 活动引擎 |
| campaign_attributions / marketing_message_logs | 归因与触达 |
| coupon_bundles* / benefit_cards*（P1） | 礼包/次卡 |

字段要点：campaign 含 store_id/status/start_at/end_at/budget_*；task 含 idempotency_key UNIQUE、status、try_count；redemption 含 subsidy_amount、REVERSED。

约定：事件带 store_id；种子业务键幂等；禁止字面量自增 id。

------------------------------------------------------------------------

# 15. API / 后台 / 小程序改造

## 15.1 新 API

| 前缀 | 能力 |
|------|------|
| `/api/marketing` | campaigns CRUD/publish、segments estimate/materialize、tasks retry、reports |
| `/api/social-benefits` | 见 §6.7 |
| `/api/customers/{id}/service-card` | POS/迎宾 |
| `/api/customers/{id}/tags` | 打标 |
| `/api/customers/{id}/lifecycle` | 生命周期只读 |

## 15.2 扩展现有文件

| 文件 | 改动 |
|------|------|
| coupon_service.py | 礼包多发 |
| OrderPriceCalculator | 公益通道 |
| paid_side_effects | 储值绑券、归因 |
| retention_service.py | 真正发订阅消息 |
| recommendation_service.py | 活动加权 |
| scheduler.py | task worker / 爱心日 / lifecycle |
| AdminLayout.vue | 营销中心菜单 |
| PosOrder.vue | 核验+服务卡 |
| Customers.vue / Settings.vue / Coupons.vue / Wallet.vue | 增强 |
| 双端 member/confirm/result/subscribe | 引导与营销模板 |

## 15.3 小程序双端清单

会员中心公益入口；结算权益提示条；结果页入会/储值/订阅；recommend-strip 服务端加权透明；微信/支付宝行为对齐。

------------------------------------------------------------------------

# 16. KPI 与归因

## 16.1 指标字典

| 指标 | 口径 | 粒度 |
|------|------|------|
| 入会率 | 新 MEMBER / 扫码相关 | 店×日 |
| 会员销售占比 | 会员实收 / 总实收 | 店×日 |
| 7/30 日复购 | 见第 1 章 | 店×周/月 |
| 券核销/过期率 | USED、EXPIRED / 发放 | 模板或活动 |
| 储值转化 | 充值会员 / 活跃会员 | 店×月 |
| 加料率 | 含加料单 / 总单 | 店×日 |
| 活动 ROI | (归因实收−补贴)/补贴 | 活动 |
| 公益人次/补贴 | redemption 计数与 sum | 店×月 |

实收优先 `financial_ledger` SALE−REFUND。

## 16.2 归因算法

```text
支付成功：
  1) 券来源含 campaign → COUPON
  2) else 48h 内活动推送成功 → MESSAGE
  3) else 订单公益快照 → SOCIAL
  4) 写 campaign_attributions；退款冲减
```

`attribution_window_hours=48`；默认券优先唯一归因。

## 16.3 验收

发券核销出现在活动归因；退款回滚；超窗不归因；门店导出无明文手机。

------------------------------------------------------------------------

# 17. 端到端旅程总图

**堂食新客**：扫码→菜单推荐✅→开通→欢迎券✅→支付→积分/里程碑✅→引导储值→30/90 唤醒✅  

**老客到店**：人脸/手机→服务卡🆕→偏好✅→会员折/券✅/公益🆕→支付  

**沉睡召回（可立即运营）**：配置 sleep 模板→每日 10:05 发券✅→P1 推送→核销报表  

**公益**：认定→preview→折扣→台账🆕→爱心日券  

------------------------------------------------------------------------

# 18. 分阶段实施与验收

> 细粒度步骤与完成定义见 **§23**。本处仅对照 Phase ↔ WP。

| Phase | 工作包 | 验收要点 |
|-------|--------|----------|
| A | WP-A0 | 现网 welcome/birthday/sleep30/90/balance 已配置；调度日志有证据 |
| B | WP-B1、B3、B2、B4 | 公益 P0 可核销；礼包多券；活动分群发券；服务卡+POS |
| C | WP-C1、C2 | 订阅推送；归因报表；推荐加权；公益 P1；RFM 筛选 |
| D | （另立项） | 权限拆分、次卡、加盟外置；不在本文强制范围 |

------------------------------------------------------------------------

# 19. 配置项与调度任务清单

## 现网先用

`welcome_coupon_name` · `birthday_coupon_name` · `sleep_wake_coupon_name_30` · `sleep_wake_coupon_name_90` · `balance_low_threshold`

## 建议新增

`welcome_bundle_template_ids` · `marketing_weekly_cap` · `pricing_stack_policy` · `lifecycle_thresholds_json` · `rfm_thresholds_json` · `love_day_soy_template_name` · `enable_social_benefit` · `attribution_window_hours`

## 调度

✅ sleep_member_wake · balance_low_remind · night_data_batch · learn_taste_profiles  
🆕 social_love_day_issue · marketing_task_worker · refresh_lifecycle  

------------------------------------------------------------------------

# 20. Cursor 增量提示词

> **推荐用法**：每个工作包（§23）单独开对话，只粘贴对应 WP 提示词，避免一次做完所有模块。

## 20.1 通用头（每个 WP 都带上）

```text
仓库：SmartNoodleHouse。增量改造，禁止 tenant_id，禁止重建 customers/coupon_templates/推荐引擎。
必读：docs/射洪肠旺面_营销策划与CRM管理系统项目实施方案_Cursor开发版.md
  - 第 2 章现网清单、第 23 章当前 WP、相关业务章（公益§6 / 活动§7 / 模块§12）。
先读再改：retention_service.py、coupon_service.py、OrderPriceCalculator、store_scope.py。
结构真源 sql/struct.sql；种子幂等且禁止硬编码自增 id；测试放 tests/；微信+支付宝同步。
```

## 20.2 按 WP 追加的任务句

| WP | 追加提示词（接在通用头后） |
|----|---------------------------|
| WP-B1 | 只实现社会贡献 P0：四表+social_benefit_service+API+计价互斥+POS核验+双端入口+限次/退款/隔离测试。详见§6。 |
| WP-B2 | 只实现活动+分群最小闭环：segments/campaigns/tasks 表与服务；发券只调 CouponService；后台营销中心菜单；幂等 mkt: 前缀。详见§7/§12。 |
| WP-B3 | 只做 welcome_bundle_template_ids 多券幂等；Settings 暴露 sleep_wake_* 与 balance_low_threshold。 |
| WP-B4 | 只做 GET service-card + PosOrder 侧栏展示偏好/券/公益态；不含完整公益规则引擎（若 B1 未合并则 mock 空）。 |
| WP-C1 | 余额提醒接订阅消息；campaign_attributions+MarketingReport；不改交易主流程。 |
| WP-C2 | 推荐活动加权；公益 P1；lifecycle/RFM 夜批与顾客筛选。 |

## 20.3 总包提示词（仅当明确要求一次做完 Phase B 时使用）

```text
（先贴 20.1 通用头）
本对话交付 Phase B 全部：WP-B1+B2+B3+B4。按 B1→B3→B2→B4 顺序提交，每步可运行 tests。
完成后给出：改动文件列表、迁移说明、人工验收清单（对照§23完成定义）。
```

------------------------------------------------------------------------

# 21. 风险、合规与铁律

禁全场打折；让利≤毛利约 20%；赠品优先低成本；禁极限词；原价≤近 7 日最低成交；储值可查可退本金；隐私分权与推荐 opt-in；公益五件套（凭证/限次/范围/叠加/台账）。

------------------------------------------------------------------------

# 22. 附录：概念迁移对照

| 旧说 | v3 |
|------|-----|
| 从零多租户 CRM | 现网增强；❌ tenant |
| 新建 coupon_template | ✅ 已有 |
| 无沉睡能力 | ✅ sleep_member_wake |
| 第四阶段才做推荐 | ✅ 已有，只运营化 |
| 第一阶段多门店 | ✅ 已有 |
| festival_campaign | 🆕 引擎编排现网发券 |
| customer_social_tag | 🆕 第 6 章完整域 |

------------------------------------------------------------------------

# 23. 可执行工作包（按此直接开工）

> 本章是 **唯一推荐的执行入口**。作战板 Canvas（`crm-exec-playbook.canvas.tsx`）是摘要；细节与验收以本章+对应业务章为准。  
> 顺序：**WP-A0 → WP-B1 → WP-B3 → WP-B2 → WP-B4 → WP-C1 → WP-C2**（B3 可与 B1 后半并行）。

------------------------------------------------------------------------

## 23.0 开工前检查清单（全体 WP）

- [ ] 已读第 2 章，确认不重建 ✅ 能力  
- [ ] 本地可跑：`struct` 库 + backend pytest + frontend 构建（按仓库 README）  
- [ ] 清楚禁止项：tenant_id、单端小程序、struct 外第二套结构真源  
- [ ] 每个 WP 结束：补 tests、更新本文完成勾选、不顺手扩 scope  

------------------------------------------------------------------------

## WP-A0 · 零开发运营开通（店长，0.5 天）

**目标**：现网留存链路先转起来。无需 Cursor 开发对话。

| 步骤 | 动作 | 验收 |
|------|------|------|
| 1 | 确认券模板存在：`新客满20减3` 等（data_sys） | Coupons 后台可见 |
| 2 | 设置 `welcome_coupon_name=新客满20减3` | 新开通会员得券 |
| 3 | 设置 `birthday_coupon_name` 为已有模板名 | 生日路径可发 |
| 4 | 设置 `sleep_wake_coupon_name_30/90` | 次日 10:05 后日志有 issued_* |
| 5 | 设置 `balance_low_threshold`（如 20） | 11:05 日志有提醒统计（尚推送） |

**完成定义**：任务日志证明沉睡发券或余额提醒跑过；无需发版。

------------------------------------------------------------------------

## WP-B1 · 社会贡献 P0（研发，5–8 天）

**必读**：§6 全文。

| 步骤 | 交付物 |
|------|--------|
| 1 | `sql/struct.sql` 增加公益四表；`data_sys` 身份字典；环卫规则种子（data_custom，幂等） |
| 2 | `social_benefit_service.py` + `/api/social-benefits` |
| 3 | `OrderPriceCalculator` 公益折扣通道；默认与会员折互斥 |
| 4 | POS「公益核验」当场 ACTIVE；台账 `social_benefit_redemptions` |
| 5 | 双端会员中心入口（可先申请/展示状态） |
| 6 | `tests/测试_社会贡献*.py`：无身份无折、8折命中、同店同日限次、互斥、退款 REVERSED、门店隔离、幂等 |

**完成定义**：收银可为环卫客核销 8 折一单且台账+审计有记录；测试全绿。

**Cursor**：§20.1 + §20.2 WP-B1 行。

------------------------------------------------------------------------

## WP-B3 · 新人礼包 + 留存 Settings（研发，2–3 天）

**必读**：§8、§9、§19。

| 步骤 | 交付物 |
|------|--------|
| 1 | `welcome_bundle_template_ids`（JSON 数组）；循环 `issue_by_template_id`；幂等 `welcome_pack:{cid}:{tid}:v1` |
| 2 | 兼容旧 `welcome_coupon_name`（仅一名时行为不变） |
| 3 | Settings UI：暴露 sleep_wake_30/90、balance_low_threshold、welcome 礼包 |
| 4 | 测试：重复开通不双发；门店设置隔离 |

**完成定义**：店长可在设置页改留存键；新会员一次领多张券且不重复。

**Cursor**：§20.1 + §20.2 WP-B3 行。

------------------------------------------------------------------------

## WP-B2 · 活动 + 分群发券最小闭环（研发，5–8 天）

**必读**：§7、§12.2–12.3、§14。

| 步骤 | 交付物 |
|------|--------|
| 1 | 表：customer_segments、marketing_campaigns/rules/tasks（最小字段集） |
| 2 | `segment_service` estimate；`marketing_service` 发布→生成 ISSUE_COUPON 任务 |
| 3 | scheduler `marketing_task_worker` 消费任务；只调 CouponService；幂等 `mkt:` |
| 4 | 后台：营销中心菜单 + Campaigns + Segments 页 |
| 5 | 频控：marketing_weekly_cap；超限 SKIPPED |
| 6 | 测试：DRAFT 不发券；幂等；暂停停发；门店隔离 |

**完成定义**：店长「建活动→选分群→发券」跑通；与欢迎券命名空间不冲突。

**Cursor**：§20.1 + §20.2 WP-B2 行。

------------------------------------------------------------------------

## WP-B4 · 服务卡 + POS（研发，2–3 天）

**必读**：§9.2、§12.1、§13。

| 步骤 | 交付物 |
|------|--------|
| 1 | `GET /api/customers/{id}/service-card`（脱敏；无证件原图） |
| 2 | PosOrder 检索顾客后展示侧栏 |
| 3 | 若 WP-B1 已合并：展示公益身份与今日是否已享 |

**完成定义**：收银 3 秒内看到「常点/忌口/可用券」。

**Cursor**：§20.1 + §20.2 WP-B4 行。

------------------------------------------------------------------------

## WP-C1 · 订阅推送 + 归因报表（研发，4–6 天）

**必读**：§15–16、§9.1 余额提醒。

| 步骤 | 交付物 |
|------|--------|
| 1 | `run_balance_low_remind` 真正发订阅（双端模板可配） |
| 2 | `marketing_message_logs`；失败可查 |
| 3 | 支付成功写 `campaign_attributions`（券优先） |
| 4 | `MarketingReport.vue`：核销率、增量、ROI；导出无明文手机 |
| 5 | 退款冲减归因 |

**完成定义**：能回答「某活动核销率与增量」；余额提醒用户可收到（需用户授权）。

**Cursor**：§20.1 + §20.2 WP-C1 行。

------------------------------------------------------------------------

## WP-C2 · 推荐加权 + 公益 P1 + RFM（研发，4–6 天）

**必读**：§6.9 P1、§12.6、§9.4–9.5。

| 步骤 | 交付物 |
|------|--------|
| 1 | 活动 RUNNING 时 boost_product_ids 进入推荐 rank |
| 2 | 公益：骑手窗/老人/小程序审核/爱心日调度/看板 |
| 3 | `customer_lifecycle` 夜批；Customers 筛选 |
| 4 | 测试：opt-out 无个性化；活动结束加权消失 |

**完成定义**：活动期目标 SKU 曝光升；店长可按 DORMANT 筛客。

**Cursor**：§20.1 + §20.2 WP-C2 行。

------------------------------------------------------------------------

## 23.1 Phase 完成门槛

| Phase | 门槛 |
|-------|------|
| A | WP-A0 勾选完成 |
| B | WP-B1～B4 完成定义均满足 + CI/本地 tests 绿 |
| C | WP-C1～C2 完成 + 店长能独立看营销报表 |
| D | 另立项（加盟等），不在本文强制范围 |

## 23.2 回滚与风险

- 结构变更可回滚：新表可空跑，功能开关 `enable_social_benefit` / 活动 DRAFT。  
- 发券异常：依赖现网幂等键，禁止手写跳过 CouponService。  
- 性能：分群 materialize 分批 500（对齐 retention 批大小）。

------------------------------------------------------------------------

# 项目最终目标

打造射洪肠旺面数字化经营系统（SmartNoodleHouse 营销增强版）：拉新、留存、复购、精准营销、公益口碑、多店复制、品牌运营。  

**一句话：先把现网欢迎券、里程碑、沉睡唤醒、储值、推荐、迎宾配置跑通，再叠加公益台账与活动引擎；不要推倒重来。**  

**执行入口：从 §23 WP-A0 开始勾选；开发按 WP-B1→B3→B2→B4→C* 用 §20 提示词开干。**

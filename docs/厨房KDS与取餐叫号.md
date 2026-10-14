# 厨房 KDS 与取餐叫号屏 — 使用说明文档

> 版本：v1.0  
> 更新日期：2026-09-14  
> 适用范围：SmartNoodleHouse 前后端

---

## 目录

1. [厨房 KDS（Kitchen Display System）](#1-厨房-kdskitchen-display-system)
2. [取餐叫号屏（Customer Display）](#2-取餐叫号屏customer-display)
3. [生产环境 vs 测试环境](#3-生产环境-vs-测试环境)
4. [常见问题排查](#4-常见问题排查)

---

## 1. 厨房 KDS（Kitchen Display System）

### 1.1 功能定位

厨房 KDS 是给**厨房员工**使用的订单展示与操作界面。厨房人员可以在此查看所有待制作订单、推进订单制作状态、认领订单、补打小票。

### 1.2 如何打开

#### 方式一：后台菜单进入（带侧边栏）

1. 员工登录后台 `/login`
2. 左侧菜单点击 **「厨房 KDS」**
3. 跳转至 `/kitchen`（在 AdminLayout 布局内，带侧边栏）

#### 方式二：独立全屏模式（厨房专用设备）

- 直接访问 `/kds`
- 无侧边栏，纯订单卡片界面，适合厨房平板 / 壁挂显示器
- 仍需登录（不在公开路径中）

### 1.3 Token 与权限

| 项目 | 说明 |
|------|------|
| 认证方式 | 员工 JWT Token（登录后自动存入 localStorage） |
| 权限要求 | `canAccessKitchen()` — 允许角色：`SUPER_ADMIN`、`STORE_ADMIN`、`KITCHEN`，或拥有 `order:update` 权限 |
| 收银员权限 | ❌ CASHIER 角色无权访问 KDS |
| WebSocket 传 token | 通过 `Sec-WebSocket-Protocol: access_token, <jwt>` 传递（不写入 URL，避免进访问日志） |
| API 接口 | `GET /api/orders/kitchen/active`，后端 `require_kitchen()` 依赖校验 |

### 1.4 界面与显示内容

**顶部工具栏：**

- 门店名称（超管可切换门店下拉框）
- 连接状态指示：实时推送 / 轮询兜底 / 已断开
- 筛选按钮（带数量统计）：
  - 全部
  - 待制作（NEW）
  - 制作中（COOKING）
  - 待出餐（READY）
  - 外卖（DELIVERY）
  - 堂食（DINE）
  - 打包（PACK）
- 语音开关 + 试听语音
- 刷新按钮
- 打印失败任务面板（红色徽章显示失败数量，可重试）
- 全屏 / 退出全屏按钮

**订单卡片内容：**

- 模式横幅：外卖 / 打包出餐 / 堂食
- 桌号或取餐号
- 标签：门店名、会员等级、外卖渠道、堂食/打包、现成品免烹饪、当前状态、认领状态
- 等待时长（按时间颜色分级）
- 订单号
- 餐品明细：名称、数量、规格、备注
- 打包费（如有）

### 1.5 可执行操作

订单状态流转按钮：

| 当前状态 | 可操作按钮 | 目标状态 |
|---------|-----------|---------|
| PAID（已支付）/ CONFIRMED（已确认） | 开始制作 | PREPARING |
| PREPARING（制作中） | 进入烹饪 / 跳过烹饪 | COOKING / READY |
| COOKING（烹饪中） | 制作完成 | READY |
| READY（待出餐） | 出餐 | COMPLETED |

其他操作：

- **认领 / 接管**：多人厨房协作。已被他人认领的订单可「接管」
- **补打小票**：重新打印厨房小票

### 1.6 数据更新机制

- **主通道**：WebSocket `channel=kitchen` 实时推送
- **兜底**：每 8 秒轮询 `GET /orders/kitchen/active`
- **断线重连**：自动指数退避重连（1s → 2s → 4s → ... 上限 15s）

---

## 2. 取餐叫号屏（Customer Display）

### 2.1 功能定位

取餐叫号屏是放置在**取餐区**给**顾客**观看的大屏。展示当前正在制作和已完成待取餐的订单，配合语音播报提醒顾客取餐。

### 2.2 如何打开

直接在浏览器访问：

```
/display?store_id=<门店ID>&token=<叫号屏令牌>
```

示例（当前开发环境）：

```
http://localhost:5173/display?store_id=1&token=VOpC_mpYINx2LA67gI-bNeeXqJI7qkLb
```

- **无需员工登录**（`/display` 在公开路径 `publicPaths` 中）
- 页面若缺少 `store_id`，会提示配置方式：`/display?store_id=1`
- 生产环境还需 `&token=xxx`

### 2.3 Token 需求

| 项目 | 说明 |
|------|------|
| Token 类型 | 短 opaque 字符串（**不是 JWT**），若用 JWT 后端会返回 4401 拒绝 |
| 配置位置 | `.env` 的 `DISPLAY_SCREEN_TOKEN`，或门店级 `store_settings.display_screen_token` |
| 传递方式 | URL query 参数 `?token=xxx`（短 token 可安全出现在 URL 中） |
| 门店级优先 | 优先读取门店级 setting，降级到全局 `.env` 值 |

#### Token 校验逻辑（后端 `_verify_display_token`）

```
if 生产环境 AND DISPLAY_SCREEN_TOKEN 为空:
    → 503 "未配置 DISPLAY_SCREEN_TOKEN"

if DISPLAY_SCREEN_TOKEN 已配置 AND 传入 token 不匹配:
    → 403 "叫号屏访问令牌无效"

否则:
    → 通过
```

### 2.4 界面与显示内容

**顶部：**

- 门店 Logo + 名称 + 副标题「堂食看桌号 · 打包看取餐号 · 制作完成请自取」
- 连接状态：实时更新 / 自动刷新 / 已断开
- 实时时钟
- 语音开关 + 试听语音
- 全屏 / 退出全屏按钮

**两个展示面板：**

| 面板 | 包含状态 | 说明 |
|------|---------|------|
| 制作中 | PAID / CONFIRMED / PREPARING / COOKING | 正在排队或制作的订单 |
| 请取餐 | READY | 已制作完成，等待顾客取餐（数字高亮） |

**每条记录显示：**

- 堂食订单 → 显示桌号（如「8号桌」）
- 打包 / 自取订单 → 显示取餐号（订单号末 4 位随机码）
- 子标签：堂食 / 打包自取 / 取餐
- 状态文案：排队中 / 制作中 / 请取餐

**底部提示：**

- 「堂食：对照您的桌号」
- 「打包/自取：对照取餐号（订单号末 4 位）」

### 2.5 语音播报策略

| 场景 | 行为 |
|------|------|
| 新订单进入「请取餐」 | 首次播报「XX 请取餐」 |
| 重复提醒间隔 | 2 分 → 3 分 → 5 分 → 5 分 → 10 分（最多 6 次） |
| 超时提醒 | 进入 READY 超过 **3 分钟**未取餐 → 闪烁高亮 + 「请尽快取餐」语音 |
| 超时后重复 | 每 3 分钟重复提醒 |
| 首屏加载 | 已有待取餐单只标记不播报（避免打开/刷新时连播） |
| 浏览器限制 | 需点击屏幕一次解锁自动播放 |

### 2.6 不显示的订单

- **外卖平台订单**（美团 MEITUAN / 饿了么 ELEME）不在叫号屏显示（由 `is_in_store_order` 过滤）

### 2.7 数据更新机制

- **主通道**：WebSocket `channel=display` 实时推送
- **兜底**：每 8 秒轮询 `GET /orders/display/active?store_id=1&token=xxx`
- **超时检查**：每 30 秒扫描待取餐超时提醒

---

## 3. 生产环境 vs 测试环境

### 3.1 厨房 KDS

| 项目 | 测试 / 开发 | 生产 |
|------|------------|------|
| 登录 | 需要 | 需要 |
| 权限校验 | 同左 | 同左 |
| 打开路径 | `/kitchen` 或 `/kds` | 同左（Nginx SPA 回退到 index.html） |
| WebSocket 协议 | `ws://` | `wss://`（Nginx 已配置 Upgrade） |

### 3.2 取餐叫号屏

| 项目 | 测试 / 开发 | 生产 |
|------|------------|------|
| 登录 | 不需要 | 不需要 |
| `DISPLAY_SCREEN_TOKEN` 留空 | ✅ 免 token 访问 | ❌ 启动报错（startup_checks 强制校验）+ 接口 503 |
| `DISPLAY_SCREEN_TOKEN` 已配置 | 必须传 `&token=xxx` | 必须传 `&token=xxx` |
| 门店级独立 token | 支持 | 支持 |
| URL 格式 | `/display?store_id=1` | `/display?store_id=1&token=xxx` |

### 3.3 生产环境叫号屏配置步骤

1. 在 `.env` 中设置：

   ```
   APP_ENV=production
   DISPLAY_SCREEN_TOKEN=<生成一个随机短字符串>
   ```

2. 重启后端 uvicorn

3. 叫号屏设备浏览器访问：

   ```
   https://你的域名/display?store_id=1&token=<上面的令牌>
   ```

4. （可选）为不同门店设置独立令牌：

   通过 `PUT /api/settings/stores/{store_id}/business` 设置 `display_screen_token`

### 3.4 当前环境快速验证

当前 `.env` 已配置 `DISPLAY_SCREEN_TOKEN=VOpC_mpYINx2LA67gI-bNeeXqJI7qkLb`：

- ✅ **KDS**：登录后台后访问 `/kitchen` 或 `/kds`
- ✅ **取餐叫号屏**：访问 `/display?store_id=1&token=VOpC_mpYINx2LA67gI-bNeeXqJI7qkLb`

> 若想在开发环境免 token 打开叫号屏，将 `.env` 中 `DISPLAY_SCREEN_TOKEN` 清空即可。

---

## 4. 常见问题排查

### 4.1 厨房 KDS

**Q：打开 `/kitchen` 或 `/kds` 提示无权限？**
A：当前账号角色不是 SUPER_ADMIN / STORE_ADMIN / KITCHEN，且没有 `order:update` 权限。请联系管理员调整角色或权限。

**Q：KDS 一直显示「轮询兜底」而非「实时推送」？**
A：WebSocket 连接失败。检查：
- 浏览器控制台是否有 WebSocket 报错
- Nginx `/api/ws/` 是否配置了 Upgrade 头
- 网络是否允许 WebSocket（部分代理会拦截）

**Q：看不到其他门店的订单？**
A：非超管账号只能查看绑定门店的订单。超管可在顶部下拉框切换门店或选「全部门店」。

### 4.2 取餐叫号屏

**Q：打开叫号屏提示「请配置门店」？**
A：URL 缺少 `store_id` 参数。请改为 `/display?store_id=<门店ID>`。

**Q：叫号屏提示需要 token 或 403 错误？**
A：
- 开发环境：检查 `.env` 中 `DISPLAY_SCREEN_TOKEN` 是否配置；若已配置，URL 必须带匹配的 `&token=xxx`
- 生产环境：`DISPLAY_SCREEN_TOKEN` 必须配置且 URL 必须带 token

**Q：叫号屏没有声音？**
A：浏览器自动播放策略限制。需点击屏幕任意位置一次解锁语音播报。

**Q：叫号屏不显示任何订单？**
A：
- 确认 `store_id` 正确且门店状态为营业
- 确认该门店有状态为 PAID/CONFIRMED/PREPARING/COOKING/READY 的堂食或自取订单
- 外卖平台订单（美团/饿了么）不会显示

**Q：生产环境后端启动失败，提示缺少 DISPLAY_SCREEN_TOKEN？**
A：在 `.env` 中配置 `DISPLAY_SCREEN_TOKEN` 后重启后端。

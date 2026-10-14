# Cwm9 多门店隔离与权限审计报告

> 审计日期：2026-09-29
> 审计范围：员工/角色/权限、门店数据隔离、顾客订单归属、小程序渠道隔离
> 审计方式：代码走读 + 多租户并发/跨店测试（791 passed, 8 skipped）

---

## 1. 模型基础

### 1.1 门店与员工

- `Store`：门店主体，`status` 控制营业。
- `User`：员工，含 `store_id`（归属门店）、`role_id`。
- `Role`：角色，`code` 唯一（SUPER_ADMIN / STORE_ADMIN / CASHIER / KITCHEN 等）。
- `RolePermission`：角色-权限多对多。
- `Permission`：细粒度权限码（如 `order:update`、`payment:refund`）。

### 1.2 顾客

- `Customer`：全局顾客（按手机号/unionid 合并），不直接绑定门店。
- `CustomerChannel`：顾客在微信/支付宝的身份，openid + channel 维度。
- 订单通过 `Order.store_id` 归属门店，顾客通过 `Order.customer_id` 拥有订单。

---

## 2. 门店隔离服务层（[backend/app/services/store_scope.py](file:///e:/SmartNoodleHouse/backend/app/services/store_scope.py)）

### 2.1 核心函数

| 函数 | 用途 | 失败行为 |
|---|---|---|
| `require_bound_store(user, store_id)` | 非超管强制覆盖为本人归属门店 | 员工无归属或访问他店 → 403 |
| `assert_store_access(user, store_id)` | 单条记录归属校验 | 跨店 → 403 |
| `assert_order_store_access(user, order)` | 订单归属校验（含订单上 store_id） | 跨店 → 403 |
| `assert_customer_owns_order(order, customer_id)` | 顾客侧订单归属 | 非本人 → 403 |

### 2.2 超管语义

- `role.code == SUPER_ADMIN` 可跨店；`effective_store` 返回请求 store_id。
- 其他角色 `effective_store` 强制 = `user.store_id`，即使请求参数传他店 ID 也会被覆盖或拒绝。
- 超管操作仍写审计日志，不豁免可追溯性。

### 2.3 测试覆盖

- 测试_门店范围.py：effective_store 强制 / 未绑定员工拒绝 / 跨店记录拒绝 / 订单归属。
- 测试_多租户并发隔离.py：并发请求不同 store_id 不串数据。
- 测试_门店隔离防护.py：calculate_items_batch 拒绝跨店规格 / DiningSession 归属 / 流票据门店编码。

---

## 3. API 层权限依赖（[backend/app/api/deps.py](file:///e:/SmartNoodleHouse/backend/app/api/deps.py)）

### 3.1 身份依赖

| 依赖 | 类型 | 校验 |
|---|---|---|
| `CurrentUser` | 员工 | typ=staff + ver=token_version + 用户存在 + 启用 |
| `OptionalStaffUser` | 可选员工 | 公开餐品列表等场景 |
| `CurrentCustomer` | 顾客 | typ=customer + ver + 顾客 active |
| `OptionalCustomer` | 可选顾客 | 冷启动浏览 |
| `StaffOrCustomer` | 员工或顾客 | 三端共用接口，拒绝 typ=stream |

### 3.2 权限装饰器

- `require_permission(code)`：从 RolePermission 联表查权限码，超管直通。
- `require_kitchen()`：SUPER_ADMIN / KITCHEN / STORE_ADMIN 或持 `order:update`。
- `require_pos()`：POS 端点额外要求员工归属门店有效。

### 3.3 权限校验位置

权限不仅靠前端路由隐藏，**每个敏感后端端点都显式 Depends**：

```python
@router.post("/refunds/{id}/approve", dependencies=[require_permission("payment:refund")])
async def approve_refund(...): ...
```

服务层关键操作（如 `_can_approve`）再次校验，防内部调用绕过 API 依赖。

---

## 4. 前端路由与权限（[frontend/src/router/index.ts](file:///e:/SmartNoodleHouse/frontend/src/router/index.ts)）

### 4.1 路由 meta

- `permission: 'xxx'`：需要具体权限码。
- `anyPermission: ['a','b']`：任一即可。
- `denyCashier: true`：收银员不可见（如门店设置、财务台账）。

### 4.2 路由守卫

- 未登录跳 `/login`。
- 已登录但无权限跳 `/403`。
- 切换门店/登出时清空动态路由与 tab 缓存。

### 4.3 401 处理（[frontend/src/api/index.ts](file:///e:/SmartNoodleHouse/frontend/src/api/index.ts)）

- 按请求 URL 与 `X-Client` 头区分 staff / customer 令牌。
- staff 401 → 清 staff token，跳管理后台登录。
- customer 401 → 清 customer token，跳小程序扫码/登录页。
- 不互清，避免一个端登出导致另一个端掉线（P1-112）。

### 4.4 登出清理

[stores/auth.ts](file:///e:/SmartNoodleHouse/frontend/src/stores/auth.ts) `logout` 清空：
- token / user / permissions / currentStore
- 动态路由、tab 缓存、WebSocket 连接

---

## 5. 业务对象门店隔离清单

| 对象 | 隔离字段 | 服务层校验 |
|---|---|---|
| Order | store_id | assert_order_store_access |
| OrderItem / Option | 通过 order | calculate_items_batch 强制 OptionGroup.store_id |
| Payment / Refund | 通过 order | POS 退款 assert_order_store_access |
| Product / Category / OptionGroup | store_id | 菜单复制/查询按门店 |
| Stock / StockMovement | store_id | 库存扣减带 store_id 条件 |
| CouponTemplate / Coupon | store_id（模板） | 发券/核销按门店 |
| Table / DiningSession | store_id | 扫码绑定校验桌台归属 |
| Customer | 全局 | 订单归属校验，不按门店隔离顾客本身 |
| PlatformStoreBinding | store_id + platform 唯一 | 外卖凭证按门店解析 |
| DeliveryOrderMapping | platform + platform_order_id | 跨店不可查 |
| Camera | store_id | 流票据 store 字段二次校验 |
| PrintJob | store_id | 打印队列按门店消费 |
| KitchenTicket | store_id | KDS 按门店筛选 |
| AuditLog | store_id | 日志查询按门店可见范围 |
| SystemSetting | store_id 可空（全局/门店级） | effective_bool 按门店覆盖 |
| StoreHours | store_id | 营业时间按门店 |
| PosRefundRequest | store_id | 审批 assert_store_access |
| PosShift | store_id + cashier_id | 班次仅本门店本收银员 |

---

## 6. 跨店污染专项防护

### 6.1 规格（OptionGroup）

- 下单时 `calculate_items_batch` 校验每个 OptionGroup.store_id == 订单 store_id（P1-109）。
- 防止 A 店菜品携带 B 店规格导致价格/库存错乱。
- 测试：测试_跨门店规格.py。

### 6.2 购物车

- 小程序购物车 storage key 含 store_id：`cart:{customer_id}:{store_id}`。
- 切换门店时不展示他店购物车；跨店续单走显式流程（P0-014）。
- 测试：测试_多门店续单.py、测试_我的订单续单.py。

### 6.3 菜单复制

- 菜单跨店复制时重新生成 ID，不直接共享 OptionGroup（[menu_copy_service.py](file:///e:/SmartNoodleHouse/backend/app/services/menu_copy_service.py)）。
- 测试：测试_菜单复制.py。

### 6.4 桌台 qr_token

- qr_token 不作为永久静态凭证；扫码后由 `DiningSessionService.start_or_refresh` 裁决会话有效期。
- 前端 4 小时 TTL 不主动销毁有效凭证（项目记忆中的教训）。
- 远程枚举 table_id 不可获得 qr_token（P0-013）。
- 测试：测试_二维码链接.py、测试_多门店桌台.py、测试_桌台订单解析.py。

### 6.5 优惠券

- 模板按门店创建；跨店模板需显式标记全局。
- 发券 `remaining` 原子扣减，client_request_id 幂等。
- 核销时校验券模板 store_id 与订单一致。
- 测试：测试_券模板门店.py、测试_券并发.py、测试_券批量发放.py。

### 6.6 外卖平台绑定

- PlatformStoreBinding 唯一约束 (platform, platform_shop_id)。
- 回调按平台店铺反查门店，不接受客户端传 store_id。
- 凭证加密存储，按门店隔离使用。
- 测试：测试_外卖凭证范围.py。

---

## 7. 顾客侧越权防护

### 7.1 订单归属

- 顾客查询订单详情/取消/支付/退款，端点内 `assert_customer_owns_order`。
- 不可通过遍历 order_id 查看他人订单。
- 测试：测试_订单访问安全.py。

### 7.2 渠道身份

- 小程序登录 channel 由服务端根据 JWT/请求头决定，body 中 channel 被忽略（P1-203）。
- 微信 openid 不可用支付宝 token 查询，反之亦然。
- unionid 合并多 openid 走 CustomerChannel 表，不覆盖原渠道。

### 7.3 会员操作

- 余额/积分/券查询强制绑定 CurrentCustomer，不接受 body 传 customer_id。
- 手机号合并需显式确认（verified_phone 冲突时）。
- 测试：测试_顾客合并.py、测试_渠道档案与手机号合并.py、测试_手机号仅官方授权.py、测试_顾客微信OpenID.py。

### 7.4 人脸

- 注册/识别均绑定当前 customer_id，活体 token 一次性并校验 customer 一致。
- 流票据 cam/store 绑定，不可借票据看他店摄像头。

---

## 8. 角色矩阵（典型）

| 能力 | SUPER_ADMIN | STORE_ADMIN | CASHIER | KITCHEN |
|---|---|---|---|---|
| 跨店访问 | ✓ | ✗ | ✗ | ✗ |
| 门店设置 | ✓ | ✓（本店） | ✗ | ✗ |
| 员工管理 | ✓ | ✓（本店） | ✗ | ✗ |
| 接单/出餐 | ✓ | ✓ | ✗（POS 除外） | ✓ |
| POS 收银 | ✓ | ✓ | ✓ | ✗ |
| 退款发起 | ✓ | ✓ | ✓ | ✗ |
| 退款审批 | ✓ | ✓ | 需 payment:refund | ✗ |
| 财务台账 | ✓ | ✓（本店） | ✗ | ✗ |
| 抹零/改价 | ✓ | ✓ | ✓（限阈值） | ✗ |
| 摄像头配置 | ✓ | ✓（本店） | ✗ | ✗ |
| 审计日志 | ✓ | ✓（本店） | ✗ | ✗ |

前端路由 `denyCashier` 仅控制可见性，后端依赖独立校验。

---

## 9. 并发与竞态

### 9.1 同顾客多端

- token_version 撤销机制：改密/登出使所有端旧 token 失效。
- 订单创建 idempotency_key 防多端重复提交。

### 9.2 同订单多员工操作

- 退款审批/拒绝/撤销行锁互斥。
- 厨房接单/完成状态机断言，非法转换拒绝。
- Leader 锁保证定时任务单 worker 执行（[leader_lock.py](file:///e:/SmartNoodleHouse/backend/app/core/leader_lock.py)）。

### 9.3 跨店并发

- 多租户并发测试验证：不同 store_id 并发请求不会出现 effective_store 串号。
- SystemSettings 缓存按 store_id 维度隔离，invalidate 时 bump Redis 版本通知其他 worker。

---

## 10. 审计日志可见范围

- 超管可查全门店。
- 店长仅本门店。
- 收银员无审计日志菜单与 API 权限。
- 日志条目含 store_id，查询 SQL 强制带 store_id 条件。
- 测试：测试_审计日志可见范围.py。

---

## 11. 遗留事项

1. **角色自定义 UI**：当前角色与权限码已支持 RBAC 表结构，但管理后台自定义角色编辑器能力有限，主要靠预置角色；下一阶段可补可视化角色编辑（REMAINING_ISSUES P2）。
2. **字段级权限**：当前为端点+门店级，未实现字段级（如收银员不可看成本价）；如需更细粒度，建议在序列化层加字段权限。
3. **临时委派**：不支持"店长休假临时授权"，建议未来加 RoleDelegation 表与有效期。

---

## 12. 结论

多门店隔离在服务层、API 依赖层、前端路由层均有强制控制，且关键写入路径（下单/退款/库存/券/摄像头）均做了门店字段校验。顾客侧订单归属与渠道身份独立隔离。多租户并发与跨店污染测试通过，未发现可绕过 store_id 访问他店数据的缺陷。

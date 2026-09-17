# 外卖管理整体重构（独立【外卖】菜单 + 多门店 + 防超卖 + 自动开关店）实施计划

> 范围：美团 + 饿了么双平台、多门店。目标是把当前散落在 `Delivery.vue` 多个 Tab 的能力，重构为独立的一级【外卖】菜单，并补齐"餐品/规格/价格/库存/上下架/营业时间/自动开关店/KDS 联动/跨平台防超卖/同步监控"全链路。

## 一、仓库调研结论（现状盘点）

### 已具备（直接复用，不重写）
- **平台对接底座**：`PlatformStoreBinding`（多门店绑定，store_id+platform+platform_shop_id 唯一）、回调验签、Webhook Inbox、接单/退款/取消、美团+饿了么 Token 刷新（`PlatformTokenService`，调度器每 6h）。
- **本地防超卖已完备**：[stock_service.py](file:///e:/SmartNoodleHouse/backend/app/services/stock_service.py) 的 `deduct_stock` 用**单条条件 UPDATE**（`stock=stock-qty WHERE stock>=qty`，999=不限），门店级 `store_product_stocks` + 品牌 `products` 两级；`restore_stock` 取消/退款回库，由 `order_lifecycle` 幂等保护。
- **外卖落单已扣本地库存**：[delivery_service.py L336-343](file:///e:/SmartNoodleHouse/backend/app/services/delivery_service.py#L336-L343)；订单初始 `status=PAID, dining_type=TAKEAWAY`。
- **KDS 已天然显示外卖**：[orders.py kitchen/active L617](file:///e:/SmartNoodleHouse/backend/app/api/v1/orders.py#L617) 按状态查询不限渠道，且 join 了 `DeliveryOrder` 携带外卖信息；厨房出餐状态已通过 `DeliveryService.sync_kitchen_status` 回传平台。
- **事务型 Outbox 设施完备**：`enqueue_outbox(aggregate_type, aggregate_id, event_kind, payload)` + `register_outbox_handler` + claim/过期回收/指数退避 + Leader 调度。
- **门店级库存/停售覆盖**：`StoreProductStock(store_id, product_id, stock, status)`。
- 现有单条同步：`food/save`、`food/updatePrice`、`food/updateStock`、饿了么 `eleme.product.item.createItem/updateItem`。

### 缺口（本次要做）
1. **无门店营业时间模型 / 无自动开店关店**（全仓 grep `business_hours|open_time|shipping_time|开店|关店` 零命中）。
2. **平台库存不随本地售罄联动**：本地面只剩 5 碗卖完后，美团/饿了么仍显示有货，两平台并发来单会超卖（仅靠落单时扣减兜底，且失败只抛错重试，**不会主动拒单**，平台侧挂单）。
3. 平台用的是**旧版逐个接口**，未用官方推荐的批量 `food/batchinitdata`、批量上下架 `food/sku/sellStatus`、**原子增减** `food/sku/inc_stock`、`food/sku/desc_stock`。
4. **商品改价/上下架/库存变更无自动同步钩子**，只能手动点按钮。
5. **无平台分类映射表**（目前直接传本地分类名）。
6. 外卖是 [Delivery.vue](file:///e:/SmartNoodleHouse/frontend/src/views/Delivery.vue) 内的 Tab，不是独立【外卖】菜单；缺少"营业时间、平台上下架、库存对账、同步监控"运营界面。

### 官方文档核对（2026-09 检索）
- 美团（沿用项目现用的 `waimaiopen.meituan.com` 餐饮体系，签名 `sig`）：
  - 门店：`poi/save`（`is_online` 上下线、`open_level` 1可配送/3休息中、`shipping_time` 营业时间，格式 `7:00-9:00,11:30-19:00`）、`poi/online`、`poi/offline`、`poi/mget`。
  - 菜品：`foodCat/update`（分类）、`food/batchinitdata`（批量，建议 ≤200/次，含 `is_sold_out/stock/skus/available_times`）、`food/sku/sellStatus`（批量上下架，sell_status 1下架/0上架）。
  - 库存：`food/sku/price`、`food/sku/stock`（覆盖）、`food/sku/inc_stock`、`food/sku/desc_stock`（**原子增减**）。
  - 业务规则：库存归 0 = 售罄（补货后自动恢复展示）；"下架"后补货不自动恢复，需重新上架。
  - 来源：developer.waimai.meituan.com/home/guide/4（门店/商品/价格库存方案）、/docDetail/59（batchinitdata）、/docDetail/70（sellStatus）、developer.meituan.com poi/save。
- 饿了么（沿用项目现用餐饮 JSON-RPC `eleme.*`）：商品 `eleme.product.item.*`（onShelf/offShelf/updateShelfStock）、门店营业 `eleme.shop.*`；库存/价格更新走原子接口。开放平台已演进为淘宝闪购 `open-retail.ele.me`（餐饮与零售/连锁模型不同），本项目维持餐饮 `eleme.*` RPC 体系，不迁移网关。
- **不贸然切换**到美团 `api-open-cater.meituan.com` 新版网关（鉴权改为 appAuthToken+businessId+developerId+sign），避免在无真实凭证时引入鉴权断层；新接口在现有体系内补齐，mock 兜底，标注待真机联调。

## 二、核心设计：跨平台防超卖闭环

本地 `stock_service` 是唯一真实源，平台库存只做镜像。三道闸：

1. **接单强闸（最后防线）**：平台推单 → 落单时原子扣本地库存 → 成功才确认接单；扣减失败（`AppException 40031`）则**不建本地单**，立即调平台"商家取消/拒单（原因：库存不足）"，记录告警与同步监控，并触发该商品平台库存归 0。替换当前"抛异常→Inbox 反复重试"的行为。
2. **售罄即时镜像（主防线）**：扣减/回库事务中检测库存**跨 0 翻转**（>0→0 售罄、0→>0 恢复），经 Outbox 给该门店已绑定的**每个平台**各发一条镜像事件。事件 handler 执行时**重新读取最新本地库存再推送**（美团 `food/sku/stock=0`，饿了么 updateShelfStock），因此即使 Outbox 乱序/去重也最终收敛到正确值（售罄只置库存 0，不做"下架"，补货可自动恢复）。
3. **定时对账（兜底）**：每 5 分钟按门店+平台把本地在售商品库存/价格/上下架状态对账覆盖一次，修复任何漏推。

> Outbox 幂等合并是期望行为：同一商品已有未消费的"售罄"事件时，新的售罄事件被合并；"售罄/恢复"是不同 `event_kind` 不会互相吞；handler 以实时库存为准，乱序无害。

## 三、文件与模块改动

### 后端
- [models/__init__.py](file:///e:/SmartNoodleHouse/backend/app/models/__init__.py)：新增
  - `StoreBusinessHours`（store_id、weekday 1-7、open_time、close_time、is_open、跨天 midnight_cross）门店营业时间。
  - `PlatformCategoryMap`（store_id 可空=总部、platform、local_category_id、platform_category_code、platform_category_name、status）。
  - `PlatformStoreBinding` 增加：`auto_sync_open`(bool，默认 false)、`platform_open_status`(string，最后同步的营业状态)、`last_sync_at`。
  - `DeliverySyncAlert`（或复用 outbox/日志）记录拒单/同步失败告警，供监控页展示。
- 新增 `sql/tools/027_delivery_menu_ops.sql`（+ alembic 版本 0012 备注）建表/加列。
- [delivery_providers.py](file:///e:/SmartNoodleHouse/backend/app/services/delivery_providers.py)
  - 美团：`batch_init_foods`、`batch_set_sell_status`、`inc_stock/desc_stock`、`set_sku_stock`（已有覆盖版保留）、`poi_open/poi_offline/poi_save_hours(open_level+shipping_time)`、`update_food_category`。
  - 饿了么：`item_on_shelf/item_off_shelf`、原子库存增减/设置、`shop_open/shop_close/shop_update_hours`。
  - 抽象基类补对应方法（默认 mock），无凭证时沿用 `allow_delivery_mock` 兜底。
- 新增 `app/services/store_hours_service.py`：解析周几时段/跨天，计算门店"当前应营业/休息"，含当日临时休息覆盖。
- 扩展 [product_sync_service.py](file:///e:/SmartNoodleHouse/backend/app/services/product_sync_service.py)：批量菜单（按 ≤200 分批）、批量上下架、库存镜像、价格镜像、分类映射注入、单店对账 `reconcile_store`。
- 新增 `app/services/delivery_sync_handlers.py`：注册 Outbox handler
  - `(delivery_menu, *, stock_zero|stock_resume|stock_reconcile)`
  - `(delivery_menu, *, product_changed|price_changed|shelf_changed)`
  - `(delivery_store, *, open|close|hours_changed)`（遍历该门店所有平台 binding 推送）。
- [stock_service.py](file:///e:/SmartNoodleHouse/backend/app/services/stock_service.py)：扣减/回库后检测跨 0 翻转，事务内 `enqueue_outbox`（仅在该商品存在平台映射时；门店维度）。
- 商品/分店库存后台写入处（products、store-stocks 对应 service）：改价/上下架/库存保存成功后 enqueue 平台同步事件。
- [delivery_service.py](file:///e:/SmartNoodleHouse/backend/app/services/delivery_service.py) `_create_from_platform`：包裹扣库存 `except AppException(40031)` → 主动 `provider.cancel_order(库存不足)` + 写告警 + 触发售罄镜像，不创建本地单。
- [tasks/scheduler.py](file:///e:/SmartNoodleHouse/backend/app/tasks/scheduler.py)：
  - 每分钟 `store_hours_tick`：对开启 `auto_sync_open` 的门店按时段自动开/关店（Leader only）。
  - 每 5 分钟 `delivery_stock_reconcile`：库存/价格/上下架对账。
- [api/v1/delivery.py](file:///e:/SmartNoodleHouse/backend/app/api/v1/delivery.py)：新增路由
  - 营业时间：`GET/PUT /delivery/stores/{id}/business-hours`、`POST /delivery/stores/{id}/open|close`（手动）。
  - 平台餐品：`POST /delivery/sync/batch-menu`、`POST /delivery/sync/shelf`（上下架）、`POST /delivery/sync/reconcile`、分类映射 `GET/POST/DELETE /delivery/category-maps`。
  - 监控：`GET /delivery/sync-alerts`、`GET /delivery/sync-tasks`（基于 outbox delivery 事件状态）。
  - 复用已有 `product:read/update` 权限与 `store_scope` 门店隔离。

### 前端
- AdminLayout：新增独立一级菜单组 **【外卖】**（子项：外卖订单、门店与营业、外卖餐品、同步监控），从"营业"组移除 `/delivery`；KDS 保留原入口（已合并显示，仅增强平台徽标）。
- 路由：`/delivery` → 重定向 `/delivery/orders`；新增 `/delivery/orders`、`/delivery/stores`、`/delivery/stores/:id/hours`（或同页抽屉）、`/delivery/menu`、`/delivery/sync`。
- 新增视图目录 `frontend/src/views/delivery/`：
  - `DeliveryOrders.vue`：迁移现有订单+退款 Tab。
  - `DeliveryStores.vue`：多门店绑定卡片（平台、门店、授权状态、Token 过期、营业状态徽标）+ 手动开/关店 + 进入营业时间配置（周几多时段、跨天、自动开关店开关，默认关）。
  - `DeliveryMenu.vue`：按门店+平台筛选；商品列表（本地名/平台 SKU/价格/本地库存/平台库存/上下架）；批量同步菜单、单个/批量上架下架、改价、库存对账、分类映射维护。
  - `DeliverySync.vue`：最近同步任务状态（成功/失败/重试次数）、拒单与告警、回调日志入口。
  - 现有 [Delivery.vue](file:///e:/SmartNoodleHouse/frontend/src/views/Delivery.vue) 逻辑拆分迁移；bindings/maps/logs 能力迁入对应新页。

## 四、实施步骤（依赖顺序）

1. **数据层**：模型新增字段/表 + `027` SQL；Provider 抽象方法补默认 mock。
2. **平台能力层**：美团/饿了么批量、上下架、原子库存、开关店/营业时间接口（mock 兜底）。
3. **营业时间服务 + 调度**：`store_hours_service` + `store_hours_tick`（默认仅对 `auto_sync_open=true` 生效）。
4. **防超卖闭环**：stock 跨 0 翻转 enqueue；Outbox handler 推送平台；落单扣减失败主动拒单+告警。
5. **商品变更钩子 + 对账**：改价/上下架/库存事件；批量菜单/分类映射；`reconcile_store` 定时任务。
6. **后端 API**：营业时间、开关店、批量同步、上下架、分类映射、监控路由。
7. **前端重构**：独立【外卖】菜单 + 4 个视图迁移与新建。
8. **验证**：单测 + 构建（见下）。

## 五、依赖与考量
- 双平台门店隔离：所有同步/开关店以 `PlatformStoreBinding`(store_id+platform) 为单位；本地一次库存变更向多个平台分别投递事件。
- 自动开关店**安全默认关闭**，需逐门店显式开启，防止上线即影响真实门店营业；手动开关店始终可用。
- 售罄用"库存置 0"而非"下架"（补货自动恢复，符合美团规则）；长期不卖才用显式下架。
- 接口路径/参数依官方文档实现，无真实 App 凭证时走 mock；字段可能随平台版本微调，标注需联调点。
- 营业时间格式：内部统一 `HH:MM`，推送美团转 `shipping_time`，饿了么按其 RPC 字段。
- 不改动堂食/小程序既有交易逻辑；外卖镜像全部走异步 Outbox，不阻塞堂食下单。

## 六、验证
- `python -m pytest`：新增 ①营业时间判定（跨天/多时段/休息覆盖）②库存跨0翻转只发对应事件 ③落单扣减失败触发拒单不建单 ④Outbox handler 以实时库存对账（乱序收敛）⑤多门店门店隔离；并回归现有外卖/库存用例。
- `ast.parse` 校验全部改动 Python；`cd frontend && npm run build` 通过。
- 手动：mock 模式下走"售罄→两平台库存归0→补货恢复""到点自动关店/开店""批量同步菜单/上下架"全链路。

## 七、风险与兜底
- **真实平台字段/鉴权差异**：沿用现网关系列，接口按官方文档实现 + mock；真机联调若字段不符，仅调整 provider 映射，不动业务闭环。
- **误关店影响营业**：自动同步默认关闭 + 手动二次确认 + 仅 Leader 执行。
- **事件乱序/重复**：handler 读实时库存对账；Outbox 幂等 + 过期回收 + 退避已具备。
- **拒单合规**：仅在本地确实无库存时拒单，原因固定"商品售罄"，并留告警供人工核对。
- **工作量大**：按八步顺序提交，每步可独立验证；前端为纯迁移+新增，不影响既有页面路由可用旧路径重定向过渡。

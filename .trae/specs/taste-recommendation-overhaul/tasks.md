# 顾客智能口味推荐系统系统性优化 - 实施计划

> 说明：任务按依赖顺序串行推进；同一编号内后端模型/服务/接口与测试保持同一切片。
> 所有新增后端测试放 `e:\SmartNoodleHouse\tests`；迁移沿用 0017 的 MySQL 原生 SQL + 存在性检查风格，编号 0018。

## Task 1: 数据模型与 Alembic 0018 迁移
- **Status**: `pending`
- **Priority**: high
- **Depends On**: None
- **Description**:
  - `backend/app/models/__init__.py` 新增模型：
    - `ProductTasteProfile`（product_id 唯一；spicy/sweetness 等数值维度用 JSON `dimensions`；`tags` JSON 字符串数组；`source` AUTO/MANUAL；`manual_overrides` JSON 保存被手动覆盖的键；时间戳）
    - `CustomerTasteProfile`（唯一键 customer_id+dim_type+target_key；dim_type ∈ CATEGORY/PRODUCT/TASTE_TAG；score Numeric(6,4)；event_count；last_event_at；last_computed_at）
    - `CustomerBehaviorEvent`（id；customer_id；store_id；event_type；scene；product_id 可空；category_id 可空；event_id 唯一；ab_bucket；payload JSON；created_at；索引 customer_id+created_at、store_id+event_type+created_at）
    - `ProductFeedback`（唯一键 order_id+product_id+customer_id；verdict 标签 JSON；sentiment POSITIVE/NEGATIVE/NEUTRAL；created_at）
    - `AbExperiment`（experiment_key 唯一；name；status DRAFT/RUNNING/PAUSED；traffic_percent；buckets JSON；created/更新时间）
    - `AbAssignment`（唯一键 experiment_key+customer_id；bucket；assigned_at）
  - `customers` 表新增：`recommend_opt_in`（Integer 默认 1）、`consent_version`（String 可空）、`recommend_consent_at`（DateTime 可空）。
  - `product_categories` 表新增 `recommend_config` JSON 可空。
  - 新建 `backend/alembic/versions/0018_taste_recommendation.py`（down_revision=0017_financial_ledger_idempotency），MySQL information_schema 存在性检查建表/加列/加索引，含 downgrade。
- **Acceptance Criteria Addressed**: AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-8, AC-10（模型承载）
- **Test Requirements**:
  - `rule` TR-1.1: sqlite create_all 后新表均可插入与查询（pytest 冒烟）；0018 迁移文件可被 alembic 识别（`alembic history` 含 0018，或静态检查 revision/down_revision 链完整）。
  - `rule` TR-1.2: 唯一约束生效：同 event_id 重复插入、同 (customer,dim,target) 重复画像、同订单产品重复评价均触发 IntegrityError。
- **Notes**: 模型字段命名与现有 TimestampMixin 风格一致；BigInteger 主键沿用 `.with_variant(Integer, "sqlite")`。

## Task 2: 产品口味画像推导服务与后台读写接口
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 1
- **Description**:
  - 新建 `backend/app/services/taste_profile_service.py`：
    - 可配置词典（辣度 spicy、甜度 sweetness、酸度、麻度、温度 temperature、口感 texture、风味 flavor 等），从 option group/option 的 code+name、产品名、描述、分类名推导；词典以 Python 常量 + system_settings 可选覆盖。
    - `derive_for_product(product, groups/options/category)` → dimensions + tags（带置信来源）。
    - `recompute_product(db, product_id, *, manual=False)`：AUTO 重算保留 `manual_overrides` 中的键。
    - `upsert_manual(product_id, payload)`：合并保存手动标签、登记 overrides。
    - 批量 `recompute_store(store_id)`。
  - 管理端接口（挂现有 products 路由或新 admin 路由，沿用 CurrentUser 权限体系）：`GET/PUT /products/{id}/taste-profile`、`POST /products/taste-profiles/rebuild?store_id=`。
  - 产品/规格变更的现有 create/update 路径中触发自动推导（仅新画像或 AUTO 画像）。
  - 产品 payload（后台与小程序商品详情）附带 taste_profile 摘要。
- **Acceptance Criteria Addressed**: AC-2, AC-14
- **Test Requirements**:
  - `rule` TR-2.1: 构造名称/规格含"微辣/少糖/冰/劲道/酸辣"的产品，推导结果对应维度与标签正确；未知词不产生脏标签。
  - `rule` TR-2.2: MANUAL 覆盖键在 AUTO 重算后保持不变，未覆盖键正常刷新；越权（无令牌顾客端）不可写。
  - `rubric` TR-2.3: 推导规则可扩展性（新增词典项不改核心流程）；scale 1-5；anchors 1=硬编码 if-else 堆叠、3=词典可配但维度固定、5=维度/词表/权重均可配置且有单测表驱动；threshold >= 4；evidence=代码走查+参数化测试。

## Task 3: 后台产品编辑页口味标签微调 UI
- **Status**: `pending`
- **Priority**: medium
- **Depends On**: Task 2
- **Description**:
  - `frontend/` Vue 后台产品编辑表单新增"口味属性"分区：数值维度滑杆/选择、标签多选+自定义、"恢复自动推导"按钮（清除该键手动态）。
  - 调用 Task 2 的 GET/PUT 接口；保存成功有明确反馈；门店列表/批量操作中提供"重算口味画像"入口（可选二次确认）。
- **Acceptance Criteria Addressed**: AC-2
- **Test Requirements**:
  - `rule` TR-3.1: 页面可读取画像、修改并保存成功（网络面板 PUT 200），再次进入回显已保存值；"恢复自动"后该键回退为 AUTO 值。
  - `rubric` TR-3.2: 交互与现有后台表单风格一致性；scale 1-5；anchors 1=风格突兀无校验、3=可用但反馈弱、5=风格统一、有加载/错误/成功态与来源标记；threshold >= 4；evidence=代码走查。
- **Notes**: 无浏览器环境时以代码走查 + 构建（npm run build 或现有校验脚本）通过为准。

## Task 4: 顾客行为事件采集 API 与双端埋点 SDK
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 1
- **Description**:
  - 后端新建 `backend/app/services/behavior_service.py` + 小程序路由：
    - `POST /miniprogram/events`：顾客 JWT；body `{events:[...]}`，单批 ≤50；event_id 客户端生成 uuid 去重（INSERT 或 IGNORE / 先查后插）；event_type/scene 白名单枚举；字段长度截断/拒绝；ab_bucket 透传；为每条补齐 customer_id/store_id；关闭个性化（recommend_opt_in=0）时除开关审计事件外全部丢弃并返回 accepted=0。
    - 入站事件入表后投递增量画像更新钩子（Task 5 接入，先留内部 hook 点）。
  - 双端新建 `utils/tracker.js`（同构，wx/my 适配）：本地队列 + event_id 生成 + 批量合并（≤20 条或 5s 超时）+ 页面 onHide/ App onHide flush + 失败静默本地重试一次；scene 常量；从 globalData 读取 customer/store/ab_bucket。
  - 双端 app.js 注册 onHide/onShow flush 时机。
- **Acceptance Criteria Addressed**: AC-1, AC-7（埋点底座）, AC-10（关闭不落库）
- **Test Requirements**:
  - `rule` TR-4.1: pytest：批量 50 条成功、51 条 4xx、重复 event_id 只入库一次、非法 event_type 拒绝、无令牌 401、opt_in=0 时 accepted=0 且表内无记录。
  - `rule` TR-4.2: 双端 tracker.js node --check 通过；静态检查：事件经单一队列、flush 绑定 onHide、wx/my 双 API 分支齐备。
  - `rubric` TR-4.3: 对主流程零侵入（点餐/支付不因埋点失败中断）；scale 1-5；anchors 1=埋点 await 阻塞主操作且错误上抛、3=有 try/catch 但有阻塞点、5=全异步 fire-and-forget + 一次重试 + 队列上限防内存膨胀；threshold >= 4；evidence=代码走查。

## Task 5: 多维画像增量学习服务与兜底任务
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 2, Task 4
- **Description**:
  - 新建 `backend/app/services/taste_learning_service.py`：
    - 维度聚合：CATEGORY（分类亲和）、PRODUCT（单品/复购）、TASTE_TAG（口味标签，从 ProductTasteProfile 关联事件 product_id 取）；行为权重表（EXPOSE 0.2 / CLICK 1 / CART_ADD 2 / CART_REMOVE -1.5 / ORDER_PAID 按数量 3 / REORDER +1 / 负向评价 -3 / 正向评价 +2），时间半衰期衰减（可配），结果归一化到 0..1。
    - `apply_events_incremental(db, events)`：仅重算受影响顾客的受影响维度，更新 score/event_count/last_event_at/last_computed_at。
    - `recompute_customer_full(db, customer_id)`：从行为事件 + 已付订单全量重算（与现有规格偏好并存，不写 customer_preferences）。
  - Task 4 事件入库成功后同步调用增量更新（同事务或事务后安全 catch，失败不影响上报响应）。
  - `tasks/scheduler.py` 新增 ≤5 分钟 interval 任务：扫描 last_computed_at 落后于最新事件的顾客做补偿重算（leader fence 模式）。
  - 支付成功链路在现有 PreferenceService.update_from_order 之外挂接 ORDER_PAID 画像更新（不改动现有规格偏好算法）。
- **Acceptance Criteria Addressed**: AC-4, AC-5（降权信号消费）, AC-12
- **Test Requirements**:
  - `rule` TR-5.1: 单条 CLICK/CART_ADD 后对应维度 score 上升并推进 last_computed_at；CART_REMOVE/负反馈后下降；权重方向零反转。
  - `rule` TR-5.2: 模拟增量异常（monkeypatch 抛错）后，补偿任务能把滞留顾客画像重算到与全量一致；任务周期配置 ≤300 秒。
  - `rule` TR-5.3: 现有 `customer_preferences` 与 `/customers/me/preferences` 返回值在新链路接入后与基线一致（快照对比测试）。

## Task 6: 轻量评价（后端接口 + 双端提交入口）
- **Status**: `pending`
- **Priority**: medium
- **Depends On**: Task 5
- **Description**:
  - 后端：`POST /miniprogram/orders/{id}/feedback`（仅本人 PAID 订单可评；每订单每产品一次，幂等；verdict 标签白名单 + sentiment 推导；写入 product_feedbacks 并调画像增量更新）；`GET /miniprogram/orders/{id}/feedback` 回显。
  - 双端：订单详情/结果页提供轻量评价条（标签点选，不强制；可跳过），通过 tracker 同构 request 封装提交；提交后不弹打扰式成功页。
- **Acceptance Criteria Addressed**: AC-5
- **Test Requirements**:
  - `rule` TR-6.1: 非本人订单 403；未支付订单 4xx；重复提交幂等；负向标签提交后画像 TASTE_TAG/PRODUCT 分数下降（排序变化在 Task 7 验证）。
  - `rule` TR-6.2: 双端提交代码 node --check 通过；页面存在评价入口与跳过路径（静态走查）。

## Task 7: 产品级混合推荐核心服务
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 5
- **Description**:
  - 新建 `backend/app/services/recommendation_service.py`：
    - 门店商品池构建（品牌状态 + StoreProductStock 覆盖 + stock>0），复用现有菜单读模型，结果可缓存。
    - 召回四路：①内容画像匹配（CATEGORY/TASTE_TAG 画像 × ProductTasteProfile）②订单共现协同过滤（按门店近 N 单聚合"点了又点"，进程内 LRU + Redis 缓存，无数据时该路为空）③门店热销（sales_count/is_hot）④新品/运营推荐（is_recommended + 新近上架）。
    - 排序：加权线性分 + preference_scene 场景乘子复用；权重从分类 recommend_config 合并全局默认（分类级：content/cf/popular/novel 权重、去重窗口天、复购窗口）。
    - 负反馈降权与近期已购去重窗口；多样性约束（同分类连续不超过 2 个）。
    - 三态判定与降级补齐：热（画像事件数 ≥ 阈值）/温（1+ 事件）/冷（无事件或 opt_in=0）；任何分支不足 limit 用热销补齐；空门店返回空列表。
    - 理由文案生成（"您偏爱的辣口""常点品类""本店热销""新品"等，可解释）。
    - 场景：recommend_menu（默认 10）与 settle_addon（排除购物车 product_ids，搭配向：与购物车品类互补）。
  - 纯函数核心（打分/排序/补齐）与 IO 分离，便于离线评估（Task 14）直接导入。
- **Acceptance Criteria Addressed**: AC-3, AC-6, AC-13
- **Test Requirements**:
  - `rule` TR-7.1: 三态顾客在同一门店得到各自预期分支结果（断言 reason 来源与画像相关性）；跨店商品绝不出现；停售/库存 0 被过滤；空店返回 []。
  - `rule` TR-7.2: 翻转某分类 recommend_config 的 cf/content 权重后，该分类产品次序发生预期翻转；删配置回退默认。
  - `rule` TR-7.3: 负向评价/退款产品名次下降；去重窗口内已购产品降权；settle_addon 排除购物车商品。
  - `rubric` TR-7.4: 可解释性与多样性（每条结果有 reason、分类不刷屏）；scale 1-5；anchors 1=黑盒无理由且同品类霸榜、3=有理由但经常不贴切、5=理由稳定贴切且多样性约束生效；threshold >= 4；evidence=用例断言 reason 非空 + 走查。

## Task 8: 推荐小程序接口与 Redis 缓存层
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 7, Task 9（A/B 桶位由 Task 9 提供；可先内置默认桶并行开发）
- **Description**:
  - `GET /miniprogram/recommendations?store_id=&scene=&limit=`：顾客 JWT（缺失可走冷启动）；瘦字段 payload（id/name/price/main_image 完整 URL/taste_tags/reason/is_hot/活动标记）；响应体带 `ab:{experiment,bucket}`。
  - `backend/app/core/cache_keys.py` 统一键规范；推荐缓存 `reco:{cid}:{store}:{scene}:{bucket}` TTL ≤120s；门店商品池/共现矩阵缓存；画像更新、下单、评价提交后主动删除该顾客推荐缓存；菜单/分类聚合缓存（`menu:{store}` 与 `catalog_version:{store}`）由产品/分类/规格/门店库存写路径失效。
  - Redis 异常全部 try 降级直查 DB（不抛给顾客端）。
- **Acceptance Criteria Addressed**: AC-9, AC-3（接口形态）
- **Test Requirements**:
  - `rule` TR-8.1: 首次请求未命中、第二次命中（通过调用计数/monkeypatch 断言服务函数只执行一次）；下单/画像更新后再请求为新结果；Redis monkeypatch 故障时仍 200。
  - `rule` TR-8.2: payload 不含 option_groups 等重字段；未登录顾客返回冷启动结果；场景 settle_addon 支持 cart_item_ids 参数排除。

## Task 9: A/B 实验框架（分流服务 + 管理接口）
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 1
- **Description**:
  - 新建 `backend/app/services/ab_service.py`：
    - 实验 CRUD（管理端）：key/name/status/traffic_percent/buckets（[{code, params}]），参数白名单（如 reco_weights profile）。
    - `assign(customer_id, experiment_key)`：md5(customer_id+key)%100 < traffic 则按比例稳定分桶并落 AbAssignment（幂等 upsert）；否则 default 桶；PAUSED/DRAFT 恒走默认。
    - 推荐服务经 AB 参数改变排序权重（v1=旧逻辑近似/v2=新混合权重），默认实验 `reco_algo` 种子数据由 system_settings ensure_seeded 写入（DRAFT 状态，运营启用）。
  - 管理端接口：`/admin/ab-experiments` CRUD + `GET /admin/ab-experiments/{key}/report?date_from=&date_to=`（基于行为事件聚合各桶曝光/CTR/加购采纳率/下单转化）。
- **Acceptance Criteria Addressed**: AC-8
- **Test Requirements**:
  - `rule` TR-9.1: 同顾客 100 次调用桶位恒定；模拟 1000 顾客 50% 流量两桶分布在 ±10% 内；暂停实验后全走 default。
  - `rule` TR-9.2: 报表聚合数值与手工构造事件一致（曝光/点击/加购计数与比率误差 0）。

## Task 10: 双端"猜你喜欢"推荐位与结算搭配推荐 UI
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 4, Task 8, Task 9
- **Description**:
  - 双端菜单页口味条下方新增横滑"猜你喜欢"组件（独立 component：骨架屏、卡片图/名/价/理由标签、点击进详情、"+"复用 quickAdd 链路）；异步于菜单首屏之后加载；EXPOSE 在卡片实际进入可视区（或组件 onShow 批量）上报，CLICK/CART_ADD 带 scene=recommend、ab_bucket。
  - 双端结算页空购物车下方/有商品时底部一行"顺手再来一份"settle_addon 推荐，排除购物车商品；埋点同构。
  - tracker 注入 ab_bucket（从推荐响应缓存）。
- **Acceptance Criteria Addressed**: AC-7, AC-13
- **Test Requirements**:
  - `rule` TR-10.1: 双端 4 个页面/组件文件（js+wxml+wxss 或 axml/acss）齐备且 node --check 通过；接口请求在菜单数据加载之后发起（代码走查调用顺序）。
  - `rule` TR-10.2: 曝光/点击/加购三类事件均携带 scene 与 ab_bucket（静态走查 tracker 调用参数 + 服务端契约测试）。
  - `rubric` TR-10.3: 视觉与打扰度；scale 1-5；anchors 1=遮挡菜单/自动弹层、3=位置合理但样式粗糙、5=横滑不抢主流程、有理由标签、双端一致、弱网有骨架与降级隐藏；threshold >= 4；evidence=wxml/wxss 走查。

## Task 11: 后台推荐效果报表页
- **Status**: `pending`
- **Priority**: medium
- **Depends On**: Task 9
- **Description**:
  - `frontend/` Vue 新增"推荐效果"页：实验选择、日期范围、桶对比表（曝光、点击、CTR、加购采纳率、下单转化、人均推荐 GMV 口径=推荐位归因订单金额），简单趋势（可用现有图表库；若无则表格 + 原生 SVG 折线）。
  - 归因口径：scene=recommend 且 ab_bucket 命中的 CLICK→CART_ADD→ORDER_PAID 同顾客同产品 24h 内漏斗（后端聚合实现，文档化口径）。
- **Acceptance Criteria Addressed**: AC-8
- **Test Requirements**:
  - `rule` TR-11.1: 构造跨 2 天两桶事件后接口返回数值与手算一致；前端页面构建通过且调用真实接口路径。
  - `rubric` TR-11.2: 口径可读性（页面展示归因说明）；scale 1-5；anchors 1=无口径说明数字误导、3=有数字无解释、5=漏斗口径与时间窗明确标注；threshold >= 4；evidence=走查。

## Task 12: 性能治理（中间件/GZip/菜单缓存/前端请求优化）
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 8
- **Description**:
  - 后端：
    - `PerformanceMetricsMiddleware`：记录每请求耗时，按路由模板（路径归一化，去数字 ID）写 Redis 滚动窗口结构（P50/P95/P99/QPS，近 24h 分桶），>800ms 慢请求结构化日志。
    - GZip 中间件（JSON 且 >1KB 才压缩）。
    - 管理接口 `GET /admin/performance/reco-metrics`：只读返回聚合指标。
    - 菜单聚合（分类+产品+规格组一次成型）读侧缓存配合 Task 8 失效；产品列表支持 `version`/`updated_since` 轻量校验，无变化返回 304 语义小响应（code=0, changed:false）。
  - 双端前端：
    - 菜单/产品数据 storage 缓存 + 版本戳；先渲染缓存再后台静默校验；`utils/products.js` 与 menu loadMenu 改造；推荐接口独立异步。
    - tracker 批量与 onHide flush（Task 4 已含，此处联调）。
    - 确认 image lazy-load 现状保持；推荐图使用 CDN/原图尺寸参数则保留现状不新增依赖。
  - 基准脚本 `tests/推荐系统性能基准.py`：同机构造 500 SKU/多分类数据，分别对"直查基线"与"缓存路径"连续计时统计，输出提升百分比。
- **Acceptance Criteria Addressed**: AC-9, AC-11 部分
- **Test Requirements**:
  - `rule` TR-12.1: 基准脚本输出：缓存命中 P95 ≤100ms、预热态 P95 ≤500ms、平均耗时相对直查基线下降 ≥40%（断言并打印报告）。
  - `rule` TR-12.2: GZip 对 >1KB JSON 响应生效（Accept-Encoding: gzip 返回 gzip 解码内容一致）；慢查询日志与指标查询接口有数据；版本戳未变化时返回 changed:false 且前端不重绘列表。
  - `rule` TR-12.3: 双端改动文件 node --check 通过；弱网（接口失败）菜单缓存仍可渲染。

## Task 13: 隐私合规包（开关/画像查看清除/留存清理/同意留痕）
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 5, Task 4
- **Description**:
  - 后端接口（顾客本人）：
    - `GET/PUT /miniprogram/customers/me/privacy-settings`（recommend_opt_in 读写；关闭时：行为事件丢弃生效、推荐返回非个性化、可选清理埋点队列由前端执行）。
    - `GET /miniprogram/customers/me/taste-profile`：返回规格偏好 + 三维画像可读视图。
    - `POST /miniprogram/customers/me/taste-profile/clear`：删行为事件、画像、产品评价（画像派生数据），现有 customer_preferences 同步清空；订单本体保留；写 AuditService 日志。
    - 首次同意：`POST /miniprogram/privacy/consent`（版本化 recommend 项 + 时间戳）；隐私文案常量/系统设置提供"个性化推荐与口味画像说明"。
  - 定时任务：行为事件留存期（默认 90 天，system_settings 可调）自动删除/匿名化（leader fence）。
  - 双端：会员/设置页新增"个性化推荐"开关、"查看我的口味画像"页/弹层、"清除口味画像"二次确认；隐私弹窗/隐私说明页新增段落；tracker 尊重开关（关闭时不入队）。
  - 顾客注销/合并既有匿名化链路挂接事件清理（如存在该服务）。
- **Acceptance Criteria Addressed**: AC-10
- **Test Requirements**:
  - `rule` TR-13.1: 6 子项 pytest 全过：关开关后推荐非个性化且事件不落库；画像查看完整；清除后三表为空且订单仍在、审计有记录；超期事件被任务清理；consent 版本/时间落库；顾客 A 令牌访问顾客 B 画像/清除接口 403。
  - `rule` TR-13.2: 双端入口代码齐备 node --check 通过；tracker 在 opt_in=0 时不产生网络请求（静态走查）。

## Task 14: 离线评估合成数据与效果报告
- **Status**: `pending`
- **Priority**: medium
- **Depends On**: Task 7
- **Description**:
  - `tests/推荐系统离线评估.py`（与数据生成器同文件或同目录 helper）：
    - 固定随机种子，生成 ≥500 顾客（多口味原型人群：嗜辣/清淡/甜饮/尝鲜等）、≥100 SKU 跨 ≥5 品类（含面/饮品/小食/烘焙风格分类）、≥2000 订单（含规格选项快照），注入噪声（5% 随机单）与负反馈（退款/移除/差评）。
    - 时序切分：前 80% 时间训练，后 20% 订单产品为 ground truth。
    - 基线：门店热销 + 现有规格偏好直接映射；新算法：Task 7 混合推荐。
    - 指标：precision@5、recall@5、采纳代理命中率（TOP5 命中未来点击/加购/购买）；输出文本报告（总体 + 分人群 + 冷启动子集）。
    - 断言新算法 precision@5 提升 ≥30%、采纳代理 ≥25%，否则退出码非 0。
- **Acceptance Criteria Addressed**: AC-11
- **Test Requirements**:
  - `rule` TR-14.1: 脚本连续两次运行（固定种子）结果一致且退出码 0，报告包含基线/新算法两组数值与提升百分比（报告文本存档于测试输出）。
  - `rubric` TR-14.2: 数据集真实度（人群原型/噪声/负反馈/品类结构贴近堂食场景）；scale 1-5；anchors 1=纯随机数据指标无意义、3=有订单但无噪声与负反馈、5=人群原型+时序切分+噪声+负反馈+冷启动子集齐备；threshold >= 4；evidence=报告与生成器走查。

## Task 15: 全量回归与端到端联调
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 1–14
- **Description**:
  - 新增 pytest 测试文件覆盖上述各 rule TR；运行 `python -m pytest tests/ -q` 全绿；双端所有新增/修改 JS 逐个 node --check；后台前端构建/校验通过。
  - 端到端走查脚本化：登录→上报事件→画像更新→推荐三态→A/B 桶→埋点聚合报表→关闭个性化→清除画像。
  - 迁移在 0017 旧库升级路径检查（本地 MySQL 若可用；否则 alembic SQL 静态审查 + sqlite create_all）。
- **Acceptance Criteria Addressed**: AC-12
- **Test Requirements**:
  - `rule` TR-15.1: 全量 pytest 通过数 = 既有用例 + 新增用例，无失败（已知 pytest 进程不退出为既有问题，不作失败）。
  - `rule` TR-15.2: 既有 17 项 P0/生命周期回归测试 + 全量测试通过；规格偏好快照测试无差异。
  - `rubric` TR-15.3: 端到端链路完整度；scale 1-5；anchors 1=各模块孤立可用但链路断裂、3=主链路通但边界缺失、5=主链路+边界（弱网/关开关/清画像/冷启动）全部串通并有自动化覆盖；threshold >= 4；evidence=E2E 测试/走查记录。

## Task 16: 独立评审与整改闭环
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 15
- **Description**:
  - 队列清空后创建 review.md，委派全新上下文的独立评审（只读），按 AC-1～AC-14 逐项核对证据；fail 则把 actionable 发现落成 Issue 任务回到 Implement，直至新评审 pass。
- **Acceptance Criteria Addressed**: 全部 AC
- **Test Requirements**:
  - `rule` TR-16.1: review.md 存在且最新一轮 Review Result=pass，每个 AC 均有独立证据。

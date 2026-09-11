你现在需要作为这个项目的「顶级软件架构师 + 高级 Python/FastAPI 工程师 + 高级 Vue/微信小程序/支付宝小程序工程师 + 支付系统架构师 + 数据库专家 + Redis 分布式系统专家 + 网络安全专家 + 人脸识别系统工程师 + QA/测试工程师」，对当前整个 SmartNoodleHouse / Cwm 项目进行一次真正可以上线生产的全面审计、修复和完善。

============================================================
一、最高优先级总要求
============================================================

不要只做代码点评。

必须：

1. 先完整扫描整个仓库。
2. 理解现有架构。
3. 找出所有安全、逻辑、业务、并发、支付、数据一致性、性能、前端、小程序、数据库、部署问题。
4. 对发现的问题进行 P0/P1/P2/P3 分级。
5. 对 P0/P1 问题直接修改代码。
6. 必要时新增 Alembic migration。
7. 必要时修改前端。
8. 必要时修改微信小程序。
9. 必要时修改支付宝小程序。
10. 必要时增加测试。
11. 不允许为了“通过测试”而删除功能。
12. 不允许通过弱化安全检查来规避问题。
13. 不允许把异常简单 catch 后静默忽略。
14. 不允许新增 TODO 代替实际实现。
15. 不允许只修改注释而不修改实际逻辑。
16. 不允许破坏现有正常业务能力。
17. 所有支付、退款、积分、优惠券、库存、账号合并必须优先保证数据一致性。
18. 所有身份认证必须 fail-closed。
19. 所有支付回调必须 fail-closed。
20. 所有分布式锁必须使用真正原子操作。
21. 所有异步任务必须支持 crash recovery。
22. 所有重要业务操作必须具备幂等能力。
23. 所有门店数据必须严格 store scope 隔离。
24. 最终必须运行代码检查、测试和构建验证。

不要停留在“建议”。

能修的直接修。

============================================================
二、先理解并保留正确的业务模型
============================================================

当前项目的正确产品目标：

顾客扫码进入面馆：

扫码
→ 微信/支付宝身份认证
→ 如果是新用户，后台可以创建 Customer
→ Customer 初始 member_status=GUEST
→ GUEST 可以正常浏览菜单、点餐、支付
→ 不允许强迫注册会员
→ 用户主动点击“开启智能会员”后：
   GUEST → MEMBER
→ 会员可以：
   - 会员等级
   - 会员折扣
   - 优惠券
   - 积分
   - 智能口味
   - 人脸迎宾（必须再次明确授权）

请保留这个核心设计。

绝对不要恢复成：

“第一次扫码必须注册会员才能点餐”。

Customer 是“平台身份”。

MEMBER 是“业务会员状态”。

这是两个不同概念。

微信官方 OpenID / UnionID、支付宝 user_id、经过官方验证的手机号都属于稳定身份。

============================================================
三、P0 安全漏洞：支付宝手机号绝对不能接受客户端伪造明文
============================================================

重点检查：

backend/app/services/alipay_mini_client.py

当前 decrypt_phone_response / _extract_cipher_from_envelope 存在严重风险：

不能接受客户端自己提交：

{
    "mobile": "138xxxxxxxx"
}

并把它视为“支付宝官方验证手机号”。

必须修改为：

生产环境：

1. 必须要求支付宝官方手机号授权返回格式。
2. 必须存在有效加密数据。
3. 必须使用 ALIPAY_AES_KEY 解密。
4. 解密失败必须拒绝。
5. 不允许客户端直接提供 mobile/phoneNumber 作为 verified phone。
6. 不允许通过正则从客户端任意字符串里提取手机号并标记 verified。
7. 不允许 production 下使用明文 fallback。

开发环境：

如果确实需要 mock：

必须同时满足：

APP_ENV=development
AND
ALLOW_AUTH_MOCK=true

否则也不允许 mock。

明确区分：

manual phone input
=
未验证手机号

官方微信 phone_code
=
verified

官方支付宝加密 response
=
verified

绝对不能混淆。

增加测试：

test_alipay_phone_plaintext_cannot_be_verified
test_alipay_fake_mobile_json_rejected
test_alipay_invalid_cipher_rejected
test_alipay_valid_cipher_success
test_production_never_accepts_plain_phone
test_confirm_merge_requires_real_verified_phone

============================================================
四、P0：防止支付宝手机号伪造导致账号合并
============================================================

重点审查：

CustomerService.bind_verified_phone()
CustomerMergeService.merge()

必须保证：

只有：

WECHAT verified phone
或
ALIPAY verified phone

才能进入：

bind_verified_phone()

manual phone 永远不能触发 merge。

confirm_merge=true 也不能绕过验证。

账号合并必须满足：

1. 当前 customer authenticated。
2. 当前 customer ACTIVE。
3. 手机号来自官方验证。
4. PHONE identity 已确认。
5. 目标 customer ACTIVE。
6. source customer ACTIVE。
7. target/source 不能相同。
8. 所有 Customer 行按 ID 升序 FOR UPDATE。
9. 平台身份不能发生冲突。
10. 微信 OpenID 冲突必须拒绝。
11. 支付宝 user_id 冲突必须拒绝。
12. PHONE identity 冲突必须严格处理。
13. 订单、支付、优惠券、积分、偏好、人脸、就餐 Session 全部在一个事务中处理。
14. source token_version 必须递增。
15. source 必须 DISABLED。
16. merge 操作必须审计。
17. merge 后重新计算目标会员等级。
18. merge 后重新计算目标口味偏好，而不是简单取 source/target 的旧 score。

============================================================
五、P0：修复 Redis Leader 分布式锁
============================================================

重点：

backend/app/core/leader_lock.py

禁止：

GET → DELETE

GET → EXPIRE

这种非原子 compare-and-delete / compare-and-expire。

必须使用 Redis Lua script：

compare value:
if GET(key)==worker_id:
    DEL(key)

compare expire:
if GET(key)==worker_id:
    EXPIRE(key, ttl)

获取锁：

SET key worker_id NX EX ttl

续期：

必须原子 compare-and-expire。

释放：

必须原子 compare-and-delete。

增加测试：

两个 worker：

worker A 获得 lock
worker B 不得获得

A lock expired
B 获得 lock
A release 不得删除 B 的 lock

A renew 在 lock 已被 B 获得后不得续期 B 的 lock

高并发 100/1000 次竞争测试。

============================================================
六、P0：摄像头必须真正做到 Leader-only
============================================================

当前 CameraManager 是进程级 singleton。

必须解决：

Gunicorn 多 worker：

worker1 → CameraManager
worker2 → CameraManager

不能同时连接同一个摄像头。

必须设计成：

只有 Leader 才允许：

start_camera
restart_camera
stop_camera
camera recognition
camera capture

非 Leader API worker：

如果收到 camera start 请求：

不得在本地创建 CameraWorker。

必须：

方案 A：
返回 409/503，提示当前 camera manager 不在 Leader。

或者更好：

方案 B：
通过 Redis command queue / IPC 转给 Leader。

至少必须禁止：

非 Leader 创建 camera worker。

camera_stream 也不能在普通 API worker 内偷偷：

_start_worker(camera)

否则多 worker 仍然会重复打开摄像头。

如果当前架构无法优雅跨进程：

把 camera/recognition worker 抽成独立进程服务。

要求：

一个 camera_id 同一时间只能有一个 active CameraWorker。

Leader 丢失：

必须：

stop recognition
stop TTS
stop ALL CameraWorkers

新 Leader：

重新加载 cameras
重新启动需要运行的 cameras。

增加：

camera leader ownership
camera worker lifecycle
stale worker cleanup

测试：

2 worker + 1 camera
2 worker + 2 cameras
leader loss
leader switch
worker crash
camera reconnect

============================================================
七、P0：Webhook Inbox 必须支持 PROCESSING 崩溃恢复
============================================================

重点：

backend/app/services/webhook_inbox.py

当前：

PENDING → PROCESSING → DONE

如果 worker 崩溃：

PROCESSING 永久卡死。

必须增加：

PROCESSING stale recovery。

例如：

locked_at < now - PROCESSING_TIMEOUT

则：

PROCESSING
→ PENDING

或者：

PROCESSING
→ FAILED
→ retry

必须保证：

1. 同一 webhook 不会并发执行两次。
2. worker 崩溃后可以恢复。
3. attempt_count 正确。
4. 最大重试次数正确。
5. 指数退避正确。
6. DONE 永远不再执行。
7. FAILED 到时间自动重试。
8. PROCESSING 超时可以 reclaim。

process_pending_inbox() 不能把“调用次数”当作 claimed 数量。

必须真正统计 claim 成功数。

增加：

test_webhook_processing_recovery
test_webhook_concurrent_claim
test_webhook_done_not_reprocessed
test_webhook_retry_backoff
test_webhook_max_attempts

============================================================
八、P0：支付回调必须 DB-first，Redis 不能成为最终幂等真相
============================================================

重点：

backend/app/services/payment_service.py

当前：

Redis claim
→ DB receipt

存在：

Redis claim 成功
→ process crash
→ DB receipt 没写
→ provider retry
→ Redis claim false
→ 被当成重复

这是支付丢单风险。

修改设计：

最终幂等依据：

PaymentCallbackReceipt
UNIQUE(provider, receipt_key)

Redis 只能作为：

短期锁 / 性能优化

不能因为：

Redis claim false

就直接认为：

“支付已经处理”。

如果 Redis claim false：

必须查询 DB receipt。

如果 DB receipt 不存在：

仍然允许处理。

如果 DB receipt 存在：

根据业务状态决定是否重复。

支付回调必须：

验签
→ 验商户身份
→ 验金额
→ 验币种
→ 验订单
→ DB 幂等
→ 状态机
→ 财务分录
→ commit

============================================================
九、P0：支付 callback 不能把所有 Exception 当成重复
============================================================

当前：

except Exception

必须修改为：

except IntegrityError

才处理：

“receipt unique conflict”。

其它异常：

DatabaseError
OperationalError
TimeoutError
等等

必须：

rollback
raise

让微信/支付宝重试。

增加：

test_callback_integrity_error_is_duplicate
test_callback_db_operational_error_is_not_duplicate
test_callback_unknown_db_error_retries

============================================================
十、P0：支付回调身份必须严格 fail-closed
============================================================

微信：

必须要求：

Wechatpay-Signature
Wechatpay-Timestamp
Wechatpay-Nonce
Wechatpay-Serial

serial 必须参与证书选择/验证。

商户号：

必须存在。

必须等于：

WECHAT_MCH_ID

缺失：

拒绝。

支付宝：

必须要求：

sign
app_id

app_id 必须存在。

必须等于：

ALIPAY_APP_ID

不能：

if app_id and ...

而必须：

if not app_id:
    reject

if app_id != configured:
    reject

增加测试：

wrong_mchid
missing_mchid
wrong_app_id
missing_app_id
wrong_serial
expired_timestamp
invalid_signature
tampered_body

============================================================
十一、P0：支付金额必须严格校验
============================================================

所有支付入口：

微信 JSAPI
微信 Native
微信 Micropay
支付宝小程序
支付宝当面付
Payment Sync
Webhook

必须保证：

provider amount == local payment amount

并且：

currency == CNY

退款：

provider refund amount == local PaymentRefund.amount

任何 mismatch：

payment/refund → UNKNOWN

不得：

SUCCESS

增加：

金额篡改测试
单位错误测试
元/分错误测试
部分支付测试
部分退款测试
全额退款测试

============================================================
十二、P1：微信 V2 付款码支付独立 API Key
============================================================

当前：

wechat_api_v2_client.py

不要默认使用：

WECHAT_API_V3_KEY

作为 V2 MD5 key。

增加：

WECHAT_API_V2_KEY

Settings：

wechat_api_v2_key

生产环境使用独立配置。

完善 V2：

request signature
response signature

都进行验证。

如果官方接口要求不同 sign_type，按官方协议严格实现。

增加：

test_wechat_v2_signature
test_wechat_v2_response_signature
test_wechat_micropay_amount

============================================================
十三、P1：支付查询增加单支付单级别 cooldown
============================================================

当前 IP rate limit 不够。

增加 Redis：

payment_sync:{payment_id}

例如：

2 秒内只允许一次 provider query。

如果订单已经：

PAID
REFUNDED
CLOSED

尽量避免继续查询 provider。

防止：

客户端疯狂点击“确认支付”。

============================================================
十四、P1：退款必须完整状态机
============================================================

退款：

PENDING
PROCESSING
UNKNOWN
SUCCESS
FAILED
CLOSED

必须严格区分。

不能：

“API 调用成功”
=
“资金已经到账”。

只有：

provider confirmed success

才能：

SUCCESS。

退款回调和主动 query 必须最终汇聚到：

同一个 finalize_refund()

必须幂等。

============================================================
十五、P0：订单幂等 Key 必须包含完整业务上下文
============================================================

微信/支付宝小程序：

makeIdempotencyKey()

必须至少包含：

customer_id
store_id
table_token
dining_type
need_pack
remark
coupon_id
voucher_id
items
product_id
quantity
option_ids

不要只包含：

items + needPack

特别是：

table_token

必须进入幂等内容。

否则：

A 桌
和
B 桌

可能复用同一订单。

推荐：

canonical JSON
→ stable sort
→ SHA256
→ checkout nonce

不要依赖弱 hash 作为唯一内容身份。

客户端 nonce 可以使用：

crypto.randomUUID()

或者平台安全随机源。

服务器数据库仍然使用唯一约束最终兜底。

============================================================
十六、P1：禁止所有 POST 无条件自动重试
============================================================

小程序 request():

不要：

所有 POST retries=1。

分类：

GET:
允许有限重试。

明确幂等 POST:
允许重试。

支付创建:
必须依赖 payment idempotency / server protection。

人脸 consent:
不能无条件自动重试。

会员激活:
可以，因为服务端幂等。

手机号:
必须设计成幂等。

client logs:
可以。

所有重试必须：

指数退避
最大次数
明确 timeout
不能无限递归。

============================================================
十七、P0：人脸只能给真正授权会员使用
============================================================

POST：

/miniprogram/face/consent
/miniprogram/face/consent-base64

必须验证：

customer.status == 1
customer.account_status == ACTIVE
customer.member_status == MEMBER

如果不是会员：

拒绝。

如果产品以后允许游客人脸：

必须通过独立 feature flag 明确打开。

默认关闭。

============================================================
十八、P0：人脸 Cache 必须考虑 Customer 状态
============================================================

FaceEmbeddingCache：

不能只：

FaceProfile.consent_status == AUTHORIZED

还必须关联：

Customer.status
Customer.account_status
Customer.member_status

只有：

Customer ACTIVE
AND
MEMBER
AND
FaceProfile AUTHORIZED
AND
feature_data exists

才进入识别 Cache。

顾客被禁用/合并/撤销会员：

必须及时从 cache 删除。

不要等待 5 分钟 full resync。

可以通过 Redis version/event invalidation。

============================================================
十九、P1：人脸注册并发锁
============================================================

FaceService.register_face()

必须：

SELECT Customer FOR UPDATE

然后：

撤销旧 AUTHORIZED FaceProfile
清空 feature_data
创建新 profile

保证同一 customer：

最多一个 AUTHORIZED profile。

增加并发测试：

100 concurrent consent requests

最终：

AUTHORIZED = 1

============================================================
二十、P0：人脸上传必须统一图片安全管线
============================================================

所有：

face consent
face base64
avatar upload
product upload
remote avatar

必须：

1. request body size limit
2. content-length limit
3. upload streaming
4. MIME check
5. magic bytes
6. pixel limit
7. width/height limit
8. decompression bomb protection
9. decode
10. processing

禁止：

先 cv2.imdecode
再检查尺寸。

特别是：

face_liveness.py
face_extractor.py

必须先经过安全图片验证。

============================================================
二十一、P1：Base64 JSON Body 必须提前限流
============================================================

consent-face-base64：

不要直接：

await request.json()

后才检查大小。

增加：

Content-Length limit

如果：

Content-Length > max allowed

立即：

413

然后再：

json parse

base64 size check

raw bytes size check。

============================================================
二十二、P1：Basic Anti-Spoof 必须明确边界
============================================================

当前 basic anti-spoof 可以保留用于：

“迎宾”。

但是：

不得宣称：

“真正活体认证”。

UI 必须明确：

当前为基础防伪，不是金融级/身份认证级活体。

如果系统未来用于：

支付
身份认证
高价值会员

必须支持真正活体 SDK/Challenge Response。

增加配置：

FACE_ANTISPOOF_MODE

例如：

basic
vendor
disabled

production 如果 basic：

明确 warning。

============================================================
二十三、P0：实现真正的人脸前端闭环
============================================================

微信小程序：

必须实现：

会员中心
→ 开启人脸迎宾
→ 明确授权说明
→ 打开摄像头
→ 实时拍摄
→ 上传
→ /face/consent-base64
→ 显示质量
→ 成功
→ 状态变为已开启。

禁止：

从相册选择一张照片冒充实时采集。

必须使用：

wx.createCameraContext()
或符合当前微信官方能力的实时摄像头采集流程。

支付宝：

实现：

my.camera / 官方可用摄像头 API
→ capture
→ 上传
→ face consent

如果平台能力限制：

提供明确 fallback，但不能伪造。

H5：

禁止通过 H5 假数据绕过人脸授权。

============================================================
二十四、P0：未知顾客人脸不要默认偷偷保存
============================================================

这是隐私敏感功能。

如果实现：

“陌生顾客进入 → 保存照片 → 后台审核 → 以后命名”

必须做成：

明确 feature flag。

默认：

OFF。

必须提供：

隐私告知
摄像头区域提示
数据用途
保存期限
删除机制
后台访问权限
操作审计
数据导出/删除策略

未知访客：

只能保存：

snapshot_path

不能自动生成：

Customer identity

不能自动进入 FaceProfile。

后台管理员人工确认后才可以关联。

建议：

UNKNOWN_VISITOR_CAPTURE_ENABLED=false

默认关闭。

============================================================
二十五、P1：迎宾语音不能默认泄露口味隐私
============================================================

当前可能：

欢迎回来，XXX。
今天还是老样子：多辣、多麻？

在公共面馆环境：

旁边顾客可能听到。

增加：

WELCOME_TTS_PRIVACY_MODE

例如：

name_only
safe_preference
full_preference

默认：

name_only

如果用户明确授权：

才允许：

preference TTS。

后台可配置。

============================================================
二十六、P1：智能口味算法重新设计
============================================================

保留现有：

近期
数量
午餐/晚餐
周末
季节
天气
同星期

但是增加：

distinct_order_count
quantity_count
weighted_count
confidence

不要再让：

1 个订单 × quantity=2

直接满足：

MIN_ORDER_COUNT=2。

正确：

distinct_order_count >= 2

才认为有最小历史证据。

推荐模型：

base_score

+
time_decay

+
weekday_affinity

+
meal_period_affinity

+
season_multiplier

+
weather_multiplier

最终：

final_score

建议时间衰减使用实际时间：

days_since_order

而不是只依赖 idx。

例如：

weight = exp(-lambda * days_since_order)

或者：

half_life。

配置化：

PREFERENCE_HALF_LIFE_DAYS

============================================================
二十七、智能口味要真正统计星期
============================================================

为每个：

customer + option_code

统计：

weekday 0~6

meal_period：

BREAKFAST
LUNCH
AFTERNOON
DINNER
LATE_NIGHT

得到：

weekday_score
meal_score

不要只是：

last_used_at.weekday == now.weekday

就 ×1.05。

必须基于历史频率。

============================================================
二十八、智能预选必须避免互斥规格冲突
============================================================

对于：

SINGLE

只能选择一个最高置信度 option。

对于：

MULTIPLE

才允许多个。

例如历史：

多辣 8 次
少辣 1 次

选择：

多辣

而不是：

多辣 + 少辣。

如果历史：

多辣 5
少辣 5

不要自动预选，应该：

提示用户手动选择。

设置：

AUTO_APPLY_MIN_CONFIDENCE

AUTO_APPLY_MIN_MARGIN

例如：

最高分必须比第二名高至少 15%。

============================================================
二十九、账号合并后重新计算智能偏好
============================================================

CustomerMergeService.merge()

当前不能简单：

source score vs target score

而应该：

完成订单迁移
→ 合并 source + target
→ PreferenceService.recompute_for_customer(target_id)

保证：

新顾客历史数据重新计算。

============================================================
三十、P0：支付成功副作用必须 DB Outbox 化
============================================================

不要仅依赖 Redis list。

支付成功后：

同一个 DB transaction：

Payment PAID
Order PAID
FinancialLedger
OutboxEvent

一起提交。

OutboxEvent：

PENDING
PROCESSING
DONE
FAILED

支持：

attempt_count
next_retry_at
locked_at
locked_by

Worker：

自动恢复 stale PROCESSING。

副作用：

积分
口味重算
打印
通知
订阅消息

全部从 Outbox 消费。

不能因为 Redis 暂时不可用而丢积分。

============================================================
三十一、P1：PrintJob 增加 PROCESSING 崩溃恢复
============================================================

当前：

PRINTING

可能永久卡死。

增加：

locked_at
stale timeout

例如：

PRINTING 超过 5 分钟：

→ RETRYING

然后：

指数退避。

同一 print_job：

必须避免重复出票。

注意：

打印机属于“非幂等外部设备”。

所以要尽量：

content_hash
ticket_type
order_id

形成业务幂等。

如果无法确定是否已经出票：

状态应该：

UNKNOWN

由人工/设备状态确认。

不能简单再次出票造成双票。

============================================================
三十二、P1：门店打印设置必须真正支持 store override
============================================================

print_order():

必须先获取：

order.store_id

然后：

effective_bool(
    "xpyun_enabled",
    ...,
    store_id=order.store_id
)

所有应该支持门店覆盖的设置都检查一遍。

逐项审计：

xpyun_enabled
printer_mode
printer_host
printer_port
xpyun_print_copies
xpyun_voice
kitchen_ticket_order_no

============================================================
三十三、P1：数据库增加必要普通索引
============================================================

新增 Alembic migration。

重点：

orders:
INDEX(customer_id, payment_status, paid_at, id)
INDEX(status, payment_status, created_at, id)
INDEX(store_id, status, created_at, id)

payments:
INDEX(order_id, status, id)
INDEX(customer_id, created_at, id)

payment_refunds:
INDEX(payment_id, status, id)

webhook_inbox:
INDEX(status, next_retry_at, received_at, id)
INDEX(status, locked_at, id)

print_jobs:
INDEX(status, next_retry_at, id)
INDEX(status, locked_at, id)

visitor_events:
INDEX(camera_id, event_time, id)
INDEX(expires_at)

welcome_logs:
INDEX(customer_id, created_at)
INDEX(camera_id, created_at)

order_status_history:
INDEX(order_id, created_at)

payment_status_history:
INDEX(payment_id, created_at)

point_ledger:
INDEX(customer_id, created_at)
INDEX(order_id, entry_type)
INDEX(refund_id, entry_type)

dining_sessions:
INDEX(customer_id, status, expires_at)

operation_logs:
INDEX(user_id, created_at)
INDEX(created_at)

delivery_orders:
INDEX(store_id, delivery_status, created_at)
INDEX(last_sync_at)

检查实际 SQL 查询后再决定最终索引，不要盲目添加重复索引。

必须使用 EXPLAIN 验证关键 SQL。

============================================================
三十四、P1：上传对象生命周期
============================================================

头像：

旧头像不能立即删除，必须确认没有引用。

商品：

修改/删除主图后：

旧对象进入 orphan cleanup。

实现：

object_storage orphan cleanup worker。

可以：

每日扫描。

保留：

grace period 24~72h。

防止事务失败后误删。

增加：

storage quota

customer/day upload limit

IP/day limit

============================================================
三十五、P1：H5 必须明确产品定位
============================================================

现在 H5 production 不允许伪造微信登录。

不要偷偷绕过。

如果产品要求：

H5 可以完整点餐

则实现真正 H5 customer session：

例如：

signed session cookie
或
短期 anonymous customer token

但必须考虑：

顾客身份
订单
支付
微信支付
支付宝支付
二维码支付

如果 H5 只是：

“一码通扫码 → 引导微信/支付宝小程序”

那么：

明确 H5 = Scan Landing

不要把：

/order

伪装成完整 production customer client。

请根据当前项目实际产品定位做出合理实现。

不能通过 fake code 恢复生产 H5 登录。

============================================================
三十六、P1：修复 H5 Customer Token 401 清理
============================================================

frontend/src/api/index.ts

当前 401：

clearStaffToken()

必须判断：

customer API
→ clearCustomerToken()

staff API
→ clearStaffToken()

避免 customer token 过期后无限 401。

============================================================
三十七、P1：关键安全配置不能被运行时业务设置削弱
============================================================

以下属于 security infrastructure：

WEBHOOK_IP_ALLOWLIST
WEBHOOK_REQUIRE_ALLOWLIST
TRUSTED_PROXY_IPS

生产环境：

不能允许普通 business setting 覆盖成：

空
*
0.0.0.0/0

建议：

安全设置只允许：

环境变量
Secret Manager
部署配置

如果保留后台修改：

必须：

SUPER_ADMIN only
二次确认
审计
生产最小权限
严格格式校验
禁止空白导致 fail-open

Webhook production：

没有 allowlist：

必须拒绝。

不能：

“告警但放行”。

============================================================
三十八、Trusted Proxy 必须严格验证
============================================================

当前：

X-Forwarded-For

不要简单取最左边。

必须根据：

trusted proxy chain

从右往左解析可信链。

例如：

client, proxy1, proxy2

只有：

proxy2 trusted
proxy1 trusted

才允许继续。

否则直接使用：

request.client.host

防止：

X-Forwarded-For spoofing。

============================================================
三十九、生产环境禁止 Mock fail-open
============================================================

所有 mock：

payment
auth
delivery
print
face fallback

必须：

APP_ENV == development
AND
对应 ALLOW_*_MOCK=true

绝不能：

“非 production 就可以”。

尤其：

staging

默认：

mock OFF。

如果真实 provider 未配置：

应该明确返回：

配置错误

而不是自动 mock。

============================================================
四十、生产 docs / security headers
============================================================

生产关闭：

/docs
/redoc
/openapi.json

或者增加严格权限。

增加：

Strict-Transport-Security
X-Content-Type-Options: nosniff
X-Frame-Options / CSP frame-ancestors
Referrer-Policy
Permissions-Policy
Content-Security-Policy

CSP 不要破坏 Vue/H5。

对 admin：

frame-ancestors 'none'

对 customer H5：

根据实际业务决定。

============================================================
四十一、生产配置 fail-closed
============================================================

当前 Settings 默认：

DATABASE_URL
JWT_SECRET
FACE_ENCRYPTION_KEY

存在开发默认值。

必须保证：

如果：

APP_ENV=production

则：

DATABASE_URL 必须显式配置
REDIS_URL 必须显式配置
JWT_SECRET 必须显式配置
FACE_ENCRYPTION_KEY 必须显式配置
SECRET_ENCRYPTION_KEY 必须显式配置

支付密钥根据启用 provider 检查。

不能让：

APP_ENV 忘记配置

导致公网机器进入 development mock。

建议：

production deployment 必须显式：

APP_ENV=production

如果检测到：

APP_HOST=0.0.0.0

且 APP_ENV 不是 development/local：

打印明确 warning/error。

============================================================
四十二、H5_PUBLIC_BASE_URL 必须严格 URL 验证
============================================================

不能允许：

javascript:
data:
file:
localhost production
127.0.0.1 production

生产必须：

https://

开发可以：

http://localhost
http://LAN-IP

但要严格限制。

============================================================
四十三、Webhook body size limit
============================================================

payment webhook
delivery webhook

不要：

await request.body()

无限制。

增加：

max body size middleware

不同 provider：

PAYMENT_WEBHOOK_MAX_BODY
DELIVERY_WEBHOOK_MAX_BODY

超过：

413

并记录安全事件。

============================================================
四十四、Webhook headers 不要完整保存敏感数据
============================================================

WebhookInbox.headers_json：

不能盲目保存：

Authorization
Cookie
secret
token

必须做 header redaction。

保留：

签名验证所需字段

或者脱敏存储。

============================================================
四十五、外卖 Webhook 必须验证 provider contract
============================================================

美团：

签名
event id
order id
金额
门店
状态

饿了么：

签名
event id
order id
金额
门店
状态

必须测试：

重复 callback
乱序 callback
签名错误
未知订单
已处理订单
退款
取消
确认
完成

并根据官方平台要求确认：

成功响应格式
失败响应格式
是否触发 retry。

不要凭猜测决定 HTTP status。

============================================================
四十六、门店隔离进行全项目审计
============================================================

对所有 API 检查：

SUPER_ADMIN
STORE_ADMIN
CASHIER
KITCHEN
OPERATOR

必须严格：

store_id scope

特别检查：

customers
orders
payments
refunds
coupons
vouchers
products
stocks
tables
cameras
visitor events
welcome logs
delivery
financial ledger
settings

任何：

WHERE id = xxx

都要问：

是否还需要 store_id scope？

不能只依赖：

“前端不会传别的 ID”。

服务器必须自己验证。

============================================================
四十七、订单状态机必须统一
============================================================

建立唯一状态机：

PENDING_PAYMENT
CONFIRMED
PREPARING
COOKING
READY
COMPLETED
CANCELLED
REFUNDED

支付状态：

UNPAID
PARTIAL_PAID
PAID
PARTIAL_REFUND
REFUNDED
CANCELLED

禁止 API 任意：

order.status = PAID

所有状态变化统一经过：

apply_order_status()

所有支付统一：

_finalize_tender()

所有退款统一：

_finalize_refund()

避免不同 API 各自实现一套状态逻辑。

============================================================
四十八、库存一致性
============================================================

订单创建：

必须：

product/store stock row FOR UPDATE

或者：

atomic UPDATE stock = stock - quantity WHERE stock >= quantity

避免：

先 select stock
再 update

导致 oversell。

取消/退款：

必须确保：

同一订单库存只恢复一次。

增加：

stock ledger

或者：

order_stock_transaction

确保：

deduct
restore

严格幂等。

============================================================
四十九、优惠券/代金券一致性
============================================================

状态：

AVAILABLE
RESERVED
USED
EXPIRED

订单取消：

RESERVED → AVAILABLE

支付成功：

RESERVED → USED

必须防：

double spend

并发下单：

同一 coupon 两个订单

只能一个成功。

使用：

FOR UPDATE

+ DB unique/invariant

测试：

100 concurrent order attempts
同一 coupon

最终只能一个订单占用。

============================================================
五十、积分必须完全幂等
============================================================

支付成功：

同一 order 只能：

EARN 一次。

退款：

同一 refund_id 只能：

REFUND_DEDUCT 一次。

不要只依赖：

select then insert。

最好：

DB unique constraint。

检查：

order_id + entry_type
refund_id + entry_type

确保数据库最终一致。

============================================================
五十一、金融账本必须最终一致
============================================================

Payment:

SUCCESS
→ financial ledger RECEIVE

Refund:

SUCCESS
→ financial ledger REFUND

必须：

同一 payment 只能一条 receive ledger。

同一 refund 只能一条 refund ledger。

数据库 UNIQUE 最终兜底。

如果 ledger 写入失败：

支付事务必须回滚到：

“未确认本地成功”

不要：

Payment PAID
但 ledger 丢失。

============================================================
五十二、顾客删除/禁用/合并
============================================================

所有 customer lifecycle：

ACTIVE
DISABLED
MERGED

必须检查：

customer token
face cache
websocket
orders
payments
sessions
coupons
preferences
points

禁用后：

旧 token 必须立即失效。

合并后：

source token 必须失效。

目标 customer token 可以继续。

============================================================
五十三、数据库时间统一
============================================================

当前使用：

Asia/Shanghai

但数据库保存：

DATETIME/local naive

运行时修改：

app_timezone

会导致历史数据解释不一致。

建议：

数据库统一 UTC

业务层统一 timezone-aware datetime

展示：

Asia/Shanghai

如果当前迁移成本过大：

至少：

禁止生产运行时修改 app_timezone。

并在设置页面标记：

“系统时区为部署级配置，修改需要迁移”。

============================================================
五十四、API 参数完整校验
============================================================

补充：

username max length
login code max length
auth_code max length
nickname max length
remark max length
channel enum
provider enum
channel_type enum
table_token max length
phone code max length
image base64 max length

不要让客户端提交无限长字符串。

OrderCreate：

items:
min_length=1
max_length=合理值

例如：

max 50

option_ids：

max 30

remark：

max 500

============================================================
五十五、图片上传统一策略
============================================================

统一：

MAX_IMAGE_BYTES
MAX_IMAGE_PIXELS
MAX_EDGE
MIME
MAGIC

不要让：

extension

成为唯一判断。

GIF：

如果业务不需要动态 GIF：

直接关闭。

更建议：

avatar/product/face：

只允许：

JPEG
PNG
WebP

减少攻击面。

============================================================
五十六、对象存储
============================================================

生产建议：

S3 / MinIO

本地：

仅 development。

如果：

STORAGE_BACKEND=local

production：

至少 warning。

如果业务真正依赖多实例：

local storage 不可作为共享文件系统。

============================================================
五十七、HTTP Client 统一连接池
============================================================

当前很多 provider：

每次：

httpx.AsyncClient()

会造成：

连接池无法充分复用。

尽量统一：

get_http_client()

支持：

timeout
connect timeout
read timeout
pool limits
retry policy

不要对支付 POST 自动 retry。

GET query 可以谨慎 retry。

============================================================
五十八、摄像头 MJPEG Stream 增加资源保护
============================================================

camera stream：

必须：

认证
权限
门店隔离
短时 ticket

已经有部分机制。

继续增加：

per-user stream limit
per-camera stream limit
global stream limit

防止：

一个有 camera:read 权限的账号

打开：

100 个 stream

造成：

CPU OOM。

============================================================
五十九、TTS Worker
============================================================

当前：

asyncio.Queue()

无界。

必须：

Bounded Queue

例如：

MAX_TTS_QUEUE=100

如果满：

可以：

drop oldest

或者：

drop duplicate customer

不能无限占内存。

TTS 必须避免：

同一顾客短时间重复欢迎。

Leader loss：

停止 TTS。

Leader recovery：

重新启动。

============================================================
六十、前端日志
============================================================

继续保留：

client logs

但是确保：

token
phone
secret
authorization
openid
unionid
table_token

都不会被日志保存。

检查：

Error.stack
Error.message
URL
query string

不要泄露：

JWT
手机号
支付密钥
签名。

============================================================
六十一、项目测试体系必须真正建立
============================================================

至少建立：

tests/

分组：

tests/unit/
tests/integration/
tests/security/
tests/concurrency/
tests/payment/
tests/order/
tests/customer/
tests/face/
tests/webhook/

必须覆盖：

1. 登录
2. Token
3. Token version
4. customer identity
5. phone bind
6. account merge
7. order idempotency
8. stock concurrency
9. coupon concurrency
10. payment concurrency
11. payment callback duplicate
12. payment callback crash recovery
13. refund duplicate
14. point duplicate
15. webhook duplicate
16. webhook stale processing
17. print stale processing
18. leader lock race
19. camera leader
20. face concurrent registration
21. face disabled customer
22. image bomb
23. SSRF
24. store isolation
25. RBAC
26. H5 customer token
27. frontend build

============================================================
六十二、必须做并发测试
============================================================

至少：

100 concurrent

测试：

同一个订单创建
同一个 idempotency key

结果：

1 个订单。

同一个 coupon：

100 requests

结果：

1 个 RESERVED。

同一个库存：

100 requests

结果：

库存不能负数。

同一个支付：

100 create payment

结果：

不能产生多笔并发有效支付。

同一个 callback：

100 callbacks

结果：

只入账一次。

同一个 refund：

100 refund callbacks

结果：

只退款一次。

同一个 face consent：

100 requests

结果：

AUTHORIZED profile = 1。

Leader：

100 workers

结果：

同一时刻只有一个 leader。

============================================================
六十三、前端构建检查
============================================================

必须运行：

frontend npm install / npm ci

然后：

npm run build

修复所有：

TypeScript
Vue
Vite
dependency
build

问题。

============================================================
六十四、小程序检查
============================================================

微信：

所有 JS 做 syntax check。

检查：

app.json
pages
subPackages
privacy
camera
request
payment

支付宝：

同样检查。

确保：

所有实际调用 API 的页面都存在。

尤其：

face consent

必须确认前端真实调用。

============================================================
六十五、不要留下重复/废弃代码
============================================================

扫描：

duplicate page
legacy API
dead function
dead import
deprecated route
old auth flow
fake H5 login
old payment implementation

如果存在旧实现：

判断是否还在被引用。

如果完全没有引用：

删除。

如果为了兼容：

明确标记 deprecated。

不要保留两套互相漂移的业务逻辑。

============================================================
六十六、数据库 migration
============================================================

任何 schema 修改：

必须新增 Alembic migration。

不能：

直接修改 baseline

不能：

让 production 数据库必须重新初始化。

Migration 必须：

可升级
可回滚
尽量在线
大表 index 创建考虑锁表风险。

新增字段：

先 nullable
数据回填
再 NOT NULL

根据 MySQL 实际能力实现。

============================================================
六十七、生产部署文件
============================================================

补充：

.env.example

README.md

scripts/init_admin.py

scripts/check_production.py

scripts/backup_db.sh 或 Windows 等价说明

必要的：

systemd / supervisor / gunicorn

Nginx：

/api
/uploads
WebSocket

HTTPS

HSTS

静态前端

都必须有完整说明。

不要把真实密钥写进仓库。

============================================================
六十八、生产配置必须 fail-closed
============================================================

APP_ENV=production：

必须：

JWT_SECRET
FACE_ENCRYPTION_KEY
SECRET_ENCRYPTION_KEY
DATABASE_URL
REDIS_URL

全部显式配置。

支付 provider：

如果启用：

必须配置完整。

Webhook：

必须有 IP allowlist。

Mock：

全部 false。

OpenCV face fallback：

false。

Rate limit：

true。

APP_DEBUG：

false。

Mini program env：

release。

H5：

https。

============================================================
六十九、不要改变核心业务体验
============================================================

保留：

第一次扫码：

不强制注册。

后台：

自动创建：

Customer GUEST。

用户：

可以直接点餐。

会员：

主动开通。

手机号：

官方授权后可以绑定。

如果手机号已经属于另一个 Customer：

提示：

“该手机号已关联其他账号，是否合并历史订单、积分、优惠券？”

用户确认后：

完整合并。

人脸：

必须明确授权。

人脸默认关闭。

============================================================
七十、最终验收标准
============================================================

完成后必须做到：

A. 安全：

没有 P0。
P1 必须全部修复或者给出明确不可修复原因。

B. 支付：

不能重复入账。
不能金额篡改。
不能回调丢失。
不能退款重复。
不能支付成功但财务账本缺失。

C. 订单：

不能重复订单。
不能跨桌复用错误订单。
不能跨门店。
不能库存负数。
不能优惠券双花。

D. 顾客：

GUEST / MEMBER 逻辑正确。
微信/支付宝身份稳定。
手机号验证严格。
账号合并安全。

E. 人脸：

只有明确授权。
只有符合条件的会员。
禁用账户不能继续识别。
并发注册不会出现多个 AUTHORIZED。
未知人脸默认不偷偷保存。
Basic anti-spoof 明确标注。

F. 摄像头：

多 worker 不重复连接。
Leader loss 能清理。
Leader switch 能恢复。

G. Webhook：

重复安全。
乱序安全。
崩溃可恢复。

H. Print：

崩溃可恢复。
不会永久 PROCESSING。

I. 性能：

关键 SQL 有合理索引。
没有明显 N+1。
没有无界队列。
没有无限 body。
没有无限 upload。

J. 前端：

npm build 成功。
401 customer/staff token 处理正确。

K. 小程序：

微信 JS 检查通过。
支付宝 JS 检查通过。
支付流程完整。
face consent 真正可用。

============================================================
七十一、执行方式
============================================================

严格按照以下顺序：

STEP 1：
扫描整个仓库。

STEP 2：
输出内部审计清单：

P0
P1
P2
P3

STEP 3：
先修 P0。

STEP 4：
运行对应测试。

STEP 5：
修 P1。

STEP 6：
新增数据库 migration。

STEP 7：
修前端。

STEP 8：
修微信小程序。

STEP 9：
修支付宝小程序。

STEP 10：
运行完整测试。

STEP 11：
运行 build。

STEP 12：
检查 git diff。

STEP 13：
检查是否有：

secret
token
password
private key
mock
debug
TODO
dead code

STEP 14：
再次进行全仓库安全扫描。

STEP 15：
最终输出：

1. 修改了哪些文件。
2. 每个文件为什么修改。
3. P0 修复清单。
4. P1 修复清单。
5. 数据库 migration。
6. 新增测试。
7. 测试结果。
8. npm build 结果。
9. 小程序检查结果。
10. 是否仍存在风险。
11. 生产部署注意事项。
12. 最终上线检查表。

============================================================
七十二、最重要的执行纪律
============================================================

不要：

只告诉我问题。

我要：

你直接修改代码。

不要：

只生成 TODO。

不要：

只修改 README。

不要：

为了通过测试删除业务功能。

不要：

把生产安全改弱。

不要：

用 fake data 绕过支付。

不要：

用 fake phone 绕过支付宝验证。

不要：

用 fake face 绕过人脸授权。

不要：

用 mock 绕过生产 provider。

不要：

把 Redis 当最终金融数据。

不要：

把内存队列当最终业务数据。

所有最终业务状态必须由：

MySQL / PostgreSQL 等持久化数据库

作为最终事实来源。

Redis：

只作为：

cache
lock
rate limit
short-lived state

不能作为：

最终订单事实
最终支付事实
最终积分事实
最终退款事实。

完成全部修改后，再从：

攻击者视角
顾客视角
收银员视角
厨房视角
管理员视角
多门店视角
支付平台视角
摄像头 Worker 视角
数据库故障视角
Redis 故障视角
网络超时视角
进程崩溃视角

重新审查整个项目。

最终目标：

不是“代码能运行”。

而是：

“这个面馆系统可以在真实门店长期运行，并且在高并发、网络异常、支付回调重复、进程崩溃、Redis 故障、数据库异常、多 Worker、多门店、恶意客户端情况下仍然保证安全、正确、可恢复、可审计。”
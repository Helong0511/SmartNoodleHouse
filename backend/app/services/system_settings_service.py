import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models import SystemSetting, VisitorEvent

logger = logging.getLogger(__name__)

# 可在后台在线修改的业务参数（持久化到 system_settings 表，运行时优先于 .env 默认值）
# 类型：int / float / bool / string / enum（enum 须带 choices）
# group：前端「门店信息 / 系统设置」分页分类

BUSINESS_SETTING_GROUPS: list[dict[str, str]] = [
    {"id": "order", "label": "订单与厨房", "description": "写入当前门店：支付超时、厨房自动出餐、大厅叫号屏"},
    {"id": "welcome", "label": "迎宾与人脸", "description": "会员迎宾语/陌生客欢迎语与开关（可按店）、摄像头播报、冷却、人脸匹配；访客抓拍/保留天数为平台级"},
    {"id": "member", "label": "会员与优惠", "description": "写入当前门店：新客赠券、生日积分倍数等会员权益"},
    {"id": "miniprogram", "label": "小程序与 H5", "description": "点餐页展示写入当前门店；小程序主体隐私、域名与版本为平台级（系统设置）"},
    {"id": "delivery", "label": "外卖", "description": "自动接单写入当前门店；.env 兜底门店为平台级"},
    {"id": "print", "label": "厨房打印", "description": "写入当前门店：开关、模式、打印机地址、份数与票面"},
    {"id": "security", "label": "安全与网络", "description": "平台级：Token 有效期、Webhook 白名单、可信反代"},
    {
        "id": "system",
        "label": "系统通用",
        "description": "口味场景/天气加权按门店（需经纬度）；时区、夜间批处理、上传限制与日志保留为平台级",
    },
]

# 全部门店共用，只能由系统管理权限改 system_settings
PLATFORM_ONLY_KEYS = frozenset({
    "visitor_data_retention_days",
    "operation_log_retention_days",
    "unknown_visitor_capture_enabled",
    "enable_alipay_avatar",
    "enable_alipay_nickname",
    "enable_alipay_phone_bind",
    "enable_wx_avatar",
    "enable_wx_nickname",
    "enable_wx_phone_bind",
    "enable_face_welcome",
    "max_upload_size_mb",
    "jwt_expire_minutes",
    "customer_jwt_expire_minutes",
    "webhook_ip_allowlist",
    "webhook_require_allowlist",
    "trusted_proxy_ips",
    "h5_public_base_url",
    "dev_lan_host",
    "mini_program_env_version",
    "wechat_subscribe_template_order_ready",
    "wechat_subscribe_template_balance_low",
    "alipay_subscribe_template_order_ready",
    "alipay_subscribe_template_balance_low",
    "allowed_image_extensions",
    "app_timezone",
    "delivery_env_fallback_store_id",
    "night_batch_enabled",
    "night_batch_window_start_hour",
    "night_batch_window_end_hour",
    "night_batch_sales_lookback_days",
    "night_batch_hot_top_n",
    "night_batch_recommend_top_n",
    "night_batch_taste_customer_limit",
    "night_batch_taste_lookback_days",
    "night_batch_category_sort_enabled",
    "night_batch_category_sort_lookback_days",
    "night_batch_category_sort_soft_qty",
})

BUSINESS_SETTING_META: dict[str, dict[str, Any]] = {
    "order_payment_timeout_minutes": {
        "group": "order",
        "label": "订单支付超时",
        "unit": "分钟",
        "type": "int",
        "min": 1,
        "max": 1440,
        "description": "未支付订单自动关闭时间",
    },
    "kitchen_auto_complete_minutes": {
        "group": "order",
        "label": "厨房自动出餐",
        "unit": "分钟",
        "type": "int",
        "min": 0,
        "max": 1440,
        "description": "支付后厨房 KDS 未处理超过该时间则自动完成出餐；0 表示关闭",
    },
    "enable_customer_display": {
        "group": "order",
        "label": "大厅叫号屏",
        "unit": "",
        "type": "bool",
        "default": False,
        "store_override": True,
        "description": "开启后可用 /display 顾客叫号屏；关闭时 API/推送不可用；无屏门店可关；可按门店覆盖",
    },
    "display_screen_token": {
        "group": "order",
        "label": "叫号屏访问令牌",
        "unit": "",
        "type": "string",
        "max_length": 128,
        "store_override": True,
        "description": "本店叫号屏 URL 的 token 参数；留空则沿用平台 .env 的 DISPLAY_SCREEN_TOKEN；生产环境建议各店独立",
    },
    "visitor_data_retention_days": {
        "group": "welcome",
        "label": "访客数据保留",
        "unit": "天",
        "type": "int",
        "min": 1,
        "max": 365,
        "description": "匿名访客事件保留天数；缩短时会同步压缩已有记录的过期时间",
    },
    "operation_log_retention_days": {
        "group": "system",
        "label": "操作日志保留",
        "unit": "天",
        "type": "int",
        "min": 7,
        "max": 3650,
        "description": "后台操作日志自动清理周期",
    },
    "welcome_greeting_prefix": {
        "group": "welcome",
        "label": "迎宾语模板",
        "unit": "",
        "type": "string",
        "max_length": 200,
        "default": "欢迎回来，{称呼}。",
        "store_override": True,
        "description": "人脸迎宾主句，可按门店覆盖。占位符：{称呼}{会员等级}{店名}{口味}{餐品}（英文 name/level/store/taste/product）。例：尊贵的 {称呼} {会员等级}，欢迎您再次光临{店名}",
    },
    "welcome_preference_template": {
        "group": "welcome",
        "label": "口味偏好附句",
        "unit": "",
        "type": "string",
        "max_length": 120,
        "default": "今天还是老样子：{口味}？",
        "store_override": True,
        "description": "有口味爱好且主模板未写 {口味} 时追加；{口味} 来自顾客规格偏好 Top2。留空则用默认「今天还是老样子：{口味}？」",
    },
    "welcome_product_template": {
        "group": "welcome",
        "label": "餐品推荐附句",
        "unit": "",
        "type": "string",
        "max_length": 120,
        "default": "",
        "store_override": True,
        "description": "有智能推荐餐品且主模板未写 {餐品} 时追加；{餐品} 为推荐引擎 Top2 菜名。默认留空不追加（可在主模板直接写 {餐品}）",
    },
    "welcome_cooldown_seconds": {
        "group": "welcome",
        "label": "迎宾冷却时间",
        "unit": "秒",
        "type": "int",
        "min": 0,
        "max": 3600,
        "description": "同一顾客两次迎宾的最小间隔",
    },
    "face_match_threshold": {
        "group": "welcome",
        "label": "人脸匹配阈值",
        "unit": "",
        "type": "float",
        "min": 0.35,
        "max": 0.99,
        "step": 0.01,
        "description": "余弦相似度达到该值才视为同一人；InsightFace 门禁常用 0.45–0.50，过高（如 0.75）会大量认不出",
    },
    "face_confirm_frames": {
        "group": "welcome",
        "label": "人脸确认帧数",
        "unit": "帧",
        "type": "int",
        "min": 1,
        "max": 30,
        "description": "连续 N 帧匹配成功才触发迎宾（推荐 2；过大则反应慢）",
    },
    "unknown_visitor_capture_enabled": {
        "group": "welcome",
        "label": "未知访客抓拍",
        "unit": "",
        "type": "bool",
        "default": True,
        "store_override": False,
        "description": "店内摄像头识别到未登记人脸时抓拍存图；路径 visitors/年/月/日/，按访客保留天数自动清理",
    },
    "unknown_visitor_welcome_enabled": {
        "group": "welcome",
        "label": "陌生访客迎宾播报",
        "unit": "",
        "type": "bool",
        "default": True,
        "store_override": True,
        "description": "未匹配到授权会员人脸时是否播报欢迎语（与会员迎宾独立）。可按门店开关；播报走 TTS/摄像头扬声器同一通道",
    },
    "unknown_visitor_welcome_text": {
        "group": "welcome",
        "label": "陌生访客欢迎语",
        "unit": "",
        "type": "string",
        "max_length": 120,
        "default": "欢迎光临",
        "store_override": True,
        "description": "陌生访客播报文案，可按门店覆盖。不支持会员占位符，建议简短口语",
    },
    "unknown_visitor_welcome_cooldown_seconds": {
        "group": "welcome",
        "label": "陌生访客迎宾冷却",
        "unit": "秒",
        "type": "int",
        "min": 0,
        "max": 3600,
        "default": 60,
        "store_override": True,
        "description": "同一摄像头两次陌生客迎宾的最小间隔，避免反复播报",
    },
    "camera_speaker_tts_enabled": {
        "group": "welcome",
        "label": "摄像头播放迎宾语音",
        "unit": "",
        "type": "bool",
        "default": True,
        "store_override": True,
        "description": "后台开关，改完即时生效（不写 .env）。开启后会员/访客迎宾 TTS 经海康对讲推到触发识别的摄像头扬声器（须对讲型机）；关闭则本机播报；推流失败自动回退本机。可按门店覆盖。已有库若仍为 false 请在业务参数中打开",
    },
    "camera_speaker_volume_percent": {
        "group": "welcome",
        "label": "摄像头播报音量",
        "unit": "%",
        "type": "int",
        "min": 0,
        "max": 100,
        "default": 80,
        "store_override": True,
        "description": "写入海康对讲通道 speakerVolume（0 静音～100 最大）。播报前自动下发；设备无该字段时才回退软件增益。可按门店覆盖",
    },
    "delivery_auto_confirm": {
        "group": "delivery",
        "label": "外卖自动接单",
        "unit": "",
        "type": "bool",
        "store_override": True,
        "description": "新外卖单是否自动向平台 confirm；关闭则本地 PENDING，需商家确认；可按门店覆盖",
    },
    "welcome_coupon_name": {
        "group": "member",
        "label": "新客赠券模板名",
        "unit": "",
        "type": "string",
        "max_length": 100,
        "store_override": True,
        "description": "从本店或总部已启用的券模板中选择；各店可不同；留空则本店不发新客券",
    },
    "welcome_bundle_template_ids": {
        "group": "member",
        "label": "新人礼包券模板",
        "unit": "",
        "type": "string",
        "max_length": 500,
        "store_override": True,
        "description": "多选本店/总部已启用券模板；非空时按所选发多张欢迎券（幂等）；空则回退「新客赠券模板名」",
    },
    "birthday_benefit_mode": {
        "group": "member",
        "label": "生日优惠方式",
        "unit": "",
        "type": "enum",
        "choices": ["off", "points", "coupon", "points_and_coupon"],
        "default": "points_and_coupon",
        "store_override": True,
        "description": "生日当天权益：关闭 / 仅积分倍数 / 仅赠券 / 积分+赠券；默认积分翻倍+生日免费加蛋券；可按门店覆盖",
    },
    "birthday_points_multiplier": {
        "group": "member",
        "label": "生日积分倍数",
        "unit": "倍",
        "type": "int",
        "min": 1,
        "max": 10,
        "default": 2,
        "store_override": True,
        "description": "方式含「积分」时生效；相对「实付 1 元 = 1 分」的倍数，默认 2",
    },
    "birthday_coupon_name": {
        "group": "member",
        "label": "生日赠券模板名",
        "unit": "",
        "type": "string",
        "max_length": 100,
        "store_override": True,
        "description": "方式含「赠券」时，从本店或总部已启用券模板中选择；留空则不发生日券",
    },
    "sleep_wake_coupon_name_30": {
        "group": "member",
        "label": "沉睡唤醒券(30–90天)",
        "unit": "",
        "type": "string",
        "max_length": 100,
        "store_override": True,
        "description": "从本店或总部已启用券模板中选择；距上次消费 30–90 天唤醒；留空关闭该档",
    },
    "sleep_wake_coupon_name_90": {
        "group": "member",
        "label": "沉睡唤醒券(≥90天)",
        "unit": "",
        "type": "string",
        "max_length": 100,
        "store_override": True,
        "description": "从本店或总部已启用券模板中选择；距上次消费 ≥90 天唤醒；留空关闭该档",
    },
    "balance_low_threshold": {
        "group": "member",
        "label": "储值余额提醒阈值",
        "unit": "元",
        "type": "number",
        "min": 0,
        "max": 10000,
        "default": 20,
        "store_override": True,
        "description": "余额低于该值触发提醒；0=关闭",
    },
    "enable_social_benefit": {
        "group": "member",
        "label": "社会贡献公益",
        "unit": "",
        "type": "bool",
        "default": True,
        "store_override": True,
        "description": "开启后 POS/小程序可核验公益身份并按规则打折",
    },
    "enable_marketing_campaigns": {
        "group": "member",
        "label": "营销活动",
        "unit": "",
        "type": "bool",
        "default": True,
        "store_override": True,
        "description": "关闭后本店不可新建/发布营销活动，推荐加权也不再生效；总部活动仍可由超管在「总部」范围发布",
    },
    "marketing_weekly_cap": {
        "group": "member",
        "label": "营销周发券上限",
        "unit": "次/人/周",
        "type": "int",
        "min": 0,
        "max": 50,
        "default": 3,
        "store_override": True,
        "description": "营销活动 ISSUE_COUPON 每人每周上限；0=不限制；活动可单独覆盖",
    },
    "attribution_window_hours": {
        "group": "member",
        "label": "营销归因窗口",
        "unit": "小时",
        "type": "int",
        "min": 1,
        "max": 720,
        "default": 48,
        "store_override": False,
        "description": "无券归因时，成功推送后多少小时内下单仍算活动贡献",
    },
    "points_exchange_enabled": {
        "group": "member",
        "label": "积分兑换",
        "unit": "",
        "type": "bool",
        "default": False,
        "store_override": True,
        "description": "开启后会员可在小程序用积分兑换优惠券；关闭则隐藏兑换入口",
    },
    "points_expire_days": {
        "group": "member",
        "label": "积分过期天数",
        "unit": "天",
        "type": "int",
        "min": 0,
        "max": 3650,
        "default": 365,
        "store_override": False,
        "description": "距上次消费超过该天数的活跃会员积分整体清零；0=永不过期",
    },
    "lifecycle_thresholds_json": {
        "group": "member",
        "label": "生命周期阈值",
        "unit": "",
        "type": "string",
        "max_length": 500,
        "default": '{"new_days":7,"active_days":30,"at_risk_days":90,"dormant_days":120}',
        "store_override": False,
        "description": "JSON：new/active/at_risk/dormant 天数阈值，供夜批与筛选",
    },
    "rfm_thresholds_json": {
        "group": "member",
        "label": "RFM 分档阈值",
        "unit": "",
        "type": "string",
        "max_length": 500,
        "default": '{"r_days":[7,30,60,90],"f_orders":[1,2,4,8],"m_amount":[30,80,150,300]}',
        "store_override": False,
        "description": "JSON：R 沉默天 / F 近90天单量 / M 近90天实付 的四档分界",
    },
    "love_day_soy_template_name": {
        "group": "member",
        "label": "爱心日豆浆券模板",
        "unit": "",
        "type": "string",
        "max_length": 100,
        "default": "",
        "store_override": True,
        "description": "从本店或总部已启用券模板中选择；空=关闭；配置后每周爱心日给 ACTIVE 公益身份发券（幂等 love_day:）",
    },
    "love_day_weekday": {
        "group": "member",
        "label": "爱心日星期",
        "unit": "0=周一…6=周日",
        "type": "int",
        "min": 0,
        "max": 6,
        "default": 0,
        "store_override": True,
        "description": "与 love_day_soy_template_name 联用；默认周一",
    },
    "wechat_subscribe_template_balance_low": {
        "group": "miniprogram",
        "label": "微信余额提醒订阅模板ID",
        "unit": "",
        "type": "string",
        "max_length": 120,
        "default": "",
        "store_override": False,
        "description": "空=仅记日志不推送；配置后 run_balance_low_remind 真正发订阅（平台级）",
    },
    "alipay_subscribe_template_order_ready": {
        "group": "miniprogram",
        "label": "支付宝出餐订阅模板",
        "unit": "",
        "type": "string",
        "max_length": 128,
        "default": "",
        "store_override": False,
        "description": "支付宝小程序订阅消息模板 ID；空则不下发授权；与微信模板 ID 禁止混用",
    },
    "alipay_subscribe_template_balance_low": {
        "group": "miniprogram",
        "label": "支付宝余额提醒订阅模板",
        "unit": "",
        "type": "string",
        "max_length": 128,
        "default": "",
        "store_override": False,
        "description": "支付宝余额偏低订阅模板 ID；空则仅记日志；与微信模板 ID 禁止混用",
    },
    "queue_enabled": {
        "group": "miniprogram",
        "label": "取餐排队码",
        "unit": "",
        "type": "bool",
        "default": False,
        "store_override": True,
        "description": "经 /miniprogram/config 下发；开启后顾客可查排队；可按门店覆盖",
    },
    "quick_remark_options": {
        "group": "miniprogram",
        "label": "结算快捷备注",
        "unit": "",
        "type": "string",
        "max_length": 500,
        "default": "不要香菜,少油少盐,不要辣,微辣,多加醋,加份米饭",
        "store_override": True,
        "description": "逗号分隔；经 /miniprogram/config 下发到结算页；可按门店覆盖",
    },
    "pay_success_coupon_name": {
        "group": "miniprogram",
        "label": "支付成功领券模板名",
        "unit": "",
        "type": "string",
        "max_length": 100,
        "default": "",
        "store_override": True,
        "description": "结果页「支付成功领券」匹配的券模板名；空=关闭；可按门店覆盖",
    },
    "enable_alipay_avatar": {
        "group": "miniprogram",
        "label": "支付宝头像能力",
        "unit": "",
        "type": "bool",
        "store_override": False,
        "description": "经小程序 /miniprogram/config 下发；个人主体请关闭（小程序主体级，全店共用）",
    },
    "enable_alipay_nickname": {
        "group": "miniprogram",
        "label": "支付宝昵称能力",
        "unit": "",
        "type": "bool",
        "store_override": False,
        "description": "经小程序 /miniprogram/config 下发（小程序主体级，全店共用）",
    },
    "enable_alipay_phone_bind": {
        "group": "miniprogram",
        "label": "支付宝手机号绑定",
        "unit": "",
        "type": "bool",
        "store_override": False,
        "description": "个人主体小程序通常须关闭（小程序主体级，全店共用）",
    },
    "enable_wx_avatar": {
        "group": "miniprogram",
        "label": "微信头像能力",
        "unit": "",
        "type": "bool",
        "store_override": False,
        "description": "经小程序 /miniprogram/config 下发（小程序主体级，全店共用）",
    },
    "enable_wx_nickname": {
        "group": "miniprogram",
        "label": "微信昵称能力",
        "unit": "",
        "type": "bool",
        "store_override": False,
        "description": "经小程序 /miniprogram/config 下发（小程序主体级，全店共用）",
    },
    "enable_wx_phone_bind": {
        "group": "miniprogram",
        "label": "微信手机号绑定",
        "unit": "",
        "type": "bool",
        "store_override": False,
        "description": "个人主体小程序通常须关闭（小程序主体级，全店共用）",
    },
    "enable_face_welcome": {
        "group": "welcome",
        "label": "人脸迎宾入口",
        "unit": "",
        "type": "bool",
        "default": False,
        "store_override": False,
        "description": "会员中心是否显示「人脸迎宾」开关；默认关闭。开启后顾客可挑战+拍照注册特征（平台级，全店共用）",
    },
    "show_menu_search": {
        "group": "miniprogram",
        "label": "点餐页·搜索菜品",
        "unit": "",
        "type": "bool",
        "default": True,
        "store_override": True,
        "description": "顾客端点餐页是否显示搜索框；可按门店覆盖",
    },
    "show_menu_smart_taste": {
        "group": "miniprogram",
        "label": "点餐页·智能口味",
        "unit": "",
        "type": "bool",
        "default": True,
        "store_override": True,
        "description": "顾客端点餐页是否显示智能口味条；可按门店覆盖",
    },
    "show_menu_guess_you_like": {
        "group": "miniprogram",
        "label": "点餐页·猜你喜欢",
        "unit": "",
        "type": "bool",
        "default": True,
        "store_override": True,
        "description": "顾客端点餐页是否显示猜你喜欢推荐条；可按门店覆盖",
    },
    "hide_member_empty_wallet": {
        "group": "miniprogram",
        "label": "会员中心·无券隐藏券区",
        "unit": "",
        "type": "bool",
        "default": True,
        "store_override": True,
        "description": "开启后无可用优惠券/代金券时不显示对应区块；可按门店覆盖",
    },
    "invoice_enabled": {
        "group": "miniprogram",
        "label": "电子发票",
        "unit": "",
        "type": "bool",
        "default": False,
        "store_override": True,
        "description": "开启后本店顾客可对已支付订单申请开票（结果页入口 + 小程序 API）；可按门店覆盖，默认关闭",
    },
    "shared_cart_enabled": {
        "group": "miniprogram",
        "label": "同桌共享购物车",
        "unit": "",
        "type": "bool",
        "default": False,
        "store_override": True,
        "description": "开启后堂食同桌多人共用一份服务端购物车草稿；关闭则每人仍用本机购物车；可按门店覆盖，默认关闭",
    },
    "kitchen_ticket_order_no": {
        "group": "print",
        "label": "厨房票订单号样式",
        "unit": "",
        "type": "enum",
        "choices": ["barcode", "text", "off"],
        "store_override": True,
        "description": "barcode=条码+单号；text=仅单号；off=不打印；可按门店覆盖",
    },
    "xpyun_print_copies": {
        "group": "print",
        "label": "小票打印份数",
        "unit": "份",
        "type": "int",
        "min": 1,
        "max": 5,
        "store_override": True,
        "description": "局域网/云打印出票份数；可按门店覆盖",
    },
    "xpyun_voice": {
        "group": "print",
        "label": "云打印语音",
        "unit": "",
        "type": "int",
        "min": 0,
        "max": 3,
        "store_override": True,
        "description": "仅芯烨云：0取消单 1静音 2来单 3退单；可按门店覆盖",
    },
    "max_upload_size_mb": {
        "group": "system",
        "label": "上传大小上限",
        "unit": "MB",
        "type": "int",
        "min": 1,
        "max": 50,
        "description": "单文件上传上限（餐品图/头像等）；改后对新上传生效",
    },
    "jwt_expire_minutes": {
        "group": "security",
        "label": "员工 Token 有效期",
        "unit": "分钟",
        "type": "int",
        "min": 30,
        "max": 10080,
        "description": "仅影响新签发的员工 JWT",
    },
    "customer_jwt_expire_minutes": {
        "group": "security",
        "label": "顾客 Token 有效期",
        "unit": "分钟",
        "type": "int",
        "min": 30,
        "max": 10080,
        "description": "仅影响新签发的顾客 JWT",
    },
    "webhook_ip_allowlist": {
        "group": "security",
        "label": "Webhook IP 白名单",
        "unit": "",
        "type": "string",
        "max_length": 2000,
        "description": "支付/外卖回调允许的 IP/CIDR，逗号分隔；空=不限制",
    },
    "webhook_require_allowlist": {
        "group": "security",
        "label": "强制 Webhook 白名单",
        "unit": "",
        "type": "bool",
        "store_override": False,
        "description": "生产环境白名单为空时是否直接拒绝回调（全局安全策略，不可按门店覆盖）",
    },
    "trusted_proxy_ips": {
        "group": "security",
        "label": "可信反代 IP",
        "unit": "",
        "type": "string",
        "max_length": 2000,
        "description": "仅这些直连 peer 可信任 X-Real-IP / X-Forwarded-For",
    },
    "h5_public_base_url": {
        "group": "miniprogram",
        "label": "H5 对外域名",
        "unit": "",
        "type": "string",
        "max_length": 255,
        "description": "桌台一码通扫 / 顾客 H5 基址，如 https://order.example.com",
    },
    "dev_lan_host": {
        "group": "miniprogram",
        "label": "开发机局域网 IP",
        "unit": "",
        "type": "string",
        "max_length": 128,
        "description": "开发环境把 localhost 改写成真机可访问地址；生产可留空",
    },
    "mini_program_env_version": {
        "group": "miniprogram",
        "label": "小程序码版本",
        "unit": "",
        "type": "enum",
        "choices": ["auto", "develop", "trial", "release"],
        "description": "auto=按环境推断；桌台码/URL Link 的 env_version",
    },
    "xpyun_enabled": {
        "group": "print",
        "label": "厨房打印总开关",
        "unit": "",
        "type": "bool",
        "store_override": True,
        "description": "关闭后不再出制作单（历史字段名 XPYUN_ENABLED）；可按门店覆盖",
    },
    "printer_mode": {
        "group": "print",
        "label": "打印机模式",
        "unit": "",
        "type": "enum",
        "choices": ["local", "xpyun"],
        "store_override": True,
        "description": "local=局域网 ESC/POS；xpyun=芯烨云；可按门店覆盖",
    },
    "printer_host": {
        "group": "print",
        "label": "打印机 IP",
        "unit": "",
        "type": "string",
        "max_length": 128,
        "store_override": True,
        "description": "局域网模式全局默认；可按门店覆盖",
    },
    "printer_port": {
        "group": "print",
        "label": "打印机端口",
        "unit": "",
        "type": "int",
        "min": 1,
        "max": 65535,
        "store_override": True,
        "description": "局域网 ESC/POS 端口，默认 9100；可按门店覆盖",
    },
    "wechat_subscribe_template_order_ready": {
        "group": "miniprogram",
        "label": "微信出餐订阅模板",
        "unit": "",
        "type": "string",
        "max_length": 128,
        "description": "公众平台订阅消息模板 ID；空则不下发",
    },
    "allowed_image_extensions": {
        "group": "system",
        "label": "允许的图片扩展名",
        "unit": "",
        "type": "string",
        "max_length": 128,
        "description": "逗号分隔，如 jpg,jpeg,png,webp,gif",
    },
    "app_timezone": {
        "group": "system",
        "label": "业务时区",
        "unit": "",
        "type": "string",
        "max_length": 64,
        "description": "如 Asia/Shanghai；影响订单时间展示与超时计算",
    },
    "delivery_env_fallback_store_id": {
        "group": "delivery",
        "label": "外卖 .env 兜底门店",
        "unit": "",
        "type": "int",
        "min": 0,
        "max": 999999,
        "description": "无绑定时允许用 .env Token 的本系统门店 ID；多店生产必须 0",
    },
    "preference_scene_enabled": {
        "group": "system",
        "label": "口味场景加权",
        "unit": "",
        "type": "bool",
        "default": True,
        "store_override": True,
        "description": (
            "读侧总开关：规格偏好（迎宾/智能口味）与餐品推荐（猜你喜欢）按季节、同星期同餐段微调；"
            "开启后才生效「天气口味加权」。不改写历史偏好分。可按门店覆盖"
        ),
    },
    "weather_pref_enabled": {
        "group": "system",
        "label": "天气口味加权",
        "unit": "",
        "type": "bool",
        "default": True,
        "store_override": True,
        "description": (
            "依赖「口味场景加权」+ 门店经纬度；Open-Meteo 免 Key。"
            "≥28℃ 炎热抬升清淡/冰品，≤10℃ 寒冷抬升加辣/浓汤，雨天抬升暖胃汤面；"
            "失败静默降级。门店信息页可「探测天气」。可按门店覆盖"
        ),
    },
    "night_batch_enabled": {
        "group": "system",
        "label": "关店夜间批处理",
        "unit": "",
        "type": "bool",
        "default": True,
        "description": "北京时间关店窗（默认 02:00–05:00）跑销量热销/推荐、餐品与顾客口味等慢任务",
    },
    "night_batch_window_start_hour": {
        "group": "system",
        "label": "夜间批处理开始",
        "unit": "时",
        "type": "int",
        "min": 0,
        "max": 23,
        "default": 2,
        "description": "业务时区整点，含；默认 2 点（北京时间）",
    },
    "night_batch_window_end_hour": {
        "group": "system",
        "label": "夜间批处理结束",
        "unit": "时",
        "type": "int",
        "min": 0,
        "max": 24,
        "default": 5,
        "description": "业务时区整点，不含；默认 5 点。可跨午夜（如 23→4）",
    },
    "night_batch_sales_lookback_days": {
        "group": "system",
        "label": "热销统计天数",
        "unit": "天",
        "type": "int",
        "min": 1,
        "max": 90,
        "default": 7,
        "description": "按近 N 日已支付销量刷新 is_hot；is_recommended 仅人工勾选、不在此覆盖",
    },
    "night_batch_hot_top_n": {
        "group": "system",
        "label": "热销 Top N",
        "unit": "个",
        "type": "int",
        "min": 0,
        "max": 50,
        "default": 5,
        "description": "各门店近 N 日销量前 N 自动标为热销；0 表示不改热销标记",
    },
    "night_batch_recommend_top_n": {
        "group": "system",
        "label": "推荐 Top N（已停用）",
        "unit": "个",
        "type": "int",
        "min": 0,
        "max": 50,
        "default": 8,
        "description": "已停用：推荐改为店长在餐品管理中人工勾选，夜间批不再按销量覆盖",
    },
    "night_batch_taste_customer_limit": {
        "group": "system",
        "label": "夜间口味学习人数上限",
        "unit": "人",
        "type": "int",
        "min": 50,
        "max": 20000,
        "default": 2000,
        "description": "关店窗内单次最多学习多少名近期有行为的顾客",
    },
    "night_batch_taste_lookback_days": {
        "group": "system",
        "label": "夜间口味学习天数",
        "unit": "天",
        "type": "int",
        "min": 1,
        "max": 30,
        "default": 3,
        "description": "扫描近 N 日有行为事件的顾客做智能口味深度学习",
    },
    "night_batch_category_sort_enabled": {
        "group": "system",
        "label": "分类时段销量自动排序",
        "unit": "",
        "type": "bool",
        "default": True,
        "description": "关店窗内按各时段历史销量刷新已开启「销量自动」的分类时段方案",
    },
    "night_batch_category_sort_lookback_days": {
        "group": "system",
        "label": "分类时段统计天数",
        "unit": "天",
        "type": "int",
        "min": 1,
        "max": 180,
        "default": 30,
        "description": "统计近 N 日该时段已支付销量；天数越长、样本越多，自动排序越稳",
    },
    "night_batch_category_sort_soft_qty": {
        "group": "system",
        "label": "分类时段满权销量",
        "unit": "件",
        "type": "int",
        "min": 1,
        "max": 100000,
        "default": 100,
        "description": "该时段累计销量达此值时完全按销量排序；不足则与默认序加权混合，数据越多越准",
    },
}

from app.core.cache_keys import SYSTEM_SETTINGS_VER_KEY as REDIS_CACHE_VER_KEY


class SystemSettingsService:
    _cache: dict[str, str] | None = None
    # invalidate 后 _cache 为空时，sync 读优先用上次 DB 快照，禁止直接回退 .env（热更黑洞）
    _last_db_cache: dict[str, str] | None = None
    _cache_at: float = 0.0
    _cache_ver: int = -1
    CACHE_TTL = 15.0
    _seed_lock = asyncio.Lock()
    # Redis 版本失效/故障时合并并发刷新，避免每个请求同时回源 MySQL。
    _cache_refresh_lock = asyncio.Lock()

    @classmethod
    def _env_default(cls, key: str, settings: Settings | None = None) -> str:
        s = settings or get_settings()
        val = getattr(s, key, None)
        meta = BUSINESS_SETTING_META.get(key) or {}
        if meta.get("type") == "bool":
            # 无 Settings 字段时优先用 META.default，避免把本应开启的项种成 false
            fallback = bool(meta.get("default", False))
            if val is None and "default" in meta:
                return "true" if fallback else "false"
            return "true" if cls._parse_bool(val, fallback) else "false"
        if key == "mini_program_env_version":
            raw = (str(val).strip().lower() if val is not None else "")
            return raw if raw in ("develop", "trial", "release") else "auto"
        if val is not None:
            return str(val)
        if "default" in meta and meta.get("default") is not None:
            return str(meta["default"])
        return ""

    # Redis 版本键缺失/不可用均视为“版本未知”，不能与有效版本 0 混为一谈。
    # 版本未知时只允许使用本进程最近 15 秒内的 DB 快照，之后必须回源 DB。
    _REMOTE_VERSION_TTL = 30 * 24 * 3600
    _BUMP_REMOTE_VERSION_LUA = """
    local version = redis.call('INCR', KEYS[1])
    redis.call('EXPIRE', KEYS[1], ARGV[1])
    return version
    """

    @classmethod
    async def _remote_version(cls) -> int | None:
        try:
            from app.core.redis_client import get_redis

            r = await get_redis()
            raw = await r.get(REDIS_CACHE_VER_KEY)
            version = int(raw or 0)
            return version if version > 0 else None
        except Exception:
            logger.debug("读取系统设置 Redis 版本失败，进入短时 DB 快照降级", exc_info=True)
            return None

    @classmethod
    async def _bump_remote_version(cls) -> int:
        try:
            from app.core.redis_client import get_redis

            r = await get_redis()
            # INCR + EXPIRE 必须在同一 Lua 原子执行，避免进程在两条命令之间崩溃
            # 导致版本键永久无 TTL，最终形成永不过期的跨 worker 状态。
            return int(await r.eval(
                cls._BUMP_REMOTE_VERSION_LUA,
                1,
                REDIS_CACHE_VER_KEY,
                cls._REMOTE_VERSION_TTL,
            ))
        except Exception:
            logger.warning("递增系统设置 Redis 版本失败，跨 worker 缓存失效可能延迟", exc_info=True)
            return 0

    @classmethod
    async def public_config_version(cls) -> int:
        """小程序/公开 API 用的配置世代号；Redis 未知时返回 0 表示不可验证。"""
        return int(await cls._remote_version() or 0)

    @classmethod
    async def bump_public_config_version(cls) -> int:
        """门店覆盖等不走 invalidate_cache 的写路径，仍须 bump 以便客户端感知。"""
        return await cls._bump_remote_version()

    @classmethod
    async def _load_raw(cls, db: AsyncSession, *, force: bool = False) -> dict[str, str]:
        now = time.monotonic()
        remote_ver = await cls._remote_version()
        cache_age = now - cls._cache_at
        if not force and cls._cache is not None and cache_age < cls.CACHE_TTL:
            if remote_ver is None:
                # Redis 故障或版本键丢失：仅短时复用本进程 DB 快照，不把“未知”当作版本 0。
                cls._cache_ver = -1
                return cls._cache
            if remote_ver > 0 and cls._cache_ver == remote_ver:
                return cls._cache
        result = await db.execute(select(SystemSetting))
        cls._cache = {row.setting_key: row.setting_value for row in result.scalars()}
        cls._last_db_cache = dict(cls._cache)
        cls._cache_at = now
        cls._cache_ver = remote_ver if remote_ver is not None and remote_ver > 0 else -1
        return cls._cache

    @classmethod
    async def invalidate_cache(cls) -> None:
        """本进程立即失效，并 bump Redis 版本使其他 worker 在下次读取时失效。

        保留 _last_db_cache：sync 路径在重载前仍可读到最近一次 DB 值，避免回退 .env。
        """
        cls._cache = None
        cls._cache_at = 0.0
        cls._cache_ver = -1
        await cls._bump_remote_version()

    @classmethod
    def invalidate_cache_local(cls) -> None:
        """仅清本进程（测试或 Redis 不可用时）。保留 _last_db_cache。"""
        cls._cache = None
        cls._cache_at = 0.0
        cls._cache_ver = -1

    @classmethod
    def _patch_last_db_cache(cls, patch: dict[str, str]) -> None:
        if not patch:
            return
        if cls._last_db_cache is None:
            cls._last_db_cache = {}
        cls._last_db_cache.update(patch)

    @classmethod
    async def _sqlite_next_id(cls, db: AsyncSession) -> int:
        from sqlalchemy import func

        cur = await db.scalar(select(func.coalesce(func.max(SystemSetting.id), 0)))
        return int(cur or 0) + 1

    @classmethod
    async def _is_sqlite(cls, db: AsyncSession) -> bool:
        bind = db.get_bind()
        return bool(bind and bind.dialect.name == "sqlite")

    @classmethod
    async def ensure_seeded(cls, db: AsyncSession) -> None:
        """首次访问时把 .env 默认值写入 DB；并发下用锁 + savepoint 兜底。"""
        async with cls._seed_lock:
            settings = get_settings()
            result = await db.execute(select(SystemSetting.setting_key))
            existing = set(result.scalars().all())
            added = False
            use_sqlite = await cls._is_sqlite(db)
            next_id = await cls._sqlite_next_id(db) if use_sqlite else None
            for key, meta in BUSINESS_SETTING_META.items():
                if key in existing:
                    continue
                try:
                    async with db.begin_nested():
                        row = SystemSetting(
                            setting_key=key,
                            setting_value=cls._env_default(key, settings),
                            description=meta.get("description"),
                        )
                        if use_sqlite and next_id is not None:
                            row.id = next_id
                            next_id += 1
                        db.add(row)
                        await db.flush()
                    added = True
                except IntegrityError as e:
                    msg = str(getattr(e, "orig", e)).upper()
                    if "UNIQUE" in msg or "DUPLICATE" in msg:
                        logger.debug("system_settings seed race on key=%s", key)
                    else:
                        raise
            if added:
                await cls.invalidate_cache()

    @classmethod
    async def get_value(cls, db: AsyncSession, key: str, fallback: str | int | float) -> str:
        raw = await cls._load_raw(db)
        if key in raw:
            return raw[key]
        return str(fallback)

    @classmethod
    async def get_int(cls, db: AsyncSession, key: str, fallback: int) -> int:
        try:
            return int(await cls.get_value(db, key, fallback))
        except (TypeError, ValueError):
            return fallback

    @classmethod
    def peek_cached(cls, key: str) -> str | None:
        """同步窥视：热缓存 → 上次 DB 快照；皆无则 None（调用方才可回退 .env）。"""
        if cls._cache is not None and key in cls._cache:
            return cls._cache[key]
        if cls._last_db_cache is not None and key in cls._last_db_cache:
            return cls._last_db_cache[key]
        return None

    @classmethod
    def resolved_int_sync(cls, key: str, fallback: int) -> int:
        raw = cls.peek_cached(key)
        if raw is None:
            return fallback
        try:
            return int(raw)
        except (TypeError, ValueError):
            return fallback

    @classmethod
    def resolved_bool_sync(cls, key: str, fallback: bool) -> bool:
        raw = cls.peek_cached(key)
        if raw is None:
            return fallback
        return cls._parse_bool(raw, fallback)

    @classmethod
    def resolved_str_sync(cls, key: str, fallback: str) -> str:
        raw = cls.peek_cached(key)
        return fallback if raw is None else str(raw)

    @classmethod
    def resolved_mini_program_env_version_sync(cls) -> str:
        settings = get_settings()
        raw = cls.resolved_str_sync(
            "mini_program_env_version",
            (settings.mini_program_env_version or "").strip() or "auto",
        ).strip().lower()
        if raw in ("develop", "trial", "release"):
            return raw
        return "release" if settings.is_production else "develop"

    @classmethod
    async def touch_cache(cls, db: AsyncSession | None = None) -> None:
        """请求路径轻量刷新；缓存失效时单进程 single-flight 回源，避免 DB 雪崩。"""
        if db is not None:
            # 调用方提供 Session 的路径也必须 single-flight；否则所有请求同时遇到
            # Redis 版本变化/缓存过期时，会各自使用自己的 Session 并发回源 MySQL。
            async with cls._cache_refresh_lock:
                await cls._load_raw(db)
            return

        def _is_fresh(remote_ver: int | None) -> bool:
            if cls._cache is None or time.monotonic() - cls._cache_at >= cls.CACHE_TTL:
                return False
            if remote_ver is None:
                # Redis 状态未知时允许短时 stale-if-error，TTL 到期后回源 DB。
                cls._cache_ver = -1
                return True
            return remote_ver > 0 and cls._cache_ver == remote_ver

        remote_ver = await cls._remote_version()
        if _is_fresh(remote_ver):
            return

        # 多个请求同时遇到 Redis 版本变化/Redis 故障时，只允许一个请求回源；
        # 其余请求等待后重新检查缓存，不再同时打 MySQL。
        async with cls._cache_refresh_lock:
            remote_ver = await cls._remote_version()
            if _is_fresh(remote_ver):
                return
            from app.core.database import AsyncSessionLocal

            async with AsyncSessionLocal() as session:
                await cls._load_raw(session)

    @classmethod
    async def get_float(cls, db: AsyncSession, key: str, fallback: float) -> float:
        try:
            return float(await cls.get_value(db, key, fallback))
        except (TypeError, ValueError):
            return fallback

    @classmethod
    def _parse_bool(cls, raw: str | bool | int | None, fallback: bool = False) -> bool:
        if isinstance(raw, bool):
            return raw
        if raw is None:
            return fallback
        s = str(raw).strip().lower()
        if s in ("1", "true", "yes", "on", "y"):
            return True
        if s in ("0", "false", "no", "off", "n", ""):
            return False
        return fallback

    @classmethod
    async def get_bool(cls, db: AsyncSession, key: str, fallback: bool) -> bool:
        return cls._parse_bool(await cls.get_value(db, key, fallback), fallback)

    @classmethod
    async def store_override_map(cls, db: AsyncSession, key: str) -> dict[int, str]:
        """门店覆盖：store_id → raw value。"""
        from app.models import StoreSetting

        rows = (
            await db.execute(select(StoreSetting).where(StoreSetting.setting_key == key))
        ).scalars().all()
        return {int(r.store_id): r.setting_value for r in rows}

    @classmethod
    async def effective_setting(
        cls,
        db: AsyncSession,
        key: str,
        *,
        store_id: int | None = None,
        fallback: str | int | float | None = None,
    ) -> str:
        """
        业务读参入口：门店 store_settings 覆盖优先，否则全局 system_settings，再否则 fallback/.env。
        """
        if store_id is not None:
            from app.models import StoreSetting

            ov = await db.scalar(
                select(StoreSetting.setting_value).where(
                    StoreSetting.store_id == int(store_id),
                    StoreSetting.setting_key == key,
                )
            )
            if ov is not None and str(ov).strip() != "":
                return str(ov)
        fb = fallback if fallback is not None else cls._env_default(key)
        return await cls.get_value(db, key, fb)

    @classmethod
    async def effective_int(
        cls,
        db: AsyncSession,
        key: str,
        fallback: int,
        *,
        store_id: int | None = None,
    ) -> int:
        try:
            return int(await cls.effective_setting(db, key, store_id=store_id, fallback=fallback))
        except (TypeError, ValueError):
            return fallback

    @classmethod
    async def effective_float(
        cls,
        db: AsyncSession,
        key: str,
        fallback: float,
        *,
        store_id: int | None = None,
    ) -> float:
        try:
            return float(await cls.effective_setting(db, key, store_id=store_id, fallback=fallback))
        except (TypeError, ValueError):
            return fallback

    @classmethod
    async def effective_bool(
        cls,
        db: AsyncSession,
        key: str,
        fallback: bool,
        *,
        store_id: int | None = None,
    ) -> bool:
        return cls._parse_bool(
            await cls.effective_setting(db, key, store_id=store_id, fallback=fallback),
            fallback,
        )

    @classmethod
    def _validate_value(cls, key: str, value: str) -> str:
        if key not in BUSINESS_SETTING_META:
            raise ValueError(f"未知配置项: {key}")
        meta = BUSINESS_SETTING_META[key]
        typ = meta["type"]
        raw = str(value).strip()
        if typ == "bool":
            low = raw.lower()
            if low in ("1", "true", "yes", "on", "y"):
                return "true"
            if low in ("0", "false", "no", "off", "n"):
                return "false"
            raise ValueError(f"{meta['label']}须为 true/false")
        if typ == "string":
            # 允许空串（如关闭新客券 / 白名单）
            max_len = int(meta.get("max_length") or 256)
            if len(raw) > max_len:
                raise ValueError(f"{meta['label']}最多 {max_len} 字符")
            if key == "app_timezone" and raw:
                try:
                    from zoneinfo import ZoneInfo

                    ZoneInfo(raw)
                except Exception as e:
                    raise ValueError(f"{meta['label']}无效: {raw}") from e
            if key == "allowed_image_extensions" and raw:
                parts = [p.strip().lower().lstrip(".") for p in raw.split(",") if p.strip()]
                if not parts:
                    raise ValueError(f"{meta['label']}不能为空列表")
                return ",".join(parts)
            if key == "h5_public_base_url" and raw:
                if not (raw.startswith("http://") or raw.startswith("https://")):
                    raise ValueError(f"{meta['label']}须以 http:// 或 https:// 开头")
            return raw
        if typ == "enum":
            choices = [str(c) for c in (meta.get("choices") or [])]
            if raw.lower() in ("nan", "none", "null"):
                raise ValueError(f"{meta['label']}不能为空")
            # mini_program：空串视为 auto
            if key == "mini_program_env_version" and raw == "":
                return "auto"
            if raw not in choices:
                raise ValueError(f"{meta['label']}须为: {', '.join(choices)}")
            if key == "mini_program_env_version" and raw in ("develop", "trial"):
                from app.core.config import get_settings

                if get_settings().is_production:
                    raise ValueError("生产环境不可将小程序环境设为 develop/trial")
            return raw
        if raw == "" or raw.lower() in ("nan", "none", "null"):
            raise ValueError(f"{meta['label']}不能为空")
        try:
            if typ == "int":
                n = int(float(raw))  # 允许 "15.0"
                if n < meta["min"] or n > meta["max"]:
                    raise ValueError(f"{meta['label']}须在 {meta['min']}~{meta['max']} 之间")
                return str(n)
            if typ == "float":
                f = float(raw)
                if f < meta["min"] or f > meta["max"]:
                    raise ValueError(f"{meta['label']}须在 {meta['min']}~{meta['max']} 之间")
                return str(round(f, 4))
        except ValueError as e:
            if "须在" in str(e) or "不能为空" in str(e):
                raise
            raise ValueError(f"{meta['label']}格式无效") from e
        return raw

    @classmethod
    async def _apply_side_effects(cls, db: AsyncSession, key: str, normalized: str) -> None:
        if key != "visitor_data_retention_days":
            return
        from app.core.response import now_local

        days = int(normalized)
        new_expiry_cap = now_local() + timedelta(days=days)
        await db.execute(
            update(VisitorEvent)
            .where(VisitorEvent.expires_at > new_expiry_cap)
            .values(expires_at=new_expiry_cap)
        )

    @classmethod
    def allows_store_override(cls, key: str) -> bool:
        """是否按门店写入 store_settings。

        平台级键（安全、小程序主体、域名等）不可按店覆盖。
        其余业务参数默认可按店管理；META 里显式 store_override=False 时仍禁止。
        """
        if key in PLATFORM_ONLY_KEYS:
            return False
        meta = BUSINESS_SETTING_META.get(key) or {}
        if "store_override" in meta:
            return bool(meta["store_override"])
        return key in BUSINESS_SETTING_META

    @classmethod
    def group_meta_map(cls) -> dict[str, dict[str, str]]:
        return {g["id"]: g for g in BUSINESS_SETTING_GROUPS}

    @classmethod
    async def list_business(cls, db: AsyncSession) -> list[dict]:
        await cls.ensure_seeded(db)
        settings = get_settings()
        raw = await cls._load_raw(db)
        groups = cls.group_meta_map()
        items = []
        for key, meta in BUSINESS_SETTING_META.items():
            val = raw.get(key, cls._env_default(key, settings))
            gid = meta.get("group") or "system"
            gmeta = groups.get(gid) or {"id": gid, "label": gid, "description": ""}
            item: dict[str, Any] = {
                "key": key,
                "value": val,
                "label": meta["label"],
                "unit": meta.get("unit", ""),
                "type": meta["type"],
                "min": meta.get("min"),
                "max": meta.get("max"),
                "step": meta.get("step"),
                "max_length": meta.get("max_length"),
                "choices": meta.get("choices"),
                "description": meta.get("description"),
                "group": gid,
                "group_label": gmeta.get("label", gid),
                "group_description": gmeta.get("description", ""),
                "store_override": cls.allows_store_override(key),
                "scope": "store" if cls.allows_store_override(key) else "platform",
                "editable": True,
            }
            if meta["type"] == "bool":
                item["bool_value"] = cls._parse_bool(val, False)
            items.append(item)
        return items

    @classmethod
    async def list_business_grouped(cls, db: AsyncSession) -> list[dict]:
        """按 BUSINESS_SETTING_GROUPS 顺序返回分组 + items。"""
        items = await cls.list_business(db)
        by_group: dict[str, list[dict]] = {}
        for it in items:
            by_group.setdefault(it["group"], []).append(it)
        result = []
        for g in BUSINESS_SETTING_GROUPS:
            result.append(
                {
                    "id": g["id"],
                    "label": g["label"],
                    "description": g.get("description", ""),
                    "items": by_group.get(g["id"], []),
                }
            )
        # 兜底：META 中出现未登记的 group
        known = {g["id"] for g in BUSINESS_SETTING_GROUPS}
        for gid, gitems in by_group.items():
            if gid not in known:
                result.append({"id": gid, "label": gid, "description": "", "items": gitems})
        return result

    @classmethod
    async def update_business(cls, db: AsyncSession, updates: dict[str, str | int | float | bool]) -> list[dict]:
        if not updates:
            raise ValueError("未提交任何配置项")
        unknown = [k for k in updates if k not in BUSINESS_SETTING_META]
        if unknown:
            raise ValueError(f"不可修改的配置项: {', '.join(unknown)}")

        await cls.ensure_seeded(db)
        changed: dict[str, str] = {}
        for key, val in updates.items():
            normalized = cls._validate_value(key, str(val))
            row = await db.scalar(select(SystemSetting).where(SystemSetting.setting_key == key))
            if row:
                if row.setting_value == normalized:
                    continue
                row.setting_value = normalized
            else:
                meta = BUSINESS_SETTING_META[key]
                new_row = SystemSetting(
                    setting_key=key,
                    setting_value=normalized,
                    description=meta.get("description"),
                )
                if await cls._is_sqlite(db):
                    new_row.id = await cls._sqlite_next_id(db)
                db.add(new_row)
            changed[key] = normalized
            await cls._apply_side_effects(db, key, normalized)

        await db.flush()
        if changed:
            cls._patch_last_db_cache(changed)
            await cls.invalidate_cache()
            logger.info("Business settings updated: %s", changed)
        return await cls.list_business(db)

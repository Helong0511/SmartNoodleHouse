"""Redis 键与 TTL 统一目录（全站唯一真源）。

约定：
- 前缀一律 `snh:`（SmartNoodleHouse）；禁止业务代码再散落裸字符串键。
- TTL 由调用方显式传入，并应遵守本文件常量上限。
- 热缓存（进程内存）见 `app.core.hot_cache`，不进 Redis。
- Redis 不可用时业务须降级（除 Leader 锁等 fail-closed 场景）。
"""
from __future__ import annotations

import hashlib
import random

# ---------------------------------------------------------------------------
# 推荐 / 菜单
# ---------------------------------------------------------------------------
RECO_KEY_PREFIX = "snh:reco"
RECO_TTL_SECONDS = 120  # 推荐结果最长缓存 120s（spec 要求 ≤120s）

MENU_CATALOG_PREFIX = "snh:menu"
MENU_CATALOG_TTL_SECONDS = 120
MENU_BUNDLE_TTL_SECONDS = 60
MENU_VERSION_TTL_SECONDS = 30 * 24 * 3600

# ---------------------------------------------------------------------------
# 鉴权 / 登录
# ---------------------------------------------------------------------------
CUSTOMER_REFRESH_ROTATION_PREFIX = "snh:cust:refresh"
LOGIN_CREDENTIAL_USED_PREFIX = "snh:authcode:used"
LOGIN_CREDENTIAL_TTL_SECONDS = 300
LOGIN_FAIL_PREFIX = "snh:login:fail"
LOGIN_LOCK_PREFIX = "snh:login:lock"

# ---------------------------------------------------------------------------
# 迎宾 / 人脸 / TTS
# ---------------------------------------------------------------------------
TTS_WELCOME_QUEUE_KEY = "snh:tts:welcome"
SPEAK_TASK_PREFIX = "snh:tts:speak_task"
SPEAK_TASK_TTL_SECONDS = 600
WELCOME_COOLDOWN_PREFIX = "snh:welcome:cooldown"
UNKNOWN_WELCOME_COOLDOWN_PREFIX = "snh:welcome:unknown_cd"
WELCOME_FRAMES_PREFIX = "snh:welcome:frames"
SPEAK_GENERATION_PREFIX = "snh:welcome:speak_gen"
FACE_LIVENESS_PREFIX = "snh:face:liveness"
# 海康对讲 codec（alaw/ulaw）跨热重载持久化，避免首次播报再堵十几秒探测
HIKVISION_CODEC_PREFIX = "snh:hik:codec:"


def hikvision_codec_key(base_url: str, channel: int = 1) -> str:
    return f"{HIKVISION_CODEC_PREFIX}{base_url}|{int(channel)}"


def unknown_welcome_cooldown_key(camera_id: int) -> str:
    return f"{UNKNOWN_WELCOME_COOLDOWN_PREFIX}:{int(camera_id)}"


def speak_generation_key(camera_id: int) -> str:
    return f"{SPEAK_GENERATION_PREFIX}:{int(camera_id)}"


def speak_task_key(task_id: str) -> str:
    return f"{SPEAK_TASK_PREFIX}:{str(task_id).strip()}"

# ---------------------------------------------------------------------------
# 系统设置 / 调度 / Leader
# ---------------------------------------------------------------------------
SYSTEM_SETTINGS_VER_KEY = "snh:system_settings:ver"
SCHEDULER_OVERRIDES_VER_KEY = "snh:scheduler:overrides:ver"
SCHEDULER_OVERRIDES_CHANNEL = "snh:scheduler:overrides"
SCHEDULER_JOB_STATS_KEY = "snh:scheduler:job_stats"
SCHEDULER_NEXT_RUNS_KEY = "snh:scheduler:next_runs"
SCHEDULER_JOB_LOCK_PREFIX = "snh:scheduler:joblock"
LEADER_KEY = "snh:process:leader"

# ---------------------------------------------------------------------------
# 限流 / 支付 / 催单 / 消息
# ---------------------------------------------------------------------------
RATE_LIMIT_PREFIX = "snh:rl"
ORDER_URGE_PREFIX = "snh:order:urge"
PAY_SYNC_PREFIX = "snh:pay:sync"
MSG_DONE_PREFIX = "snh:msg:done:"
WEBHOOK_REPLAY_PREFIX = "snh:webhook:replay:"
BALANCE_NOTIFY_PREFIX = "snh:notify:balance_low"
WEATHER_PREFIX = "snh:weather:"

# ---------------------------------------------------------------------------
# WebSocket / 性能
# ---------------------------------------------------------------------------
WS_BROADCAST_CHANNEL = "snh:ws:broadcast"
WS_CONN_IP_PREFIX = "snh:ws:conn:ip:"
WS_CONN_USER_PREFIX = "snh:ws:conn:user:"
PERF_SAMPLES_PREFIX = "snh:perfs"
PERF_COUNT_PREFIX = "snh:perfc"
PERF_ROUTES_SET_KEY = "snh:perf:routes"


def ttl_with_jitter(ttl: int, *, min_ratio: float = 0.8, max_ratio: float = 1.2) -> int:
    """TTL 加随机偏移，防止缓存雪崩（同一批 key 同时过期导致 DB 瞬时压力）。"""
    return max(1, int(ttl * random.uniform(min_ratio, max_ratio)))


def reco_generation_key(customer_id: int | None) -> str:
    """顾客推荐缓存代际版本；画像/授权变化时原子递增，阻止旧计算结果回写。"""
    cid = int(customer_id) if customer_id else 0
    return f"{RECO_KEY_PREFIX}:gen:c{cid}"


def reco_key(
    customer_id: int | None, store_id: int, scene: str, bucket: str,
    fingerprint: str = "", generation: int = 0,
) -> str:
    """推荐结果键按顾客代际隔离；旧代际缓存自然过期，不会被新请求读取。"""
    cid = int(customer_id) if customer_id else 0
    base = f"{RECO_KEY_PREFIX}:c{cid}:s{int(store_id)}:{scene}:{bucket}:g{max(0, int(generation))}"
    if fingerprint:
        return f"{base}:{fingerprint}"
    return base


def reco_customer_pattern(customer_id: int) -> str:
    """画像/订单/评价变化后批量失效该顾客全部场景桶位。"""
    return f"{RECO_KEY_PREFIX}:c{int(customer_id)}:*"


def reco_rebuild_lock_key(
    customer_id: int | None, store_id: int, scene: str, bucket: str, fingerprint: str = ""
) -> str:
    """推荐结果重建互斥锁键。fingerprint 用于 settle_addon 区分不同购物车内容。"""
    cid = int(customer_id) if customer_id else 0
    base = f"{RECO_KEY_PREFIX}:lock:c{cid}:s{int(store_id)}:{scene}:{bucket}"
    return f"{base}:{fingerprint}" if fingerprint else base


def menu_rebuild_lock_key(store_id: int) -> str:
    """菜单 bundle 重建互斥锁键。"""
    return f"{MENU_CATALOG_PREFIX}:lock:{int(store_id)}"


def menu_catalog_key(store_id: int) -> str:
    return f"{MENU_CATALOG_PREFIX}:s{int(store_id)}"


def menu_bundle_key(store_id: int) -> str:
    return f"{MENU_CATALOG_PREFIX}:bundle:{int(store_id)}"


def menu_version_key(store_id: int) -> str:
    return f"{MENU_CATALOG_PREFIX}:ver:{int(store_id)}"


def cart_fingerprint(product_ids) -> str:
    """购物车餐品集合指纹：仅与集合内容有关，与顺序无关。"""
    ids = sorted({int(pid) for pid in (product_ids or []) if pid})
    if not ids:
        return ""
    raw = ",".join(str(i) for i in ids)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def customer_refresh_rotation_key(customer_id: int, token_jti: str | None = None) -> str:
    """顾客令牌刷新旋转键：按旧令牌 jti 隔离，不误伤同一顾客的其他设备/渠道会话。"""
    if not token_jti:
        raise ValueError("token_jti is required for refresh rotation")
    digest = hashlib.sha256(str(token_jti).encode("utf-8")).hexdigest()[:32]
    return f"{CUSTOMER_REFRESH_ROTATION_PREFIX}:{int(customer_id)}:{digest}"


def login_credential_used_key(channel: str, credential: str) -> str:
    """P2-7 登录凭证一次性消费键：凭证原文不落 Redis，仅存不可逆短指纹。"""
    digest = hashlib.sha256(str(credential or "").encode("utf-8")).hexdigest()[:32]
    return f"{LOGIN_CREDENTIAL_USED_PREFIX}:{(channel or 'x').strip().lower()}:{digest}"


def login_fail_key(scope: str) -> str:
    return f"{LOGIN_FAIL_PREFIX}:{scope}"


def login_lock_key(scope: str) -> str:
    return f"{LOGIN_LOCK_PREFIX}:{scope}"


def welcome_cooldown_key(customer_id: int) -> str:
    return f"{WELCOME_COOLDOWN_PREFIX}:{int(customer_id)}"


def welcome_frames_key(camera_id: int, customer_id: int) -> str:
    return f"{WELCOME_FRAMES_PREFIX}:{int(camera_id)}:{int(customer_id)}"


def face_liveness_key(token: str) -> str:
    return f"{FACE_LIVENESS_PREFIX}:{token}"


def scheduler_job_lock_key(job_id: str) -> str:
    return f"{SCHEDULER_JOB_LOCK_PREFIX}:{job_id}"


def rate_limit_key(client: str, path: str) -> str:
    return f"{RATE_LIMIT_PREFIX}:{client}:{path}"


def products_public_rate_limit_key(ip: str, store_id: int) -> str:
    return f"{RATE_LIMIT_PREFIX}:products:public:{ip}:{int(store_id)}"


def order_urge_key(order_id: int) -> str:
    return f"{ORDER_URGE_PREFIX}:{int(order_id)}"


def pay_sync_key(payment_id: int) -> str:
    return f"{PAY_SYNC_PREFIX}:{int(payment_id)}"


def balance_notify_key(customer_id: int) -> str:
    return f"{BALANCE_NOTIFY_PREFIX}:{int(customer_id)}"


def weather_key(suffix: str) -> str:
    return f"{WEATHER_PREFIX}{suffix}"


def perf_samples_key(bucket: str, route: str) -> str:
    return f"{PERF_SAMPLES_PREFIX}:{bucket}:{route}"


def perf_count_key(bucket: str, route: str) -> str:
    return f"{PERF_COUNT_PREFIX}:{bucket}:{route}"

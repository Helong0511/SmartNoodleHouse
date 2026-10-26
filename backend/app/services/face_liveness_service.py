"""P1: 人脸活体检测 — 随机动作挑战模式。

模式说明：
- none：仅多帧确认（原有逻辑，被动）
- challenge：系统下发随机动作（如"请眨眼"/"请向左转头"），
  前端录制对应动作视频帧，后端校验挑战 token + 时效 + 动作完成。

能力边界（务必遵守产品文案）：
- `action_completed` 是**客户端自报**，不是服务端视觉证明；不能当作防照片/
  防视频回放的充分证据。
- 本模块保证：token 一次性、过期、顾客绑定、可选动作名一致性。
- 摄像头门口迎宾与小程序高风险注册应使用不同安全等级；勿把 basic 门控
  或客户端挑战标记宣传为「真活体」。
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import timedelta

from app.core.cache_keys import face_liveness_key
from app.core.config import get_settings
from app.core.exceptions import AppException
from app.core.redis_client import try_get_redis
from app.core.response import now_local, serialize_dt

logger = logging.getLogger(__name__)

# 可选动作列表
LIVENESS_ACTIONS = ("blink", "turn_left", "turn_right", "nod", "smile")
ACTION_LABELS = {
    "blink": "请眨眼一次",
    "turn_left": "请向左转头",
    "turn_right": "请向右转头",
    "nod": "请点头一次",
    "smile": "请微笑一下",
}
CHALLENGE_TTL = 30  # 秒

# 原子 GETDEL：一次性挑战 token 必须在单条命令内取出并删除，
# 否则 GET 与 DEL 两次往返之间可被同一 token 并发重放。
_LUA_GET_DEL = """
local v = redis.call('get', KEYS[1])
if v then
    redis.call('del', KEYS[1])
end
return v
"""


def action_label(action: str | None) -> str | None:
    if not action:
        return None
    return ACTION_LABELS.get(str(action), str(action))


async def generate_liveness_challenge(
    *, customer_id: int | None = None, camera_id: int | None = None
) -> dict:
    """生成随机活体挑战，返回 {token, action, action_label, expires_at}。

    Token 写入 Redis，校验时原子删除防止重放。challenge 模式下 Redis 不可用则 fail-closed。
    """
    settings = get_settings()
    mode = (getattr(settings, "face_liveness_mode", "challenge") or "challenge").strip().lower()
    if mode == "none":
        return {
            "token": "",
            "action": None,
            "action_label": None,
            "mode": "none",
            "expires_at": None,
        }

    redis = await try_get_redis()
    if redis is None:
        raise AppException(50340, "活体挑战服务暂不可用，请稍后重试")

    action = secrets.choice(LIVENESS_ACTIONS)
    token = secrets.token_urlsafe(32)
    ttl = int(getattr(settings, "face_liveness_challenge_timeout", CHALLENGE_TTL))
    payload = {
        "action": action,
        "customer_id": int(customer_id) if customer_id is not None else None,
        "camera_id": camera_id,
        "issued_at": serialize_dt(now_local()),
    }
    from app.core.response import dumps_json

    await redis.set(face_liveness_key(token), dumps_json(payload), ex=ttl)
    expires_at = now_local() + timedelta(seconds=ttl)
    return {
        "token": token,
        "action": action,
        "action_label": action_label(action),
        "mode": "challenge",
        "expires_at": serialize_dt(expires_at),
    }


async def verify_liveness_challenge(
    token: str,
    *,
    action_completed: bool = False,
    claimed_action: str | None = None,
    customer_id: int | None = None,
) -> bool:
    """校验活体挑战 token：原子删除防止重放，并可校验绑定顾客。

    Args:
        token: generate_liveness_challenge 返回的 token
        action_completed: 客户端自报动作是否完成（默认 False，须显式 True）
        claimed_action: 可选；若传入须与签发时 action 一致
        customer_id: 若传入，须与签发时绑定的顾客一致
    Returns:
        True 表示挑战协议校验通过（非服务端视觉活体证明）
    """
    settings = get_settings()
    mode = (getattr(settings, "face_liveness_mode", "challenge") or "challenge").strip().lower()
    if mode == "none":
        return True

    if not token:
        return False

    # 未声称完成则直接失败，且不消费 token（允许客户端重试同一挑战）
    if not action_completed:
        logger.info("Liveness challenge rejected: action_completed not asserted")
        return False

    redis = await try_get_redis()
    if redis is None:
        logger.error("Liveness verify: Redis 不可用，fail-closed")
        return False

    key = face_liveness_key(token)
    # Lua 原子 GETDEL：取出即删，杜绝同一 token 并发重放
    raw = await redis.eval(_LUA_GET_DEL, 1, key)
    if not raw:
        logger.warning("Liveness challenge token not found or expired: %s", token[:8])
        return False

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return False

    bound = payload.get("customer_id")
    if customer_id is not None and bound is not None and int(bound) != int(customer_id):
        logger.warning(
            "Liveness challenge customer mismatch token=%s bound=%s got=%s",
            token[:8],
            bound,
            customer_id,
        )
        return False

    issued_action = str(payload.get("action") or "")
    if claimed_action is not None and issued_action:
        if str(claimed_action).strip().lower() != issued_action.strip().lower():
            logger.warning(
                "Liveness challenge action mismatch token=%s issued=%s claimed=%s",
                token[:8],
                issued_action,
                claimed_action,
            )
            return False

    return True

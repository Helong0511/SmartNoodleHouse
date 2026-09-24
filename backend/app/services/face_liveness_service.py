"""P1: 人脸活体检测 — 随机动作挑战模式。

模式说明：
- none：仅多帧确认（原有逻辑，被动）
- challenge：系统下发随机动作（如"请眨眼"/"请向左转头"），
  前端录制对应动作视频帧，后端校验挑战 token + 时效 + 动作完成。

当前实现提供挑战生成与校验基础设施；实际动作识别依赖前端 SDK 回传
action_completed 标记，后端做 token 防伪 + 时效 + 顾客绑定校验，防止重放。
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import timedelta

from app.core.config import get_settings
from app.core.exceptions import AppException
from app.core.redis_client import try_get_redis
from app.core.response import now_local

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
CHALLENGE_KEY = "snh:face:liveness:{token}"
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
        "issued_at": now_local().isoformat(),
    }
    await redis.set(CHALLENGE_KEY.format(token=token), json.dumps(payload), ex=ttl)
    expires_at = now_local() + timedelta(seconds=ttl)
    return {
        "token": token,
        "action": action,
        "action_label": action_label(action),
        "mode": "challenge",
        "expires_at": expires_at.isoformat(),
    }


async def verify_liveness_challenge(
    token: str,
    *,
    action_completed: bool = True,
    customer_id: int | None = None,
) -> bool:
    """校验活体挑战 token：原子删除防止重放，并可校验绑定顾客。

    Args:
        token: generate_liveness_challenge 返回的 token
        action_completed: 前端 SDK 判定动作是否完成
        customer_id: 若传入，须与签发时绑定的顾客一致
    Returns:
        True 表示挑战通过（token 有效 + 动作完成 + 顾客匹配）
    """
    settings = get_settings()
    mode = (getattr(settings, "face_liveness_mode", "challenge") or "challenge").strip().lower()
    if mode == "none":
        return True

    if not token:
        return False

    redis = await try_get_redis()
    if redis is None:
        logger.error("Liveness verify: Redis 不可用，fail-closed")
        return False

    key = CHALLENGE_KEY.format(token=token)
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

    if not action_completed:
        logger.info("Liveness challenge action not completed for token %s", token[:8])
        return False

    return True

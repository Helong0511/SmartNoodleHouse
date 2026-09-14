"""P1: 人脸活体检测 — 随机动作挑战模式。

模式说明：
- none：仅多帧确认（原有逻辑，被动）
- challenge：系统下发随机动作（如"请眨眼"/"请向左转头"），
  前端录制对应动作视频帧，后端校验挑战 token + 时效 + 动作完成。

当前实现提供挑战生成与校验基础设施；实际动作识别依赖前端 SDK 回传
action_completed 标记，后端做 token 防伪 + 时效校验，防止重放。
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import timedelta

from app.core.config import get_settings
from app.core.response import now_local
from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

# 可选动作列表
LIVENESS_ACTIONS = ("blink", "turn_left", "turn_right", "nod", "smile")
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


async def generate_liveness_challenge(
    *, customer_id: int | None = None, camera_id: int | None = None
) -> dict:
    """生成随机活体挑战，返回 {token, action, expires_at}。

    Token 写入 Redis，校验时原子删除防止重放。
    """
    settings = get_settings()
    mode = getattr(settings, "face_liveness_mode", "challenge") or "challenge"
    if mode == "none":
        # 被动模式：不生成挑战，直接放行
        return {"token": "", "action": None, "mode": "none", "expires_at": None}

    action = secrets.choice(LIVENESS_ACTIONS)
    token = secrets.token_urlsafe(32)
    ttl = int(getattr(settings, "face_liveness_challenge_timeout", CHALLENGE_TTL))
    payload = {
        "action": action,
        "customer_id": customer_id,
        "camera_id": camera_id,
        "issued_at": now_local().isoformat(),
    }
    redis = await get_redis()
    await redis.set(CHALLENGE_KEY.format(token=token), json.dumps(payload), ex=ttl)
    expires_at = now_local() + timedelta(seconds=ttl)
    return {
        "token": token,
        "action": action,
        "mode": "challenge",
        "expires_at": expires_at.isoformat(),
    }


async def verify_liveness_challenge(token: str, *, action_completed: bool = True) -> bool:
    """校验活体挑战 token：原子删除防止重放。

    Args:
        token: generate_liveness_challenge 返回的 token
        action_completed: 前端 SDK 判定动作是否完成
    Returns:
        True 表示挑战通过（token 有效 + 动作完成）
    """
    if not token:
        # mode=none 时不要求 token
        settings = get_settings()
        if getattr(settings, "face_liveness_mode", "challenge") == "none":
            return True
        return False

    redis = await get_redis()
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

    if not action_completed:
        logger.info("Liveness challenge action not completed for token %s", token[:8])
        return False

    return True

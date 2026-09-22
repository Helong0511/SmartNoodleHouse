"""消息队列与幂等消费域。

本系统的「队列」采用事务型 Outbox + Webhook Inbox（落 MySQL），
而非把 Redis List 当持久队列——符合「Redis 不当数据库」原则。

- 入队：业务事务内 ``enqueue_outbox`` / ``enqueue_delivery_webhook``
- 消费：定时任务 claim → handler（必须幂等）
- 跨渠道重复投递：``idempotency.try_acquire``（Redis 加速 + MySQL UNIQUE）
"""
from app.domains.messaging import idempotency  # noqa: F401
from app.services.outbox_worker import (  # noqa: F401
    enqueue_outbox,
    process_pending_outbox,
    register_outbox_handler,
)
from app.services.webhook_inbox import (  # noqa: F401
    enqueue_delivery_webhook,
    make_event_id,
    process_pending_inbox,
)

__all__ = [
    "idempotency",
    "enqueue_outbox",
    "process_pending_outbox",
    "register_outbox_handler",
    "enqueue_delivery_webhook",
    "make_event_id",
    "process_pending_inbox",
]

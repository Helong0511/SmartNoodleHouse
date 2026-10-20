"""领域模块边界（模块化开发入口）。

不要把用户 / 餐品 / 订单 / 权限全塞进一个巨型 service。
本包用「门面」划清边界：路由与外部包应优先从这里导入，后续可把实现迁入子包而不牵动调用方。

| 域 | 职责 | 现状落点 |
|---|---|---|
| auth | 登录、JWT、角色权限 | api/auth、api/deps、services/audit |
| user | 员工账号、顾客会员 | api/users、api/customers、customer_service |
| product | 餐品/分类/库存/菜单 | api/products、menu_service、stock_service |
| order | 下单/支付/厨房/打印 | api/orders、api/payments、order_*、print_* |
| messaging | 消息队列、幂等消费 | outbox / webhook_inbox / idempotency |

说明：HTTP 路由在 backend/app/api/（无 api/v1 前缀）。order / product / user
子包目前多为占位，真实实现仍在 services/ 与 api/；messaging 子包已落地。
"""

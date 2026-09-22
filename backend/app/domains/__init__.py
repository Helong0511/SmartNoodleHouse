"""领域模块边界（模块化开发入口）。

不要把用户 / 商品 / 订单 / 权限全塞进一个巨型 service。
本包用「门面」划清边界：路由与外部包应优先从这里导入，后续可把实现迁入子包而不牵动调用方。

| 域 | 职责 | 现状落点 |
|---|---|---|
| auth | 登录、JWT、角色权限 | api/v1/auth, api/deps, services/audit |
| user | 员工账号、顾客会员 | api/v1/users, customers, customer_service |
| product | 商品/分类/库存/菜单 | api/v1/products, menu_service, stock_service |
| order | 下单/支付/厨房/打印 | api/v1/orders, payments, order_*, print_* |
| messaging | 消息队列、幂等消费 | outbox / webhook_inbox / idempotency |
"""

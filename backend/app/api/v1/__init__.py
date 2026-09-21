from fastapi import APIRouter

from app.api.v1 import (
    ab_experiments,
    audit_logs,
    auth,
    cameras,
    client_logs,
    coupons,
    customers,
    dashboard,
    delivery,
    financial_ledger,
    health,
    member_levels,
    miniprogram,
    orders,
    payments,
    performance,
    print_jobs,
    products,
    settings,
    tables,
    users,
    welcome,
    websocket,
    pos,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(products.router)
api_router.include_router(orders.router)
api_router.include_router(payments.router)
api_router.include_router(financial_ledger.router)
api_router.include_router(print_jobs.router)
api_router.include_router(pos.router)
api_router.include_router(customers.router)
api_router.include_router(member_levels.router)
api_router.include_router(coupons.router)
api_router.include_router(tables.router)
api_router.include_router(cameras.router)
api_router.include_router(welcome.router)
api_router.include_router(dashboard.router)
api_router.include_router(miniprogram.router)
api_router.include_router(delivery.router)
api_router.include_router(settings.router)
api_router.include_router(client_logs.router)
api_router.include_router(websocket.router)
api_router.include_router(audit_logs.router)
api_router.include_router(ab_experiments.router)
api_router.include_router(performance.router)

from fastapi import APIRouter

from app.api.v1 import (
    auth,
    cameras,
    customers,
    dashboard,
    delivery,
    miniprogram,
    orders,
    payments,
    products,
    settings,
    tables,
    users,
    welcome,
    websocket,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(products.router)
api_router.include_router(orders.router)
api_router.include_router(payments.router)
api_router.include_router(customers.router)
api_router.include_router(tables.router)
api_router.include_router(cameras.router)
api_router.include_router(welcome.router)
api_router.include_router(dashboard.router)
api_router.include_router(miniprogram.router)
api_router.include_router(delivery.router)
api_router.include_router(settings.router)
api_router.include_router(websocket.router)

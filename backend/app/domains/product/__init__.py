"""餐品 / 菜单 / 库存域门面。"""
from app.services import stock_service as stock  # noqa: F401
from app.services.menu_service import (  # noqa: F401
    build_menu_bundle,
    get_or_build_menu_bundle,
    invalidate_local_menu_cache,
)

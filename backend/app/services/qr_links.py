"""桌台扫码链接：一码通扫 H5 落地页 + 微信/支付宝小程序 Scheme。"""
from urllib.parse import quote

from app.core.config import get_settings


def resolve_h5_public_base() -> str:
    settings = get_settings()
    custom = (settings.h5_public_base_url or "").strip().rstrip("/")
    if custom:
        return custom
    origins = settings.cors_origin_list
    if origins:
        return origins[0].rstrip("/")
    return "http://127.0.0.1:5173"


def build_table_qr_links(token: str, *, h5_base: str | None = None) -> dict[str, str]:
    """生成桌台相关扫码/跳转 URL（token 须为 dining_tables.qr_token）。"""
    settings = get_settings()
    wx_appid = settings.wechat_app_id or "YOUR_WECHAT_APPID"
    ali_appid = settings.alipay_app_id or "YOUR_ALIPAY_APPID"
    base = (h5_base or resolve_h5_public_base()).rstrip("/")
    query_wechat = quote(f"token={token}&channel=wechat", safe="")
    query_alipay = quote(f"token={token}&channel=alipay", safe="")
    wechat_scheme = (
        f"weixin://dl/business/?appid={wx_appid}"
        f"&path=pages/index/index&query={query_wechat}"
    )
    alipay_scheme = (
        f"alipays://platformapi/startapp?appId={ali_appid}"
        f"&page=pages/index/index&query={query_alipay}"
    )
    scan_url = f"{base}/scan?token={quote(token, safe='')}"
    h5_order_url = f"{base}/order?token={quote(token, safe='')}"
    return {
        "wechat_scheme": wechat_scheme,
        "alipay_scheme": alipay_scheme,
        "scan_url": scan_url,
        "h5_order_url": h5_order_url,
        "wechat_page": f"pages/index/index?token={token}&channel=wechat",
        "alipay_page": f"pages/index/index?token={token}&channel=alipay",
        "h5_fallback": f"/order?token={token}",
    }

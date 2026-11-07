from app.core.rate_limit import (
    SENSITIVE_LIMITS,
    _fail_closed_prefixes,
    path_requires_fail_closed,
)


def test_wechat_and_alipay_login_routes_have_strict_rate_limits():
    assert SENSITIVE_LIMITS["/api/miniprogram/wechat/login"] == (10, 60)
    assert SENSITIVE_LIMITS["/api/miniprogram/alipay/login"] == (10, 60)
    assert SENSITIVE_LIMITS["/api/miniprogram/auth/refresh"] == (30, 60)
    assert SENSITIVE_LIMITS["/api/miniprogram/member/phone"] == (10, 60)


def test_specific_mini_program_auth_limits_precede_generic_prefix():
    paths = [
        "/api/miniprogram/wechat/login",
        "/api/miniprogram/alipay/login",
        "/api/miniprogram/auth/refresh",
        "/api/miniprogram/member/phone",
    ]
    for path in paths:
        matched = next(
            config for prefix, config in SENSITIVE_LIMITS.items()
            if path.startswith(prefix) or path == prefix.rstrip("/")
        )
        assert matched in ((10, 60), (30, 60))


def test_redis_fail_closed_prefixes_match_real_auth_endpoints():
    prefixes = _fail_closed_prefixes(
        "/api/auth/login,/api/miniprogram/wechat/login,"
        "/api/miniprogram/alipay/login,/api/miniprogram/auth/refresh,"
        "/api/miniprogram/member/phone"
    )
    assert path_requires_fail_closed("/api/miniprogram/wechat/login", prefixes)
    assert path_requires_fail_closed("/api/miniprogram/alipay/login", prefixes)
    assert path_requires_fail_closed("/api/miniprogram/auth/refresh", prefixes)
    assert path_requires_fail_closed("/api/miniprogram/member/phone", prefixes)
    assert not path_requires_fail_closed("/api/miniprogram/menu", prefixes)

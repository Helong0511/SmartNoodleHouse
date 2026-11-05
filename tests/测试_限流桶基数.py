"""限流桶键基数回归测试：动态 URL 标识不得扩张 Redis 键空间。"""
from app.core.rate_limit import _rate_limit_bucket


def test_api_dynamic_resource_ids_share_bounded_bucket():
    paths = [
        "/api/orders/100001",
        "/api/orders/999999999",
        "/api/orders/random-slug-abc/status",
        "/api/orders/../../probe",
    ]
    assert {_rate_limit_bucket(path) for path in paths} == {"/api/orders"}


def test_api_subroutes_share_resource_bucket_unless_sensitive_policy_overrides():
    assert _rate_limit_bucket("/api/customers/1/profile") == "/api/customers"
    assert _rate_limit_bucket("/api/customers/2/orders") == "/api/customers"


def test_non_api_path_does_not_include_dynamic_segments():
    assert _rate_limit_bucket("/uploads/visitor/abc123/image.jpg") == "/uploads"


def test_query_string_is_not_part_of_rate_limit_bucket():
    assert _rate_limit_bucket("/api/orders/123?random=1") == _rate_limit_bucket(
        "/api/orders/987?random=2"
    )

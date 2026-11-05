"""限流桶键基数回归测试：动态 URL 标识不得扩张 Redis 键空间。"""
from app.core.rate_limit import _rate_limit_bucket


def test_api_dynamic_resource_ids_share_bounded_bucket():
    paths = [
        "/api/orders/100001",
        "/api/orders/999999999",
        "/api/orders/random-slug-abc/status",
        "/api/orders/../../probe",
    ]
    assert len({_rate_limit_bucket(path) for path in paths}) == 1


def test_api_subroutes_share_resource_bucket_unless_sensitive_policy_overrides():
    assert _rate_limit_bucket("/api/customers/1/profile") == _rate_limit_bucket(
        "/api/customers/2/orders"
    )


def test_non_api_path_does_not_include_dynamic_segments():
    assert _rate_limit_bucket("/uploads/visitor/abc123/image.jpg") == _rate_limit_bucket(
        "/uploads/visitor/def456/image.jpg"
    )


def test_query_string_is_not_part_of_rate_limit_bucket():
    assert _rate_limit_bucket("/api/orders/123?random=1") == _rate_limit_bucket(
        "/api/orders/987?random=2"
    )


def test_arbitrary_unknown_resources_have_a_hard_bucket_cardinality_bound():
    buckets = {
        _rate_limit_bucket(f"/api/random-{i}-resource/anything")
        for i in range(10_000)
    }
    assert len(buckets) <= 64

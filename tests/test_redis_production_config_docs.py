from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_mixed_redis_production_policy_is_noeviction():
    config = (ROOT / "redis.conf.example").read_text(encoding="utf-8")
    deployment = (ROOT / "docs" / "部署说明.md").read_text(encoding="utf-8")

    assert "maxmemory-policy noeviction" in config
    assert "maxmemory-policy noeviction" in deployment
    assert "maxmemory-policy=allkeys-lru" not in deployment
    assert "独立 Redis 实例" in deployment
    assert "vm.overcommit_memory = 1" in deployment
    assert "sysctl --system" in deployment

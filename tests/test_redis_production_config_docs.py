from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_redis_production_policy_protects_coordination_and_security_keys():
    redis_conf = (ROOT / "redis.conf.example").read_text(encoding="utf-8")
    deployment = (ROOT / "docs" / "部署说明.md").read_text(encoding="utf-8")

    assert "maxmemory-policy noeviction" in redis_conf
    assert "maxmemory-policy noeviction" in deployment
    assert "maxmemory-policy=allkeys-lru" not in deployment
    assert "纯缓存拆到独立 Redis 实例" in deployment

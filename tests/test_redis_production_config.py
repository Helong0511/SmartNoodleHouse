from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _redis_directives() -> dict[str, str]:
    directives: dict[str, str] = {}
    for raw in (ROOT / "redis.conf.example").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition(" ")
        directives[key.lower()] = value.strip()
    return directives


def test_production_redis_config_has_an_explicit_memory_ceiling_and_noeviction():
    directives = _redis_directives()

    assert directives.get("maxmemory"), "production Redis must not use unlimited maxmemory"
    assert directives["maxmemory"].lower() not in {"0", "0b"}
    assert directives.get("maxmemory-policy", "").lower() == "noeviction"
    assert directives.get("appendonly", "").lower() == "yes"
    assert directives.get("bind", "").split() == ["127.0.0.1"]
    assert directives.get("protected-mode", "").lower() == "yes"


def test_ubuntu_deployment_instructions_cover_redis_kernel_and_memory_checks():
    instructions = (ROOT / "scripts" / "生产部署-ubuntu.txt").read_text(encoding="utf-8")

    assert "vm.overcommit_memory = 1" in instructions
    assert "INFO memory" in instructions
    assert "INFO persistence" in instructions
    assert "python3.11 -m venv .venv" in instructions
    assert "pip install -r requirements.lock" in instructions
    assert "python scripts/check_requirements_lock.py" in instructions
    assert "chmod 600 .env" in instructions
    assert "chmod 640 /etc/redis/redis.conf" in instructions
    assert "maxmemory-policy noeviction" in instructions

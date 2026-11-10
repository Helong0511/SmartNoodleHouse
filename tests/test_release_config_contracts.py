from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _dotenv_value(path: Path, key: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    raise AssertionError(f"{key} is missing from {path}")


def test_generic_env_file_is_an_overview_not_a_production_template() -> None:
    path = ROOT / ".env.example"
    content = path.read_text(encoding="utf-8")
    assert "仅供查阅，不可直接复制为运行配置" in content.splitlines()[0]
    assert _dotenv_value(path, "APP_ENV") == "development"
    assert _dotenv_value(path, "APP_HOST") == "127.0.0.1"
    assert "请勿直接复制为 .env" in content


def test_production_webhook_allowlist_policy_matches_operator_guide() -> None:
    template = ROOT / ".env.production.example"
    docs = (ROOT / "docs/部署说明.md").read_text(encoding="utf-8")
    assert _dotenv_value(template, "WEBHOOK_REQUIRE_ALLOWLIST") == "false"
    assert "WEBHOOK_REQUIRE_ALLOWLIST=false" in docs
    assert "不得猜测或复制未经官方确认的 IP" in docs
    assert "签名/证书验签" in docs
    assert "数据库幂等" in docs

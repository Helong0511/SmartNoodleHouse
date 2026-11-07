from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readme_single_host_production_example_opts_into_loopback_services():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    start = readme.index("APP_ENV=production")
    end = readme.index("\n```", start)
    example = readme[start:end]

    assert "DATABASE_URL=mysql+aiomysql://snh:URL_ENCODED_DB_PASSWORD@127.0.0.1" in example
    assert "REDIS_URL=redis://:URL_ENCODED_REDIS_PASSWORD@127.0.0.1" in example
    assert "ALLOW_LOOPBACK_PRODUCTION_SERVICES=true" in example
    assert "URL 百分号编码" in readme


def test_readme_explains_remote_dependencies_must_not_use_loopback_opt_in():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "跨主机部署必须使用私网地址并保持 `false`" in readme

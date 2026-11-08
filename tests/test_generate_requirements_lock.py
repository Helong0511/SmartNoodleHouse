from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "生成依赖锁定文件.py"
SPEC = importlib.util.spec_from_file_location("requirements_lock_generator", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
GENERATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GENERATOR)


def _env(platform: str) -> dict[str, str]:
    result = dict(GENERATOR.default_environment())
    result["platform_system"] = platform
    return result


def test_build_lock_preserves_extras_platform_markers_and_transitive_pins():
    requirements = (
        "SQLAlchemy[asyncio]>=2.1.6,<3.0\n"
        'pywin32>=312; platform_system == "Windows"\n'
    )
    existing = (
        "SQLAlchemy[asyncio]==2.0.40\n"
        'pywin32==312; platform_system == "Windows"\n'
        "botocore==1.43.123\n"
    )
    frozen = "SQLAlchemy==2.1.8\n"

    result = GENERATOR.build_lock_text(
        requirements,
        existing,
        frozen,
        environment=_env("Linux"),
        python_version="3.11.18",
    )

    assert "SQLAlchemy[asyncio]==2.1.8" in result
    assert 'pywin32==312; platform_system == "Windows"' in result
    assert "botocore==1.43.123" in result


def test_inactive_platform_dependency_cannot_keep_a_stale_pin():
    requirements = 'pywin32>=312; platform_system == "Windows"\n'
    existing = 'pywin32==306; platform_system == "Windows"\n'

    with pytest.raises(ValueError, match="violates"):
        GENERATOR.build_lock_text(
            requirements,
            existing,
            "",
            environment=_env("Linux"),
        )


def test_windows_generation_uses_installed_pywin32_and_keeps_marker():
    requirements = 'pywin32>=312; platform_system == "Windows"\n'
    existing = 'pywin32==306; platform_system == "Windows"\n'

    result = GENERATOR.build_lock_text(
        requirements,
        existing,
        "pywin32==312\n",
        environment=_env("Windows"),
    )

    assert 'pywin32==312; platform_system == "Windows"' in result


def test_installed_direct_pin_must_satisfy_declared_range():
    with pytest.raises(ValueError, match="violates"):
        GENERATOR.build_lock_text(
            "redis>=8.1.0,<9.0\n",
            "redis==5.2.1\n",
            "redis==5.2.1\n",
        )


def test_missing_active_dependency_is_an_error_not_a_silent_omission():
    with pytest.raises(ValueError, match="not installed"):
        GENERATOR.build_lock_text(
            "pypinyin>=0.55.0,<1.0\n",
            "",
            "",
        )


def test_build_lock_pins_all_installed_transitive_dependencies_and_prefers_fresh_resolution():
    result = GENERATOR.build_lock_text(
        "redis>=5,<9\n",
        "redis==5.3.1\nurllib3==2.5.0\npywin32==312; platform_system == \\"Windows\\"\n",
        "redis==8.1.0\nurllib3==2.6.0\nidna==3.10\npip==26.3\n",
        environment=_env("Linux"),
    )

    assert "redis==8.1.0" in result
    assert "urllib3==2.6.0" in result
    assert "idna==3.10" in result
    assert "urllib3==2.5.0" not in result
    assert "pip==26.3" not in result
    assert 'pywin32==312; platform_system == "Windows"' in result

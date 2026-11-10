"""对象存储：S3 失败不得静默降级。"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest


def test_s3_init_failure_does_not_fallback_to_local():
    from app.services import object_storage as osmod

    osmod.reset_storage_for_tests()
    with patch.object(
        osmod,
        "get_settings",
        return_value=SimpleNamespace(
            storage_backend="s3",
            s3_endpoint="",
            s3_access_key="",
            s3_secret_key="",
            s3_bucket="",
            upload_dir="uploads",
        ),
    ):
        with pytest.raises(RuntimeError, match="S3"):
            osmod.get_storage()
    osmod.reset_storage_for_tests()


def test_local_storage_ok():
    from app.services import object_storage as osmod

    osmod.reset_storage_for_tests()
    with patch.object(
        osmod,
        "get_settings",
        return_value=SimpleNamespace(
            storage_backend="local",
            upload_dir="uploads",
        ),
    ):
        stor = osmod.get_storage()
        assert stor.__class__.__name__ == "LocalObjectStorage"
    osmod.reset_storage_for_tests()


def test_unknown_storage_backend_fails_closed():
    from app.services import object_storage as osmod

    osmod.reset_storage_for_tests()
    with patch.object(
        osmod,
        "get_settings",
        return_value=SimpleNamespace(
            storage_backend="s33",
            upload_dir="uploads",
        ),
    ):
        with pytest.raises(RuntimeError, match="不支持的 STORAGE_BACKEND"):
            osmod.get_storage()
    osmod.reset_storage_for_tests()


def test_s3_client_has_bounded_timeouts_and_retries(monkeypatch):
    import boto3
    from app.services import object_storage as osmod

    captured = {}
    fake_client = object()
    monkeypatch.setattr(
        boto3,
        "client",
        lambda service, **kwargs: captured.update(service=service, **kwargs) or fake_client,
    )
    monkeypatch.setattr(
        osmod,
        "get_settings",
        lambda: SimpleNamespace(
            storage_backend="s3",
            s3_endpoint="http://127.0.0.1:9000",
            s3_access_key="test-access",
            s3_secret_key="test-secret",
            s3_bucket="test-bucket",
            s3_region="us-east-1",
            s3_public_base_url="",
        ),
    )

    storage = osmod.S3ObjectStorage()

    assert storage.client is fake_client
    assert captured["service"] == "s3"
    config = captured["config"]
    assert config.connect_timeout == 5
    assert config.read_timeout == 30
    assert config.max_pool_connections == 50
    assert config.tcp_keepalive is True
    assert config.retries == {"mode": "standard", "max_attempts": 3}


def test_startup_storage_initialization_fails_closed(monkeypatch):
    from types import SimpleNamespace

    from app import main as mainmod
    from app.services import object_storage as osmod

    def fail_storage():
        raise RuntimeError("S3 SDK 缺失")

    monkeypatch.setattr(osmod, "get_storage", fail_storage)
    with pytest.raises(SystemExit) as exc:
        mainmod._initialize_object_storage(SimpleNamespace(storage_backend="s3"))
    assert exc.value.code == 1


def test_startup_storage_initialization_accepts_configured_adapter(monkeypatch):
    from types import SimpleNamespace

    from app import main as mainmod
    from app.services import object_storage as osmod

    expected = object()
    monkeypatch.setattr(osmod, "get_storage", lambda: expected)
    assert mainmod._initialize_object_storage(SimpleNamespace(storage_backend="local")) is None


def test_s3_aws_default_endpoint_uses_iam_credential_chain(monkeypatch):
    import boto3
    from app.services import object_storage as osmod

    captured = {}
    fake_client = object()
    monkeypatch.setattr(
        boto3,
        "client",
        lambda service, **kwargs: captured.update(service=service, **kwargs) or fake_client,
    )
    monkeypatch.setattr(
        osmod,
        "get_settings",
        lambda: SimpleNamespace(
            storage_backend="s3",
            s3_endpoint="",
            s3_access_key="",
            s3_secret_key="",
            s3_bucket="aws-bucket",
            s3_region="us-west-2",
            s3_public_base_url="",
        ),
    )

    storage = osmod.S3ObjectStorage()

    assert storage.client is fake_client
    assert captured["endpoint_url"] is None
    assert captured["aws_access_key_id"] is None
    assert captured["aws_secret_access_key"] is None
    assert captured["region_name"] == "us-west-2"


def test_s3_rejects_half_configured_static_credentials(monkeypatch):
    from app.services import object_storage as osmod

    monkeypatch.setattr(
        osmod,
        "get_settings",
        lambda: SimpleNamespace(
            storage_backend="s3",
            s3_endpoint="",
            s3_access_key="only-access-key",
            s3_secret_key="",
            s3_bucket="aws-bucket",
            s3_region="us-east-1",
            s3_public_base_url="",
        ),
    )

    with pytest.raises(RuntimeError, match="必须同时配置"):
        osmod.S3ObjectStorage()


def test_s3_rejects_invalid_custom_endpoint(monkeypatch):
    from app.services import object_storage as osmod

    monkeypatch.setattr(
        osmod,
        "get_settings",
        lambda: SimpleNamespace(
            storage_backend="s3",
            s3_endpoint="minio.internal:9000",
            s3_access_key="test-access",
            s3_secret_key="test-secret",
            s3_bucket="test-bucket",
            s3_region="us-east-1",
            s3_public_base_url="",
        ),
    )

    with pytest.raises(RuntimeError, match="S3_ENDPOINT"):
        osmod.S3ObjectStorage()

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

"""餐品主图 URL 白名单。"""
import pytest

from app.api.products import _assert_safe_product_image_url
from app.core.exceptions import AppException


def test_product_image_allows_uploads_path():
    assert _assert_safe_product_image_url("/uploads/a/b.jpg") == "/uploads/a/b.jpg"
    assert _assert_safe_product_image_url("uploads/x.png") == "/uploads/x.png"


def test_product_image_rejects_external_and_traversal():
    with pytest.raises(AppException):
        _assert_safe_product_image_url("https://evil.example/x.jpg")
    with pytest.raises(AppException):
        _assert_safe_product_image_url("/uploads/../etc/passwd")
    with pytest.raises(AppException):
        _assert_safe_product_image_url("http://127.0.0.1/uploads/a.jpg")

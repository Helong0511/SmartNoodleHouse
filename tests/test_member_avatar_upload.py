"""小程序会员头像：只接受本系统 /uploads 路径落库；CDN 地址须服务端转存。"""
import pytest

from app.api.v1.miniprogram import _persistable_avatar_url
from app.core.exceptions import AppException
from app.utils.upload import assert_public_http_url, sniff_image_type


def test_persistable_avatar_url_accepts_uploads_path():
    assert _persistable_avatar_url("/uploads/avatars/a.jpg") == "/uploads/avatars/a.jpg"
    assert _persistable_avatar_url("https://x.test/uploads/avatars/a.jpg?v=1") == "/uploads/avatars/a.jpg"


def test_persistable_avatar_url_rejects_temp_paths():
    assert _persistable_avatar_url("wxfile://tmp_abc") is None
    assert _persistable_avatar_url("http://tmp/xxx.jpg") is None
    assert _persistable_avatar_url("file:///tmp/x.png") is None
    assert _persistable_avatar_url("https://tfsimg.alipay.com/images/partner/T1xxx") is None
    assert _persistable_avatar_url("") is None
    assert _persistable_avatar_url(None) is None


def test_assert_public_http_url_rejects_ssrf():
    with pytest.raises(AppException) as ei:
        assert_public_http_url("file:///etc/passwd")
    assert ei.value.code == 40021

    with pytest.raises(AppException):
        assert_public_http_url("http://127.0.0.1/avatar.png")
    with pytest.raises(AppException):
        assert_public_http_url("http://192.168.1.10/a.jpg")
    with pytest.raises(AppException):
        assert_public_http_url("http://10.0.0.1/a.jpg")
    with pytest.raises(AppException):
        assert_public_http_url("http://169.254.169.254/latest/meta-data")
    with pytest.raises(AppException):
        assert_public_http_url("http://localhost/a.jpg")


def test_sniff_image_type():
    jpeg = b"\xff\xd8\xff" + b"\x00" * 16
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    assert sniff_image_type(jpeg) == ("jpg", "image/jpeg")
    assert sniff_image_type(png) == ("png", "image/png")
    with pytest.raises(AppException) as ei:
        sniff_image_type(b"<html>not image</html>")
    assert ei.value.code == 40021

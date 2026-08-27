"""上传魔数校验 / 对象存储本地"""
import pytest

from app.core.exceptions import AppException
from app.utils.upload import _validate_magic


def test_png_magic_ok():
    _validate_magic("png", b"\x89PNG\r\n\x1a\n" + b"xxxx")


def test_jpg_magic_reject():
    with pytest.raises(AppException):
        _validate_magic("jpg", b"notajpg")


def test_webp_magic():
    data = b"RIFF....WEBP...."
    # fix length: RIFF + 4 size + WEBP
    data = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"xxxx"
    _validate_magic("webp", data)

"""小程序会员头像：只接受本系统 /uploads 路径落库。"""
from app.api.v1.miniprogram import _persistable_avatar_url


def test_persistable_avatar_url_accepts_uploads_path():
    assert _persistable_avatar_url("/uploads/avatars/a.jpg") == "/uploads/avatars/a.jpg"
    assert _persistable_avatar_url("https://x.test/uploads/avatars/a.jpg?v=1") == "/uploads/avatars/a.jpg"


def test_persistable_avatar_url_rejects_temp_paths():
    assert _persistable_avatar_url("wxfile://tmp_abc") is None
    assert _persistable_avatar_url("http://tmp/xxx.jpg") is None
    assert _persistable_avatar_url("file:///tmp/x.png") is None
    assert _persistable_avatar_url("") is None
    assert _persistable_avatar_url(None) is None

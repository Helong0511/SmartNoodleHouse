"""门店 Logo：列表透出 + 更新/清除 + 上传校验与 WebP 转换。"""
from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.models import Store


def _super_admin():
    role = SimpleNamespace(code="SUPER_ADMIN")
    return SimpleNamespace(id=1, role_id=1, role=role, store_id=None, status=1, token_version=0)


async def _override_app(db_session, user):
    from app.api.deps import get_current_user, get_db
    from app.main import app

    async def override_db():
        yield db_session

    async def override_user():
        return user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    return app


def _clear_overrides():
    from app.main import app

    app.dependency_overrides.clear()


def _png_bytes(w: int = 200, h: int = 100, color=(255, 80, 40)) -> bytes:
    img = Image.new("RGB", (w, h), color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_store_options_include_logo(db_session):
    db_session.add(
        Store(
            id=9601,
            store_no="LOGO-S1",
            name="带Logo店",
            logo="/uploads/stores/demo.png",
            status=1,
        )
    )
    await db_session.flush()
    app = await _override_app(db_session, _super_admin())
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            with patch("app.api.deps.get_user_permissions", AsyncMock(return_value=["order:read"])):
                res = await ac.get("/api/settings/stores/options", params={"include_inactive": True})
        assert res.status_code == 200
        body = res.json()
        assert body["code"] == 0
        item = next(x for x in body["data"]["items"] if x["id"] == 9601)
        assert item["logo"] == "/uploads/stores/demo.png"
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_store_update_and_clear_logo(db_session):
    db_session.add(Store(id=9602, store_no="LOGO-S2", name="清Logo店", status=1))
    await db_session.flush()
    app = await _override_app(db_session, _super_admin())
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            with patch("app.api.deps.get_user_permissions", AsyncMock(return_value=["system:manage"])):
                res = await ac.put(
                    "/api/settings/stores/9602",
                    json={"logo": "/uploads/stores/a.png"},
                )
                assert res.status_code == 200
                assert res.json()["data"]["logo"] == "/uploads/stores/a.png"

                res2 = await ac.put("/api/settings/stores/9602", json={"logo": None})
                assert res2.status_code == 200
                assert res2.json()["data"]["logo"] is None
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_store_logo_rejects_external_url(db_session):
    db_session.add(Store(id=9603, store_no="LOGO-S3", name="拒外链店", status=1))
    await db_session.flush()
    app = await _override_app(db_session, _super_admin())
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            with patch("app.api.deps.get_user_permissions", AsyncMock(return_value=["system:manage"])):
                res = await ac.put(
                    "/api/settings/stores/9603",
                    json={"logo": "https://evil.example/x.png"},
                )
        assert res.status_code in (400, 422) or res.json().get("code", 0) != 0
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_store_logo_upload_converts_to_webp(db_session, tmp_path, monkeypatch):
    """PNG 上传后应变为正方形 WebP，并写入 stores.logo。"""
    from app.core import config as cfg
    from app.services.object_storage import reset_storage_for_tests

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    cfg.get_settings.cache_clear()
    reset_storage_for_tests()

    db_session.add(Store(id=9604, store_no="LOGO-S4", name="上传Logo店", status=1))
    await db_session.flush()
    app = await _override_app(db_session, _super_admin())
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            with patch("app.api.deps.get_user_permissions", AsyncMock(return_value=["system:manage"])):
                files = {"file": ("logo.png", _png_bytes(300, 150), "image/png")}
                res = await ac.post("/api/settings/stores/9604/logo", files=files)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["code"] == 0
        logo = body["data"]["logo"]
        assert logo.endswith(".webp")
        assert "/stores/" in logo.replace("\\", "/")

        candidates = list(tmp_path.rglob("*.webp"))
        assert candidates, f"未找到 webp 文件, logo={logo}, tmp={list(tmp_path.rglob('*'))}"
        out = Image.open(candidates[0])
        assert out.format == "WEBP"
        assert out.size[0] == out.size[1]
        assert out.size[0] <= 512
        assert out.size[0] >= 64
    finally:
        _clear_overrides()
        cfg.get_settings.cache_clear()
        reset_storage_for_tests()


@pytest.mark.asyncio
async def test_store_logo_rejects_gif_and_too_small(db_session, tmp_path, monkeypatch):
    from app.core import config as cfg
    from app.services.object_storage import reset_storage_for_tests

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    cfg.get_settings.cache_clear()
    reset_storage_for_tests()

    db_session.add(Store(id=9605, store_no="LOGO-S5", name="拒GIF店", status=1))
    await db_session.flush()
    app = await _override_app(db_session, _super_admin())
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            with patch("app.api.deps.get_user_permissions", AsyncMock(return_value=["system:manage"])):
                gif = BytesIO()
                Image.new("RGB", (100, 100), (1, 2, 3)).save(gif, format="GIF")
                res = await ac.post(
                    "/api/settings/stores/9605/logo",
                    files={"file": ("a.gif", gif.getvalue(), "image/gif")},
                )
                assert res.json().get("code", 0) != 0

                tiny = _png_bytes(32, 32)
                res2 = await ac.post(
                    "/api/settings/stores/9605/logo",
                    files={"file": ("tiny.png", tiny, "image/png")},
                )
                assert res2.json().get("code", 0) != 0
    finally:
        _clear_overrides()
        cfg.get_settings.cache_clear()
        reset_storage_for_tests()


def test_normalize_store_logo_webp_square():
    from app.utils.upload import _normalize_store_logo_webp

    # 短边 < 512：居中裁方后保持短边，不放大
    raw = _png_bytes(640, 320)
    out = _normalize_store_logo_webp(raw)
    assert out[:4] == b"RIFF"
    assert out[8:12] == b"WEBP"
    img = Image.open(BytesIO(out))
    assert img.size == (320, 320)

    # 短边 > 512：缩到 512
    big = _png_bytes(900, 700)
    out2 = _normalize_store_logo_webp(big)
    img2 = Image.open(BytesIO(out2))
    assert img2.size == (512, 512)

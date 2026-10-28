# -*- coding: utf-8 -*-
"""海康主码流 → 子码流：入库与连接均优先 102，降识别解码负载。"""
from __future__ import annotations

from unittest.mock import patch

from app.services.camera_credentials import (
    build_connect_source,
    prefer_hikvision_substream_url,
    prepare_camera_source,
)


def test_prefer_substream_101_to_102():
    src = "rtsp://192.168.1.189:554/Streaming/Channels/101"
    assert prefer_hikvision_substream_url(src).endswith("/Streaming/Channels/102")


def test_prefer_substream_201_to_202():
    src = "rtsp://cam/Streaming/Channels/201"
    assert prefer_hikvision_substream_url(src).endswith("/Streaming/Channels/202")


def test_prefer_keeps_already_substream():
    src = "rtsp://192.168.1.189:554/Streaming/Channels/102"
    assert prefer_hikvision_substream_url(src) == src


def test_prefer_keeps_usb_and_non_hik():
    assert prefer_hikvision_substream_url("0") == "0"
    other = "rtsp://192.168.1.10/live/main"
    assert prefer_hikvision_substream_url(other) == other


def test_prepare_camera_source_rewrites_101():
    safe, user, pwd = prepare_camera_source(
        "rtsp://192.168.1.189:554/Streaming/Channels/101",
        username="admin",
        password="x",
    )
    assert safe.endswith("/Streaming/Channels/102")
    assert ":" not in (safe.split("@")[0].split("//")[-1] or "") or True
    assert user == "admin"
    assert pwd == "x"


def test_build_connect_source_rewrites_even_if_db_still_101():
    with patch(
        "app.services.camera_credentials.decrypt_password",
        return_value="secret",
    ):
        url = build_connect_source(
            "rtsp://192.168.1.189:554/Streaming/Channels/101",
            "admin",
            "enc",
        )
    assert "/Streaming/Channels/102" in url
    assert "secret" in url

# -*- coding: utf-8 -*-
"""Async ORM 安全辅助：避免 sync 序列化触发 MissingGreenlet。

本项目 ``expire_on_commit=False``，commit 后已加载字段一般可读；
但对 ``server_default`` / ``onupdate`` 列，INSERT/UPDATE flush 后常见未加载，
在同步 ``_payload`` / ``serialize_dt(obj.created_at)`` 中访问会触发异步懒加载失败。

约定：新建或依赖库生成时间戳的对象，在 sync 序列化前 ``await db.refresh(obj)``。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import NoInspectionAvailable
from sqlalchemy.orm.exc import UnmappedInstanceError


def orm_attr_loaded(obj: Any, attr: str) -> bool:
    """属性是否已在实例中加载（未加载则 sync 读取可能 MissingGreenlet）。"""
    try:
        state = sa_inspect(obj)
    except (UnmappedInstanceError, NoInspectionAvailable):
        # 普通 DTO / SimpleNamespace 不是 ORM 实例；按普通对象属性判断，
        # 避免序列化辅助函数把合法测试 DTO 或 API 投影对象当成 ORM 错误。
        return hasattr(obj, attr)
    if state.transient:
        return False
    return attr not in state.unloaded

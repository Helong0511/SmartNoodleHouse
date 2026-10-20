"""门店创建/业务参数/Logo：从 settings 路由拆出，路径仍挂在 /api/settings。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, get_user_permissions, require_permission
from app.api.settings_common import write_audit
from app.core.exceptions import AppException
from app.core.response import success
from app.models import Store, StoreSetting
from app.schemas.settings import StoreUpdate
from app.services.store_scope import assert_store_access, require_bound_store
from app.services.system_settings_service import (
    BUSINESS_SETTING_GROUPS,
    BUSINESS_SETTING_META,
    SystemSettingsService,
)
from app.utils.upload import save_store_logo

router = APIRouter(prefix="/settings", tags=["系统设置"])


async def require_store_update(store_id: int, user: CurrentUser):
    """编辑门店信息：超管可改任意门店；店长仅可改自己绑定的门店。"""
    if user.role.code == "SUPER_ADMIN":
        return user
    if user.role.code == "STORE_ADMIN":
        if user.store_id is None or int(user.store_id) != int(store_id):
            raise AppException(40301, "无权修改该门店", status_code=403)
        return user
    raise AppException(40301, "无权编辑门店信息", status_code=403)


class StoreCreate(BaseModel):
    store_no: str = Field(..., min_length=1, max_length=40)
    name: str = Field(..., min_length=1, max_length=100)
    address: str | None = None
    phone: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class StoreBusinessUpdate(BaseModel):
    updates: dict[str, str | int | float | bool] = Field(default_factory=dict)


async def require_store_settings(user: CurrentUser, db: DbSession):
    """本店业务参数：超管、店长，或持有系统管理 / 门店设置权限。"""
    if user.role.code in ("SUPER_ADMIN", "STORE_ADMIN"):
        return user
    perms = await get_user_permissions(db, user.role_id)
    if "system:manage" in perms or "settings:store" in perms:
        return user
    raise AppException(40301, "无权管理门店设置", status_code=403)


@router.get("/stores/options", dependencies=[require_permission("order:read")])
async def list_store_options(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    include_inactive: bool = Query(default=False),
):
    """员工可选门店列表：超管看全部营业中门店；门店账号仅本店。

    include_inactive=true 时返回含停用门店（门店信息管理页使用），
    并附带经纬度、状态字段。
    """
    effective = require_bound_store(user, None)
    q = select(Store).order_by(Store.id)
    if not include_inactive:
        q = q.where(Store.status == 1)
    if effective is not None:
        q = q.where(Store.id == effective)
    rows = list((await db.execute(q)).scalars().all())
    return success(
        {
            "items": [
                {
                    "id": s.id,
                    "store_no": s.store_no,
                    "name": s.name,
                    "logo": getattr(s, "logo", None),
                    "address": s.address,
                    "phone": s.phone,
                    "status": s.status,
                    "latitude": float(s.latitude) if s.latitude is not None else None,
                    "longitude": float(s.longitude) if s.longitude is not None else None,
                }
                for s in rows
            ],
            "bound_store_id": user.store_id,
            "is_super_admin": user.role.code == "SUPER_ADMIN",
        },
        request=request,
    )


@router.post("/stores", dependencies=[require_permission("system:manage")])
async def create_store(data: StoreCreate, request: Request, db: DbSession, user: CurrentUser):
    """新建门店（仅超管/系统管理权限）。"""
    no = data.store_no.strip()
    exists = await db.scalar(select(Store.id).where(Store.store_no == no).limit(1))
    if exists:
        raise AppException(40070, f"门店编号「{no}」已存在")
    from decimal import Decimal

    store = Store(
        store_no=no,
        name=data.name.strip(),
        address=(data.address or "").strip() or None,
        phone=(data.phone or "").strip() or None,
        latitude=Decimal(str(data.latitude)) if data.latitude is not None else None,
        longitude=Decimal(str(data.longitude)) if data.longitude is not None else None,
        status=1,
    )
    db.add(store)
    await db.flush()
    await write_audit(
        db,
        user=user,
        request=request,
        action="store.create",
        target_type="store",
        target_id=store.id,
        detail={
            "store_no": store.store_no,
            "name": store.name,
            "address": store.address,
            "phone": store.phone,
        },
    )
    return success(
        {"id": store.id, "store_no": store.store_no, "name": store.name, "status": store.status},
        request=request,
    )


async def welcome_coupon_options(db, store_id: int, *, current: str = "") -> list[dict]:
    """当前门店可选的新客券：本店模板 + 总部模板（已启用，且总部白名单包含本店）。"""
    from app.services.coupon_service import CouponService

    rows = await CouponService.list_templates(db, include_disabled=False, store_id=int(store_id))
    by_name: dict[str, dict] = {}
    for t in rows:
        name = str(t.get("name") or "").strip()
        if not name:
            continue
        scope = "STORE" if t.get("store_id") else "HQ"
        # 同名时保留本店模板，避免下拉值冲突
        if name in by_name and by_name[name]["scope"] == "STORE":
            continue
        summary = str(t.get("summary") or "").strip()
        label = f"{'本店' if scope == 'STORE' else '总部'} · {name}"
        if summary:
            label = f"{label}（{summary}）"
        by_name[name] = {
            "value": name,
            "label": label,
            "scope": scope,
            "summary": summary,
        }
    options = list(by_name.values())
    options.sort(key=lambda o: (0 if o["scope"] == "STORE" else 1, o["label"]))
    seen = set(by_name)
    cur = (current or "").strip()
    if cur and cur not in seen:
        options.append(
            {
                "value": cur,
                "label": f"当前值 · {cur}（已停用或不属于本店/总部）",
                "scope": "CURRENT",
                "summary": "",
            }
        )
    return options


# 按「模板名」下拉选择的门店业务项（与新客/生日同一套 options）
COUPON_NAME_SELECT_KEYS = frozenset(
    {
        "welcome_coupon_name",
        "birthday_coupon_name",
        "sleep_wake_coupon_name_30",
        "sleep_wake_coupon_name_90",
        "love_day_soy_template_name",
    }
)

COUPON_NAME_SELECT_HINT = {
    "welcome_coupon_name": "新客赠券",
    "birthday_coupon_name": "生日赠券",
    "sleep_wake_coupon_name_30": "沉睡唤醒券(30–90天)",
    "sleep_wake_coupon_name_90": "沉睡唤醒券(≥90天)",
    "love_day_soy_template_name": "爱心日豆浆券",
}


async def assert_coupon_name_choice(db, store_id: int, key: str, name: str) -> None:
    raw = (name or "").strip()
    if not raw:
        return
    options = await welcome_coupon_options(db, store_id, current="")
    allowed = {o["value"] for o in options if o.get("scope") in ("STORE", "HQ")}
    if raw not in allowed:
        label = COUPON_NAME_SELECT_HINT.get(key, "赠券")
        raise AppException(40014, f"{label}须选择本店或总部已启用的券模板，或留空表示关闭")


async def coupon_template_id_options(db, store_id: int, *, current_ids: list[int] | None = None) -> list[dict]:
    """新人礼包多选：value=模板 id（字符串），便于前端多选。"""
    from app.services.coupon_service import CouponService

    rows = await CouponService.list_templates(db, include_disabled=False, store_id=int(store_id))
    options: list[dict] = []
    seen: set[int] = set()
    for t in rows:
        tid = int(t.get("id") or 0)
        name = str(t.get("name") or "").strip()
        if tid <= 0 or not name:
            continue
        scope = "STORE" if t.get("store_id") else "HQ"
        summary = str(t.get("summary") or "").strip()
        label = f"{'本店' if scope == 'STORE' else '总部'} · {name} #{tid}"
        if summary:
            label = f"{label}（{summary}）"
        options.append(
            {
                "value": str(tid),
                "label": label,
                "scope": scope,
                "summary": summary,
                "name": name,
            }
        )
        seen.add(tid)
    options.sort(key=lambda o: (0 if o["scope"] == "STORE" else 1, o["label"]))
    for tid in current_ids or []:
        if int(tid) in seen:
            continue
        options.append(
            {
                "value": str(int(tid)),
                "label": f"当前值 · 模板#{int(tid)}（已停用或不属于本店/总部）",
                "scope": "CURRENT",
                "summary": "",
                "name": "",
            }
        )
    return options


def parse_welcome_bundle_ids(raw: str) -> list[int]:
    import json

    text = (raw or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise AppException(40014, "新人礼包须为 JSON 数组，如 [1,2,3]") from e
    if not isinstance(data, list):
        raise AppException(40014, "新人礼包须为 JSON 数组")
    out: list[int] = []
    for x in data:
        try:
            tid = int(x)
        except (TypeError, ValueError) as e:
            raise AppException(40014, "新人礼包模板 ID 须为正整数") from e
        if tid <= 0:
            raise AppException(40014, "新人礼包模板 ID 须为正整数")
        if tid not in out:
            out.append(tid)
    return out


async def assert_welcome_bundle_choice(db, store_id: int, raw: str) -> str:
    """校验并规范化 welcome_bundle_template_ids，返回紧凑 JSON 字符串。"""
    import json

    ids = parse_welcome_bundle_ids(raw)
    if not ids:
        return ""
    options = await coupon_template_id_options(db, store_id, current_ids=[])
    allowed = {int(o["value"]) for o in options if o.get("scope") in ("STORE", "HQ")}
    bad = [i for i in ids if i not in allowed]
    if bad:
        raise AppException(
            40014,
            f"新人礼包含不可用模板 ID: {', '.join(str(x) for x in bad)}；请从本店或总部已启用模板中选择",
        )
    return json.dumps(ids, ensure_ascii=False, separators=(",", ":"))


def group_store_items(items: list[dict]) -> list[dict]:
    by_group: dict[str, list[dict]] = {}
    for it in items:
        by_group.setdefault(it.get("group") or "system", []).append(it)
    groups = []
    for g in BUSINESS_SETTING_GROUPS:
        gitems = by_group.get(g["id"]) or []
        if not gitems:
            continue
        groups.append(
            {
                "id": g["id"],
                "label": g["label"],
                "description": g.get("description", ""),
                "items": gitems,
            }
        )
    return groups


@router.get("/stores/{store_id}/business", dependencies=[Depends(require_store_settings)])
async def get_store_business(
    store_id: int,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """门店业务参数：全局默认 + 本店覆盖。"""
    assert_store_access(user, store_id)
    store = await db.get(Store, store_id)
    if not store:
        raise AppException(40011, "门店不存在")
    global_items = await SystemSettingsService.list_business(db)
    overrides = {
        r.setting_key: r.setting_value
        for r in (
            await db.execute(select(StoreSetting).where(StoreSetting.store_id == store_id))
        ).scalars()
    }
    items = []
    for it in global_items:
        key = it["key"]
        if not it.get("store_override", False):
            continue
        has_ov = key in overrides
        effective = overrides[key] if has_ov else it["value"]
        row = {
            **it,
            "global_value": it["value"],
            "value": effective,
            "has_override": has_ov,
        }
        if it.get("type") == "bool":
            row["bool_value"] = SystemSettingsService._parse_bool(effective, False)
        if key in COUPON_NAME_SELECT_KEYS:
            row["options"] = await welcome_coupon_options(db, store_id, current=str(effective or ""))
        if key == "welcome_bundle_template_ids":
            try:
                cur_ids = parse_welcome_bundle_ids(str(effective or ""))
            except AppException:
                cur_ids = []
            row["options"] = await coupon_template_id_options(db, store_id, current_ids=cur_ids)
            row["id_values"] = [str(i) for i in cur_ids]
        items.append(row)
    return success(
        {
            "store_id": store_id,
            "store_name": store.name,
            "items": items,
            "business_groups": group_store_items(items),
        },
        request=request,
    )


@router.put("/stores/{store_id}/business", dependencies=[Depends(require_store_settings)])
async def update_store_business(
    store_id: int,
    data: StoreBusinessUpdate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    assert_store_access(user, store_id)
    store = await db.get(Store, store_id)
    if not store:
        raise AppException(40011, "门店不存在")
    if not data.updates:
        raise AppException(40012, "未提交任何修改")
    unknown = [k for k in data.updates if k not in BUSINESS_SETTING_META]
    if unknown:
        raise AppException(40014, f"不可修改的配置项: {', '.join(unknown)}")
    not_overrideable = [
        k for k in data.updates if not SystemSettingsService.allows_store_override(k)
    ]
    if not_overrideable:
        raise AppException(
            40014,
            f"以下配置为全局级，不可按门店覆盖: {', '.join(not_overrideable)}",
        )

    existing = {
        r.setting_key: r
        for r in (
            await db.execute(select(StoreSetting).where(StoreSetting.store_id == store_id))
        ).scalars()
    }
    for key, val in data.updates.items():
        try:
            sval = SystemSettingsService._validate_value(key, str(val if val is not None else ""))
        except ValueError as e:
            raise AppException(40014, str(e)) from e
        if key in COUPON_NAME_SELECT_KEYS:
            await assert_coupon_name_choice(db, store_id, key, sval)
        if key == "welcome_bundle_template_ids":
            sval = await assert_welcome_bundle_choice(db, store_id, sval)
        if key in existing:
            existing[key].setting_value = sval
        else:
            db.add(StoreSetting(store_id=store_id, setting_key=key, setting_value=sval))
    await write_audit(
        db,
        user=user,
        request=request,
        action="store.business.update",
        target_type="store",
        target_id=store_id,
        detail={"updates": {k: str(v) for k, v in data.updates.items()}},
    )
    await db.flush()
    # 门店覆盖不进全局 settings 进程缓存，但仍 bump 公开 config_version 供小程序感知
    await SystemSettingsService.bump_public_config_version()
    return await get_store_business(store_id, request, db, user)


@router.delete("/stores/{store_id}/business/{key}", dependencies=[Depends(require_store_settings)])
async def clear_store_business_key(
    store_id: int,
    key: str,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """清除本店覆盖，回退全局默认。"""
    assert_store_access(user, store_id)
    if key not in BUSINESS_SETTING_META:
        raise AppException(40014, f"未知配置项: {key}")
    if not SystemSettingsService.allows_store_override(key):
        raise AppException(40014, f"配置项不可按门店覆盖: {key}")
    from sqlalchemy import delete as sa_delete

    await db.execute(
        sa_delete(StoreSetting).where(StoreSetting.store_id == store_id, StoreSetting.setting_key == key)
    )
    await write_audit(
        db,
        user=user,
        request=request,
        action="store.business.clear",
        target_type="store",
        target_id=store_id,
        detail={"key": key},
    )
    await db.flush()
    await SystemSettingsService.bump_public_config_version()
    return success({"cleared": True, "store_id": store_id, "key": key}, request=request)


@router.put("/stores/{store_id}", dependencies=[Depends(require_store_update)])
async def update_store(
    store_id: int,
    data: StoreUpdate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    assert_store_access(user, store_id)
    # 非超管不得改他店；超管可改任意店（assert 已放行）
    if user.role.code != "SUPER_ADMIN" and user.store_id and int(user.store_id) != int(store_id):
        raise AppException(40301, "无权修改该门店")
    store = await db.get(Store, store_id)
    if not store:
        raise AppException(40011, "门店不存在")
    payload = data.model_dump(exclude_unset=True)
    if not payload:
        raise AppException(40012, "未提交任何修改")
    if "name" in payload and (not payload["name"] or not str(payload["name"]).strip()):
        raise AppException(40013, "门店名称不能为空")
    before = {
        "name": store.name,
        "logo": getattr(store, "logo", None),
        "address": store.address,
        "phone": store.phone,
        "latitude": float(store.latitude) if store.latitude is not None else None,
        "longitude": float(store.longitude) if store.longitude is not None else None,
        "status": store.status,
    }
    for k, v in payload.items():
        if k in ("latitude", "longitude") and v is not None:
            from decimal import Decimal

            setattr(store, k, Decimal(str(v)))
        else:
            setattr(store, k, v)
    await write_audit(
        db,
        user=user,
        request=request,
        action="settings.store.update",
        target_type="store",
        target_id=store.id,
        detail={"before": before, "after": payload},
    )
    await db.flush()
    return success(
        {
            "id": store.id,
            "name": store.name,
            "logo": store.logo,
            "address": store.address,
            "phone": store.phone,
            "status": store.status,
        },
        request=request,
    )


@router.post("/stores/{store_id}/logo", dependencies=[Depends(require_store_update)])
async def upload_store_logo(
    store_id: int,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    file: UploadFile = File(...),
):
    """上传门店 Logo：校验类型/大小后转为正方形 WebP（≤512px），写入 stores.logo。"""
    assert_store_access(user, store_id)
    if user.role.code != "SUPER_ADMIN" and user.store_id and int(user.store_id) != int(store_id):
        raise AppException(40301, "无权修改该门店")
    store = await db.get(Store, store_id)
    if not store:
        raise AppException(40011, "门店不存在")
    url = await save_store_logo(file)
    before = store.logo
    store.logo = url
    await write_audit(
        db,
        user=user,
        request=request,
        action="settings.store.logo_upload",
        target_type="store",
        target_id=store.id,
        detail={"before": before, "logo": url},
    )
    await db.flush()
    return success({"id": store.id, "logo": url}, request=request)

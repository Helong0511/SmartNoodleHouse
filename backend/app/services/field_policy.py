"""字段级权限：按角色权限裁剪响应中的敏感字段。"""
from __future__ import annotations

from typing import Any, Iterable


# 字段名 → 所需权限（任一命中即可保留；超管绕过）
FIELD_PERMISSION_GATES: dict[str, frozenset[str]] = {
    "cost_price": frozenset({"product:cost:read"}),
    "phone": frozenset({"customer:pii", "customer:manage"}),
    "id_card_no": frozenset({"customer:pii", "customer:manage"}),
    "openid": frozenset({"customer:pii", "customer:manage"}),
    "unionid": frozenset({"customer:pii", "customer:manage"}),
    "alipay_user_id": frozenset({"customer:pii", "customer:manage"}),
    "birthday": frozenset({"customer:pii", "customer:manage"}),
    "birthday_picker": frozenset({"customer:pii", "customer:manage"}),
    "birthday_calendar": frozenset({"customer:pii", "customer:manage"}),
    "birthday_display": frozenset({"customer:pii", "customer:manage"}),
    "birthday_locked": frozenset({"customer:pii", "customer:manage"}),
}


def mask_phone(phone: str | None) -> str | None:
    if not phone:
        return phone
    s = str(phone).strip()
    if len(s) < 7:
        return "***"
    return f"{s[:3]}****{s[-4:]}"


def apply_field_policy(
    payload: dict[str, Any],
    permissions: Iterable[str],
    *,
    is_super_admin: bool = False,
    mask_instead_of_drop: frozenset[str] | None = None,
) -> dict[str, Any]:
    """就地裁剪/脱敏敏感字段后返回同一 dict。"""
    if is_super_admin:
        return payload
    perms = set(permissions or [])
    mask_fields = mask_instead_of_drop or frozenset({"phone"})
    for field, needed in FIELD_PERMISSION_GATES.items():
        if field not in payload:
            continue
        if perms & needed:
            continue
        if field in mask_fields and field == "phone":
            payload[field] = mask_phone(payload.get(field))
        else:
            payload.pop(field, None)
    return payload


def apply_field_policy_list(
    items: list[dict[str, Any]],
    permissions: Iterable[str],
    *,
    is_super_admin: bool = False,
) -> list[dict[str, Any]]:
    return [
        apply_field_policy(it, permissions, is_super_admin=is_super_admin) for it in items
    ]



def apply_customer_detail_policy(
    payload: dict[str, Any],
    permissions: Iterable[str],
    *,
    is_super_admin: bool = False,
) -> dict[str, Any]:
    """裁剪顾客详情中的顶层及渠道账号嵌套敏感字段。"""
    apply_field_policy(payload, permissions, is_super_admin=is_super_admin)
    if is_super_admin:
        return payload
    for key in ("wechat_accounts", "alipay_accounts"):
        accounts = payload.get(key)
        if isinstance(accounts, list):
            payload[key] = [
                apply_field_policy(item, permissions)
                for item in accounts
                if isinstance(item, dict)
            ]
    return payload

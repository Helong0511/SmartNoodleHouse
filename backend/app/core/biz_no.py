"""业务单号：业务2位 + 门店编号(4位) + 桌位3位 + yyyyMMddHHmmss + 4位随机（总长27）。

门店段取实际 store_no（字母数字大写，固定 4 位：不足右补 0，超长截前 4）。
示例：OTSH01A0120260827173045K7M2
微信 out_trade_no ≤32，本格式恒为27，安全。
"""
from __future__ import annotations

import re
import secrets
from typing import Literal

from app.core.response import now_local

# 去掉易混 O/0/I/1
_RAND_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"

BizCode = Literal["OT", "OD", "PW", "PA", "RW", "RA", "CU", "VI"]

BIZ_OT = "OT"  # 堂食订单
BIZ_OD = "OD"  # 外卖订单
BIZ_PW = "PW"  # 微信支付
BIZ_PA = "PA"  # 支付宝支付
BIZ_RW = "RW"  # 微信退款
BIZ_RA = "RA"  # 支付宝退款
BIZ_CU = "CU"  # 顾客
BIZ_VI = "VI"  # 访客

TABLE_NONE = "000"
TABLE_DELIVERY = "DLV"
TABLE_TAKEAWAY = "TKW"
STORE_NONE = "0000"


def store_code(store_no: str | None = None) -> str:
    """门店段固定 4 位：实际 store_no 去符号后大写；空则 0000。"""
    raw = re.sub(r"[^A-Za-z0-9]", "", (store_no or "").strip().upper())
    if not raw:
        return STORE_NONE
    if len(raw) >= 4:
        return raw[:4]
    return raw.ljust(4, "0")


def table_code(table_no: str | None = None, *, placeholder: str = TABLE_NONE) -> str:
    """桌位段固定 3 位大写：取字母数字截/右补 X；空则用占位。"""
    raw = re.sub(r"[^A-Za-z0-9]", "", (table_no or "").strip().upper())
    if not raw:
        ph = (placeholder or TABLE_NONE).upper()[:3]
        return ph.ljust(3, "0")[:3]
    if len(raw) >= 3:
        return raw[:3]
    return raw.ljust(3, "X")


def payment_biz(provider: str) -> str:
    p = (provider or "").upper()
    if p == "ALIPAY":
        return BIZ_PA
    return BIZ_PW  # WECHAT 及其他默认微信码位，避免未知渠道无码


def refund_biz(provider: str) -> str:
    p = (provider or "").upper()
    if p == "ALIPAY":
        return BIZ_RA
    return BIZ_RW


def _rand4() -> str:
    return "".join(secrets.choice(_RAND_ALPHABET) for _ in range(4))


def generate_no(
    biz: str,
    store_no: str | None = None,
    table_no: str | None = None,
    *,
    table_placeholder: str = TABLE_NONE,
) -> str:
    """生成业务单号。biz 为 2 位业务码；store_no 为门店编号（如 SH01）。

    格式：{biz}{store4}{table3}{yyyyMMddHHmmss}{rand4}
    """
    code = (biz or "").strip().upper()
    if len(code) != 2 or not code.isalpha():
        raise ValueError(f"业务码须为2位字母，收到: {biz!r}")
    ts = now_local().strftime("%Y%m%d%H%M%S")
    return f"{code}{store_code(store_no)}{table_code(table_no, placeholder=table_placeholder)}{ts}{_rand4()}"


def resolve_table_parts(
    table_no: str | None = None,
    *,
    channel: str | None = None,
    dining_type: str | None = None,
    is_delivery: bool = False,
) -> tuple[str | None, str]:
    """返回 (table_no, placeholder)：有桌用桌号；外卖 DLV；外带无桌 TKW；否则 000。"""
    if table_no and str(table_no).strip():
        return str(table_no).strip(), TABLE_NONE
    ch = (channel or "").upper()
    if is_delivery or ch in ("MEITUAN", "ELEME"):
        return None, TABLE_DELIVERY
    if (dining_type or "").upper() == "TAKEAWAY":
        return None, TABLE_TAKEAWAY
    return None, TABLE_NONE


def parse_biz_no(no: str) -> dict | None:
    """解析新格式单号；旧格式返回 None。"""
    s = (no or "").strip().upper()
    if len(s) != 27:
        return None
    biz, store, table, ts, rand = s[:2], s[2:6], s[6:9], s[9:23], s[23:27]
    if not (biz.isalpha() and store.isalnum() and ts.isdigit() and len(rand) == 4):
        return None
    return {"biz": biz, "store": store, "table": table, "datetime": ts, "rand": rand}

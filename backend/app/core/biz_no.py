"""业务单号生成。

【订单/支付/退款】总长 27：
  {biz2}{store4}{table3}{yyyyMMddHHmmss}{rand4}
  示例：OTSH01A0120260827173045K7M2
  微信 out_trade_no ≤32，本格式恒为 27，安全。

【顾客/访客】总长 20（无门店桌位段，避免 CU0000000…）：
  {biz2}{yyyyMMddHHmmss}{rand4}
  示例：CU20260907121105EG4W

随机段与门店/桌位填充避开易混字符 O/0/I/1（填充用 X）。
"""
from __future__ import annotations

import re
import secrets
from typing import Literal

from app.core.response import now_local

# 去掉易混 O/0/I/1
_RAND_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
# 门店/桌位占位与右补，不用 0（避免与字母 O 混淆）
_PAD = "X"

BizCode = Literal["OT", "OD", "PW", "PA", "RW", "RA", "CU", "VI"]

BIZ_OT = "OT"  # 堂食订单
BIZ_OD = "OD"  # 外卖订单
BIZ_PW = "PW"  # 微信支付
BIZ_PA = "PA"  # 支付宝支付
BIZ_RW = "RW"  # 微信退款
BIZ_RA = "RA"  # 支付宝退款
BIZ_CU = "CU"  # 顾客
BIZ_VI = "VI"  # 访客

# 顾客/访客：无门店桌位的紧凑单号
_COMPACT_BIZ = frozenset({BIZ_CU, BIZ_VI})

TABLE_NONE = "XXX"
TABLE_DELIVERY = "DLV"
TABLE_TAKEAWAY = "TKW"
STORE_NONE = "XXXX"


def store_code(store_no: str | None = None) -> str:
    """门店段固定 4 位：实际 store_no 去符号后大写；空则 XXXX。"""
    raw = re.sub(r"[^A-Za-z0-9]", "", (store_no or "").strip().upper())
    # 去掉易混字母，降低口播/手抄误识（数字保留，门店号常含 0/1）
    raw = raw.replace("O", "Q").replace("I", "J")
    if not raw:
        return STORE_NONE
    if len(raw) >= 4:
        return raw[:4]
    return raw.ljust(4, _PAD)


def table_code(table_no: str | None = None, *, placeholder: str = TABLE_NONE) -> str:
    """桌位段固定 3 位大写：取字母数字截/右补 X；空则用占位。"""
    raw = re.sub(r"[^A-Za-z0-9]", "", (table_no or "").strip().upper())
    raw = raw.replace("O", "Q").replace("I", "J")
    if not raw:
        ph = re.sub(r"[^A-Za-z0-9]", "", (placeholder or TABLE_NONE).upper())[:3]
        return (ph or TABLE_NONE).ljust(3, _PAD)[:3]
    if len(raw) >= 3:
        return raw[:3]
    return raw.ljust(3, _PAD)


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

    - 顾客/访客 CU/VI：{biz}{yyyyMMddHHmmss}{rand4}（20 位）
    - 其余：{biz}{store4}{table3}{yyyyMMddHHmmss}{rand4}（27 位）
    """
    code = (biz or "").strip().upper()
    if len(code) != 2 or not code.isalpha():
        raise ValueError(f"业务码须为2位字母，收到: {biz!r}")
    ts = now_local().strftime("%Y%m%d%H%M%S")
    if code in _COMPACT_BIZ:
        return f"{code}{ts}{_rand4()}"
    return f"{code}{store_code(store_no)}{table_code(table_no, placeholder=table_placeholder)}{ts}{_rand4()}"


def resolve_table_parts(
    table_no: str | None = None,
    *,
    channel: str | None = None,
    dining_type: str | None = None,
    is_delivery: bool = False,
) -> tuple[str | None, str]:
    """返回 (table_no, placeholder)：有桌用桌号；外卖 DLV；外带无桌 TKW；否则 XXX。"""
    if table_no and str(table_no).strip():
        return str(table_no).strip(), TABLE_NONE
    ch = (channel or "").upper()
    if is_delivery or ch in ("MEITUAN", "ELEME"):
        return None, TABLE_DELIVERY
    if (dining_type or "").upper() == "TAKEAWAY":
        return None, TABLE_TAKEAWAY
    return None, TABLE_NONE


def parse_biz_no(no: str) -> dict | None:
    """解析单号；无法识别返回 None。

    支持：新 27 位标准单号；CU/VI 20 位紧凑单号；旧 CU 27 位（含 0000000 占位）。
    """
    s = (no or "").strip().upper()
    if len(s) == 20 and s[:2] in _COMPACT_BIZ:
        biz, ts, rand = s[:2], s[2:16], s[16:20]
        if biz.isalpha() and ts.isdigit() and len(rand) == 4:
            return {"biz": biz, "store": None, "table": None, "datetime": ts, "rand": rand, "compact": True}
        return None
    if len(s) != 27:
        return None
    biz, store, table, ts, rand = s[:2], s[2:6], s[6:9], s[9:23], s[23:27]
    if not (biz.isalpha() and store.isalnum() and ts.isdigit() and len(rand) == 4):
        return None
    return {"biz": biz, "store": store, "table": table, "datetime": ts, "rand": rand, "compact": False}

"""局域网 ESC/POS 热敏打印（芯烨 XP-80T USB+WiFi 等，TCP 9100，80mm）。"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

ESC = b"\x1b"
GS = b"\x1d"
_INIT = ESC + b"@"
# 稍紧行距，减少小票头部「空走纸」观感（单位：点）
_LINE_SPACING = ESC + b"3\x2c"
_ALIGN_LEFT = ESC + b"a\x00"
_ALIGN_CENTER = ESC + b"a\x01"
_BOLD_ON = ESC + b"E\x01"
_BOLD_OFF = ESC + b"E\x00"
_SIZE_NORMAL = GS + b"!\x00"
_SIZE_DOUBLE = GS + b"!\x11"
# 走纸 n 单位后再切（Epson/芯烨 GS V 65 n）；XP-80T 只发这一刀，避免双切
_CUT_FEED_THEN = GS + b"V\x41"  # + 1 byte n
_LF = b"\n"

_TAG_RE = re.compile(
    r"</?(?:C|B|B2|BOLD|BR|CUT|BARCODE(?:\s+[^>]*)?|QR(?:\s+[^>]*)?)>",
    re.IGNORECASE,
)
_BARCODE_RE = re.compile(
    r"<BARCODE(?:\s+[^>]*)?>([^<]+)</BARCODE>",
    re.IGNORECASE,
)
_QR_RE = re.compile(
    r"<QR(?:\s+[^>]*)?>([^<]+)</QR>",
    re.IGNORECASE,
)
_BARCODE_ATTR_RE = re.compile(
    r'\b([whps])\s*=\s*"?(\d+)"?',
    re.IGNORECASE,
)


def _encode_text(text: str, encoding: str = "gbk") -> bytes:
    return (text or "").encode(encoding, errors="replace")


def _code128_body(data: str) -> bytes:
    """ESC/POS CODE128 数据区：自动 {B}/{C}。

    芯烨 GS w 仅支持 2~6；27 位订单号在 w=2 下必须用 Code C 压缩中间 14 位时间戳，
    否则超宽会被整段丢弃（小票上只剩文字单号）。
    """
    s = re.sub(r"[^A-Za-z0-9]", "", (data or "").strip())
    if not s:
        return b""
    out = bytearray()
    charset: str | None = None
    i = 0

    def select(cs: str) -> None:
        nonlocal charset
        if charset != cs:
            out.extend(b"{" + cs.encode("ascii"))
            charset = cs

    while i < len(s):
        j = i
        while j < len(s) and s[j].isdigit():
            j += 1
        run = j - i
        # ≥4 位连续数字用 Code C；已在 C 时 ≥2 位也继续 C
        if run >= 4 or (charset == "C" and run >= 2):
            if run % 2 == 1:
                select("B")
                out.append(ord(s[i]))
                i += 1
                run -= 1
            if run >= 2:
                select("C")
                while run >= 2:
                    out.append(int(s[i : i + 2]))
                    i += 2
                    run -= 2
            continue
        select("B")
        out.append(ord(s[i]))
        i += 1
    return bytes(out) if out else b"{B0"


def _code128_module_count(body: bytes) -> int:
    """估算 CODE128 模块数（含校验与终止符），用于判断 80mm 是否放得下。"""
    symbols = 0
    i = 0
    while i < len(body):
        if body[i : i + 1] == b"{" and i + 1 < len(body) and body[i + 1] in b"ABC":
            symbols += 1
            i += 2
            continue
        if body[i : i + 2] == b"{{":
            symbols += 1
            i += 2
            continue
        symbols += 1
        i += 1
    # symbols 已含起始集选择；再加校验符；终止符 13 模块
    return symbols * 11 + 11 + 13


def _barcode_code128(data: str, *, module_w: int = 2, height: int = 64, hri: int = 0) -> bytes:
    """ESC/POS CODE128。芯烨模块宽须 2~6；订单号用 B/C 压缩后按 w=2 打印。"""
    raw = _code128_body(data)
    if not raw:
        return b""
    # 芯烨/Epson：n=2~6；w=1 非法会被整段忽略
    w = max(2, min(int(module_w or 2), 6))
    # 仍超宽则缩短为「时间戳14+随机4」（业务单号后 18 位，仍可唯一检索）
    printable = 560  # 80mm 约 576 点，留边
    if _code128_module_count(raw) * w > printable:
        compact = re.sub(r"[^A-Za-z0-9]", "", (data or "").strip())
        if len(compact) >= 18:
            raw = _code128_body(compact[-18:])
        if _code128_module_count(raw) * w > printable:
            raw = _code128_body(compact[-16:] if len(compact) >= 16 else compact)
    if len(raw) > 255:
        raw = raw[:255]
    h = max(40, min(int(height or 64), 120))
    hri_pos = max(0, min(int(hri if hri is not None else 0), 3))
    out = bytearray()
    out.extend(GS + b"h" + bytes([h]))
    out.extend(GS + b"w" + bytes([w]))
    out.extend(GS + b"H" + bytes([hri_pos]))
    out.extend(GS + b"f\x00")
    out.extend(GS + b"kI" + bytes([len(raw)]) + raw)
    out.extend(_LF)
    return bytes(out)


def _qr_code(data: str, *, module_size: int = 5, ec_level: int = 49) -> bytes:
    """ESC/POS 二维码（Epson/芯烨兼容 GS ( k）。ec: 48=L 49=M 50=Q 51=H。"""
    raw = (data or "").strip().encode("ascii", errors="ignore")
    if not raw:
        return b""
    size = max(3, min(int(module_size or 5), 8))
    ec = max(48, min(int(ec_level or 49), 51))
    out = bytearray()
    # CN=49 QR, FN=65 规格
    out.extend(GS + b"(k\x04\x00\x31\x41\x32\x00")
    # FN=67 模块大小
    out.extend(GS + b"(k\x03\x00\x31\x43" + bytes([size]))
    # FN=69 纠错
    out.extend(GS + b"(k\x03\x00\x31\x45" + bytes([ec]))
    # FN=80 存数据
    store = bytes([0x31, 0x50, 0x30]) + raw
    pL = len(store) & 0xFF
    pH = (len(store) >> 8) & 0xFF
    out.extend(GS + b"(k" + bytes([pL, pH]) + store)
    # FN=81 打印
    out.extend(GS + b"(k\x03\x00\x31\x51\x30")
    out.extend(_LF)
    return bytes(out)


def _parse_barcode_attrs(tag_open: str) -> tuple[int, int, int]:
    """从 <BARCODE w=2 h=64 p=0> 解析宽/高/HRI（芯烨 w 仅 2~6）。"""
    w, h, p = 2, 64, 0
    for key, val in _BARCODE_ATTR_RE.findall(tag_open or ""):
        n = int(val)
        k = key.lower()
        if k == "w":
            w = n
        elif k == "h":
            h = n
        elif k == "p":
            p = n
    return w, h, p


def _parse_qr_size(tag_open: str) -> int:
    size = 5
    for key, val in _BARCODE_ATTR_RE.findall(tag_open or ""):
        if key.lower() == "s":
            size = int(val)
    return size


def _append_cut(buf: bytearray) -> None:
    """条码出刀口后再切：XP-80T 刀距约 12~15mm；只发一次走纸切，避免双切。"""
    buf.extend(_LF * 2)
    buf.extend(_CUT_FEED_THEN + bytes([18]))


def _has_cut(buf: bytes | bytearray) -> bool:
    """是否已含切纸指令（全切或走纸切）。"""
    return (GS + b"V") in bytes(buf)


def xpyun_markup_to_escpos(content: str, *, encoding: str = "gbk") -> bytes:
    """将芯烨云标签小票内容转为 ESC/POS 字节流（XP-80T / 80mm）。"""
    buf = bytearray(_INIT)
    buf.extend(_LINE_SPACING)
    leading = True
    for raw_line in (content or "").split("<BR>"):
        line = raw_line.strip()
        if not line:
            # 跳过票头前导空行，减少顶部空白
            if leading:
                continue
            buf.extend(_LF)
            continue
        leading = False

        has_cut = "<CUT>" in line.upper()
        line = re.sub(r"<CUT>", "", line, flags=re.IGNORECASE).strip()
        if not line and has_cut:
            _append_cut(buf)
            continue

        centered = bool(re.search(r"<C>", line, re.IGNORECASE))
        double = bool(re.search(r"<B2>", line, re.IGNORECASE))
        bold = bool(re.search(r"<B(?:OLD)?>", line, re.IGNORECASE))

        qr_match = _QR_RE.search(line)
        barcode_match = _BARCODE_RE.search(line)
        if qr_match:
            before = line[: qr_match.start()]
            after = line[qr_match.end() :]
            tag_open = line[qr_match.start() : qr_match.end()].split(">", 1)[0]
            if before.strip():
                buf.extend(_render_text_line(before, centered, bold, double, encoding))
            buf.extend(_ALIGN_CENTER)
            buf.extend(_qr_code(qr_match.group(1), module_size=_parse_qr_size(tag_open)))
            if after.strip():
                buf.extend(_render_text_line(after, centered, bold, double, encoding))
        elif barcode_match:
            before = line[: barcode_match.start()]
            after = line[barcode_match.end() :]
            tag_chunk = line[barcode_match.start() : barcode_match.end()]
            tag_open = tag_chunk.split(">", 1)[0]
            mod_w, height, hri = _parse_barcode_attrs(tag_open)
            if before.strip():
                buf.extend(_render_text_line(before, centered, bold, double, encoding))
            buf.extend(_ALIGN_CENTER)
            buf.extend(
                _barcode_code128(
                    barcode_match.group(1),
                    module_w=mod_w,
                    height=height,
                    hri=hri,
                )
            )
            if after.strip():
                buf.extend(_render_text_line(after, centered, bold, double, encoding))
        else:
            buf.extend(_render_text_line(line, centered, bold, double, encoding))

        if has_cut:
            _append_cut(buf)

    if not _has_cut(buf):
        _append_cut(buf)
    return bytes(buf)


_INLINE_B2_RE = re.compile(r"<B2>(.*?)</B2>", re.IGNORECASE | re.DOTALL)
# 注意：不要匹配 <B2>；仅 <B> / <BOLD>
_INLINE_B_RE = re.compile(r"<(?:B|BOLD)>(.*?)</(?:B|BOLD)>", re.IGNORECASE | re.DOTALL)


def _render_text_line(
    line: str,
    centered: bool,
    bold: bool,
    double: bool,
    encoding: str,
) -> bytes:
    raw = line or ""
    # 同行混排：时间普通字 + <B2>…</B2> 加大
    if _INLINE_B2_RE.search(raw) and _TAG_RE.sub("", _INLINE_B2_RE.sub("", raw)).strip():
        return _render_inline_styled_line(raw, centered, encoding, size_tag="B2")
    # 同行混排：时间靠左普通字 + <B>堂食 桌名</B> 加粗靠右
    if _INLINE_B_RE.search(raw) and _TAG_RE.sub("", _INLINE_B_RE.sub("", raw)).strip():
        return _render_inline_styled_line(raw, centered, encoding, size_tag="B")

    text = _TAG_RE.sub("", raw).strip()
    if not text:
        return b""
    out = bytearray()
    out.extend(_ALIGN_CENTER if centered else _ALIGN_LEFT)
    if double:
        out.extend(_SIZE_DOUBLE)
    if bold:
        out.extend(_BOLD_ON)
    out.extend(_encode_text(text, encoding))
    if bold:
        out.extend(_BOLD_OFF)
    if double:
        out.extend(_SIZE_NORMAL)
    out.extend(_LF)
    return bytes(out)


def _render_inline_styled_line(
    line: str,
    centered: bool,
    encoding: str,
    *,
    size_tag: str,
) -> bytes:
    """同一行内普通段与 <B>/<B2> 段混排（不换行，用于时间左、堂食右）。"""
    pattern = _INLINE_B2_RE if size_tag.upper() == "B2" else _INLINE_B_RE
    out = bytearray()
    out.extend(_ALIGN_CENTER if centered else _ALIGN_LEFT)
    pos = 0
    for m in pattern.finditer(line or ""):
        before = _TAG_RE.sub("", (line or "")[pos : m.start()])
        if before:
            out.extend(_SIZE_NORMAL)
            out.extend(_BOLD_OFF)
            out.extend(_encode_text(before, encoding))
        inner = _TAG_RE.sub("", m.group(1) or "")
        if inner:
            if size_tag.upper() == "B2":
                out.extend(_SIZE_DOUBLE)
            else:
                out.extend(_SIZE_NORMAL)
            out.extend(_BOLD_ON)
            out.extend(_encode_text(inner, encoding))
            out.extend(_BOLD_OFF)
            if size_tag.upper() == "B2":
                out.extend(_SIZE_NORMAL)
        pos = m.end()
    after = _TAG_RE.sub("", (line or "")[pos:])
    if after:
        out.extend(_SIZE_NORMAL)
        out.extend(_encode_text(after, encoding))
    out.extend(_LF)
    return bytes(out)


class EscposClient:
    def __init__(self, *, host: str, port: int = 9100, timeout: float = 8.0) -> None:
        self.host = (host or "").strip()
        self.port = int(port)
        self.timeout = float(timeout)

    @property
    def configured(self) -> bool:
        return bool(self.host)

    async def print_ticket(self, content: str, *, copies: int = 1) -> dict[str, Any]:
        if not self.configured:
            raise RuntimeError("未配置打印机 IP（PRINTER_HOST）")
        payload = xpyun_markup_to_escpos(content)
        copies = max(1, min(int(copies), 5))
        for i in range(copies):
            try:
                _reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port),
                    timeout=self.timeout,
                )
            except (OSError, asyncio.TimeoutError) as exc:
                logger.warning("局域网打印机连接失败 host=%s port=%s err=%s", self.host, self.port, exc)
                raise RuntimeError(f"无法连接打印机 {self.host}:{self.port}") from exc
            try:
                writer.write(payload)
                await writer.drain()
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
            if i < copies - 1:
                await asyncio.sleep(0.3)
        return {"code": 0, "msg": "ok", "mode": "local", "host": self.host}

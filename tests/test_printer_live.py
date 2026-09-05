"""局域网热敏打印机联调脚本（芯烨 XP-80T 等，TCP 9100）。

设计原则
--------
- 完全独立：不读 .env，不 import 本仓库业务代码
- 直接组 ESC/POS 字节，经 TCP 发到打印机原始端口（默认 9100）

用法
----
  python tests/test_printer_live.py                  # 默认：字体/字号/粗细样张
  python tests/test_printer_live.py --ticket         # 原来的厨房票样
  python tests/test_printer_live.py --host 192.168.1.150
  python tests/test_printer_live.py --port 9100
  python tests/test_printer_live.py --check          # 只测连通，不出纸

改票样：build_escpos()；改字号样张：build_font_specimen()。
改默认 IP/端口：看 main() 里 argparse 的 default。
"""
from __future__ import annotations

import argparse
import socket
import sys
from datetime import datetime

# ---------------------------------------------------------------------------
# ESC/POS 常用控制字节（Epson / 芯烨兼容）
#   ESC = 0x1B   GS = 0x1D
# ---------------------------------------------------------------------------
ESC = b"\x1b"
GS = b"\x1d"

# 分隔线：80mm 纸、正常字号约 48 半角列；要铺满一行就保持 48 个 '-'
# 若你机器是 42 列模式，改成 42 个；偏短/偏长以实打为准
_LINE = "-" * 48


def check_tcp(host: str, port: int, timeout: float = 3.0) -> None:
    """探测打印机 TCP 是否可达。

    成功：能建立连接（函数正常返回）
    失败：抛 OSError（超时、拒绝、主机不可达等）
    """
    with socket.create_connection((host, port), timeout=timeout):
        pass


def _text(s: str) -> bytes:
    """中文热敏机常用 GBK 编码；无法编码的字符用替换，避免整单失败。"""
    return (s or "").encode("gbk", errors="replace")


def _display_width(s: str) -> int:
    """估算半角列宽：ASCII=1，中文等全角≈2（用于左右对齐算空格）。"""
    return sum(2 if ord(c) > 0x7F else 1 for c in s or "")


def _gs_size(width_mult: int, height_mult: int) -> bytes:
    """GS ! n：宽/高倍率 1~8 → n = ((高-1)<<4) | (宽-1)。"""
    w = max(1, min(int(width_mult), 8)) - 1
    h = max(1, min(int(height_mult), 8)) - 1
    return GS + b"!" + bytes([(h << 4) | w])


def _line_spacing(height_mult: int, *, font_dots: int = 24) -> bytes:
    """按字高设行距，避免倍高字被下一行盖住。"""
    n = min(255, int(font_dots) * max(1, int(height_mult)) + 10)
    return ESC + b"3" + bytes([n])


def _reset_style(out: bytearray) -> None:
    """恢复 Font A、1x1、不加粗、左对齐、默认行距。"""
    out.extend(ESC + b"M\x00")  # Font A
    out.extend(GS + b"!\x00")
    out.extend(ESC + b"E\x00")
    out.extend(ESC + b"G\x00")  # 叠打关
    out.extend(ESC + b"-\x00")  # 下划线关
    out.extend(ESC + b"a\x00")
    out.extend(ESC + b"3\x2c")


def _caption(out: bytearray, s: str) -> None:
    """样张说明行：始终用普通字，避免被上一档字号带跑。"""
    _reset_style(out)
    out.extend(_text(s))
    out.extend(b"\n")


def _sample(
    out: bytearray,
    text: str,
    *,
    font: int = 0,
    w: int = 1,
    h: int = 1,
    bold: bool = False,
    double_strike: bool = False,
) -> None:
    """打一行样本文字（指定字体/宽高/粗细），打完立刻复位。"""
    font_dots = 17 if font else 24
    out.extend(ESC + b"M" + bytes([0 if font == 0 else 1]))
    out.extend(_line_spacing(h, font_dots=font_dots))
    out.extend(_gs_size(w, h))
    out.extend(ESC + b"E" + (b"\x01" if bold else b"\x00"))
    out.extend(ESC + b"G" + (b"\x01" if double_strike else b"\x00"))
    out.extend(_text(text))
    out.extend(b"\n")
    _reset_style(out)


def build_font_specimen() -> bytes:
    """打印一张字体 / 字号 / 粗细对照样张（80mm）。

    指令对照（Epson / 芯烨 XP-80T）：
      ESC M n   字体：0=Font A(约12x24)  1=Font B(约9x17，更密更小)
      GS  ! n   宽高倍率：n=((高-1)<<4)|(宽-1)，各 1~8 倍
      ESC E n   强调加粗（厨房票 <B> 用这个）
      ESC G n   叠打加粗（另一种「黑」，可与 E 叠加）

    厨房票当前用法：标题 FontA + ESC E；菜名 GS ! 0x11（2x2）。
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out = bytearray()
    out.extend(ESC + b"@")
    _reset_style(out)

    # ---- 封面 ----
    out.extend(ESC + b"a\x01")
    out.extend(ESC + b"E\x01")
    out.extend(_text("字体 / 字号 / 粗细样张"))
    out.extend(ESC + b"E\x00")
    out.extend(b"\n")
    out.extend(_text(now))
    out.extend(b"\n")
    out.extend(ESC + b"a\x00")
    out.extend(_text(_LINE))
    out.extend(b"\n")

    # ---- 1) 字体 A / B ----
    _caption(out, "【1】字体  ESC M")
    _caption(out, "--- Font A  12x24  标准 ---")
    _sample(out, "正常  绵阳米粉 ABC123", font=0, bold=False)
    _sample(out, "加粗  绵阳米粉 ABC123", font=0, bold=True)
    _caption(out, "--- Font B  9x17  压缩 ---")
    _sample(out, "正常  绵阳米粉 ABC123", font=1, bold=False)
    _sample(out, "加粗  绵阳米粉 ABC123", font=1, bold=True)
    out.extend(_text(_LINE))
    out.extend(b"\n")

    # ---- 2) 字号（Font A）----
    # 80mm 约 576 点；4x 宽时中文大约一行 6 个字，样本文案要短
    sizes = [
        (1, 1, "1x1  n=00  正常字"),
        (1, 2, "1x2  n=10  只倍高"),
        (2, 1, "2x1  n=01  只倍宽"),
        (2, 2, "2x2  n=11  倍高倍宽=厨房菜名"),
        (2, 3, "2x3  n=21  宽2 高3"),
        (3, 2, "3x2  n=12  宽3 高2"),
        (3, 3, "3x3  n=22  宽3 高3"),
        (4, 2, "4x2  n=13  宽4 高2"),
        (4, 4, "4x4  n=33  宽4 高4"),
    ]
    _caption(out, "【2】字号  GS !  Font A")
    _caption(out, "每档：上行正常 / 下行加粗")
    for w, h, label in sizes:
        n = ((h - 1) << 4) | (w - 1)
        _caption(out, f"--- {label} (0x{n:02X}) ---")
        sample = "米粉x1" if w >= 3 else "绵阳米粉 x1"
        _sample(out, f"正常 {sample}", w=w, h=h, bold=False)
        _sample(out, f"加粗 {sample}", w=w, h=h, bold=True)
    out.extend(_text(_LINE))
    out.extend(b"\n")

    # ---- 3) 粗细对比（固定 2x2，最常用档）----
    _caption(out, "【3】粗细对比  固定 2x2")
    _caption(out, "--- 不加粗 ---")
    _sample(out, "米粉 x1", w=2, h=2, bold=False)
    _caption(out, "--- ESC E 强调加粗 ---")
    _sample(out, "米粉 x1", w=2, h=2, bold=True)
    _caption(out, "--- ESC G 叠打加粗 ---")
    _sample(out, "米粉 x1", w=2, h=2, double_strike=True)
    _caption(out, "--- E+G 强调+叠打 ---")
    _sample(out, "米粉 x1", w=2, h=2, bold=True, double_strike=True)
    out.extend(_text(_LINE))
    out.extend(b"\n")

    # ---- 4) 厨房票当前三档对照 ----
    _caption(out, "【4】厨房票当前实际用法")
    _caption(out, "--- 标题 1x1+加粗 居中 ---")
    out.extend(ESC + b"a\x01")
    out.extend(ESC + b"E\x01")
    out.extend(_text("SmartNoodleHouse 厨房制作单"))
    out.extend(ESC + b"E\x00")
    out.extend(b"\n")
    out.extend(ESC + b"a\x00")
    _caption(out, "--- 堂食桌名 1x1+加粗 ---")
    _sample(out, "堂食 2号桌", bold=True)
    _caption(out, "--- 菜名 2x2 不加粗 ---")
    _sample(out, "绵阳米粉 x1", w=2, h=2, bold=False)
    _caption(out, "--- 菜名 2x2+加粗（可改用）---")
    _sample(out, "绵阳米粉 x1", w=2, h=2, bold=True)
    _caption(out, "--- 选项 1x1 普通 ---")
    _sample(out, "  大碗、微辣、正常葱")

    out.extend(_text(_LINE))
    out.extend(b"\n")
    out.extend(ESC + b"a\x01")
    out.extend(_text("看完告诉我要用哪一档"))
    out.extend(b"\n")
    out.extend(ESC + b"a\x00")

    out.extend(b"\n\n")
    out.extend(GS + b"V\x41" + bytes([5]))
    return bytes(out)


def build_escpos() -> bytes:
    """组装一张测试小票的完整 ESC/POS 字节流（80mm）。

    票面结构（从上到下）：
      1) 标题：居中 + 加粗
      2) 空行
      3) 时间靠左 + 堂食/桌名靠右（同行）
      4) 分隔线
      5) 菜名倍高倍宽 + 选项普通字
      6) 分隔线
      7) CODE-128 条码 + 可读单号
      8) 走纸后切纸（只切一刀）

    想改文案/时间格式/条码内容：改本函数内对应字符串即可。
    """
    # 【时间格式】带秒；只要分可改成 "%Y-%m-%d %H:%M"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 条码内容宜短；过长在模块宽 w=2 时可能超纸宽被打印机丢弃
    sample_no = "PRINTTEST001"

    out = bytearray()

    # --- 初始化：清空状态，避免上次打印残留字号/对齐 ---
    out.extend(ESC + b"@")
    # --- 行距：n=0x2C（点）；数值越大行越疏 ---
    out.extend(ESC + b"3\x2c")

    # ========== 1) 标题：居中 + 加粗 ==========
    # ESC a n : 0=左 1=中 2=右
    out.extend(ESC + b"a\x01")
    # ESC E n : 1=加粗开 0=关
    out.extend(ESC + b"E\x01")
    out.extend(_text("打印机联调测试"))
    out.extend(ESC + b"E\x00")
    out.extend(b"\n\n")  # 标题下多空一行，避免与下一行贴太紧

    # ========== 2) 时间左 + 堂食右（同一行，约 48 列） ==========
    out.extend(ESC + b"a\x00")  # 左对齐
    left = f"时间：{now}"
    right = "堂食 2号桌"
    # 中间用空格撑开；cols 与分隔线一致（48）；印偏了可改这个数字
    cols = 48
    gap = max(1, cols - _display_width(left) - _display_width(right))
    out.extend(_text(left))
    out.extend(_text(" " * gap))
    out.extend(ESC + b"E\x01")  # 右侧加粗（字号仍正常，与标题同档）
    out.extend(_text(right))
    out.extend(ESC + b"E\x00")
    out.extend(b"\n")

    # ========== 3) 分隔线 ==========
    out.extend(_text(_LINE))
    out.extend(b"\n")

    # ========== 4) 菜名：倍高倍宽 ==========
    # GS ! n : 0x11 = 宽×2 高×2；打完要恢复 0x00，否则后面全变大
    out.extend(GS + b"!\x11")
    out.extend(_text("绵阳米粉（牛肉）    x1"))
    out.extend(GS + b"!\x00")
    out.extend(b"\n")
    # 选项：普通字号，前面缩进两格
    out.extend(_text("  大碗、微辣、正常葱"))
    out.extend(b"\n")
    out.extend(_text(_LINE))
    out.extend(b"\n")

    # ========== 5) CODE-128 条码 ==========
    # 数据区须带字符集前缀：{A / {B / {C；字母数字用 {B
    # 芯烨模块宽 GS w n 合法范围一般是 2~6（不要用 1，整段可能被丢弃）
    payload = b"{B" + sample_no.encode("ascii")
    out.extend(ESC + b"a\x01")  # 条码居中
    out.extend(GS + b"h" + bytes([64]))  # 高度（点），可调 40~120
    out.extend(GS + b"w" + bytes([2]))  # 模块宽，最小建议 2
    out.extend(GS + b"H" + bytes([0]))  # HRI：0=条码旁不打字（下面另打可读号）
    # GS k m n d… : m=73(I) 表示 CODE128；n=后续数据字节数
    out.extend(GS + b"kI" + bytes([len(payload)]) + payload)
    out.extend(b"\n")
    out.extend(_text(sample_no))  # 人眼可读单号
    out.extend(b"\n")

    # ========== 6) 切纸（只发一刀，避免「切两下」） ==========
    # 先走纸，让条码离开刀口；刀在打印头下方约 12~15mm
    out.extend(b"\n\n")
    # GS V 65 n : 走纸 n 单位后再切；n 越大尾部空白越多（当前 18）
    out.extend(GS + b"V\x41" + bytes([5]))
    # 注意：不要再追加 GS V 0 全切，否则会感觉切了两刀
    return bytes(out)


def send(host: str, port: int, payload: bytes, timeout: float = 8.0) -> None:
    """TCP 连上打印机后一次性 sendall 整票数据。"""
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(payload)


def main(argv: list[str] | None = None) -> int:
    """命令行入口。返回值：0 成功，1 网络/打印失败。"""
    parser = argparse.ArgumentParser(description="局域网 ESC/POS 打印机联调（独立脚本）")
    parser.add_argument(
        "--host",
        default="192.168.1.150",  # 【改默认 IP】只改这里
        help="打印机 IP，默认 192.168.1.150",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9100,  # 【改默认端口】一般 9100 不用动
        help="端口，默认 9100",
    )
    parser.add_argument("--check", action="store_true", help="只测 TCP，不出纸")
    parser.add_argument(
        "--ticket",
        action="store_true",
        help="打原来的厨房票样（默认打字体/字号/粗细样张）",
    )
    args = parser.parse_args(argv)

    host = (args.host or "").strip() or "192.168.1.150"
    port = int(args.port)

    print(f"目标打印机 {host}:{port}")
    try:
        check_tcp(host, port)
        print("TCP 连通：OK")
    except OSError as exc:
        print(f"TCP 连通：失败 — {exc}")
        print("请检查：开机、同网段、IP 是否变更、防火墙、路由器 AP 隔离")
        return 1

    if args.check:
        return 0

    if args.ticket:
        payload = build_escpos()
        kind = "厨房票样"
    else:
        payload = build_font_specimen()
        kind = "字体/字号/粗细样张"
    print(f"正在出{kind}（{len(payload)} 字节）…")
    try:
        send(host, port, payload)
    except OSError as exc:
        print(f"打印失败：{exc}")
        return 1
    print("打印成功")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

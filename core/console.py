#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Giao diện console (CMD / PowerShell / Terminal) cho KHOA AN TOOLS.

- Bật ANSI 24-bit trên Windows 10/11 (SetConsoleMode)
- Banner gradient + ASCII art, panel thông tin khởi động
- Log màu:  giờ  ● INFO  module │ nội dung
- Dòng sự kiện thật khi tải file:  ⬇ TIKTOK  @user · video · 2.4 MB   ✔ 1.8s
- Tự hạ cấp sang ASCII khi console không dùng được UTF-8 (cmd.exe cp1258)

Chỉ dùng thư viện chuẩn — không thêm dependency nào.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sys
import time
import unicodedata
from contextlib import contextmanager

RESET = "\033[0m"

# ------------------------------------------------------------------ màu
PALETTE = {
    "cyan": "#22d3ee",
    "violet": "#a855f7",
    "pink": "#f472b6",
    "green": "#34d399",
    "amber": "#fbbf24",
    "red": "#f87171",
    "blue": "#60a5fa",
    "slate": "#94a3b8",
    "dark": "#475569",
    "white": "#e2e8f0",
    "line": "#6f7ef8",
    "teal": "#2dd4bf",
    "indigo": "#818cf8",
}

SITE_COLOR = {
    "tiktok": PALETTE["pink"],
    "instagram": PALETTE["violet"],
    "facebook": PALETTE["blue"],
    "youtube": PALETTE["red"],
    "music": PALETTE["cyan"],
    "bot": PALETTE["green"],
}

_STATE = {"color": None, "utf8": None}


def _enable_windows_vt() -> None:
    """Windows 10+ : bật xử lý escape sequence cho console hiện tại."""
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel = ctypes.windll.kernel32
        handle = kernel.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:  # noqa: BLE001
        pass


def color_on() -> bool:
    """Màu bật khi là terminal thật, hoặc khi có FORCE_COLOR (log xuất ra file/CI)."""
    if _STATE["color"] is None:
        _enable_windows_vt()
        forced = os.getenv("FORCE_COLOR") not in (None, "", "0", "false")
        _STATE["color"] = forced or (
            bool(getattr(sys.stdout, "isatty", lambda: False)()) and not os.getenv("NO_COLOR")
        )
    return bool(_STATE["color"])


def fix_stdout() -> None:
    """cmd.exe cp1258: tắt UTF-8 + cho phép thay ký tự lạ, để banner không mất.

    v18: dò bằng encode() chứ KHÔNG ghi thử ra màn hình (bản cũ in rác "╭█").
    """
    enc = getattr(sys.stdout, "encoding", "") or "ascii"
    try:
        "\u256d\u2588\u2500".encode(enc)
        return
    except (UnicodeEncodeError, LookupError):
        _STATE["utf8"] = False
    except Exception:  # noqa: BLE001
        return
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass


def utf8_ok() -> bool:
    """Console có vẽ được box/emoji không (cmd.exe mặc định cp1258 -> không)."""
    if _STATE["utf8"] is None:
        enc = (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "")
        _STATE["utf8"] = ("utf" in enc) and bool(
            os.getenv("WT_SESSION") or os.name != "nt" or "utf" in enc
        )
    return bool(_STATE["utf8"])


def _hex_rgb(value: str) -> tuple[int, int, int]:
    v = (value or "").lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    try:
        return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)
    except Exception:  # noqa: BLE001
        return 200, 200, 200


def fg(value: str) -> str:
    r, g, b = _hex_rgb(value)
    return f"\033[38;2;{r};{g};{b}m"


def style(text: str, color: str | None = None, *, bold: bool = False,
          dim: bool = False) -> str:
    """Tô màu 1 đoạn text (tự bỏ qua nếu console không hỗ trợ)."""
    text = "" if text is None else str(text)
    if not color_on():
        return text
    pre = ""
    if bold:
        pre += "\033[1m"
    if dim:
        pre += "\033[2m"
    if color:
        pre += fg(color)
    return f"{pre}{text}{RESET}" if pre else text


def grad(text: str, start: str = PALETTE["cyan"], end: str = PALETTE["violet"]) -> str:
    """Chuyển màu từng ký tự (gradient ngang)."""
    text = "" if text is None else str(text)
    if not color_on() or not text:
        return text
    r1, g1, b1 = _hex_rgb(start)
    r2, g2, b2 = _hex_rgb(end)
    n = max(len(text) - 1, 1)
    out = []
    for i, ch in enumerate(text):
        t = i / n
        out.append(
            f"\033[38;2;{int(r1 + (r2 - r1) * t)};{int(g1 + (g2 - g1) * t)};"
            f"{int(b1 + (b2 - b1) * t)}m{ch}"
        )
    return "".join(out) + RESET


# ------------------------------------------------------------------ ký tự vẽ khung
def _chars() -> dict[str, str]:
    if utf8_ok():
        return {
            "tl": "╭", "tr": "╮", "bl": "╰", "br": "╯", "h": "─", "v": "│",
            "dot": "●", "diamond": "◆", "check": "✔", "cross": "✖", "warn": "▲",
            "arrow": "▸", "dl": "↓", "bullet": "•", "spark": "✦",
            "ltee": "├", "rtee": "┤", "bar": "▐",
            "tl2": "╔", "tr2": "╗", "bl2": "╚", "br2": "╝",
            "ltee2": "╠", "rtee2": "╣", "lthin": "╟", "rthin": "╢",
            "v2": "║", "dbl": "═", "thin": "─", "shade": "▁",
            "pip": "▍", "glow": "▔",
        }
    return {
        "tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|",
        "dot": "*", "diamond": "*", "check": "OK", "cross": "X", "warn": "!",
        "arrow": ">", "dl": "v", "bullet": "-", "spark": "*",
        "ltee": "+", "rtee": "+", "bar": "|",
        "tl2": "+", "tr2": "+", "bl2": "+", "br2": "+",
        "ltee2": "+", "rtee2": "+", "lthin": "+", "rthin": "+",
        "v2": "|", "dbl": "=", "thin": "-", "shade": "_",
        "pip": "#", "glow": "-",
    }


# ------------------------------------------------------------------ banner
# Font logo 6 dòng. SHADOW = khối có bóng (cần UTF-8), BLOCK = ASCII an toàn.
SHADOW = {
    "K": ("██╗  ██╗", "██║ ██╔╝", "█████╔╝ ", "██╔═██╗ ", "██║  ██╗", "╚═╝  ╚═╝"),
    "H": ("██╗  ██╗", "██║  ██║", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"),
    "O": (" ██████╗ ", "██╔═══██╗", "██║   ██║", "██║   ██║", "╚██████╔╝", " ╚═════╝ "),
    "A": (" █████╗ ", "██╔══██╗", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"),
    "N": ("███╗   ██╗", "████╗  ██║", "██╔██╗ ██║", "██║╚██╗██║", "██║ ╚████║", "╚═╝  ╚═══╝"),
    "T": ("████████╗", "╚══██╔══╝", "   ██║   ", "   ██║   ", "   ██║   ", "   ╚═╝   "),
    "S": ("███████╗", "██╔════╝", "███████╗", "╚════██║", "███████║", "╚══════╝"),
    "L": ("██╗     ", "██║     ", "██║     ", "██║     ", "███████╗", "╚══════╝"),
    "V": ("██╗   ██╗", "██║   ██║", "██║   ██║", "╚██╗ ██╔╝", " ╚████╔╝ ", "  ╚═══╝  "),
    "I": ("██╗", "██║", "██║", "██║", "██║", "╚═╝"),
    "E": ("███████╗", "██╔════╝", "█████╗  ", "██╔══╝  ", "███████╗", "╚══════╝"),
    "W": ("██╗    ██╗", "██║    ██║", "██║ █╗ ██║", "██║███╗██║", "╚███╔███╔╝", " ╚══╝╚══╝ "),
    "C": (" ██████╗", "██╔════╝", "██║     ", "██║     ", "╚██████╗", " ╚═════╝"),
    "G": (" ██████╗ ", "██╔════╝ ", "██║  ███╗", "██║   ██║", "╚██████╔╝", " ╚═════╝ "),
    "B": ("██████╗ ", "██╔══██╗", "██████╔╝", "██╔══██╗", "██████╔╝", "╚═════╝ "),
    "F": ("███████╗", "██╔════╝", "█████╗  ", "██╔══╝  ", "██║     ", "╚═╝     "),
    "Y": ("██╗   ██╗", "╚██╗ ██╔╝", " ╚████╔╝ ", "  ╚██╔╝  ", "   ██║   ", "   ╚═╝   "),
    "R": ("██████╗ ", "██╔══██╗", "██████╔╝", "██╔══██╗", "██║  ██║", "╚═╝  ╚═╝"),
    "P": ("██████╗ ", "██╔══██╗", "██████╔╝", "██╔═══╝ ", "██║     ", "╚═╝     "),
    "D": ("██████╗ ", "██╔══██╗", "██║  ██║", "██║  ██║", "██████╔╝", "╚═════╝ "),
    "U": ("██╗   ██╗", "██║   ██║", "██║   ██║", "██║   ██║", "╚██████╔╝", " ╚═════╝ "),
    "M": ("███╗   ███╗", "████╗ ████║", "██╔████╔██║", "██║╚██╔╝██║", "██║ ╚═╝ ██║", "╚═╝     ╚═╝"),
    " ": ("    ", "    ", "    ", "    ", "    ", "    "),
}

BLOCK = {
    "K": ("#   #", "#  # ", "###  ", "##   ", "#  # ", "#   #"),
    "H": ("#   #", "#   #", "#####", "#####", "#   #", "#   #"),
    "O": (" ### ", "#   #", "#   #", "#   #", "#   #", " ### "),
    "A": (" ### ", "#   #", "#####", "#   #", "#   #", "#   #"),
    "N": ("#   #", "##  #", "# # #", "#  ##", "#   #", "#   #"),
    "T": ("#####", "  #  ", "  #  ", "  #  ", "  #  ", "  #  "),
    "S": ("#### ", "#    ", " ### ", "    #", "#   #", " ####"),
    "L": ("#    ", "#    ", "#    ", "#    ", "#    ", "#####"),
    "V": ("#   #", "#   #", "#   #", " # # ", " # # ", "  #  "),
    "I": ("###", " # ", " # ", " # ", " # ", "###"),
    "E": ("#####", "#    ", "#### ", "#    ", "#    ", "#####"),
    "W": ("#   #", "#   #", "# # #", "# # #", "## ##", "#   #"),
    "C": (" ####", "#    ", "#    ", "#    ", "#    ", " ####"),
    "G": (" ####", "#    ", "#  ##", "#   #", "#   #", " ####"),
    "B": ("#### ", "#   #", "#### ", "#   #", "#   #", "#### "),
    "F": ("#####", "#    ", "#### ", "#    ", "#    ", "#    "),
    "Y": ("#   #", " # # ", "  #  ", "  #  ", "  #  ", "  #  "),
    "R": ("#### ", "#   #", "#### ", "#  # ", "#   #", "#   #"),
    "P": ("#### ", "#   #", "#### ", "#    ", "#    ", "#    "),
    "D": ("#### ", "#   #", "#   #", "#   #", "#   #", "#### "),
    "U": ("#   #", "#   #", "#   #", "#   #", "#   #", " ### "),
    "M": ("#   #", "## ##", "# # #", "#   #", "#   #", "#   #"),
    " ": ("    ", "    ", "    ", "    ", "    ", "    "),
}

_ROWS = 6
BANNER_W = 70


def _art(word: str, gap: int = 1) -> list[str]:
    """Vẽ chữ khối 6 dòng (SHADOW khi console đọc được UTF-8, không thì BLOCK)."""
    font = SHADOW if utf8_ok() else BLOCK
    rows = [""] * _ROWS
    for idx, ch in enumerate(word.upper()):
        glyph = font.get(ch) or font[" "]
        w = max(len(g) for g in glyph)
        for i in range(_ROWS):
            rows[i] += glyph[i].ljust(w)
            if idx != len(word) - 1:
                rows[i] += " " * gap
    return [r.rstrip() for r in rows]


def _pad(content: str, width: int, align: str = "left") -> str:
    """Đệm cho bằng width (tính cả ANSI) để khung không bị lệch."""
    space = max(width - _vis(content), 0)
    if align == "right":
        return " " * space + content
    if align == "center":
        left = space // 2
        return " " * left + content + " " * (space - left)
    return content + " " * space


def _frame(content: str, width: int = BANNER_W, color: str = PALETTE["dark"]) -> str:
    """1 dòng nằm trong khung dọc."""
    v = style("│", color)
    return f" {v} {_pad(content, width - 4)} {v}"


def _hr(width: int = BANNER_W, start: str | None = None, end: str | None = None,
        left: str | None = None, right: str | None = None) -> str:
    start = start or PALETTE["violet"]
    end = end or PALETTE["cyan"]
    c = _chars()
    body = grad(c["h"] * (width - 2), start, end)
    return (" " + style(left or c["ltee"], PALETTE["dark"]) + body
            + style(right or c["rtee"], PALETTE["dark"]))


def _grad2d(text: str, row: int = 0, rows: int = _ROWS,
            start: str = PALETTE["cyan"], end: str = PALETTE["pink"]) -> str:
    """Gradient chéo: màu chạy từ trái-trên sang phải-dưới."""
    if not color_on() or not text:
        return text
    r1, g1, b1 = _hex_rgb(start)
    r2, g2, b2 = _hex_rgb(end)
    n = max(len(text) - 1, 1)
    ry = row / max(rows - 1, 1)
    out = []
    for i, ch in enumerate(text):
        if ch == " ":
            out.append(ch)
            continue
        t = min(0.34 * (i / n) + 0.66 * ry, 1.0)
        out.append(f"\033[38;2;{int(r1 + (r2 - r1) * t)};{int(g1 + (g2 - g1) * t)};"
                   f"{int(b1 + (b2 - b1) * t)}m{ch}")
    return "".join(out) + RESET


def _logo_rows(word: str, width: int, phase: int = 0) -> list[str]:
    """Logo gradient chéo; `phase` nhích cặp màu khi khởi động."""
    art = _art(word)
    art_w = max(_vis(r) for r in art)
    ramps = [(PALETTE["cyan"], PALETTE["violet"]),
             (PALETTE["blue"], PALETTE["pink"]),
             (PALETTE["cyan"], PALETTE["pink"]),
             (PALETTE["violet"], PALETTE["amber"])]
    start, end = ramps[phase % len(ramps)]
    pad = " " * max((width - 4 - art_w) // 2, 0)
    out = []
    for i, row in enumerate(art):
        out.append(_frame(pad + _grad2d(row, i, _ROWS, start, end)))
    return out
def _vis(s: str) -> int:
    """Độ dài hiển thị (bỏ qua mã màu ANSI)."""
    return len(re.sub(r"\033\[[0-9;]*m", "", str(s)))


def _rule(width: int, start: str, end: str, left: str = "╭", right: str = "╮") -> str:
    c = _chars()
    body = grad(c["h"] * max(width - 2, 4), start, end)
    return " " + style(left, start) + body + style(right, end)


def status_bar(left: str = "", right: str = "", width: int = 62) -> str:
    """Dòng trạng thái 1 hàng."""
    c = _chars()
    ltxt = style(c["dot"], PALETTE["green"], bold=True) + " " + style(left, PALETTE["slate"])
    gap = max(width - _vis(ltxt) - _vis(right) - 5, 2)
    return (" " + style(c["v"], PALETTE["dark"]) + " " + ltxt
            + " " * gap + style(right, PALETTE["dark"]) + " "
            + style(c["v"], PALETTE["dark"]))


def _chips(items: list[tuple[str, str, str]], width: int) -> list[str]:
    """Xếp chip (mã, tên, màu) thành dòng — tự xuống dòng khi hết chỗ."""
    lines: list[str] = []
    cur = ""
    limit = width - 8
    for code, name, col in items:
        chip = (style("▐", col, bold=True)
                + style(code + " ", col, bold=True)
                + style(name, PALETTE["white"], bold=True))
        add = (style("   ", PALETTE["dark"]) if cur else "") + chip
        if _vis(cur + add) > limit:
            lines.append(cur)
            cur = chip
        else:
            cur += add
    if cur:
        lines.append(cur)
    return lines


def _edge(t: float) -> str:
    """Viền dọc đổi màu dần (cyan -> violet) để khung có chiều sâu."""
    v = _chars()["v"]
    if not color_on():
        return v
    r1, g1, b1 = _hex_rgb(PALETTE["cyan"])
    r2, g2, b2 = _hex_rgb(PALETTE["violet"])
    return (f"\033[38;2;{int(r1 + (r2 - r1) * t)};{int(g1 + (g2 - g1) * t)};"
            f"{int(b1 + (b2 - b1) * t)}m{v}{RESET}")


def _frow(content: str, width: int = BANNER_W, t: float = 0.0) -> str:
    """1 dòng trong khung, viền trái/phải lấy màu theo vị trí."""
    return f" {_edge(t)} {_pad(content, width - 4)} {_edge(t)}"


def _block(code: str, name: str, col: str) -> str:
    """Chip nền đặc:  [ TT ] TikTok  — nhìn đậm hơn ▐ nhiều."""
    if not color_on():
        return f"[{code}] {name}"
    r, g, b = _hex_rgb(col)
    # v18b: capsule nen dac (nhu badge) + chu dam -> nhin "xịn" hon han
    tag = (f"\033[48;2;{r};{g};{b}m\033[38;2;10;14;24m\033[1m"
           f"  {code}  {RESET}")
    return tag + " " + style(name, PALETTE["white"], bold=True)


def _edge2(t: float = 0.5, right: bool = False) -> str:
    """Viền dọc: một màu cố định để cột viền nhìn liền mạch từ trên xuống."""
    ch = _chars().get("v", "|")
    col = PALETTE.get("line", "#6f7ef8")
    if not color_on():
        return ch
    r, g, b = _hex_rgb(col)
    return f"\033[38;2;{r};{g};{b}m{ch}{RESET}"


def _row2(content: str, t: float = 0.0, width: int = BANNER_W) -> str:
    """Một dòng trong khung kép — tổng độ rộng hiển thị đúng bằng `width`."""
    return f"{_edge2()} {_pad(content, width - 4)} {_edge2()}"


def _cap(left_char: str, right_char: str, fill: str, t: float = 0.0,
         width: int = BANNER_W) -> str:
    c = _chars()
    body = grad(fill * (width - 2), PALETTE["cyan"], PALETTE["violet"])
    return (style(c.get(left_char, "+"), PALETTE["cyan"], bold=True) + body
            + style(c.get(right_char, "+"), PALETTE["violet"], bold=True))


def _section(label: str, width: int = BANNER_W, color: str = PALETTE["violet"]) -> str:
    """Vạch chia có nhãn:  ╟─── ▍NỀN TẢNG ───────────────────╢"""
    c = _chars()
    tag = (style(c.get("pip", "#"), color, bold=True) + " "
           + style(label.upper(), color, bold=True))
    room = max(width - 7 - _vis(tag), 2)
    left = grad(c.get("thin", "-") * 3, PALETTE["dark"], color)
    right = grad(c.get("thin", "-") * room, color, PALETTE["dark"])
    return (style(c.get("lthin", "+"), PALETTE["dark"])
            + left + " " + tag + " " + right
            + style(c.get("rthin", "+"), PALETTE["dark"]))



# ------------------------------------------------------------------ ASCII hoá
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _ascii_piece(seg: str) -> str:
    """Bỏ dấu tiếng Việt + ký tự lạ, giữ độ dài 1 ký tự -> 1 ký tự."""
    seg = seg.replace("đ", "d").replace("Đ", "D")
    seg = unicodedata.normalize("NFD", seg)
    seg = "".join(ch for ch in seg if not unicodedata.combining(ch))
    return seg.encode("ascii", "replace").decode("ascii")


def _ascii_fold(text: str) -> str:
    """Terminal không đọc được UTF-8 -> chuyển cả khối về ASCII, giữ mã màu."""
    if utf8_ok() or not text:
        return text
    segs = _ANSI_RE.split(text)
    codes = _ANSI_RE.findall(text)
    out: list[str] = []
    for i, seg in enumerate(segs):
        out.append(_ascii_piece(seg))
        if i < len(codes):
            out.append(codes[i])
    return "".join(out)


def banner(version: str = "", subtitle: str = "", *, name: str = "KHOA AN",
           animate: bool = True, user: str = "",
           stats_platforms: int = 6, stats_voices: int = 41,
           stats_commands: int = 0, stream: bool = False) -> str:
    """Banner khởi động v18.3: khung kép, logo gradient có bệ đỡ, chip dạng lưới."""
    fix_stdout()
    width = BANNER_W
    c = _chars()
    stamp = time.strftime("%H:%M:%S")
    ver = version or ""
    out: list[str] = [""]

    def nxt(content: str) -> None:
        out.append(_row2(content, min(len(out) / 26.0, 1.0), width))

    # ---------- nắp neon
    if color_on():
        out.append(grad(c.get("glow", "-") * width, PALETTE["teal"], PALETTE["indigo"]))

    # ---------- mép trên
    out.append(_cap("tl2", "tr2", c.get("dbl", "=")))

    # ---------- dải trạng thái
    head_left = (style(c["dot"], PALETTE["green"], bold=True) + " "
                 + style("ONLINE", PALETTE["green"], bold=True)
                 + style("   ", PALETTE["dark"])
                 + style(c.get("diamond", "*"), PALETTE["violet"])
                 + style(" BUILD ", PALETTE["slate"]) + style(ver or "dev", PALETTE["white"], bold=True)
                 + style("   ", PALETTE["dark"])
                 + style(c.get("diamond", "*"), PALETTE["pink"])
                 + style(" 24/7", PALETTE["pink"], bold=True))
    head_right = style(stamp, PALETTE["dark"])
    nxt(head_left + " " * max(width - 4 - _vis(head_left) - _vis(head_right), 1)
        + head_right)
    out.append(_section("khởi động", width, PALETTE["cyan"]))

    # ---------- logo
    art = _art(name)
    art_w = max(_vis(r) for r in art)
    ramps = [(PALETTE["cyan"], PALETTE["violet"]),
             (PALETTE["blue"], PALETTE["pink"]),
             (PALETTE["cyan"], PALETTE["pink"]),
             (PALETTE["violet"], PALETTE["amber"])]
    start, end = ramps[(1 if animate else 0) % len(ramps)]
    pad = " " * max((width - 4 - art_w) // 2, 0)
    for i, row in enumerate(art):
        nxt(pad + _grad2d(row, i, _ROWS, start, end))

    # ---------- bóng đổ mờ (mirror 3 dòng cuối, tối dần) — hiệu ứng nổi 3D
    if color_on() and len(art) >= 4:
        blocks = re.compile(r"[█▓▒░]")
        last = art[-1]
        row1 = blocks.sub("▄", last)
        row2 = blocks.sub("▁", last)
        nxt(pad + _grad2d(row1, 0, 4, PALETTE["indigo"], PALETTE["blue"]))
        nxt(pad + _grad2d(row2, 2, 4, PALETTE["dark"], PALETTE["dark"]))

    # ---------- bệ đỡ mờ dưới logo (tạo cảm giác 3D)
    if color_on():
        base = c.get("shade", "_") * max(art_w - 12, 10)
        nxt(_pad(grad(base, PALETTE["dark"], PALETTE["violet"]), width - 4, "center"))

    # ---------- chữ phụ
    spaced = " ".join(list("TOOLS"))
    nxt(_pad(_grad2d(spaced, 2, 4, PALETTE["violet"], PALETTE["cyan"]),
             width - 4, "center"))
    nxt("")

    # ---------- dải chức năng
    parts = [p.strip() for p in (subtitle or "TẢI VIDEO · ÂM THANH · ẢNH · 24/7")
             .split("·") if p.strip()]
    if parts:
        segs: list[str] = []
        cur = ""
        for i, p in enumerate(parts):
            col = PALETTE["amber"] if i < 3 else PALETTE["green"]
            piece = style(p, col, bold=True)
            add = (style("   ·   ", PALETTE["dark"]) if cur else "") + piece
            if cur and _vis(cur + add) > width - 14:
                segs.append(cur)
                cur = piece
            else:
                cur += add
        if cur:
            segs.append(cur)
        for seg in segs:
            dash = c["h"] * 4
            nxt(_pad(grad(dash, PALETTE["indigo"], PALETTE["slate"]) + " " + seg + " "
                     + grad(dash, PALETTE["slate"], PALETTE["indigo"]),
                     width - 4, "center"))
        nxt("")

    # ---------- lưới chip nền tảng (3 cột, thẳng hàng)
    out.append(_section("nền tảng", width, PALETTE["pink"]))
    rows = [[("TT", "TikTok", PALETTE["pink"]),
             ("IG", "Instagram", PALETTE["violet"]),
             ("FB", "Facebook", PALETTE["blue"])],
            [("YT", "YouTube", PALETTE["red"]),
             ("MU", "Music", PALETTE["cyan"]),
             ("VO", "Voice AI", PALETTE["green"])]]
    colw = 19
    for row in rows:
        cells = [_pad(_block(code, label, col), colw, "left") for code, label, col in row]
        nxt(_pad("".join(cells), width - 4, "center"))

    # ---------- số liệu
    stats = [(stats_platforms, "nền tảng"), (stats_voices, "giọng AI"),
             (stats_commands, "lệnh")]
    pieces: list[str] = []
    for num, label in stats:
        if num:
            pieces.append(style(str(num), PALETTE["green"], bold=True) + " "
                          + style(label, PALETTE["slate"]))
    pieces.append(style("24/7", PALETTE["pink"], bold=True) + " "
                  + style("trực", PALETTE["slate"]))
    nxt(_pad(style("   ·   ", PALETTE["dark"]).join(pieces), width - 4, "center"))
    nxt("")

    # ---------- gợi ý lệnh
    out.append(_section("bắt đầu", width, PALETTE["cyan"]))
    hint = (style(c["arrow"], PALETTE["violet"], bold=True) + " "
            + style("/menu", PALETTE["white"], bold=True)
            + style(" bảng điều khiển", PALETTE["slate"])
            + style("     ", PALETTE["dark"])
            + style(c["arrow"], PALETTE["cyan"], bold=True) + " "
            + style("/help", PALETTE["white"], bold=True)
            + style(" toàn bộ lệnh", PALETTE["slate"]))
    nxt(_pad(hint, width - 4, "center"))
    nxt("")

    # ---------- chân khung
    out.append(_cap("bl2", "br2", c.get("dbl", "=")))

    # ---------- meta ngoài khung
    meta = (style(c["spark"], PALETTE["violet"]) + " "
            + style(f"python {sys.version_info.major}.{sys.version_info.minor}", PALETTE["dark"])
            + style("   ·   ", PALETTE["dark"])
            + style(time.strftime("%d/%m/%Y  %H:%M"), PALETTE["dark"])
            + style("   ·   ", PALETTE["dark"])
            + style(name.upper() + " TOOLS", PALETTE["violet"], bold=True))
    if color_on() and ver:
        r, g, b = _hex_rgb(PALETTE["violet"])
        meta += (f"  \033[48;2;{r};{g};{b}m\033[38;2;10;14;24m\033[1m {ver} {RESET}")
    out.append(_pad(" " + meta, width, "left"))
    out.append("")

    live = bool(stream) and color_on() and bool(
        getattr(sys.stdout, "isatty", lambda: False)())
    if live:
        delay = 0.03 if animate else 0.0
        try:
            for line in out:
                sys.stdout.write(_ascii_fold(line) + "\n")
                sys.stdout.flush()
                if delay:
                    time.sleep(delay)
        except Exception:  # noqa: BLE001
            return _ascii_fold("\n".join(out))
        return ""
    return _ascii_fold("\n".join(out))


def cmdsheet(title: str, items, *, cols: int = 2, width: int = BANNER_W) -> str:
    """Bảng tra lệnh nhiều cột: /lenh + mô tả, căn đều, khung gradient.

    items: [(cmd, desc), ...] hoặc [(cmd, desc, color), ...]
    """
    fix_stdout()
    c = _chars()
    rows: list[tuple[str, str, str]] = []
    for it in items or []:
        cmd = str(it[0])
        desc = str(it[1]) if len(it) > 1 else ""
        col = it[2] if len(it) > 2 and it[2] else PALETTE["cyan"]
        rows.append((cmd, desc, col))
    if not rows:
        return ""

    cmd_w = max(_vis(r[0]) for r in rows) + 2
    cell_w = max((width - 4 - (cols - 1) * 3) // max(cols, 1), 10)
    desc_w = max(cell_w - cmd_w, 4)
    if not utf8_ok():
        desc_w = max(desc_w, 4)

    lines: list[str] = []
    for i in range(0, len(rows), cols):
        cells = []
        for cmd, desc, col in rows[i:i + cols]:
            cell = (style(cmd.ljust(cmd_w), col, bold=True)
                    + style(desc[:desc_w].ljust(desc_w), PALETTE["slate"]))
            cells.append(_pad(cell, cell_w))
        lines.append(_pad(style("   ", PALETTE["dark"]).join(cells), width - 4))

    body = _chars()["h"] * (width - 2)
    top = (" " + style(c["tl"], PALETTE["cyan"], bold=True)
           + grad(body, PALETTE["cyan"], PALETTE["violet"])
           + style(c["tr"], PALETTE["violet"], bold=True))
    bot = (" " + style(c["bl"], PALETTE["cyan"], bold=True)
           + grad(body, PALETTE["violet"], PALETTE["cyan"])
           + style(c["br"], PALETTE["cyan"], bold=True))
    head = (style("\u258c", PALETTE["violet"], bold=True) + " "
            + style(title.upper(), PALETTE["amber"], bold=True)
            + style("  ", PALETTE["dark"])
            + style(c["diamond"] + " gõ để dùng", PALETTE["dark"]))
    out = [top, _frame(head, width), _hr(width)] + [_frame(x, width) for x in lines] + [bot, ""]
    return "\n".join(out)


def box(title: str, rows: list[tuple[str, str]], accent: str = PALETTE["cyan"]) -> str:
    """Khung key/value bo góc, tiêu đề nằm trên viền, tự canh chuẩn cả khi có ANSI."""
    c = _chars()
    labels: list[str] = []
    key_w = max([_vis(str(k)) for k, _ in rows] + [7])
    for key, value in rows:
        pad = " " * max(key_w - _vis(str(key)), 0)
        labels.append(style(c["bullet"], accent, bold=True) + " "
                      + style(str(key), PALETTE["slate"]) + pad + "  " + str(value))
    inner = max([_vis(x) for x in labels] + [len(title) + 10]) + 1
    width = inner + 4
    head = f"{c['diamond']} {title} "
    a = 2
    b = max(width - 2 - a - _vis(head), 1)
    top = (" " + style(c["tl"], accent)
           + grad(c["h"] * a, accent, PALETTE["violet"])
           + style(head, accent, bold=True)
           + grad(c["h"] * b, PALETTE["violet"], accent)
           + style(c["tr"], accent))
    sep = (" " + style(c["ltee"], accent)
           + grad(c["h"] * (width - 2), accent, PALETTE["violet"])
           + style(c["rtee"], accent))
    out = [top, sep]
    for cell in labels:
        out.append(style(" " + c["v"], accent) + " " + cell
                   + " " * max(inner - _vis(cell), 0) + " " + style(c["v"], accent))
    out.append(" " + style(c["bl"], accent)
               + grad(c["h"] * (width - 2), accent, PALETTE["violet"])
               + style(c["br"], accent))
    return "\n".join(out)


# ------------------------------------------------------------------ số liệu
def human_size(n: float) -> str:
    """2.4 MB / 812 KB / 1.1 GB."""
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return (f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}")
        n /= 1024
    return f"{n:.1f} TB"


def human_time(sec: float) -> str:
    """1h 04m / 3m 12s / 42s."""
    sec = int(max(sec or 0, 0))
    if sec >= 3600:
        return f"{sec // 3600}h {(sec % 3600) // 60:02d}m"
    if sec >= 60:
        return f"{sec // 60}m {sec % 60:02d}s"
    return f"{sec}s"


# ------------------------------------------------------------------ tiến trình
def progress(label: str, done: float, total: float = 0, *, speed: float = 0,
             width: int = 24) -> None:
    """Vẽ thanh tiến trình ngay tại chỗ (một dòng, tự ghi đè).

    11:37:48  ↓  YOUTUBE 720p   ███████████░░░░░░░░░░░░  42.7%  3.2/7.5 MB  1.8 MB/s  còn 2s
    """
    if not color_on():
        return
    c = _chars()
    pct = (done / total * 100) if total else 0.0
    pct = max(0.0, min(100.0, pct))
    filled = int(round(pct / 100 * width))
    bar = grad("█" * filled, PALETTE["cyan"], PALETTE["green"]) if filled else ""
    bar += style("░" * (width - filled), PALETTE["dark"])
    bits = [style(time.strftime("%H:%M:%S"), PALETTE["dark"]),
            style(c["dl"], PALETTE["cyan"], bold=True),
            style(label[:26].ljust(26), PALETTE["white"], bold=True),
            bar,
            style(f"{pct:5.1f}%", PALETTE["cyan"], bold=True)]
    if total:
        bits.append(style(f"{human_size(done)}/{human_size(total)}", PALETTE["slate"]))
    if speed:
        bits.append(style(f"{human_size(speed)}/s", PALETTE["green"]))
        if total and done < total:
            bits.append(style(f"còn {human_time((total - done) / speed)}", PALETTE["dark"]))
    _write("\r  " + "  ".join(bits) + "\033[K")


def progress_end(label: str, *, size: str = "", secs: float = 0.0, ok: bool = True) -> None:
    """Kết thúc thanh tiến trình: xoá dòng nháp rồi in dòng sự kiện gọn."""
    if color_on():
        _write("\r\033[K")
    site = (label.split()[0] if label else "?").upper()
    event(site, " ".join(label.split()[1:]) or label, ok=ok, secs=secs, size=size)


SPIN_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPIN_ASCII = "|/-\\"


@contextmanager
def spin(label: str, color: str = PALETTE["violet"]):
    """Dòng chờ có hiệu ứng xoay: ⠹ Đang dò metadata …  1.4s"""
    c = _chars()
    t0 = time.perf_counter()
    if not color_on():
        _emit(f" {style(c['arrow'], color)} {label} …")
        try:
            yield
        except Exception:
            _emit(f" {style(c['cross'], PALETTE['red'])} {label} "
                  f"({time.perf_counter() - t0:.2f}s)")
            raise
        _emit(f" {style(c['check'], PALETTE['green'])} {label} "
              f"({time.perf_counter() - t0:.2f}s)")
        return

    stop = {"v": False}
    frames = SPIN_FRAMES if utf8_ok() else SPIN_ASCII

    def _run() -> None:
        i = 0
        while not stop["v"]:
            fr = frames[i % len(frames)]
            el = time.perf_counter() - t0
            _write("\r  " + style(fr, color, bold=True) + " "
                   + style(label, PALETTE["white"])
                   + style(f"  {el:.1f}s", PALETTE["dark"]) + "\033[K")
            i += 1
            time.sleep(0.08)

    import threading
    th = threading.Thread(target=_run, daemon=True)
    th.start()
    err = None
    try:
        yield
    except Exception as e:  # noqa: BLE001
        err = e
        raise
    finally:
        stop["v"] = True
        th.join(timeout=0.4)
        _write("\r\033[K")
        if err is None:
            _emit(f" {style(c['check'], PALETTE['green'])} {label} "
                  f"{style(f'({time.perf_counter() - t0:.2f}s)', PALETTE['dark'])}")
        else:
            _emit(f" {style(c['cross'], PALETTE['red'])} {label} "
                  f"{style(f'({time.perf_counter() - t0:.2f}s)', PALETTE['dark'])}")


def _write(text: str) -> None:
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
        return
    except Exception:  # noqa: BLE001
        pass
    try:  # console không nhận ký tự -> ghi thẳng buffer, thay ký tự lạ
        enc = (getattr(sys.stdout, "encoding", "") or "utf-8")
        sys.stdout.buffer.write(str(text).encode(enc, "replace"))
        sys.stdout.buffer.flush()
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------ in nhanh
def _emit(line: str) -> None:
    try:
        print(line, flush=True)
    except Exception:  # noqa: BLE001
        try:
            print(line.encode("ascii", "replace").decode(), flush=True)
        except Exception:  # noqa: BLE001
            pass


def ok(msg: str) -> None:
    _emit(f" {style(_chars()['check'], PALETTE['green'], bold=True)} {msg}")


def info(msg: str) -> None:
    _emit(f" {style(_chars()['dot'], PALETTE['cyan'])} {msg}")


def warn(msg: str) -> None:
    _emit(f" {style(_chars()['warn'], PALETTE['amber'], bold=True)} {msg}")


def err(msg: str) -> None:
    _emit(f" {style(_chars()['cross'], PALETTE['red'], bold=True)} {msg}")


@contextmanager
def step(name: str):
    """Đo thời gian 1 bước: ▸ name …  rồi  ✔ name (0.42s)."""
    c = _chars()
    t0 = time.perf_counter()
    _emit(f" {style(c['arrow'], PALETTE['violet'])} {name} {style('…', PALETTE['dark'])}")
    try:
        yield
    except Exception:
        _emit(f" {style(c['cross'], PALETTE['red'])} {name} "
              f"{style(f'({time.perf_counter() - t0:.2f}s)', PALETTE['dark'])}")
        raise
    else:
        _emit(f" {style(c['check'], PALETTE['green'])} {name} "
              f"{style(f'({time.perf_counter() - t0:.2f}s)', PALETTE['dark'])}")


def event(site: str, detail: str, *, ok: bool = True, secs: float = 0.0,
          size: str = "") -> None:
    """Dòng sự kiện tải:  14:03:22  ⬇ TIKTOK  @user · video · 2.4 MB   ✔ 1.8s"""
    c = _chars()
    col = SITE_COLOR.get((site or "").lower(), PALETTE["slate"])
    ts = time.strftime("%H:%M:%S")
    label = (site or "?").upper()
    label = label[:9].ljust(9)
    body = detail or ""
    if len(body) > 40:
        body = body[:39] + ("…" if utf8_ok() else ".")
    parts = [style(ts, PALETTE["dark"]),
             style(c["dl"], col, bold=True),
             style(label, col, bold=True)]
    if body:
        parts.append(style(body.ljust(40), PALETTE["white"]))
    if size:
        parts.append(style(size.rjust(9), PALETTE["slate"]))
    tail = style(f"{c['check']} {secs:.1f}s", PALETTE["green"]) if ok else style(c["cross"], PALETTE["red"])
    _emit(" " + "  ".join(parts) + "   " + tail)


# ------------------------------------------------------------------ log màu
LEVEL_LOOK = {
    "DEBUG": (PALETTE["dark"], "·"),
    "INFO": (PALETTE["cyan"], "●"),
    "WARNING": (PALETTE["amber"], "▲"),
    "ERROR": (PALETTE["red"], "✖"),
    "CRITICAL": (PALETTE["red"], "◆"),
}


class Formatter(logging.Formatter):
    """Định dạng log gọn, có màu theo level + tên module."""

    def format(self, record: logging.LogRecord) -> str:
        c = _chars()
        col, icon = LEVEL_LOOK.get(record.levelname, (PALETTE["slate"], "·"))
        ts = time.strftime("%H:%M:%S", time.localtime(record.created))
        name = record.name.replace("khoaan.", "")
        if len(name) > 14:
            name = name[:13] + "…" if utf8_ok() else name[:14]
        msg = record.getMessage()
        line = (
            f" {style(ts, PALETTE['dark'])} "
            f"{style(icon, col, bold=True)} "
            f"{style(record.levelname.ljust(7), col)} "
            f"{style(name.ljust(14), PALETTE['violet'])} "
            f"{style(c['v'], PALETTE['dark'])} {msg}"
        )
        if record.exc_info:
            line += "\n" + style(self.formatException(record.exc_info), PALETTE["dark"])
        return line


_NOISY = ("aiohttp.access", "aiohttp.client", "urllib3", "httpx", "httpcore",
          "asyncio", "telegram.ext.Application", "telegram.request")


def force_windows_utf8() -> bool:
    """Windows: ép console sang UTF-8 (chcp 65001) để khung + logo hiện ĐÚNG.

    cmd.exe mặc định cp1258/cp936 -> trước đây banner bị lùi về ASCII xấu.
    Trả True nếu đã bật được UTF-8.
    """
    if os.name != "nt":
        return True
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.SetConsoleOutputCP(65001)
        k32.SetConsoleCP(65001)
    except Exception:  # noqa: BLE001
        pass
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    _STATE["utf8"] = None
    enc = (getattr(sys.stdout, "encoding", "") or "").lower()
    return "utf" in enc


def setup(level: int = logging.INFO) -> None:
    """Cấu hình logging gốc cho cả bot."""
    force_windows_utf8()
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(Formatter())
    root.addHandler(handler)
    root.setLevel(level)
    for name in _NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("yt_dlp").setLevel(logging.ERROR)
    logging.getLogger("PIL").setLevel(logging.WARNING)


def clear() -> None:
    if not color_on():
        return
    try:
        cols = shutil.get_terminal_size((80, 24)).columns
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.write("\033[3%dm" % 0)
        sys.stdout.flush()
        _ = cols
    except Exception:  # noqa: BLE001
        pass

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Doi log console (co ma mau ANSI 24-bit) thanh anh PNG - de xem truoc giao dien CMD.

Dung: python tools/ansi2png.py <file_log.txt> <anh_ra.png> [--title "CMD"]
"""
from __future__ import annotations

import re
import sys

from PIL import Image, ImageDraw, ImageFont

SGR = re.compile(r"\033\[([0-9;]*)m")
CTRL = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
BG = (11, 16, 32)
FG = (226, 232, 240)
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
SIZE = 17


def _clean(line: str) -> str:
    """Bo cac ma dieu khien khong phai mau (xoa dong \\033[K, xoa man hinh...)."""
    return CTRL.sub(lambda m: "" if m.group(0)[-1] != "m" else m.group(0), line).replace("\r", "")


def parse(line: str):
    """-> [(text, (r,g,b), bold, dim), ...]"""
    line = _clean(line)
    out, cur, bold, i, dim = [], FG, False, 0, False
    for m in SGR.finditer(line):
        chunk = line[i:m.start()]
        if chunk:
            out.append((chunk, cur, bold, dim))
        nums = [n for n in m.group(1).split(";") if n != ""]
        j = 0
        while j < len(nums):
            n = int(nums[j])
            if n == 0:
                cur, bold, dim = FG, False, False
            elif n == 1:
                bold = True
            elif n == 2:
                dim = True
            elif n == 22:
                bold = dim = False
            elif n == 38 and j + 4 < len(nums) and nums[j + 1] == "2":
                cur = (int(nums[j + 2]), int(nums[j + 3]), int(nums[j + 4]))
                j += 4
            elif n == 39:
                cur = FG
            j += 1
        i = m.end()
    tail = line[i:]
    if tail:
        out.append((tail, cur, bold, dim))
    return [s for s in out if s[0]]


def render(src: str, dst: str, title: str = "KHOA AN TOOLS - v17") -> None:
    raw = open(src, encoding="utf-8", errors="replace").read().replace("\r", "")
    lines = raw.split("\n")
    f, fb = ImageFont.truetype(FONT, SIZE), ImageFont.truetype(FONT_B, SIZE)
    cw = f.getlength("M")
    lh = int(SIZE * 1.22)
    pad, bar = 26, 40
    width = int(max((len(re.sub(SGR, "", ln)) for ln in lines), default=80) * cw) + pad * 2
    width = max(width, 980)
    height = lh * len(lines) + pad * 2 + bar
    img = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, width, bar], fill=(22, 27, 44))
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([16 + i * 22, 13, 28 + i * 22, 25], fill=c)
    d.text((width / 2 - f.getlength(title) / 2, 11), title, font=f, fill=(148, 163, 184))
    y = bar + pad - 2
    for ln in lines:
        x = float(pad)
        for text, color, bold, _dim in parse(ln):
            font = fb if bold else f
            for ch in text:  # vẽ từng ký tự, bước nhảy cố định -> ký tự khối không đè nhau
                if ch == "█":  # khối đặc: tô kín ô để chữ art liền mạch
                    d.rectangle([x, y - 1, x + cw - 0.5, y + lh - 1], fill=color)
                elif ch != " ":
                    d.text((x, y), ch, font=font, fill=color)
                x += cw
        y += lh
    img.save(dst)
    print(f"OK {dst} {img.size[0]}x{img.size[1]}")


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) < 2:
        print(__doc__)
        raise SystemExit(1)
    ttl = "KHOA AN TOOLS - v17"
    if "--title" in a:
        k = a.index("--title")
        ttl = a[k + 1]
        a = a[:k] + a[k + 2:]
    render(a[0], a[1], ttl)

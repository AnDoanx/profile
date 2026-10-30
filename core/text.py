#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tiện ích text/số/thời gian."""

import html
import re
import time
from datetime import datetime, timezone, timedelta

VN_TZ = timezone(timedelta(hours=7))


def now_vn() -> str:
    return datetime.now(VN_TZ).strftime("%H:%M:%S • %d/%m/%Y")


def human_int(n) -> str:
    try:
        n = int(n)
    except Exception:  # noqa: BLE001
        return "0"
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B".replace(".0B", "B")
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{n / 1_000:.1f}K".replace(".0K", "K")
    return f"{n}"


def human_duration(seconds) -> str:
    try:
        sec = int(float(seconds))
    except Exception:  # noqa: BLE001
        return "--:--"
    if sec <= 0:
        return "--:--"
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def parse_duration(text: str) -> int:
    if not text:
        return 0
    parts = [p for p in str(text).strip().split(":") if p.isdigit()]
    total = 0
    for p in parts:
        total = total * 60 + int(p)
    return total


def uptime_str(start_ts: float) -> str:
    sec = int(time.time() - start_ts)
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d} ngày {h} giờ"
    if h:
        return f"{h} giờ {m} phút"
    if m:
        return f"{m} phút {s} giây"
    return f"{s} giây"


def esc(text) -> str:
    return html.escape(str(text or ""), quote=False)


def clean_text(text: str) -> str:
    """Bỏ emoji lạ/ký tự điều khiển cho canvas."""
    text = re.sub(r"[\u0000-\u001f\u007f]", " ", str(text or ""))
    return re.sub(r"\s+", " ", text).strip()


def extract_url(text: str) -> str | None:
    m = re.search(r"https?://[^\s<>\"']+", text or "")
    return m.group(0).rstrip(").,]") if m else None


def file_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"

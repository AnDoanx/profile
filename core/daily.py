#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hạn mức theo NGÀY, tự reset lúc 00:00 giờ Việt Nam.

Dùng cho /anh18 (mỗi user 3 lượt/ngày). Lưu bền trong data/daily.json:

    {"quota": {"anh18:123456": {"date": "2026-05-01", "used": 2}}}
"""

from datetime import datetime, timedelta, timezone

from core.store import JsonStore

VN = timezone(timedelta(hours=7))
STORE = JsonStore("daily.json", {"quota": {}})


def today() -> str:
    return datetime.now(VN).strftime("%Y-%m-%d")


def now_text() -> str:
    return datetime.now(VN).strftime("%H:%M %d/%m/%Y")


def _rec(key: str) -> dict:
    """Bản ghi của hôm nay; sang ngày mới thì tự tạo lại (used = 0)."""
    q = STORE.data.setdefault("quota", {})
    rec = q.get(key)
    if not isinstance(rec, dict) or rec.get("date") != today():
        rec = {"date": today(), "used": 0}
        q[key] = rec
        STORE.save()
    return rec


def used(key: str) -> int:
    return int(_rec(key).get("used") or 0)


def remaining(key: str, limit: int) -> int:
    if limit <= 0:
        return 0
    return max(0, limit - used(key))


def take(key: str, limit: int, n: int = 1) -> tuple[bool, int]:
    """Trừ n lượt. Trả về (được_phép, số_lượt_còn_lại)."""
    if limit <= 0:
        return False, 0
    rec = _rec(key)
    u = int(rec.get("used") or 0)
    if u + n > limit:
        return False, max(0, limit - u)
    rec["used"] = u + n
    STORE.save()
    return True, max(0, limit - rec["used"])


def give_back(key: str, n: int = 1) -> None:
    """Trả lại lượt (dùng khi lấy ảnh thất bại — không tính lượt của user)."""
    rec = _rec(key)
    rec["used"] = max(0, int(rec.get("used") or 0) - int(n))
    STORE.save()


def reset(key: str) -> None:
    STORE.data.setdefault("quota", {}).pop(key, None)
    STORE.save()

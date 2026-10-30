#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sổ tay người dùng: nhớ username ⇄ ID để /id @username tìm được.

Telegram Bot API **không cho bot tra ID từ @username của người lạ**
(get_chat("@abc") chỉ chạy với kênh/nhóm công khai, còn user thì báo
"chat not found"). Vì vậy bot tự ghi lại mọi người nó từng gặp:

• ai nhắn cho bot / bấm nút  -> ghi ngay
• ai vào nhóm, ai nói trong nhóm bot ở -> ghi
• /id @username -> tra trong sổ trước, không có thì mới gọi API

Dữ liệu lưu ở data/users.json, restart vẫn nhớ.
"""

from __future__ import annotations

import time

from core.store import JsonStore

_STORE = JsonStore("users.json", {"users": {}})
MAX_USERS = 20000


def _db() -> dict:
    raw = _STORE.data.get("users")
    if not isinstance(raw, dict):
        raw = {}
        _STORE.data["users"] = raw
    return raw


def remember(user, name: str = "") -> int:
    """Ghi 1 người vào sổ. Gọi rất nhiều lần nên phải rẻ: chỉ ghi đĩa khi có gì mới."""
    uid = getattr(user, "id", None)
    if not uid or getattr(user, "is_bot", False):
        return 0
    uname = (getattr(user, "username", "") or "").strip().lstrip("@").lower()
    full = name or getattr(user, "full_name", "") or getattr(user, "first_name", "") or ""
    db = _db()
    key = str(int(uid))
    old = db.get(key) or {}
    new_uname = uname or old.get("u", "")
    new_name = (full or old.get("n", ""))[:64]
    now = int(time.time())
    changed = (new_uname != old.get("u", "")) or (new_name != old.get("n", ""))
    stale = now - int(old.get("t") or 0) > 6 * 3600
    if old and not changed and not stale:
        return len(db)                       # không có gì mới -> khỏi ghi đĩa
    rec = {"u": new_uname, "n": new_name, "t": now}
    if old.get("u") and not uname:
        rec["u_old"] = old["u"]
    db[key] = rec
    if len(db) > MAX_USERS:                  # dọn bớt bản ghi cũ nhất
        for k in sorted(db, key=lambda x: db[x].get("t", 0))[: len(db) - MAX_USERS]:
            db.pop(k, None)
    _STORE.save()
    return len(db)


def total() -> int:
    return len(_db())


def name_of(uid: int) -> str:
    return (_db().get(str(int(uid))) or {}).get("n", "")


def username_of(uid: int) -> str:
    return (_db().get(str(int(uid))) or {}).get("u", "")


def find_username(uname: str) -> int | None:
    """Tra ID theo username (không phân biệt hoa thường)."""
    uname = (uname or "").strip().lstrip("@").lower()
    if not uname:
        return None
    db = _db()
    for uid, rec in db.items():
        if rec.get("u") == uname or rec.get("u_old") == uname:
            return int(uid)
    return None


def search(term: str, limit: int = 6) -> list[dict]:
    """Tìm mờ theo username hoặc tên hiển thị (dùng khi tra đúng không ra)."""
    term = (term or "").strip().lstrip("@").lower()
    if len(term) < 2:
        return []
    db = _db()
    hits: list[tuple[int, dict]] = []
    for uid, rec in db.items():
        u = rec.get("u", "")
        n = (rec.get("n", "") or "").lower()
        if term in u or term in n or u.startswith(term):
            hits.append((int(uid), rec))
    hits.sort(key=lambda x: (x[1].get("u", "") != term, -x[1].get("t", 0)))
    return [{"id": uid, "username": rec.get("u", ""), "name": rec.get("n", "")}
            for uid, rec in hits[:limit]]

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cookie cho Instagram / Facebook (định dạng Netscape cookies.txt).

Instagram (nhất là reel của người lạ) và Facebook (video trong nhóm/kín) thường
bắt đăng nhập. Bot cho phép nạp cookie 1 lần rồi tự dùng cho mọi lần tải:

    /cookie ig   (reply 1 file cookies.txt, hoặc dán nội dung)
    /cookie fb
    /cookie ig xem | /cookie ig xoa

File lưu tại  DATA_DIR/cookies_<site>.txt
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import config

log = logging.getLogger("khoaan.cookie")

SITES = {
    "ig": "instagram.com",
    "fb": "facebook.com",
    "yt": "youtube.com",
}
# tên hiển thị cho panel /cookie
LABELS = {"ig": "Instagram", "fb": "Facebook", "yt": "YouTube"}
ALIAS = {
    "instagram": "ig", "ins": "ig", "ig": "ig",
    "facebook": "fb", "fb": "fb",
    "youtube": "yt", "ytb": "yt", "yt": "yt", "google": "yt",
}


def norm(site: str) -> str:
    key = (site or "").strip().lower().lstrip("/")
    return ALIAS.get(key, key)


def path(site: str) -> Path:
    site = norm(site)
    return Path(config.DATA_DIR) / f"cookies_{site}.txt"


def _lines(text: str) -> list[str]:
    return [ln for ln in (text or "").replace("\r\n", "\n").split("\n") if ln.strip()]


def valid(site: str, text: str) -> tuple[bool, str]:
    """Kiểm tra nội dung có phải cookies.txt Netscape đúng site không."""
    site = norm(site)
    domain = SITES.get(site, "")
    rows = _lines(text)
    if not rows:
        return False, "Nội dung rỗng."
    hits = 0
    for ln in rows:
        if ln.startswith("#HttpOnly_"):
            ln = ln[len("#HttpOnly_"):]
        if ln.startswith("#"):
            continue
        fields = ln.split("\t")
        if len(fields) < 7:
            continue
        if domain and domain not in fields[0]:
            continue
        if not fields[5]:
            continue
        hits += 1
    if hits == 0:
        return False, f"Không thấy cookie nào của {domain}. Cần file cookies.txt (Netscape)."
    return True, f"{hits} cookie"


def save(site: str, text: str) -> tuple[int, str]:
    site = norm(site)
    ok, info = valid(site, text)
    if not ok:
        return 0, info
    p = path(site)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text.replace("\r\n", "\n").strip() + "\n", encoding="utf-8")
    try:
        p.chmod(0o600)
    except Exception:  # noqa: BLE001
        pass
    return len(_lines(text)), info


def get(site: str) -> str | None:
    """Đường dẫn cookie còn dùng được (None nếu chưa nạp)."""
    p = path(site)
    if not p.exists() or p.stat().st_size < 20:
        return None
    return str(p)


def count(site: str) -> int:
    p = path(site)
    if not p.exists():
        return 0
    ok, _ = valid(site, p.read_text(encoding="utf-8", errors="replace"))
    if not ok:
        return 0
    return len(_lines(p.read_text(encoding="utf-8", errors="replace")))


def age_days(site: str) -> float:
    p = path(site)
    if not p.exists():
        return 0.0
    return (time.time() - p.stat().st_mtime) / 86400


def clear(site: str) -> bool:
    p = path(site)
    if p.exists():
        p.unlink()
        return True
    return False


def header(site: str, domains: tuple[str, ...] = ()) -> dict[str, str]:
    """Header Cookie để gửi kèm request HTTP (FB/IG chặn IP máy chủ nếu thiếu).

    Trả {} nếu chưa nạp cookie. Chỉ lấy cookie thuộc domain liên quan để
    header gọn và không dính cookie của site khác trong cùng file.
    """
    p = path(site)
    if not p.exists() or p.stat().st_size < 20:
        return {}
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return {}
    want = tuple(d.lower() for d in domains)
    pairs: list[str] = []
    seen: set[str] = set()
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") and not ln.startswith("#HttpOnly_"):
            continue
        if ln.startswith("#HttpOnly_"):
            ln = ln[len("#HttpOnly_"):]
        f = ln.split("\t")
        if len(f) < 7:
            continue
        dom, _flag, _p, _sec, _exp, name, val = f[-7:]
        if want and not any(w in dom.lower() for w in want):
            continue
        if not name or name in seen:
            continue
        seen.add(name)
        pairs.append(f"{name}={val}")
    return {"Cookie": "; ".join(pairs)} if pairs else {}


def status_line(site: str) -> str:
    """Câu mô tả ngắn để in trong panel /cookie."""
    n = count(site)
    if not n:
        return "chưa nạp"
    return f"{n} cookie · {age_days(site):.0f} ngày"

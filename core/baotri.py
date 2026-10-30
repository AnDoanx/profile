#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BẢO TRÌ: toàn bot + từng lệnh riêng, có hẹn giờ tự mở lại.

Lưu bền trong data/baotri.json:

    {
      "global": {"on": true, "until": 1750000000},
      "cmds":   {"anh18": {"on": true, "until": 0}, "sing": {"on": false, "until": 0}},
      "note":   "Nâng cấp server, 30 phút nữa xong"
    }

`until` = 0 nghĩa là bảo trì không hẹn giờ (admin tự tắt).
Hết hạn thì tự coi như đã mở lại, không cần job nền.
"""

import time
from datetime import datetime, timedelta, timezone

from config import OWNER_NAME
from core.store import JsonStore

VN = timezone(timedelta(hours=7))
STORE = JsonStore("baotri.json", {
    "global": {"on": False, "until": 0},
    "cmds": {},
    "note": "",
})

# Tên gọi khác -> tên lệnh chuẩn (để /baotri nhac off khoá luôn /mp3)
ALIASES = {
    "menu": "start", "trogiup": "help",
    "nhac": "mp3", "music": "mp3",
    "capweb": "capwed", "screenshot": "capwed",
    "cover": "banner", "taobanner": "banner",
    "weather": "thoitiet", "translate": "dich",
    "id": "getid", "getemoji": "js", "emojiuid": "js",
    "18": "anh18", "anhsex": "anh18",
    "mong": "anh18", "girl": "anh18", "bu": "anh18", "gainhat": "anh18",
    "japcosplay": "anh18", "tw": "anh18", "anime": "anh18", "sagiri": "anh18",
    "admin": "panel",
    "quangcao": "qc", "ads": "qc", "qcstop": "qc",
}


def canon(cmd: str) -> str:
    """Chuẩn hoá tên lệnh: bỏ dấu /, đổi bí danh về tên gốc."""
    c = (cmd or "").strip().lower().lstrip("/")
    return ALIASES.get(c, c)


def parse_duration(raw: str) -> int:
    """'30m' / '2h' / '1d' / '90' (mặc định phút) -> số giây. Không hiểu -> 0."""
    s = (raw or "").strip().lower().replace(" ", "")
    if not s:
        return 0
    mult = 60
    if s[-1] == "h":
        mult = 3600
    elif s[-1] == "d":
        mult = 86400
    if s[-1].isalpha():
        s = s[:-1]
    try:
        return max(0, int(float(s) * mult))
    except (TypeError, ValueError):
        return 0


def human_left(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds <= 0:
        return ""
    h, m = seconds // 3600, (seconds % 3600) // 60
    if h and m:
        return f"{h}h{m:02d}p"
    if h:
        return f"{h}h"
    if m:
        return f"{m}p"
    return f"{seconds}s"


def _left_text(until: int) -> str:
    if not until:
        return ""
    return human_left(until - time.time())


# ------------------------------------------------------------------ toàn bot
def _g() -> dict:
    return STORE.data.setdefault("global", {"on": False, "until": 0})


def global_on() -> bool:
    g = _g()
    if not g.get("on"):
        return False
    until = int(g.get("until") or 0)
    if until and time.time() >= until:
        g["on"] = False
        g["until"] = 0
        STORE.save()
        return False
    return True


def global_until() -> int:
    return int(_g().get("until") or 0)


def set_global(on: bool, seconds: int = 0) -> None:
    g = _g()
    g["on"] = bool(on)
    g["until"] = int(time.time() + seconds) if (on and seconds > 0) else 0
    STORE.save()


# ------------------------------------------------------------------ từng lệnh
def cmds() -> dict:
    return STORE.data.setdefault("cmds", {})


def cmd_on(name: str) -> bool:
    name = canon(name)
    row = cmds().get(name)
    if not isinstance(row, dict):
        return False
    until = int(row.get("until") or 0)
    if until and time.time() >= until:
        cmds().pop(name, None)
        STORE.save()
        return False
    return bool(row.get("on"))


def cmd_until(name: str) -> int:
    row = cmds().get(canon(name)) or {}
    return int(row.get("until") or 0) if isinstance(row, dict) else 0


def set_cmd(name: str, on: bool, seconds: int = 0) -> None:
    name = canon(name)
    if not name:
        return
    table = cmds()
    if not on:
        table.pop(name, None)
        STORE.save()
        return
    table[name] = {
        "on": True,
        "until": int(time.time() + seconds) if seconds > 0 else 0,
    }
    STORE.save()


def disabled() -> list[tuple[str, int]]:
    """[(tên_lệnh, giây_còn_lại)] — chỉ những lệnh đang bảo trì."""
    out: list[tuple[str, int]] = []
    for name in list(cmds().keys()):
        if cmd_on(name):
            until = cmd_until(name)
            out.append((name, max(0, int(until - time.time())) if until else 0))
    return sorted(out)


# ------------------------------------------------------------------ lời nhắn
def note() -> str:
    return str(STORE.data.get("note") or "")


def set_note(text: str) -> None:
    STORE.data["note"] = (text or "").strip()[:300]
    STORE.save()


def _e(text) -> str:
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def msg_text(cmd: str = "") -> str:
    """Câu trả lời khi người dùng đụng vào phần đang bảo trì."""
    head = "🛠 <b>BẢO TRÌ</b>"
    if cmd:
        body = f"Lệnh <code>/{canon(cmd)}</code> đang bảo trì, bạn quay lại sau nhé."
    else:
        body = "Bot đang bảo trì, bạn quay lại sau nhé."
    extra = ""
    if note():
        extra += f"\n📌 <i>{_e(note())}</i>"
    until = cmd_until(cmd) if cmd else global_until()
    if until:
        extra += f"\n⏳ <i>Dự kiến mở lại sau {human_left(until - time.time())}.</i>"
    return f"{head}\n{body}{extra}\n👤 <i>{OWNER_NAME}</i>"


def status_text() -> str:
    now = datetime.now(VN).strftime("%H:%M %d/%m")
    lines = ["🛠 <b>TRẠNG THÁI BẢO TRÌ</b>", f"<i>{now} · giờ VN</i>", "━━━━━━━━━━━━━━━"]

    if global_on():
        until = global_until()
        left = f" · còn {human_left(until - time.time())}" if until else " · không hẹn giờ"
        lines.append(f"🚫 <b>Toàn bot</b>: ĐANG BẢO TRÌ{left}")
    else:
        lines.append("✅ <b>Toàn bot</b>: đang chạy bình thường")

    items = disabled()
    if items:
        lines.append("")
        lines.append("▍<b>Lệnh đang bảo trì</b>")
        for name, left in items:
            tail = f" · còn {human_left(left)}" if left else ""
            lines.append(f"• <code>/{name}</code>{tail}")
    else:
        lines.append("▍<b>Lệnh riêng</b>: không khoá lệnh nào")

    if note():
        lines.append("")
        lines.append(f"📌 <i>{_e(note())}</i>")

    lines += [
        "━━━━━━━━━━━━━━━",
        "<b>Cách dùng</b> (admin)",
        "<code>/baotri on</code> · <code>/baotri off</code> — toàn bot",
        "<code>/baotri on 30m</code> — tự mở sau 30 phút (m/h/d)",
        "<code>/baotri sing off</code> — khoá 1 lệnh",
        "<code>/baotri sing on 2h</code> — khoá 1 lệnh có hẹn giờ",
        "<code>/baotri list</code> · <code>/baotri msg nội dung</code>",
    ]
    return "\n".join(lines)

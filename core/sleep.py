#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GIỜ NGỦ của nhóm — admin tự đặt, bot tự khoá/mở.

Ví dụ: 23:00 → 06:00 (qua đêm). Trong khung giờ đó bot set quyền nhóm thành
"không ai gửi tin được" (trừ admin) và gửi 1 thông báo; hết giờ tự mở lại
đúng quyền cũ (bot lưu lại quyền trước khi khoá).

Lệnh (dùng trong nhóm, cần quyền quản trị):

    /sleep                      → xem cấu hình + nút bấm
    /sleep 23:00 06:00          → bật, ngủ 23:00–06:00
    /sleep 23:00-06:00          → như trên
    /sleep off                  → tắt
    /sleep lock on|off          → on = khoá chat thật, off = chỉ thông báo
    /sleep msg <nội dung>       → đổi lời nhắn (bỏ trống = mặc định)
    /sleep now | /sleep wake    → chạy thử ngay (test)
"""

import logging
import time
from datetime import datetime, timedelta, timezone

from telegram import ChatPermissions, InlineKeyboardMarkup
from telegram.error import BadRequest, Forbidden, RetryAfter

from core import buttons as B
from core.store import JsonStore
from core.text import esc

log = logging.getLogger("khoaan.sleep")

VN = timezone(timedelta(hours=7))
STORE = JsonStore("sleep.json", {"chats": {}})

TEXT_SLEEP = (
    "🌙 <b>{chat}</b> đã vào <b>giờ ngủ</b>\n"
    "Thời gian: <b>{start} – {end}</b>\n"
    "<i>Mọi người nghỉ ngơi nhé, sáng bot tự mở lại.</i>"
)
TEXT_WAKE = (
    "☀️ <b>{chat}</b> đã mở lại\n"
    "Chúc mọi người một ngày tốt lành!"
)

LOCKED = ChatPermissions(can_send_messages=False)
OPEN = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
    can_invite_users=True,
)


# ---------------------------------------------------------------- store
def _chats() -> dict:
    raw = STORE.data.setdefault("chats", {})
    if not isinstance(raw, dict):
        raw = {}
        STORE.data["chats"] = raw
    return raw


def cfg(chat_id: int) -> dict:
    c = _chats().get(str(chat_id))
    if not isinstance(c, dict):
        c = {}
        _chats()[str(chat_id)] = c
    c.setdefault("on", 0)
    c.setdefault("start", "23:00")
    c.setdefault("end", "06:00")
    c.setdefault("lock", 1)
    c.setdefault("msg", "")
    c.setdefault("asleep", 0)          # trạng thái bot đã khoá chưa
    c.setdefault("perms", None)        # quyền cũ để trả lại
    c.setdefault("seen", 0)            # lần cuối kiểm tra
    return c


def save() -> None:
    STORE.save()


def all_chats() -> list[int]:
    out = []
    for k, v in _chats().items():
        if isinstance(v, dict) and v.get("on"):
            try:
                out.append(int(k))
            except Exception:          # noqa: BLE001
                continue
    return out


def enabled(chat_id: int) -> bool:
    return bool(cfg(chat_id).get("on"))


# ---------------------------------------------------------------- giờ
def parse_hm(raw: str) -> int | None:
    """'23:00' | '23h' | '2300' | '6' → số phút trong ngày."""
    s = str(raw or "").strip().lower().replace("h", ":").replace(".", ":")
    if not s:
        return None
    if ":" in s:
        a, _, b = s.partition(":")
        if not a.isdigit():
            return None
        h = int(a)
        m = int(b) if b.isdigit() else 0
    elif s.isdigit():
        if len(s) <= 2:
            h, m = int(s), 0
        elif len(s) in (3, 4):
            h, m = int(s[:-2]), int(s[-2:])
        else:
            return None
    else:
        return None
    if h > 23 or m > 59:
        return None
    return h * 60 + m


def hm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def in_window(now_min: int, start: int, end: int) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= now_min < end
    return now_min >= start or now_min < end          # qua đêm


def now_min() -> int:
    t = datetime.now(VN)
    return t.hour * 60 + t.minute


def until_text(start: int, end: int) -> str:
    """Còn bao lâu nữa tới giờ ngủ / tới giờ mở (dạng dễ đọc)."""
    cur = now_min()
    if in_window(cur, start, end):
        left = (end - cur) % (24 * 60)
        return f"còn {left // 60} giờ {left % 60} phút nữa mở lại"
    left = (start - cur) % (24 * 60)
    return f"{left // 60} giờ {left % 60} phút nữa vào giờ ngủ"


# ---------------------------------------------------------------- khoá / mở
def _perms_dict(perms) -> dict | None:
    try:
        return perms.to_dict() if perms is not None else None
    except Exception:              # noqa: BLE001
        return None


_PERM_FIELDS = (
    "can_send_messages", "can_send_polls", "can_send_other_messages",
    "can_add_web_page_previews", "can_change_info", "can_invite_users",
    "can_pin_messages", "can_manage_topics", "can_send_audios", "can_send_documents",
    "can_send_photos", "can_send_videos", "can_send_video_notes", "can_send_voice_notes",
)


def _perms_from(data) -> ChatPermissions:
    if isinstance(data, dict) and data:
        try:
            return ChatPermissions(**{k: bool(data[k]) for k in _PERM_FIELDS if k in data})
        except Exception:          # noqa: BLE001
            return OPEN
    return OPEN


def _sleep_text(chat_title: str, start: int, end: int, custom: str = "") -> str:
    if custom:
        return (custom.replace("{chat}", chat_title or "nhóm")
                .replace("{start}", hm(start)).replace("{end}", hm(end)))[:600]
    return TEXT_SLEEP.format(chat=esc(chat_title or "nhóm"), start=hm(start), end=hm(end))


def _wake_text(chat_title: str, start: int, end: int) -> str:
    return TEXT_WAKE.format(chat=esc(chat_title or "nhóm"))


async def close_chat(bot, chat_id: int, title: str = "", silent: bool = False) -> bool:
    """Khoá chat + thông báo. Trả True nếu đã khoá."""
    c = cfg(chat_id)
    if not c.get("lock", 1):
        c["asleep"] = 1
        save()
        return True
    try:
        chat = await bot.get_chat(chat_id)
        c["perms"] = _perms_dict(getattr(chat, "permissions", None))
        title = title or getattr(chat, "title", "") or ""
    except Exception:              # noqa: BLE001
        pass
    try:
        await bot.set_chat_permissions(chat_id, LOCKED)
    except (BadRequest, Forbidden) as e:
        log.info("không khoá được %s: %s", chat_id, str(e)[:90])
        return False
    except RetryAfter as e:
        log.info("flood khi khoá %s: %s", chat_id, e.retry_after)
        return False
    c["asleep"] = 1
    save()
    if not silent:
        await send_note(bot, chat_id, _sleep_text(title, parse_hm(c["start"]) or 0,
                                                  parse_hm(c["end"]) or 0, c.get("msg") or ""),
                        kind="sleep")
    return True


async def open_chat(bot, chat_id: int, title: str = "", silent: bool = False) -> bool:
    """Mở lại chat đúng quyền cũ + thông báo."""
    c = cfg(chat_id)
    perms = _perms_from(c.get("perms"))
    try:
        await bot.set_chat_permissions(chat_id, perms)
    except (BadRequest, Forbidden) as e:
        log.info("không mở được %s: %s", chat_id, str(e)[:90])
        return False
    except RetryAfter as e:
        log.info("flood khi mở %s: %s", chat_id, e.retry_after)
        return False
    c["asleep"] = 0
    save()
    if not silent:
        if not title:
            try:
                title = (await bot.get_chat(chat_id)).title or ""
            except Exception:      # noqa: BLE001
                title = ""
        await send_note(bot, chat_id, _wake_text(title, 0, 0), kind="wake")
    return True


async def send_note(bot, chat_id: int, text: str, kind: str = "sleep") -> None:
    """Gửi thông báo kèm nút (icon + màu)."""
    kb = None
    if kind == "sleep":
        kb = B.kb([B.row(
            B.btn("Đánh thức", cb="sl:wake", style=B.GREEN, icon="🌙"),
            B.btn("Tắt giờ ngủ", cb="sl:off", style=B.RED, icon="❌"),
        ), B.row(
            B.btn("Cấu hình", cb="sl:panel", style=B.BLUE, icon="⚡"),
        )])
    try:
        await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb,
                               disable_notification=True)
    except Exception as e:         # noqa: BLE001
        log.info("gửi thông báo giờ ngủ lỗi: %s", str(e)[:90])


# ---------------------------------------------------------------- job
async def tick(context) -> None:
    """Chạy mỗi phút: bật/tắt theo khung giờ của từng nhóm."""
    bot = context.bot
    cur = now_min()
    for chat_id in all_chats():
        c = cfg(chat_id)
        start = parse_hm(c.get("start")) or 0
        end = parse_hm(c.get("end")) or 0
        want = in_window(cur, start, end)
        asleep = bool(c.get("asleep"))
        if want and not asleep:
            await close_chat(bot, chat_id)
        elif not want and asleep:
            await open_chat(bot, chat_id)
        c["seen"] = int(time.time())
        save()


# ---------------------------------------------------------------- panel
def panel_text(chat_id: int, chat_title: str = "") -> str:
    c = cfg(chat_id)
    start = parse_hm(c.get("start")) or 0
    end = parse_hm(c.get("end")) or 0
    state = "🌙 đang ngủ (đã khoá)" if c.get("asleep") else "☀️ đang mở"
    return (
        "🌙 <b>GIỜ NGỦ CỦA NHÓM</b>\n"
        f"<i>{esc(chat_title or 'nhóm này')}</i>\n"
        "───────────────\n"
        f"├ Trạng thái: <b>{'BẬT' if c.get('on') else 'TẮT'}</b> · {state}\n"
        f"├ Khung giờ: <b>{hm(start)} → {hm(end)}</b>\n"
        f"├ Kiểu: <b>{'khoá chat' if c.get('lock') else 'chỉ thông báo'}</b>\n"
        f"└ {until_text(start, end) if c.get('on') else 'bật lên để dùng'}\n"
        "\n"
        "<b>Lệnh:</b>\n"
        "<code>/sleep 23:00 06:00</code> — bật\n"
        "<code>/sleep off</code> — tắt\n"
        "<code>/sleep lock on|off</code> — có khoá chat không\n"
        "<code>/sleep msg nội dung</code> — lời nhắn riêng\n"
        "<code>/sleep now</code> · <code>/sleep wake</code> — chạy thử\n"
        "\n"
        "<i>Bot cần quyền “Hạn chế thành viên” (Restrict members) trong nhóm.</i>"
    )


def panel_kb(chat_id: int) -> InlineKeyboardMarkup:
    c = cfg(chat_id)
    on = bool(c.get("on"))
    lock = bool(c.get("lock"))
    rows = [
        B.row(
            B.btn("Tắt giờ ngủ" if on else "Bật giờ ngủ",
                  cb="sl:toggle", style=B.RED if on else B.GREEN,
                  icon="❌" if on else "🌙"),
            B.btn("Khoá chat: BẬT" if lock else "Khoá chat: TẮT",
                  cb="sl:lock", style=B.BLUE, icon="🔒"),
        ),
        B.row(
            B.btn("Ngủ thử ngay", cb="sl:sleepnow", style=B.BLUE, icon="⚡"),
            B.btn("Mở lại ngay", cb="sl:wake", style=B.GREEN, icon="☀️"),
        ),
        B.row(B.btn("Làm mới", cb="sl:panel", style=B.BLUE, icon="🔄")),
    ]
    return B.kb(rows)

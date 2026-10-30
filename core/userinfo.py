#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lấy hồ sơ người dùng Telegram: tên, ID, username, TIỂU SỬ và ẢNH ĐẠI DIỆN.

Dùng cho /id — trước đây chỉ in được tên + ID vì thiếu bước get_chat + get_user_profile_photos.
"""

import io
import logging

from core.text import esc, now_vn

log = logging.getLogger("khoaan.userinfo")


def _name(obj) -> str:
    if obj is None:
        return "—"
    for attr in ("full_name", "title"):
        val = getattr(obj, attr, None)
        if val:
            return str(val)
    first = getattr(obj, "first_name", "") or ""
    last = getattr(obj, "last_name", "") or ""
    return (" ".join(x for x in (first, last) if x)).strip() or "—"


async def fetch(bot, uid: int) -> dict:
    """Trả {'full': ChatFullInfo|None, 'avatar': bytes|None, 'err': str}."""
    out: dict = {"full": None, "avatar": None, "err": ""}
    if not uid:
        return out
    try:
        out["full"] = await bot.get_chat(uid)
    except Exception as e:  # noqa: BLE001
        out["err"] = str(e)[:90]
        log.info("get_chat %s lỗi: %s", uid, out["err"])

    try:
        photos = await bot.get_user_profile_photos(user_id=uid, limit=1)
        if photos and photos.total_count:
            sizes = photos.photos[0] if photos.photos else []
            if sizes:
                tg_file = await bot.get_file(sizes[-1].file_id)
                out["avatar"] = bytes(await tg_file.download_as_bytearray())
    except Exception as e:  # noqa: BLE001
        log.info("lấy avatar %s lỗi: %s", uid, str(e)[:90])
        if not out["err"]:
            out["err"] = str(e)[:90]
    return out


def build(obj, full, how: str = "", caller_id: int | None = None,
          chat=None) -> str:
    """Dựng caption/nội dung cho /id. `obj` = User/Chat, `full` = ChatFullInfo."""
    uid = getattr(obj, "id", None) or getattr(full, "id", None) or 0
    name = _name(full) if _name(full) != "—" else _name(obj)
    username = getattr(obj, "username", None) or getattr(full, "username", None) or ""
    bio = getattr(full, "bio", None) or ""
    lang = getattr(obj, "language_code", None) or getattr(full, "language_code", None) or ""
    premium = getattr(obj, "is_premium", None)
    if premium is None:
        premium = getattr(full, "is_premium", None)
    is_bot = getattr(obj, "is_bot", None)
    if is_bot is None:
        is_bot = getattr(full, "is_bot", None)

    lines = [
        "👤 <b>THÔNG TIN NGƯỜI DÙNG</b>",
        "──────────────",
        f"👑 <b>Tên:</b> {esc(name)}",
        f"🆔 <b>User ID:</b> <code>{uid}</code>",
        f"🔗 <b>Username:</b> " + (f"@{esc(username)}" if username else "<i>không có</i>"),
        f"💬 <b>Tiểu sử:</b> " + (esc(bio) if bio else "<i>không có / ẩn</i>"),
    ]
    if lang:
        lines.append(f"🌐 <b>Ngôn ngữ:</b> {esc(lang)}")
    if premium is not None:
        lines.append(f"💎 <b>Premium:</b> {'Có' if premium else 'Không'}")
    if is_bot is not None:
        lines.append(f"🤖 <b>Bot:</b> {'Có' if is_bot else 'Không'}")

    msg_chat = chat
    if msg_chat is not None and getattr(msg_chat, "id", None) != uid:
        lines += [
            "──────────────",
            f"🏠 <b>Chat hiện tại:</b> <code>{getattr(msg_chat, 'id', 0)}</code>",
            f"📛 <b>Loại chat:</b> {esc(str(getattr(msg_chat, 'type', '—')))}",
        ]

    lines.append("──────────────")
    if how:
        lines.append(f"🔍 <b>Nguồn:</b> {esc(how)}")
    if caller_id and caller_id != uid:
        lines.append(f"🙋 <b>Người hỏi:</b> <code>{caller_id}</code>")
    lines.append(f"🕐 <i>{now_vn()}</i>")
    return "\n".join(lines)


def avatar_or_none(raw: bytes | None):
    """Bọc bytes ảnh thành BytesIO có tên tệp (Telegram cần tên để đoán định dạng)."""
    if not raw or len(raw) < 64:
        return None
    buf = io.BytesIO(raw)
    buf.name = "avatar.jpg"
    return buf

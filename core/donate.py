#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lệnh /donate — gửi QR ủng hộ + nội dung chuyển khoản random theo user.

- Ảnh QR: assets/donate_qr.jpg (đổi ảnh = thay file, không cần sửa code).
- Mỗi lần gọi /donate: random 1 mã CK + 1 câu cảm ơn, kèm @username và id người gọi.
- Nút 🔁 Nội dung khác: đổi mã/câu khác mà không gửi lại ảnh.
"""

import logging
import random
import re
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import config
from core import premium

log = logging.getLogger("khoaan.donate")

QR_NAMES = ("donate_qr.jpg", "donate_qr.png", "donate_qr.webp")

PREFIXES = ("CF", "DONATE", "UONGCF", "MOM", "CAFE", "TANG", "SUPPORT")

THANKS = (
    "Cảm ơn bạn rất nhiều vì đã ủng hộ {brand} 💚",
    "Có bạn là bot có thêm động lực chạy 24/7 ☕",
    "Ly cà phê này mình xin nhận và hứa code tiếp thật mượt 🔥",
    "Cảm ơn {name} — bạn là lý do bot vẫn sống khoẻ mỗi ngày ✨",
    "Ủng hộ nhỏ, tình cảm lớn. Cảm ơn bạn nhiều 🙏",
    "Đã ghi sổ vàng: {name} đã ủng hộ {brand} 📒✨",
    "Cảm ơn bạn! Tiền điện server tháng này có bạn lo rồi 😆",
    "Món quà của bạn giúp bot thêm chủ đề, thêm lệnh mới 🚀",
)

EMOJI = ("💖", "☕", "🔥", "✨", "🍀", "🎁", "🌙", "💚")

CONTACT_TEXT = "💬 Nhắn admin: {url}"


def esc(text) -> str:
    return (str(text or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


def qr_path() -> Path:
    """Tìm ảnh QR: assets/ trước, rồi tới DATA_DIR/. Không có thì trả Path rỗng."""
    for folder in (Path(config.BASE_DIR) / "assets", Path(config.DATA_DIR)):
        for name in QR_NAMES:
            p = folder / name
            if p.is_file() and p.stat().st_size > 0:
                return p
    return Path()


def _tag(user) -> str:
    """@username nếu có, không thì tên (đã bỏ ký tự lạ)."""
    uname = getattr(user, "username", "") or ""
    if uname:
        return "@" + re.sub(r"[^\w]", "", uname)
    name = getattr(user, "first_name", "") or getattr(user, "full_name", "") or "ban"
    return re.sub(r"[^\w\s.-]", "", str(name)).strip()[:32] or "ban"


def make_note(user) -> str:
    """Nội dung chuyển khoản random, luôn có @username + id người gọi."""
    code = f"{random.choice(PREFIXES)}{random.randint(100, 9999)}"
    tag = _tag(user)
    uid = int(getattr(user, "id", 0) or 0)
    # Ngân hàng giới hạn ~50-80 ký tự nội dung -> giữ gọn
    note = f"{code} {tag} {uid}"
    return note[:70]


def make_caption(user, note: str | None = None) -> str:
    note = note or make_note(user)
    brand = esc(getattr(config, "BRAND", "BOT"))
    name = esc(_tag(user))
    thanks = random.choice(THANKS).format(brand=brand, name=name)
    emo = random.choice(EMOJI)
    support = esc(getattr(config, "SUPPORT_URL", ""))
    return (
        f"{emo} <b>ỦNG HỘ {brand.upper()}</b>\n"
        f"<i>{thanks}</i>\n"
        "━━━━━━━━━━━━━━━\n"
        "📷 <b>Quét QR ở trên</b> để chuyển (MoMo / ngân hàng tuỳ QR)\n"
        f"📝 Nội dung CK: <code>{esc(note)}</code>\n"
        f"🆔 Người ủng hộ: <b>{name}</b> · <code>{int(getattr(user, 'id', 0) or 0)}</code>\n"
        "━━━━━━━━━━━━━━━\n"
        f"{CONTACT_TEXT.format(url=support)}\n"
        "<i>Bấm 🔁 để đổi nội dung CK khác.</i>"
    )


def donate_kb(user) -> InlineKeyboardMarkup:
    from core import buttons as B

    rows = [B.row(B.btn("Nội dung khác", cb=f"dn:new:{int(getattr(user, 'id', 0) or 0)}",
                        style=B.BLUE, icon="💬"))]
    support = getattr(config, "SUPPORT_URL", "")
    if support:
        rows.append(B.row(B.btn("Liên hệ admin", url=support, style=B.GREEN, icon="💬")))
    return B.kb(rows)


async def send_qr(bot, chat_id: int, user, reply_to: int | None = None):
    """Gửi QR + caption random tới 1 chat. Dùng cho /donate và nút trong panel."""
    cap = premium.ensure(make_caption(user))
    kb = donate_kb(user)
    path = qr_path()
    if path:
        try:
            with open(path, "rb") as f:
                return await bot.send_photo(chat_id, photo=f, caption=cap,
                                            parse_mode=ParseMode.HTML,
                                            reply_markup=kb,
                                            reply_to_message_id=reply_to)
        except Exception as e:  # noqa: BLE001
            log.warning("donate gui anh loi: %s", str(e)[:160])
    # Không có / không gửi được ảnh -> vẫn gửi nội dung để user không bị chặn
    return await bot.send_message(chat_id,
                                  cap + "\n\n<i>(Chưa có ảnh QR trong assets/donate_qr.jpg)</i>",
                                  parse_mode=ParseMode.HTML, reply_markup=kb,
                                  disable_web_page_preview=True,
                                  reply_to_message_id=reply_to)


async def cmd_donate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/donate — gửi QR + nội dung random."""
    msg = update.effective_message
    user = update.effective_user
    if msg is None or user is None:
        return
    await send_qr(context.bot, msg.chat_id, user, reply_to=msg.message_id)


async def cb_donate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nút 🔁 đổi nội dung CK — sửa caption của chính tin nhắn QR."""
    q = update.callback_query
    if q is None:
        return
    try:
        await q.answer("Đã đổi nội dung ✅")
    except Exception:  # noqa: BLE001
        pass
    user = q.from_user
    cap = premium.ensure(make_caption(user))
    try:
        await q.message.edit_caption(caption=cap, parse_mode=ParseMode.HTML,
                                     reply_markup=donate_kb(user))
    except Exception as e:  # noqa: BLE001
        log.warning("donate edit caption loi: %s", str(e)[:160])
        try:
            await q.message.reply_text(cap, parse_mode=ParseMode.HTML,
                                       reply_markup=donate_kb(user),
                                       disable_web_page_preview=True)
        except Exception:  # noqa: BLE001
            pass

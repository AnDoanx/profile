#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Nút inline có ICON + MÀU.

Telegram Bot API cho phép mỗi nút inline mang 2 field mới:

    style                 -> "danger" (đỏ) | "success" (xanh lá) | "primary" (xanh dương)
    icon_custom_emoji_id  -> icon premium (custom emoji) đặt trước chữ

python-telegram-bot 21.x chưa khai báo 2 field này, nhưng nó có `api_kwargs`
nên mình nhét thẳng vào payload — vẫn gửi được đúng chuẩn, không cần nâng thư viện.

Cách dùng:

    from core import buttons as B
    kb = B.kb([
        B.row(B.btn("Tải video", cb="dl:1", style=B.GREEN, icon="🎬"),
              B.btn("Xoá", cb="dl:x", style=B.RED, icon="🗑")),
        B.row(B.btn("Chi tiết", url="https://t.me/x", style=B.BLUE, icon="ℹ️")),
    ])

Nếu bot chưa đủ điều kiện dùng icon premium (chủ bot không có Telegram Premium /
chưa mua username trên Fragment), Telegram sẽ trả lỗi -> `fallback()` tự tắt
icon/màu rồi gửi lại bằng nút thường, bot không chết.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram._copytextbutton import CopyTextButton

import config
from core import premium

# ------------------------------------------------------------------ màu
RED = "danger"
GREEN = "success"
BLUE = "primary"

_STATE = {"on": bool(getattr(config, "BUTTON_ICON_PREMIUM", True))}   # v19: theo .env



def enabled() -> bool:
    return _STATE["on"]


def disable() -> None:
    _STATE["on"] = False


def enable() -> None:
    _STATE["on"] = True


# ------------------------------------------------------------------ icon
def icon_id(emoji: str) -> str | None:
    """Lấy custom_emoji_id của icon premium.

    v20: emoji không có id riêng thì lấy icon gần nghĩa (vẫn là premium) —
    nhờ vậy KHÔNG nút nào bị trống icon.
    """
    if not _STATE["on"]:
        return None
    raw = (emoji or "").strip()
    if raw.isdigit():          # data/premium_flags.json có thể nạp thẳng id số
        return raw if premium.is_valid_id(raw) else None
    cid = premium.id_of(raw)
    if cid:
        return cid
    return premium.id_of(premium.pick_icon(raw))


# ------------------------------------------------------------------ copy_text
def copy_obj(value) -> "CopyTextButton":
    """Telegram Bot API 9.x: `copy_text` phải là OBJECT {text: …}, không phải chuỗi.

    Bản python-telegram-bot cũ nhận str nên gói cũ gửi thẳng chuỗi lên -> Telegram
    trả "Can't parse inlinekeyboardbutton: field \"copy_text\" must be of type object".
    Hàm này luôn trả về CopyTextButton (tự cắt 256 ký tự theo giới hạn Telegram).
    """
    if isinstance(value, CopyTextButton):
        return value
    return CopyTextButton(text=str(value or "")[:256])


# ------------------------------------------------------------------ tạo nút
def btn(text: str, cb: str | None = None, *, url: str | None = None,
        style: str | None = None, icon: str | None = None,
        copy: str | None = None, pay: bool = False,
        disabled: bool = False) -> InlineKeyboardButton:
    """1 nút có icon + màu.

    - `icon`: emoji (vd "🎬"). v18: mặc định BỎ emoji, nút chỉ còn chữ + màu.
      Muốn hiện emoji thường: đặt BUTTON_EMOJI=1 trong .env.
    - `style`: RED / GREEN / BLUE. Bỏ trống = màu mặc định của app.
    """
    label = str(text or "").strip()
    extra: dict = {}

    if _STATE["on"]:
        # v20: nhãn nút KHÔNG còn emoji thường — icon đi qua icon_custom_emoji_id
        clean = premium.drop_emoji(label)
        if clean:
            label = clean
        cid = icon_id(icon or "") or premium.id_of(premium.pick_icon(label))
        if cid:
            extra["icon_custom_emoji_id"] = cid
    elif icon and getattr(config, "BUTTON_EMOJI", False) and not label.startswith(icon):
        label = f"{icon} {label}"

    if style and _STATE["on"]:
        extra["style"] = style

    if disabled:
        extra["disabled"] = True

    kwargs: dict = {}
    if copy:
        kwargs["copy_text"] = copy_obj(copy)
    elif url:
        kwargs["url"] = url
    elif cb is not None:
        kwargs["callback_data"] = str(cb)[:64]
    if pay:
        kwargs["pay"] = True

    return InlineKeyboardButton(label, api_kwargs=extra or None, **kwargs)


def row(*items) -> list[InlineKeyboardButton]:
    """1 hàng nút (bỏ qua item None)."""
    return [b for b in items if b is not None]


def kb(rows, per_row: int = 0) -> InlineKeyboardMarkup:
    """Ghép bàn phím. `per_row` > 0 thì tự chia hàng đều."""
    rows = [r for r in rows if r]
    if per_row > 0:
        flat = [b for r in rows for b in r]
        rows = [flat[i:i + per_row] for i in range(0, len(flat), per_row)]
    return InlineKeyboardMarkup(rows)


def grid(items: list[InlineKeyboardButton], per_row: int = 2) -> InlineKeyboardMarkup:
    return kb([items], per_row=per_row) if items else InlineKeyboardMarkup([])


# ------------------------------------------- v19: icon cho MỌI nút (kể cả nút dựng tay)
# Các chỗ dựng thẳng InlineKeyboardButton("🔑 Nhập API", …) sẽ tự có icon premium,
# không phải sửa từng dòng. Chèn ở bước serialize -> áp cho toàn bộ tin gửi đi.
def _install_auto_icon() -> None:
    if getattr(InlineKeyboardButton, "_v19_auto_icon", False):
        return

    _orig = InlineKeyboardButton.to_dict

    def _to_dict(self, *args, **kwargs):  # noqa: ANN001
        data = _orig(self, *args, **kwargs)
        try:
            if _STATE["on"]:
                txt = getattr(self, "text", "") or ""
                if "icon_custom_emoji_id" not in data:
                    # 1) emoji có sẵn trong nhãn  2) chọn theo nghĩa  3) icon mặc định
                    cid = (premium.id_of(premium.first_emoji(txt))
                           or premium.id_of(premium.pick_icon(txt)))
                    if cid:
                        data["icon_custom_emoji_id"] = cid
                # bỏ emoji thường khỏi nhãn để không hiện icon basic cạnh icon premium
                clean = premium.drop_emoji(txt)
                if clean and clean != txt:
                    data["text"] = clean
        except Exception:  # noqa: BLE001
            pass
        return data

    InlineKeyboardButton.to_dict = _to_dict
    try:
        InlineKeyboardButton._v19_auto_icon = True
    except Exception:  # noqa: BLE001
        pass


_install_auto_icon()


# ------------------------------------------------------------------ hạ cấp
def strip(markup):
    """Bỏ icon/màu khỏi bàn phím (dựng lại nút vì api_kwargs là thuộc tính chỉ-đọc)."""
    if markup is None or not hasattr(markup, "inline_keyboard"):
        return markup
    if not has_extras(markup):
        return markup
    rows = []
    for line in markup.inline_keyboard or []:
        rows.append([
            InlineKeyboardButton(
                b.text,
                url=b.url,
                callback_data=b.callback_data,
                switch_inline_query=b.switch_inline_query,
                switch_inline_query_current_chat=b.switch_inline_query_current_chat,
                callback_game=b.callback_game,
                pay=b.pay,
                login_url=b.login_url,
                web_app=b.web_app,
                switch_inline_query_chosen_chat=b.switch_inline_query_chosen_chat,
                copy_text=b.copy_text,
            )
            for b in line
        ])
    return InlineKeyboardMarkup(rows)


def has_extras(markup) -> bool:
    if markup is None or not hasattr(markup, "inline_keyboard"):
        return False
    for line in markup.inline_keyboard or []:
        for b in line:
            kw = getattr(b, "api_kwargs", None)
            if kw and ("style" in kw or "icon_custom_emoji_id" in kw):
                return True
    return False


_BAD_WORDS = ("style", "icon_custom_emoji_id", "custom_emoji", "button", "BUTTON")


def is_style_error(err: str) -> bool:
    """Lỗi có phải do style / icon premium của nút?"""
    e = str(err or "").lower()
    if any(w in e for w in _BAD_WORDS) and ("button" in e or "keyboard" in e or "style" in e or "emoji" in e):
        return True
    return False

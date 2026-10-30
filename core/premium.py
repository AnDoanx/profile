#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Icon Premium (custom emoji của Telegram) — tự động thay emoji thường bằng bản premium.

Cách dùng: bọc mọi text gửi đi bằng `premium.p(text)`.
Nếu bot/client không hỗ trợ, text tự trả về emoji thường như cũ.
"""

import logging
import re

import config

log = logging.getLogger("khoaan.premium")

# emoji thường -> custom_emoji_id (đều đã được xác thực qua Bot API)
RAW: dict[str, str] = {
    "👑": "5042225965518816316",
    "🎵": "5258289810082111221",
    "▶️": "5258084656674250503",
    "🎬": "5258084656674250503",
    "🎥": "5258084656674250503",
    "🎤": "5260652149469094137",
    "📸": "5257974976094412956",
    "🎨": "5257974976094412956",
    "🖼": "5257974976094412956",
    "🌤": "5258453452631056344",
    "☀️": "5258453452631056344",
    "🧰": "5258089153505009279",
    "⚙️": "5258089153505009279",
    "🔧": "5258089153505009279",
    "🔍": "5260730055880876557",
    "🔎": "5260730055880876557",
    "🔗": "5258105663359294787",
    "🏠": "5260450573768990626",
    "👤": "5258476306152038031",
    "👥": "5258513401784573443",
    "🛡": "5258430848218176413",
    "🎟": "5258185631355378853",
    "📣": "5260325873688518261",
    "🛠": "5258089153505009279",
    "🔒": "5258430848218176413",
    "✅": "5258474669769497337",
    "❌": "5267123797600783095",
    "⏳": "5280867942056108177",
    "⚠️": "5260512129240276089",
    "⭐": "5258185631355378853",
    "🌟": "5042200814190330758",
    "🏆": "5258185631355378853",
    "ℹ️": "5287595522993991899",
    "📄": "5258391252914676042",
    "📋": "5258391252914676042",
    "🗂": "5258391252914676042",
    "🧾": "5258391252914676042",
    "🔊": "5260325873688518261",
    "🎞": "5258212268742549391",
    "🔄": "5258420634785947640",
    "✔️": "5357069174512303778",
    "👀": "5258362429389152256",
    "✖️": "5258267368877989660",
    "💖": "5039753786638205957",
    "💎": "5042050649248760772",
    "🪙": "5041975203853239332",
    "🗑": "5039614900280754969",
    "🧹": "5039614900280754969",
    "🌙": "5041906342642582678",
    "👍": "5042155068493661440",
    "📊": "5042290883949495533",
    "📈": "5042290883949495533",
    "📶": "5042290883949495533",
    "💰": "5039789890133296083",
    "🎧": "5041955142060999726",
    "🌐": "5039727604517570274",
    "🌍": "5039727604517570274",
    "📍": "5039895103947146186",
    "🎆": "5041976440803820473",
    "💬": "5040036030414062506",
    "🎁": "5039686583284925280",
    "⚡": "5042334757040423886",
    # --- bổ sung: các icon còn dùng trong menu/help/getinfo/thời tiết ---
    "🔥": "5170202955713872686",   # tăng tốc (id thật, lấy từ pack công khai)
    "👉": "5172769263097741912",
    "😀": "5170606072754340445",
    "⏱": "5280867942056108177",   # ⏳ đồng hồ cát
    "⏰": "5280867942056108177",
    "🕐": "5280867942056108177",
    "🐢": "5280867942056108177",
    "⬇": "5280867942056108177",
    "✨": "5041976440803820473",   # 🎆 lấp lánh
    "📌": "5039895103947146186",   # 📍 ghim
    "📖": "5258391252914676042",   # 📄 tài liệu
    "💾": "5258391252914676042",
    "🖥": "5257974976094412956",   # 📸
    "📱": "5257974976094412956",
    "🌡": "5258453452631056344",   # 🌤
    "💧": "5258453452631056344",
    "💨": "5258453452631056344",
    "🎭": "5040036030414062506",   # 💬
    "☕": "5039753786638205957",   # 💖
    "➕": "5258476306152038031",   # 👤
    "🔙": "5260450573768990626",   # 🏠
    # --- v11: banner theo tên / captcha / id / tiểu sử ---
    "🖌": "5257974976094412956",   # cọ vẽ (dùng id 🎨)
    "📐": "5257974976094412956",   # kích thước
    "✏️": "5257974976094412956",   # bút
    "🔁": "5258420634785947640",   # tạo lại (id 🔄)
    "👇": "5172769263097741912",   # chỉ xuống (id 👉)
    "🎉": "5041976440803820473",   # chúc mừng (id 🎆)
    "🤖": "5258089153505009279",   # bot (id ⚙️)
    "📛": "5258391252914676042",   # loại chat (id 📄)
    "🙋": "5258476306152038031",   # người hỏi (id 👤)
    "🏷": "5258391252914676042",   # nhãn
    "🆔": "5258476306152038031",   # ID (id 👤)
}

# ------------------------------------------------------------------ v19: phủ 100%
# Bảng id lấy từ các bộ emoji công khai (core/premium_ids.py) — chỉ để LẤP chỗ trống,
# id nào đã có trong RAW ở trên thì giữ nguyên bản đã xác thực.
try:
    from core.premium_ids import EXTRA as _EXTRA
except Exception:  # noqa: BLE001
    _EXTRA = {}

# Ký hiệu / emoji chưa có bộ riêng -> mượn icon gần nghĩa nhất (vẫn là icon premium).
_FALLBACK: dict[str, str] = {
    # mũi tên + hình khối (dùng làm bullet, mũi tên chỉ dẫn, spinner)
    "←": "⬅️", "⬅": "⬅️", "◀": "⬅️", "↩": "⬅️", "↰": "⬅️", "◁": "⬅️", "«": "⬅️",
    "→": "➡️", "➡": "➡️", "▶": "➡️", "▸": "➡️", "▹": "➡️", "↳": "➡️", "↗": "➡️", "»": "➡️",
    "↑": "⬆️", "⬆": "⬆️", "▲": "⬆️", "△": "⬆️",
    "↓": "⬇️", "⬇": "⬇️", "▼": "⬇️", "▽": "⬇️",
    "↻": "🔄", "⇄": "🔄", "♻": "🔄", "⟳": "🔄",
    "⌛": "⏳", "⌨": "💻",
    "▫": "⚪", "⬜": "⚪", "○": "⚪", "◻": "⚪", "□": "⚪", "∙": "⚪",
    "●": "🔘", "◆": "🔘", "◈": "🔘", "◉": "🔘", "▪": "🔘", "◾": "🔘", "•": "🔘",
    "✦": "⭐", "✷": "⭐", "★": "⭐", "☆": "⭐", "✧": "⭐",
    "✓": "✅", "✔": "✅",
    "✕": "❌", "✖": "❌", "✗": "❌", "✘": "❌",
    "✍": "📝", "☀": "☀️", "⚙": "⚙️", "⚠": "⚠️", "✂": "✂️",
    "♀": "👤", "♂": "👤", "≡": "📋", "⌕": "🔍", "⌫": "🗑", "⌦": "🗑",
    "▣": "🖼", "◍": "🎙", "◐": "🖼", "♫": "🎵", "♪": "🎵", "♥": "💖",
    # emoji chưa có bộ riêng -> mượn icon gần nghĩa
    "🌬": "🌤", "🍚": "🍰", "🍜": "🍰", "🍪": "🍰", "🍽": "🍰", "🍕": "🍰", "🍔": "🍰",
    "🎀": "🌸", "🎩": "🎁", "🐟": "🐳", "👕": "🕶", "💄": "🕶", "💇": "🕶",
    "💊": "💤", "🔬": "🔍", "🪑": "📦", "🪞": "👀", "🕳": "👀", "🛞": "⚙️",
    "🚶": "👟", "🤲": "🙏", "🤷": "😑", "💦": "💧", "💩": "😂", "💯": "🔥",
    "💿": "🎵", "📒": "📄", "📘": "📄", "📗": "📄", "📙": "📄", "📓": "📄",
    "📨": "✉️", "🔞": "🔴", "🛑": "⛔", "🏳": "🚫", "👎": "😑", "🪙": "💰",
}

RAW = {**_EXTRA, **_FALLBACK, **RAW}   # RAW gốc (đã xác thực) luôn thắng

# thêm biến thể có/không có VS16 (︎️) để khớp mọi kiểu gõ
_MAP: dict[str, str] = {}
for _k, _v in RAW.items():
    _MAP[_k] = _v
    _MAP[_k + "\ufe0f"] = _v
    _MAP[_k.replace("\ufe0f", "")] = _v

_KEYS = sorted(_MAP, key=len, reverse=True)
_RE = re.compile("|".join(re.escape(k) for k in _KEYS))
_TAG_RE = re.compile(r"<tg-emoji\b[^>]*>(.*?)</tg-emoji>", re.S)

ENABLED = getattr(config, "EMOJI_PREMIUM", True)
_valid: set[str] = set(_MAP.values())  # trước khi probe thì tin tưởng (có fallback khi lỗi)
_PROBED = {"on": False}                 # probe() xong -> chỉ dùng id đã xác thực
_CUSTOM_IDS: set[str] = set()           # id dán tay (data/premium_flags.json) -> probe cùng


def register_ids(*ids: str) -> None:
    """Nạp thêm id tự nhập để probe() kiểm tra chung với bộ id có sẵn."""
    for i in ids:
        i = str(i or "").strip()
        if i.isdigit():
            _CUSTOM_IDS.add(i)


def is_valid_id(cid: str | None) -> bool:
    """ID có dùng được không.

    Trước khi probe: tin tưởng (Telegram từ chối thì có fallback).
    Sau khi probe: chỉ nhận id đã được Bot API xác nhận — tránh 1 id hỏng
    (vd. id dán tay vào data/premium_flags.json) làm Telegram từ chối CẢ tin
    và bot phải bỏ hết icon của mọi nút.
    """
    if not cid:
        return False
    if cid in _valid:
        return True
    return not _PROBED["on"]


def _wrap(m) -> str:
    ch = m.group(0)
    cid = _MAP.get(ch)
    if not cid or cid not in _valid:
        return ch
    return f'<tg-emoji emoji-id="{cid}">{ch}</tg-emoji>'


_CODE_RE = re.compile(r"(<code>.*?</code>|<pre>.*?</pre>)", re.S)


def p(text: str) -> str:
    """Đổi emoji thường -> premium. Bỏ qua phần trong <code>/<pre> (không phá cú pháp)."""
    if not ENABLED or not text or "<tg-emoji" in text:
        return text
    try:
        parts = _CODE_RE.split(text)
        out = []
        for i, seg in enumerate(parts):
            # phần lẻ (1,3,5…) là <code>…</code> -> giữ nguyên
            out.append(seg if i % 2 else _RE.sub(_wrap, seg))
        return "".join(out)
    except Exception:  # noqa: BLE001
        return text


_EMO_ONE = re.compile("[" + "".join([
    "\U0001F000-\U0001FAFF", "\u2100-\u21FF", "\u2300-\u23FF",
    "\u2460-\u24FF", "\u25A0-\u27BF", "\u2B00-\u2BFF",
]) + "]")


def first_emoji(text: str) -> str | None:
    """Emoji ĐẦU TIÊN trong chuỗi (bản gốc, chưa bọc thẻ)."""
    if not text:
        return None
    m = _RE.search(text) or _EMO_ONE.search(text)
    return m.group(0) if m else None


def first_id(text: str) -> str | None:
    """ID premium của emoji ĐẦU TIÊN trong chuỗi (dùng cho icon nút inline).

    Dùng _RE (khớp thep chuỗi dài nhất) nên cờ 2 codepoint như 🇻🇳 cũng nhận.
    """
    if not ENABLED or not text:
        return None
    for m in _RE.finditer(text):
        cid = _MAP.get(m.group(0))
        if cid and cid in _valid:
            return cid
    return None


# ═══════════════════════════════════════════════ v20: KHÔNG tin nào được trống icon
# Chủ bot yêu cầu 100% premium: tin nhắn / nút / kết quả lệnh đều phải có icon.
# Bảng dưới đây chọn icon theo nghĩa của câu để tin nào cũng có icon đúng ngữ cảnh
# (thay vì tin nào cũng nhét một cái giống nhau).
DEFAULT_ICON = "⚡"

_SEMANTIC: tuple[tuple[tuple[str, ...], str], ...] = (
    (("lỗi", "thất bại", "không được", "không tìm", "sai", "hết hạn", "bị chặn",
      "không thể", "không hỗ trợ", "từ chối", "quá tải", "bận"), "❌"),
    (("thành công", "hoàn tất", "đã gửi", "đã xong", "đã lưu", "đã tải", "đã đổi",
      "đã bật", "đã tắt", "đã xoá", "đã cập nhật", "ok", "xong"), "✅"),
    (("đang chờ", "vui lòng đợi", "đang xử lý", "đang tải", "xin đợi", "chờ chút",
      "đang tìm", "đang dịch", "đang tạo"), "⏳"),
    (("cảnh báo", "chú ý", "lưu ý", "quan trọng", "cẩn thận"), "⚠️"),
    (("dịch", "ngôn ngữ", "phiên dịch"), "🌐"),
    (("nhạc", "bài hát", "mp3", "audio", "âm thanh", "sing", "cover"), "🎵"),
    (("video", "tải xuống", "download", "tiktok", "reel", "clip"), "⬇️"),
    (("ảnh", "hình", "avatar", "sticker", "banner", "ảnh đại diện"), "📸"),
    (("thời tiết", "nhiệt độ", "mưa", "nắng"), "🌤"),
    (("id", "uid", "username", "tiểu sử", "hồ sơ"), "🆔"),
    (("admin", "quản trị", "quản lý", "panel"), "👑"),
    (("vip", "stars", "thanh toán", "mua"), "⭐"),
    (("menu", "danh sách lệnh", "tất cả lệnh", "hướng dẫn"), "📋"),
    (("facebook", "fb"), "👤"),
    (("youtube", "ytb"), "▶️"),
    (("instagram", "ig"), "💬"),
    (("giọng nói", "tts", "voice", "đọc"), "🎤"),
    (("nhập", "gửi link", "dán link", "điền"), "✏️"),
    (("khoá", "khóa", "cấm", "riêng tư", "bảo mật", "18+"), "🔒"),
    (("sao chép", "copy"), "📋"),
    (("thống kê", "số liệu", "báo cáo", "ping", "độ trễ", "trạng thái"), "📊"),
    (("link", "đường dẫn", "url"), "🔗"),
    (("war", "spam", "tag"), "🔥"),
)


def pick_icon(text: str) -> str:
    """Chọn 1 icon premium hợp nghĩa cho câu (luôn trả về emoji CÓ id premium)."""
    low = (text or "").lower()
    for words, ic in _SEMANTIC:
        if any(w in low for w in words):
            if ic in RAW:
                return ic
    if DEFAULT_ICON in RAW:
        return DEFAULT_ICON
    for cand in ("⚡", "✅", "📄", "💬"):
        if cand in RAW:
            return cand
    return next(iter(RAW)) if RAW else "⚡"


def id_of(emoji: str) -> str | None:
    """ID premium của 1 emoji (None nếu không có / id không còn dùng được)."""
    e = (emoji or "").strip()
    if not e:
        return None
    for key in (e, e + "\ufe0f", e.replace("\ufe0f", "")):
        cid = _MAP.get(key)
        if is_valid_id(cid):
            return cid
    return None


def tag(emoji: str) -> str:
    """Bọc 1 emoji thành thẻ tg-emoji (trả về chuỗi rỗng nếu không có id dùng được)."""
    e = emoji or DEFAULT_ICON
    cid = id_of(e)
    if not cid:
        return ""
    return f'<tg-emoji emoji-id="{cid}">{e}</tg-emoji>'


def has_premium(text: str) -> bool:
    return bool(text) and "<tg-emoji" in text


# v20: bỏ hết emoji THƯỜNG khỏi nhãn nút — nút chỉ hiện icon premium
# (nếu để emoji thường trong nhãn, client sẽ hiện thêm 1 icon basic nữa).
_EMO_ALL = re.compile("[" + "".join([
    "\U0001F000-\U0001FAFF", "\u2190-\u21FF", "\u2300-\u23FF",
    "\u2460-\u24FF", "\u25A0-\u27BF", "\u2B00-\u2BFF",
    "\ufe0f", "\u20e3", "\u200d",
]) + "]+")


def drop_emoji(text: str) -> str:
    """Bỏ emoji thường trong 1 chuỗi (giữ chữ). Chuỗi chỉ có emoji thì giữ nguyên."""
    src = (text or "").strip()
    if not src:
        return src
    out = re.sub(r"\s{2,}", " ", _EMO_ALL.sub(" ", src)).strip()
    return out or src


def ensure(text: str, icon: str | None = None) -> str:
    """v20: bọc emoji sẵn có -> premium; nếu câu KHÔNG có emoji nào thì chèn icon đầu câu.

    Nhờ vậy không còn tin nhắn / kết quả lệnh nào trống icon, kể cả các chỗ module
    khác tự gửi tin mà quên thêm emoji.
    """
    if not text:
        return text
    out = p(text)
    if not ENABLED or has_premium(out):
        return out
    head = tag(icon or pick_icon(text))
    if not head:
        # id ưu tiên hỏng -> lấy icon khác còn dùng được (luôn còn ít nhất 1)
        for cand in (DEFAULT_ICON, "⚡", "✅", "📄", "💬", "🔗", "🌐"):
            head = tag(cand)
            if head:
                break
    if not head:
        return out
    s = out.lstrip()
    # câu bắt đầu bằng khối mã / trích dẫn: chèn icon ra ngoài cho khỏi phá cú pháp
    return f"{head} {s}" if s else head


def strip(text: str) -> str:
    """Bỏ hết thẻ tg-emoji, giữ emoji thường + các thẻ HTML khác."""
    if not text or "<tg-emoji" not in text:
        return text
    return _TAG_RE.sub(r"\1", text)


def plain_all(text: str) -> str:
    """Bỏ toàn bộ thẻ HTML (dùng khi Telegram từ chối parse)."""
    return re.sub(r"<[^>]+>", "", strip(text))


async def probe(bot) -> str:
    """Kiểm tra id nào còn dùng được; id hỏng thì bỏ (tự về emoji thường)."""
    global ENABLED
    if not ENABLED:
        return "tắt"
    ids = sorted(set(_MAP.values()) | _CUSTOM_IDS)
    good: set[str] = set()
    try:
        for i in range(0, len(ids), 40):
            chunk = ids[i:i + 40]
            try:
                stickers = await bot.get_custom_emoji_stickers(custom_emoji_ids=chunk, read_timeout=15)
                good.update(s.custom_emoji_id for s in stickers if s and s.custom_emoji_id)
            except Exception as e:  # noqa: BLE001
                log.info("probe emoji lỗi (%s) — bỏ qua nhóm này", str(e)[:80])
        if good:
            _valid.clear()
            _valid.update(good)
            _PROBED["on"] = True
            log.info("premium emoji: %d/%d id hợp lệ", len(good), len(ids))
            return f"{len(good)}/{len(ids)} id dùng được"
        ENABLED = False
        _PROBED["on"] = True
        log.info("premium emoji: không xác thực được, dùng emoji thường")
        return "không xác thực được -> emoji thường"
    except Exception as e:  # noqa: BLE001
        ENABLED = False
        log.info("premium emoji tắt: %s", str(e)[:80])
        return f"lỗi: {str(e)[:60]}"


def reset() -> None:
    """Tắt hẳn premium (khi Telegram báo lỗi entity)."""
    global ENABLED
    ENABLED = False


# ═══════════════════════════════════════════════ v19: tự áp premium cho MỌI tin
# Mục tiêu: không còn icon thường nào lọt ra chat. Mọi lời gọi gửi tin (kể cả
# các chỗ gọi thẳng msg.reply_text / edit_text / caption trong module khác) đều
# được bọc p() ở đây, không phải sửa từng dòng.
_PATCHED = {"on": False}


def _auto_install() -> None:
    if _PATCHED["on"]:
        return
    try:
        import inspect

        from telegram import Bot, Message
    except Exception:  # noqa: BLE001
        return

    targets = []
    for cls, names in (
        (Message, ("reply_text", "edit_text", "reply_photo", "reply_video",
                   "reply_audio", "reply_document", "reply_animation",
                   "reply_voice", "edit_caption", "edit_media")),
        (Bot, ("send_message", "send_photo", "send_video", "send_audio",
               "send_document", "send_animation", "edit_message_text",
               "edit_message_caption", "edit_message_media")),
    ):
        for name in names:
            fn = getattr(cls, name, None)
            if fn is not None:
                targets.append((cls, name, fn))

    def make(fn, key):
        try:
            names = list(inspect.signature(fn).parameters)
        except Exception:  # noqa: BLE001
            return None
        if key not in names:
            return None
        idx = names.index(key) - 1  # trừ 'self'

        async def wrapper(self, *args, **kwargs):  # noqa: ANN001
            args = list(args)
            src = args[idx] if len(args) > idx else kwargs.get(key)
            if not isinstance(src, str) or not src:
                return await fn(self, *args, **kwargs)
            new = ensure(src)
            if new == src:
                return await fn(self, *args, **kwargs)
            if len(args) > idx:
                args[idx] = new
            else:
                kwargs[key] = new
            added = not kwargs.get("parse_mode")
            if added:
                kwargs["parse_mode"] = "HTML"
            try:
                return await fn(self, *args, **kwargs)
            except Exception as e:  # noqa: BLE001
                low = str(e).lower()
                if "entit" not in low and "parse" not in low:
                    raise
                # Telegram từ chối entity -> gửi bản bỏ thẻ premium
                plain = strip(src)
                if len(args) > idx:
                    args[idx] = plain
                else:
                    kwargs[key] = plain
                if added:
                    kwargs.pop("parse_mode", None)
                try:
                    return await fn(self, *args, **kwargs)
                except Exception as e2:  # noqa: BLE001
                    low2 = str(e2).lower()
                    if "entit" not in low2 and "parse" not in low2:
                        raise
                    # v20: vẫn lỗi -> gửi hẳn bản chữ thuần (không còn cách nào khác)
                    safe = plain_all(src)
                    if len(args) > idx:
                        args[idx] = safe
                    else:
                        kwargs[key] = safe
                    kwargs.pop("parse_mode", None)
                    return await fn(self, *args, **kwargs)

        wrapper._v19_auto = True
        return wrapper

    for cls, name, fn in targets:
        if getattr(fn, "_v19_auto", False):
            continue
        for key in ("text", "caption"):
            w = make(fn, key)
            if w is not None:
                try:
                    setattr(cls, name, w)
                except Exception:  # noqa: BLE001
                    pass
                break
    _PATCHED["on"] = True


_auto_install()

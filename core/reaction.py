#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bot tự thả cảm xúc (reaction) vào tin nhắn theo NỘI DUNG.

- Đọc chữ của tin nhắn → chọn 1 cảm xúc hợp nghĩa (cảm ơn → 🙏, buồn → 😢…).
- Không khớp gì thì KHÔNG thả (không spam reaction vô tội vạ).
- Sai/không được phép (Telegram chặn emoji lạ) → tự lùi về 👍.
- Admin bật/tắt bằng /react on|off, lưu bền trong data/settings.json.
"""

import logging
import random
import re
import time

from core import settings

log = logging.getLogger("khoaan.reaction")

KEY = "react.enabled"
FALLBACK = "👍"

# (chat_id, message_id) mà BOT vừa tự thả -> tính năng "reaction là xoá tin" phải bỏ qua,
# nếu không bot tự thả xong lại xoá mất tin của người dùng.
OWN: dict[tuple[int, int], float] = {}


def mark_own(chat_id: int, message_id: int) -> None:
    if len(OWN) > 800:
        cutoff = time.time() - 120
        for key in [k for k, ts in OWN.items() if ts < cutoff]:
            OWN.pop(key, None)
    OWN[(int(chat_id), int(message_id))] = time.time()


def is_own(chat_id: int, message_id: int, window: float = 120.0) -> bool:
    ts = OWN.get((int(chat_id), int(message_id)))
    return bool(ts) and time.time() - ts < window

# (mẫu chữ, các cảm xúc có thể dùng) — xét theo thứ tự, khớp trước thắng
RULES: list[tuple[str, tuple[str, ...]]] = [
    (r"cảm ơn|cám ơn|cam on|thanks|thank you|\bthank\b|\btks\b|\btysm\b",
     ("🙏", "❤", "🥰")),
    (r"chúc mừng|chuc mung|sinh nhật|sinh nhat|happy birthday|congrats",
     ("🎉", "🥰", "👏")),
    (r"đẹp trai|đẹp gái|soái ca|\bxinh\b|xinh quá|dễ thương|de thuong|cute|đáng yêu|dang yeu",
     ("🥰", "❤")),
    (r"hay quá|hay qua|\bgiỏi\b|gioi qua|tuyệt vời|tuyet voi|\bđỉnh\b|dinh cao|slay|ngon quá",
     ("👏", "🔥", "💯")),
    (r"haha|hjhj|hihi|kkk|=\)|:\)|\blol\b|\blmao\b|buồn cười|buan cuoi|\bhài\b",
     ("😁",)),
    (r"\bbuồn\b|\bbuon\b|khóc|\bkhoc\b|\bsad\b|huhu|đau lòng|dau long|chán quá|chan qua|"
     r"\bstress\b|tủi thân",
     ("😢",)),
    (r"\bsợ\b|so qua|kinh quá|\bshock\b|sốc|vãi|vai that|trời ơi|troi oi|\bomg\b|\bwtf\b|"
     r"\bvl\b|\bvcl\b|toang|xong đời",
     ("🤯", "😱")),
    (r"\byêu\b|thương em|thuong em|\bmến\b|\bcrush\b", ("❤", "🥰")),
    (r"\btiền\b|\btien\b|giàu|\bgiau\b|đắt|dat qua|lương về|\bcoin\b", ("🤑",)),
    (r"\bngủ\b|ngu ngon|buồn ngủ|mệt quá|mệt mỏi", ("😴", "😢")),
    (r"\b(ok|oke|okay|okela|được|duoc|chuẩn|chuan|đúng rồi|dung roi)\b", ("👍", "👌")),
    (r"\b(chào|chao|hello|hey|alo|halo|hi)\b", ("👋",)),
    (r"ghét|ghet|tệ quá|te qua|\bdở\b|do qua|thất vọng|that vong|không thích|ko thích|\bbực\b",
     ("👎", "😑")),
    (r"\bnóng\b|\bnong\b|cháy|\bchay\b|\bfire\b|lửa|\bmáu\b|\bgắt\b|\bchất\b",
     ("🔥",)),
    (r"\bcăng\b|\bcang\b|hồi hộp|hoi hop|\brun\b", ("😨", "🤯")),
    (r"\bhóng\b|đang chờ|cho lâu", ("👀",)),
    (r":v|:3|:d", ("😁",)),
]

_URL_RE = re.compile(r"https?://|www\.", re.I)
_COMPILED = [(re.compile(p, re.I), emojis) for p, emojis in RULES]


def enabled() -> bool:
    """Đang bật? (.env REACT_AUTO + công tắc /react của admin)."""
    import config
    if not getattr(config, "REACT_AUTO", True):
        return False
    return str(settings.get(KEY, "1")).strip().lower() not in ("0", "off", "false", "no")


def set_enabled(on: bool) -> None:
    settings.set_value(KEY, "1" if on else "0")


def pick(text: str) -> str | None:
    """Chọn cảm xúc cho câu này. None = không thả gì."""
    raw = (text or "").strip()
    if not raw:
        return None
    low = raw.lower()

    for rx, emojis in _COMPILED:
        if rx.search(low):
            return random.choice(emojis) if len(emojis) > 1 else emojis[0]

    if _URL_RE.search(raw):                       # gửi link -> đáng chú ý
        return "🔥"
    if raw.endswith("?"):                          # đang hỏi
        return "🤔"
    return None


async def apply(bot, chat_id: int, message_id: int, text: str) -> str | None:
    """Thả cảm xúc. Trả emoji đã thả (hoặc None). Không bao giờ ném lỗi ra ngoài."""
    from telegram import ReactionTypeEmoji

    emoji = pick(text)
    if not emoji:
        return None
    for candidate in (emoji, FALLBACK):
        try:
            await bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=[ReactionTypeEmoji(candidate)],
                is_big=False,
            )
            mark_own(chat_id, message_id)
            return candidate
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            if "message to react not found" in msg or "message can't be" in msg:
                return None
            log.info("thả %s không được (%s) — thử loại khác", candidate, msg[:70])
    return None

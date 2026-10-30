#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KHOA AN TOOLS v19 — Đa nền tảng: TikTok · IG · FB · YouTube · nhạc · TTS · 18+ · Stars."""

import asyncio
import html
import io
from pathlib import Path
import logging
import os
import re
import time
import types
import uuid
from urllib.parse import quote

from telegram import (
    BotCommand,
    InputFile,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Update,
)
from telegram.constants import ChatAction, ChatType, ParseMode
from telegram.error import BadRequest, Forbidden, RetryAfter, TimedOut
from telegram.ext import (
    ApplicationHandlerStop,
    MessageReactionHandler,
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PreCheckoutQueryHandler,
    TypeHandler,
    filters,
)

import config
from config import BRAND, MAX_UPLOAD_MB, OWNER_NAME, TMP_DIR

VERSION = "v21"
from core import admins, banner, canvas, captcha, capture, cover, downloader, gifpool, health, http
from core import music, reaction, settings, stats, tts, userinfo, userindex
from core import pool
from core import facebook
from core import anh18, war
from core import emoji as emoji_uid
from core import premium
from core import utils
from core import baotri, daily, qc
from core import donate
from core import social, console
from core import translate as dich
from core import buttons as B
from core import sleep as night
from core import stars
from core.text import esc, extract_url, file_size, human_duration, human_int, now_vn, uptime_str

console.setup(logging.INFO)          # giao diện console màu: banner, log, sự kiện tải
log = logging.getLogger("khoaan.bot")

START_TS = time.time()
COOLDOWN: dict[int, float] = {}
USER_VOICE: dict[int, str] = {}
CAP_STATE: dict[int, str] = {}      # user_id -> url đang chụp
PENDING: dict[int, str] = {}        # user_id -> hành động chờ (thêm admin…)
BANNER_STATE: dict[int, dict] = {}  # user_id -> {name, style, size, logo, msg_id}


# ================================================================= helpers
def throttled(user_id: int, seconds: float = 1.2) -> bool:
    now = time.time()
    last = COOLDOWN.get(user_id, 0)
    if now - last < seconds:
        return True
    COOLDOWN[user_id] = now
    return False


async def _rich_send(fn, text: str, field: str = "text", **kwargs):
    """Gửi text/caption kèm icon premium; Telegram từ chối thì tự lùi về emoji thường rồi text trơn.

    `field` cho biết chữ này nằm ở đâu: "text" (reply_text) hay "caption"
    (reply_photo / reply_video / reply_audio / reply_document).
    Nhờ vậy mọi kết quả — kể cả caption video TikTok và info kênh — đều có icon premium.
    """
    rich = premium.ensure(text)

    def call(payload: str, **kw):
        kw[field] = payload
        return fn(**kw)

    async def _once(payload: str, **kw):
        """Gửi 1 lần; Telegram từ chối icon/màu của nút thì bỏ icon/màu gửi lại."""
        try:
            return await call(payload, **kw)
        except BadRequest as e:
            if B.has_extras(kw.get("reply_markup")) and B.is_style_error(str(e)):
                B.disable()
                kw["reply_markup"] = B.strip(kw["reply_markup"])
                log.info("nút màu/icon bị từ chối -> gửi lại bằng nút thường")
                return await call(payload, **kw)
            raise

    try:
        return await _once(rich, **kwargs)
    except BadRequest as e:
        err = str(e).lower()
        if "message is not modified" in err:
            return None
        # bot chưa đủ điều kiện dùng custom emoji -> tắt premium vĩnh viễn cho nhẹ
        if "custom_emoji" in err or "custom emoji" in err:
            premium.reset()
            log.info("premium emoji bị từ chối -> dùng emoji thường")
            try:
                return await call(premium.strip(text), **kwargs)
            except Exception:  # noqa: BLE001
                return None
        if "parse entities" in err or "unsupported start tag" in err or "can't parse" in err:
            try:
                return await call(premium.strip(rich), **kwargs)
            except BadRequest:
                kw = {k: v for k, v in kwargs.items() if k != "parse_mode"}
                try:
                    return await call(premium.plain_all(rich), **kw)
                except Exception:  # noqa: BLE001
                    return None
        log.info("send fail: %s", str(e)[:90])
        return None
    except (TimedOut, RetryAfter):
        return None
    except Exception as e:  # noqa: BLE001
        log.info("send fail: %s", str(e)[:90])
        return None


async def _send(fn, text: str, **kwargs):
    """Gửi text kèm icon premium (bọc _rich_send cho field text)."""
    return await _rich_send(fn, text, "text", **kwargs)


async def say(msg, text: str, **kwargs):
    """reply_text: HTML + icon premium + tự lùi khi lỗi."""
    kwargs.setdefault("parse_mode", ParseMode.HTML)
    return await _send(msg.reply_text, text, **kwargs)


async def safe_edit(msg, text: str, **kwargs):
    if not msg:
        return None
    kwargs.setdefault("parse_mode", ParseMode.HTML)
    try:
        return await msg.edit_text(premium.ensure(text), **kwargs)
    except BadRequest as e:
        err = str(e).lower()
        if "not modified" in err:
            return msg
        if "custom_emoji" in err or "custom emoji" in err:
            premium.reset()
            try:
                return await msg.edit_text(premium.strip(text), **kwargs)
            except Exception:  # noqa: BLE001
                return None
        if "parse entities" in err or "unsupported start tag" in err or "can't parse" in err:
            try:
                return await msg.edit_text(premium.strip(text), **kwargs)
            except BadRequest:
                kw = {k: v for k, v in kwargs.items() if k != "parse_mode"}
                return await msg.edit_text(premium.plain_all(text), **kw)
        if "no text in the message" in err:
            return await say(msg, text, **kwargs)
        log.info("edit fail: %s", str(e)[:80])
        return None
    except (TimedOut, RetryAfter):
        return None
    except Exception as e:  # noqa: BLE001
        log.warning("edit fail: %s", e)
    return None


async def reply_html(update: Update, text: str, **kwargs):
    kwargs.setdefault("parse_mode", ParseMode.HTML)
    return await _send(update.effective_message.reply_text, text, **kwargs)


async def safe_answer(q, text: str | None = None, alert: bool = False) -> None:
    """Trả lời callback query. KHÔNG bao giờ ném lỗi -> handler phía sau vẫn chạy.

    Lỗi "query is too old" xảy ra khi nút được bấm quá lâu (thường do gif chặn
    trước đó). Nuốt lỗi ở đây để nút vẫn xử lý bình thường.
    """
    if not q:
        return
    try:
        await q.answer(text or "", show_alert=alert)
    except Exception as e:  # noqa: BLE001
        log.info("answer callback lỗi (bỏ qua): %s", str(e)[:80])


async def _silent_delete(msg) -> None:
    """Xoá tin nhắn trạng thái trong nền; lỗi thì bỏ qua, không làm hỏng luồng gửi."""
    if not msg:
        return
    try:
        await msg.delete()
    except Exception as e:  # noqa: BLE001
        log.info("xoá tin trạng thái lỗi (bỏ qua): %s", str(e)[:80])


async def drop_sing_keyboard(msg, idx: int, tr: dict) -> None:
    """Tải xong bài -> xoá luôn bàn phím 10 bài trên ảnh bìa (không cho bấm lại / spam)."""
    if not msg:
        return
    try:
        await msg.edit_reply_markup(reply_markup=None)
    except Exception as e:  # noqa: BLE001
        log.info("bỏ bàn phím sing lỗi: %s", str(e)[:80])
    context_hint = f"✅ Đã tải #{idx}: {tr.get('title', '')[:40]}"
    try:
        base = (msg.caption or "").rstrip()
        if base and len(base) + len(context_hint) < 900:
            await msg.edit_caption(caption=premium.ensure(f"{base}\n\n{context_hint}"),
                                   parse_mode=ParseMode.HTML)
    except Exception:  # noqa: BLE001
        pass


def speed_tag(t0: float) -> str:
    ms = int((time.time() - t0) * 1000)
    return f"⚡ xử lý trong <code>{ms} ms</code>"


def in_group(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.type in (ChatType.GROUP, ChatType.SUPERGROUP))


# ================================================================= gif mỗi câu trả lời
LAST_GIF: dict[int, float] = {}
GIF_FLOOD_UNTIL: dict[int, float] = {}

GIF_SETTING = "gif.enabled"     # lưu bền trong data/settings.json (admin bật/tắt bằng /gif)
BOT_COMMANDS: set[str] = set()  # tự nạp từ build_app(): chỉ lệnh của bot này mới có gif
CALLBACK_PREFIX = ("p_", "a_", "cw:", "sg_", "tk:", "vc:")


def gif_enabled() -> bool:
    """GIF đang bật? (config .env + công tắc /gif của admin đều phải bật)."""
    if not config.GIF_REPLY:
        return False
    return str(settings.get(GIF_SETTING, "1")).strip().lower() not in ("0", "off", "false", "no")


def set_gif_enabled(on: bool) -> None:
    settings.set_value(GIF_SETTING, "1" if on else "0")


def is_bot_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True nếu update này là lệnh / nút bấm CỦA BOT NÀY.

    Tin nhắn thường, lệnh của bot khác (/start@otherbot) hay nút của bot khác -> False,
    nên bot không nhả gif tràn lan trong nhóm.
    """
    q = update.callback_query
    if q:
        # nút phải do chính bot này gửi
        cm = getattr(q, "message", None)
        owner = getattr(cm, "from_user", None) if cm else None
        if owner is not None and getattr(owner, "id", None) != context.bot.id:
            return False
        return (q.data or "").startswith(CALLBACK_PREFIX)
    msg = update.effective_message
    txt = ((msg.text or msg.caption or "") if msg else "").strip()
    if not txt.startswith("/"):
        return False
    head = txt.split()[0][1:]
    name, _, target = head.partition("@")
    if target:
        me = (getattr(context.bot, "username", "") or "").lower()
        if target.lower() != me:
            return False                 # lệnh gọi bot khác -> không gif
    return name.lower() in BOT_COMMANDS


async def cb_pre_gif(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gửi 1 gif chiikawa / anime PHONG CẢNH TRƯỚC câu trả lời.

    Group=-1 + blocking nên gif xong trước handler chính → gif nằm TRÊN câu trả lời.
    Có giới hạn 5s và tự nghỉ khi Telegram báo flood, nên KHÔNG bao giờ treo lệnh
    hay làm nút bấm hết hạn (lỗi "query is too old" trước đây là do khâu này).
    """
    if not gif_enabled():
        return
    if update.edited_message or getattr(update, "message_reaction", None):
        return
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not msg or not chat or not user:
        return
    if chat.type not in (ChatType.PRIVATE, ChatType.GROUP, ChatType.SUPERGROUP):
        return
    if user.is_bot:
        return
    # CHỈ gif khi đúng lệnh của bot này (hoặc bấm nút của bot này).
    # Tin nhắn thường / lệnh của bot khác / bot khác trả lời -> không gif, đỡ ồn.
    if not is_bot_command(update, context):
        return

    now = time.time()
    if GIF_FLOOD_UNTIL.get(chat.id, 0) > now:      # Telegram đang bắt chờ -> im lặng hẳn
        return
    if now - LAST_GIF.get(chat.id, 0) < config.GIF_MIN_GAP:
        return
    LAST_GIF[chat.id] = now

    # bấm nút thì chờ gif ngắn hơn (2s) để callback không bị hết hạn
    budget = 2.0 if update.callback_query else 5.0
    try:
        await asyncio.wait_for(
            gifpool.send(context.bot, chat.id, reply_to=msg.message_id), timeout=budget,
        )
    except asyncio.TimeoutError:
        log.info("gif quá %.0fs -> bỏ qua để lệnh chạy ngay", budget)
    except RetryAfter as e:
        wait = max(config.GIF_FLOOD_COOLDOWN, int(getattr(e, "retry_after", 0)) + 5)
        GIF_FLOOD_UNTIL[chat.id] = time.time() + wait
        log.info("gif bị flood ở chat %s -> nghỉ %ss", chat.id, wait)
    except Exception as e:  # noqa: BLE001
        log.info("gửi gif lỗi: %s", str(e)[:100])


# ================================================================= captcha (lần đầu)
CAPTCHA_PREFIX = "cp_"


async def send_captcha(target_msg, context: ContextTypes.DEFAULT_TYPE, user,
                       reason: str = "first") -> None:
    """Gửi câu hỏi xác minh + 4 nút.

    - Vừa gửi <8s: im lặng (câu hỏi vẫn nằm ngay trên màn hình, không spam).
    - Câu cũ quá 10 phút: đổi câu mới.
    """
    now = time.time()
    item = captcha.pending(user.id)
    if item and now - float(item.get("sent") or item.get("ts") or 0) < 8:
        return
    if not item or now - float(item.get("sent") or item.get("ts") or 0) > 600:
        item = captcha.issue(user.id, force=True)
    if not item:
        return
    item["sent"] = now
    try:
        await _rich_send(
            target_msg.reply_text, captcha.text(item, reason=reason), "text",
            parse_mode=ParseMode.HTML, reply_markup=captcha.keyboard(item),
        )
    except Exception as e:  # noqa: BLE001
        log.info("gửi captcha lỗi: %s", str(e)[:80])


async def track_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ghi mọi người bot gặp vào sổ (để /id @username tra được)."""
    try:
        u = update.effective_user
        if u and not u.is_bot:
            userindex.remember(u)
        msg = update.effective_message
        for m in (getattr(msg, "new_chat_members", None) or []) if msg else []:
            userindex.remember(m)
        r = getattr(msg, "reply_to_message", None) if msg else None
        if r is not None and getattr(r, "from_user", None):
            userindex.remember(r.from_user)
    except Exception as e:  # noqa: BLE001
        log.info("track_users lỗi: %s", str(e)[:80])


def _cap_reason(uid: int) -> str:
    """Câu hỏi đang hiện là do người mới hay do dùng quá nhiều lệnh."""
    return "first" if not captcha.is_verified(uid) else "spam"


async def cb_gate_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chặn MỌI lệnh/nút của người chưa xác minh (chat riêng) cho tới khi giải đúng.

    Chạy ở group=-2 (trước cả gif) và raise ApplicationHandlerStop để handler chính
    không chạy → người lạ không dùng được bot.
    """
    if not captcha.enabled():
        return
    if update.edited_message or getattr(update, "message_reaction", None):
        return

    q = update.callback_query
    if q and (q.data or "").startswith(CAPTCHA_PREFIX):
        return                                  # nút đáp án -> để cb_captcha xử lý

    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not msg or not chat or not user or user.is_bot:
        return
    if chat.type != ChatType.PRIVATE:            # trong nhóm không chặn (đỡ ồn)
        return
    if admins.is_admin(user.id):
        return

    # đếm số lệnh đã dùng: tới mốc CAPTCHA_EVERY (mặc định 10) thì bắt xác minh lại
    if msg.text and msg.text.startswith("/"):
        captcha.bump_usage(user.id)

    first_time = not captcha.is_verified(user.id)
    too_many = (not first_time) and captcha.requires_verify(user.id)
    if not (first_time or too_many):
        return

    reason = "first" if first_time else "spam"
    if q:                                        # bấm nút khi đang bị chặn
        await safe_answer(q, "🛡 Giải phép tính ở tin nhắn phía trên trước nhé!", alert=True)
    await send_captcha(msg, context, user, reason)
    raise ApplicationHandlerStop


async def cb_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Người dùng bấm 1 trong 4 đáp án."""
    q = update.callback_query
    user = q.from_user
    data = q.data or ""
    body = data[len(CAPTCHA_PREFIX):]
    token, _, value = body.partition("_")

    result = captcha.check(user.id, token, value)

    if result == "ok":
        first = captcha.mark(user.id, user.full_name or "")
        captcha.clear(user.id)
        captcha.reset_usage(user.id)
        stats.bump("captcha")
        await safe_answer(q, "✅ Đúng rồi! Chào mừng bạn 🎉")
        text = (
            "✅ <b>XÁC MINH THÀNH CÔNG</b>\n"
            "──────────────\n"
            + dashboard(user.full_name or user.first_name or "bạn")
        )
        try:
            await safe_edit(q.message, text, parse_mode=ParseMode.HTML,
                            reply_markup=main_menu(admins.is_admin(user.id)))
        except Exception:  # noqa: BLE001
            pass
        log.info("captcha OK: %s (%s)%s", user.id, user.full_name,
                 " lần đầu" if first else "")
        return

    if result == "stale":
        item = captcha.issue(user.id, force=True)
        await safe_answer(q, "⏳ Câu này cũ rồi, mình đổi câu mới nhé!", alert=True)
        if item:
            await safe_edit(q.message, captcha.text(item, reason=_cap_reason(user.id)),
                            parse_mode=ParseMode.HTML, reply_markup=captcha.keyboard(item))
        return

    if result == "none":
        item = captcha.issue(user.id, force=True)
        await safe_answer(q, "🛡 Bấm đáp án ở tin nhắn mới nhất nhé!", alert=True)
        if item:
            await say(q.message, captcha.text(item), reply_markup=captcha.keyboard(item))
        return

    tries = captcha.bump_try(user.id)
    if tries >= captcha.MAX_TRIES:
        item = captcha.issue(user.id, force=True)
        await safe_answer(q, "❌ Sai 3 lần — đổi câu khác cho bạn!", alert=True)
        if item:
            await safe_edit(q.message, captcha.text(item, tries=0, reason=_cap_reason(user.id)),
                            parse_mode=ParseMode.HTML, reply_markup=captcha.keyboard(item))
        return
    await safe_answer(q, "❌ Chưa đúng, tính lại rồi bấm tiếp nhé!", alert=True)


async def cmd_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: /captcha · /captcha on|off · /captcha reset <uid>"""
    if not admins.is_admin(update.effective_user.id):
        await reply_html(update, "🔒 Lệnh này dành cho admin bot.")
        return
    arg = (context.args or [""])[0].strip().lower()

    if arg in ("on", "off", "bat", "bật", "tat", "tắt"):
        on = arg in ("on", "bat", "bật")
        captcha.set_enabled(on)
        await reply_html(
            update,
            f"🛡 Captcha lần đầu: <b>{'BẬT' if on else 'TẮT'}</b>\n"
            f"<i>Đã lưu — restart vẫn nhớ.</i>",
        )
        return

    if arg in ("reset", "xoa", "xoá") and len(context.args) >= 2:
        raw = context.args[1].lstrip("-")
        if not raw.isdigit():
            await reply_html(update, "Cú pháp: <code>/captcha reset 123456789</code>")
            return
        done = captcha.forget(int(raw))
        await reply_html(
            update,
            ("🗑 Đã xoá xác minh của <code>{}</code> — lần sau phải giải lại.".format(int(raw)))
            if done else "ℹ️ User này chưa xác minh nên không có gì để xoá.",
        )
        return

    await reply_html(
        update,
        "🛡 <b>CAPTCHA LẦN ĐẦU</b>\n"
        f"├ Trạng thái: <b>{'bật' if captcha.enabled() else 'tắt'}</b>\n"
        f"├ Đã xác minh: <b>{captcha.total()}</b> người\n"
        "└ Câu hỏi: phép +, −, × (4 đáp án bấm)\n"
        "\n"
        "<code>/captcha on</code> · <code>/captcha off</code>\n"
        "<code>/captcha reset &lt;uid&gt;</code> — bắt giải lại",
    )


# ================================================================= banner theo tên
BANNER_STYLES = ("Aurora", "Neon", "Sunset", "Sáng")


def banner_state(uid: int) -> dict:
    st = BANNER_STATE.get(uid)
    if not st:
        st = {"name": "", "style": 0, "size": "banner", "logo": False}
        BANNER_STATE[uid] = st
    return st


def banner_caption(st: dict) -> str:
    name = (st.get("name") or "—").upper()
    return (
        f"🎨 <b>BANNER: {esc(name)}</b>\n"
        f"──────────────\n"
        f"🖌 <b>Phong cách:</b> {BANNER_STYLES[int(st['style']) % len(BANNER_STYLES)]}\n"
        f"📐 <b>Kích thước:</b> {banner.SIZE_LABEL.get(st['size'], st['size'])}\n"
        f"🖼 <b>Logo:</b> {'ảnh đại diện của bạn' if st.get('logo') else 'chữ viết tắt tên'}\n"
        f"──────────────\n"
        f"👇 <i>Bấm bên dưới để đổi kiểu / kích thước, hoặc gõ <code>/banner Tên Mới</code>.</i>"
    )


def banner_kb(st: dict) -> InlineKeyboardMarkup:
    cur = int(st["style"]) % len(BANNER_STYLES)
    style_row = [
        B.btn(name_, cb=f"bn_st_{i}",
              style=B.GREEN if i == cur else B.BLUE,
              icon="✅" if i == cur else "🎨")
        for i, name_ in enumerate(BANNER_STYLES)
    ]
    size_row = [
        B.btn(label, cb=f"bn_sz_{key}",
              style=B.GREEN if st["size"] == key else B.BLUE,
              icon="✅" if st["size"] == key else "📐")
        for key, label in (("banner", "Ngang"), ("vuong", "Vuông"), ("story", "Story"))
    ]
    return B.kb([
        style_row[:2], style_row[2:],
        size_row,
        [B.btn("Dùng avatar của tôi", cb="bn_logo",
               style=B.GREEN if st.get("logo") else B.BLUE,
               icon="✅" if st.get("logo") else "🖼")],
        [B.btn("Menu", cb="p_home", style=B.BLUE, icon="⬅️")],
    ])


async def _banner_photo(context, st: dict, user, logo: bytes | None = None) -> io.BytesIO | None:
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            None, lambda: banner.render(st["name"], int(st["style"]), st["size"], logo)
        )
    except Exception as e:  # noqa: BLE001
        log.info("render banner lỗi: %s", str(e)[:100])
        return None


async def _user_avatar(context, uid: int) -> bytes | None:
    try:
        photos = await context.bot.get_user_profile_photos(user_id=uid, limit=1)
        if photos and photos.total_count and photos.photos[0]:
            f = await context.bot.get_file(photos.photos[0][-1].file_id)
            return bytes(await f.download_as_bytearray())
    except Exception as e:  # noqa: BLE001
        log.info("avatar cho banner lỗi: %s", str(e)[:80])
    return None


async def send_banner(update: Update, context: ContextTypes.DEFAULT_TYPE,
                      st: dict, edit_msg=None) -> None:
    """Render + gửi ảnh banner kèm bộ nút đổi kiểu.

    Lần đầu: gửi ảnh mới. Bấm nút đổi kiểu: sửa luôn ảnh cũ (đỡ spam chat);
    nếu Telegram không cho sửa thì gửi ảnh mới.
    """
    if not st.get("name"):
        await reply_html(
            update,
            "🎨 <b>TẠO BANNER THEO TÊN</b>\n"
            "Gõ: <code>/banner Khoa An</code>\n"
            "<i>Hoặc gõ tên ngay sau khi bot hỏi.</i>",
        )
        return
    user = update.effective_user
    logo = await _user_avatar(context, user.id) if st.get("logo") else None
    buf = await _banner_photo(context, st, user, logo)
    if not buf:
        await reply_html(update, "❌ Vẽ banner thất bại, thử lại nhé.")
        return
    stats.bump("banner")
    cap = banner_caption(st)
    kb = banner_kb(st)

    if edit_msg is not None:
        try:
            await edit_msg.edit_media(
                media=InputMediaPhoto(media=buf, caption=premium.ensure(cap),
                                      parse_mode=ParseMode.HTML),
                reply_markup=kb,
            )
            return
        except Exception as e:  # noqa: BLE001
            log.info("edit banner fail: %s", str(e)[:80])
            # ảnh cũ đã bị đổi/không sửa được -> gửi ảnh mới
    await _rich_send(update.effective_message.reply_photo, cap, "caption",
                     photo=buf, parse_mode=ParseMode.HTML, reply_markup=kb)


async def cmd_banner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /banner <tên> — tạo ảnh bìa theo tên. """
    st = banner_state(update.effective_user.id)
    name = " ".join(context.args or []).strip()
    if not name:
        PENDING[update.effective_user.id] = "banner"
        await reply_html(
            update,
            "🎨 <b>BANNER THEO TÊN</b>\n"
            "Gửi <b>tên</b> bạn muốn in lên banner.\n"
            "<i>Ví dụ: Khoa An · Miu Store · Nguyễn Văn A</i>\n\n"
            "Gõ <code>huy</code> để bỏ.",
        )
        return
    st["name"] = name[:banner.MAX_NAME]
    await send_banner(update, context, st)


async def cb_banner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nút đổi phong cách / kích thước / avatar / tạo lại."""
    q = update.callback_query
    uid = q.from_user.id
    st = banner_state(uid)
    action = (q.data or "")[3:]
    await safe_answer(q, "Đang vẽ lại…")

    if action.startswith("st_"):
        st["style"] = int(action[3:] or 0) % len(BANNER_STYLES)
    elif action.startswith("sz_"):
        size = action[3:]
        st["size"] = size if size in banner.SIZES else "banner"
    elif action == "logo":
        st["logo"] = not st.get("logo")
    elif action == "again":
        pass
    elif action == "name":
        PENDING[uid] = "banner"
        await say(q.message, "✏️ Gửi tên mới cho banner nhé (gõ <code>huy</code> để bỏ).")
        return

    if not st.get("name"):
        await say(q.message, "🎨 Gõ <code>/banner Tên Của Bạn</code> để bắt đầu.")
        return
    await send_banner(update, context, st, edit_msg=q.message)


# ================================================================= tự thả reaction
async def cb_auto_react(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Đọc nội dung tin nhắn → thả cảm xúc hợp nghĩa (group=1, sau handler chính)."""
    if not reaction.enabled():
        return
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not msg or not chat or not user or user.is_bot:
        return
    if chat.type not in (ChatType.PRIVATE, ChatType.GROUP, ChatType.SUPERGROUP):
        return
    text = msg.text or msg.caption or ""
    if not text:
        return
    await reaction.apply(context.bot, chat.id, msg.message_id, text)


async def cmd_react(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: /react · /react on|off"""
    if not admins.is_admin(update.effective_user.id):
        await reply_html(update, "🔒 Lệnh này dành cho admin bot.")
        return
    arg = (context.args or [""])[0].strip().lower()
    if arg in ("on", "off", "bat", "bật", "tat", "tắt"):
        on = arg in ("on", "bat", "bật")
        reaction.set_enabled(on)
        await reply_html(
            update,
            f"💬 Tự thả reaction: <b>{'BẬT' if on else 'TẮT'}</b>\n"
            f"<i>Đã lưu — restart vẫn nhớ.</i>",
        )
        return
    await reply_html(
        update,
        "💬 <b>TỰ THẢ REACTION</b>\n"
        f"├ Trạng thái: <b>{'bật' if reaction.enabled() else 'tắt'}</b>\n"
        "├ Cách chạy: đọc nội dung tin nhắn → chọn cảm xúc hợp nghĩa\n"
        "├ Ví dụ: “cảm ơn” → 🙏 · “haha” → 😁 · “buồn quá” → 😢 · gửi link → 🔥\n"
        "└ Không khớp gì thì <b>không thả</b> (không spam)\n"
        "\n"
        "<code>/react on</code> · <code>/react off</code>",
    )


# ================================================================= reaction -> tự xoá
REACT_DELETED = 0


async def on_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ai thả cảm xúc vào tin nào -> xoá tin đó (dọn chat nhanh, không cần bấm nút)."""
    global REACT_DELETED
    if not getattr(config, "REACT_DELETE", True):
        return
    r = getattr(update, "message_reaction", None)
    if not r or not r.chat:
        return
    # chỉ xử lý khi có cảm xúc MỚI (bỏ cảm xúc thì kệ)
    if not r.new_reaction:
        return
    # BOT tự thả cảm xúc (tính năng /react) -> KHÔNG được xoá tin của người ta
    actor = getattr(r, "user", None)
    if actor is not None and getattr(actor, "id", None) == context.bot.id:
        return
    if reaction.is_own(r.chat.id, r.message_id):
        return
    try:
        await context.bot.delete_message(r.chat.id, r.message_id)
        REACT_DELETED += 1
    except Exception as e:  # noqa: BLE001
        log.info("xoá theo reaction không được: %s", str(e)[:80])


# ================================================================= menu
def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    """Bàn phím chính — gọn, 2 nút/hàng, KHÔNG icon premium.

    v18: mọi nền tảng tải (TikTok / YouTube / Instagram / Facebook) gộp vào
    MỘT nút "Tải xuống" duy nhất — bấm vào dán link là bot tự nhận nền tảng.
    """
    rows = [
        B.row(B.btn("Tải xuống", cb="p_dl", style=B.GREEN, icon="⬇️"),
              B.btn("Nhạc", cb="p_sing", style=B.BLUE, icon="🎵")),
        B.row(B.btn("Giọng nói", cb="p_tts", style=B.BLUE, icon="🎙"),
              B.btn("Chụp web", cb="p_cap", style=B.BLUE, icon="📸")),
        B.row(B.btn("Thời tiết", cb="p_weather", style=B.BLUE, icon="☀️"),
              B.btn("Info kênh", cb="p_tools", style=B.BLUE, icon="🔎")),
        B.row(B.btn("Banner", cb="p_banner", style=B.BLUE, icon="🖼"),
              B.btn("ID", cb="p_id", style=B.BLUE, icon="🆔")),
        B.row(B.btn("Cover nhạc", cb="p_cover", style=B.GREEN, icon="💿"),
              B.btn("VIP · Stars", cb="p_stars", style=B.GREEN, icon="⭐")),
        B.row(B.btn("Ảnh 18+", cb="p_anh18", style=B.RED, icon="🔞"),
              B.btn("War / Spam", cb="p_war", style=B.RED, icon="⚔")),
        B.row(B.btn("Dịch", cb="p_dich", style=B.GREEN, icon="🧭"),
              B.btn("Ủng hộ", cb="p_donate", style=B.GREEN, icon="💝")),
        B.row(B.btn("Dọn tệp tạm", cb="p_clean", style=B.RED, icon="🗑")),
        B.row(B.btn("Tất cả lệnh", cb="p_help", style=B.BLUE, icon="📋")),
    ]
    if is_admin:
        rows.append(B.row(B.btn("Giờ ngủ", cb="p_sleep", style=B.BLUE, icon="🌙"),
                          B.btn("Trạng thái", cb="p_status", style=B.GREEN, icon="📊")))
        rows.append(B.row(B.btn("Quảng cáo", cb="p_qc", style=B.GREEN, icon="📢"),
                          B.btn("Bảo trì", cb="p_bt", style=B.BLUE, icon="🔧")))
        rows.append(B.row(B.btn("Bảng điều khiển", cb="a_panel", style=B.GREEN, icon="⚙️")))
    return B.kb(rows)


def back_menu() -> InlineKeyboardMarkup:
    return B.kb([B.row(B.btn("Menu", cb="p_home", style=B.BLUE, icon="⬅️"))])


def dashboard(name: str) -> str:
    return (
        f"👑 <b>{esc(BRAND).upper()}</b>\n"
        f"<i>bot tiện ích · TikTok · nhạc · ảnh</i>\n"
        f"\n"
        f"👤 <b>{esc(name)}</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🎬 <b>Tải video</b> — TikTok · IG · FB · YouTube, dán link là xong\n"
        f"🎵 <b>Nhạc</b> — <code>/sing tên bài</code> → 10 bài\n"
        f"🎧 <b>Nhạc nền TikTok</b> — <code>/mp3 link</code>\n"
        f"🧭 <b>Dịch</b> — reply tin rồi gửi <code>.</code> · <code>/dich nội dung</code>\n"
        f"🔎 <b>Info kênh</b> — <code>/getinfo user</code>\n"
        f"🎤 <b>Giọng nói</b> — <code>/tts nội dung</code>\n"
        f"📸 <b>Chụp web</b> — <code>/capwed link</code>\n"
        f"🎨 <b>Banner theo tên</b> — <code>/banner Khoa An</code>\n"
        f"🏷 <b>Đổi tên + bìa nhạc</b> — <code>/cover</code> <i>(reply bài vừa tải)</i>\n"
        f"👤 <b>Facebook</b> — <code>/fb link</code> <i>(ID + avatar)</i>\n"
        f"🆔 <b>ID + avatar + tiểu sử</b> — <code>/id</code>\n"
        f"🔒 <b>Ảnh 18+</b> — <code>/anh18</code> <i>(chat riêng, có nút chủ đề)</i>\n"
        f"🌤 <b>Thời tiết</b> — <code>/thoitiet Hà Nội</code>\n"
        f"⭐ <b>VIP</b> — <code>/stars</code> <i>(mua bằng Telegram Stars)</i>\n"
        f"📶 <b>Ping</b> — <code>/ping</code> kiểm tra độ trễ\n"
        f"━━━━━━━━━━━━━━━\n"
        f"⏱ <i>{uptime_str(START_TS)}</i>  ·  🕐 <i>{now_vn()}</i>"
    )


HELP_TEXT = (
    f"📖 <b>TẤT CẢ LỆNH</b>\n"
    f"<i>{esc(BRAND)} · chọn 1 mục bên dưới</i>\n"
    "\n"
    "▍<b>TẢI XUỐNG</b> <i>(gửi link nào cũng được)</i>\n"
    "🎬 <code>/dl link</code> — video (TikTok · IG · FB · YouTube)\n"
    "▶️ <code>/ytb link</code> — YouTube 1080p · <code>720</code> · <code>mp3</code> · <code>cả 2</code>\n"
    "📸 <code>/ig link</code> — Reel / bài đăng Instagram\n"
    "🎵 <code>/mp3 link</code> — nhạc MP3 (TikTok: cả video + nhạc)\n"
    "🔎 <code>/getinfo user</code> — thông tin kênh TikTok\n"
    "<i>Chỉ cần dán link vào chat — bot tự nhận nền tảng.</i>\n"
    "\n"
    "▍<b>NHẠC</b>\n"
    "🎧 <code>/sing tên bài</code> — 10 bài + bìa + MP3\n"
    "🏷 <code>/cover Tên | Nghệ sĩ</code> — reply bài vừa tải để đổi tên + ảnh bìa\n"
    "\n"
    "▍<b>TIỆN ÍCH</b>\n"
    "🎤 <code>/tts nội dung</code> — chữ → giọng nói (nhiều giọng AI)\n"
    "🔊 <code>/voice</code> — đổi giọng đọc + tốc độ\n"
    "👤 <code>/fb link</code> — lấy ID + ảnh đại diện Facebook\n"
    "📸 <code>/capwed link</code> — chụp ảnh website\n"
    "🎨 <code>/banner tên</code> — tạo banner theo tên (4 kiểu)\n"
    "🌤 <code>/thoitiet thành phố</code> — thời tiết 3 ngày\n"
    "🧭 <code>/dich nội dung</code> — dịch ~80 ngôn ngữ (reply + dấu .)\n"
    "🔗 <code>/short link</code> — rút gọn link\n"
    "🖼 <code>/sticker</code> — reply ảnh để tạo sticker\n"
    "👤 <code>/id</code> — ID + username + <b>tiểu sử + avatar</b>\n"
    "🎞 <code>/js</code> — reply tin có emoji premium → file UID\n"
    "\n"
    "▍<b>ẢNH 18+</b> <i>(chat riêng)</i>\n"
    "🔒 <code>/anh18</code> — bảng chủ đề có nút bấm\n"
    "🎨 <code>/mong</code> · <code>/girl</code> · <code>/bu</code>\n"
    "🎨 <code>/gainhat</code> · <code>/japcosplay</code> · <code>/tw</code>\n"
    "🎨 <code>/anime</code> · <code>/sagiri</code> · 🎬 <code>/vdgai</code>\n"
    f"🎟 <i>Mỗi người {config.ANH18_DAILY} lượt/ngày · reset 00:00 giờ VN</i>\n"
    "\n"
    "▍<b>WAR / SPAM</b> <i>(trong nhóm)</i>\n"
    "🔥 <code>/war @user 30</code> — war kèm câu chửi\n"
    "⚡ <code>/war @user 30 0.5</code> — chỉnh giãn cách (0.5s/tin)\n"
    "💬 <code>/spam @user 20 0.5</code> — spam tag\n"
    "✖️ <code>/stop</code> — dừng war/spam\n"
    "\n"
    "▍<b>VIP · STARS</b>\n"
    "⭐ <code>/stars</code> — mua VIP bằng Telegram Stars\n"
    "👑 <code>/vip</code> — xem hạn VIP còn lại\n"
    "\n"
    "▍<b>HỆ THỐNG</b>\n"
    "📶 <code>/ping</code> — kiểm tra độ trễ + tải máy\n"
    "💖 <code>/donate</code> — ủng hộ tác giả\n"
    "🗑 <code>/cleanup</code> · 👑 <code>/panel</code> <i>(admin)</i>\n"
    "📣 <code>/qc</code> <i>(admin — rải quảng cáo)</i>\n"
    "🛠 <code>/baotri</code> <i>(admin — bảo trì bot / lệnh)</i>\n"
    "🎞 <code>/gif on | off</code> <i>(admin — bật/tắt gif)</i>\n"
    "🛡 <code>/captcha on | off</code> <i>(admin — xác minh)</i>\n"
    "💬 <code>/react on | off</code> <i>(admin — tự thả cảm xúc)</i>\n"
    "🛡 <i>Dùng 10 lệnh liên tiếp sẽ phải giải 1 phép tính chống spam.</i>\n"
    "\n"
    "<i>Gif chiikawa chỉ gửi khi gõ lệnh của bot (chat thường không có gif)</i>"
)


# ================================================================= /start
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats.bump("start")
    u = update.effective_user
    text = dashboard(u.full_name or u.first_name or "bạn")
    try:
        await say(update.effective_message, 
            text, parse_mode=ParseMode.HTML, reply_markup=main_menu(admins.is_admin(u.id)),
        )
    except BadRequest:
        await say(update.effective_message, 
            re.sub(r"<[^>]+>", "", text), reply_markup=main_menu(admins.is_admin(u.id)),
        )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_html(update, HELP_TEXT, reply_markup=back_menu())


# ================================================================= panel điều hướng
async def panel_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    action = data[2:] if data.startswith("p_") else ""
    await safe_answer(q)

    if action == "home":
        await safe_edit(
            q.message, dashboard(q.from_user.full_name or "bạn"),
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(admins.is_admin(q.from_user.id)),
        )
        return
    if action == "dich":
        await dich.panel(update, context)
        return
    if action == "sing":
        await say(q.message,
            "🎵 <b>Tìm &amp; tải nhạc</b>\n"
            "<code>/sing tên bài hát</code>\n"
            "<i>Ví dụ: /sing Tắt đèn đi</i>\n\n"
            "Bot trả về ảnh bìa 10 bài — bấm số để lấy MP3.",
            reply_markup=back_menu(),
        )
        return
    if action == "dl":
        await safe_edit(
            q.message,
            "⬇ <b>TẢI XUỐNG</b>\n"
            "<i>Chỉ cần dán link vào chat — bot tự nhận nền tảng.</i>\n"
            "──────────────\n"
            "<code>/dl link</code> — video không logo (TikTok · IG · FB · YT)\n"
            "<code>/ytb link</code> — YouTube 1080p / <code>mp3</code>\n"
            "<code>/tiktok link mp3</code> — lấy nhạc nền\n"
            "<code>/ig link</code> — Reel · bài đăng · album\n"
            "<code>/fb link</code> — video/reel + ID + avatar\n"
            "──────────────\n"
            "<i>Một nút duy nhất — bấm để chèn lệnh rồi dán link.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=B.kb([
                B.row(B.btn("Tải video", copy="/dl ", style=B.GREEN, icon="🎬")),
                B.row(B.btn("Menu", cb="p_home", style=B.BLUE, icon="⬅️")),
            ]),
        )
        return
    if action == "dltt":
        await say(q.message,
            "🎬 <b>TẢI TIKTOK</b>\n"
            "<code>/dl https://www.tiktok.com/@user/video/123</code>\n"
            "<code>/dl https://vt.tiktok.com/xxxx</code>\n"
            "<code>/mp3 link</code> — nhạc nền\n\n"
            "<i>Video không logo. Tệp gửi kèm nút <b>Tải audio (MP3)</b> — bấm 1 lần là xong.</i>",
            reply_markup=back_menu(),
        )
        return
    if action == "ytb":
        await say(q.message,
            "▶️ <b>TẢI YOUTUBE</b>\n"
            "<code>/ytb https://youtu.be/xxxx</code> — video 1080p\n"
            "<code>/ytb link 720</code> — bản nhẹ, tải nhanh hơn\n"
            "<code>/ytb link mp3</code> — chỉ lấy nhạc\n"
            "<code>/ytb link cả 2</code> — video + mp3\n\n"
            "<i>Tải song song nhiều luồng + cache: bấm lại 1 bài là trả ngay.</i>",
            reply_markup=back_menu(),
        )
        return
    if action == "ig":
        await say(q.message,
            "📸 <b>TẢI INSTAGRAM</b>\n"
            "<code>/ig https://www.instagram.com/reel/xxxx</code>\n"
            "<code>/ig https://www.instagram.com/p/xxxx</code>\n"
            "<code>/ig link mp3</code> — lấy nhạc\n\n"
            "<i>Nhận Reel, video, album nhiều ảnh. Bài riêng tư: /cookie</i>",
            reply_markup=back_menu(),
        )
        return
    if action == "tts":
        await say(q.message,
            "🎤 <b>Chữ → giọng nói</b>\n"
            "<code>/tts nội dung cần đọc</code>\n"
            "<code>/tts nu nội dung</code> — đọc bằng giọng nữ\n"
            "<i>Đổi giọng + tốc độ: /voice</i>",
            reply_markup=back_menu(),
        )
        return
    if action == "cap":
        await say(q.message,
            "📸 <b>Chụp ảnh website</b>\n"
            "<code>/capwed https://example.com</code>\n"
            "<i>Gõ thiếu https cũng được.</i>",
            reply_markup=back_menu(),
        )
        return
    if action == "weather":
        await say(q.message,
            "🌤 <b>Thời tiết</b>\n"
            "<code>/thoitiet Hà Nội</code>\n"
            "<i>Không gõ thành phố thì mặc định Hà Nội.</i>",
            reply_markup=back_menu(),
        )
        return
    if action in ("tools", "help"):
        await safe_edit(q.message, HELP_TEXT, reply_markup=back_menu())
        return
    if action == "banner":
        PENDING[q.from_user.id] = "banner"
        await say(q.message,
            "🎨 <b>BANNER THEO TÊN</b>\n"
            "Gửi <b>tên</b> bạn muốn in lên banner.\n"
            "<i>Ví dụ: Khoa An · Miu Store · Nguyễn Văn A</i>\n\n"
            "Hoặc gõ nhanh: <code>/banner Khoa An</code>\n"
            "Gõ <code>huy</code> để bỏ.",
            reply_markup=back_menu(),
        )
        return
    if action == "id":
        await say(q.message,
            "🆔 <b>THÔNG TIN ID</b>\n"
            "<code>/id</code> — của chính bạn\n"
            "<code>/id @username</code> — người có username\n"
            "<code>/id 123456789</code> — theo ID số\n"
            "Reply 1 tin nhắn rồi <code>/id</code> — chắc ăn nhất\n\n"
            "<i>Kết quả kèm ảnh đại diện + tiểu sử.</i>",
            reply_markup=back_menu(),
        )
        return
    if action == "status":
        if not admins.is_admin(q.from_user.id):
            await safe_answer(q, "🔒 Mục này không dành cho bạn.", alert=True)
            return
        await say(q.message, "📊 <i>Đang render bảng thông số…</i>", parse_mode=ParseMode.HTML)
        await send_status(q.message, context)
        return
    if action == "clean":
        n, size = utils.clean_temp()
        await say(q.message, 
            f"🗑 Đã dọn <b>{n}</b> tệp tạm ({size:.1f} MB).\n"
            f"<i>Thư mục tạm còn {utils.dir_size():.1f} MB</i>",
            reply_markup=back_menu(),
        )
        return
    if action == "anh18":
        if not anh18_allowed(update):
            await safe_answer(q, "Lệnh 18+ chỉ dùng trong chat riêng.", alert=True)
            return
        await safe_edit(q.message, anh18_panel_text(q.from_user.id),
                        reply_markup=anh18_kb(ANH18_LAST.get(q.message.chat_id, "")))
        return
    if action == "qc":
        if not qc.allowed(q.from_user.id):
            await safe_answer(q, "Chỉ admin bot dùng được.", alert=True)
            return
        await safe_edit(q.message, qc.panel_text(q.from_user.id),
                        reply_markup=qc.panel_kb(q.from_user.id))
        return
    if action == "bt":
        if not admins.is_admin(q.from_user.id):
            await safe_answer(q, "Chỉ admin bot dùng được.", alert=True)
            return
        await safe_edit(q.message, baotri.status_text(), reply_markup=back_menu())
        return
    if action == "fb":
        await safe_edit(q.message,
            "👤 <b>FACEBOOK</b> — tải video + lấy ID\n"
            "<code>/fb https://www.facebook.com/share/xxxx</code> — tải video/reel\n"
            "<code>/fb https://www.facebook.com/ten</code>\n"
            "<code>/fb profile.php?id=100012345678</code>\n"
            "<code>/fb 100012345678</code>\n\n"
            "<i>Link có sẵn số ID là chắc ăn nhất.</i>",
            parse_mode=ParseMode.HTML, reply_markup=back_menu())
        return
    if action == "fbtok":
        cur = facebook.token()
        await safe_edit(
            q.message,
            "🔑 <b>FB TOKEN</b>\n"
            f"├ Hiện tại: <code>{'đã có' if cur else 'chưa có'}</code>\n"
            "├ Cách lấy: mở <b>developers.facebook.com/tools/explorer</b> → "
            "chọn app → <i>Get User Access Token</i> → copy chuỗi <code>EAAB…</code>\n"
            "├ Đặt: <code>/fb token EAAB…</code> <i>(bot tự xoá tin chứa token)</i>\n"
            "└ Xoá: <code>/fb token clear</code>\n\n"
            "<i>Không cần token vẫn lấy được ID; token chỉ để lấy TÊN + ẢNH THẬT 100%.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=B.kb([[
                B.btn("Sao chép lệnh", copy="/fb token ", style=B.GREEN, icon="📋"),
                B.btn("Menu", cb="p_home", style=B.BLUE, icon="⬅️"),
            ]]),
        )
        return
    if action == "fbcookie":
        from core import cookies as ck
        have = ck.count("fb")
        await safe_edit(
            q.message,
            "🍪 <b>LẤY ẢNH ĐẠI DIỆN THẬT (1 lần là xong)</b>\n"
            "Facebook chỉ trả ảnh xám cho khách chưa đăng nhập — bot cần cookie của "
            "<b>chính bạn</b> (hoặc admin bot nạp 1 lần, cả bot dùng chung).\n"
            "──────────────\n"
            f"Trạng thái: <b>{'đã có ' + str(have) + ' cookie' if have else 'chưa nạp'}</b>\n\n"
            "<b>3 bước:</b>\n"
            "1. Trên máy tính, Chrome → cài extension "
            "<b>Get cookies.txt LOCALLY</b>\n"
            "2. Mở <b>facebook.com</b> (đang đăng nhập) → bấm extension → <i>Export</i> "
            "→ được <code>cookies.txt</code>\n"
            "3. Gửi file đó vào đây kèm chú thích <code>/cookie fb</code>\n"
            "──────────────\n"
            "<i>Sau đó /fb gõ tên hay @username nào cũng ra ID + ảnh thật 100%.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=B.kb([[
                B.btn("Sao chép lệnh", copy="/cookie fb ", style=B.GREEN, icon="📋"),
                B.btn("Menu", cb="p_home", style=B.BLUE, icon="⬅️"),
            ]]),
        )
        return
    if action == "cover":
        await safe_edit(q.message, _cover_help(), parse_mode=ParseMode.HTML,
                        reply_markup=back_menu())
        return
    if action == "war":
        await say(q.message,
            "🔥 <b>WAR / SPAM TAG</b> <i>(chỉ trong nhóm, admin nhóm hoặc admin bot)</i>\n"
            "<code>/war @user 30</code> — war 30 lượt (mỗi lượt 1 câu chửi)\n"
            "<code>/war 30</code> — reply tin người cần war\n"
            "<code>/spam @user 20</code> — spam tag 20 lượt\n"
            "<code>/stop</code> — dừng ngay\n"
            "<code>/war @user 30 0.5</code> — 0.5s/tin (nhanh)\n"
            f"<i>Tối đa {config.WAR_MAX} lượt/lần · giãn cách mặc định {config.WAR_INTERVAL:g}s, "
            f"sàn {config.WAR_MIN_INTERVAL:g}s.</i>",
            reply_markup=back_menu(),
        )
        return
    if action == "donate":
        await donate.send_qr(context.bot, q.message.chat_id, q.from_user)
        return
    if action == "stars":
        await safe_edit(q.message, stars.panel_text(q.from_user.id),
                        parse_mode=ParseMode.HTML, reply_markup=stars.panel_kb())
        return
    if action == "sleep":
        chat = update.effective_chat
        if not await _is_chat_admin(update, context):
            await safe_answer(q, "🔒 Mục này không dành cho bạn.", alert=True)
            return
        if chat.type == ChatType.PRIVATE:
            await say(q.message,
                "🌙 <b>GIỜ NGỦ CỦA NHÓM</b>\n"
                "Lệnh này chạy <b>trong nhóm</b> (cần quyền quản trị):\n"
                "<code>/sleep 23:00 06:00</code> — ngủ 23h → 6h sáng\n"
                "<code>/sleep off</code> — tắt\n"
                "<code>/sleep lock on|off</code> — khoá chat thật hay chỉ thông báo\n"
                "<code>/sleep msg nội dung</code> — lời nhắn riêng\n\n"
                "<i>Trong khung giờ đó bot khoá chat, hết giờ tự mở lại đúng quyền cũ.</i>",
                reply_markup=back_menu(),
            )
            return
        if not await _is_chat_admin(update, context):
            await safe_answer(q, "Chỉ quản trị viên nhóm dùng được.", alert=True)
            return
        await safe_edit(q.message, sleep_text(chat.id, getattr(chat, "title", "") or ""),
                        parse_mode=ParseMode.HTML, reply_markup=night.panel_kb(chat.id))
        return
# ================================================================= ẢNH 18+ (/anh18)
ANH18_LAST: dict[int, str] = {}
ANH18_ALIAS: dict[str, str] = {k: k for k in anh18.ORDER}
ANH18_ALIAS.update({
    "ass": "mong", "butt": "mong", "gai": "girl", "sex": "girl",
    "jap": "gainhat", "nhat": "gainhat", "cosplay": "japcosplay",
    "china": "tw", "tau": "tw", "khua": "tw", "vdgai": "gainhat",
})


def anh18_kb(active: str = "") -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for k in anh18.ORDER:
        c = anh18.CATS[k]
        row.append(B.btn(c["btn"], cb=f"ah:c:{k}",
                         style=B.GREEN if k == active else B.BLUE,
                         icon="✅" if k == active else c["emoji"]))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(B.row(
        B.btn("Video", cb="ah:v", style=B.RED, icon="🎬"),
        B.btn("Thêm ảnh", cb="ah:m", style=B.GREEN, icon="🔄"),
    ))
    rows.append(B.row(
        B.btn("Kho ảnh", cb="ah:s", style=B.BLUE, icon="📊"),
        B.btn("Đóng", cb="ah:x", style=B.RED, icon="❌"),
    ))
    return InlineKeyboardMarkup(rows)


def anh18_limit(uid: int) -> int:
    """Hạn mức /anh18 mỗi ngày: cơ bản + thưởng VIP."""
    return config.ANH18_DAILY + stars.bonus(uid)


def anh18_left(uid: int) -> int:
    """Số lượt còn lại hôm nay. -1 = không giới hạn (admin bot)."""
    if admins.is_admin(uid):
        return -1
    return daily.remaining(f"anh18:{uid}", anh18_limit(uid))


def anh18_take(uid: int) -> tuple[bool, int]:
    """Trừ 1 lượt của hôm nay. Trả (được_phép, còn_lại)."""
    if admins.is_admin(uid):
        return True, -1
    return daily.take(f"anh18:{uid}", anh18_limit(uid))


def anh18_give_back(uid: int) -> None:
    """Lấy ảnh/video thất bại -> trả lại lượt."""
    if uid and not admins.is_admin(uid):
        daily.give_back(f"anh18:{uid}")


def anh18_quota_text(uid: int) -> str:
    left = anh18_left(uid)
    if left < 0:
        return "🎟 Lượt hôm nay: <b>không giới hạn</b> <i>(admin)</i>"
    tag = " 👑 VIP" if stars.is_vip(uid) else ""
    return f"🎟 Lượt hôm nay còn <b>{left}/{anh18_limit(uid)}</b>{tag} · reset 00:00"


def anh18_out_text(uid: int) -> str:
    return (
        "🎟 <b>HẾT LƯỢT HÔM NAY</b>\n"
        f"Hạn mức của bạn: <b>{anh18_limit(uid)}</b> lượt/ngày.\n"
        "Lượt tự reset lúc <b>00:00 giờ VN</b>.\n"
        "<i>Quay lại ngày mai, hoặc mua VIP bằng <code>/stars</code> để có 20 lượt/ngày.</i>"
    )


def anh18_panel_text(uid: int = 0) -> str:
    total = sum(anh18.count(k) for k in anh18.ORDER)
    return (
        "🔒 <b>ẢNH 18+ THEO CHỦ ĐỀ</b>\n"
        "<i>Bấm nút để lấy ảnh — mỗi chủ đề một kho riêng, tự lọc trùng.</i>\n"
        "━━━━━━━━━━━━━━━\n"
        "📸 Mông · Girl · Bú\n"
        "📸 Gái Nhật · Cosplay · Tài Khựa\n"
        "📸 Anime · Sagiri\n"
        "🎬 Video — clip gái thật (ngắn)\n"
        "━━━━━━━━━━━━━━━\n"
        f"📊 Kho hiện có <b>{total}</b> ảnh\n"
        f"{anh18_quota_text(uid)}\n"
        "⏳ Lần đầu mỗi chủ đề mất ~10–20s để tìm đủ ảnh.\n"
        "🔁 Bấm <b>Thêm ảnh</b> để lấy tiếp 10 ảnh khác.\n"
        "<i>Ảnh tự tìm từ nguồn công khai — bot không vẽ, không tạo ảnh.</i>"
    )


def anh18_allowed(update: Update) -> bool:
    chat = update.effective_chat
    if chat is None:
        return False
    if chat.type == ChatType.PRIVATE:
        return True
    uid = update.effective_user.id if update.effective_user else 0
    return bool(config.ANH18_IN_GROUP or admins.is_admin(uid) or stars.is_vip(uid))


def _key_of(raw: str) -> str:
    k = (raw or "").strip().lower()
    return ANH18_ALIAS.get(k, k if k in anh18.CATS else "")


async def send_anh18(context: ContextTypes.DEFAULT_TYPE, chat_id: int, key: str,
                     panel=None, note: str = "", uid: int = 0) -> int:
    """Gửi 1 album ảnh của chủ đề `key` + bàn phím chủ đề."""
    k = _key_of(key) or "girl"
    label = anh18.cat_label(k)
    if panel is not None:
        try:
            await safe_edit(panel, f"🔍 Đang tìm ảnh <b>{esc(label)}</b>…")
        except Exception:  # noqa: BLE001
            pass
    paths = await anh18.pick(k, max(1, min(config.ANH18_BATCH, 10)))

    blobs: list[bytes] = []
    for p in paths:
        try:
            blobs.append(Path(p).read_bytes())
        except Exception:  # noqa: BLE001
            continue

    cap = premium.ensure(
        f"🔞 <b>{esc(label.upper())}</b> 🔥 · <i>{len(blobs)} ảnh</i>{note}\n"
        f"💎 <i>Bấm chủ đề khác, hoặc Thêm ảnh để lấy tiếp.</i>"
    )
    media: list[InputMediaPhoto] = []
    for i, raw in enumerate(blobs):
        if i == 0:
            media.append(InputMediaPhoto(io.BytesIO(raw), caption=cap,
                                         parse_mode=ParseMode.HTML))
        else:
            media.append(InputMediaPhoto(io.BytesIO(raw)))

    sent = 0
    if media:
        try:
            # Telegram không cho gắn bàn phím vào album -> gửi album trước,
            # bàn phím nằm ở tin bảng điều khiển (hoặc tin riêng nếu không có).
            await context.bot.send_media_group(
                chat_id=chat_id, media=media,
                read_timeout=90, write_timeout=120,
            )
            sent = len(media)
        except Exception as e:  # noqa: BLE001
            log.info("album lỗi (%s) — gửi lẻ", str(e)[:80])
            for raw in blobs:
                try:
                    await context.bot.send_photo(chat_id, photo=io.BytesIO(raw), read_timeout=60)
                    sent += 1
                except Exception:  # noqa: BLE001
                    continue

    if sent:
        stats.bump("anh18")
        ANH18_LAST[chat_id] = k
        if panel is not None:
            try:
                await safe_edit(panel, anh18_panel_text(uid), reply_markup=anh18_kb(k))
            except Exception:  # noqa: BLE001
                pass
        else:
            try:
                await context.bot.send_message(
                    chat_id, premium.ensure(anh18_panel_text(uid)), parse_mode=ParseMode.HTML,
                    reply_markup=anh18_kb(k), disable_web_page_preview=True,
                )
            except Exception:  # noqa: BLE001
                pass
    elif panel is not None:
        try:
            await safe_edit(panel, "❌ Chưa lấy được ảnh (nguồn đang bận). Thử lại sau ít phút.",
                            reply_markup=anh18_kb(k))
        except Exception:  # noqa: BLE001
            pass
    return sent


async def send_vdgai(context: ContextTypes.DEFAULT_TYPE, chat_id: int, key: str,
                     panel=None, uid: int = 0) -> int:
    """Gửi 1–2 clip ngắn của chủ đề (mp4 tải từ nguồn công khai)."""
    k = _key_of(key) or "gainhat"
    label = anh18.cat_label(k)
    if panel is not None:
        try:
            await safe_edit(panel, "🎬 Đang tải video… (có thể mất ~15s)")
        except Exception:  # noqa: BLE001
            pass
    clips = await anh18.take_videos(k, 2, max_mb=24)
    sent = 0
    for i, raw in enumerate(clips):
        try:
            await context.bot.send_video(
                chat_id,
                video=InputFile(io.BytesIO(raw), filename=f"{k}{i + 1}.mp4"),
                caption=premium.ensure(f"🎬 <b>{esc(label.upper())}</b> · video {i + 1}/{len(clips)}"),
                parse_mode=ParseMode.HTML,
                supports_streaming=True,
                read_timeout=120, write_timeout=180,
            )
            sent += 1
        except Exception as e:  # noqa: BLE001
            log.info("gửi video lỗi: %s", str(e)[:80])
    if sent:
        stats.bump("anh18")
        ANH18_LAST[chat_id] = k
    if panel is not None:
        hint = "" if sent else "❌ Chưa lấy được video (nguồn đang bận). Thử lại sau."
        try:
            await safe_edit(panel, (hint + "\n\n" if hint else "") + anh18_panel_text(uid),
                            reply_markup=anh18_kb(k))
        except Exception:  # noqa: BLE001
            pass
    return sent


async def cmd_anh18(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not anh18_allowed(update):
        await reply_html(update, "🔒 Lệnh 18+ chỉ dùng trong chat riêng.")
        return
    chat_id = update.effective_chat.id
    uid = update.effective_user.id if update.effective_user else chat_id
    raw = (update.effective_message.text or "").split()
    cmd = raw[0][1:].split("@")[0].lower() if raw else "anh18"
    args = [a.lower() for a in (context.args or [])]

    key = ""
    if args:
        key = _key_of(args[0])
    if not key:
        key = _key_of(cmd)

    # Không có chủ đề -> chỉ mở bảng, KHÔNG tính lượt
    if not key:
        await reply_html(update, anh18_panel_text(uid),
                         reply_markup=anh18_kb(ANH18_LAST.get(chat_id, "")))
        return

    ok, _left = anh18_take(uid)
    if not ok:
        await reply_html(update, anh18_out_text(uid))
        return

    if args and args[0] in ("video", "vdgai"):
        panel = await reply_html(update, "🎬 Đang chuẩn bị…")
        sent = await send_vdgai(context, chat_id, key, panel, uid)
    else:
        panel = await reply_html(update, "🔍 Đang chuẩn bị kho ảnh…")
        sent = await send_anh18(context, chat_id, key, panel, uid=uid)
    if not sent:
        anh18_give_back(uid)
        try:
            await update.effective_message.reply_text("↩️ Lượt này không bị trừ (nguồn đang bận).")
        except Exception:  # noqa: BLE001
            pass


async def cmd_vdgai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not anh18_allowed(update):
        await reply_html(update, "🔒 Lệnh 18+ chỉ dùng trong chat riêng.")
        return
    chat_id = update.effective_chat.id
    uid = update.effective_user.id if update.effective_user else chat_id
    key = _key_of((context.args or [ANH18_LAST.get(chat_id, "gainhat")])[0]) or "gainhat"

    ok, _left = anh18_take(uid)
    if not ok:
        await reply_html(update, anh18_out_text(uid))
        return
    panel = await reply_html(update, "🎬 Đang chuẩn bị…")
    sent = await send_vdgai(context, chat_id, key, panel, uid)
    if not sent:
        anh18_give_back(uid)


async def cb_anh18(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)
    if not anh18_allowed(update):
        await safe_answer(q, "Lệnh 18+ chỉ dùng trong chat riêng.", alert=True)
        return
    chat_id = update.effective_chat.id
    uid = q.from_user.id
    data = q.data or ""
    panel = q.message

    if data == "ah:x":
        try:
            await panel.delete()
        except Exception:  # noqa: BLE001
            await safe_edit(panel, "Đã đóng.")
        return
    if data == "ah:s":
        rows = "\n".join(
            f"{anh18.CATS[s['key']]['btn']} — <b>{s['count']}</b>/{s['target']} ảnh"
            for s in anh18.pool_status()
        )
        await safe_edit(panel,
                        "📊 <b>KHO ẢNH</b>\n" + rows + "\n" + anh18_quota_text(uid) +
                        "\n\n<i>Kho tự đầy dần khi bạn bấm từng chủ đề.</i>",
                        reply_markup=anh18_kb(ANH18_LAST.get(chat_id, "")))
        return

    if data == "ah:v" or data == "ah:m" or data.startswith("ah:c:"):
        ok, _left = anh18_take(uid)
        if not ok:
            await safe_edit(panel, anh18_out_text(uid),
                            reply_markup=anh18_kb(ANH18_LAST.get(chat_id, "")))
            return
        if data == "ah:v":
            sent = await send_vdgai(context, chat_id,
                                    ANH18_LAST.get(chat_id, "gainhat"), panel, uid)
        elif data == "ah:m":
            sent = await send_anh18(context, chat_id,
                                    ANH18_LAST.get(chat_id, "girl"), panel, uid=uid)
        else:
            sent = await send_anh18(context, chat_id, data[5:], panel, uid=uid)
        if not sent:
            anh18_give_back(uid)
        return


# ================================================================= WAR / SPAM TAG
async def _war_allowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id if update.effective_user else 0
    if admins.is_admin(uid):
        return True
    chat = update.effective_chat
    if chat is None or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return False
    try:
        m = await context.bot.get_chat_member(chat.id, uid)
        return str(getattr(m, "status", "")).lower().endswith(("administrator", "creator"))
    except Exception:  # noqa: BLE001
        return False


def _target_and_count(update: Update, context: ContextTypes.DEFAULT_TYPE,
                      default: int) -> tuple[str, int, float]:
    """Đọc mục tiêu · số lượt · giãn cách.

    Dùng được mọi kiểu:
        /war @user 30          → 30 lượt, 1s
        /war @user 30 0.5      → 30 lượt, 0.5s/lượt
        /war 50 800ms          → 50 lượt, 0.8s
        /spam @user 20 2s
    """
    args = list(context.args or [])
    msg = update.effective_message
    mention = ""
    if msg is not None and msg.reply_to_message is not None and msg.reply_to_message.from_user:
        mention = war.mention_of(msg.reply_to_message.from_user)
        if args and args[0].startswith("@"):
            args.pop(0)
    elif args and args[0].startswith("@"):
        mention = args.pop(0)

    count = default
    interval = float(config.WAR_INTERVAL)
    got_count = False
    for a in args:
        low = a.lower()
        looks_time = low.endswith(("s", "ms", "giây")) or ("." in low or "," in low)
        if looks_time:
            val = war.parse_interval(low)
            if val is not None:
                interval = val
                continue
        if low.isdigit() and not got_count:
            count = int(low)
            got_count = True
    return mention, count, interval


async def cmd_war(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await reply_html(update, "⚠️ /war chỉ dùng trong nhóm.\n"
                                 "<i>Reply tin 1 người rồi gõ <code>/war 30</code>.</i>")
        return
    if not await _war_allowed(update, context):
        await reply_html(update, "🚫 Chỉ <b>admin nhóm</b> hoặc admin bot mới bật được war.")
        return
    mention, count, interval = _target_and_count(update, context, war.DEFAULT_WAR)
    if not mention:
        await reply_html(update,
                         "⚠️ Chưa biết war ai.\n"
                         "<i>Reply tin của người đó rồi gõ <code>/war 30</code>, "
                         "hoặc <code>/war @username 30</code>.</i>\n"
                         "Chỉnh tốc độ: <code>/war @user 30 0.5</code> (0.5s/tin)\n"
                         f"Dừng bằng <code>/stop</code> · tối đa {config.WAR_MAX} lượt.")
        return
    res = war.start(context.bot, chat.id, mention, count, "war",
                    interval, update.effective_user.id)
    if not res["ok"]:
        await reply_html(update, f"⚠️ {esc(res['msg'])}")
        return
    await reply_html(update,
                     f"🔥 <b>WAR BẮT ĐẦU</b>\n"
                     f"🎯 Mục tiêu: {mention}\n"
                     f"🔁 Số lượt: <b>{res['total']}</b> · giãn cách {res['interval']:.1f}s\n"
                     f"🛑 Dừng: <code>/stop</code>",
                     disable_web_page_preview=True)


async def cmd_spam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await reply_html(update, "⚠️ /spam chỉ dùng trong nhóm.")
        return
    if not await _war_allowed(update, context):
        await reply_html(update, "🚫 Chỉ <b>admin nhóm</b> hoặc admin bot mới bật được spam.")
        return
    mention, count, interval = _target_and_count(update, context, war.DEFAULT_SPAM)
    if not mention:
        await reply_html(update, "⚠️ <code>/spam @username 20</code> hoặc reply tin rồi gõ "
                                 "<code>/spam 20</code>.")
        return
    res = war.start(context.bot, chat.id, mention, count, "spam",
                    interval, update.effective_user.id)
    if not res["ok"]:
        await reply_html(update, f"⚠️ {esc(res['msg'])}")
        return
    await reply_html(update,
                     f"📢 <b>SPAM TAG</b>\n🎯 {mention}\n"
                     f"🔁 <b>{res['total']}</b> lượt · giãn cách {res['interval']:.1f}s\n"
                     f"🛑 Dừng: <code>/stop</code>",
                     disable_web_page_preview=True)


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    job = war.info(chat.id)
    if not job:
        await reply_html(update, "ℹ️ Không có war/spam nào đang chạy.")
        return
    n = await war.stop(chat.id)
    await reply_html(update,
                     f"🛑 <b>ĐÃ DỪNG</b> · đã tag <b>{max(n, 0)}</b>/{job.get('total', 0)} lượt.")


# ================================================================= BẢO TRÌ
BAOTRI_USAGE = (
    "🛠 <b>BẢO TRÌ</b>\n"
    "<code>/baotri on</code> · <code>/baotri off</code> — toàn bot\n"
    "<code>/baotri on 30m</code> — bật có hẹn giờ tự mở (m/h/d)\n"
    "<code>/baotri sing off</code> — khoá riêng 1 lệnh\n"
    "<code>/baotri sing on 2h</code> — khoá 1 lệnh có hẹn giờ\n"
    "<code>/baotri list</code> — xem trạng thái\n"
    "<code>/baotri msg nội dung</code> — lời nhắn hiện khi bảo trì\n"
    "<code>/baotri msg -</code> — xoá lời nhắn"
)


async def cmd_baotri(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/baotri — bảo trì toàn bot hoặc từng lệnh (chỉ admin bot)."""
    if not admins.is_admin(update.effective_user.id):
        await reply_html(update, "🔒 Chỉ admin bot.")
        return
    args = list(context.args or [])
    if not args:
        await reply_html(update, baotri.status_text())
        return

    a0 = args[0].lower()

    if a0 in ("on", "off", "bat", "tat"):
        on = a0 in ("on", "bat")
        secs = baotri.parse_duration(args[1]) if len(args) > 1 else 0
        baotri.set_global(on, secs)
        if on:
            left = f" trong <b>{baotri.human_left(secs)}</b>" if secs else " (không hẹn giờ)"
            await reply_html(update, f"🚫 Đã BẬT bảo trì toàn bot{left}.")
        else:
            await reply_html(update, "✅ Đã TẮT bảo trì toàn bot.")
        return

    if a0 in ("list", "status", "xem"):
        await reply_html(update, baotri.status_text())
        return

    if a0 in ("msg", "note", "loinh"):
        text = " ".join(args[1:]).strip()
        if text.lower() in ("-", "xoa", "xoá", "clear", "off"):
            text = ""
        baotri.set_note(text)
        await reply_html(update,
                         "✅ Đã cập nhật lời nhắn bảo trì." if text
                         else "✅ Đã xoá lời nhắn bảo trì.")
        return

    if a0 in ("help", "?", "huongdan"):
        await reply_html(update, BAOTRI_USAGE)
        return

    # /baotri <lệnh> on|off [thời gian]
    cmd = baotri.canon(a0)
    if len(args) < 2 or args[1].lower() not in ("on", "off", "bat", "tat"):
        await reply_html(update, f"❓ Thiếu trạng thái cho <code>/{cmd}</code>.\n\n{BAOTRI_USAGE}")
        return
    on = args[1].lower() in ("on", "bat")
    secs = baotri.parse_duration(args[2]) if len(args) > 2 else 0
    baotri.set_cmd(cmd, on, secs)
    if on:
        left = f" trong <b>{baotri.human_left(secs)}</b>" if secs else " (không hẹn giờ)"
        await reply_html(update, f"🚫 Đã khoá <code>/{cmd}</code>{left}.")
    else:
        await reply_html(update, f"✅ Đã mở lại <code>/{cmd}</code>.")


async def cb_gate_baotri(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chặn lệnh khi bot / lệnh đang bảo trì. Admin bot luôn đi qua."""
    user = update.effective_user
    if user is None or admins.is_admin(user.id):
        return
    msg = update.effective_message
    if msg is None:
        return

    cmd = ""
    if msg.text and msg.text.startswith("/"):
        cmd = msg.text.split()[0][1:].split("@")[0].lower()

    if baotri.global_on():
        if cmd == "baotri":
            return
        chat = update.effective_chat
        private_text = bool(msg.text) and chat is not None and chat.type == ChatType.PRIVATE
        if cmd or private_text:
            try:
                await msg.reply_text(baotri.msg_text(), parse_mode=ParseMode.HTML)
            except Exception:  # noqa: BLE001
                pass
            raise ApplicationHandlerStop
        return

    if cmd and baotri.cmd_on(cmd):
        try:
            await msg.reply_text(baotri.msg_text(cmd), parse_mode=ParseMode.HTML)
        except Exception:  # noqa: BLE001
            pass
        raise ApplicationHandlerStop


# ================================================================= status
async def ping_telegram() -> int:
    t = time.perf_counter()
    status, _, _ = await http.fetch("https://api.telegram.org", timeout=5, retries=0)
    return int((time.perf_counter() - t) * 1000) if status else 999


async def render_status() -> io.BytesIO:
    ping = await ping_telegram()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        canvas.status_canvas,
        START_TS,
        ping,
        {"dl": "TikTok", "music": "Sẵn sàng", "tts": "Sẵn sàng", "cap": "Sẵn sàng"},
    )


async def send_status(target, context: ContextTypes.DEFAULT_TYPE):
    try:
        buf = await render_status()
        await _rich_send(target.reply_photo, status_caption(), "caption",
                         photo=buf, parse_mode=ParseMode.HTML)
    except Exception as e:  # noqa: BLE001
        log.warning("status fail: %s", e)
        await say(target, f"❌ Không render được bảng trạng thái: {esc(str(e)[:100])}")


def status_caption() -> str:
    s = stats.all_stats()
    return (
        f"📊 <b>{esc(BRAND)}</b> — trạng thái hệ thống\n"
        f"⏱ Uptime: <b>{uptime_str(START_TS)}</b>\n"
        f"🎬 TikTok: <b>{s.get('tiktok_dl', 0)}</b> lượt • 🎵 Nhạc: <b>{s.get('sing', 0)}</b>\n"
        f"🎤 Voice: <b>{s.get('tts', 0)}</b> • 📸 Chụp web: <b>{s.get('capwed', 0)}</b>\n"
        f"🕐 {now_vn()}"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bảng thông số hệ thống — chỉ admin bot (đã ẩn khỏi menu người dùng)."""
    if not admins.is_admin(update.effective_user.id):
        await reply_html(update, "🔒 Lệnh này không dành cho bạn.")
        return
    msg = await reply_html(update, "📊 <i>Đang render bảng thông số…</i>")
    buf = await render_status()
    try:
        await msg.delete()
    except Exception:  # noqa: BLE001
        pass
    await _rich_send(
        update.effective_message.reply_photo, status_caption(), "caption",
        photo=buf, parse_mode=ParseMode.HTML,
    )


# ================================================================= /gif (admin)
async def cmd_gif(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/gif on | off — admin bật/tắt gif cho toàn bot (lưu bền sau khi restart)."""
    if not admins.is_admin(update.effective_user.id):
        await reply_html(update, "❌ Lệnh này chỉ dành cho admin bot.")
        return
    arg = (context.args[0].strip().lower() if context.args else "")
    if arg in ("on", "bat", "bật", "1", "mo", "mở"):
        set_gif_enabled(True)
        await reply_html(update, "✅ <b>Đã BẬT gif</b>\n<i>Mỗi lệnh của bot sẽ có 1 gif chiikawa phía trên câu trả lời.</i>")
        return
    if arg in ("off", "tat", "tắt", "0", "khoa", "khóa"):
        set_gif_enabled(False)
        await reply_html(update, "✅ <b>Đã TẮT gif</b>\n<i>Bot trả lời chữ, không gửi gif nữa. Bật lại: <code>/gif on</code></i>")
        return

    state = "✅ đang BẬT" if gif_enabled() else "❌ đang TẮT"
    await reply_html(
        update,
        f"🎞 <b>GIF: {state}</b>\n"
        "────────────\n"
        "• <code>/gif on</code> — bật gif\n"
        "• <code>/gif off</code> — tắt gif\n"
        "• Chỉ gửi khi gõ lệnh của bot hoặc bấm nút của bot (chat thường không có gif).\n"
        f"• Chống spam: tối thiểu {config.GIF_MIN_GAP:g}s / chat.",
    )


# ================================================================= /ping
async def cmd_console(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /console — xem lại giao diện console (chỉ admin). """
    if not admins.is_admin(update.effective_user.id):
        return
    import html as _html
    import shutil as _sh
    b = console.banner(VERSION, "TikTok · Instagram · Facebook · YouTube · Music · TTS",
                       stats_commands=len(BOT_COMMANDS),
                       animate=False)
    ff = "có" if _sh.which("ffmpeg") else "thiếu"
    body = (b + "\n"
            + console.box("THÔNG TIN", [
                ("Bot", "KHOA AN TOOLS"),
                ("Phiên bản", VERSION),
                ("ffmpeg", ff),
                ("Số lệnh", str(len(BOT_COMMANDS))),
                ("Đang chạy", time.strftime("%H:%M:%S")),
            ]))
    await reply_html(update, "<pre>" + _html.escape(body) + "</pre>")


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/ping — đo độ trễ thật: vòng lặp xử lý + đường tới Telegram API."""
    t0 = time.perf_counter()
    msg = await reply_html(update, "📶 <i>Đang ping…</i>")
    proc_ms = int((time.perf_counter() - t0) * 1000)
    api_ms = await ping_telegram()
    if api_ms >= 999:
        api_txt = "❌ không kết nối được"
    elif api_ms < 150:
        api_txt = f"🔥 <b>{api_ms} ms</b> — rất nhanh"
    elif api_ms < 400:
        api_txt = f"⚡ <b>{api_ms} ms</b> — ổn định"
    else:
        api_txt = f"🐢 <b>{api_ms} ms</b> — hơi chậm"

    gif_state = "✅ bật" if gif_enabled() else "❌ tắt"
    chat = update.effective_chat
    try:
        load = os.getloadavg()[0]
    except Exception:  # noqa: BLE001
        load = 0.0
    try:
        import shutil

        free_gb = shutil.disk_usage(str(TMP_DIR)).free / (1024 ** 3)
    except Exception:  # noqa: BLE001
        free_gb = 0.0
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            mem = {}
            for line in f:
                k, _, v = line.partition(":")
                mem[k] = int(v.strip().split()[0])
        used_pct = 100 - int(mem.get("MemAvailable", 0) * 100 / max(mem.get("MemTotal", 1), 1))
    except Exception:  # noqa: BLE001
        used_pct = -1

    text = "\n".join([
        "📶 <b>PONG!</b>",
        "────────────",
        f"⏱ Xử lý: <b>{proc_ms} ms</b>",
        f"📶 Telegram API: {api_txt}",
        f"🕐 Bot chạy: <b>{uptime_str(START_TS)}</b>",
        f"🖥 Tải máy: <b>{load:.2f}</b>" + (f" · RAM <b>{used_pct}%</b>" if used_pct >= 0 else ""),
        f"💾 Đĩa trống: <b>{free_gb:.1f} GB</b>",
        f"🎞 GIF: {gif_state}",
        f"💬 Chat: <code>{chat.id if chat else '—'}</code>",
        f"⏰ {now_vn()}",
    ])
    if msg:
        await safe_edit(msg, text)
    else:
        await reply_html(update, text)


async def cmd_cleanup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admins.is_admin(update.effective_user.id):
        await reply_html(update, "🔒 Chỉ admin bot dùng được lệnh này.")
        return
    msg = await reply_html(update, "🧹 <i>Đang dọn tệp tạm…</i>")
    n, size = utils.clean_temp()
    await safe_edit(
        msg,
        f"🧹 Đã xoá <b>{n}</b> tệp tạm ({size:.1f} MB)\nThư mục tạm còn: {utils.dir_size():.1f} MB",
        parse_mode=ParseMode.HTML,
    )


def donate_text() -> str:
    return (
        f"💖 <b>ỦNG HỘ {esc(BRAND)}</b>\n"
        f"Bot chạy miễn phí 24/7. Nếu thấy hữu ích, mời {esc(OWNER_NAME)} ly cà phê ☕\n"
        f"Liên hệ: {esc(config.SUPPORT_URL)}"
    )


async def cmd_donate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/donate — QR ủng hộ + nội dung CK random (core/donate.py)."""
    await donate.cmd_donate(update, context)


# ================================================================= /sing
async def cmd_sing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if throttled(update.effective_user.id):
        return
    if not context.args:
        await reply_html(update, "🎵 Cú pháp: <code>/sing tên bài hát</code>\nVí dụ: <code>/sing Tắt đèn đi</code>")
        return
    stats.bump("sing")
    keyword = " ".join(context.args).strip()[:80]
    t0 = time.time()
    msg = await reply_html(update, f"🔍 <i>Đang tìm:</i> <b>{esc(keyword)}</b>…")
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

    tracks = await music.search_songs(keyword, 10)
    if not tracks:
        await safe_edit(msg, "❌ Không tìm thấy bài nào. Thử từ khoá khác nhé (tên bài + ca sĩ).")
        return

    # Chỉ báo tiến độ khi khâu tìm thực sự lâu (bình thường 0.3–1s, thêm tin nhắn
    # sẽ làm chậm thêm 1 vòng gọi API). Nhờ vậy máy chậm vẫn thấy bot đang chạy.
    if time.time() - t0 > 1.2:
        await safe_edit(msg, f"✅ Thấy <b>{len(tracks)}</b> bài • đang vẽ ảnh bìa…")

    thumbs = await music.fetch_thumbs(tracks)
    # Vẽ ảnh chạy ở pool CPU riêng -> không chặn các lệnh khác của bot.
    buf = await pool.run_cpu(
        canvas.sing_canvas, keyword, tracks, thumbs, int((time.time() - t0) * 1000)
    )
    context.bot_data[f"sing_{update.effective_chat.id}"] = tracks

    rows, row = [], []
    for i, _ in enumerate(tracks, 1):
        row.append(InlineKeyboardButton(f"{i}", callback_data=f"sg_{i}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("← Menu", callback_data="p_home")])

    caption = (f"🎵 <b>{esc(keyword)}</b>\n"
               f"Tìm được <b>{len(tracks)}</b> bài • bấm số để tải MP3\n{speed_tag(t0)}")
    # Gửi ảnh TRƯỚC, xoá tin "đang tìm" sau (chạy nền) -> không bắt user chờ thêm
    # 1 vòng API, mà vẫn không để lại tin nhắn rác.
    sent = await _rich_send(
        update.effective_message.reply_photo, caption, "caption",
        photo=buf, parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
    )
    if sent is None:
        log.error("send canvas fail -> gửi dạng chữ")
        await reply_html(update, caption, reply_markup=InlineKeyboardMarkup(rows))
    else:
        asyncio.create_task(_silent_delete(msg))


async def cb_sing_download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        idx = int((q.data or "").split("_", 1)[1])
    except (IndexError, ValueError):
        await safe_answer(q)
        return
    tracks = context.bot_data.get(f"sing_{q.message.chat_id}") or []
    if not tracks or idx < 1 or idx > len(tracks):
        await safe_answer(q, "Danh sách đã cũ, tìm lại giúp bạn nhé.", alert=True)
        return
    await safe_answer(q, "⏳ Đang lấy audio…")

    tr = tracks[idx - 1]
    chat_id = q.message.chat_id
    status = await say(q.message, f"⏳ Đang tải: <b>{esc(tr['title'][:60])}</b>", parse_mode=ParseMode.HTML)
    await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_VOICE)

    async def safe_thumb():
        try:
            return await http.download_bytes(tr.get("thumb", ""), max_bytes=900_000, timeout=12)
        except Exception:  # noqa: BLE001
            return None

    # tải audio và ảnh bìa SONG SONG (trước đây tải xong audio mới đi lấy ảnh -> chậm thêm)
    (stream, info), thumb = await asyncio.gather(music.download_audio(tr["id"]), safe_thumb())
    try:
        await status.delete()
    except Exception:  # noqa: BLE001
        pass

    if not stream:
        await say(q.message, 
            "❌ Không lấy được audio bài này.\n• Video có thể bị chặn theo vùng.\n• Thử bài khác giúp bạn nhé.",
        )
        return

    title = html.escape(info.get("title") or tr["title"])
    artist = html.escape(info.get("uploader") or tr.get("channel") or "YouTube")
    size_mb = len(stream.getvalue()) / 1024 / 1024
    caption = (f"🎵 <b>{title}</b>\n"
               f"👤 {artist} • ⏱ {human_duration(info.get('duration') or tr.get('duration'))}\n"
               f"💾 {size_mb:.1f} MB • {esc(BRAND)}")

    sent = False
    try:
        await _rich_send(
            q.message.reply_audio, caption, "caption",
            audio=stream, title=info.get("title") or tr["title"], performer=info.get("uploader") or "",
            parse_mode=ParseMode.HTML, duration=int(info.get("duration") or 0) or None,
            thumbnail=thumb, read_timeout=120, write_timeout=120,
        )
        sent = True
    except Exception as e:  # noqa: BLE001
        log.warning("send audio fail: %s", e)
        try:
            await _rich_send(
                q.message.reply_document, caption, "caption",
                document=stream, parse_mode=ParseMode.HTML,
                read_timeout=120, write_timeout=120,
            )
            sent = True
        except Exception as e2:  # noqa: BLE001
            await say(q.message, f"❌ Gửi tệp thất bại: {esc(str(e2)[:100])}")

    if sent:
        # tải xong -> bỏ luôn bàn phím 10 bài để danh sách không bị bấm lại / spam
        await drop_sing_keyboard(q.message, idx, tr)


# ================================================================= tải TikTok
DL_CACHE: dict[str, dict] = {}
DL_TTL = 3600
DL_MAX = 500


SITE_NAME = {"tiktok": "TikTok", "instagram": "Instagram", "facebook": "Facebook", "youtube": "YouTube"}
SITE_ICON = {"tiktok": "🎬", "instagram": "📸", "facebook": "📘", "youtube": "▶️"}


def site_label(url: str) -> tuple[str, str, str]:
    """(mã, tên, icon) của nền tảng từ link."""
    site = social.platform(url)
    return site, SITE_NAME.get(site, "Media"), SITE_ICON.get(site, "🎬")


def auto_kind(text: str, site: str) -> str:
    """Đọc ý người dùng. TikTok ghi 'mp3' -> tải CẢ 2 (video + nhạc)."""
    kind = social.want_kind(text, "video")
    if site == "tiktok" and kind == "audio":
        return "both"
    return kind


def dl_remember(url: str) -> str:
    """Lưu link gốc để nút bấm tải tiếp bản còn lại (video <-> mp3)."""
    now = time.time()
    for k in [k for k, v in DL_CACHE.items() if now - v["t"] > DL_TTL]:
        DL_CACHE.pop(k, None)
    if len(DL_CACHE) > DL_MAX:
        for k in sorted(DL_CACHE, key=lambda x: DL_CACHE[x]["t"])[:100]:
            DL_CACHE.pop(k, None)
    tok = uuid.uuid4().hex[:12]
    DL_CACHE[tok] = {"url": url, "t": now, "site": social.platform(url)}
    return tok


def dl_buttons(token: str, kind: str) -> InlineKeyboardMarkup | None:
    """Nút tải thêm MP3 dưới tệp vừa gửi.

    v18: BỎ nút "Cả 2". Chỉ còn 1 nút tải audio, và nút đó chỉ dùng được MỘT lần
    (bấm xong bot gỡ bàn phím + đánh dấu đã dùng) để không ai bấm spam.
    """
    if kind != "video":
        return None
    return B.kb([[
        B.btn("Tải audio (MP3)", cb=f"tk:m:{token}", style=B.BLUE, icon="🎵"),
    ]])


def media_caption(item: downloader.MediaItem, t0: float) -> str:
    icon = {"audio": "🎵", "photo": "🖼"}.get(item.kind, "🎬")
    site = SITE_NAME.get(item.platform or "", "")
    head = f"{icon} <b>{esc(item.title[:90] or site or 'Media')}</b>"
    if site:
        head += f"\n<code>{site}</code>"
    parts = [head]
    meta = []
    if item.uploader:
        meta.append(f"👤 {esc(item.uploader)}")
    if item.duration:
        meta.append(f"⏱ {human_duration(item.duration)}")
    if item.views:
        meta.append(f"👀 {human_int(item.views)}")
    if meta:
        parts.append(" • ".join(meta))
    parts.append(speed_tag(t0))
    return "\n".join(parts)


async def send_media(update: Update, context: ContextTypes.DEFAULT_TYPE, items, t0: float,
                     reply_markup=None):
    chat = update.effective_chat.id
    if not items:
        return
    photos = [i for i in items if i.kind == "photo"]
    if photos:
        from telegram import InputMediaPhoto as IMP

        chunk = photos[:10]
        if len(chunk) > 1:
            media = [IMP(media=open(p.path, "rb")) for p in chunk]
            try:
                await context.bot.send_media_group(chat, media)
            except Exception as e:  # noqa: BLE001
                log.warning("send album fail: %s", e)
            await say(update.effective_message, 
                media_caption(chunk[0], t0), parse_mode=ParseMode.HTML, reply_markup=reply_markup,
            )
        else:
            with open(chunk[0].path, "rb") as f:
                await _rich_send(
                    update.effective_message.reply_photo, media_caption(chunk[0], t0), "caption",
                    photo=f, parse_mode=ParseMode.HTML, reply_markup=reply_markup,
                )
        return

    first = True
    for item in items:
        size = os.path.getsize(item.path)
        if size > MAX_UPLOAD_MB * 1024 * 1024:
            await reply_html(update, f"⚠️ Tệp {file_size(size)} vượt giới hạn {MAX_UPLOAD_MB} MB của Telegram.")
            continue
        markup = reply_markup if first else None
        cap = media_caption(item, t0)
        try:
            with open(item.path, "rb") as f:
                await context.bot.send_chat_action(chat, ChatAction.UPLOAD_VIDEO)
                if item.kind == "video":
                    sent = await _rich_send(
                        update.effective_message.reply_video, cap, "caption",
                        video=f, parse_mode=ParseMode.HTML,
                        duration=item.duration or None, supports_streaming=True,
                        read_timeout=180, write_timeout=180, reply_markup=markup,
                    )
                else:
                    sent = await _rich_send(
                        update.effective_message.reply_audio, cap, "caption",
                        audio=f, title=item.title[:64] or "audio", performer=item.uploader[:64],
                        parse_mode=ParseMode.HTML,
                        duration=item.duration or None, read_timeout=180, write_timeout=180,
                        reply_markup=markup,
                    )
            if sent is None:
                raise RuntimeError("gửi media không thành công")
            first = False
        except Exception as e:  # noqa: BLE001
            log.warning("send media fail: %s", e)
            try:
                with open(item.path, "rb") as f:
                    sent = await _rich_send(
                        update.effective_message.reply_document, cap, "caption",
                        document=f, parse_mode=ParseMode.HTML,
                        read_timeout=180, write_timeout=180, reply_markup=markup,
                    )
                if sent is None:
                    raise RuntimeError("gửi document không thành công")
                first = False
            except Exception as e2:  # noqa: BLE001
                await reply_html(update, f"❌ Không gửi được tệp: {esc(str(e2)[:120])}")


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _fmt_secs(s: float) -> str:
    s = int(max(s, 0))
    return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


class _Progress:
    """Hiện % + tốc độ + ETA lên chính tin nhắn 'đang tải' (throttle 2.5s)."""

    def __init__(self, msg, label: str, icon: str) -> None:
        self.msg = msg
        self.label = label
        self.icon = icon
        self.t0 = time.time()
        self.last = 0.0
        self.done = False
        self._task: asyncio.Task | None = None

    def feed(self, d: dict) -> None:
        if self.done:
            return
        status = d.get("status")
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        got = d.get("downloaded_bytes") or 0
        if status == "finished":
            self.done = True
            return
        if status != "downloading":
            return
        now = time.time()
        if now - self.last < 2.5:
            self._pending = d
            return
        self.last = now
        self._render(total, got, d.get("speed") or 0, d.get("eta") or 0)

    def _render(self, total: float, got: float, speed: float, eta: float) -> None:
        pct = (got / total * 100) if total else 0.0
        bar_len = 10
        filled = int(round(pct / 100 * bar_len))
        bar = "█" * filled + "░" * (bar_len - filled)
        lines = [f"{self.icon} <b>Đang tải từ {self.label}…</b>",
                 f"<code>{bar}</code> {pct:4.1f}%"]
        if total:
            lines.append(f"<i>{_fmt_bytes(got)} / {_fmt_bytes(total)}</i>")
        if speed:
            lines.append(f"⚡ <i>{_fmt_bytes(speed)}/s"
                         + (f" · còn ~{_fmt_secs(eta)}" if eta else "") + "</i>")
        self._spawn("\n".join(lines))

    def _spawn(self, text: str) -> None:
        async def _go() -> None:
            await safe_edit(self.msg, text, parse_mode=ParseMode.HTML)
        try:
            asyncio.create_task(_go())
        except Exception:  # noqa: BLE001
            pass

    def stop(self) -> None:
        self.done = True


async def _slow_note(msg, label: str) -> None:
    """Sau 8s chưa xong thì trấn an người dùng, khỏi tưởng bot treo."""
    try:
        await asyncio.sleep(8)
        await safe_edit(msg, f"⏳ <i>Vẫn đang tải từ {label}… video dài sẽ lâu hơn chút.</i>",
                        parse_mode=ParseMode.HTML)
        await asyncio.sleep(15)
        await safe_edit(msg, f"⏳ <i>{label} đang trả dữ liệu chậm — mình vẫn đang chờ, bạn đừng gửi lại link.</i>",
                        parse_mode=ParseMode.HTML)
    except (Exception, asyncio.CancelledError):  # noqa: BLE001
        pass


async def do_download(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str,
                      kind: str = "video", *, height: int = 1080,
                      quiet_fail: bool = False) -> bool:
    """Tải media từ TikTok / Instagram / Facebook / YouTube. Trả True nếu có tệp."""
    site, label, icon = site_label(url)
    if not site:
        if not quiet_fail:
            await reply_html(
                update,
                "🎬 <b>Link chưa hỗ trợ</b>\n"
                "Bot nhận: <b>TikTok · Instagram · Facebook · YouTube</b>\n"
                "• <code>https://www.tiktok.com/@user/video/…</code>\n"
                "• <code>https://www.instagram.com/reel/…</code>\n"
                "• <code>https://www.facebook.com/share/…</code>\n"
                "• <code>https://youtu.be/…</code>",
            )
        return False

    t0 = time.time()
    msg = await reply_html(update, f"{icon} <i>Đang tải từ {label}…</i>")
    try:
        await context.bot.send_chat_action(
            update.effective_chat.id,
            ChatAction.UPLOAD_VOICE if kind == "audio" else ChatAction.UPLOAD_VIDEO,
        )
    except Exception:  # noqa: BLE001
        pass

    prog = _Progress(msg, label, icon)
    slow = asyncio.create_task(_slow_note(msg, label))
    try:
        result = await social.download_any(url, kind, height=height,
                                           on_progress=prog.feed)
    finally:
        slow.cancel()
        prog.stop()

    secs = time.time() - t0
    if not result.items:
        err = result.error or "Không tải được"
        console.event(site, f"lỗi · {kind} · {err[:60]}", ok=False, secs=secs)
        if quiet_fail:
            try:
                await msg.delete()
            except Exception:  # noqa: BLE001
                pass
            return False
        await safe_edit(
            msg,
            f"❌ <b>Tải thất bại — {label}</b>\n<i>{esc(err[:200])}</i>\n\n"
            "• Kiểm tra link còn sống không (bài riêng tư / đã xoá thì bot không vào được)\n"
            "• Instagram/Facebook đôi lúc bắt đăng nhập — thử lại sau 1 phút\n"
            "• Video YouTube dài: gửi lại kèm <code>720</code> để tải bản nhẹ hơn",
        )
        return False

    stats.bump("dl")
    stats.bump("tiktok_dl")
    try:
        await msg.delete()
    except Exception:  # noqa: BLE001
        pass

    token = dl_remember(url)
    for it in result.items:
        try:
            console.event(site, f"{it.kind} · {(it.uploader or 'n/a')[:22]}",
                          ok=True, secs=secs,
                          size=file_size(os.path.getsize(it.path)))
        except Exception:  # noqa: BLE001
            console.event(site, it.kind, ok=True, secs=secs)
    await send_media(update, context, result.items, t0, reply_markup=dl_buttons(token, kind))
    downloader.cleanup(result.items)
    return True


async def cb_tiktok_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nút dưới tệp: tải MP3 — CHỈ dùng 1 lần, bấm xong bot gỡ nút luôn."""
    q = update.callback_query
    parts = (q.data or "").split(":")
    if len(parts) < 3:
        await safe_answer(q)
        return
    want, token = parts[1], parts[2]
    item = DL_CACHE.get(token)
    if not item or time.time() - item["t"] > DL_TTL:
        await safe_answer(q, "⚠️ Nút đã hết hạn, gửi lại link giúp mình nhé.", alert=True)
        return

    # đã bấm 1 lần rồi -> không tải nữa (chống spam bấm liên tục)
    if item.get("used"):
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001
            pass
        await safe_answer(q, "🔒 Nút này chỉ bấm được 1 lần thôi nhé.", alert=True)
        return

    item["used"] = True
    kind = {"v": "video", "m": "audio", "b": "both"}.get(want, "video")
    # gỡ bàn phím NGAY để không ai bấm lại được nữa
    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception as e:  # noqa: BLE001
        log.debug("gỡ nút không được: %s", str(e)[:60])
    await safe_answer(q, "⏳ Đang tải audio…")
    await do_download(update, context, item["url"], kind)


async def cmd_dl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /dl <link> — video không logo (TikTok / IG / FB / YouTube). """
    text = update.effective_message.text or ""
    url = extract_url(text) or social.any_link(text) or ""
    if not url:
        await reply_html(
            update,
            "🎬 <b>TẢI VIDEO</b>\n"
            "<code>/dl https://www.tiktok.com/@user/video/123</code>\n"
            "<code>/dl https://www.instagram.com/reel/xxxx</code>\n"
            "<code>/dl https://youtu.be/xxxx</code>\n"
            "<i>Hoặc chỉ cần dán link vào chat.</i>",
        )
        return
    if throttled(update.effective_user.id):
        return
    site = social.platform(url)
    await do_download(update, context, url, auto_kind(text, site) if site else "video")


async def cmd_mp3(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /mp3 <link> — nhạc MP3 (TikTok: tải cả video + nhạc). """
    text = update.effective_message.text or ""
    url = extract_url(text) or social.any_link(text) or ""
    if not url:
        await reply_html(
            update,
            "🎵 <b>LẤY NHẠC / ÂM THANH</b>\n"
            "<code>/mp3 https://www.tiktok.com/@user/video/123</code>\n"
            "<code>/mp3 https://youtu.be/xxxx</code> · <code>/mp3 link IG</code>\n"
            "<i>TikTok: bot gửi cả video lẫn MP3.</i>",
        )
        return
    if throttled(update.effective_user.id):
        return
    site = social.platform(url)
    if site == "tiktok":
        await do_download(update, context, url, "both")
    else:
        await do_download(update, context, url, "audio")


async def cmd_cookie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /cookie — dán cookie trình duyệt cho Instagram / Facebook (bài riêng tư). """
    from core import cookies as ck

    msg = update.effective_message
    args = list(context.args or [])
    site = ck.norm(args[0]) if args and ck.norm(args[0]) in ck.SITES else "ig"
    body = " ".join(args[1:]).strip() if len(args) > 1 else ""

    if not body and msg.reply_to_message:
        body = (msg.reply_to_message.text or msg.reply_to_message.caption or "").strip()
    if not body and msg.document:
        try:
            f = await msg.document.get_file()
            raw = await f.download_as_bytearray()
            body = bytes(raw).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""

    if body.lower() in ("clear", "xoa", "xoá", "reset", "-"):
        ck.clear(site)
        await reply_html(update, f"🗑 Đã xoá cookie {site}.")
        return
    if not body:
        rows = []
        for name in ("ig", "fb", "yt"):
            n, days = ck.count(name), ck.age_days(name)
            label = ck.LABELS.get(name, name)
            rows.append(f"• <b>{label}</b>: {n} cookie" + (f" · {days:.0f} ngày" if n else ""))
        await reply_html(
            update,
            "🍪 <b>COOKIE (bài riêng tư · video bị chặn)</b>\n" + "\n".join(rows) + "\n\n"
            "<b>Cách lấy (1 lần, dùng được lâu):</b>\n"
            "1. Chrome trên máy tính → cài extension <b>Get cookies.txt LOCALLY</b>\n"
            "2. Mở instagram.com / facebook.com / youtube.com (đã đăng nhập) → bấm extension → "
            "<i>Export</i> → được file <code>cookies.txt</code>\n"
            "3. Gửi file đó vào đây kèm chú thích:\n"
            "   <code>/cookie ig</code> · <code>/cookie fb</code> · <code>/cookie yt</code>\n\n"
            "Hoặc dán trực tiếp nội dung (reply tin chứa cookie).\n"
            "Xoá: <code>/cookie ig clear</code>\n\n"
            "<i>Cookie chỉ lưu trên máy chủ của bot, không gửi đi đâu khác.</i>",
        )
        return
    n, err = ck.save(site, body)
    try:
        await msg.delete()
    except Exception:  # noqa: BLE001
        pass
    if n:
        await reply_html(update, f"✅ Đã lưu <b>{n}</b> cookie cho <b>{site}</b> (tin chứa cookie vừa xoá).")
    else:
        await reply_html(update, f"❌ Cookie không hợp lệ: {esc(err)}")


HEIGHT_RE = re.compile(r"\b(144|240|360|480|720|1080|1440|2160)p?\b")


def _height_of(text: str) -> int:
    m = HEIGHT_RE.search(text or "")
    return int(m.group(1)) if m else 1080


async def cmd_ytb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /ytb <link> [mp3 | cả 2] [720/1080] — tải YouTube. """
    text = update.effective_message.text or ""
    url = extract_url(text) or social.any_link(text) or ""
    if not url:
        await reply_html(
            update,
            "▶️ <b>TẢI YOUTUBE</b>\n"
            "<code>/ytb https://youtu.be/xxxx</code> — video 1080p\n"
            "<code>/ytb link mp3</code> — chỉ lấy nhạc\n"
            "<code>/ytb link cả 2</code> — video + mp3\n"
            "<code>/ytb link 720</code> — chọn độ phân giải\n"
            "<i>Video dài trên 2 giờ sẽ bị Telegram chặn 50MB — chọn 360/480.</i>",
        )
        return
    if social.platform(url) != "youtube":
        await reply_html(update, "❌ Đây không phải link YouTube. Link hợp lệ: "
                                 "<code>youtube.com/watch?v=…</code> hoặc <code>youtu.be/…</code>")
        return
    if throttled(update.effective_user.id, 1.0):
        return
    kind = social.want_kind(text, "video")
    await do_download(update, context, url, kind, height=_height_of(text))


async def cmd_ig(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /ig <link> — tải Reel / bài đăng Instagram (ảnh, video, mp3). """
    text = update.effective_message.text or ""
    url = extract_url(text) or social.any_link(text) or ""
    if not url:
        await reply_html(
            update,
            "📸 <b>TẢI INSTAGRAM</b>\n"
            "<code>/ig https://www.instagram.com/reel/xxxx</code>\n"
            "<code>/ig https://www.instagram.com/p/xxxx</code>\n"
            "<code>/ig link mp3</code> — lấy nhạc\n"
            "<i>Bài nhiều ảnh: bot gửi cả album. Bài riêng tư cần cookie: /cookie</i>",
        )
        return
    if social.platform(url) != "instagram":
        await reply_html(update, "❌ Đây không phải link Instagram. "
                                 "Dạng đúng: <code>instagram.com/reel/…</code>")
        return
    if throttled(update.effective_user.id, 1.0):
        return
    site = "instagram"
    await do_download(update, context, url, auto_kind(text, site))


# ================================================================= info TikTok
TIKINFO_API = "https://ksjdjdmfmxm.x10.mx/tik1.php"
TIKWM_INFO = "https://www.tikwm.com/api/user/info"


async def _info_newapi(user: str):
    """API riêng của bạn: trả (info, lý do lỗi)."""
    data = await http.fetch_json(f"{TIKINFO_API}?user={quote(user)}", timeout=20, retries=1)
    if not isinstance(data, dict):
        return None, "net"
    if data.get("error"):
        log.info("tikinfo: %s", str(data.get("error"))[:120])
        return None, "notfound"
    u = data.get("user") or {}
    if not u:
        return None, "notfound"
    # statsV2 là chuỗi (không bị tràn int32 như stats), ưu tiên số dương từ statsV2
    st = dict(data.get("stats") or {})
    for k, v in (data.get("statsV2") or {}).items():
        try:
            iv = int(str(v))
        except (TypeError, ValueError):
            continue
        if iv >= 0:
            st[k] = iv
    return {"user": u, "stats": st}, ""


async def _info_tikwm(user: str):
    data = await http.fetch_json(
        TIKWM_INFO, method="POST", data={"unique_id": user},
        headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=20, retries=1,
    )
    if not data or data.get("code") != 0 or not data.get("data"):
        return None
    d = data["data"] or {}
    return {"user": d.get("user") or {}, "stats": d.get("stats") or {}}


TIKTOK_WEB = "https://www.tiktok.com/@"
REGION_NAMES = {
    "VN": "VI",  # TikTok hiển thị mã khu vực VI cho Việt Nam
}


def _num(value) -> str:
    try:
        return f"{int(value or 0):,}"
    except (TypeError, ValueError):
        return "0"


def _created(u: dict) -> str:
    stamp = (u.get("createTime_chuan") or "").strip()
    if stamp:
        return stamp
    ts = u.get("createTime")
    try:
        from datetime import datetime, timedelta, timezone

        return datetime.fromtimestamp(int(ts), timezone(timedelta(hours=7))).strftime("%Y-%m-%d | %H:%M:%S")
    except Exception:  # noqa: BLE001
        return "—"


def _latency_label(sec: float) -> str:
    if sec <= 0:
        return "—"
    if sec < 1.5:
        icon, label = "🔥", "Nhanh"
    elif sec < 3.5:
        icon, label = "⚡", "Ổn định"
    else:
        icon, label = "🐢", "Chậm"
    return f"{icon} {label} ({sec:.2f}s)"


_REGION_CACHE: dict[str, tuple[float, str]] = {}


async def _region_for(user: str, u: dict) -> str:
    """Khu vực: ưu tiên field API, sau đó tikwm, node/share, cuối cùng là HTML TikTok."""
    reg = str(u.get("region") or "").strip().upper()
    if len(reg) == 2:
        return REGION_NAMES.get(reg, reg)

    key = user.lower()
    hit = _REGION_CACHE.get(key)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]

    found = await _region_lookup(user)
    _REGION_CACHE[key] = (time.time(), found)
    if len(_REGION_CACHE) > 500:
        _REGION_CACHE.clear()
        _REGION_CACHE[key] = (time.time(), found)
    return found


async def _region_lookup(user: str) -> str:
    # 0) API phụ do bạn tự khai báo (REGION_API trong .env)
    if config.REGION_API:
        try:
            status, body, _ = await http.fetch(
                config.REGION_API.format(user=quote(user)), timeout=6, retries=0,
            )
            if status == 200 and body:
                text = body.decode("utf-8", errors="replace")
                m = re.search(r'"region"\s*:\s*"([A-Za-z]{2})"', text) or re.fullmatch(r"\s*([A-Za-z]{2})\s*", text)
                if m:
                    return m.group(1).upper()
        except Exception:  # noqa: BLE001
            pass

    # 1) tikwm
    try:
        data = await http.fetch_json(
            TIKWM_INFO, method="POST", data={"unique_id": user},
            headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=6, retries=0,
        )
        if isinstance(data, dict):
            cand = ((data.get("data") or {}).get("user") or {}).get("region")
            if isinstance(cand, str) and len(cand.strip()) == 2:
                return cand.strip().upper()
    except Exception:  # noqa: BLE001
        pass

    # 2) node/share (nhẹ hơn tải cả trang)
    try:
        status, body, _ = await http.fetch(
            "https://www.tiktok.com/node/share/user/@" + quote(user),
            headers={"Accept-Language": "vi-VN,vi;q=0.9"}, timeout=6, retries=0,
        )
        if status == 200 and body:
            m = re.search(r'"region"\s*:\s*"([A-Za-z]{2})"', body.decode("utf-8", errors="replace"))
            if m:
                return m.group(1).upper()
    except Exception:  # noqa: BLE001
        pass

    # 3) HTML trang profile (chỉ lấy đúng khối userInfo để không nhầm khu vực người xem)
    try:
        status, body, _ = await http.fetch(
            TIKTOK_WEB + quote(user), headers={"Accept-Language": "vi-VN,vi;q=0.9"}, timeout=8, retries=0,
        )
        if status == 200 and body:
            text = body.decode("utf-8", errors="replace")
            m = re.search(r'"userInfo".{0,6000}?"region"\s*:\s*"([A-Za-z]{2})"', text, re.S)
            if m:
                return m.group(1).upper()
            m = re.search(r'"region"\s*:\s*"([A-Za-z]{2})"', text)
            if m:
                return m.group(1).upper()
    except Exception:  # noqa: BLE001
        pass
    return "—"


def info_caption(u: dict, st: dict, user: str, region: str = "—", latency: float = 0.0) -> str:
    name = u.get("nickname") or user
    uid_ = u.get("uniqueId") or u.get("unique_id") or user
    private = bool(u.get("privateAccount"))
    verified = bool(u.get("verified"))
    commerce = u.get("commerceUserInfo") or {}
    shop = bool(u.get("ttSeller")) or bool(commerce.get("commerceUser"))
    bio = (u.get("signature") or "").strip()
    bio_txt = esc(bio[:180]) if bio else "« Không có tiểu sử. »"
    if bio:
        bio_txt = f"« {bio_txt} »"
    link = f"{TIKTOK_WEB}{uid_}"
    return "\n".join([
        "╭━━━〔 ✨ TIKTOK PROFILE STATS 〕━━━╮",
        "┃",
        f"┣ 📌 Tên hiển thị: <b>{esc(name)}</b>",
        f"┣ ⚡ ID: <code>{esc(str(u.get('id') or '—'))}</code>",
        f"┣ ✨ Trạng thái: {'🔒 Riêng tư' if private else '🌍 Công khai'}",
        f"┣ ✅ Huy hiệu: {'✅ Đã xác minh' if verified else '⚠️ Chưa xác minh'}",
        f"┣ 🔥 Cửa hàng: {'✅ Có' if shop else '❌ Không có'}",
        f"┣ 🌐 Khu vực: <b>{esc(region)}</b>",
        "┃",
        "┣ 📊 Tương tác:",
        f"┃ ├ 👥 Follower: <b>{_num(st.get('followerCount'))}</b>",
        f"┃ ├ 👤 Following: <b>{_num(st.get('followingCount'))}</b>",
        f"┃ ├ 🎥 Video: <b>{_num(st.get('videoCount'))}</b> clips",
        f"┃ └ 💖 Likes: <b>{_num(st.get('heartCount') or st.get('heart'))}</b>",
        "┃",
        f"┣ ⏰ Ngày tạo: <b>{esc(_created(u))}</b>",
        "┣ 📌 Tiểu sử:",
        f"┃ {bio_txt}",
        "┃",
        "╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯",
        f'👉 Xem trang: <a href="{esc(link)}">tiktok.com/@{esc(uid_)}</a>',
        "",
        f"⚡ Độ trễ phản hồi: {_latency_label(latency)}",
    ])


async def cmd_getinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Thông tin kênh TikTok — API riêng (ksjdjdmfmxm.x10.mx), tikwm làm dự phòng."""
    if not context.args:
        await reply_html(update, "🔎 Cú pháp: <code>/getinfo username</code> (không cần @)")
        return
    raw = " ".join(context.args).strip()
    m = re.search(r"tiktok\.com/@([\w.\-]+)", raw)
    user = (m.group(1) if m else raw).strip().lstrip("@")
    if not user:
        await reply_html(update, "🔎 Cú pháp: <code>/getinfo username</code> (không cần @)")
        return
    if throttled(update.effective_user.id):
        return

    msg = await reply_html(update, "🔎 <i>Đang lấy thông tin…</i>")
    t0 = time.time()
    info, reason = await _info_newapi(user)
    latency = time.time() - t0
    if not info and reason == "net":
        info = await _info_tikwm(user)
    if not info:
        await safe_edit(
            msg,
            f"❌ Không tìm thấy kênh <code>{esc(user)}</code>.\n"
            "<i>Kiểm tra lại username (không cần @), hoặc kênh có thể đã bị xoá.</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    u, st = info["user"], info["stats"]
    avatar = u.get("avatarLarger") or u.get("avatarMedium") or u.get("avatarThumb") or ""
    # lấy khu vực + tải avatar cùng lúc cho nhanh
    region, raw_img = await asyncio.gather(
        _region_for(user, u),
        http.download_bytes(avatar, max_bytes=6 * 1024 * 1024, timeout=25) if avatar else asyncio.sleep(0, result=b""),
    )
    caption = info_caption(u, st, user, region, latency)
    if raw_img:
        buf = io.BytesIO(raw_img)
        buf.name = "avatar.jpg"
        try:
            await msg.delete()
        except Exception:  # noqa: BLE001
            pass
        try:
            sent = await _rich_send(
                update.effective_message.reply_photo, caption, "caption",
                photo=buf, parse_mode=ParseMode.HTML,
            )
            if sent is not None:
                return
            log.info("send getinfo photo fail: trả về None")
        except Exception as e:  # noqa: BLE001
            log.info("send getinfo photo fail: %s", e)
    await safe_edit(msg, caption, parse_mode=ParseMode.HTML)


# ================================================================= TTS
VOICE_GROUP: dict[int, str] = {}      # user -> nhóm giọng đang xem
VOICE_PAGE: dict[int, int] = {}       # user -> trang trong nhóm
USER_SPEED: dict[int, str] = {}       # user -> cham | vua | nhanh
VOICE_PER_PAGE = 9


def voice_panel(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    """Bảng chọn giọng: theo nhóm ngôn ngữ, có phân trang + tốc độ đọc."""
    cur = USER_VOICE.get(uid, tts.DEFAULT_VOICE)
    speed = USER_SPEED.get(uid, tts.DEFAULT_SPEED)
    group = VOICE_GROUP.get(uid, "vi")
    voices = tts.group_voices(group)
    pages = max(1, (len(voices) + VOICE_PER_PAGE - 1) // VOICE_PER_PAGE)
    page = min(max(VOICE_PAGE.get(uid, 0), 0), pages - 1)
    VOICE_PAGE[uid] = page
    chunk = voices[page * VOICE_PER_PAGE:(page + 1) * VOICE_PER_PAGE]

    rows: list = []
    for i in range(0, len(chunk), 3):
        rows.append(B.row(*[
            B.btn(v.desc, cb=f"vc:{v.key}",
                  style=B.GREEN if v.key == cur else B.BLUE,
                  icon="✅" if v.key == cur else "🎤")
            for v in chunk[i:i + 3]
        ]))

    grows, grow = [], []
    for key, label in tts.GROUPS:
        grow.append(B.btn(label, cb=f"vc:g:{key}",
                          style=B.GREEN if key == group else B.BLUE,
                          icon="✅" if key == group else "🎧"))
        if len(grow) == 2:
            grows.append(grow)
            grow = []
    if grow:
        grows.append(grow)
    rows += grows

    rows.append(B.row(
        B.btn("Trước", cb=f"vc:p:{page - 1}", style=B.BLUE, icon="⬅️"),
        B.btn(f"{page + 1}/{pages}", cb="vc:noop", style=B.BLUE),
        B.btn("Sau", cb=f"vc:p:{page + 1}", style=B.BLUE, icon="➡️"),
        B.btn("Tất cả giọng", cb="vc:all", style=B.GREEN, icon="🌍"),
    ))
    rows.append(B.row(*[
        B.btn(label, cb=f"vc:s:{k}",
              style=B.GREEN if speed == k else B.BLUE,
              icon="✅" if speed == k else "⚡")
        for k, (_rate, label) in tts.SPEEDS.items()
    ]))

    label_now = tts.desc_of(cur)
    text = (
        "🎤 <b>CHỌN GIỌNG ĐỌC</b>\n"
        f"├ Đang dùng: <b>{esc(label_now)}</b>\n"
        f"├ Tốc độ: <b>{tts.SPEEDS.get(speed, tts.SPEEDS['vua'])[1]}</b>\n"
        f"└ Nhóm: <b>{dict(tts.GROUPS).get(group, group)}</b> · {len(voices)} giọng\n"
        "──────────────\n"
        "Chọn giọng bên dưới rồi gõ <code>/tts nội dung</code>.\n"
        "<i>Muốn đọc bằng giọng khác mà không đổi cài đặt:</i> "
        "<code>/tts nu nội dung</code>"
    )
    return text, B.kb(rows)


async def cmd_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text, kb = voice_panel(uid)
    await reply_html(update, text, reply_markup=kb)


def _voice_all_panel() -> tuple[str, InlineKeyboardMarkup]:
    langs = [
        ("vi-VN", "Việt"), ("en-US", "Anh-Mỹ"), ("en-GB", "Anh-Anh"),
        ("zh-CN", "Trung"), ("ja-JP", "Nhật"), ("ko-KR", "Hàn"),
        ("th-TH", "Thái"), ("id-ID", "Indo"), ("fr-FR", "Pháp"),
        ("de-DE", "Đức"), ("es-ES", "Tây Ban Nha"), ("ru-RU", "Nga"),
        ("hi-IN", "Hindi"), ("pt-BR", "Bồ Đào Nha"), ("it-IT", "Ý"),
        ("ar-SA", "Ả Rập"), ("tr-TR", "Thổ"), ("nl-NL", "Hà Lan"),
    ]
    rows, row = [], []
    for code, label in langs:
        row.append(B.btn(label, cb=f"vc:l:{code}:0", style=B.BLUE, icon="🌐"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(B.row(B.btn("Quay lại", cb="vc:g:vi", style=B.BLUE, icon="⬅️")))
    return ("🌍 <b>TẤT CẢ GIỌNG AI</b>\n"
            "Chọn ngôn ngữ để xem toàn bộ giọng (Microsoft neural).\n"
            "<i>Giọng chọn ở đây cũng dùng được cho /tts.</i>"), B.kb(rows)


async def _voice_lang_panel(prefix: str, page: int) -> tuple[str, InlineKeyboardMarkup, bool]:
    voices = await tts.edge_voices(prefix)
    per = 9
    pages = max(1, (len(voices) + per - 1) // per)
    page = min(max(page, 0), pages - 1)
    chunk = voices[page * per:(page + 1) * per]
    rows, row = [], []
    for v in chunk:
        short = v.get("ShortName", "")
        name = short.replace(prefix + "-", "").replace("Neural", "")
        gender = {"Female": "♀", "Male": "♂"}.get(v.get("Gender", ""), "")
        row.append(B.btn(f"{gender} {name}"[:28], cb=f"vc:edge:{short}", style=B.BLUE))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(B.row(
        B.btn("Trước", cb=f"vc:l:{prefix}:{page - 1}", style=B.BLUE, icon="⬅️"),
        B.btn(f"{page + 1}/{pages}", cb="vc:noop", style=B.BLUE),
        B.btn("Sau", cb=f"vc:l:{prefix}:{page + 1}", style=B.BLUE, icon="➡️"),
        B.btn("Nhóm", cb="vc:all", style=B.BLUE, icon="⬅️"),
    ))
    text = (f"🌍 <b>{esc(prefix)}</b> · {len(voices)} giọng\n"
            "Bấm 1 giọng để dùng cho /tts.")
    return text, B.kb(rows), bool(voices)


async def cb_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    body = (q.data or "")[3:]
    parts = body.split(":")

    if body == "noop":
        await safe_answer(q)
        return

    if parts[0] == "g" and len(parts) > 1:
        VOICE_GROUP[uid] = parts[1]
        VOICE_PAGE[uid] = 0
        await safe_answer(q)
        text, kb = voice_panel(uid)
        await safe_edit(q.message, text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if parts[0] == "p" and len(parts) > 1:
        try:
            VOICE_PAGE[uid] = max(0, int(parts[1]))
        except ValueError:
            pass
        await safe_answer(q)
        text, kb = voice_panel(uid)
        await safe_edit(q.message, text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if parts[0] == "s" and len(parts) > 1:
        if parts[1] in tts.SPEEDS:
            USER_SPEED[uid] = parts[1]
        await safe_answer(q, f"Tốc độ: {tts.SPEEDS.get(parts[1], tts.SPEEDS['vua'])[1]}")
        text, kb = voice_panel(uid)
        await safe_edit(q.message, text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if parts[0] == "all":
        await safe_answer(q)
        text, kb = _voice_all_panel()
        await safe_edit(q.message, text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if parts[0] == "l" and len(parts) >= 3:
        prefix = parts[1]
        try:
            page = int(parts[2])
        except ValueError:
            page = 0
        await safe_answer(q)
        text, kb, has = await _voice_lang_panel(prefix, page)
        if not has:
            await safe_edit(q.message, f"❌ Chưa lấy được danh sách giọng {esc(prefix)}.\n"
                                       "<i>Mạng máy chủ đang chặn máy chủ giọng nói, thử lại sau.</i>",
                            parse_mode=ParseMode.HTML, reply_markup=back_menu())
            return
        await safe_edit(q.message, text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    # chọn giọng: vc:<key> hoặc vc:edge:<ShortName>
    key = body[len("edge:"):] if parts[0] == "edge" else body
    if key.startswith("edge:"):
        USER_VOICE[uid] = key
    elif key in tts.VOICES:
        USER_VOICE[uid] = key
    else:
        await safe_answer(q)
        return
    await safe_answer(q, f"Đã chọn: {tts.desc_of(key)}")
    text, kb = voice_panel(uid)
    try:
        await safe_edit(q.message, text, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception:  # noqa: BLE001
        pass


async def do_tts(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str,
                 voice_key: str | None = None):
    if not text.strip():
        await reply_html(
            update,
            "🎤 Cú pháp: <code>/tts nội dung</code>\n"
            "Đổi giọng: <code>/voice</code> · hoặc <code>/tts nu nội dung</code>\n"
            "Reply 1 tin nhắn bằng <code>/tts</code> để đọc tin đó.",
        )
        return
    stats.bump("tts")
    msg = await reply_html(update, "🎤 <i>Đang tạo giọng nói…</i>")
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.RECORD_VOICE)
    uid = update.effective_user.id
    voice = voice_key or USER_VOICE.get(uid, tts.DEFAULT_VOICE)
    speed = USER_SPEED.get(uid, tts.DEFAULT_SPEED)
    data, used, desc = await tts.text_to_speech(text, voice, speed)
    if not data:
        await safe_edit(msg, "❌ Không tạo được giọng nói, thử lại sau nhé.")
        return
    buf = io.BytesIO(data)
    buf.name = "voice.mp3"
    try:
        await msg.delete()
    except Exception:  # noqa: BLE001
        pass
    try:
        sent = await _rich_send(
            update.effective_message.reply_voice, f"🎤 {esc(desc)}", "caption",
            voice=buf, parse_mode=ParseMode.HTML,
            read_timeout=120, write_timeout=120,
        )
        if sent is None:
            raise RuntimeError("voice không gửi được")
    except Exception:
        try:
            buf.seek(0)
            await _rich_send(
                update.effective_message.reply_audio, f"🎤 {esc(desc)}", "caption",
                audio=buf, parse_mode=ParseMode.HTML,
                read_timeout=120, write_timeout=120,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("send voice fail: %s", e)
            await reply_html(update, f"❌ Gửi voice lỗi: {esc(str(e)[:100])}")


async def cmd_tts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/tts [giọng] nội dung — đổi giọng ngay trong lệnh cũng được."""
    args = list(context.args or [])
    voice_key = None
    if args:
        head = args[0].lower()
        if head in tts.VOICES or head.startswith("edge:"):
            voice_key = args.pop(0)
    text = " ".join(args)
    if not text and update.effective_message.reply_to_message:
        r = update.effective_message.reply_to_message
        text = r.text or r.caption or ""
    await do_tts(update, context, text, voice_key)


# ================================================================= chụp website
def cap_buttons(url: str, mode: str = "desktop") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(("✓ " if mode == "desktop" else "") + "Máy tính", callback_data="cw:d"),
         InlineKeyboardButton(("✓ " if mode == "mobile" else "") + "Điện thoại", callback_data="cw:m")],
        [InlineKeyboardButton("↻ Chụp lại", callback_data="cw:r"),
         InlineKeyboardButton("↗ Mở trang", url=url)],
    ])


async def shot(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, mode: str = "desktop"):
    uid = update.effective_user.id
    w, h = (390, 844) if mode == "mobile" else (config.CAPTURE_W, config.CAPTURE_H)
    try:
        data, info = await capture.screenshot(url, w, h)
    except capture.CaptureError as e:
        await reply_html(
            update,
            f"❌ <b>Không chụp được</b>\n<i>{esc(str(e))}</i>\n\n"
            "• Kiểm tra link còn mở được không\n"
            "• Trang chặn bot / cần đăng nhập thì không chụp được",
        )
        return
    data = capture.to_png(data)
    title = await capture.page_title(url)
    stats.bump("capwed")
    buf = io.BytesIO(data)
    buf.name = "website.png"
    caption = (
        f"📸 <b>{esc(title or 'Ảnh chụp website')}</b>\n"
        f"🔗 <code>{esc(url[:180])}</code>\n"
        f"🖼 {info['width']}×{info['height']} • {file_size(len(data))} • {info.get('elapsed_ms', 0)} ms"
        + (f" • ⚠️ HTTP {info['status']}" if int(info.get('status') or 0) >= 400 else "")
    )
    try:
        await _rich_send(
            update.effective_message.reply_photo, caption, "caption",
            photo=buf, parse_mode=ParseMode.HTML, reply_markup=cap_buttons(url, mode),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("send capture fail: %s", e)
        try:
            buf.seek(0)
            await _rich_send(
                update.effective_message.reply_document, caption, "caption",
                document=buf, filename="website.png", parse_mode=ParseMode.HTML,
                reply_markup=cap_buttons(url, mode),
            )
        except Exception as e2:  # noqa: BLE001
            await reply_html(update, f"❌ Gửi ảnh lỗi: {esc(str(e2)[:100])}")


async def cmd_capwed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    raw = " ".join(context.args).strip() if context.args else ""
    if not raw:
        reply = msg.reply_to_message
        raw = extract_url((reply.text or reply.caption or "") if reply else "") or ""
        if not raw:
            raw = extract_url(msg.text or "") or ""
    if not raw:
        await reply_html(
            update,
            "📸 Cú pháp: <code>/capwed https://example.com</code>\n"
            "Cũng có thể reply một tin nhắn có link rồi gõ <code>/capwed</code>.",
        )
        return
    if throttled(update.effective_user.id, 2.0):
        return
    url = capture.normalize_url(raw)
    CAP_STATE[update.effective_user.id] = url
    wait = await reply_html(update, f"📸 <i>Đang chụp</i> <code>{esc(url[:120])}</code>…")
    await shot(update, context, url, "desktop")
    try:
        await wait.delete()
    except Exception:  # noqa: BLE001
        pass


async def cb_capture(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    mode = (q.data or "").split(":", 1)[-1]
    url = CAP_STATE.get(q.from_user.id)
    if not url:
        await safe_answer(q, "Hết phiên, gửi lại /capwed link nhé.", alert=True)
        return
    target_mode = "mobile" if mode == "m" else "desktop"
    await safe_answer(q, "📸 Đang chụp lại…")
    try:
        w, h = (390, 844) if target_mode == "mobile" else (config.CAPTURE_W, config.CAPTURE_H)
        data, info = await capture.screenshot(url, w, h)
        data = capture.to_png(data)
        buf = io.BytesIO(data)
        buf.name = "website.png"
        title = await capture.page_title(url)
        caption = (
            f"📸 <b>{esc(title or 'Ảnh chụp website')}</b>\n"
            f"🔗 <code>{esc(url[:180])}</code>\n"
            f"🖼 {info['width']}×{info['height']} • {file_size(len(data))} • {info.get('elapsed_ms', 0)} ms"
            + (f" • ⚠️ HTTP {info['status']}" if int(info.get('status') or 0) >= 400 else "")
        )
        await q.edit_message_media(
            media=InputMediaPhoto(media=buf, caption=premium.ensure(caption), parse_mode=ParseMode.HTML),
            reply_markup=cap_buttons(url, target_mode),
        )
    except capture.CaptureError as e:
        await say(q.message, f"❌ {esc(str(e))}")
    except Exception as e:  # noqa: BLE001
        log.warning("recapture fail: %s", e)
        await safe_answer(q, "Không chụp lại được, thử /capwed lại nhé.", alert=True)


# ================================================================= thời tiết (canvas)
async def cmd_weather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    city = " ".join(context.args).strip() if context.args else "Hà Nội"
    msg = await reply_html(update, f"🌤 <i>Đang lấy thời tiết</i> <b>{esc(city)}</b>…")
    w = await utils.weather(city)
    if not w:
        await safe_edit(msg, "❌ Không tìm thấy thành phố này. Thử tên khác (VD: <code>Đà Nẵng</code>).")
        return
    stats.bump("weather")
    loop = asyncio.get_running_loop()
    try:
        buf = await pool.run_cpu(canvas.weather_canvas, w)
    except Exception as e:  # noqa: BLE001
        log.exception("weather canvas: %s", e)
        await safe_edit(msg, "❌ Render ảnh thời tiết lỗi, thử lại nhé.")
        return
    caption = (
        f"🌤 <b>{esc(w['name'])}</b>\n"
        f"🌡 <b>{round(float(w['temp']))}°C</b> • {esc(w['desc'])}\n"
        f"💧 {w['humidity']}% • 💨 {w['wind']} km/h"
    )
    try:
        await msg.delete()
    except Exception:  # noqa: BLE001
        pass
    await _rich_send(
        update.effective_message.reply_photo, caption, "caption",
        photo=buf, parse_mode=ParseMode.HTML,
    )


# ================================================================= tiện ích
async def cmd_translate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await dich.cmd_translate(update, context)


async def cmd_short(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = context.args[0] if context.args else extract_url(update.effective_message.text or "")
    if not url:
        await reply_html(update, "🔗 Cú pháp: <code>/short https://link-dài…</code>")
        return
    msg = await reply_html(update, "🔗 <i>Đang rút gọn…</i>")
    out = await utils.shorten(url)
    if not out:
        await safe_edit(msg, "❌ Không rút gọn được link này.")
        return
    await safe_edit(msg, f"🔗 <code>{esc(out)}</code>", parse_mode=ParseMode.HTML)


# ================================================================= id / sticker
def _uid_from_text(raw: str) -> int | None:
    """Đọc ID từ: 123456789 · t.me/abc · tg://user?id=… · https://t.me/abc/123."""
    t = (raw or "").strip()
    if not t:
        return None
    m = re.search(r"(?:id=|/)(\d{5,})(?:[/?#]|$)", t)
    if t.isdigit():
        return int(t)
    if m:
        return int(m.group(1))
    return None


def _uname_from_text(raw: str) -> str:
    """Đọc username từ @abc · t.me/abc · https://t.me/abc?x=1."""
    t = (raw or "").strip()
    m = re.search(r"(?:t\.me/|telegram\.me/|@)([A-Za-z0-9_]{3,32})", t)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_]{3,32}", t):
        return t
    return ""


async def _username_to_id(context: ContextTypes.DEFAULT_TYPE, uname: str) -> tuple[int | None, str]:
    """Tìm ID từ username bằng 4 lớp — lớp nào trúng thì thôi.

    1. Sổ tay bot tự ghi (người từng nhắn / từng ở trong nhóm bot).
    2. Bot API get_chat (kênh & nhóm công khai).
    3. Tài khoản userbot đã đăng nhập bằng /qc (nếu có).
    4. Không ra -> trả None, nơi gọi sẽ gợi ý người giống nhất.
    """
    uname = uname.lstrip("@").strip()
    if not uname:
        return None, ""

    uid = userindex.find_username(uname)
    if uid:
        return uid, "sổ người dùng của bot"

    try:
        chat = await context.bot.get_chat("@" + uname)
        got = getattr(chat, "id", None)
        if got:
            userindex.remember(chat, getattr(chat, "full_name", "") or getattr(chat, "title", ""))
            return int(got), "Telegram API"
    except Exception as e:  # noqa: BLE001
        log.info("get_chat @%s lỗi: %s", uname, str(e)[:80])

    # userbot: chỉ admin bot đăng nhập /qc mới có
    for aid in admins.all_ids():
        try:
            client, _err = await qc.get_client(aid)
            if not client:
                continue
            ent = await asyncio.wait_for(client.get_entity("@" + uname), timeout=8)
            got = getattr(ent, "id", None)
            if got:
                userindex.remember(ent)
                return int(got), "tài khoản /qc của admin"
        except Exception:  # noqa: BLE001
            continue
    return None, ""


async def cmd_getid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /id — User ID + username + tiểu sử + ẢNH ĐẠI DIỆN.

    Nguồn, theo thứ tự chắc ăn: reply tin nhắn → ID số / link → @username
    (tra sổ bot → Telegram API → userbot /qc) → không tham số = chính mình.
    """
    msg = update.effective_message
    me = update.effective_user
    chat = update.effective_chat
    raw = " ".join(context.args or []).strip()
    r = msg.reply_to_message

    target = None
    how = "Chính bạn"
    err = ""

    if r:
        how = "Reply tin nhắn"
        target = getattr(r, "from_user", None) or getattr(r, "sender_chat", None)
        if target is None:
            err = "Tin nhắn đó không có người gửi (ẩn danh hoặc tin của kênh)."
    elif raw:
        uid = _uid_from_text(raw)
        if uid:
            how = "ID / link có sẵn ID"
            try:
                target = await context.bot.get_chat(uid)
                if target is None:
                    raise RuntimeError("none")
            except Exception as e:  # noqa: BLE001
                log.info("get_chat %s lỗi: %s", uid, str(e)[:80])
                target = types.SimpleNamespace(id=uid)
                err = ("Bot chưa từng thấy người này nên chỉ có ID, "
                       "chưa lấy được tên/ảnh đại diện.")
        else:
            uname = _uname_from_text(raw)
            if not uname:
                await reply_html(
                    update,
                    "❌ Không đọc được username/ID.\n"
                    "Thử: <code>/id @username</code> · <code>/id 123456789</code> · "
                    "reply tin nhắn rồi <code>/id</code>",
                )
                return
            how = "@username"
            got_id, via = await _username_to_id(context, uname)
            if got_id:
                try:
                    target = await context.bot.get_chat(got_id)
                except Exception:  # noqa: BLE001
                    target = types.SimpleNamespace(id=got_id, username=uname)
                how = f"@username → {via}" if via else "@username"
            else:
                # gợi ý người giống nhất trong sổ để bấm chọn
                near = userindex.search(uname)
                kb = B.kb([[B.btn(f"{n['name'] or n['username'] or n['id']} · @{n['username']}",
                                  cb=f"idp:{n['id']}", style=B.BLUE, icon="👤")]
                           for n in near]) if near else None
                await reply_html(
                    update,
                    f"❌ <b>Không tìm thấy</b> <b>@{esc(uname)}</b> trong sổ.\n"
                    "──────────────\n"
                    "Telegram <b>không cho bot tra ID của người lạ</b> chỉ từ username "
                    "(chỉ kênh/nhóm công khai mới tra được).\n\n"
                    "<b>Cách chắc ăn 100%:</b>\n"
                    "• Reply (trả lời) 1 tin nhắn của người đó rồi gõ <code>/id</code>\n"
                    "• Hoặc dán link <code>t.me/username</code> / ID số\n"
                    "• Hoặc để họ nhắn cho bot 1 câu, sau đó <code>/id @username</code> chạy ngay"
                    + ("\n\n<i>Trong sổ có mấy người giống thế này:</i>" if near else ""),
                    reply_markup=kb,
                )
                return
    else:
        target = me

    if target is None:
        await reply_html(update, f"❌ {err or 'Không xác định được người dùng.'}")
        return

    uid = getattr(target, "id", None)
    if getattr(target, "username", None):
        userindex.remember(target)
    prof = await userinfo.fetch(context.bot, uid) if uid else {"full": None, "avatar": None}
    text = userinfo.build(target, prof.get("full"), how, me.id if me else None, chat)
    if err:
        text += f"\n\n⚠️ <i>{esc(err)}</i>"

    avatar = userinfo.avatar_or_none(prof.get("avatar"))
    if avatar is not None:
        sent = await _rich_send(msg.reply_photo, text, "caption",
                                photo=avatar, parse_mode=ParseMode.HTML)
        if sent is not None:
            return
    await reply_html(update, text)


async def cb_id_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bấm vào tên trong danh sách gợi ý của /id."""
    q = update.callback_query
    try:
        uid = int((q.data or "")[4:])
    except ValueError:
        await safe_answer(q, "ID không hợp lệ.", alert=True)
        return
    await safe_answer(q, "Đang lấy hồ sơ…")
    prof = await userinfo.fetch(context.bot, uid)
    target = types.SimpleNamespace(id=uid, username=userindex.username_of(uid))
    text = userinfo.build(target, prof.get("full"), "chọn từ sổ người dùng",
                          q.from_user.id, update.effective_chat)
    avatar = userinfo.avatar_or_none(prof.get("avatar"))
    try:
        if avatar is not None:
            await _rich_send(q.message.reply_photo, text, "caption",
                             photo=avatar, parse_mode=ParseMode.HTML)
        else:
            await say(q.message, text, parse_mode=ParseMode.HTML)
    except Exception as e:  # noqa: BLE001
        log.info("cb_id_pick lỗi: %s", str(e)[:80])


async def cmd_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    r = msg.reply_to_message
    src = r.photo[-1] if (r and r.photo) else (r.document if (r and r.document) else None)
    if not src:
        await reply_html(update, "🖼 Reply một ảnh rồi gõ <code>/sticker</code> nhé.")
        return
    wait = await reply_html(update, "🖼 <i>Đang tạo sticker…</i>")
    try:
        tg_file = await src.get_file()
        raw = bytes(await tg_file.download_as_bytearray())
        loop = asyncio.get_running_loop()
        out = await pool.run_cpu(_photo_to_sticker, raw)
        if not out:
            await safe_edit(wait, "❌ Ảnh này không chuyển được.")
            return
        buf = io.BytesIO(out)
        buf.name = "sticker.webp"
        await msg.reply_sticker(sticker=buf)
        try:
            await wait.delete()
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        log.warning("sticker fail: %s", e)
        await safe_edit(wait, "❌ Tạo sticker thất bại.")


def _photo_to_sticker(raw: bytes) -> bytes | None:
    from PIL import Image, ImageOps

    try:
        img = Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None
    img = ImageOps.exif_transpose(img)
    w, h = img.size
    side = min(w, h)
    img = img.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))
    img = img.resize((512, 512), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="WEBP", quality=95)
    return out.getvalue()


# ================================================================= /js emoji uid
async def cmd_js(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    target = msg.reply_to_message or msg
    items = emoji_uid.extract(target)
    if not items:
        await reply_html(
            update,
            "🎭 <b>LẤY UID EMOJI PREMIUM</b>\n"
            "Reply một tin nhắn có <b>emoji premium</b> rồi gõ <code>/js</code>.\n"
            "Hoặc gửi kèm: <code>/js 😀</code> (nếu emoji đó là premium).\n\n"
            "<i>Emoji thường (không phải premium) sẽ không có UID.</i>",
        )
        return
    stats.bump("emoji")
    src = "tin nhắn được reply"
    txt, name = emoji_uid.build_txt(items, src)
    buf = io.BytesIO(txt)
    buf.name = name
    preview = " ".join(it.get("emoji") or "▫️" for it in items[:12])
    await _rich_send(
        msg.reply_document,
        (
            f"🎭 <b>ĐÃ LẤY {len(items)} UID EMOJI</b>\n"
            f"{esc(preview)}\n"
            f"📄 File: <code>{esc(name)}</code>\n"
            "<i>Mở file để lấy UID + cú pháp HTML.</i>"
        ),
        "caption",
        document=buf,
        filename=name,
        parse_mode=ParseMode.HTML,
    )
    # gửi thêm JSON nếu nhiều emoji
    if len(items) > 1:
        js, jname = emoji_uid.build_json(items)
        jbuf = io.BytesIO(js)
        jbuf.name = jname
        try:
            await _rich_send(
                msg.reply_document, "🗂 Bản JSON (dễ dùng trong code)", "caption",
                document=jbuf, filename=jname, parse_mode=ParseMode.HTML,
            )
        except Exception:  # noqa: BLE001
            pass


# ================================================================= panel admin
def admin_panel_text() -> str:
    s = stats.all_stats()
    return (
        f"👑 <b>PANEL ADMIN — {esc(BRAND)}</b>\n"
        f"────────────\n"
        f"⏱ Uptime: <b>{uptime_str(START_TS)}</b>\n"
        f"🛡 Admin bot: <b>{len(admins.all_ids())}</b>\n"
        f"────────────\n"
        f"🎬 TikTok: <b>{s.get('tiktok_dl', 0)}</b>\n"
        f"🎵 Nhạc: <b>{s.get('sing', 0)}</b> • 🎤 Voice: <b>{s.get('tts', 0)}</b>\n"
        f"📸 Chụp web: <b>{s.get('capwed', 0)}</b> • 🌤 Thời tiết: <b>{s.get('weather', 0)}</b>\n"
        f"🎭 Emoji UID: <b>{s.get('emoji', 0)}</b> • 🎨 Banner: <b>{s.get('banner', 0)}</b>\n"
        f"🎞 GIF: <b>{'bật' if gif_enabled() else 'tắt'}</b> • /ping: <code>/gif on|off</code>\n"
        f"🛡 Captcha: <b>{'bật' if captcha.enabled() else 'tắt'}</b> "
        f"(<b>{captcha.total()}</b> người đã xác minh)\n"
        f"💬 Reaction: <b>{'bật' if reaction.enabled() else 'tắt'}</b>\n"
        f"────────────\n"
        f"🕐 {now_vn()}"
    )


def admin_panel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Thống kê chi tiết", callback_data="a_stats")],
        [InlineKeyboardButton(f"Captcha: {'bật' if captcha.enabled() else 'tắt'}",
                              callback_data="a_captcha"),
         InlineKeyboardButton(f"Reaction: {'bật' if reaction.enabled() else 'tắt'}",
                              callback_data="a_react")],
        [InlineKeyboardButton("Danh sách admin", callback_data="a_admins"),
         InlineKeyboardButton("Thêm admin", callback_data="a_addadmin")],
        [InlineKeyboardButton("Dọn rác", callback_data="a_clean")],
        [InlineKeyboardButton("← Menu", callback_data="p_home"),
         InlineKeyboardButton("✕ Đóng", callback_data="a_close")],
    ])


async def cmd_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admins.is_admin(update.effective_user.id):
        await reply_html(update, "🔒 Lệnh này dành cho admin bot.")
        return
    await reply_html(update, admin_panel_text(), reply_markup=admin_panel_kb())


async def admin_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    if not admins.is_admin(q.from_user.id):
        await safe_answer(q, "🔒 Chỉ admin bot.", alert=True)
        return
    action = data[2:]

    if action == "close":
        await safe_answer(q)
        try:
            await q.message.delete()
        except Exception:  # noqa: BLE001
            pass
        return

    if action == "panel":
        await safe_answer(q)
        await safe_edit(q.message, admin_panel_text(), parse_mode=ParseMode.HTML, reply_markup=admin_panel_kb())
        return

    if action == "stats":
        await safe_answer(q, "Đang render…")
        buf = await render_status()
        try:
            await q.message.edit_media(
                media=InputMediaPhoto(media=buf, caption=premium.ensure(status_caption()), parse_mode=ParseMode.HTML),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Panel", callback_data="a_panel")]]),
            )
        except Exception as e:  # noqa: BLE001
            log.info("edit stats fail: %s", e)
            await _rich_send(q.message.reply_photo, status_caption(), "caption",
                             photo=buf, parse_mode=ParseMode.HTML)
        return

    if action == "admins":
        await safe_answer(q)
        lines = ["🛡 <b>DANH SÁCH ADMIN BOT</b>", ""]
        for uid in sorted(admins.all_ids()):
            tag = " 👑 (root)" if admins.is_root(uid) else ""
            lines.append(f"• <code>{uid}</code>{tag}")
        lines.append("\nThêm: <code>/addadmin &lt;id&gt;</code> • Gỡ: <code>/deladmin &lt;id&gt;</code>")
        await safe_edit(q.message, "\n".join(lines), parse_mode=ParseMode.HTML,
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Panel", callback_data="a_panel")]]))
        return

    if action == "addadmin":
        PENDING[q.from_user.id] = "addadmin"
        await safe_answer(q)
        await say(q.message, 
            "➕ <b>THÊM ADMIN</b>\n"
            "Gửi <b>User ID</b> của người bạn muốn cấp quyền admin bot.\n"
            "(Người đó nhắn <code>/getid</code> cho bot để lấy ID.)\n\n"
            "Gõ <code>huy</code> để bỏ.",
            parse_mode=ParseMode.HTML,
        )
        return

    if action == "clean":
        n, size = utils.clean_temp()
        await safe_answer(q, f"Đã xoá {n} tệp ({size:.1f} MB)")
        await safe_edit(q.message, admin_panel_text(), parse_mode=ParseMode.HTML, reply_markup=admin_panel_kb())
        return

    if action == "captcha":
        captcha.set_enabled(not captcha.enabled())
        await safe_answer(q, f"Captcha: {'BẬT' if captcha.enabled() else 'TẮT'}")
        await safe_edit(q.message, admin_panel_text(), parse_mode=ParseMode.HTML,
                        reply_markup=admin_panel_kb())
        return

    if action == "react":
        reaction.set_enabled(not reaction.enabled())
        await safe_answer(q, f"Reaction: {'BẬT' if reaction.enabled() else 'TẮT'}")
        await safe_edit(q.message, admin_panel_text(), parse_mode=ParseMode.HTML,
                        reply_markup=admin_panel_kb())
        return

    await safe_answer(q)


async def cmd_addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admins.is_admin(update.effective_user.id):
        await reply_html(update, "🔒 Chỉ admin bot.")
        return
    arg = (context.args or [""])[0].lstrip("-")
    if not arg.isdigit():
        await reply_html(update, "Cú pháp: <code>/addadmin 123456789</code>")
        return
    uid = int(arg)
    ok = admins.add(uid)
    await reply_html(update, f"{'✅ Đã thêm' if ok else 'ℹ️ Đã có sẵn'} admin <code>{uid}</code>.")


async def cmd_deladmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admins.is_admin(update.effective_user.id):
        await reply_html(update, "🔒 Chỉ admin bot.")
        return
    arg = (context.args or [""])[0].lstrip("-")
    if not arg.isdigit():
        await reply_html(update, "Cú pháp: <code>/deladmin 123456789</code>")
        return
    uid = int(arg)
    if admins.is_root(uid):
        await reply_html(update, "⚠️ Không thể gỡ admin gốc.")
        return
    ok = admins.remove(uid)
    await reply_html(update, f"{'🗑 Đã gỡ' if ok else 'ℹ️ Không có trong danh sách'} admin <code>{uid}</code>.")


# ================================================================= router text
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.text:
        return
    text = msg.text.strip()
    if text.startswith("/"):
        return

    # admin reply một tin rồi gửi "." -> bot tự dịch (kèm bảng chọn ngôn ngữ)
    if text == ".":
        user = update.effective_user
        if user and admins.is_admin(user.id):
            if msg.reply_to_message:
                if await dich.on_dot_reply(update, context):
                    return
            else:
                await dich.panel(update, context)
                return

    # đang chờ SĐT / mã đăng nhập / 2FA / nội dung quảng cáo (/qc)
    if await qc.on_text(update, context):
        return

    uid = update.effective_user.id if update.effective_user else 0

    # đang chờ TÊN cho banner
    if PENDING.get(uid) == "banner":
        if text.lower() in ("huy", "huỷ", "cancel", "thôi"):
            PENDING.pop(uid, None)
            await reply_html(update, "Đã bỏ.")
            return
        if 1 <= len(text) <= banner.MAX_NAME:
            PENDING.pop(uid, None)
            st = banner_state(uid)
            st["name"] = " ".join(text.split())
            await send_banner(update, context, st)
        else:
            await reply_html(update, "❌ Tên phải từ 1–42 ký tự. Gửi lại hoặc gõ <code>huy</code>.")
        return

    # chờ nhập ID admin từ panel
    if PENDING.get(uid) == "addadmin":
        if text.lower() in ("huy", "huỷ", "cancel", "thôi"):
            PENDING.pop(uid, None)
            await reply_html(update, "Đã bỏ.")
            return
        if text.lstrip("-").isdigit():
            PENDING.pop(uid, None)
            ok = admins.add(int(text))
            await reply_html(update, f"{'✅ Đã thêm' if ok else 'ℹ️ Đã có sẵn'} admin <code>{int(text)}</code>.")
        else:
            await reply_html(update, "❌ ID phải là số. Gửi lại hoặc gõ <code>huy</code>.", parse_mode=ParseMode.HTML)
        return

    url = extract_url(text) or social.any_link(text)
    if url:
        site = social.platform(url)
        if site:
            if throttled(uid):
                return
            await do_download(update, context, url, auto_kind(text, site))
            return
        await reply_html(
            update,
            "🎬 <b>Link chưa hỗ trợ</b>\n"
            "Bot nhận <b>TikTok · Instagram · Facebook · YouTube</b>.\n"
            "<i>Gửi đúng link bài viết/video, đừng gửi link trang chủ.</i>",
        )
        return

    if in_group(update):
        return
    await reply_html(update, "Gõ <code>/help</code> để xem hết lệnh, hoặc gửi link TikTok để tải.")


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ảnh gửi vào: chỉ dùng khi đang chờ NỘI DUNG quảng cáo (/qc)."""
    if await qc.on_photo(update, context):
        return


# ================================================================= FACEBOOK (/fb)
def _fb_text(info: dict) -> str:
    lines = [
        "👤 <b>FACEBOOK</b>",
        "──────────────",
    ]
    if info.get("name"):
        lines.append(f"👤 <b>Tên:</b> {esc(info['name'])}")
    lines.append(f"🆔 <b>ID:</b> <code>{esc(info.get('id', ''))}</code>")
    if info.get("username"):
        lines.append(f"🔗 <b>Username:</b> @{esc(info['username'])}")
    lines.append(f"🌐 <b>Hồ sơ:</b> https://www.facebook.com/{esc(info.get('id', ''))}")
    if info.get("how"):
        lines.append(f"🔍 <b>Nguồn:</b> {esc(info['how'])}")
    lines.append(f"🕐 <i>{now_vn()}</i>")
    return "\n".join(lines)


def _fb_kb(info: dict, default_av: bool = False) -> InlineKeyboardMarkup:
    uid = info.get("id", "")
    rows = [
        B.row(B.btn("Sao chép ID", copy=uid, style=B.GREEN, icon="🆔"),
              B.btn("Mở hồ sơ", url=f"https://www.facebook.com/{uid}", style=B.BLUE, icon="🌐")),
    ]
    if default_av:
        # chưa có cookie/token: nút "ảnh HD" chỉ mở ra ảnh xám -> thay bằng cách lấy ảnh thật
        rows.append(B.row(B.btn("Lấy ảnh thật (nạp cookie)", cb="p_fbcookie", style=B.GREEN, icon="🍪")))
        if not facebook.token():
            rows.append(B.row(B.btn("Hoặc nạp token", cb="p_fbtok", style=B.BLUE, icon="🔑")))
    else:
        hd = info.get("avatar_url") or (f"https://graph.facebook.com/{uid}/picture"
                                        "?type=large&width=720&height=720")
        rows.append(B.row(B.btn("Ảnh đại diện HD", url=hd, style=B.BLUE, icon="🖼")))
    return B.kb(rows)


async def cmd_fb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/fb <link | username | ID> — lấy ID + ảnh đại diện Facebook."""
    msg = update.effective_message
    args = list(context.args or [])
    if args and args[0].lower() in ("token", "key"):
        if not admins.is_admin(update.effective_user.id):
            await reply_html(update, "🔒 Chỉ admin bot đặt token.")
            return
        value = " ".join(args[1:]).strip()
        if value.lower() in ("clear", "xoa", "xoá", "reset", "-"):
            facebook.set_token("")
            await reply_html(update, "🗑 Đã xoá FB token.")
            return
        if not value:
            cur = facebook.token()
            await reply_html(
                update,
                "🔑 <b>FB TOKEN</b>\n"
                f"├ Hiện tại: <code>{'đã có' if cur else 'chưa có'}</code>\n"
                "├ Đặt: <code>/fb token EAAB…</code>\n"
                "└ Xoá: <code>/fb token clear</code>\n\n"
                "<i>Có token (Graph API) thì /fb @username tra được 100%.</i>",
            )
            return
        facebook.set_token(value)
        try:
            await msg.delete()
        except Exception:  # noqa: BLE001
            pass
        await reply_html(update, "✅ Đã lưu FB token (tin chứa token vừa được xoá).")
        return

    raw = " ".join(args).strip()
    if not raw and msg.reply_to_message:
        r = msg.reply_to_message
        raw = (r.text or r.caption or "").strip()
    if not raw:
        await reply_html(
            update,
            "📘 <b>FACEBOOK — LẤY ID + ẢNH ĐẠI DIỆN</b>\n"
            "<code>/fb https://www.facebook.com/ten.nguoi.dung</code>\n"
            "<code>/fb https://www.facebook.com/profile.php?id=100012345678</code>\n"
            "<code>/fb 100012345678</code> · <code>/fb @username</code>\n\n"
            "<i>Link nào có sẵn số ID là lấy được ngay (nhanh + chắc nhất).</i>",
        )
        return
    if throttled(update.effective_user.id, 2.0):
        return

    # Link Facebook: link video/reel thì tải media trước; link hồ sơ (/share/…,
    # /username) thì đi lấy ID trước cho nhanh, không tải thử nữa.
    media_hint = re.search(r"/(reel|videos|watch|photo|story\.php|permalink)|fb\.watch|vb\.\d+",
                           raw, re.I)
    if raw.lower().startswith("http") and social.platform(raw) == "facebook" and media_hint:
        kind = auto_kind(update.effective_message.text or raw, "facebook")
        if await do_download(update, context, raw, kind, quiet_fail=True):
            return
    wait = await reply_html(update, "🔎 <i>Đang tra Facebook…</i>")
    info = await facebook.resolve(raw)
    if not info.get("ok"):
        await safe_edit(
            wait,
            f"❌ <b>Chưa lấy được ID</b>\n<i>{info.get('err') or ''}</i>\n\n"
            "<b>CÁCH CHẮC ĂN (làm theo thứ tự):</b>\n"
            "1️⃣ <b>Trên điện thoại</b>: mở hồ sơ → <b>Chia sẻ</b> → <b>Sao chép liên kết</b>"
            " → dán vào đây\n"
            "   <i>(link chia sẻ dạng /share/… luôn mở ra được ID, kể cả hồ sơ để riêng tư)</i>\n"
            "2️⃣ Đã biết số ID thì dán thẳng:\n"
            "   <code>/fb 100012345678</code> · <code>/fb https://www.facebook.com/profile.php?id=100012345678</code>\n"
            "3️⃣ <b>@username</b> của nick không công khai thì cần cookie (1 lần là xong):\n"
            "   <code>/cookie fb</code> → gửi file cookies.txt",
            parse_mode=ParseMode.HTML,
            reply_markup=B.kb([[
                B.btn("Cách nạp cookie", copy="/cookie fb", style=B.GREEN, icon="🍪"),
                B.btn("Menu", cb="p_home", style=B.BLUE, icon="ℹ️"),
            ]]),
        )
        return

    avatar = await facebook.avatar(info["id"], info.get("avatar_url", ""))
    default_av = facebook.looks_default(avatar)
    caption = _fb_text(info)
    if default_av:
        # v18b: ảnh xám mặc định thì KHÔNG gửi (gửi vào chỉ tưởng bot lỗi)
        caption += ("\n\n🖼 <b>Ảnh đại diện:</b> Facebook chỉ trả ảnh xám khi chưa đăng nhập.\n"
                    "Nạp cookie 1 lần → ra ảnh THẬT 100%: <code>/cookie fb</code>")
        await safe_edit(wait, caption, parse_mode=ParseMode.HTML,
                        reply_markup=_fb_kb(info, True))
        return
    if avatar:
        buf = io.BytesIO(avatar)
        buf.name = "facebook.jpg"
        sent = await _rich_send(msg.reply_photo, caption, "caption", photo=buf,
                                parse_mode=ParseMode.HTML,
                                reply_markup=_fb_kb(info, default_av))
        if sent is not None:
            try:
                await wait.delete()
            except Exception:  # noqa: BLE001
                pass
            return
    await safe_edit(wait, caption + "\n\n<i>(Không tải được ảnh đại diện — hồ sơ có thể đang khoá ảnh.)</i>",
                    parse_mode=ParseMode.HTML, reply_markup=_fb_kb(info))


# ================================================================= COVER (/cover)
def _cover_args(raw: str, info: dict) -> tuple[str, str]:
    """Tách 'Tên bài | Nghệ sĩ' (hoặc 'Tên bài - Nghệ sĩ'). Không có thì lấy tag cũ."""
    raw = (raw or "").strip()
    if "|" in raw:
        left, _, right = raw.partition("|")
        return left.strip(), right.strip()
    if " - " in raw:
        left, _, right = raw.partition(" - ")
        return left.strip(), right.strip()
    if raw:
        # người dùng đổi tên -> mặc định ghi tên họ làm "bản quyền"
        return raw, OWNER_NAME
    return (info.get("title") or "Audio"), OWNER_NAME


def _cover_help() -> str:
    return (
        "🎧 <b>ĐỔI TÊN + ẢNH BÌA BÀI HÁT</b>\n"
        "Reply (trả lời) đúng tin nhắn có file nhạc rồi gõ lệnh:\n"
        "──────────────\n"
        "• <code>/cover</code> — lấy tên cũ, nghệ sĩ = " + esc(OWNER_NAME) + "\n"
        "• <code>/cover Tên bài</code> — đổi tên, nghệ sĩ mặc định\n"
        "• <code>/cover Tên bài | Nghệ sĩ</code> — đổi cả hai\n"
        "• Reply nhạc + gửi <b>ảnh kèm caption</b> <code>/cover Tên | Nghệ sĩ</code> "
        "→ ảnh đó thành bìa\n"
        "──────────────\n"
        "<i>Hỗ trợ MP3 · M4A · FLAC. File nhạc giữ nguyên chất lượng, chỉ đổi phần tên/ảnh bìa.</i>\n"
        "<i>Lấy nhạc trước bằng /sing hoặc /mp3.</i>"
    )


async def cmd_cover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/cover — ghi lại tên bài + nghệ sĩ + ảnh bìa cho file nhạc vừa tải."""
    msg = update.effective_message
    r = msg.reply_to_message
    src = None
    if r is not None:
        src = r.audio or r.document or r.voice
    if src is None or not getattr(src, "file_id", None):
        await reply_html(update, _cover_help())
        return

    mime = str(getattr(src, "mime_type", "") or "")
    name = getattr(src, "file_name", "") or "audio.mp3"
    if getattr(src, "voice", None) or name.endswith(".ogg"):
        await reply_html(update, "❌ Voice/OGG chưa đổi bìa được. Dùng file MP3 / M4A / FLAC nhé.")
        return
    if mime and not (mime.startswith("audio") or mime in ("video/mp4", "application/octet-stream")):
        await reply_html(update, "❌ Tin này không phải file nhạc. Reply đúng file MP3 / M4A / FLAC nhé.")
        return
    if int(getattr(src, "file_size", 0) or 0) > config.MAX_AUDIO_MB * 1024 * 1024:
        await reply_html(update, f"❌ File lớn hơn {config.MAX_AUDIO_MB} MB, Telegram không cho bot tải về.")
        return

    if throttled(update.effective_user.id, 2.0):
        return
    wait = await reply_html(update, "🎧 <i>Đang xử lý file nhạc…</i>")

    try:
        tg_file = await context.bot.get_file(src.file_id)
        data = bytes(await tg_file.download_as_bytearray())
    except Exception as e:  # noqa: BLE001
        log.info("cover tải file lỗi: %s", str(e)[:90])
        await safe_edit(wait, "❌ Không tải được file nhạc, thử lại nhé.")
        return

    info = cover.probe(data, name)
    if not info["ext"]:
        await safe_edit(wait, "❌ Định dạng này chưa hỗ trợ. Chỉ MP3 · M4A · FLAC.")
        return

    title, artist = _cover_args(" ".join(context.args or []), info)

    # ảnh bìa: ảnh kèm caption -> ảnh thu nhỏ của bài -> bìa cũ -> avatar người dùng
    pic = await _download_photo(context, msg)
    if not pic and getattr(src, "thumbnail", None):
        try:
            f = await context.bot.get_file(src.thumbnail.file_id)
            pic = bytes(await f.download_as_bytearray())
        except Exception:  # noqa: BLE001
            pic = None
    if not pic and info.get("cover"):
        pic = info["cover"]
    if not pic:
        pic = await _user_avatar(context, update.effective_user.id)

    try:
        out, ext = cover.apply(data, title, artist, pic, album="", filename=name)
    except cover.CoverError as e:
        log.info("cover apply lỗi: %s", str(e)[:100])
        await safe_edit(wait, "❌ File này đang bị khoá hoặc hỏng tag, mình không ghi được.")
        return

    ext_dot = cover.extension_for(ext)
    fname = cover.safe_filename(f"{title} - {artist}") + ext_dot
    buf = io.BytesIO(out)
    buf.name = fname
    caption = (
        f"🎧 <b>{esc(title)}</b>\n"
        f"👤 {esc(artist)}\n"
        f"📦 {file_size(len(out))} · {ext.upper()}"
        + (" · đã gắn ảnh bìa mới" if pic else "")
        + "\n<i>Tên + nghệ sĩ đã ghi thẳng vào file.</i>"
    )
    try:
        await msg.reply_audio(
            audio=buf, filename=fname, title=title, performer=artist,
            caption=caption, parse_mode=ParseMode.HTML,
            read_timeout=120, write_timeout=180,
        )
        try:
            await wait.delete()
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        log.warning("gửi cover lỗi: %s", str(e)[:100])
        try:
            buf.seek(0)
            await msg.reply_document(document=buf, filename=fname, caption=caption,
                                     parse_mode=ParseMode.HTML)
            try:
                await wait.delete()
            except Exception:  # noqa: BLE001
                pass
        except Exception as e2:  # noqa: BLE001
            await safe_edit(wait, f"❌ Gửi file lỗi: {esc(str(e2)[:90])}")


# ================================================================= setup
async def post_init(app: Application):
    base_cmds = [
        BotCommand("start", "Menu chính"),
        BotCommand("help", "Danh sách lệnh"),
        BotCommand("sing", "Tìm 10 bài hát + tải MP3"),
        BotCommand("dl", "Tải video TikTok"),
        BotCommand("mp3", "Tải nhạc nền TikTok (MP3)"),
        BotCommand("getinfo", "Thông tin kênh TikTok"),
        BotCommand("tts", "Chữ thành giọng nói"),
        BotCommand("voice", "Đổi giọng đọc"),
        BotCommand("capwed", "Chụp ảnh website"),
        BotCommand("banner", "Tạo banner theo tên"),
        BotCommand("thoitiet", "Thời tiết 3 ngày (ảnh)"),
        BotCommand("dich", "Dịch ~80 ngôn ngữ"),
        BotCommand("short", "Rút gọn link"),
        BotCommand("sticker", "Ảnh thành sticker"),
        BotCommand("getid", "Xem User ID / Chat ID"),
        BotCommand("js", "Lấy UID emoji premium"),
        BotCommand("anh18", "Ảnh 18+ theo chủ đề (nút bấm)"),
        BotCommand("vdgai", "Video ngắn 18+"),
        BotCommand("cover", "Đổi tên + ảnh bìa bài hát"),
        BotCommand("fb", "Facebook: lấy ID + avatar"),
        BotCommand("ping", "Kiểm tra độ trễ"),
        BotCommand("stars", "Mua VIP bằng Telegram Stars"),
        BotCommand("vip", "Xem hạn VIP"),
    ]
    # Menu mặc định: KHÔNG có lệnh admin
    try:
        await app.bot.set_my_commands(base_cmds)
    except Exception as e:  # noqa: BLE001
        log.info("set_my_commands lỗi: %s", e)

    # Menu riêng cho từng admin (chỉ admin thấy panel / lệnh quản trị bot)
    admin_cmds = base_cmds + [
        BotCommand("panel", "👑 Panel admin"),
        BotCommand("addadmin", "Thêm admin bot"),
        BotCommand("deladmin", "Gỡ admin bot"),
        BotCommand("cleanup", "Dọn tệp tạm"),
        BotCommand("gif", "Bật / tắt gif (on | off)"),
        BotCommand("captcha", "Xác minh lần đầu (on | off)"),
        BotCommand("react", "Tự thả cảm xúc (on | off)"),
        BotCommand("war", "War tag trong nhóm"),
        BotCommand("spam", "Spam tag trong nhóm"),
        BotCommand("stop", "Dừng war/spam"),
        BotCommand("qc", "Rải quảng cáo bằng acc riêng"),
        BotCommand("qcstop", "Dừng rải quảng cáo"),
        BotCommand("baotri", "Bảo trì bot / từng lệnh"),
        BotCommand("status", "Trạng thái hệ thống"),
        BotCommand("sleep", "Giờ ngủ của nhóm"),
    ]
    for aid in admins.all_ids():
        try:
            await app.bot.set_my_commands(admin_cmds, scope=BotCommandScopeChat(chat_id=aid))
        except Exception:  # noqa: BLE001
            pass
    # job kiểm tra giờ ngủ mỗi phút (bật/tắt theo khung giờ từng nhóm)
    if app.job_queue:
        app.job_queue.run_repeating(night.tick, interval=60, first=25)
    asyncio.create_task(gifpool.warmup())
    me = await app.bot.get_me()
    health.set_bot(me.username or "")
    await health.start()
    try:
        state = await premium.probe(app.bot)
    except Exception as e:  # noqa: BLE001
        state = f"probe lỗi: {str(e)[:60]}"
    log.info("Bot @%s đã sẵn sàng • icon premium: %s", me.username, state)


async def post_shutdown(app: Application):
    await war.stop_all()
    await qc.shutdown()
    await health.stop()
    await http.close_session()


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Lỗi trong handler: lỗi 'lành' (nút cũ, flood, mất mạng) thì im lặng, không spam chat."""
    health.count_request(errors=1)
    err = context.error
    text = str(err).lower()
    benign = (
        "query is too old", "query id is invalid", "message is not modified",
        "message to delete not found", "message can't be deleted",
        "flood control", "retry after", "timed out", "connection",
        "chat write forbidden", "bot was blocked", "message file_id",
    )
    if any(k in text for k in benign):
        log.info("bỏ qua lỗi lành: %s", str(err)[:100])
        return
    log.exception("Handler lỗi: %s", err)
    msg = getattr(update, "effective_message", None)
    if msg:
        try:
            await msg.reply_text(
                "⚠️ <i>Lệnh này vừa lỗi, bạn thử lại giúp mình nhé.</i>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:  # noqa: BLE001
            pass


# ================================================================= card chào / tạm biệt
async def _is_chat_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Bot admin HOẶC quản trị viên của chính nhóm đang dùng lệnh."""
    u = update.effective_user
    chat = update.effective_chat
    if not u or not chat:
        return False
    if admins.is_admin(u.id):
        return True
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return False
    try:
        m = await context.bot.get_chat_member(chat.id, u.id)
        return m.status in ("administrator", "creator")
    except Exception:                    # noqa: BLE001
        return False


async def _download_photo(context, msg) -> bytes | None:
    """Lấy bytes ảnh từ tin nhắn (photo hoặc document ảnh)."""
    if not msg:
        return None
    file_id = None
    if getattr(msg, "photo", None):
        file_id = msg.photo[-1].file_id
    elif getattr(msg, "document", None) and str(msg.document.mime_type or "").startswith("image/"):
        file_id = msg.document.file_id
    if not file_id:
        return None
    try:
        f = await context.bot.get_file(file_id)
        return bytes(await f.download_as_bytearray())
    except Exception as e:                # noqa: BLE001
        log.info("tải ảnh nền lỗi: %s", str(e)[:80])
        return None


# ================================================================= giờ ngủ
def sleep_text(chat_id: int, chat_title: str = "") -> str:
    return night.panel_text(chat_id, chat_title)


async def cmd_sleep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/sleep — admin nhóm tự đặt giờ ngủ."""
    chat = update.effective_chat
    msg = update.effective_message
    if not await _is_chat_admin(update, context):
        await reply_html(update, "🔒 Lệnh này không dành cho bạn.")
        return
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await reply_html(
            update,
            "🌙 <b>GIỜ NGỦ</b>\n"
            "Lệnh chạy <b>trong nhóm</b> (cần quyền quản trị).\n"
            "<code>/sleep 23:00 06:00</code> — ngủ 23h → 6h sáng\n"
            "<code>/sleep off</code> · <code>/sleep lock on|off</code>\n"
            "<i>Bot cần quyền “Hạn chế thành viên”.</i>",
        )
        return

    args = list(context.args or [])
    a0 = (args[0] or "").strip().lower() if args else ""
    c = night.cfg(chat.id)
    title = getattr(chat, "title", "") or ""

    if not args:
        await safe_edit(msg, sleep_text(chat.id, title), parse_mode=ParseMode.HTML,
                        reply_markup=night.panel_kb(chat.id))
        return

    if a0 in ("off", "tat", "tắt"):
        c["on"] = 0
        night.save()
        if c.get("asleep"):
            await night.open_chat(context.bot, chat.id, title)
        await reply_html(update, "⛔ Đã <b>TẮT</b> giờ ngủ của nhóm.")
        return

    if a0 == "lock":
        val = (args[1] if len(args) > 1 else "").lower()
        if val not in ("on", "off", "1", "0"):
            await reply_html(update, "Cú pháp: <code>/sleep lock on</code> hoặc <code>/sleep lock off</code>")
            return
        c["lock"] = 1 if val in ("on", "1") else 0
        night.save()
        await reply_html(update, "🔒 Kiểu: <b>khoá chat thật</b>" if c["lock"]
                         else "📣 Kiểu: <b>chỉ thông báo</b> (không khoá chat)")
        return

    if a0 in ("msg", "noidung", "nội dung"):
        body = " ".join(args[1:]).strip()
        c["msg"] = body[:300]
        night.save()
        await reply_html(update, "✅ Đã lưu lời nhắn giờ ngủ." if body
                         else "♻️ Đã về lời nhắn mặc định.")
        return

    if a0 in ("now", "thu", "thử"):
        ok = await night.close_chat(context.bot, chat.id, title)
        await reply_html(update, "🌙 Đã khoá thử." if ok
                         else "⚠️ Không khoá được — bot thiếu quyền <b>Hạn chế thành viên</b>.")
        return

    if a0 in ("wake", "mo", "mở"):
        ok = await night.open_chat(context.bot, chat.id, title)
        await reply_html(update, "☀️ Đã mở lại." if ok
                         else "⚠️ Không mở được — kiểm tra quyền của bot.")
        return

    raw = " ".join(args).replace("–", "-").replace("→", "-").replace(">", "-")
    parts = [p for p in re.split(r"[\s\-]+", raw) if p]
    if len(parts) >= 2:
        start, end = night.parse_hm(parts[0]), night.parse_hm(parts[1])
        if start is None or end is None or start == end:
            await reply_html(update, "⏰ Giờ không hợp lệ. Ví dụ: <code>/sleep 23:00 06:00</code>")
            return
        c.update({"on": 1, "start": night.hm(start), "end": night.hm(end)})
        night.save()
        await safe_edit(
            msg,
            f"✅ <b>ĐÃ ĐẶT GIỜ NGỦ</b>\n"
            f"├ Khung giờ: <b>{night.hm(start)} → {night.hm(end)}</b>\n"
            f"├ Kiểu: <b>{'khoá chat' if c.get('lock') else 'chỉ thông báo'}</b>\n"
            f"└ {night.until_text(start, end)}\n\n"
            + sleep_text(chat.id, title),
            parse_mode=ParseMode.HTML, reply_markup=night.panel_kb(chat.id),
        )
        return

    await safe_edit(msg, sleep_text(chat.id, title), parse_mode=ParseMode.HTML,
                    reply_markup=night.panel_kb(chat.id))


async def cb_sleep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    act = (q.data or "")[3:]
    chat = update.effective_chat
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await safe_answer(q, "Chỉ dùng trong nhóm.", alert=True)
        return
    if not await _is_chat_admin(update, context):
        await safe_answer(q, "Chỉ quản trị viên nhóm dùng được.", alert=True)
        return
    c = night.cfg(chat.id)
    title = getattr(chat, "title", "") or ""

    if act == "toggle":
        c["on"] = 0 if c.get("on") else 1
        night.save()
        if not c["on"] and c.get("asleep"):
            await night.open_chat(context.bot, chat.id, title)
        await safe_answer(q, "Đã BẬT" if c["on"] else "Đã TẮT")
    elif act == "lock":
        c["lock"] = 0 if c.get("lock") else 1
        night.save()
        await safe_answer(q, "Khoá chat: BẬT" if c["lock"] else "Khoá chat: TẮT")
    elif act == "wake":
        ok = await night.open_chat(context.bot, chat.id, title)
        await safe_answer(q, "Đã mở lại nhóm ☀️" if ok else "Không mở được (thiếu quyền?)", alert=True)
    elif act == "sleepnow":
        ok = await night.close_chat(context.bot, chat.id, title)
        await safe_answer(q, "Đã khoá 🌙" if ok else "Không khoá được (thiếu quyền?)", alert=True)
    elif act == "off":
        c["on"] = 0
        night.save()
        if c.get("asleep"):
            await night.open_chat(context.bot, chat.id, title)
        await safe_answer(q, "Đã tắt giờ ngủ")
    else:
        await safe_answer(q)

    await safe_edit(q.message, sleep_text(chat.id, title), parse_mode=ParseMode.HTML,
                    reply_markup=night.panel_kb(chat.id))


def build_app(base_url: str | None = None) -> Application:
    """Dựng Application + đăng ký toàn bộ handler (tách ra để test được装配 thật).

    `base_url`: chỉ dùng khi test — trỏ Bot API về server giả để bắt đúng request.
    """
    builder = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .concurrent_updates(64)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
    )
    if base_url:
        builder = builder.base_url(base_url).base_file_url(base_url)
    app = builder.build()

    def reg(names, fn):
        """Đăng ký lệnh + ghi tên vào BOT_COMMANDS (để gif chỉ chạy cho lệnh của bot này)."""
        names = [names] if isinstance(names, str) else list(names)
        BOT_COMMANDS.update(n.lower() for n in names)
        app.add_handler(CommandHandler(names, fn))

    # ---------------- lệnh chung
    reg(["start", "menu"], cmd_start)
    reg(["help", "trogiup"], cmd_help)
    reg("status", cmd_status)
    reg("ping", cmd_ping)
    reg("console", cmd_console)
    reg("cleanup", cmd_cleanup)
    reg("donate", cmd_donate)
    reg("sing", cmd_sing)
    reg("dl", cmd_dl)
    reg(["mp3", "nhac", "music"], cmd_mp3)
    reg(["ytb", "youtube", "yt"], cmd_ytb)
    reg(["tiktok", "tt"], cmd_dl)
    reg(["ig", "insta", "instagram"], cmd_ig)
    reg(["cookie", "cookies"], cmd_cookie)
    reg("getinfo", cmd_getinfo)
    reg("tts", cmd_tts)
    reg("voice", cmd_voice)
    reg(["capwed", "capweb", "screenshot"], cmd_capwed)
    reg(["banner", "taobanner"], cmd_banner)
    reg("cover", cmd_cover)
    reg(["fb", "facebook"], cmd_fb)
    reg(["thoitiet", "weather"], cmd_weather)
    reg(["dich", "translate"], cmd_translate)
    reg("short", cmd_short)
    reg(["getid", "id"], cmd_getid)
    reg("sticker", cmd_sticker)
    reg(["js", "getemoji", "emojiuid"], cmd_js)
    reg(["anh18", "18", "anhsex"], cmd_anh18)
    reg("vdgai", cmd_vdgai)
    reg(["mong", "girl", "bu", "gainhat", "japcosplay", "tw", "anime", "sagiri"], cmd_anh18)
    reg("war", cmd_war)
    reg("spam", cmd_spam)
    reg("stop", cmd_stop)
    reg(["qc", "quangcao", "ads"], qc.cmd_qc)
    reg("qcstop", qc.cmd_qcstop)
    reg("qcapi", qc.cmd_qcapi)
    reg(["baotri", "maintenance"], cmd_baotri)

    # ---------------- giờ ngủ · Stars
    reg(["sleep", "gingu"], cmd_sleep)
    reg(["stars", "muavip"], stars.cmd_stars)
    reg(["vip"], stars.cmd_vip)

    # ---------------- panel admin
    reg(["panel", "admin"], cmd_panel)
    reg("addadmin", cmd_addadmin)
    reg("deladmin", cmd_deladmin)
    reg("gif", cmd_gif)
    reg("captcha", cmd_captcha)
    reg("react", cmd_react)

    # ---------------- callback
    app.add_handler(CallbackQueryHandler(cb_captcha, pattern=r"^cp_"))
    app.add_handler(CallbackQueryHandler(cb_banner, pattern=r"^bn_"))
    app.add_handler(CallbackQueryHandler(panel_router, pattern=r"^p_"))
    app.add_handler(CallbackQueryHandler(admin_router, pattern=r"^a_"))
    app.add_handler(CallbackQueryHandler(cb_capture, pattern=r"^cw:"))
    app.add_handler(CallbackQueryHandler(cb_sing_download, pattern=r"^sg_"))
    app.add_handler(CallbackQueryHandler(cb_tiktok_button, pattern=r"^tk:"))
    app.add_handler(CallbackQueryHandler(cb_voice, pattern=r"^vc:"))
    app.add_handler(CallbackQueryHandler(cb_id_pick, pattern=r"^idp:"))
    app.add_handler(CallbackQueryHandler(cb_anh18, pattern=r"^ah:"))
    app.add_handler(CallbackQueryHandler(qc.on_callback, pattern=r"^qc:"))
    app.add_handler(CallbackQueryHandler(donate.cb_donate, pattern=r"^dn:"))
    app.add_handler(CallbackQueryHandler(cb_sleep, pattern=r"^sl:"))
    app.add_handler(CallbackQueryHandler(stars.cb_stars, pattern=r"^st:"))
    app.add_handler(CallbackQueryHandler(dich.cb_translate, pattern=r"^tr:"))

    # ---------------- Telegram Stars (thanh toán trong app)
    app.add_handler(PreCheckoutQueryHandler(stars.precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, stars.on_paid))

    # ---------------- message
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, on_photo))
    app.add_handler(MessageReactionHandler(on_reaction))

    # ---------------- tự thả reaction theo nội dung (chạy SAU handler chính)
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & ~filters.UpdateType.EDITED_MESSAGE,
            cb_auto_react,
        ),
        group=1,
    )

    # ---------------- chặn người chưa xác minh (chạy TRƯỚC TẤT CẢ)
    app.add_handler(TypeHandler(Update, track_users), group=-4)
    app.add_handler(TypeHandler(Update, cb_gate_baotri), group=-3)
    app.add_handler(TypeHandler(Update, cb_gate_captcha), group=-2)

    # ---------------- gif TRƯỚC câu trả lời (group -1 chạy trước handler chính)
    app.add_handler(TypeHandler(Update, cb_pre_gif), group=-1)

    app.add_error_handler(on_error)
    return app


def _startup_panel() -> None:
    """Khối thông tin khởi động (in 1 lần, đẹp, có màu)."""
    import shutil as _sh
    import platform as _pf

    dc = console.PALETTE
    ff = "có" if _sh.which("ffmpeg") else "thiếu"
    ff_s = console.style(ff, dc["green"] if ff == "có" else dc["red"], bold=True)
    os_name = {"nt": "Windows", "posix": "macOS" if _pf.system() == "Darwin" else "Linux"}.get(
        os.name, os.name)
    rows = [
        ("Bot", console.style("KHOA AN TOOLS", dc["amber"], bold=True)),
        ("Phiên bản", console.style(VERSION, dc["cyan"], bold=True)),
        ("Nền tảng", f"{os_name} · Python {_pf.python_version()}"),
        ("ffmpeg", ff_s),
        ("Dữ liệu", console.style(str(config.DATA_DIR), dc["slate"])),
        ("Tệp tạm", console.style(str(config.TMP_DIR), dc["slate"])),
        ("Số lệnh", str(len(BOT_COMMANDS))),
        ("Admin", ", ".join(str(x) for x in (config.ADMIN_IDS or [])) or "—"),
    ]
    print(console.box("THÔNG TIN", rows, accent=dc["cyan"]))
    sites = [("TikTok", dc["pink"]), ("Instagram", dc["violet"]),
             ("Facebook", dc["blue"]), ("YouTube", dc["red"]),
             ("Music", dc["cyan"]), ("TTS", dc["green"]), ("Stars", dc["amber"])]
    line = "  ".join(console.style(n, c, bold=True) for n, c in sites)
    print(" " + console.style("▸", dc["violet"]) + " " + console.style("Đang bật:", dc["slate"])
          + " " + line + "\n")
    quick = [
        ("/menu", "bảng điều khiển", dc["violet"]),
        ("/help", "toàn bộ lệnh", dc["violet"]),
        ("/dl", "video TikTok", dc["pink"]),
        ("/ig", "Instagram reel/post", dc["violet"]),
        ("/fb", "Facebook ID + avatar", dc["blue"]),
        ("/ytb", "YouTube video/mp3", dc["red"]),
        ("/sing", "tìm + tải MP3", dc["cyan"]),
        ("/tts", "chữ thành giọng nói", dc["green"]),
        ("/cover", "đổi tên + ảnh bìa", dc["amber"]),
        ("/cookie", "nạp cookie IG/FB", dc["slate"]),
    ]
    print(console.cmdsheet("LỆNH NHANH", quick) + "\n")


def main():
    _bn = console.banner(VERSION,
                         "TikTok · Instagram · Facebook · YouTube · Music · TTS · 18+",
                         stats_commands=len(BOT_COMMANDS), stream=True)
    if _bn:                      # console không phải terminal thật (Railway/log) -> in một lần
        print(_bn)
    with console.spin("Nạp toàn bộ lệnh + handler", console.PALETTE["cyan"]):
        app = build_app()
    try:
        _startup_panel()
    except Exception as e:  # noqa: BLE001
        console.warn(f"Không in được panel khởi động: {e}")
    print(console.status_bar(f"khoaan {VERSION} · chạy 24/7 · Ctrl+C để dừng",
                             time.strftime("%H:%M:%S")))
    console.ok("Bot đã sẵn sàng nhận lệnh")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

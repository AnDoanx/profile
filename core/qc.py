#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RẢI QUẢNG CÁO TELEGRAM bằng tài khoản thật (userbot Telethon).

Luồng:
    /qc → Đăng nhập SĐT → Telegram gửi mã → nhắn mã cho bot → bot tự nhập
        → (nếu có 2FA thì nhắn mật khẩu) → lấy danh sách nhóm trong acc
        → chọn nhóm → nhập nội dung → Rải, có log theo SĐT.

Dữ liệu: data/qc.json (phiên đăng nhập + nhóm đã chọn + nội dung + log).
Không cài telethon thì bot vẫn chạy bình thường, chỉ riêng /qc báo thiếu thư viện.
"""

import asyncio
import logging
import random
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import config
from core import admins, settings
from core.store import JsonStore

log = logging.getLogger("khoaan.qc")

VN = timezone(timedelta(hours=7))
STORE = JsonStore("qc.json", {"acc": {}, "targets": {}, "content": {}, "stats": {}, "log": []})
PHOTO_DIR = config.DATA_DIR / "qc_media"

STATE: dict[int, dict] = {}     # user_id -> bước đang chờ (phone / code / 2fa / content)
CLIENTS: dict[int, object] = {}  # user_id -> TelegramClient đang mở
TASKS: dict[int, asyncio.Task] = {}
STOP: set[int] = set()
PAGES: dict[int, int] = {}
PAGE_SIZE = 8
LOG_CAP = 400


# ================================================================= tiện ích
def _e(text) -> str:
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _t(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts), VN).strftime("%d/%m %H:%M")
    except (TypeError, ValueError, OSError):
        return "—"


def allowed(uid: int) -> bool:
    """Ai được dùng /qc: admin bot, hoặc mọi người nếu QC_ALLOW_ALL=1."""
    return admins.is_admin(uid) or bool(getattr(config, "QC_ALLOW_ALL", False))


HASH_RE = re.compile(r"[0-9a-fA-F]{32}")

# api_id của các app công khai (Telegram Desktop / Android / X…). Telegram chặn
# dùng để đăng nhập → đúng lỗi "api_id/api_hash combination is invalid".
PUBLIC_IDS = {1, 4, 6, 2040, 2496, 10840, 17349, 21724, 94575}

HINT_CREDS = (
    "1️⃣ Mở <a href=\"https://my.telegram.org\">my.telegram.org</a> → đăng nhập bằng "
    "<b>chính SĐT của acc sẽ dùng để rải</b>\n"
    "2️⃣ <b>API development tools</b> → điền tên gì cũng được (vd <code>khoaan-tools</code>) "
    "→ bấm <b>Create application</b>\n"
    "3️⃣ Copy đúng 2 dòng vừa tạo:\n"
    "   • <b>App api_id</b> = dãy số, vd <code>12345678</code>\n"
    "   • <b>App api_hash</b> = đúng <b>32 ký tự</b>, "
    "vd <code>0123456789abcdef0123456789abcdef</code>\n"
    "4️⃣ Gõ: <code>/qcapi 12345678 0123456789abcdef0123456789abcdef</code>\n\n"
    "⛔ <b>Đừng</b> dùng api_id “lấy trên mạng” hay của bot khác — cặp công khai đã bị chặn, "
    "dùng là ra đúng lỗi này.\n"
    "⛔ Không dán ngược (api_id là số, api_hash là 32 ký tự), không thừa dấu cách / ngoặc kép."
)


def _clean_hash(raw: str) -> str:
    """api_hash: chỉ giữ 0-9a-f, bỏ space, gạch, ngoặc kép…"""
    return re.sub(r"[^0-9a-fA-F]", "", str(raw or "")).lower()


def api_creds(uid: int | None = None) -> tuple[int, str]:
    """Ưu tiên: creds riêng của user → creds chung đã lưu → biến môi trường."""
    if uid:
        api_id, api_hash = _read_creds(f"qc.api_id.{uid}", f"qc.api_hash.{uid}")
        if api_id and api_hash:
            return api_id, api_hash
    api_id, api_hash = _read_creds("qc.api_id", "qc.api_hash")
    if api_id and api_hash:
        return api_id, api_hash
    try:
        env_id = int(getattr(config, "TG_API_ID", 0) or 0)
    except (TypeError, ValueError):
        env_id = 0
    return env_id, _clean_hash(getattr(config, "TG_API_HASH", ""))


def _read_creds(id_key: str, hash_key: str) -> tuple[int, str]:
    try:
        api_id = int(str(settings.get(id_key, "0") or "0").strip() or 0)
    except (TypeError, ValueError):
        api_id = 0
    api_hash = _clean_hash(settings.get(hash_key, ""))
    return api_id, api_hash


def parse_creds(raw: str) -> tuple[int, str, str]:
    """Bóc api_id/api_hash từ chuỗi dán kiểu gì cũng được.

    Nhận: ``12345678 abc…``, ``api_id: 12345678`` + ``api_hash: abc…``,
    ``12345678:abc…``, hoặc cả khối copy từ my.telegram.org.
    Trả ``(api_id, api_hash, lỗi)`` — lỗi rỗng nghĩa là đọc được.
    """
    text = str(raw or "").replace("：", ":").replace("＝", "=")
    text = re.sub(r"[\u200b\u00a0]", " ", text)
    text = text.replace("“", " ").replace("”", " ").replace('"', " ").replace("'", " ")

    api_id, api_hash = 0, ""
    m_id = re.search(r"api[_\s-]*id\D{0,12}(\d{3,12})", text, re.I)
    if m_id:
        api_id = int(m_id.group(1))
    m_h = re.search(r"api[_\s-]*hash\D{0,12}([0-9a-fA-F]{32})", text, re.I)
    if m_h:
        api_hash = m_h.group(1).lower()
    if not api_hash:
        m2 = HASH_RE.search(text)
        if m2:
            api_hash = m2.group(0).lower()
    if not api_id:
        # bỏ phần hash ra khỏi chuỗi rồi mới tìm api_id (hash toàn số cũng có,
        # không bỏ thì dễ bắt nhầm 10 chữ số đầu của hash làm api_id)
        rest = re.sub(re.escape(api_hash), " ", text, flags=re.I) if api_hash else text
        for tok in re.findall(r"\d{4,12}", rest):
            api_id = int(tok)
            break
    if not api_id or not api_hash:
        return 0, "", ("Không đọc ra <b>api_id</b> / <b>api_hash</b> trong tin bạn gửi.\n"
                       "Cần 1 dãy số (api_id) + 1 chuỗi đúng 32 ký tự (api_hash).")
    bad = format_creds_error(api_id, api_hash)
    if bad:
        return 0, "", bad
    return api_id, api_hash, ""


def format_creds_error(api_id: int, api_hash: str) -> str:
    """Soi lỗi hay gặp trước khi gọi Telegram. Rỗng = hình thức hợp lệ."""
    try:
        aid = int(api_id)
    except (TypeError, ValueError):
        return "api_id phải là dãy số (vd <code>12345678</code>)."
    if aid in PUBLIC_IDS:
        return ("<b>api_id này là của app công khai</b> (Telegram Desktop / Android / X…). "
                "Telegram chặn dùng để đăng nhập → ra đúng lỗi "
                "<i>api_id/api_hash combination is invalid</i>.\n"
                "Phải tạo app <b>riêng của bạn</b> (bên dưới).")
    if not (1000 <= aid <= 99_999_999_999):
        return "api_id không đúng định dạng (phải là số, vd <code>12345678</code>)."
    if len(api_hash) != 32:
        return (f"api_hash phải đúng <b>32 ký tự</b> — bạn đang có <b>{len(api_hash)}</b>. "
                "Thường là dán thiếu, hoặc dán nhầm api_id vào chỗ api_hash.")
    return ""


async def probe_creds(api_id: int, api_hash: str) -> dict:
    """Hỏi thật Telegram xem cặp api_id/api_hash có dùng được không.

    Trả {"ok": bool, "err": str, "hint": str, "dc": int|str}.
    """
    if not api_id or not api_hash:
        return {"ok": False, "err": "Chưa có <b>API_ID / API_HASH</b>.",
                "hint": HINT_CREDS}
    mod = _telethon()
    if not mod:
        return {"ok": False, "err": "Thiếu thư viện <code>telethon</code>.",
                "hint": "Chạy <code>pip install -r requirements.txt</code> rồi restart bot."}
    bad = format_creds_error(api_id, api_hash)
    if bad:
        return {"ok": False, "err": bad, "hint": HINT_CREDS}

    TelegramClient, StringSession, _errors = mod
    client = None
    try:
        from telethon.tl import functions as tl_funcs
        client = TelegramClient(StringSession(), api_id, api_hash,
                                connection_retries=1, request_retries=1, timeout=15)
        await client.connect()
        # ExportLoginToken kiểm tra đúng cặp api_id/api_hash như SendCodeRequest
        # (cùng ra lỗi "api_id/api_hash combination is invalid") nhưng KHÔNG gửi
        # SMS, không tạo phiên → test bao nhiêu lần cũng an toàn.
        res = await client(tl_funcs.auth.ExportLoginTokenRequest(
            api_id=api_id, api_hash=api_hash, except_ids=[]))
        dc = getattr(res, "dc_id", 0) or ""
        if not dc:
            try:
                near = await client(tl_funcs.help.GetNearestDcRequest())
                dc = getattr(near, "this_dc", "") or ""
            except Exception:  # noqa: BLE001
                dc = ""
        return {"ok": True, "err": "", "hint": "", "dc": dc}
    except Exception as e:  # noqa: BLE001
        low = str(e).lower()
        log.info("qc probe lỗi: %s", str(e)[:140])
        errs = _telethon()
        errs = errs[2] if errs else None
        if errs and isinstance(e, getattr(errs, "ApiIdPublishedFloodError", ())):
            return {"ok": False,
                    "err": ("<b>api_id này đã bị Telegram chặn đăng nhập</b> "
                            "(api_id bị lộ / dùng công khai)."),
                    "hint": "Tạo application <b>mới</b> ở my.telegram.org rồi nhập lại:\n\n"
                            + HINT_CREDS}
        if "api_id" in low or "api_hash" in low or "api id" in low:
            return {"ok": False,
                    "err": ("<b>Cặp API_ID / API_HASH không hợp lệ</b> — Telegram trả "
                            "<i>api_id/api_hash combination is invalid</i>."),
                    "hint": HINT_CREDS}
        return {"ok": False,
                "err": f"Không hỏi được Telegram: <code>{_e(str(e)[:110])}</code>",
                "hint": "Do mạng/VPN của server. Thử lại sau ít phút; nếu bot vẫn chạy "
                        "bình thường thì đổi DNS/exit node rồi thử lại."}
    finally:
        if client is not None:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass


def creds_status(uid: int) -> tuple[bool, str]:
    """(đã có creds chưa, mô tả ngắn) để hiện trong panel."""
    api_id, api_hash = api_creds(uid)
    if not api_id or not api_hash:
        return False, "chưa có"
    bad = format_creds_error(api_id, api_hash)
    if bad:
        return False, "có nhưng sai định dạng"
    return True, f"<code>{api_id}</code> · hash <code>{api_hash[:4]}…{api_hash[-4:]}</code>"


def _telethon():
    """Import muộn — thiếu telethon thì bot vẫn chạy, chỉ /qc báo lỗi."""
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        from telethon import errors
        return TelegramClient, StringSession, errors
    except Exception:  # noqa: BLE001
        return None


async def _say(msg, text: str, kb=None):
    if msg is None:
        return None
    try:
        return await msg.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True
        )
    except Exception:  # noqa: BLE001
        try:
            return await msg.reply_text(re.sub(r"<[^>]+>", "", text), reply_markup=kb)
        except Exception:  # noqa: BLE001
            return None


async def _edit(msg, text: str, kb=None):
    if msg is None:
        return None
    try:
        return await msg.edit_text(
            text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True
        )
    except Exception:  # noqa: BLE001
        return await _say(msg, text, kb)


async def _answer(q, text: str | None = None, alert: bool = False) -> None:
    try:
        await q.answer(text or "", show_alert=alert)
    except Exception:  # noqa: BLE001
        pass


async def _notify(bot, chat_id: int, text: str):
    try:
        return await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML,
                                      disable_web_page_preview=True)
    except Exception as e:  # noqa: BLE001
        log.info("qc notify lỗi: %s", str(e)[:80])
        return None


# ================================================================= tài khoản
def acc(uid: int) -> dict:
    return (STORE.data.setdefault("acc", {}) or {}).get(str(uid)) or {}


def phone_of(uid: int) -> str:
    return str(acc(uid).get("phone") or "")


def _save_acc(uid: int, **kw) -> None:
    table = STORE.data.setdefault("acc", {})
    row = table.setdefault(str(uid), {})
    row.update(kw)
    row["uid"] = uid
    STORE.save()


async def _close_stale(uid: int) -> None:
    """Đóng client cũ (nếu có) trước khi mở phiên mới — tránh 2 phiên cùng lúc."""
    old = CLIENTS.pop(uid, None)
    st = STATE.get(uid) or {}
    old2 = st.get("client")
    for c in (old, old2):
        if c is None:
            continue
        try:
            await c.disconnect()
        except Exception:  # noqa: BLE001
            pass


async def get_client(uid: int):
    """Trả (client, lỗi). Client đã connect + đã đăng nhập."""
    mod = _telethon()
    if not mod:
        return None, ("Thiếu thư viện <code>telethon</code>.\n"
                      "Chạy <code>pip install telethon</code> rồi khởi động lại bot.")
    api_id, api_hash = api_creds(uid)
    if not api_id or not api_hash:
        return None, ("Chưa có <b>API_ID / API_HASH</b>.\n"
                      "Lấy ở my.telegram.org → API development tools, "
                      "rồi gõ <code>/qcapi API_ID API_HASH</code>.")

    client = CLIENTS.get(uid)
    if client is not None:
        try:
            if not client.is_connected():
                await client.connect()
            if await client.is_user_authorized():
                return client, ""
        except Exception:  # noqa: BLE001
            pass
        CLIENTS.pop(uid, None)

    sess = (acc(uid).get("session") or "").strip()
    if not sess:
        return None, "Chưa đăng nhập. Bấm <b>🔐 Đăng nhập SĐT</b>."

    TelegramClient, StringSession, _err = mod
    try:
        client = TelegramClient(StringSession(sess), api_id, api_hash, lang_code="vi")
        await client.connect()
        if not await client.is_user_authorized():
            return None, "Phiên đăng nhập đã hết hạn, đăng nhập lại nhé."
    except Exception as e:  # noqa: BLE001
        log.info("qc connect lỗi: %s", str(e)[:120])
        return None, f"Không kết nối được Telegram: <code>{_e(str(e)[:90])}</code>"
    CLIENTS[uid] = client
    return client, ""


def _norm_phone(raw: str) -> str:
    p = re.sub(r"[^\d+]", "", raw or "")
    if p.startswith("00"):
        p = "+" + p[2:]
    if p.startswith("0"):
        p = "+84" + p[1:]
    elif not p.startswith("+"):
        p = "+" + p
    return p


async def start_login(uid: int, raw_phone: str) -> str:
    mod = _telethon()
    if not mod:
        return ("Thiếu thư viện <code>telethon</code>.\n"
                "Chạy <code>pip install -r requirements.txt</code> rồi khởi động lại bot.")
    api_id, api_hash = api_creds(uid)
    if not api_id or not api_hash:
        return "❌ <b>Chưa có API_ID / API_HASH</b>\n\n" + HINT_CREDS
    phone = _norm_phone(raw_phone)
    if len(phone) < 8:
        return "SĐT không hợp lệ. Ví dụ: <code>0912345678</code> hoặc <code>+84912345678</code>."

    # Kiểm tra creds TRƯỚC khi xin mã: sai creds thì Telegram không bao giờ gửi mã,
    # cứ thử send_code chỉ tốn lượt flood-wait và ra lỗi khó hiểu.
    chk = await probe_creds(api_id, api_hash)
    if not chk.get("ok"):
        return ("❌ <b>Chưa gửi mã được — lỗi nằm ở API_ID / API_HASH</b>\n\n"
                + chk.get("err", "") + "\n\n" + (chk.get("hint") or ""))

    TelegramClient, StringSession, errors = mod
    await _close_stale(uid)
    client = None
    try:
        client = TelegramClient(StringSession(), api_id, api_hash,
                                device_model="KhoaAn", system_version="Windows 10",
                                app_version="4.16", lang_code="vi", system_lang_code="vi")
        await client.connect()
        sent = await client.send_code_request(phone)
    except Exception as e:  # noqa: BLE001
        if client is not None:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        return _send_code_error(e, errors)

    STATE[uid] = {"step": "code", "phone": phone,
                  "hash": getattr(sent, "phone_code_hash", ""),
                  "client": client, "at": time.time()}
    how = getattr(sent, "type", None)
    tail = ""
    if how is not None and type(how).__name__.endswith("App"):
        tail = ("\n<i>Telegram gửi mã vào app Telegram của acc (không phải SMS) — "
                "mở app xem tin từ “Telegram”.</i>")
    elif how is not None and type(how).__name__.endswith("Call"):
        tail = "\n<i>Telegram sẽ gọi điện đọc mã cho bạn.</i>"
    return (f"📨 Đã gửi mã tới <b>{_e(phone)}</b>.\n"
            "Nhắn <b>mã</b> vào đây (chỉ số, vd <code>12345</code>)."
            f"{tail}\n<i>Tin nhắn chứa mã sẽ được xoá ngay sau khi nhập.</i>")


def _send_code_error(e: Exception, errors) -> str:
    """Dịch lỗi sendCodeRequest sang tiếng Việt + cách xử lý."""
    low = str(e).lower()
    seconds = int(getattr(e, "seconds", 0) or 0)
    if errors is not None and isinstance(e, getattr(errors, "ApiIdPublishedFloodError", ())):
        return ("🚫 <b>api_id bị Telegram chặn đăng nhập</b> (api_id bị lộ / dùng công khai).\n"
                "Tạo application <b>mới</b> ở my.telegram.org rồi gõ lại "
                "<code>/qcapi api_id api_hash</code>.")
    if "api_id" in low or "api_hash" in low:
        return ("❌ <b>API_ID / API_HASH không hợp lệ</b>\n"
                "<i>Telegram: api_id/api_hash combination is invalid</i>\n\n" + HINT_CREDS)
    if "flood" in low or "too many" in low or seconds:
        wait = seconds or 60
        return (f"⏳ Telegram bắt chờ <b>{wait}s</b> (~{max(1, wait // 60)} phút) rồi thử lại.\n"
                "<i>Đừng bấm liên tục — càng bấm càng bị khoá lâu.</i>")
    if "phone_number_invalid" in low:
        return ("❌ SĐT không tồn tại / sai định dạng.\n"
                "Nhập lại kèm mã nước: <code>+84912345678</code>.")
    if "phone_number_banned" in low:
        return "🚫 SĐT này bị Telegram chặn. Dùng acc khác."
    if "phone_number_flood" in low:
        return "⏳ SĐT này đã xin mã quá nhiều lần trong hôm nay. Thử lại sau 24h."
    if "phone_number_purchased" in low:
        return "🚫 Số này vừa mua, Telegram tạm chặn đăng nhập. Chờ vài ngày."
    if "send_code_unavailable" in low or "auth_restart" in low:
        return "⚠️ Telegram tạm từ chối gửi mã. Thử lại sau 1–2 phút hoặc dùng 📷 QR."
    if "timeout" in low or "connection" in low or "network" in low:
        return ("🌐 Không kết nối được tới Telegram từ server.\n"
                "Kiểm tra mạng/VPN của VPS rồi thử lại.")
    return (f"Gửi mã không được: <code>{_e(str(e)[:110])}</code>\n\n"
            "Thử 📷 <b>Đăng nhập QR</b> (không cần mã) hoặc kiểm tra lại API_ID/API_HASH.")


async def _finish_login(uid: int, client, phone: str) -> str:
    try:
        me = await client.get_me()
    except Exception:  # noqa: BLE001
        me = None
    if not phone and me is not None:
        phone = str(getattr(me, "phone", "") or "")
        phone = f"+{phone}" if phone and not phone.startswith("+") else phone
    try:
        session_str = client.session.save()
    except Exception:  # noqa: BLE001
        return "Đăng nhập xong nhưng không lưu được phiên. Thử lại giúp mình."
    _save_acc(uid, phone=phone, session=session_str, login_at=int(time.time()))
    STATE.pop(uid, None)
    CLIENTS[uid] = client
    name = ""
    if me is not None:
        name = getattr(me, "username", "") or getattr(me, "first_name", "") or ""
    return (f"✅ Đăng nhập <b>{_e(phone)}</b> xong"
            + (f" (<b>@{_e(name)}</b>)" if name and not name.startswith("+") else "")
            + ".\nBấm <b>📋 Chọn nhóm</b> để lấy danh sách nhóm trong acc.")


async def submit_code(uid: int, raw_code: str) -> str:
    st = STATE.get(uid) or {}
    client = st.get("client")
    if client is None:
        STATE.pop(uid, None)
        return "⌛️ Hết phiên chờ mã. Bấm <b>🔐 Đăng nhập SĐT</b> làm lại giúp mình."
    code = "".join(ch for ch in (raw_code or "") if ch.isdigit())
    if len(code) < 4:
        return "Mã gồm 5 số, gửi lại giúp mình."

    mod = _telethon()
    if not mod:
        return "Thiếu thư viện <code>telethon</code>."
    _c, _s, errors = mod
    try:
        await client.sign_in(phone=st.get("phone"), code=code,
                             phone_code_hash=st.get("hash") or None)
    except errors.SessionPasswordNeededError:
        st["step"] = "2fa"
        STATE[uid] = st
        return ("🔐 Tài khoản có <b>mật khẩu 2 bước</b>.\n"
                "Nhắn mật khẩu vào đây (bot xoá tin ngay sau khi nhập).")
    except errors.PhoneCodeInvalidError:
        return "❌ Mã sai, nhập lại giúp mình."
    except errors.PhoneCodeExpiredError:
        STATE.pop(uid, None)
        return "⌛️ Mã đã hết hạn. Bấm <b>🔐 Đăng nhập SĐT</b> để gửi mã mới."
    except errors.FloodWaitError as e:
        return f"⏳ Telegram bắt chờ <b>{int(getattr(e, 'seconds', 60) or 60)}s</b>."
    except Exception as e:  # noqa: BLE001
        log.info("qc sign_in lỗi: %s", str(e)[:120])
        return f"Đăng nhập lỗi: <code>{_e(str(e)[:90])}</code>"
    return await _finish_login(uid, client, st.get("phone") or "")


async def submit_password(uid: int, password: str) -> str:
    st = STATE.get(uid) or {}
    client = st.get("client")
    if client is None:
        STATE.pop(uid, None)
        return "⌛️ Hết phiên. Bấm <b>🔐 Đăng nhập SĐT</b> làm lại."
    mod = _telethon()
    if not mod:
        return "Thiếu thư viện <code>telethon</code>."
    _c, _s, errors = mod
    try:
        await client.sign_in(password=password)
    except errors.PasswordHashInvalidError:
        return "❌ Mật khẩu 2FA sai, nhập lại."
    except errors.FloodWaitError as e:
        return f"⏳ Telegram bắt chờ <b>{int(getattr(e, 'seconds', 60) or 60)}s</b>."
    except Exception as e:  # noqa: BLE001
        log.info("qc 2fa lỗi: %s", str(e)[:120])
        return f"Lỗi 2FA: <code>{_e(str(e)[:90])}</code>"
    return await _finish_login(uid, client, st.get("phone") or "")


# ------------------------------------------------------------ đăng nhập QR
QR_TASKS: dict[int, asyncio.Task] = {}
QR_KB = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Tạo QR mới", callback_data="qc:qr")]])


def _qr_png(url: str, path: Path) -> bool:
    """Vẽ QR ra PNG. Không có thư viện qrcode thì trả False (dùng link)."""
    try:
        import qrcode  # type: ignore
        img = qrcode.make(url)
        img.save(str(path))
        return True
    except Exception:  # noqa: BLE001
        return False


async def _qr_show(bot, chat_id: int, uid: int, qr) -> None:
    url = getattr(qr, "url", "") or ""
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    path = PHOTO_DIR / f"qr_{uid}.png"
    cap = ("📷 <b>ĐĂNG NHẬP BẰNG QR</b>\n"
           "Mở Telegram trên điện thoại → <b>Cài đặt → Thiết bị → Quét mã QR</b> "
           "→ quét ảnh này.\n"
           "<i>QR sống ~30 giây, hết hạn bot tự gửi ảnh mới.</i>")
    if _qr_png(url, path):
        try:
            with open(path, "rb") as f:
                await bot.send_photo(chat_id, photo=f, caption=cap,
                                     parse_mode=ParseMode.HTML, reply_markup=QR_KB)
            return
        except Exception as e:  # noqa: BLE001
            log.info("qc gửi ảnh QR lỗi: %s", str(e)[:100])
    await _notify(bot, chat_id,
                  "📷 <b>ĐĂNG NHẬP QR</b>\nMở link này trên điện thoại đã đăng nhập Telegram:\n"
                  f"<code>{_e(url)}</code>\n\n<i>Muốn bot gửi ảnh QR: "
                  "pip install \"qrcode[pil]\" rồi khởi động lại.</i>")


async def _qr_watch(uid: int, client, qr, bot, chat_id: int) -> None:
    mod = _telethon()
    errors = mod[2] if mod else None
    while True:
        try:
            await qr.wait(30)
        except asyncio.TimeoutError:
            try:
                await qr.recreate()
            except Exception:  # noqa: BLE001
                await _notify(bot, chat_id,
                              "⌛️ QR hết hạn. Vào /qc → 📷 Đăng nhập QR để tạo lại.")
                return
            await _qr_show(bot, chat_id, uid, qr)
            continue
        except Exception as e:  # noqa: BLE001
            if errors is not None and isinstance(e, errors.SessionPasswordNeededError):
                STATE[uid] = {"step": "2fa", "client": client, "at": time.time()}
                await _notify(bot, chat_id,
                              "🔐 Acc có <b>mật khẩu 2 bước</b>.\n"
                              "Nhắn mật khẩu vào đây (bot xoá tin ngay sau khi nhập).")
                return
            log.info("qc qr wait lỗi: %s", str(e)[:120])
            await _notify(bot, chat_id,
                          f"❌ QR lỗi: <code>{_e(str(e)[:100])}</code> — thử lại /qc → 📷.")
            return
        break
    await _notify(bot, chat_id, await _finish_login(uid, client, phone_of(uid)))


async def start_qr(uid: int, bot, chat_id: int) -> str:
    """Đăng nhập bằng QR (không cần mã SMS). Trả '' nếu đã gửi QR."""
    mod = _telethon()
    if not mod:
        return "Thiếu thư viện <code>telethon</code>."
    api_id, api_hash = api_creds(uid)
    chk = await probe_creds(api_id, api_hash)
    if not chk.get("ok"):
        return ("❌ <b>Chưa đăng nhập QR được</b>\n\n"
                + chk.get("err", "") + "\n\n" + (chk.get("hint") or ""))
    TelegramClient, StringSession, _errors = mod
    old = QR_TASKS.pop(uid, None)
    if old and not old.done():
        try:
            old.cancel()
        except Exception:  # noqa: BLE001
            pass
    await _close_stale(uid)
    try:
        client = TelegramClient(StringSession(), api_id, api_hash,
                                device_model="KhoaAn", system_version="Windows 10",
                                app_version="4.16", lang_code="vi", system_lang_code="vi")
        await client.connect()
        qr = await client.qr_login()
    except Exception as e:  # noqa: BLE001
        log.info("qc qr_login lỗi: %s", str(e)[:120])
        return f"❌ Không tạo được QR: <code>{_e(str(e)[:110])}</code>"
    await _qr_show(bot, chat_id, uid, qr)
    QR_TASKS[uid] = asyncio.create_task(_qr_watch(uid, client, qr, bot, chat_id))
    return ""


async def logout(uid: int) -> str:
    await _close_stale(uid)
    STATE.pop(uid, None)
    table = STORE.data.setdefault("acc", {})
    table.pop(str(uid), None)
    STORE.save()
    return "🚪 Đã xoá phiên đăng nhập khỏi bot (tài khoản Telegram của bạn không bị ảnh hưởng)."


# ================================================================= nhóm
def targets(uid: int) -> list[dict]:
    rows = (STORE.data.setdefault("targets", {}) or {}).get(str(uid))
    return [r for r in rows if isinstance(r, dict) and "id" in r] if isinstance(rows, list) else []


def _save_targets(uid: int, rows: list[dict]) -> None:
    STORE.data.setdefault("targets", {})[str(uid)] = rows
    STORE.save()


def toggle(uid: int, chat_id: int) -> bool:
    rows = targets(uid)
    state = False
    for r in rows:
        try:
            if int(r["id"]) == int(chat_id):
                r["on"] = not bool(r.get("on"))
                state = bool(r["on"])
        except (KeyError, TypeError, ValueError):
            continue
    _save_targets(uid, rows)
    return state


def set_all(uid: int, on: bool) -> None:
    rows = targets(uid)
    for r in rows:
        r["on"] = bool(on)
    _save_targets(uid, rows)


async def fetch_groups(uid: int) -> tuple[list[dict], str]:
    client, err = await get_client(uid)
    if client is None:
        return [], err

    items: list[dict] = []
    try:
        async for d in client.iter_dialogs():
            ent = getattr(d, "entity", None)
            is_group = bool(getattr(d, "is_group", False))
            mega = bool(getattr(ent, "megagroup", False))
            if not (is_group or mega):
                continue
            if getattr(ent, "left", False) or getattr(ent, "deactivated", False):
                continue
            if getattr(ent, "kicked", False):
                continue
            items.append({
                "id": int(d.id),
                "title": (d.name or "Nhóm")[:90],
                "members": int(getattr(ent, "participants_count", 0) or 0),
                "on": False,
            })
    except Exception as e:  # noqa: BLE001
        log.info("qc iter_dialogs lỗi: %s", str(e)[:120])
        return [], f"Không lấy được danh sách nhóm: <code>{_e(str(e)[:90])}</code>"

    items.sort(key=lambda x: (-int(x.get("members") or 0), x.get("title") or ""))
    prev = {}
    for r in targets(uid):
        try:
            prev[int(r["id"])] = bool(r.get("on"))
        except (KeyError, TypeError, ValueError):
            continue
    for it in items:
        it["on"] = prev.get(int(it["id"]), False)
    _save_targets(uid, items)
    return items, ""


# ================================================================= nội dung
def get_content(uid: int) -> dict:
    row = (STORE.data.setdefault("content", {}) or {}).get(str(uid))
    return row if isinstance(row, dict) else {}


def set_content(uid: int, text: str = "", photo: str = "") -> None:
    table = STORE.data.setdefault("content", {})
    row = table.setdefault(str(uid), {})
    if text or text == "":
        row["text"] = text
    if photo:
        row["photo"] = photo
    row["at"] = int(time.time())
    STORE.save()


# ================================================================= log
def _log_add(uid: int, phone: str, chat: str, ok: bool, err: str = "") -> None:
    logs = STORE.data.setdefault("log", [])
    if not isinstance(logs, list):
        logs = []
        STORE.data["log"] = logs
    logs.append({"t": int(time.time()), "uid": uid, "phone": phone,
                 "chat": chat[:70], "ok": bool(ok), "err": err[:120]})
    if len(logs) > LOG_CAP:
        del logs[:-LOG_CAP]
    STORE.save()


def _bump(phone: str, ok: int, fail: int) -> None:
    table = STORE.data.setdefault("stats", {})
    row = table.setdefault(phone or "—", {"sent": 0, "fail": 0, "runs": 0, "last": 0})
    row["sent"] = int(row.get("sent") or 0) + ok
    row["fail"] = int(row.get("fail") or 0) + fail
    row["runs"] = int(row.get("runs") or 0) + 1
    row["last"] = int(time.time())
    STORE.save()


def stats_text(uid: int) -> str:
    phone = phone_of(uid)
    rec = acc(uid)
    row = (STORE.data.get("stats") or {}).get(phone or "—") or {}
    on = sum(1 for t in targets(uid) if t.get("on"))
    total = len(targets(uid))
    lines = [
        "📊 <b>LOG RẢI QUẢNG CÁO</b>",
        "━━━━━━━━━━━━━━━",
        f"📱 SĐT: <b>{_e(phone or 'chưa đăng nhập')}</b>",
        f"🔑 Đăng nhập từ: {_t(rec.get('login_at')) if rec.get('login_at') else '—'}",
        f"👥 Nhóm đã chọn: <b>{on}</b>/{total}",
        f"📤 Đã gửi: <b>{int(row.get('sent') or 0)}</b> · "
        f"lỗi: <b>{int(row.get('fail') or 0)}</b> · số lần rải: <b>{int(row.get('runs') or 0)}</b>",
    ]
    if row.get("last"):
        lines.append(f"🕐 Lần cuối: {_t(row.get('last'))}")

    mine = [x for x in (STORE.data.get("log") or []) if isinstance(x, dict) and x.get("uid") == uid]
    mine = mine[-12:]
    if mine:
        lines.append("")
        lines.append("▍<b>12 lượt gần nhất</b>")
        for x in reversed(mine):
            icon = "✅" if x.get("ok") else "❌"
            tail = "" if x.get("ok") else f" · <i>{_e(x.get('err') or 'lỗi')[:40]}</i>"
            lines.append(f"{icon} {_t(x.get('t'))} · {_e(x.get('chat') or '')}{tail}")
    else:
        lines.append("\n<i>Chưa có lượt gửi nào.</i>")
    return "\n".join(lines)


# ================================================================= panel
def is_running(uid: int) -> bool:
    task = TASKS.get(uid)
    return bool(task and not task.done())


def panel_text(uid: int) -> str:
    rec = acc(uid)
    phone = rec.get("phone") or ""
    c = get_content(uid)
    have = []
    if (c.get("text") or "").strip():
        have.append(f"{len(c['text'])} ký tự")
    if c.get("photo"):
        have.append("1 ảnh")
    on = sum(1 for t in targets(uid) if t.get("on"))
    total = len(targets(uid))
    api_id, api_hash = api_creds(uid)
    have_api, api_desc = creds_status(uid)

    lines = [
        "📣 <b>RẢI QUẢNG CÁO TELEGRAM</b>",
        "<i>Dùng tài khoản thật của bạn để gửi vào các nhóm đã chọn.</i>",
        "━━━━━━━━━━━━━━━",
        f"🔑 API: {('✅ ' + api_desc) if have_api else '⚠️ ' + api_desc}",
        f"👤 Tài khoản: <b>{_e(phone) if phone else 'chưa đăng nhập'}</b>",
        f"👥 Nhóm đã chọn: <b>{on}</b>/{total}",
        f"✍️ Nội dung: <b>{' + '.join(have) or 'chưa có'}</b>",
        f"⚙️ Trạng thái: {'🟢 đang rải' if is_running(uid) else '⚪️ rảnh'}",
    ]
    if not have_api:
        lines.append("")
        lines.append("⚠️ <i>Thiếu/sai API_ID·API_HASH thì Telegram không gửi mã — "
                     "bấm 🔑 Nhập API hoặc gõ</i> <code>/qcapi</code>")
    lines += [
        "━━━━━━━━━━━━━━━",
        "▍<b>Cách chạy</b>",
        "1️⃣ 🔑 Nhập API → 2️⃣ Đăng nhập SĐT <i>(hoặc 📷 QR)</i>",
        "3️⃣ Chọn nhóm → 4️⃣ Nhập nội dung → 🚀 Rải",
        "",
        f"<i>Giãn cách {float(getattr(config, 'QC_DELAY', 8) or 8):.0f}s/nhóm · "
        f"tối đa {int(getattr(config, 'QC_MAX', 200) or 200)} nhóm/lần · "
        "/qcstop để dừng.</i>",
    ]
    return "\n".join(lines)


def panel_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔐 Đăng nhập SĐT", callback_data="qc:login"),
         InlineKeyboardButton("📷 Đăng nhập QR", callback_data="qc:qr")],
        [InlineKeyboardButton("📋 Chọn nhóm", callback_data="qc:groups"),
         InlineKeyboardButton("✍️ Nội dung", callback_data="qc:content")],
        [InlineKeyboardButton("🚀 Rải ngay", callback_data="qc:run"),
         InlineKeyboardButton("⛔ Dừng rải", callback_data="qc:stop")],
        [InlineKeyboardButton("📊 Log / Thống kê", callback_data="qc:stats")],
        [InlineKeyboardButton("🔑 Nhập API_ID / API_HASH", callback_data="qc:api")],
        [InlineKeyboardButton("🔬 Test API", callback_data="qc:apitest"),
         InlineKeyboardButton("🚪 Xoá phiên", callback_data="qc:logout")],
    ])


def groups_kb(uid: int, page: int = 0) -> InlineKeyboardMarkup:
    items = targets(uid)
    pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(int(page), pages - 1))
    PAGES[uid] = page
    chunk = items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    rows: list[list[InlineKeyboardButton]] = []
    for t in chunk:
        mark = "✅" if t.get("on") else "⬜️"
        mem = int(t.get("members") or 0)
        tail = f" · {mem // 1000}k" if mem >= 1000 else (f" · {mem}" if mem else "")
        label = f"{mark} {(t.get('title') or 'Nhóm')[:38]}{tail}"
        rows.append([InlineKeyboardButton(label, callback_data=f"qc:t:{int(t['id'])}")])
    if not chunk:
        rows.append([InlineKeyboardButton("(chưa có nhóm nào — bấm Panel để lấy lại)",
                                          callback_data="qc:back")])
    if pages > 1:
        rows.append([
            InlineKeyboardButton("◀", callback_data=f"qc:p:{page - 1}"),
            InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="qc:noop"),
            InlineKeyboardButton("▶", callback_data=f"qc:p:{page + 1}"),
        ])
    rows.append([
        InlineKeyboardButton("✅ Chọn tất cả", callback_data="qc:all"),
        InlineKeyboardButton("⬜️ Bỏ chọn", callback_data="qc:none"),
    ])
    rows.append([
        InlineKeyboardButton("🚀 Rải ngay", callback_data="qc:run"),
        InlineKeyboardButton("← Panel", callback_data="qc:back"),
    ])
    return InlineKeyboardMarkup(rows)


def groups_text(uid: int) -> str:
    items = targets(uid)
    on = sum(1 for t in items if t.get("on"))
    return (f"👥 <b>NHÓM TRONG TÀI KHOẢN</b>\n"
            f"Đã chọn <b>{on}</b>/{len(items)} nhóm.\n"
            "<i>Bấm tên nhóm để bật/tắt. Chỉ nhóm bạn còn ở trong mới gửi được.</i>")


# ================================================================= rải
def request_stop(uid: int) -> bool:
    if not is_running(uid):
        return False
    STOP.add(uid)
    return True


async def _send_one(client, chat_id: int, text: str, photo: str) -> None:
    if photo:
        await client.send_file(chat_id, photo, caption=(text[:1024] or None))
    else:
        await client.send_message(chat_id, text)


async def run_broadcast(uid: int, bot, chat_id: int) -> None:
    """Gửi nội dung lần lượt vào các nhóm đã chọn. Có nghỉ khi bị flood."""
    phone = phone_of(uid) or str(uid)
    client, err = await get_client(uid)
    if client is None:
        await _notify(bot, chat_id, f"❌ {err}")
        return

    content = get_content(uid)
    text = (content.get("text") or "").strip()
    photo = content.get("photo") or ""
    if photo and not Path(photo).exists():
        photo = ""
    if not text and not photo:
        await _notify(bot, chat_id, "❌ Chưa có nội dung. Bấm <b>✍️ Nội dung</b> để nhập trước.")
        return

    chats = [t for t in targets(uid) if t.get("on")]
    if not chats:
        await _notify(bot, chat_id, "❌ Chưa chọn nhóm nào. Bấm <b>📋 Chọn nhóm</b> trước.")
        return

    max_n = max(1, int(getattr(config, "QC_MAX", 200) or 200))
    chats = chats[:max_n]
    delay = float(getattr(config, "QC_DELAY", 8) or 8)
    total = len(chats)
    STOP.discard(uid)

    notice = await _notify(
        bot, chat_id,
        f"🚀 Bắt đầu rải vào <b>{total}</b> nhóm…\n"
        f"📱 SĐT: <b>{_e(phone)}</b>\n<i>Gõ /qcstop để dừng.</i>",
    )

    mod = _telethon()
    errors = mod[2] if mod else None
    if errors is None:
        await _notify(bot, chat_id, "❌ Thiếu thư viện <code>telethon</code>.")
        return
    ok = fail = 0
    stopped = False

    for i, ch in enumerate(chats, 1):
        if uid in STOP:
            stopped = True
            break
        chat_target = int(ch["id"])
        title = ch.get("title") or str(chat_target)
        good = False
        err_txt = ""
        try:
            await _send_one(client, chat_target, text, photo)
            good = True
        except errors.FloodWaitError as e:  # type: ignore[union-attr]
            wait = min(int(getattr(e, "seconds", 30) or 30), 300)
            await asyncio.sleep(wait)
            try:
                await _send_one(client, chat_target, text, photo)
                good = True
            except Exception as e2:  # noqa: BLE001
                err_txt = str(e2)[:80]
        except Exception as e:  # noqa: BLE001
            err_txt = str(e)[:80]

        if good:
            ok += 1
        else:
            fail += 1
            log.info("qc gửi lỗi (%s): %s", title[:30], err_txt)
        _log_add(uid, phone, title, good, err_txt)

        if notice and (i % 5 == 0 or i == total):
            try:
                await notice.edit_text(
                    f"🚀 <b>{i}/{total}</b> · ✅ {ok} · ❌ {fail}",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:  # noqa: BLE001
                pass

        if i < total:
            await asyncio.sleep(delay + random.uniform(0, 3))

    STOP.discard(uid)
    _bump(phone, ok, fail)
    head = "⛔ Đã dừng theo lệnh" if stopped else "✅ Rải xong"
    await _notify(
        bot, chat_id,
        f"{head}\n"
        f"✅ Thành công: <b>{ok}</b> · ❌ Lỗi: <b>{fail}</b>\n"
        f"📱 SĐT: <b>{_e(phone)}</b>\n"
        "<i>Chi tiết: 📊 Log / Thống kê</i>",
    )


def _start_task(uid: int, bot, chat_id: int) -> bool:
    if is_running(uid):
        return False
    task = asyncio.create_task(run_broadcast(uid, bot, chat_id))
    TASKS[uid] = task

    def _done(_t, u=uid):
        TASKS.pop(u, None)

    task.add_done_callback(_done)
    return True


# ================================================================= lệnh
async def cmd_qc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id if update.effective_user else 0
    if not allowed(uid):
        await _say(update.effective_message, "🔒 Lệnh này dành cho admin bot.")
        return
    await _say(update.effective_message, panel_text(uid), panel_kb(uid))


async def cmd_qcstop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id if update.effective_user else 0
    if not allowed(uid):
        await _say(update.effective_message, "🔒 Lệnh này dành cho admin bot.")
        return
    if request_stop(uid):
        await _say(update.effective_message, "⛔ Đã ra lệnh dừng — đang dừng sau lượt hiện tại.")
    else:
        await _say(update.effective_message, "ℹ️ Hiện không có tiến trình rải nào chạy.")


async def cmd_qcapi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id if update.effective_user else 0
    msg = update.effective_message
    if not allowed(uid):
        await _say(msg, "🔒 Lệnh này dành cho admin bot.")
        return
    args = list(context.args or [])
    scope = "self"
    if args and args[0].lower() in ("global", "chung"):
        if not admins.is_admin(uid):
            await _say(msg, "🔒 Chỉ admin bot mới đặt API dùng chung.")
            return
        scope = "global"
        args = args[1:]

    if not args:
        have, desc = creds_status(uid)
        await _say(msg,
                   "🔑 <b>API TELEGRAM</b>\n"
                   f"Trạng thái: {'✅' if have else '⚠️'} {desc}\n\n"
                   "▍<b>Cách nhập</b>\n"
                   "• Gõ <code>/qcapi api_id api_hash</code>\n"
                   "• Hoặc bấm <b>🔑 Nhập API_ID / API_HASH</b> rồi dán cả khối "
                   "copy từ my.telegram.org\n"
                   "• Admin muốn đặt cho cả bot: <code>/qcapi global …</code>\n\n"
                   f"▍<b>Lấy ở đâu</b>\n{HINT_CREDS}",
                   InlineKeyboardMarkup([
                       [InlineKeyboardButton("🔬 Test ngay", callback_data="qc:apitest")],
                       [InlineKeyboardButton("📷 Đăng nhập QR", callback_data="qc:qr")],
                   ]))
        return

    api_id, api_hash, err = parse_creds(" ".join(args))
    if err:
        await _say(msg, "❌ " + err + "\n\n▍<b>Cách lấy API_ID / API_HASH</b>\n" + HINT_CREDS)
        return

    note = await _say(msg, "🔬 Đang kiểm tra cặp api_id/api_hash với Telegram…")
    chk = await probe_creds(api_id, api_hash)
    if not chk.get("ok"):
        await _edit(note, "❌ <b>Không lưu</b> — Telegram từ chối cặp này:\n\n"
                          + chk.get("err", "") + "\n\n" + (chk.get("hint") or ""))
        return

    if scope == "global":
        settings.set_value("qc.api_id", api_id)
        settings.set_value("qc.api_hash", api_hash)
        where = "dùng chung cho mọi người"
    else:
        settings.set_value(f"qc.api_id.{uid}", api_id)
        settings.set_value(f"qc.api_hash.{uid}", api_hash)
        where = "riêng của bạn"
    await _close_stale(uid)
    await _edit(note,
                f"✅ <b>Lưu + test OK</b> ({where})\n"
                f"• api_id: <code>{api_id}</code>\n"
                f"• api_hash: <code>{api_hash[:6]}…{api_hash[-4:]}</code>\n"
                f"• DC: <code>{chk.get('dc') or '—'}</code>\n\n"
                "Giờ bấm /qc → 🔐 Đăng nhập SĐT (hoặc 📷 QR).",
                panel_kb(uid))


# ================================================================= callback
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q is None:
        return
    uid = q.from_user.id
    if not allowed(uid):
        await _answer(q, "Chỉ admin bot dùng được.", True)
        return
    data = q.data or ""
    action = data[3:] if data.startswith("qc:") else ""
    await _answer(q)
    panel = q.message

    # bấm nút là bỏ trạng thái chờ đang treo (tránh nuốt tin nhắn sau đó)
    STATE.pop(uid, None)

    if action == "noop":
        return
    if action == "back":
        await _edit(panel, panel_text(uid), panel_kb(uid))
        return
    if action == "login":
        STATE[uid] = {"step": "phone", "at": time.time()}
        await _edit(panel,
                    "📱 <b>ĐĂNG NHẬP TELEGRAM</b>\n"
                    "Gửi <b>số điện thoại</b> của tài khoản bạn muốn dùng.\n"
                    "<i>Ví dụ: 0912345678 hoặc +84912345678</i>\n\n"
                    "Telegram sẽ gửi mã về app/ SMS, bạn nhắn mã đó cho bot.",
                    InlineKeyboardMarkup([[InlineKeyboardButton("← Panel", callback_data="qc:back")]]))
        return
    if action == "api":
        STATE[uid] = {"step": "api", "at": time.time()}
        await _edit(panel,
                    "🔑 <b>NHẬP API_ID / API_HASH</b>\n"
                    "Dán thẳng khối copy từ my.telegram.org, hoặc "
                    "<code>api_id</code> và <code>api_hash</code> cách nhau dấu cách.\n\n"
                    f"▍<b>Lấy ở đâu</b>\n{HINT_CREDS}",
                    InlineKeyboardMarkup([[InlineKeyboardButton("← Panel", callback_data="qc:back")]]))
        return
    if action == "qr":
        back = InlineKeyboardMarkup([[InlineKeyboardButton("← Panel", callback_data="qc:back")]])
        await _edit(panel, "📷 Đang tạo QR đăng nhập…", back)
        err = await start_qr(uid, context.bot, panel.chat_id)
        if err:
            await _edit(panel, err, back)
        else:
            await _edit(panel, panel_text(uid), panel_kb(uid))
        return
    if action == "apitest":
        api_id, api_hash = api_creds(uid)
        await _edit(panel, "🔬 Đang test API với Telegram…")
        chk = await probe_creds(api_id, api_hash)
        if chk.get("ok"):
            await _edit(panel,
                        "✅ <b>API dùng được</b>\n"
                        f"• api_id: <code>{api_id}</code>\n"
                        f"• api_hash: <code>{api_hash[:6]}…{api_hash[-4:]}</code>\n"
                        f"• DC: <code>{chk.get('dc') or '—'}</code>\n\n"
                        "Bấm 🔐 Đăng nhập SĐT hoặc 📷 QR.",
                        panel_kb(uid))
        else:
            await _edit(panel,
                        "❌ <b>API không dùng được</b>\n\n"
                        + chk.get("err", "") + "\n\n" + (chk.get("hint") or ""),
                        InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔑 Nhập API", callback_data="qc:api")],
                            [InlineKeyboardButton("← Panel", callback_data="qc:back")],
                        ]))
        return
    if action == "content":
        STATE[uid] = {"step": "content", "at": time.time()}
        c = get_content(uid)
        cur = (c.get("text") or "").strip()
        await _edit(panel,
                    "✍️ <b>NỘI DUNG QUẢNG CÁO</b>\n"
                    "Gửi <b>đoạn chữ</b> bạn muốn rải (hoặc gửi <b>1 ảnh</b> kèm chú thích).\n\n"
                    + (f"<i>Nội dung hiện tại:</i>\n<blockquote>{_e(cur[:300])}</blockquote>"
                       if cur else "<i>Chưa có nội dung.</i>"),
                    InlineKeyboardMarkup([[InlineKeyboardButton("← Panel", callback_data="qc:back")]]))
        return
    if action == "groups":
        await _edit(panel, "⏳ Đang lấy danh sách nhóm trong tài khoản…")
        items, err = await fetch_groups(uid)
        if err:
            await _edit(panel, f"❌ {err}",
                        InlineKeyboardMarkup([[InlineKeyboardButton("← Panel", callback_data="qc:back")]]))
            return
        await _edit(panel, groups_text(uid), groups_kb(uid, PAGES.get(uid, 0)))
        return
    if action.startswith("t:"):
        try:
            chat_id = int(action[2:])
        except ValueError:
            return
        on = toggle(uid, chat_id)
        try:
            await q.answer("✅ Đã chọn" if on else "⬜️ Bỏ chọn")
        except Exception:  # noqa: BLE001
            pass
        await _edit(panel, groups_text(uid), groups_kb(uid, PAGES.get(uid, 0)))
        return
    if action.startswith("p:"):
        try:
            page = int(action[2:])
        except ValueError:
            page = 0
        await _edit(panel, groups_text(uid), groups_kb(uid, page))
        return
    if action == "all":
        set_all(uid, True)
        await _edit(panel, groups_text(uid), groups_kb(uid, PAGES.get(uid, 0)))
        return
    if action == "none":
        set_all(uid, False)
        await _edit(panel, groups_text(uid), groups_kb(uid, PAGES.get(uid, 0)))
        return
    if action == "run":
        if not _start_task(uid, context.bot, panel.chat_id):
            await _answer(q, "Đang rải rồi, chờ xong hoặc /qcstop.", True)
            return
        await _edit(panel, panel_text(uid), panel_kb(uid))
        return
    if action == "stop":
        if request_stop(uid):
            await _answer(q, "⛔ Đang dừng…", True)
        else:
            await _answer(q, "Không có tiến trình nào đang chạy.", True)
        return
    if action == "stats":
        await _edit(panel, stats_text(uid),
                    InlineKeyboardMarkup([[InlineKeyboardButton("← Panel", callback_data="qc:back")]]))
        return
    if action == "logout":
        text = await logout(uid)
        await _edit(panel, text,
                    InlineKeyboardMarkup([[InlineKeyboardButton("← Panel", callback_data="qc:back")]]))
        return
    await _edit(panel, panel_text(uid), panel_kb(uid))


# ================================================================= text/ảnh
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Chặn tin nhắn khi đang chờ SĐT / mã / 2FA / nội dung. True = đã xử lý."""
    msg = update.effective_message
    uid = update.effective_user.id if update.effective_user else 0
    if not msg or not msg.text or not uid:
        return False
    st = STATE.get(uid)
    if not st:
        return False
    if time.time() - float(st.get("at") or 0) > 900:
        STATE.pop(uid, None)
        return False
    text = msg.text.strip()
    if text.startswith("/"):
        return False
    if not allowed(uid):
        return False

    step = st.get("step")
    if step == "api":
        STATE.pop(uid, None)
        api_id, api_hash, err = parse_creds(text)
        if err:
            await _say(msg, "❌ " + err + "\n\n▍<b>Cách lấy API_ID / API_HASH</b>\n" + HINT_CREDS)
            return True
        chk = await probe_creds(api_id, api_hash)
        if not chk.get("ok"):
            await _say(msg, "❌ <b>Không lưu</b> — Telegram từ chối cặp này:\n\n"
                            + chk.get("err", "") + "\n\n" + (chk.get("hint") or ""))
            return True
        settings.set_value(f"qc.api_id.{uid}", api_id)
        settings.set_value(f"qc.api_hash.{uid}", api_hash)
        await _close_stale(uid)
        await _say(msg,
                   "✅ <b>Lưu + test OK</b>\n"
                   f"• api_id: <code>{api_id}</code>\n"
                   f"• api_hash: <code>{api_hash[:6]}…{api_hash[-4:]}</code>\n"
                   f"• DC: <code>{chk.get('dc') or '—'}</code>\n\n"
                   "Bấm 🔐 Đăng nhập SĐT hoặc 📷 Đăng nhập QR.",
                   panel_kb(uid))
        try:  # xoá tin chứa api_hash khỏi lịch sử chat (sau khi đã trả lời)
            await msg.delete()
        except Exception:  # noqa: BLE001
            pass
        return True
    if step == "phone":
        STATE.pop(uid, None)
        reply = await start_login(uid, text)
        await _say(msg, reply)
        return True
    if step == "code":
        reply = await submit_code(uid, text)
        try:
            await msg.delete()
        except Exception:  # noqa: BLE001
            pass
        await _say(msg, reply)
        return True
    if step == "2fa":
        try:
            await msg.delete()
        except Exception:  # noqa: BLE001
            pass
        reply = await submit_password(uid, text)
        await _say(msg, reply)
        return True
    if step == "content":
        STATE.pop(uid, None)
        set_content(uid, text=text)
        await _say(msg,
                   f"✅ Đã lưu nội dung (<b>{len(text)}</b> ký tự).\n"
                   "Bấm 🚀 Rải khi đã chọn nhóm xong.",
                   panel_kb(uid))
        return True
    return False


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Gửi ảnh trong lúc chờ nội dung -> lưu ảnh + chú thích làm nội dung."""
    msg = update.effective_message
    uid = update.effective_user.id if update.effective_user else 0
    st = STATE.get(uid) or {}
    if time.time() - float(st.get("at") or 0) > 900 and st:
        STATE.pop(uid, None)
        st = {}
    if not msg or not uid or st.get("step") != "content" or not allowed(uid):
        return False
    if not msg.photo:
        return False
    STATE.pop(uid, None)
    try:
        PHOTO_DIR.mkdir(parents=True, exist_ok=True)
        path = PHOTO_DIR / f"{uid}.jpg"
        f = await msg.photo[-1].get_file()
        await f.download_to_drive(str(path))
    except Exception as e:  # noqa: BLE001
        log.info("qc tải ảnh lỗi: %s", str(e)[:100])
        await _say(msg, "❌ Không tải được ảnh, gửi lại giúp mình.")
        return True

    cap = (msg.caption or "").strip()
    set_content(uid, text=cap, photo=str(path))
    await _say(msg,
               "✅ Đã lưu <b>1 ảnh</b>" + (f" + chú thích <b>{len(cap)}</b> ký tự" if cap else "")
               + ".\nBấm 🚀 Rải khi đã chọn nhóm xong.",
               panel_kb(uid))
    return True


async def shutdown() -> None:
    """Tắt bot: huỷ tiến trình rải + đóng phiên Telethon."""
    for uid in list(TASKS.keys()):
        STOP.add(uid)
    for uid, task in list(TASKS.items()):
        try:
            task.cancel()
        except Exception:  # noqa: BLE001
            pass
    TASKS.clear()
    for uid, task in list(QR_TASKS.items()):
        try:
            task.cancel()
        except Exception:  # noqa: BLE001
            pass
    QR_TASKS.clear()
    for uid, client in list(CLIENTS.items()):
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass
    CLIENTS.clear()

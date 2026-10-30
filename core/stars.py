#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thanh toán bằng TELEGRAM STARS (tiền trong Telegram, không cần ví ngoài).

Bot bán gói VIP bằng Stars qua invoice currency "XTR" (Telegram tự thu tiền,
tiền vào ví Stars của chủ bot — không cần provider_token, không cần webhook).

Luồng chuẩn của Telegram:
    1. bot gửi invoice (send_invoice, currency="XTR")
    2. người dùng bấm "⭐ Pay"  → Telegram gửi PreCheckoutQuery  → bot answer(ok=True)
    3. Telegram gửi message.successful_payment → bot cộng hạn VIP

Lệnh:
    /stars                → bảng gói + nút mua (icon + màu)
    /vip                  → xem hạn VIP còn lại
    /stars give <uid> <ngày>   (admin bot) — tặng VIP tay
    /stars refund <uid> <charge_id>  (admin bot) — hoàn Stars cho 1 giao dịch
"""

import logging
import time
from datetime import datetime, timedelta, timezone

from telegram import LabeledPrice, Update
from telegram.error import BadRequest, Forbidden, RetryAfter
from telegram.ext import ContextTypes

from core import buttons as B
from core.store import JsonStore
from core.text import esc

log = logging.getLogger("khoaan.stars")

VN = timezone(timedelta(hours=7))
STORE = JsonStore("stars.json", {"users": {}, "payments": []})
CUR = "XTR"          # Telegram Stars

# gói VIP: id, số ngày, giá (Stars)
PACKAGES = [
    {"id": "d3", "days": 3, "stars": 25, "name": "VIP 3 ngày", "icon": "⚡",
     "desc": "Dùng thử: mở khoá toàn bộ hạn mức cao trong 3 ngày."},
    {"id": "d7", "days": 7, "stars": 50, "name": "VIP 7 ngày", "icon": "🌙",
     "desc": "1 tuần: /anh18 20 lượt/ngày, dùng được trong nhóm, không quảng cáo."},
    {"id": "d30", "days": 30, "stars": 150, "name": "VIP 30 ngày", "icon": "💎",
     "desc": "1 tháng đầy đủ: 20 lượt/ngày + ưu tiên xử lý + huy hiệu VIP."},
    {"id": "d365", "days": 365, "stars": 500, "name": "VIP 1 năm", "icon": "👑",
     "desc": "Rẻ nhất tính theo ngày: dùng cả năm, mọi tính năng VIP."},
]

VIP_ANH18_BONUS = 17          # VIP: 3 + 17 = 20 lượt/ngày
BY_ID = {p["id"]: p for p in PACKAGES}


# ---------------------------------------------------------------- store
def _users() -> dict:
    raw = STORE.data.setdefault("users", {})
    if not isinstance(raw, dict):
        raw = {}
        STORE.data["users"] = raw
    return raw


def _payments() -> list:
    raw = STORE.data.setdefault("payments", [])
    if not isinstance(raw, list):
        raw = []
        STORE.data["payments"] = raw
    return raw


def save() -> None:
    STORE.save()


def _rec(uid: int) -> dict:
    u = _users().get(str(int(uid)))
    if not isinstance(u, dict):
        u = {"until": 0, "total": 0, "count": 0}
        _users()[str(int(uid))] = u
    return u


# ---------------------------------------------------------------- VIP
def until(uid: int) -> float:
    return float(_rec(uid).get("until") or 0)


def is_vip(uid: int | None) -> bool:
    return bool(uid) and until(int(uid)) > time.time()


def days_left(uid: int) -> int:
    left = until(uid) - time.time()
    return max(0, int((left + 86399) // 86400)) if left > 0 else 0


def bonus(uid: int) -> int:
    """Số lượt /anh18 cộng thêm mỗi ngày khi là VIP."""
    return VIP_ANH18_BONUS if is_vip(uid) else 0


def total_stars(uid: int) -> int:
    return int(_rec(uid).get("total") or 0)


def gift(uid: int, days: int, note: str = "") -> float:
    """Cộng (hoặc gia hạn) VIP. Trả mốc hết hạn mới."""
    u = _rec(uid)
    base = max(time.time(), float(u.get("until") or 0))
    u["until"] = base + days * 86400
    u["last"] = time.time()
    if note:
        u["note"] = note[:120]
    save()
    return float(u["until"])


def add_payment(uid: int, stars: int, days: int, charge_id: str, payload: str) -> None:
    u = _rec(uid)
    u["total"] = int(u.get("total") or 0) + int(stars)
    u["count"] = int(u.get("count") or 0) + 1
    _payments().append({
        "uid": int(uid), "stars": int(stars), "days": int(days),
        "charge": charge_id, "payload": payload, "ts": time.time(),
    })
    STORE.data["payments"] = _payments()[-200:]
    save()


def until_text(uid: int) -> str:
    ts = until(uid)
    if ts <= time.time():
        return "chưa có VIP"
    return datetime.fromtimestamp(ts, VN).strftime("%H:%M %d/%m/%Y")


# ---------------------------------------------------------------- giao diện
def panel_text(uid: int = 0) -> str:
    lines = [
        "⭐ <b>THANH TOÁN BẰNG TELEGRAM STARS</b>",
        "<i>Trả bằng Stars có sẵn trong Telegram — không cần thẻ, không cần ví ngoài.</i>",
        "───────────────",
    ]
    for p in PACKAGES:
        lines.append(
            f"{p['icon']} <b>{p['name']}</b> — <b>{p['stars']} ⭐</b>\n"
            f"   <i>{esc(p['desc'])}</i>"
        )
    lines += [
        "───────────────",
        "🎁 <b>VIP được gì?</b>",
        f"├ /anh18: <b>20 lượt/ngày</b> (thường 3)",
        "├ Dùng ảnh 18+ ngay trong nhóm",
        "├ Huy hiệu 👑 trong /id",
        "└ Ưu tiên xử lý khi bot đông",
        "───────────────",
    ]
    if is_vip(uid):
        lines.append(f"👑 Bạn đang là <b>VIP</b> · còn <b>{days_left(uid)} ngày</b> "
                     f"(hết {until_text(uid)})")
    else:
        lines.append("👤 Bạn chưa có VIP — bấm gói bên dưới để mua.")
    return "\n".join(lines)


def panel_kb() -> object:
    rows = []
    for p in PACKAGES:                       # 1 gói / 1 hàng: nút to, dễ bấm
        rows.append([B.btn(f"{p['name']} · {p['stars']} ⭐", cb=f"st:buy:{p['id']}",
                           style=B.BLUE, icon=p["icon"])])
    rows.append(B.row(
        B.btn("Trạng thái VIP", cb="st:me", style=B.GREEN, icon="✅"),
        B.btn("Hướng dẫn", cb="st:help", style=B.BLUE, icon="ℹ️"),
    ))
    return B.kb(rows)


def vip_text(uid: int) -> str:
    if is_vip(uid):
        return (
            "👑 <b>VIP ĐANG HOẠT ĐỘNG</b>\n"
            f"├ Còn lại: <b>{days_left(uid)} ngày</b>\n"
            f"├ Hết hạn: <b>{until_text(uid)}</b>\n"
            f"├ Đã nạp: <b>{total_stars(uid)} ⭐</b>\n"
            f"└ /anh18 hôm nay: <b>{3 + bonus(uid)} lượt/ngày</b>"
        )
    return (
        "👤 <b>CHƯA CÓ VIP</b>\n"
        "├ Hạn mức thường: <b>3 lượt/ngày</b> cho /anh18\n"
        f"└ Đã nạp: <b>{total_stars(uid)} ⭐</b>\n\n"
        "Mở <code>/stars</code> để xem các gói."
    )


# ---------------------------------------------------------------- invoice
async def send_package(bot, chat_id: int, uid: int, pid: str) -> bool:
    """Gửi invoice Stars cho 1 gói."""
    p = BY_ID.get(pid)
    if not p:
        return False
    payload = f"vip:{p['id']}:{int(uid)}:{p['stars']}"
    try:
        await bot.send_invoice(
            chat_id=chat_id,
            title=f"{p['icon']} {p['name']}",
            description=p["desc"],
            payload=payload,
            provider_token="",              # XTR: Telegram thu hộ, không cần provider
            currency=CUR,
            prices=[LabeledPrice(label=p["name"], amount=int(p["stars"]))],
            start_parameter=f"vip-{p['id']}",
            reply_markup=B.kb([B.row(B.btn(f"Mua · {p['stars']} ⭐", pay=True,
                                          style=B.GREEN))]),
        )
        return True
    except (BadRequest, Forbidden) as e:
        log.info("send_invoice lỗi: %s", str(e)[:140])
        return False
    except RetryAfter as e:
        log.info("send_invoice flood: %s", e.retry_after)
        return False


def parse_payload(payload: str) -> dict | None:
    """vip:<id>:<uid>:<stars> → dict (chỉ nhận payload do bot tạo)."""
    try:
        kind, pid, uid, stars = (str(payload or "").split(":") + ["", "", "", ""])[:4]
        if kind != "vip":
            return None
        p = BY_ID.get(pid)
        if not p or int(stars) != int(p["stars"]):
            return None
        return {"pkg": p, "uid": int(uid)}
    except Exception:            # noqa: BLE001
        return None


# ---------------------------------------------------------------- handlers
async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Telegram hỏi trước khi trừ Stars — phải trả lời trong 10s."""
    q = update.pre_checkout_query
    if not q:
        return
    info = parse_payload(q.invoice_payload)
    if not info:
        await q.answer(ok=False, error_message="Gói này không còn hiệu lực, mở /stars chọn lại nhé.")
        return
    if int(q.from_user.id) != int(info["uid"]):
        await q.answer(ok=False, error_message="Invoice này không phải của bạn.")
        return
    await q.answer(ok=True)


async def on_paid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Đã trừ Stars xong → cộng VIP + báo lại."""
    msg = update.effective_message
    sp = getattr(msg, "successful_payment", None) if msg else None
    if not sp:
        return
    info = parse_payload(sp.invoice_payload)
    uid = int(update.effective_user.id) if update.effective_user else 0
    days = info["pkg"]["days"] if info else 0
    stars = int(sp.total_amount or 0)
    if info:
        gift(uid, days, note="stars")
    add_payment(uid, stars, days, sp.telegram_payment_charge_id or "", sp.invoice_payload or "")
    try:
        await msg.reply_text(
            "✅ <b>THANH TOÁN THÀNH CÔNG</b>\n"
            f"├ Đã trả: <b>{stars} ⭐</b>\n"
            f"├ Cộng: <b>{days} ngày VIP</b>\n"
            f"└ Hết hạn: <b>{until_text(uid)}</b>\n\n"
            "<i>Cảm ơn bạn đã ủng hộ bot 💖</i>",
            parse_mode="HTML",
            reply_markup=B.kb([B.row(
                B.btn("Xem VIP", cb="st:me", style=B.GREEN, icon="👑"),
                B.btn("Dùng /anh18", cb="st:anh18", style=B.BLUE, icon="📸"),
            )]),
        )
    except Exception as e:       # noqa: BLE001
        log.info("báo thanh toán lỗi: %s", str(e)[:90])
    log.info("Stars +%s từ %s (%s ngày)", stars, uid, days)


async def cmd_stars(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/stars — bảng gói."""
    u = update.effective_user
    args = [a.lower() for a in (context.args or [])]
    msg = update.effective_message

    if args and args[0] in ("give", "tang", "tặng") and len(args) >= 3:
        from core import admins
        if not admins.is_admin(u.id):
            await msg.reply_text("🔒 Lệnh này dành cho admin bot.")
            return
        try:
            uid, days = int(args[1]), int(args[2])
        except ValueError:
            await msg.reply_text("Cú pháp: <code>/stars give 123456789 30</code>")
            return
        gift(uid, days, note="admin")
        await msg.reply_text(
            f"👑 Đã tặng <b>{days} ngày VIP</b> cho <code>{uid}</code>\n"
            f"Hết hạn: <b>{until_text(uid)}</b>",
            parse_mode="HTML",
        )
        return

    await msg.reply_text(panel_text(u.id), parse_mode="HTML", reply_markup=panel_kb(),
                         disable_web_page_preview=True)


async def cb_stars(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    act = data[3:] if data.startswith("st:") else ""

    if act.startswith("buy:"):
        pid = act[4:]
        ok = await send_package(context.bot, q.message.chat_id, q.from_user.id, pid)
        if ok:
            await q.answer("Đã tạo hoá đơn — bấm nút ⭐ Pay để trả.")
        else:
            await q.answer("Chưa tạo được hoá đơn, thử lại sau nhé.", show_alert=True)
        return

    if act == "me":
        await q.answer()
        await q.message.reply_text(vip_text(q.from_user.id), parse_mode="HTML",
                                   reply_markup=B.kb([B.row(
                                       B.btn("Xem gói", cb="st:panel", style=B.BLUE, icon="⭐"),
                                   )]))
        return

    if act == "help":
        await q.answer()
        await q.message.reply_text(
            "⭐ <b>MUA BẰNG STARS — CÁCH LÀM</b>\n"
            "1. Bấm <code>/stars</code>\n"
            "2. Chọn gói → bấm nút <b>⭐ Pay</b>\n"
            "3. Telegram trừ Stars trong ví của bạn (mua Stars bằng thẻ/nạp sẵn)\n"
            "4. Bot cộng VIP ngay, không cần chờ duyệt\n\n"
            "<i>Hoá đơn do chính Telegram phát hành — bot không thấy thông tin thẻ của bạn.</i>",
            parse_mode="HTML",
            reply_markup=B.kb([B.row(
                B.btn("Mở bảng gói", cb="st:panel", style=B.GREEN, icon="⭐"),
            )]),
        )
        return

    if act == "anh18":
        await q.answer("Gõ /anh18 trong chat riêng để mở kho ảnh nhé.", show_alert=True)
        return

    # mặc định: panel
    await q.answer()
    try:
        await q.message.edit_text(panel_text(q.from_user.id), parse_mode="HTML",
                                  reply_markup=panel_kb(), disable_web_page_preview=True)
    except BadRequest:
        pass


async def cmd_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/vip — xem hạn VIP."""
    u = update.effective_user
    await update.effective_message.reply_text(
        vip_text(u.id), parse_mode="HTML",
        reply_markup=B.kb([B.row(
            B.btn("Mua VIP", cb="st:panel", style=B.GREEN, icon="⭐"),
        )]),
    )


async def refund(bot, uid: int, charge_id: str) -> tuple[bool, str]:
    """Hoàn Stars cho 1 giao dịch (admin). Telegram yêu cầu đúng charge_id."""
    try:
        await bot.refund_star_payment(user_id=int(uid), telegram_payment_charge_id=charge_id)
        for p in _payments():
            if p.get("charge") == charge_id:
                p["refunded"] = True
        save()
        return True, "ok"
    except (BadRequest, Forbidden) as e:
        return False, str(e)[:140]
    except Exception as e:       # noqa: BLE001
        return False, str(e)[:140]

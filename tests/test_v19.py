#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test v19: nút copy_text, ICON PREMIUM 100% (tin nhắn + nút), dịch đa ngôn ngữ
(reply dấu "." của admin · /dich · bảng chọn ngôn ngữ bằng icon).

Chạy: .venv19/bin/python tests/test_v19.py
"""

import asyncio
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))

TMP = tempfile.mkdtemp(prefix="khoaan-v19-")
os.environ.update({
    "BOT_TOKEN": "123456:TEST-TOKEN",
    "DATA_DIR": os.path.join(TMP, "data"),
    "TMP_DIR": os.path.join(TMP, "tmp"),
    "PORT": "0",
    "TZ": "Asia/Ho_Chi_Minh",
    "GIF_REPLY": "0",
    "CAPTCHA_EVERY": "10",
    "FORCE_COLOR": "0",
    "ADMIN_IDS": "8297032840",
})
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)
os.makedirs(os.environ["TMP_DIR"], exist_ok=True)

from telegram import InlineKeyboardButton, Update  # noqa: E402
from telegram.ext import CallbackQueryHandler  # noqa: E402

import config  # noqa: E402
import main  # noqa: E402
from core import buttons as B  # noqa: E402
from core import premium  # noqa: E402
from core import translate as dich  # noqa: E402
from fake_api import FakeAPI  # noqa: E402

ADMIN = 8297032840
BOT = None
RESULTS = []


def ok(name, cond=True, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  [OK] " if cond else "  [XX] ") + name + (f"  -> {detail}" if detail and not cond else ""))


def _msg(text, user_id=ADMIN, reply_text=None):
    mid = int(time.time() * 1000) % 1000000
    msg = {
        "message_id": mid, "date": 1730000000,
        "chat": {"id": user_id, "type": "private"},
        "from": {"id": user_id, "is_bot": False, "first_name": "Khoa An",
                 "username": "chiencuto18m"},
        "text": text,
    }
    if text.startswith("/"):
        msg["entities"] = [{"type": "bot_command", "offset": 0,
                            "length": len(text.split()[0])}]
    if reply_text is not None:
        msg["reply_to_message"] = {
            "message_id": mid + 1, "date": 1730000000,
            "chat": {"id": user_id, "type": "private"},
            "from": {"id": 999000111, "is_bot": False, "first_name": "Khach"},
            "text": reply_text,
        }
    return {"update_id": mid, "message": msg}


def private(text, reply_text=None, user_id=ADMIN):
    main.COOLDOWN.clear()
    return Update.de_json(_msg(text, user_id, reply_text), bot=BOT)


def cb(data, user_id=ADMIN):
    main.COOLDOWN.clear()
    mid = int(time.time() * 1000) % 1000000
    return Update.de_json({
        "update_id": mid,
        "callback_query": {
            "id": str(mid),
            "from": {"id": user_id, "is_bot": False, "first_name": "Khoa An",
                     "username": "chiencuto18m"},
            "chat_instance": "1", "data": data,
            "message": {
                "message_id": 555, "date": 1730000000,
                "chat": {"id": user_id, "type": "private"},
                "from": {"id": 7770001, "is_bot": True, "first_name": "Bot",
                         "username": "khoaan_test_bot"},
                "text": "bảng",
            },
        },
    }, bot=BOT)


async def make_app(api):
    app = main.build_app(base_url=api.base_url)
    await app.initialize()
    global BOT
    BOT = app.bot
    return app


def _markup(api):
    """reply_markup của tin gửi cuối (FakeAPI lưu dạng chuỗi JSON)."""
    msgs = api.of("sendMessage")
    if not msgs:
        return None
    mk = msgs[-1].get("reply_markup")
    if isinstance(mk, str):
        try:
            mk = json.loads(mk)
        except Exception:  # noqa: BLE001
            return None
    return mk


def flat(markup):
    return [b for line in (markup.inline_keyboard if markup else []) for b in line]


def icon_of(b):
    return (getattr(b, "api_kwargs", None) or {}).get("icon_custom_emoji_id")


# ------------------------------------------------------------------ 1. copy_text
def test_copy_button():
    print("\n[1] Nút 'Sao chép' không còn lỗi field copy_text")
    b = B.btn("Sao chép ID", copy=123456789, style=B.GREEN, icon="🆔")
    d = b.to_dict()
    ok("copy_text là OBJECT", isinstance(d.get("copy_text"), dict), str(d.get("copy_text")))
    ok("copy_text đúng nội dung", (d.get("copy_text") or {}).get("text") == "123456789",
       str(d.get("copy_text")))

    mk = B.kb([B.row(b, B.btn("Menu", cb="p_home", style=B.BLUE, icon="⬅️"))])
    ok("bàn phím có extras", B.has_extras(mk))
    stripped = B.strip(mk)
    sb = flat(stripped)[0]
    ok("strip vẫn giữ object copy", not isinstance(sb.copy_text, str), type(sb.copy_text).__name__)
    ok("strip serialize được", isinstance(sb.to_dict().get("copy_text"), dict),
       str(sb.to_dict().get("copy_text")))

    # đúng đường thật: bảng /fb và panel tải xuống
    menus = []
    for fn in ("main_menu",):
        f = getattr(main, fn, None)
        if f:
            try:
                menus.append((fn, f(True)))
            except Exception as e:  # noqa: BLE001
                ok(f"{fn} dựng được", False, str(e)[:80])
    for name, mk2 in menus:
        bad = [x.text for x in flat(mk2) if "copy_text" in (x.to_dict() or {})
               and isinstance((x.to_dict() or {}).get("copy_text"), str)]
        ok(f"{name}: copy_text đều là object", not bad, str(bad))


# ------------------------------------------------------------------ 2. premium 100%
def test_premium_everywhere():
    print("\n[2] Icon premium 100% — tin nhắn và nút, không còn icon thường")
    ok("premium đang bật", premium.ENABLED)
    wrapped = premium.p("🔥 Lệnh này dành cho admin bot.")
    ok("p() bọc emoji", "<tg-emoji" in wrapped, wrapped)

    # mọi icon được truyền trong code phải có id premium thật
    missing = []
    for f in Path(HERE).rglob("*.py"):
        s = str(f)
        if ".venv19" in s or "/tests/" in s or "__pycache__" in s or "premium_ids" in s:
            continue
        txt = f.read_text("utf-8", "replace")
        for m in re.finditer(r'icon\s*=\s*["\']([^"\']+)["\']', txt):
            e = m.group(1)
            if not premium.first_id(e):
                missing.append(f"{f.name}:{e}")
    ok("mọi icon= đều có id premium", not missing, str(sorted(set(missing))[:6]))

    # mọi nút của menu chính + bảng dịch đều gắn icon premium
    menu = main.main_menu(True)
    no_icon = [x.text for x in flat(menu) if not icon_of(x)]
    ok("menu chính: 100% nút có icon premium", not no_icon, str(no_icon[:6]))

    pages = max(1, (len(dich.LANGS) + dich.PAGE_SIZE - 1) // dich.PAGE_SIZE)
    bad = []
    for p in range(pages):
        for x in flat(dich.lang_kb("home", "vi", p)):
            if not icon_of(x):
                bad.append(x.text)
    ok("bảng ngôn ngữ: 100% nút có icon premium", not bad, str(bad[:6]))

    # nhãn nút không còn emoji thường đứng đầu
    lead = [x.text for x in flat(menu) + flat(dich.lang_kb("home", "vi", 0))
            if x.text and premium.p(x.text[:2]) != x.text[:2]]
    ok("không nút nào còn icon thường trong nhãn", not lead, str(lead[:6]))


async def test_premium_patch(app, api):
    print("\n[2b] Mọi lời gọi gửi tin trực tiếp đều được bọc premium")
    msg = private("/dich").effective_message
    api.clear()
    # gọi THẲNG msg.reply_text (không qua _send của main) — như các module khác
    await msg.reply_text("🔒 Lệnh này dành cho admin bot.")
    sent = api.texts()
    ok("gửi được", bool(sent), str(api.methods()[:4]))
    ok("tin đã thành premium", any("<tg-emoji" in t for t in sent), str(sent)[:160])
    ok("tự bật parse_mode HTML", any(p.get("parse_mode") == "HTML" for p in api.of("sendMessage")))


# ------------------------------------------------------------------ 3. dịch đa ngôn ngữ
def test_langs():
    print("\n[3] Bảng ngôn ngữ: nhiều nước, icon riêng, đều premium")
    ok(">= 70 ngôn ngữ", len(dich.LANGS) >= 70, str(len(dich.LANGS)))
    codes = [c for c, _ in dich.LANGS]
    ok("không trùng mã", len(codes) == len(set(codes)))
    for need in ("vi", "en", "zh-CN", "ja", "ko", "th", "fr", "de", "ru", "ar", "hi", "es"):
        ok(f"có {need}", need in codes)
    bad = [c for c in codes if "<tg-emoji" not in premium.p(dich.lang_icon(c))]
    ok("mọi icon ngôn ngữ là premium", not bad, str(bad[:5]))
    ok("tên ngôn ngữ đọc được", dich.lang_name("ja") == "Nhật", dich.lang_name("ja"))
    ok("pref mặc định hợp lệ", dich.pref(ADMIN) in codes, dich.pref(ADMIN))


async def test_dot_reply(app, api):
    print("\n[4] Admin reply tin rồi gửi '.' -> bot tự nhận ngôn ngữ và dịch")
    calls = []

    async def fake_tr(text, target, source="auto"):
        calls.append((text, target))
        return f"[{target}] {text}", "en"

    orig = dich.translate
    dich.translate = fake_tr
    try:
        api.clear()
        dich.set_pref(ADMIN, "vi")
        await app.process_update(private(".", reply_text="Hello everyone"))
        await asyncio.sleep(0.2)
        sent = api.texts()
        ok("có gọi máy dịch", bool(calls), str(calls))
        ok("đã gửi bản dịch", any("Hello everyone" in t for t in sent), str(sent)[:200])
        ok("bản dịch ddungs ngôn ngữ đích", any("[vi]" in t for t in sent), str(sent)[:200])
        mk = _markup(api)
        ok("kèm bảng chọn ngôn ngữ", bool(mk), str(mk)[:120])
        ok("nút đổi ngôn ngữ 1 chạm",
           bool(mk) and any("tr:" in str((b or {}).get("callback_data", ""))
                            for b in (mk or {}).get("inline_keyboard", [[]])[0]))

        api.clear()
        await app.process_update(private(".", reply_text="Hello everyone"))
        await asyncio.sleep(0.2)
        ok("dịch lại vẫn chạy", bool(calls))

        api.clear()
        await app.process_update(private("."))
        await asyncio.sleep(0.15)
        ok("gửi '.' không reply -> hướng dẫn", any("reply" in t.lower() or "trả lời" in t.lower()
                                                    for t in api.texts()), str(api.texts())[:160])
    finally:
        dich.translate = orig


async def test_cmd_and_panel(app, api):
    print("\n[5] /dich <nội dung> và nút menu 'Dịch'")
    async def fake_tr(text, target, source="auto"):
        return f"[{target}] {text}", "auto"
    orig = dich.translate
    dich.translate = fake_tr
    try:
        api.clear()
        dich.set_pref(ADMIN, "ja")
        await app.process_update(private("/dich xin chao"))
        await asyncio.sleep(0.2)
        sent = " | ".join(api.texts())
        ok("/dich trả bản dịch", "xin chao" in sent, sent[:160])
        ok("/dich dùng ngôn ngữ đã chọn", "[ja]" in sent, sent[:160])
        ok("/dich có premium", "<tg-emoji" in sent, sent[:120])
    finally:
        dich.translate = orig

    api.clear()
    await app.process_update(cb("p_dich"))
    await asyncio.sleep(0.2)
    sent = " | ".join(api.texts())
    ok("nút 'Dịch' mở bảng", bool(sent), sent[:120])
    mk = _markup(api)
    btns = mk.get("inline_keyboard", []) if isinstance(mk, dict) else []
    ok("bảng có nút ngôn ngữ", bool(btns) and len(btns) > 2, str(len(btns)))
    ok("bảng nhắc reply dấu .", "reply" in sent.lower(), sent[:120])

    api.clear()
    await app.process_update(cb("tr:home:ko"))
    await asyncio.sleep(0.2)
    ok("đổi ngôn ngữ mặc định", dich.pref(ADMIN) == "ko", dich.pref(ADMIN))


async def amain():
    api = FakeAPI()
    await api.start()
    app = await make_app(api)
    try:
        test_copy_button()
        test_premium_everywhere()
        await test_premium_patch(app, api)
        test_langs()
        await test_dot_reply(app, api)
        await test_cmd_and_panel(app, api)
    finally:
        try:
            await app.shutdown()
        except Exception:  # noqa: BLE001
            pass
        await api.stop()

    passed = sum(1 for _n, c, _d in RESULTS if c)
    total = len(RESULTS)
    print(f"\n{'=' * 60}\n{passed}/{total} test PASS")
    for name, cond, detail in RESULTS:
        if not cond:
            print(f"  FAIL {name}  {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))

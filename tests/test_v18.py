#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test v18: bỏ nút "Cả 2", nút MP3 dùng 1 lần, bỏ hết icon premium, gộp nút nền tảng,
/fb lấy được ID + ảnh thật, giao diện CMD mới.

Chạy: ../.venv-v17/bin/python tests/test_v18.py
"""

import asyncio
import io
import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))

TMP = tempfile.mkdtemp(prefix="khoaan-v18-")
os.environ.update({
    "BOT_TOKEN": "123456:TEST-TOKEN",
    "DATA_DIR": os.path.join(TMP, "data"),
    "TMP_DIR": os.path.join(TMP, "tmp"),
    "PORT": "0",
    "TZ": "Asia/Ho_Chi_Minh",
    "GIF_REPLY": "0",
    "CAPTCHA_EVERY": "10",
    "FORCE_COLOR": "0",
})
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)
os.makedirs(os.environ["TMP_DIR"], exist_ok=True)

from telegram import Update  # noqa: E402

import config  # noqa: E402
import main  # noqa: E402
from core import buttons as B  # noqa: E402
from core import captcha, console, facebook, social  # noqa: E402
from fake_api import FakeAPI  # noqa: E402

USER_ID = 555000111
BOT = None
API = None
RESULTS = []


def ok(name, cond=True, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  [OK] " if cond else "  [XX] ") + name + (f"  -> {detail}" if detail and not cond else ""))


def _msg(text, user_id=USER_ID):
    mid = int(time.time() * 1000) % 1000000
    msg = {
        "message_id": mid, "date": 1730000000,
        "chat": {"id": user_id, "type": "private"},
        "from": {"id": user_id, "is_bot": False, "first_name": "Khoa An",
                 "username": "chiencuto18m"},
        "text": text,
    }
    if text.startswith("/"):
        msg["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    return {"update_id": mid, "message": msg}


def private(text, user_id=USER_ID):
    main.COOLDOWN.clear()
    return Update.de_json(_msg(text, user_id), bot=BOT)


def cb(data, user_id=USER_ID):
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


def vis(s: str) -> int:
    return console._vis(s)


# ------------------------------------------------------------------ 1. nút "Cả 2" đã bỏ
def test_no_both_button():
    print("\n[1] Nút tải dưới tệp: chỉ 1 nút MP3, không còn 'Cả 2'")
    tok = main.dl_remember("https://www.tiktok.com/@a/video/1")
    kb = main.dl_buttons(tok, "video")
    ok("video có bàn phím", kb is not None)
    rows = kb.inline_keyboard if kb else []
    flat = [b for r in rows for b in r]
    ok("chỉ 1 nút", len(flat) == 1, f"{len(flat)} nút")
    ok("không còn 'Cả 2'", not any("cả 2" in (b.text or "").lower() for b in flat),
       str([b.text for b in flat]))
    label = (flat[0].text if flat else "").lower()
    ok("nút là tải audio", "audio" in label or "mp3" in label, label)
    cbd = flat[0].callback_data if flat else ""
    ok("callback đúng token", cbd == f"tk:m:{tok}", cbd)
    ok("audio không có nút", main.dl_buttons(tok, "audio") is None)
    ok("both không có nút", main.dl_buttons(tok, "both") is None)


# ------------------------------------------------------------------ 2. nút bấm 1 lần
async def test_button_once(app, api):
    print("\n[2] Bấm 1 lần là nút chết (chống spam)")
    calls = []

    async def fake_do(update, context, url, kind, **kw):
        calls.append(kind)
        return True

    orig = main.do_download
    main.do_download = fake_do
    try:
        tok = main.dl_remember("https://www.tiktok.com/@a/video/2")
        api.clear()
        await app.process_update(cb(f"tk:m:{tok}"))
        await asyncio.sleep(0.15)
        ok("bấm lần 1 -> tải audio", calls == ["audio"], str(calls))
        ok("đã gỡ bàn phím",
           any(m == "editMessageReplyMarkup" for m in api.methods()), str(api.methods()[:6]))
        ok("đánh dấu đã dùng", main.DL_CACHE[tok].get("used") is True)

        api.clear()
        await app.process_update(cb(f"tk:m:{tok}"))
        await asyncio.sleep(0.15)
        ok("bấm lần 2 -> KHÔNG tải", calls == ["audio"], str(calls))
        ans = api.of("answerCallbackQuery")
        ok("báo nút chỉ dùng 1 lần",
           any("1 lần" in (a.get("text") or "") for a in ans), str(ans))
    finally:
        main.do_download = orig


# ------------------------------------------------------------------ 3. icon premium 100%
def test_premium_everywhere():
    print("\n[3] Icon premium phủ 100% (v19: chủ bot đổi ý — muốn premium hết)")
    ok("EMOJI_PREMIUM bật", config.EMOJI_PREMIUM is True)
    ok("BUTTON_ICON_PREMIUM bật", config.BUTTON_ICON_PREMIUM is True)
    b = B.btn("Tải xuống", cb="p_dl", style=B.GREEN, icon="⬇")
    ok("nút có icon_custom_emoji_id", bool((b.api_kwargs or {}).get("icon_custom_emoji_id")),
       str(dict(b.api_kwargs or {})))
    ok("vẫn giữ màu", dict(b.api_kwargs or {}).get("style") == B.GREEN, str(dict(b.api_kwargs or {})))
    from core import premium
    ok("premium.p bọc tg-emoji", "<tg-emoji" in premium.p("🎵 nhạc"))
    ok("premium.strip gỡ sạch", premium.strip(premium.p("🎵 nhạc")) == "🎵 nhạc")
    src = open(os.path.join(HERE, "main.py"), encoding="utf-8").read()
    ok("main.py dùng premium.p", "premium.p" in src)
    # nút dựng tay cũng phải có icon premium
    from telegram import InlineKeyboardButton as K
    raw = K("🔑 Nhập API", callback_data="qc:api")
    ok("nút dựng tay cũng có icon", bool(raw.to_dict().get("icon_custom_emoji_id")),
       str(raw.to_dict()))


# ------------------------------------------------------------------ 4. gộp nút nền tảng
def test_download_menu():
    print("\n[4] Các nền tảng gộp vào 1 nút 'Tải xuống'")
    kb = main.main_menu(False) if hasattr(main, "main_menu") else None
    if kb is None:
        kb = main.kb_home(False) if hasattr(main, "kb_home") else None
    ok("có hàm tạo menu", kb is not None)
    flat = [b for r in (kb.inline_keyboard if kb else []) for b in r]
    labels = [(b.text or "").lower() for b in flat]
    cbs = [b.callback_data for b in flat if b.callback_data]
    ok("có đúng 1 nút tải xuống", sum(1 for t in labels if "tải xuống" in t) == 1, str(labels))
    ok("nút tải gọi p_dl", "p_dl" in cbs, str(cbs))
    per_site = [t for t in labels if t.strip() in ("tiktok", "instagram", "facebook", "youtube")]
    ok("không còn nút riêng từng nền tảng", not per_site, str(per_site))


# ------------------------------------------------------------------ 5. /fb
def test_fb_parse():
    print("\n[5] /fb: nhận link + ID + username")
    p = facebook.parse
    ok("link /1000446343626 -> ID", p("https://www.facebook.com/1000446343626")["id"] == "1000446343626",
       str(p("https://www.facebook.com/1000446343626")))
    ok("profile.php -> ID",
       p("https://www.facebook.com/profile.php?id=1000446343626")["id"] == "1000446343626")
    ok("people -> ID",
       p("https://www.facebook.com/people/Khoa-An/1000446343626/")["id"] == "1000446343626")
    ok("@username", p("@zuck")["username"] == "zuck")
    ok("username trần", p("zuck")["username"] == "zuck")
    ok("share -> share",
       p("https://www.facebook.com/share/1KJhFVgHRt/?mibextid=x")["share"].startswith("https://"))


async def test_fb_flow(app, api):
    print("\n[6] /fb: gửi ảnh THẬT + truyền og:image cho avatar")
    seen = []
    real_resolve, real_avatar = facebook.resolve, facebook.avatar
    real_default = facebook.looks_default

    async def fake_resolve(raw):
        return {"ok": True, "id": "1000446343626", "name": "Khoa An", "username": "",
                "avatar_url": "https://scontent.xx.fbcdn.net/real.jpg",
                "how": "og:image (ảnh thật)", "share": "", "err": ""}

    async def fake_avatar(uid, og_url=""):
        seen.append((uid, og_url))
        return b"\xff\xd8\xff" + b"x" * 20000

    facebook.resolve, facebook.avatar = fake_resolve, fake_avatar
    facebook.looks_default = lambda data: not data or len(data) < 9000
    try:
        api.clear()
        await app.process_update(private("/fb 1000446343626"))
        await asyncio.sleep(0.35)
        ok("avatar nhận og_url",
           seen and seen[0][1] == "https://scontent.xx.fbcdn.net/real.jpg", str(seen))
        ok("gửi ảnh cho người dùng", "sendPhoto" in api.methods(), str(api.methods()[:8]))
        cap = " ".join(api.texts("sendPhoto"))
        ok("caption có ID", "1000446343626" in cap, cap[:120])
        ok("caption có tên", "Khoa An" in cap, cap[:120])

        # ảnh xám -> không gửi ảnh, mời nạp cookie
        async def gray_avatar(uid, og_url=""):
            return b"\xff\xd8\xff" + b"x" * 3000

        facebook.avatar = gray_avatar
        api.clear()
        await app.process_update(private("/fb 1000446343626"))
        await asyncio.sleep(0.35)
        ok("ảnh xám -> không gửi ảnh", "sendPhoto" not in api.methods(), str(api.methods()[:8]))
        txt = " ".join(api.texts("sendMessage") + api.texts("editMessageText"))
        ok("hướng dẫn nạp cookie", "/cookie fb" in txt, txt[-160:])
    finally:
        facebook.resolve, facebook.avatar = real_resolve, real_avatar
        facebook.looks_default = real_default


# ------------------------------------------------------------------ 6. console
def test_console():
    print("\n[7] Giao diện CMD mới")
    out = console.banner("v18.3", "TẢI VIDEO · NHẠC · ẢNH · 24/7", stats_commands=60)
    plain = re.sub(r"\x1b\[[0-9;]*m", "", out)
    lines = [l for l in plain.splitlines() if l.strip()]
    ok("có logo nhiều dòng", len(lines) >= 14, f"{len(lines)} dòng")
    ok("có tên KHOA AN", "KHOA" in plain.upper() and "AN" in plain.upper())
    ok("có chỉ số nền tảng / lệnh",
       any(k in plain.lower() for k in ("nền tảng", "lệnh", "giọng")), plain[:200])
    widths = {vis(l) for l in lines if l.startswith(("│", "|", "+"))}
    ok("khung thẳng cột", len(widths) == 1, f"widths={sorted(widths)}")
    ok("banner không chứa ANSI khi tắt màu", "\x1b[" not in plain)
    # ASCII fallback: giả lập terminal không UTF-8
    orig = console.utf8_ok
    console.utf8_ok = lambda: False
    try:
        raw = console.banner("v18.3", "TAI VIDEO", stats_commands=60)
        plain2 = re.sub(r"\x1b\[[0-9;]*m", "", raw)
        ok("fallback ASCII thuần", all(ord(c) < 128 for c in plain2),
           [c for c in plain2 if ord(c) > 127][:5])
    finally:
        console.utf8_ok = orig


async def amain():
    api = FakeAPI()
    await api.start()
    global API
    API = api
    app = await make_app(api)
    captcha.set_enabled(False)
    try:
        test_no_both_button()
        await test_button_once(app, api)
        test_premium_everywhere()
        test_download_menu()
        test_fb_parse()
        await test_fb_flow(app, api)
        test_console()
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

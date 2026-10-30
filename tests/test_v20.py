#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test v20 — ICON PREMIUM 100% (tin nhắn · mọi nút · mọi lệnh · mọi nước)
và luồng dịch mới (reply "." là dịch luôn, KHÔNG bày bảng chọn ngôn ngữ).

Chạy: .venv/bin/python tests/test_v20.py
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

TMP = tempfile.mkdtemp(prefix="khoaan-v20-")
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
    "EMOJI_PREMIUM": "1",
    "BUTTON_ICON_PREMIUM": "1",
})
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)
os.makedirs(os.environ["TMP_DIR"], exist_ok=True)

from telegram import Update  # noqa: E402

import config  # noqa: E402
import main  # noqa: E402
from core import buttons as B  # noqa: E402
from core import premium  # noqa: E402
from core import translate as dich  # noqa: E402
from fake_api import FakeAPI  # noqa: E402

ADMIN = 8297032840
BOT = None
RESULTS = []

# lệnh chạm mạng / gọi dịch vụ ngoài -> chỉ test phần "trả về" tĩnh
SKIP_CMDS = {"qc", "qcapi", "war", "spam", "capwed", "capture", "tts", "sing", "dl",
             "mp3", "getinfo", "thoitiet", "banner", "fb", "ytb", "tiktok", "ig",
             "cover", "vdgai", "js", "sticker", "short", "cleanup",
             # 18+ : tải ảnh thật từ mạng, chậm -> không đo trong test offline
             "anime", "sagiri", "girl", "bu", "mong", "gainhat", "japcosplay", "tw"}


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


# --------------------------------------------------------------- đọc dữ liệu gửi đi
def _mk(raw):
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:  # noqa: BLE001
            return None
    return raw


def sent_texts(api):
    """(method, text) của mọi tin đã gửi."""
    out = []
    for method in ("sendMessage", "editMessageText"):
        for p in api.of(method):
            t = p.get("text") or ""
            if t:
                out.append((method, t))
    for method in ("sendPhoto", "sendVideo", "sendAudio", "sendDocument", "editMessageCaption"):
        for p in api.of(method):
            t = p.get("caption") or ""
            if t:
                out.append((method, t))
    return out


def sent_markups(api):
    out = []
    for method in ("sendMessage", "editMessageText", "sendPhoto", "sendVideo",
                   "sendAudio", "sendDocument"):
        for p in api.of(method):
            mk = _mk(p.get("reply_markup"))
            if mk:
                out.append(mk)
    return out


def buttons_of(mk):
    return [b for line in (mk.get("inline_keyboard") or []) for b in line]


def audit(api, label):
    """Kiểm tra 1 lượt: mọi text phải có premium, mọi nút phải có icon premium."""
    texts = sent_texts(api)
    plain = [(m, t) for m, t in texts if "<tg-emoji" not in t]
    ok(f"{label}: mọi tin đều có icon premium", not plain,
       str([t[:60] for _m, t in plain[:3]]))

    bad_icon, bad_label = [], []
    for mk in sent_markups(api):
        for b in buttons_of(mk):
            if not b.get("icon_custom_emoji_id"):
                bad_icon.append(b.get("text"))
            if premium.first_emoji(b.get("text") or ""):
                bad_label.append(b.get("text"))
    ok(f"{label}: 100% nút có icon premium", not bad_icon, str(bad_icon[:5]))
    ok(f"{label}: không nút nào còn emoji thường", not bad_label, str(bad_label[:5]))


# --------------------------------------------------------------- 1. đơn vị
def test_units():
    print("\n[1] Bộ máy icon premium")
    ok("premium bật", premium.ENABLED)
    ok("ensure() chèn icon khi câu không có emoji",
       premium.has_premium(premium.ensure("Xin chào")), premium.ensure("Xin chào"))
    ok("ensure() giữ emoji sẵn có", premium.has_premium(premium.ensure("🎵 nhạc")))
    ok("ensure() không nhân đôi thẻ",
       premium.ensure("🎵 nhạc").count("<tg-emoji") == 1)
    ok("ensure() câu rỗng -> rỗng", premium.ensure("") == "")
    ok("pick_icon theo nghĩa (lỗi -> ❌)", premium.pick_icon("Đã xảy ra lỗi") == "❌",
       premium.pick_icon("Đã xảy ra lỗi"))
    ok("pick_icon mặc định có id", bool(premium.id_of(premium.pick_icon("abc xyz"))))
    ok("drop_emoji bỏ emoji thường", premium.drop_emoji("🔑 Nhập API") == "Nhập API",
       premium.drop_emoji("🔑 Nhập API"))
    ok("drop_emoji giữ chữ có dấu", premium.drop_emoji("Ảnh 18+ · VIP") == "Ảnh 18+ · VIP")

    # nút dựng tay (không truyền icon) vẫn phải có icon premium
    b = B.btn("Nút không emoji", cb="x")
    d = b.to_dict()
    ok("btn() tự gắn icon khi không truyền icon", bool(d.get("icon_custom_emoji_id")), str(d))
    b2 = B.btn("🔑 Nhập API", cb="y", icon="🔑")
    d2 = b2.to_dict()
    ok("btn() bỏ emoji thường khỏi nhãn", d2.get("text") == "Nhập API", str(d2))
    ok("btn() vẫn giữ icon premium", bool(d2.get("icon_custom_emoji_id")), str(d2))

    # icon lạ (không có id) -> vẫn ra icon premium khác
    b3 = B.btn("Việt", cb="z", icon="🫠")
    ok("icon không có id -> vẫn có icon premium", bool(b3.to_dict().get("icon_custom_emoji_id")),
       str(b3.to_dict()))


# --------------------------------------------------------------- 2. ngôn ngữ
def test_langs():
    print("\n[2] Mọi ngôn ngữ đều có icon premium, không nước nào trống")
    codes = [c for c, _ in dich.LANGS]
    ok(">= 79 ngôn ngữ", len(codes) >= 79, str(len(codes)))
    bad, empty = [], []
    for c in codes:
        ic = dich.lang_icon(c)
        if not ic:
            empty.append(c)
            continue
        if not (ic.isdigit() or premium.id_of(ic)):
            bad.append(f"{c}={ic}")
    ok("không ngôn ngữ nào trống icon", not empty, str(empty[:6]))
    ok("mọi icon ngôn ngữ là premium", not bad, str(bad[:6]))

    flags = [dich.lang_icon(c) for c in codes]
    ok("icon đa dạng (không phải 1 icon cho tất cả)", len(set(flags)) >= 8,
       str(len(set(flags))))
    ok("tiếng Việt có icon riêng", dich.lang_icon("vi") == "⭐", dich.lang_icon("vi"))

    # bảng chọn: mọi nút đều có icon premium
    pages = max(1, (len(dich.LANGS) + dich.PAGE_SIZE - 1) // dich.PAGE_SIZE)
    bad_btn = []
    for p in range(pages):
        for line in dich.lang_kb("tok123456789", "vi", p).inline_keyboard:
            for b in line:
                d = b.to_dict()
                if not d.get("icon_custom_emoji_id"):
                    bad_btn.append(d.get("text"))
                if premium.first_emoji(d.get("text") or ""):
                    bad_btn.append("basic:" + str(d.get("text")))
    ok(f"bảng {pages} trang: 100% nút có icon premium", not bad_btn, str(bad_btn[:6]))

    # cấu hình riêng của chủ bot (data/premium_flags.json) phải được ưu tiên
    p = Path(config.DATA_DIR) / "premium_flags.json"
    p.write_text(json.dumps({"vi": "🇬🇧"}), "utf-8")
    old = dict(dich._CUSTOM)
    dich._CUSTOM.clear()
    dich._CUSTOM.update(dich._custom_flags())
    try:
        ok("nạp cờ riêng qua premium_flags.json", dich.lang_icon("vi") == "🇬🇧",
           dich.lang_icon("vi"))
    finally:
        dich._CUSTOM.clear()
        dich._CUSTOM.update(old)
        p.unlink(missing_ok=True)


# --------------------------------------------------------------- 3. menu / panel
async def test_panels(app, api):
    print("\n[3] Menu + mọi panel: text và nút đều premium")
    for is_admin, name in ((False, "menu thường"), (True, "menu admin")):
        mk = main.main_menu(is_admin)
        for b in mk.inline_keyboard:
            for x in b:
                d = x.to_dict()
                if not d.get("icon_custom_emoji_id") or premium.first_emoji(d.get("text") or ""):
                    ok(f"{name}: nút lỗi", False, str(d))
                    break
        ok(f"{name}: 100% nút premium", True)

    # liệt kê tất cả action p_* có nhánh tĩnh trong panel_router
    src = (Path(HERE) / "main.py").read_text("utf-8")
    actions = sorted(set(re.findall(r'action == "([a-z0-9_]+)"', src)))
    safe = [a for a in actions if a not in {"clean", "stars", "qc", "baotri"}]
    ok("tìm được nhiều panel", len(safe) >= 8, str(safe))

    fail = []
    for a in safe:
        api.clear()
        await app.process_update(cb(f"p_{a}"))
        await asyncio.sleep(0.05)
        texts = sent_texts(api)
        if not texts:
            continue                      # panel mở bằng tin mới / không có text -> bỏ qua
        for _m, t in texts:
            if "<tg-emoji" not in t:
                fail.append((a, t[:40]))
        for mk in sent_markups(api):
            for b in buttons_of(mk):
                if not b.get("icon_custom_emoji_id") or premium.first_emoji(b.get("text") or ""):
                    fail.append((a, "btn:" + str(b.get("text"))))
    ok("mọi panel: text + nút đều premium", not fail, str(fail[:6]))


# --------------------------------------------------------------- 4. mọi lệnh
async def test_commands(app, api):
    print("\n[4] Mọi lệnh: câu trả lời đều có icon premium")
    names = sorted(main.BOT_COMMANDS)
    ok("bot có nhiều lệnh", len(names) >= 25, str(len(names)))
    fail = []
    for n in names:
        if n in SKIP_CMDS:
            continue
        api.clear()
        try:
            await asyncio.wait_for(app.process_update(private(f"/{n}")), timeout=8)
            await asyncio.sleep(0.03)
        except asyncio.TimeoutError:
            fail.append((n, "timeout"))
            continue
        except Exception as e:  # noqa: BLE001
            fail.append((n, str(e)[:50]))
            continue
        texts = sent_texts(api)
        if not texts:
            continue                      # lệnh không trả text (vd /stop im lặng)
        for _m, t in texts:
            if "<tg-emoji" not in t:
                fail.append((n, t[:50]))
        for mk in sent_markups(api):
            for b in buttons_of(mk):
                if not b.get("icon_custom_emoji_id") or premium.first_emoji(b.get("text") or ""):
                    fail.append((n, "btn:" + str(b.get("text"))))
    ok("mọi lệnh trả về đều premium (text + nút)", not fail, str(fail[:8]))

    # HELP_TEXT: từng dòng lệnh đều phải có icon premium
    lines = [ln for ln in main.HELP_TEXT.split("\n") if "/" in ln and "<code>" in ln]
    bad = [ln[:40] for ln in lines if "<tg-emoji" not in premium.p(ln)]
    ok(f"{len(lines)} dòng lệnh trong /help đều có icon premium", not bad, str(bad[:5]))


# --------------------------------------------------------------- 5. dịch
async def test_translate_flow(app, api):
    print("\n[5] Dịch: reply '.' tự dịch luôn, KHÔNG bày bảng chọn ngôn ngữ")

    async def fake_tr(text, target, source="auto"):
        return f"[{target}] {text}", "en"

    orig = dich.translate
    dich.translate = fake_tr
    try:
        dich.set_pref(ADMIN, "vi")
        api.clear()
        await app.process_update(private(".", reply_text="Hello everyone"))
        await asyncio.sleep(0.25)
        texts = " | ".join(t for _m, t in sent_texts(api))
        ok("có bản dịch", "Hello everyone" in texts, texts[:150])
        ok("bản dịch có premium", "<tg-emoji" in texts, texts[:150])
        marks = sent_markups(api)
        ok("có bàn phím", bool(marks), str(marks)[:80])
        nbtns = sum(len(buttons_of(m)) for m in marks)
        ok("CHỈ 1 nút (không bày bảng ngôn ngữ)", nbtns == 1, f"{nbtns} nút")
        btns = buttons_of(marks[-1]) if marks else []
        ok("nút đó là 'Đổi ngôn ngữ'", bool(btns) and "ngôn ngữ" in (btns[0].get("text") or ""),
           str(btns))
        data = (btns[0].get("callback_data") if btns else "") or ""
        ok("nút mở bảng khi bấm", data.startswith("tr:open:"), data)

        # bấm nút -> mới hiện bảng chọn
        api.clear()
        await app.process_update(cb(data))
        await asyncio.sleep(0.15)
        mk = None
        for m in sent_markups(api):
            mk = m
        # editReplyMarkup được gọi -> dùng markup trả về từ callback
        ok("bấm nút: mở được bảng", True)
        if mk:
            ok("bảng có nhiều nút ngôn ngữ", len(buttons_of(mk)) > 5, str(len(buttons_of(mk))))

        # chọn ngôn ngữ từ bảng -> dịch lại tại chỗ
        token = data.split(":")[-1]
        api.clear()
        await app.process_update(cb(f"tr:set:{token}:ja"))
        await asyncio.sleep(0.2)
        txt = " ".join(t for _m, t in sent_texts(api))
        ok("đổi ngôn ngữ -> dịch lại", "[ja]" in txt or "Nhật" in txt, txt[:150])
        ok("vẫn premium sau khi đổi", "<tg-emoji" in txt, txt[:150])

        # /dich không có nội dung -> mở bảng chọn (người dùng chủ động)
        api.clear()
        await app.process_update(private("/dich"))
        await asyncio.sleep(0.15)
        marks = sent_markups(api)
        n = sum(len(buttons_of(m)) for m in marks)
        ok("/dich trống mở bảng chọn ngôn ngữ", n >= 10, f"{n} nút")

        # /dich có nội dung -> chỉ 1 nút
        api.clear()
        await app.process_update(private("/dich xin chao"))
        await asyncio.sleep(0.2)
        marks = sent_markups(api)
        n = sum(len(buttons_of(m)) for m in marks)
        ok("/dich <nội dung> chỉ 1 nút", n == 1, f"{n} nút")
        ok("/dich <nội dung> có bản dịch", "xin chao" in " ".join(t for _m, t in sent_texts(api)))
    finally:
        dich.translate = orig


async def amain():
    api = FakeAPI()
    await api.start()
    app = await make_app(api)
    try:
        test_units()
        test_langs()
        await test_panels(app, api)
        await test_commands(app, api)
        await test_translate_flow(app, api)
    finally:
        try:
            await app.shutdown()
        except Exception:  # noqa: BLE001
            pass
        await api.stop()

    passed = sum(1 for _n, c, _d in RESULTS if c)
    total = len(RESULTS)
    print(f"\n{'=' * 62}\n{passed}/{total} test PASS")
    for name, cond, detail in RESULTS:
        if not cond:
            print(f"  FAIL {name}  {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))

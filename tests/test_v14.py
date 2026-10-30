#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test end-to-end v14: /donate (QR + nội dung random) + sửa lỗi đăng nhập /qc.

Chạy: ../.venv-v11/bin/python tests/test_v14.py
      LIVE=1 ../.venv-v11/bin/python tests/test_v14.py   (thêm test gọi Telegram thật)
"""

import asyncio
import os
from pathlib import Path
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))

TMP = tempfile.mkdtemp(prefix="khoaan-v14-")
os.environ.update({
    "BOT_TOKEN": "123456:TEST-TOKEN",
    "DATA_DIR": os.path.join(TMP, "data"),
    "TMP_DIR": os.path.join(TMP, "tmp"),
    "PORT": "0",
    "TZ": "Asia/Ho_Chi_Minh",
    "GIF_REPLY": "0",
})
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)
os.makedirs(os.environ["TMP_DIR"], exist_ok=True)

from telegram import Update  # noqa: E402

import config  # noqa: E402
import main  # noqa: E402
from core import captcha, donate, premium, qc, settings  # noqa: E402
from fake_api import FakeAPI  # noqa: E402

ADMIN_ID = int(config.ROOT_ADMIN)
USER_ID = 555000111
BOT = None
API = None
RESULTS = []
REAL_PROBE = qc.probe_creds


def ok(name, cond=True, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  → {detail}" if detail and not cond else ""))


def count_premium(text: str) -> int:
    return (text or "").count("<tg-emoji")


# ------------------------------------------------------------------ helpers
def msg_update(text, user_id=USER_ID, chat_type="private", chat_id=None, first_name="Khoa An"):
    mid = int(time.time() * 1000) % 1000000
    return Update.de_json({
        "update_id": mid,
        "message": {
            "message_id": mid,
            "date": 1730000000,
            "text": text,
            "chat": {"id": chat_id or user_id, "type": chat_type},
            "from": {"id": user_id, "is_bot": False, "first_name": first_name,
                     "username": "chiencuto18m"},
            "entities": ([{"type": "bot_command", "offset": 0,
                           "length": len(text.split()[0])}] if text.startswith("/") else []),
        },
    }, bot=BOT)


def cb_update(data, user_id=USER_ID, chat_id=None, message_id=555):
    mid = int(time.time() * 1000) % 1000000
    return Update.de_json({
        "update_id": mid,
        "callback_query": {
            "id": str(mid),
            "from": {"id": user_id, "is_bot": False, "first_name": "Khoa An",
                     "username": "chiencuto18m"},
            "chat_instance": "1",
            "data": data,
            "message": {
                "message_id": message_id,
                "date": 1730000000,
                "chat": {"id": chat_id or user_id, "type": "private"},
                "from": {"id": BOT.id, "is_bot": True, "first_name": "Bot",
                         "username": "khoaan_test_bot"},
                "text": "bảng điều khiển",
                "caption": "bảng điều khiển",
            },
        },
    }, bot=BOT)


async def make_app(api):
    captcha.set_enabled(False)          # test này không kiểm tra captcha
    app = main.build_app(base_url=api.base_url)
    await app.initialize()
    global BOT
    BOT = app.bot
    await premium.probe(app.bot)
    return app


async def run(app, update):
    await app.process_update(update)


def reset():
    main.COOLDOWN.clear()
    main.LAST_GIF.clear()
    qc.STATE.clear()


def set_probe(fn):
    """Ghi đè probe mạng để test offline (trả về dict như thật)."""
    qc.probe_creds = fn


# ------------------------------------------------------------------ 1. /donate
async def test_donate(app, api):
    print("\n[1] /donate — gửi QR + nội dung random có @username + id")
    reset()
    api.clear()
    set_probe(REAL_PROBE)
    await run(app, msg_update("/donate", user_id=USER_ID))
    ok("gửi ảnh QR (sendPhoto)", len(api.of("sendPhoto")) == 1, str(api.methods()[:4]))
    cap = (api.texts("sendPhoto") or [""])[0]
    ok("có ảnh thật đính kèm", any(n == "photo" for n, _ in api.files), str(api.files))
    ok("caption có 'Nội dung CK'", "Nội dung CK" in cap)
    ok("caption có @username người gọi", "@chiencuto18m" in cap)
    ok("caption có id người gọi", str(USER_ID) in cap)
    ok("caption CÓ icon premium", count_premium(cap) > 0, str(count_premium(cap)))
    kb = (api.of("sendPhoto") or [{}])[0].get("reply_markup") or ""
    ok("có nút đổi nội dung (dn:new)", "dn:new" in str(kb), str(kb)[:120])
    ok("có nút liên hệ admin", "http" in str(kb), str(kb)[:120])
    ok("HTML cân thẻ", cap.count("<b>") == cap.count("</b>"))
    return cap


# ------------------------------------------------------------------ 2. đổi nội dung
async def test_donate_callback(app, api):
    print("\n[2] Nút 🔁 — đổi nội dung CK, mã phải khác")
    reset()
    api.clear()
    # lấy 2 caption của 2 lần bấm để chắc chắn mã CK đổi (không random ra trùng 2 lần liên tiếp)
    before = donate.make_note(msg_update("/x"))
    notes = set()
    for _ in range(3):
        api.clear()
        await run(app, cb_update(f"dn:new:{USER_ID}"))
        cap = (api.texts("editMessageCaption") or [""])[0]
        notes.add(cap.split("Nội dung CK: <code>")[1].split("</code>")[0] if "Nội dung CK: <code>" in cap else "")
    ok("bấm nút gọi editMessageCaption", len(api.of("editMessageCaption")) >= 1)
    ok("mã CK đổi giữa các lần bấm", len(notes - {""}) >= 2, str(notes))
    ok("mã CK mới khác mã cũ", (notes - {before, ""}) != set(), str(notes))


# ------------------------------------------------------------------ 3. /qcapi
async def test_qcapi_parse_errors(app, api):
    print("\n[3] /qcapi — chặn dữ liệu sai, không lưu")
    reset()
    api.clear()
    settings.set_value(f"qc.api_id.{ADMIN_ID}", 0)
    settings.set_value(f"qc.api_hash.{ADMIN_ID}", "")
    await run(app, msg_update("/qcapi 123456 0123", user_id=ADMIN_ID))
    txt = " ".join(api.texts("sendMessage") + api.texts("editMessageText"))
    ok("thiếu hash → báo lỗi", "[FAIL]" in txt or "❌" in txt, txt[:80])
    ok("báo lỗi có hướng dẫn lấy API", "my.telegram.org" in txt)
    ok("không lưu api_id sai", int(settings.get(f"qc.api_id.{ADMIN_ID}", 0) or 0) == 0)

    api.clear()
    await run(app, msg_update("/qcapi 2040 b18441a1ff607e10a989891a5462e627", user_id=ADMIN_ID))
    txt = " ".join(api.texts("sendMessage") + api.texts("editMessageText"))
    ok("api_id app công khai → báo rõ", "công khai" in txt, txt[:100])
    ok("vẫn không lưu", int(settings.get(f"qc.api_id.{ADMIN_ID}", 0) or 0) == 0)

    api.clear()
    await run(app, msg_update("/qcapi abc", user_id=ADMIN_ID))
    _t = " ".join(api.texts("sendMessage") + api.texts("editMessageText"))
    ok("rác → báo lỗi", "[FAIL]" in _t or "❌" in _t, _t[:80])


async def test_qcapi_probe(app, api):
    print("\n[4] /qcapi — probe Telegram thật thì lưu, sai thì không lưu")
    reset()
    api.clear()
    calls = []

    async def probe_fail(api_id, api_hash):
        calls.append(("fail", api_id))
        return {"ok": False, "err": "<b>Cặp API_ID / API_HASH không hợp lệ</b>",
                "hint": "Tạo app mới ở my.telegram.org", "dc": ""}

    set_probe(probe_fail)
    await run(app, msg_update("/qcapi 12345678 0123456789abcdef0123456789abcdef", user_id=ADMIN_ID))
    txt = " ".join(api.texts("sendMessage") + api.texts("editMessageText"))
    ok("probe fail → báo Telegram từ chối", "từ chối" in txt or "không hợp lệ" in txt, txt[:90])
    ok("probe fail → KHÔNG lưu api_id", int(settings.get(f"qc.api_id.{ADMIN_ID}", 0) or 0) == 0)
    ok("probe fail → KHÔNG lưu hash", not settings.get(f"qc.api_hash.{ADMIN_ID}", ""))

    async def probe_ok(api_id, api_hash):
        calls.append(("ok", api_id))
        return {"ok": True, "err": "", "hint": "", "dc": 2}

    set_probe(probe_ok)
    api.clear()
    await run(app, msg_update("/qcapi api_id: 87654321\napi_hash: FEDCBA9876543210FEDCBA9876543210",
                              user_id=ADMIN_ID))
    txt = " ".join(api.texts("sendMessage") + api.texts("editMessageText"))
    ok("probe ok → lưu api_id đúng", int(settings.get(f"qc.api_id.{ADMIN_ID}", 0) or 0) == 87654321,
       str(settings.get(f"qc.api_id.{ADMIN_ID}", 0)))
    ok("probe ok → hash chuẩn hoá lowercase",
       settings.get(f"qc.api_hash.{ADMIN_ID}", "") == "fedcba9876543210fedcba9876543210")
    ok("hash không in full ra chat", "fedcba9876543210fedcba9876543210" not in txt)
    ok("báo lưu thành công", "Lưu" in txt or "[OK]" in txt, txt[:80])
    ok("đã gọi probe 2 lần", len(calls) == 2, str(calls))


async def test_qcapi_wizard(app, api):
    print("\n[5] Nhập API qua nút (không cần gõ lệnh) + xoá tin chứa hash")
    reset()
    api.clear()

    async def probe_ok(api_id, api_hash):
        return {"ok": True, "err": "", "hint": "", "dc": 4}

    set_probe(probe_ok)
    await run(app, cb_update("qc:api", user_id=ADMIN_ID))
    ok("nút mở ô nhập API", any("NHAP API_ID" in t.upper() or "NHẬP" in t.upper()
                                for t in api.texts("editMessageText")), str(api.methods()))
    ok("STATE chờ nhập api", (qc.STATE.get(ADMIN_ID) or {}).get("step") == "api")

    api.clear()
    await run(app, msg_update("11112222 0123456789abcdef0123456789abcdef", user_id=ADMIN_ID))
    txt = " ".join(api.texts("sendMessage") + api.texts("editMessageText"))
    ok("nhập text → lưu api_id", int(settings.get(f"qc.api_id.{ADMIN_ID}", 0) or 0) == 11112222,
       str(settings.get(f"qc.api_id.{ADMIN_ID}", 0)))
    ok("gửi xác nhận", "Lưu" in txt or "[OK]" in txt, txt[:80])
    ok("xoá tin chứa hash", "deleteMessage" in api.methods(), str(api.methods()))
    ok("STATE đã xoá", ADMIN_ID not in qc.STATE)


# ------------------------------------------------------------------ 6. parse_creds
def test_parse_creds():
    print("\n[6] parse_creds — nhận mọi kiểu dán từ my.telegram.org")
    H = "0123456789abcdef0123456789abcdef"
    cases = [
        (f"12345678 {H}", (12345678, H)),
        (f"api_id: 87654321\napi_hash: {H}", (87654321, H)),
        (f"App api_id: 1234567\nApp api_hash: {H.upper()}", (1234567, H)),
        (f"{H} 7654321", (7654321, H)),          # hash trước, id sau
        (f"12345678:{H}", (12345678, H)),        # dính dấu hai chấm
        (f'"{12345678}" "{H.upper()}"', (12345678, H)),
    ]
    for text, want in cases:
        got = qc.parse_creds(text)
        ok(f"parse {text[:26]!r}", (got[0], got[1]) == want, f"{got} != {want}")

    for bad in ("2040 b18441a1ff607e10a989891a5462e627",   # app công khai
                f"12345678 {H[:20]}",                       # hash thiếu
                "12345678",                                 # không có hash
                "abc"):
        a, h, err = qc.parse_creds(bad)
        ok(f"từ chối {bad[:20]!r}", a == 0 and err, f"{a}/{h}/{err[:30]}")

    ok("bảng id công khai có Telegram Desktop", 2040 in qc.PUBLIC_IDS)
    ok("PUBLIC_IDS có app Android", 6 in qc.PUBLIC_IDS)


# ------------------------------------------------------------------ 7. panel
async def test_panel(app, api):
    print("\n[7] /qc — panel có nút QR + Test API + Nhập API")
    reset()
    api.clear()
    await run(app, msg_update("/qc", user_id=ADMIN_ID))
    txt = " ".join(api.texts("sendMessage") + api.texts("editMessageText"))
    kb = str([p.get("reply_markup") for p in api.of("sendMessage")])
    ok("panel mở được", "RAI QUẢNG CÁO" in txt.upper() or "QUẢNG CÁO" in txt.upper(), txt[:80])
    ok("có nút đăng nhập QR", "qc:qr" in kb, kb[:200])
    ok("có nút test API", "qc:apitest" in kb)
    ok("có nút nhập API", "qc:api" in kb)

    api.clear()
    await run(app, cb_update("qc:apitest", user_id=ADMIN_ID))

    async def probe_fail(api_id, api_hash):
        return {"ok": False, "err": "<b>Cặp API_ID / API_HASH không hợp lệ</b>",
                "hint": "Tạo app mới", "dc": ""}

    set_probe(probe_fail)
    api.clear()
    await run(app, cb_update("qc:apitest", user_id=ADMIN_ID))
    txt = " ".join(api.texts("editMessageText"))
    ok("Test API báo lỗi rõ", "FAIL" in txt or "❌" in txt, txt[:80])


# ------------------------------------------------------------------ 8. live
async def test_live():
    print("\n[8] LIVE — probe_creds gọi Telegram thật với cặp sai")
    r = await REAL_PROBE(12345678, "0123456789abcdef0123456789abcdef")
    ok("cặp sai → ok=False", r.get("ok") is False, str(r)[:120])
    ok("báo đúng 'không hợp lệ'", "không hợp lệ" in (r.get("err") or ""), str(r.get("err"))[:80])
    ok("có hướng dẫn sửa", "my.telegram.org" in (r.get("hint") or ""))


# ------------------------------------------------------------------ main
async def main_():
    api = await FakeAPI().start()
    global API
    API = api
    app = await make_app(api)
    try:
        await test_donate(app, api)
        await test_donate_callback(app, api)
        await test_qcapi_parse_errors(app, api)
        await test_qcapi_probe(app, api)
        await test_qcapi_wizard(app, api)
        test_parse_creds()
        await test_panel(app, api)
        if os.getenv("LIVE"):
            set_probe(REAL_PROBE)
            await test_live()
    finally:
        await app.shutdown()
        await api.stop()

    passed = sum(1 for _, c, _ in RESULTS if c)
    total = len(RESULTS)
    print(f"\n KẾT QUẢ: {passed}/{total} test đạt")
    for name, c, d in RESULTS:
        if not c:
            print(f"  ❌ {name} → {d}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_()))

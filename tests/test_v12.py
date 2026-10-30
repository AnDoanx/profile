#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test end-to-end v12: /anh18 (kho ảnh 18+ theo chủ đề) + /war /spam /stop.

Chạy: ../.venv-v11/bin/python tests/test_v12.py
      LIVE=1 ../.venv-v11/bin/python tests/test_v12.py   (thêm test mạng thật)
"""

import asyncio
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))

TMP = tempfile.mkdtemp(prefix="khoaan-v12-")
os.environ.update({
    "BOT_TOKEN": "123456:TEST-TOKEN",
    "DATA_DIR": os.path.join(TMP, "data"),
    "TMP_DIR": os.path.join(TMP, "tmp"),
    "PORT": "0",
    "TZ": "Asia/Ho_Chi_Minh",
    "GIF_REPLY": "0",
    "WAR_INTERVAL": "1.5",
    "ANH18_DAILY": "9999",   # test cũ chạy 106 lượt liên tiếp (v13 mới thêm quota thật)
})
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)
os.makedirs(os.environ["TMP_DIR"], exist_ok=True)

from telegram import Update  # noqa: E402
from PIL import Image  # noqa: E402

import config  # noqa: E402
import main  # noqa: E402
from core import anh18, captcha, premium, war  # noqa: E402
from fake_api import FakeAPI  # noqa: E402

ADMIN_ID = int(config.ROOT_ADMIN)
USER_ID = 555000111          # user thường (cũng là admin nhóm trong test)
OTHER_ID = 777111222         # user thường, không phải admin nhóm
GROUP_ID = -1001234567890
BOT = None
API = None
RESULTS = []

FAKE_DIR = os.path.join(TMP, "anh18")
os.makedirs(FAKE_DIR, exist_ok=True)


def ok(name, cond=True, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  → {detail}" if detail and not cond else ""))


def count_premium(text: str) -> int:
    return (text or "").count("<tg-emoji")


# ------------------------------------------------------------------ helpers
def msg_update(text, user_id=USER_ID, chat_type="private", chat_id=None,
               reply_from=None, first_name="Khoa An"):
    mid = int(time.time() * 1000) % 1000000
    data = {
        "message_id": mid,
        "date": 1730000000,
        "text": text,
        "chat": {"id": chat_id or user_id, "type": chat_type},
        "from": {"id": user_id, "is_bot": False, "first_name": first_name,
                 "username": "chiencuto18m"},
        "entities": ([{"type": "bot_command", "offset": 0,
                       "length": len(text.split()[0])}] if text.startswith("/") else []),
    }
    if reply_from:
        data["reply_to_message"] = {
            "message_id": mid - 1,
            "date": 1730000000,
            "text": "tin nhắn gốc",
            "chat": {"id": chat_id or user_id, "type": chat_type},
            "from": reply_from,
        }
    return Update.de_json({"update_id": mid, "message": data}, bot=BOT)


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
                "text": "bảng chủ đề",
            },
        },
    }, bot=BOT)


def make_jpgs(n=10):
    """Tạo n ảnh thật trong pool giả (không cần mạng cho test giao diện)."""
    out = []
    for i in range(n):
        p = os.path.join(FAKE_DIR, f"t{i}.jpg")
        if not os.path.exists(p):
            im = Image.new("RGB", (720, 900), (30 + i * 7, 40, 60 + i * 5))
            im.save(p, "JPEG", quality=80)
        out.append(p)
    return out


async def fake_pick(key, n=10):
    pics = [Path(p) for p in make_jpgs(n)]
    anh18.PICKED.append(key)
    return pics[:n]


async def fake_videos(key, n=2, max_mb=24):
    anh18.PICKED.append(f"video:{key}")
    return [b"\x00\x00\x00\x18ftypisom" + b"\x00" * 5000]


async def make_app(api):
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


# ------------------------------------------------------------------ 1. panel
async def test_panel(app, api):
    print("\n[1] /anh18 — bảng chủ đề + nút bấm")
    reset()
    api.clear()
    await run(app, msg_update("/anh18"))
    txt = " ".join(api.texts("sendMessage"))
    ok("mở bảng chủ đề", "ẢNH 18+ THEO CHỦ ĐỀ" in txt, txt[:120])
    kb = (api.of("sendMessage")[0].get("reply_markup") or "") if api.of("sendMessage") else ""
    kb = str(kb)
    for k in anh18.ORDER:
        ok(f"có nút {k}", f"ah:c:{k}" in kb)
    ok("có nút Video + Thêm ảnh", "ah:v" in kb and "ah:m" in kb)
    ok("có nút kho ảnh + đóng", "ah:s" in kb and "ah:x" in kb)
    ok("kho ảnh có 8 chủ đề", len(anh18.pool_status()) == 8)

    api.clear()
    await run(app, msg_update("/anh18"))
    kb = str(api.of("sendMessage")[0].get("reply_markup") or "")
    ok("8 chủ đề = 3 hàng (3+3+2)", kb.count("ah:c:") == 8, str(kb.count("ah:c:")))


# ------------------------------------------------------------------ 2. ảnh theo chủ đề
async def test_images(app, api):
    print("\n[2] Bấm chủ đề → gửi album ảnh + quay lại bảng điều khiển")
    reset()
    for key in ("mong", "girl", "bu", "gainhat", "japcosplay", "tw", "anime", "sagiri"):
        api.clear()
        anh18.PICKED.clear()
        await run(app, cb_update(f"ah:c:{key}"))
        groups = api.of("sendMediaGroup")
        media = groups[0].get("media") if groups else None
        if isinstance(media, str):
            try:
                media = json.loads(media)
            except json.JSONDecodeError:
                media = []
        ok(f"[{key}] gửi 1 album", len(groups) == 1)
        ok(f"[{key}] album có 10 ảnh", len(media or []) == 10, str(len(media or [])))
        ok(f"[{key}] đúng chủ đề", anh18.PICKED == [key], str(anh18.PICKED))
        cap = ""
        if media:
            cap = str(media[0].get("caption") or "")
        ok(f"[{key}] caption CÓ icon premium", count_premium(cap) > 0, str(count_premium(cap)))
        ok(f"[{key}] caption đúng tên chủ đề",
           anh18.CATS[key]["label"].upper() in premium.strip(cap).upper(), cap[:80])
        ok(f"[{key}] bảng điều khiển được cập nhật lại (có bàn phím)",
           any("ah:c:" in str(p.get("reply_markup") or "") for p in api.of("editMessageText")))

    api.clear()
    await run(app, cb_update("ah:m"))
    ok("nút Thêm ảnh lấy tiếp", len(api.of("sendMediaGroup")) == 1)

    api.clear()
    await run(app, cb_update("ah:s"))
    st = " ".join(api.texts("editMessageText"))
    ok("nút Kho ảnh hiện số lượng", "KHO ẢNH" in st and "Mông" in st, st[:100])

    api.clear()
    await run(app, cb_update("ah:x"))
    ok("nút Đóng xoá bảng", len(api.of("deleteMessage")) == 1)


# ------------------------------------------------------------------ 3. lệnh lẻ
async def test_single_commands(app, api):
    print("\n[3] Lệnh lẻ từng chủ đề (/mong /bu /tw …)")
    reset()
    cases = {
        "/mong": "mong", "/girl": "girl", "/bu": "bu", "/gainhat": "gainhat",
        "/japcosplay": "japcosplay", "/tw": "tw", "/anime": "anime",
        "/sagiri": "sagiri", "/anh18 mong": "mong", "/anh18 gai": "girl",
    }
    for cmd, key in cases.items():
        api.clear()
        anh18.PICKED.clear()
        await run(app, msg_update(cmd))
        ok(f"{cmd} → {key}", anh18.PICKED == [key] and len(api.of("sendMediaGroup")) == 1,
           f"{anh18.PICKED} / {len(api.of('sendMediaGroup'))}")

    api.clear()
    await run(app, msg_update("/anh18 khongbiet"))
    ok("chủ đề lạ → hiện bảng", "ẢNH 18+ THEO CHỦ ĐỀ" in " ".join(api.texts("sendMessage")))


# ------------------------------------------------------------------ 4. video
async def test_video(app, api):
    print("\n[4] /vdgai — video ngắn")
    reset()
    api.clear()
    anh18.PICKED.clear()
    main.ANH18_LAST.clear()
    await run(app, msg_update("/vdgai"))
    ok("tải video theo chủ đề mặc định", anh18.PICKED == ["video:gainhat"], str(anh18.PICKED))
    ok("gửi sendVideo", len(api.of("sendVideo")) == 1, str(api.methods()))
    cap = api.of("sendVideo")[0].get("caption") if api.of("sendVideo") else ""
    ok("caption video CÓ icon premium", count_premium(str(cap)) > 0, str(cap)[:60])

    api.clear()
    anh18.PICKED.clear()
    await run(app, msg_update("/vdgai tw"))
    ok("/vdgai tw → chủ đề tw", anh18.PICKED == ["video:tw"], str(anh18.PICKED))


# ------------------------------------------------------------------ 5. chặn nhóm
async def test_group_guard(app, api):
    print("\n[5] Nhóm: chỉ admin bot được dùng ảnh 18+ (mặc định)")
    reset()
    config.ANH18_IN_GROUP = False
    api.clear()
    await run(app, msg_update("/anh18", user_id=OTHER_ID, chat_type="supergroup",
                              chat_id=GROUP_ID))
    txt = " ".join(api.texts("sendMessage"))
    ok("người thường trong nhóm bị chặn", "chỉ dùng trong chat riêng" in txt, txt[:120])
    ok("không gửi ảnh trong nhóm", not api.of("sendMediaGroup"))

    api.clear()
    await run(app, msg_update("/anh18 mong", user_id=ADMIN_ID, chat_type="supergroup",
                              chat_id=GROUP_ID))
    ok("admin bot trong nhóm vẫn dùng được", bool(api.of("sendMediaGroup")))


# ------------------------------------------------------------------ 6. war
async def test_war(app, api):
    print("\n[6] /war — war tag kèm câu chửi + icon premium")
    reset()
    await war.stop(GROUP_ID)

    api.clear()
    await run(app, msg_update("/war", chat_type="supergroup", chat_id=GROUP_ID))
    ok("thiếu mục tiêu → hướng dẫn", "Chưa biết war ai" in " ".join(api.texts("sendMessage")))

    api.clear()
    await run(app, msg_update("/war @someone 5", user_id=OTHER_ID, chat_type="supergroup",
                              chat_id=GROUP_ID))
    ok("người thường không bật được war",
       "admin nhóm" in " ".join(api.texts("sendMessage")), str(api.texts())[:120])
    ok("không có war nào chạy", not war.is_running(GROUP_ID))

    api.clear()
    before = len(api.of("sendMessage"))
    await run(app, msg_update("/war @someone 3", user_id=USER_ID, chat_type="supergroup",
                              chat_id=GROUP_ID))
    ok("admin nhóm bật được war", war.is_running(GROUP_ID))
    start_txt = " ".join(api.texts("sendMessage")[before:])
    ok("báo bắt đầu + số lượt", "WAR BẮT ĐẦU" in start_txt and "3" in start_txt, start_txt[:100])
    ok("hướng dẫn /stop", "/stop" in start_txt)

    await asyncio.sleep(5.2)
    war_msgs = [t for t in api.texts("sendMessage")
                if "@someone" in t and "WAR BẮT ĐẦU" not in t]
    ok("đã tag mục tiêu ≥ 2 lượt", len(war_msgs) >= 2, str(len(war_msgs)))
    ok("câu chửi lấy từ bộ 200 câu",
       all(any(premium.strip(line) in premium.strip(t) for line in war.LINES)
           for t in war_msgs), str(war_msgs)[:100])
    ok("câu chửi CÓ icon premium", count_premium(war_msgs[0] if war_msgs else "") > 0,
       str(war_msgs[:1])[:100])
    ok("không vượt quá số lượt yêu cầu", len(war_msgs) <= 3, str(len(war_msgs)))

    await asyncio.sleep(2.0)
    ok("tự kết thúc sau đủ lượt", not war.is_running(GROUP_ID))

    api.clear()
    await run(app, msg_update("/stop", chat_type="supergroup", chat_id=GROUP_ID))
    ok("stop khi không chạy → báo rõ",
       "Không có war/spam" in " ".join(api.texts("sendMessage")))


# ------------------------------------------------------------------ 7. spam + stop
async def test_spam_stop(app, api):
    print("\n[7] /spam — spam tag theo số lần + /stop dừng giữa chừng")
    reset()
    await war.stop(GROUP_ID)

    api.clear()
    await run(app, msg_update("/spam @victim 6", user_id=USER_ID, chat_type="supergroup",
                              chat_id=GROUP_ID))
    ok("bật spam", war.is_running(GROUP_ID))
    ok("báo SPAM TAG", "SPAM TAG" in " ".join(api.texts("sendMessage")))

    await asyncio.sleep(3.4)
    mid = len([t for t in api.texts("sendMessage") if "@victim" in t])
    ok("đang spam dở (2–4 lượt)", 2 <= mid < 6, str(mid))

    api.clear()
    await run(app, msg_update("/stop", user_id=USER_ID, chat_type="supergroup",
                              chat_id=GROUP_ID))
    ok("stop báo đã dừng", "ĐÃ DỪNG" in " ".join(api.texts("sendMessage")))
    ok("dừng thật", not war.is_running(GROUP_ID))

    n1 = len([t for t in api.texts("sendMessage") if "@victim" in t])
    await asyncio.sleep(2.2)
    n2 = len([t for t in api.texts("sendMessage") if "@victim" in t])
    ok("sau /stop không gửi thêm", n1 == n2, f"{n1} -> {n2}")

    api.clear()
    await run(app, msg_update("/war @x 5", user_id=USER_ID, chat_type="supergroup",
                              chat_id=GROUP_ID))
    await asyncio.sleep(0.4)
    api.clear()
    await run(app, msg_update("/war @y 5", user_id=USER_ID, chat_type="supergroup",
                              chat_id=GROUP_ID))
    ok("đang chạy thì không bật chồng",
       "Đang chạy rồi" in " ".join(api.texts("sendMessage")))
    await war.stop(GROUP_ID)


# ------------------------------------------------------------------ 8. bộ câu
def test_lines():
    print("\n[8] Bộ câu chửi")
    ok("có ≥ 200 câu", len(war.LINES) >= 200, str(len(war.LINES)))
    ok("không trùng câu", len(set(war.LINES)) == len(war.LINES))
    ok("câu nào cũng có emoji",
       all(any(ord(c) > 0x2000 for c in line) for line in war.LINES),
       str([l for l in war.LINES if not any(ord(c) > 0x2000 for c in l)][:2]))
    ok("bộ spam ngắn có 16 câu", len(war.SHORT) == 16)
    m = war.mention_of(type("U", (), {"id": 123, "username": None, "first_name": "An",
                                      "full_name": "An"})())
    ok("mention khi không có username", "tg://user?id=123" in m, m)


# ------------------------------------------------------------------ 9. live
async def test_live():
    print("\n[9] Mạng thật: nguồn ảnh + video")
    from core import http
    try:
        got = await anh18._gather_urls("mong", 20)
        ok("eporner trả URL", len(got) >= 10, str(len(got)))
        raw = await anh18._fetch_image(got[0], asyncio.Semaphore(1)) if got else None
        ok("tải + chuẩn hoá ảnh thật", bool(raw) and len(raw) > 8000,
           str(None if raw is None else len(raw)))
        anim = await anh18._gather_urls("anime", 20)
        ok("lolicon trả URL anime", len(anim) >= 3, str(len(anim)))
        clips = await anh18.take_videos("gainhat", 1, max_mb=24)
        ok("redgifs trả video mp4", len(clips) == 1 and clips[0][4:8] == b"ftyp",
           str([len(c) for c in clips]))
    finally:
        await http.close_session()


# ------------------------------------------------------------------ main
async def amain():
    global API
    print("=" * 62)
    print(" KHOA AN TOOLS v12 — test thật (Application + Bot API giả)")
    print("=" * 62)

    test_lines()          # không cần app

    API = await FakeAPI().start()
    app = await make_app(API)

    # test giao diện: thay nguồn ảnh bằng ảnh thật sinh tại chỗ (không phụ thuộc mạng)
    anh18.PICKED = []
    anh18.pick = fake_pick
    anh18.take_videos = fake_videos

    captcha.set_enabled(False)

    try:
        await test_panel(app, API)
        await test_images(app, API)
        await test_single_commands(app, API)
        await test_video(app, API)
        await test_group_guard(app, API)
        await test_war(app, API)
        await test_spam_stop(app, API)
        if os.getenv("LIVE") == "1":
            await test_live()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        ok("không có exception", False, "xem traceback ở trên")
    finally:
        await war.stop(GROUP_ID)
        await app.shutdown()
        await API.stop()

    passed = sum(1 for _, c, _ in RESULTS if c)
    total = len(RESULTS)
    print("\n" + "=" * 62)
    print(f" KẾT QUẢ: {passed}/{total} test đạt")
    print("=" * 62)
    if passed != total:
        print(" Lỗi:")
        for name, cond, detail in RESULTS:
            if not cond:
                print(f"   ❌ {name}  {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test end-to-end v11: captcha lần đầu · banner theo tên · reaction theo nội dung · /id + avatar + bio.

Chạy: ../.venv-v11/bin/python tests/test_v11.py
"""

import asyncio
import io
import json
import os
import re
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))

TMP = tempfile.mkdtemp(prefix="khoaan-v11-")
os.environ.update({
    "BOT_TOKEN": "123456:TEST-TOKEN",
    "DATA_DIR": os.path.join(TMP, "data"),
    "TMP_DIR": os.path.join(TMP, "tmp"),
    "PORT": "0",
    "TZ": "Asia/Ho_Chi_Minh",
})
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)
os.makedirs(os.environ["TMP_DIR"], exist_ok=True)

from telegram import Update  # noqa: E402

import config  # noqa: E402
import main  # noqa: E402
from core import banner, captcha, premium, reaction  # noqa: E402
from fake_api import BOT_USERNAME, FakeAPI  # noqa: E402

ADMIN_ID = int(config.ROOT_ADMIN)
USER_ID = 555000111          # user thường (đã xác minh trong phần lớn test)
NEW_USER = 777111222         # người dùng lần đầu
BOT = None
RESULTS = []


def ok(name, cond=True, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  → {detail}" if detail and not cond else ""))


# ------------------------------------------------------------------ helpers
def count_premium(text: str) -> int:
    return text.count("<tg-emoji")


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
                         "username": BOT_USERNAME},
                "text": "nút",
            },
        },
    }, bot=BOT)


def reaction_update(emoji, actor_id, chat_id=USER_ID, message_id=9):
    mid = int(time.time() * 1000) % 1000000
    return Update.de_json({
        "update_id": mid,
        "message_reaction": {
            "chat": {"id": chat_id, "type": "private"},
            "message_id": message_id,
            "date": 1730000000,
            "old_reaction": [],
            "new_reaction": [{"type": "emoji", "emoji": emoji}],
            "user": {"id": actor_id, "is_bot": actor_id == BOT.id, "first_name": "Ai đó"},
        },
    }, bot=BOT)


async def make_app(api):
    app = main.build_app(base_url=api.base_url)
    await app.initialize()
    global BOT
    BOT = app.bot
    print(f"  · premium probe: {await premium.probe(app.bot)}")
    return app


async def run(app, update):
    await app.process_update(update)


def reset():
    main.COOLDOWN.clear()
    main.LAST_GIF.clear()


def react_emojis(api):
    out = []
    for p in api.of("setMessageReaction"):
        rx = p.get("reaction") or []
        if isinstance(rx, str):
            try:
                rx = json.loads(rx)
            except json.JSONDecodeError:
                rx = []
        if rx and isinstance(rx[0], dict):
            out.append(rx[0].get("emoji"))
    return out


# ------------------------------------------------------------------ 1. captcha
async def test_captcha(app, api):
    print("\n[1] Captcha cho người dùng lần đầu")
    captcha.set_enabled(True)
    reset()

    api.clear()
    await run(app, msg_update("/start", user_id=NEW_USER))
    texts = api.texts("sendMessage")
    ok("chặn /start, gửi câu hỏi xác minh",
       any("XÁC MINH" in t for t in texts), str(texts)[:160])
    ok("không lọt vào menu chính",
       not any("bot tiện ích" in t for t in texts), str(texts)[:160])
    ok("câu hỏi CÓ icon premium", count_premium(texts[0] if texts else "") > 0,
       str(count_premium(texts[0] if texts else "")))
    kb = (api.of("sendMessage")[0].get("reply_markup") or "") if api.of("sendMessage") else ""
    ok("có 4 nút đáp án cp_", kb.count("cp_") == 4, str(kb)[:200])
    ok("chưa đánh dấu đã xác minh", not captcha.is_verified(NEW_USER))

    item = captcha.pending(NEW_USER)
    ok("có câu hỏi đang chờ", bool(item))
    wrong = [o for o in item["options"] if int(o) != item["answer"]][0]

    api.clear()
    await run(app, cb_update(f"cp_{item['token']}_{wrong}", user_id=NEW_USER))
    ans = api.texts("answerCallbackQuery")
    ok("bấm sai → báo chưa đúng", any("Chưa đúng" in t for t in ans), str(ans)[:120])
    ok("bấm sai chưa cho qua", not captcha.is_verified(NEW_USER))

    api.clear()
    await run(app, cb_update(f"cp_{item['token']}_{item['answer']}", user_id=NEW_USER))
    edited = api.texts("editMessageText")
    ok("bấm đúng → xác minh thành công",
       any("THÀNH CÔNG" in t for t in edited), str(edited)[:120])
    ok("đã lưu vào danh sách xác minh", captcha.is_verified(NEW_USER))
    ok("xác minh xong thấy menu chính",
       any("bot tiện ích" in t for t in edited), str(edited)[:160])

    api.clear()
    await run(app, msg_update("/help", user_id=NEW_USER))
    ok("lần sau không hỏi lại",
       not any("XÁC MINH" in t for t in api.texts("sendMessage")),
       str(api.texts("sendMessage"))[:160])

    # admin không bị chặn
    api.clear()
    await run(app, msg_update("/help", user_id=ADMIN_ID))
    ok("admin đi qua không bị hỏi",
       not any("XÁC MINH" in t for t in api.texts("sendMessage")))

    # tắt công tắc
    captcha.set_enabled(False)
    api.clear()
    await run(app, msg_update("/help", user_id=888000333))
    ok("tắt captcha → người mới dùng được luôn",
       not any("XÁC MINH" in t for t in api.texts("sendMessage")))
    captcha.set_enabled(True)

    # gửi dồn trong 8 giây -> không nhả thêm câu hỏi (chống spam)
    fresh = 888777666
    api.clear()
    await run(app, msg_update("/start", user_id=fresh))
    first = len(api.of("sendMessage"))
    await run(app, msg_update("/help", user_id=fresh))
    ok("gửi dồn không spam captcha", len(api.of("sendMessage")) == first,
       f"{first} -> {len(api.of('sendMessage'))}")
    ok("vẫn chặn khi chưa giải", not captcha.is_verified(fresh))

    # lệnh admin /captcha
    api.clear()
    await run(app, msg_update("/captcha", user_id=ADMIN_ID))
    ok("/captcha xem trạng thái",
       any("CAPTCHA" in t for t in api.texts("sendMessage")), str(api.texts("sendMessage"))[:100])


# ------------------------------------------------------------------ 2. banner
async def test_banner(app, api):
    print("\n[2] Banner theo tên")
    captcha.set_enabled(False)
    reset()

    api.clear()
    await run(app, msg_update("/banner Khoa An"))
    photos = api.of("sendPhoto")
    ok("gửi ảnh banner", bool(photos), str(api.methods()))
    cap = api.texts("sendPhoto")[0] if photos else ""
    ok("caption có tên", "KHOA AN" in cap, cap[:120])
    ok("caption CÓ icon premium", count_premium(cap) > 0, str(count_premium(cap)))
    ok("ảnh thật được upload", any(n > 3000 for _, n in api.files), str(api.files)[-3:])
    kb = photos[0].get("reply_markup") or "" if photos else ""
    ok("có nút đổi kiểu + kích thước",
       "bn_st_" in kb and "bn_sz_" in kb and "bn_logo" in kb, str(kb)[:200])

    api.clear()
    await run(app, cb_update("bn_st_1"))
    ok("bấm đổi phong cách → vẽ lại ngay",
       bool(api.of("editMessageMedia") or api.of("sendPhoto")), str(api.methods()))

    api.clear()
    await run(app, cb_update("bn_sz_story"))
    ok("bấm đổi kích thước story",
       bool(api.of("editMessageMedia") or api.of("sendPhoto")), str(api.methods()))

    # nhập tên qua hội thoại
    api.clear()
    await run(app, msg_update("/banner"))
    ok("thiếu tên → hỏi lại",
       any("BANNER THEO TÊN" in t for t in api.texts("sendMessage")), str(api.texts("sendMessage"))[:100])
    api.clear()
    await run(app, msg_update("Miu Store"))
    cap2 = api.texts("sendPhoto")[0] if api.of("sendPhoto") else ""
    ok("gửi tên → ra banner mới", "MIU STORE" in cap2, cap2[:120])

    api.clear()
    await run(app, cb_update("bn_logo"))
    ok("bật dùng avatar làm logo", main.BANNER_STATE[USER_ID]["logo"] is True)
    ok("avatar dùng được khi vẽ banner",
       bool(api.of("editMessageMedia") or api.of("sendPhoto")), str(api.methods()))

    # render đủ 4 phong cách
    good = 0
    for style in range(4):
        buf = banner.render("Test Name", style, "banner")
        if len(buf.getvalue()) > 5000:
            good += 1
    ok("render đủ 4 phong cách", good == 4, str(good))


# ------------------------------------------------------------------ 3. reaction
async def test_reaction(app, api):
    print("\n[3] Bot tự thả reaction theo nội dung")
    reaction.set_enabled(True)
    reset()

    api.clear()
    await run(app, msg_update("cảm ơn bạn nhiều nhé"))
    emojis = react_emojis(api)
    ok("“cảm ơn” → cảm xúc biết ơn", emojis and emojis[-1] in ("🙏", "❤", "🥰"), str(emojis))

    api.clear()
    await run(app, msg_update("haha vui quá trời"))
    emojis = react_emojis(api)
    ok("“haha” → cười", emojis and emojis[-1] == "😁", str(emojis))

    api.clear()
    await run(app, msg_update("zzz qwerty asdfgh"))
    ok("câu vô nghĩa → không thả gì", not react_emojis(api), str(react_emojis(api)))

    api.clear()
    await run(app, msg_update("buồn quá, chán quá"))
    emojis = react_emojis(api)
    ok("“buồn” → mặt buồn", emojis and emojis[-1] in ("😢", "😨"), str(emojis))

    api.clear()
    await run(app, msg_update("gửi bạn link này https://tiktok.com/@a/video/1"))
    emojis = react_emojis(api)
    ok("gửi link → 🔥", emojis and emojis[-1] == "🔥", str(emojis))

    api.clear()
    await run(app, msg_update("hôm nay ăn gì?"))
    emojis = react_emojis(api)
    ok("câu hỏi → 🤔", emojis and emojis[-1] == "🤔", str(emojis))

    # Telegram từ chối emoji -> tự lùi về 👍
    api.clear()
    api.reject = {"🔥"}
    await run(app, msg_update("lửa cháy quá"))
    emojis = react_emojis(api)
    ok("emoji bị chặn → tự lùi về 👍", emojis and emojis[-1] == "👍", str(emojis))
    api.reject = set()

    # tắt công tắc
    reaction.set_enabled(False)
    api.clear()
    await run(app, msg_update("cảm ơn bạn"))
    ok("tắt reaction → im lặng", not react_emojis(api))
    reaction.set_enabled(True)

    # bot tự thả KHÔNG được xoá tin của user
    api.clear()
    await run(app, reaction_update("👍", actor_id=BOT.id))
    ok("bot tự thả → không xoá tin user", not api.of("deleteMessage"), str(api.methods()))

    # kể cả update không kèm actor, tin bot vừa thả vẫn không bị xoá
    api.clear()
    await run(app, msg_update("cảm ơn nhé"))
    await run(app, reaction_update("🙏", actor_id=BOT.id, message_id=9))
    ok("tin bot vừa thả không bị xoá oan", not api.of("deleteMessage"), str(api.methods()))

    api.clear()
    await run(app, reaction_update("👍", actor_id=USER_ID))
    ok("người dùng thả → vẫn xoá như cũ", bool(api.of("deleteMessage")), str(api.methods()))

    # lệnh admin
    api.clear()
    await run(app, msg_update("/react", user_id=ADMIN_ID))
    ok("/react xem trạng thái",
       any("TỰ THẢ REACTION" in t for t in api.texts("sendMessage")), str(api.texts("sendMessage"))[:100])


# ------------------------------------------------------------------ 4. /id
async def test_id(app, api):
    print("\n[4] /id — avatar + tiểu sử")
    captcha.set_enabled(False)
    reset()
    target = {"id": FakeAPI.USER_ID, "is_bot": False, "first_name": "Khoa An",
              "last_name": "Nguyen", "username": "chiencuto18m"}

    api.clear()
    await run(app, msg_update("/id", reply_from=target))
    ok("reply → gửi ảnh đại diện", bool(api.of("sendPhoto")), str(api.methods()))
    cap = api.texts("sendPhoto")[0] if api.of("sendPhoto") else ""
    ok("có User ID", str(FakeAPI.USER_ID) in cap, cap[:200])
    ok("có TIỂU SỬ (lỗi cũ thiếu)", "làm bot" in cap and "Sài Gòn" in cap, cap[:260])
    ok("có username", "chiencuto18m" in cap, cap[:200])
    ok("caption CÓ icon premium", count_premium(cap) > 0, str(count_premium(cap)))
    ok("ảnh avatar thật được upload", any(n > 100 for _, n in api.files), str(api.files[-2:]))

    api.clear()
    await run(app, msg_update("/id chiencuto18m"))
    cap2 = api.texts("sendPhoto")[0] if api.of("sendPhoto") else ""
    ok("@username → lấy được hồ sơ", "chiencuto18m" in cap2, str(api.methods())[:140])
    ok("username cũng có avatar", bool(api.of("sendPhoto")))

    api.clear()
    await run(app, msg_update("/id 555000111"))
    cap3 = api.texts("sendPhoto")[0] if api.of("sendPhoto") else ""
    ok("ID số → lấy được hồ sơ", str(FakeAPI.USER_ID) in cap3, str(api.methods())[:140])

    api.clear()
    await run(app, msg_update("/id @khongco"))
    msg = " ".join(api.texts("sendMessage"))
    ok("@không tồn tại → báo rõ + gợi ý reply",
       "Không tìm thấy" in msg and "reply" in msg.lower(), msg[:160])

    api.clear()
    await run(app, msg_update("/id"))
    cap5 = api.texts("sendPhoto")[0] if api.of("sendPhoto") else ""
    ok("không tham số → hồ sơ của chính mình",
       str(USER_ID) in cap5 and "Chính bạn" in cap5, cap5[:200])


# ------------------------------------------------------------------ main
async def main_async():
    api = FakeAPI()
    await api.start()
    app = await make_app(api)
    try:
        await test_captcha(app, api)
        await test_banner(app, api)
        await test_reaction(app, api)
        await test_id(app, api)
    finally:
        await app.shutdown()
        await api.stop()

    passed = sum(1 for _, c, _ in RESULTS if c)
    print("\n" + "=" * 58)
    print(f"KẾT QUẢ: {passed}/{len(RESULTS)} test đạt")
    if passed != len(RESULTS):
        print("TEST FAIL:")
        for name, cond, detail in RESULTS:
            if not cond:
                print(f"  ❌ {name}  {detail}")
    print("=" * 58)


if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)
    sys.exit(0 if all(c for _, c, _ in RESULTS) else 1)

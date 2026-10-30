#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test end-to-end v16: /id theo username · captcha sau 10 lệnh · war nhanh ·
/cover đổi tên + bìa · /fb ID + avatar · panel giọng AI.

Chạy: ../.venv-v16/bin/python tests/test_v16.py
"""

import asyncio
import io
import os
import subprocess
import sys
import tempfile
import time
import types

from pathlib import Path

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))

TMP = tempfile.mkdtemp(prefix="khoaan-v16-")
os.environ.update({
    "BOT_TOKEN": "123456:TEST-TOKEN",
    "DATA_DIR": os.path.join(TMP, "data"),
    "TMP_DIR": os.path.join(TMP, "tmp"),
    "PORT": "0",
    "TZ": "Asia/Ho_Chi_Minh",
    "GIF_REPLY": "0",
    "CAPTCHA_EVERY": "10",
})
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)
os.makedirs(os.environ["TMP_DIR"], exist_ok=True)

from telegram import Update  # noqa: E402

import config  # noqa: E402
import main  # noqa: E402
from core import captcha, cover, facebook, premium, tts, userindex, war  # noqa: E402
from fake_api import FakeAPI  # noqa: E402

USER_ID = 555000111
OTHER_ID = 666000222
ADMIN_ID = config.ROOT_ADMIN
GROUP = -1001234567890
CHAT_TITLE = "Miu Store • Anime"
BOT = None
RESULTS = []


def ok(name, cond=True, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  → {detail}" if detail and not cond else ""))


# ------------------------------------------------------------------ update builders
def _msg(text=None, user_id=USER_ID, chat_id=None, chat_type="private", title=None,
         extra=None, reply_to=None, caption=None):
    mid = int(time.time() * 1000) % 1000000
    chat = {"id": chat_id or user_id, "type": chat_type}
    if title:
        chat["title"] = title
    msg = {
        "message_id": mid,
        "date": 1730000000,
        "chat": chat,
        "from": {"id": user_id, "is_bot": False, "first_name": "Khoa An",
                 "username": "chiencuto18m"},
    }
    if text:
        msg["text"] = text
        if text.startswith("/"):
            msg["entities"] = [{"type": "bot_command", "offset": 0,
                                "length": len(text.split()[0])}]
    if caption:
        msg["caption"] = caption
    if reply_to:
        msg["reply_to_message"] = reply_to
    if extra:
        msg.update(extra)
    return {"update_id": mid, "message": msg}


def upd(payload):
    return Update.de_json(payload, bot=BOT)


def private(text, user_id=USER_ID):
    return upd(_msg(text, user_id=user_id))


def cb(data, user_id=USER_ID):
    mid = int(time.time() * 1000) % 1000000
    return upd({
        "update_id": mid,
        "callback_query": {
            "id": str(mid),
            "from": {"id": user_id, "is_bot": False, "first_name": "Khoa An",
                     "username": "chiencuto18m"},
            "chat_instance": "1",
            "data": data,
            "message": {
                "message_id": 555, "date": 1730000000,
                "chat": {"id": user_id, "type": "private"},
                "from": {"id": 7770001, "is_bot": True, "first_name": "Bot",
                         "username": "khoaan_test_bot"},
                "text": "bảng",
            },
        },
    })


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
    api_calls_clear()


API = None


def api_calls_clear():
    if API is not None:
        API.clear()


# ------------------------------------------------------------------ 1. /id
def test_id_parse():
    print("\n[1] Đọc ID/username từ nhiều kiểu người dùng dán vào")
    ok("số thuần", main._uid_from_text("100012345678") == 100012345678)
    ok("link profile.php", main._uid_from_text(
        "https://www.facebook.com/profile.php?id=100012345678") == 100012345678)
    ok("t.me/abc/123 không phải user id", main._uid_from_text("https://t.me/miustore/123") is None)
    ok("tg://user?id=", main._uid_from_text("tg://user?id=999888777") == 999888777)
    ok("@username → username", main._uname_from_text("@chiencuto18m") == "chiencuto18m")
    ok("t.me/username", main._uname_from_text("https://t.me/chiencuto18m") == "chiencuto18m")
    ok("gõ trần username", main._uname_from_text("chiencuto18m") == "chiencuto18m")


async def test_id_username(app, api):
    print("\n[2] /id @username khi bot CHƯA từng gọi API cho người đó")
    reset()
    # người dùng 555000111 nhắn 1 câu -> bot ghi vào sổ
    await run(app, private("/ping", USER_ID))
    got = userindex.find_username("chiencuto18m")
    ok("bot ghi username vào sổ", got == USER_ID, str(got))

    # Telegram API "không tra được" username này -> phải lấy từ sổ
    api.unknown_usernames = {"chiencuto18m"}
    api.clear()
    await run(app, private("/id @chiencuto18m", OTHER_ID))
    texts = api.texts() + api.texts("sendPhoto")
    joined = "\n".join(texts)
    ok("không báo 'Không tìm thấy' nữa", "Không tìm thấy" not in joined, joined[:200])
    ok("trả đúng User ID", str(USER_ID) in joined, joined[:300])
    ok("ghi nguồn lấy ID", "sổ" in joined.lower(), joined[:300])

    # người lạ hoàn toàn -> gợi ý cách làm + không crash
    api.unknown_usernames = {"rayzorw"}
    api.clear()
    await run(app, private("/id @rayzorw", OTHER_ID))
    joined = "\n".join(api.texts())
    ok("username lạ: nói rõ vì sao không tra được", "không cho bot tra ID" in joined, joined[:300])
    ok("username lạ: chỉ cách reply", "Reply" in joined, joined[:300])

    # ID số -> có hồ sơ
    api.unknown_usernames = set()
    api.clear()
    await run(app, private("/id 555000111", OTHER_ID))
    joined = "\n".join(api.texts() + api.texts("sendPhoto"))
    ok("/id theo số vẫn chạy", str(USER_ID) in joined, joined[:200])

    # reply tin nhắn
    api.clear()
    reply = _msg("hello", user_id=USER_ID)["message"]
    await run(app, upd(_msg("/id", OTHER_ID, reply_to=reply)))
    joined = "\n".join(api.texts() + api.texts("sendPhoto"))
    ok("/id reply tin nhắn vẫn chạy", str(USER_ID) in joined, joined[:200])


# ------------------------------------------------------------------ 2. status/sleep ẩn
async def test_admin_only(app, api):
    print("\n[3] /status · /sleep chỉ dành cho admin")
    reset()
    api.clear()
    await run(app, private("/status", OTHER_ID))
    joined = "\n".join(api.texts())
    ok("user thường bị chặn /status", "không dành cho bạn" in joined, joined[:200])
    ok("/status không render ảnh cho user", not api.of("sendPhoto"), str(api.of("sendPhoto"))[:120])

    api.clear()
    await run(app, private("/sleep 23:00 06:00", OTHER_ID))
    joined = "\n".join(api.texts())
    ok("user thường bị chặn /sleep", "không dành cho bạn" in joined, joined[:200])

    api.clear()
    await run(app, private("/status", ADMIN_ID))
    ok("admin xem được /status (render ảnh)", bool(api.of("sendPhoto")),
       str(api.methods())[:200])


# ------------------------------------------------------------------ 3. captcha 10 lệnh
async def test_captcha_after_10(app, api):
    print("\n[4] Dùng 10 lệnh -> bắt xác minh chống spam")
    reset()
    captcha.set_enabled(True)
    uid = 777111333
    captcha.mark(uid, "Khoa An")          # đã xác minh lần đầu
    captcha.reset_usage(uid)
    api.clear()
    asked = False
    for i in range(10):
        api.clear()
        await run(app, private("/ping", uid))
        joined = "\n".join(api.texts())
        if "CHỐNG SPAM" in joined:
            asked = True
            ok(f"tới lệnh thứ {i + 1} thì hỏi captcha", i + 1 == 10, f"hỏi ở lệnh {i + 1}")
            break
    ok("có bắt xác minh sau 10 lệnh", asked)

    # đang bị chặn thì lệnh khác không chạy (không có PONG)
    api.clear()
    await run(app, private("/ping", uid))
    joined = "\n".join(api.texts())
    ok("đang chờ captcha thì lệnh bị chặn", "PONG" not in joined and captcha.requires_verify(uid),
       joined[:200])

    # giải đúng -> đếm lại từ 0, dùng được tiếp
    item = captcha.pending(uid)
    api.clear()
    await run(app, cb(f"cp_{item['token']}_{item['answer']}", uid))
    ok("giải đúng thì mở khoá", captcha.usage_of(uid) == 0 and not captcha.requires_verify(uid),
       f"usage={captcha.usage_of(uid)}")
    api.clear()
    await run(app, private("/ping", uid))
    ok("mở khoá xong /ping chạy lại", bool(api.of("sendMessage")), str(api.methods())[:120])
    captcha.set_enabled(False)


# ------------------------------------------------------------------ 4. war tốc độ
def test_war_interval():
    print("\n[5] War/spam chỉnh được giãn cách")
    ok("0.5 → 0.5s", war.parse_interval("0.5") == 0.5)
    ok("0.5s → 0.5s", war.parse_interval("0.5s") == 0.5)
    ok("800ms → 0.8s", abs(war.parse_interval("800ms") - 0.8) < 1e-6)
    ok("1,2s → 1.2s", abs(war.parse_interval("1,2s") - 1.2) < 1e-6)
    ok("dưới sàn bị kéo lên 0.4", war.parse_interval("0.1") == war.MIN_INTERVAL)
    ok("rác → None", war.parse_interval("abc") is None)

    upd_obj = types.SimpleNamespace(effective_message=None)
    ctx = types.SimpleNamespace(args=["@user", "30", "0.5"])
    mention, count, interval = main._target_and_count(upd_obj, ctx, war.DEFAULT_WAR)
    ok("đọc @user 30 0.5", (mention, count, interval) == ("@user", 30, 0.5),
       f"{mention},{count},{interval}")
    ctx = types.SimpleNamespace(args=["20", "800ms"])
    mention, count, interval = main._target_and_count(upd_obj, ctx, war.DEFAULT_SPAM)
    ok("đọc 20 800ms", (count, interval) == (20, 0.8), f"{count},{interval}")
    ctx = types.SimpleNamespace(args=["@user", "30"])
    mention, count, interval = main._target_and_count(upd_obj, ctx, war.DEFAULT_WAR)
    ok("không nhập giãn cách → 1s mặc định", interval == 1.0, str(interval))


# ------------------------------------------------------------------ 5. cover
def _make_mp3(path: str) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=0.4", "-q:a", "9", "-y", path],
        check=True,
    )


def test_cover():
    print("\n[6] /cover — ghi tên + nghệ sĩ + ảnh bìa vào file nhạc")
    mp3 = os.path.join(TMP, "song.mp3")
    _make_mp3(mp3)
    data = Path(mp3).read_bytes()
    ok("nhận ra MP3", cover.detect("song.mp3", data) == "mp3", cover.detect("song.mp3", data))
    ok("file mp3 thật (>2KB)", len(data) > 2000, str(len(data)))

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (500, 500), (30, 120, 220)).save(buf, "PNG")
    pic = buf.getvalue()

    out, ext = cover.apply(data, "Tắt Đèn Đi", "Khoa An", pic, filename="song.mp3")
    ok("ghi xong vẫn là mp3", ext == "mp3" and out[:3] == b"ID3", out[:6].hex())
    info = cover.probe(out, "song.mp3")
    ok("đọc lại đúng tên bài", info["title"] == "Tắt Đèn Đi", info["title"])
    ok("đọc lại đúng nghệ sĩ", info["artist"] == "Khoa An", info["artist"])
    ok("ảnh bìa nhúng vào file", len(info["cover"]) > 1000, str(len(info["cover"])))
    ok("file vẫn còn tiếng (dung lượng > 2KB)", len(out) > 2000, str(len(out)))
    ok("tên file an toàn (bỏ ký tự lạ)", cover.safe_filename("Tắt Đèn Đi / Khoa An") == "Tắt Đèn Đi Khoa An",
       cover.safe_filename("Tắt Đèn Đi / Khoa An"))

    # m4a cũng phải chạy
    m4a = os.path.join(TMP, "song.m4a")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "sine=frequency=330:duration=0.3", "-y", m4a], check=True)
    d2 = Path(m4a).read_bytes()
    try:
        out2, ext2 = cover.apply(d2, "Bài Mới", "Khoa An", pic, filename="song.m4a")
        ok("M4A ghi được", ext2 == "m4a" and len(out2) > 1000, str(len(out2)))
        ok("M4A đọc lại đúng tên", cover.probe(out2, "song.m4a")["title"] == "Bài Mới")
    except cover.CoverError as e:
        ok("M4A ghi được", False, str(e))

    # args tách tên | nghệ sĩ
    info = {"title": "Cũ", "artist": "Ai Đó"}
    ok("tách 'Tên | NS'", main._cover_args("Tắt Đèn | Khoa An", info) == ("Tắt Đèn", "Khoa An"))
    ok("tách 'Tên - NS'", main._cover_args("Tắt Đèn - Khoa An", info) == ("Tắt Đèn", "Khoa An"))
    ok("chỉ tên → nghệ sĩ mặc định là chủ bot",
       main._cover_args("Tắt Đèn", info)[1] == config.OWNER_NAME,
       main._cover_args("Tắt Đèn", info)[1])
    ok("không nhập → giữ tên cũ", main._cover_args("", info)[0] == "Cũ")


# ------------------------------------------------------------------ 6. facebook
def test_fb_parse():
    print("\n[7] /fb — đọc mọi kiểu link Facebook")
    cases = {
        "https://www.facebook.com/profile.php?id=100012345678": ("id", "100012345678"),
        "https://web.facebook.com/people/Khoa-An/100087654321/": ("id", "100087654321"),
        "https://www.facebook.com/AnDoanx": ("username", "AnDoanx"),
        "AnDoanx": ("username", "AnDoanx"),
        "@AnDoanx": ("username", "AnDoanx"),
        "100012345678": ("id", "100012345678"),
    }
    for raw, (kind, want) in cases.items():
        got = facebook.parse(raw)
        val = got["id"] if kind == "id" else got["username"]
        ok(f"{raw[:46]} → {kind}", val == want, str(got))

    info = {"ok": True, "id": "100012345678", "name": "Khoa An", "username": "AnDoanx",
            "how": "có sẵn ID trong link"}
    t = main._fb_text(info)
    ok("caption có ID", "100012345678" in t)
    ok("caption có link hồ sơ", "facebook.com/100012345678" in t)
    kb = main._fb_kb(info)
    labels = [b.text for row in kb.inline_keyboard for b in row]
    ok("có nút Sao chép ID", any(("Sao chép" in x or "Copy" in x) for x in labels), str(labels))


async def test_fb_cmd(app, api):
    print("\n[8] /fb trong chat — lấy ID thật, avatar tải thật")
    reset()
    api.clear()
    await run(app, private("/fb https://www.facebook.com/profile.php?id=100012345678"))
    joined = "\n".join(api.texts() + api.texts("sendPhoto") + api.texts("editMessageText"))
    ok("trả về ID", "100012345678" in joined, joined[:300])
    ok("gửi card có ảnh hoặc chữ", bool(api.of("sendPhoto") or api.of("sendMessage")),
       str(api.methods())[:160])

    api.clear()
    await run(app, private("/fb"))
    ok("gõ /fb không tham số → hướng dẫn", "FACEBOOK" in "\n".join(api.texts()))


# ------------------------------------------------------------------ 7. voice panel
async def test_voice_panel(app, api):
    print("\n[9] /voice — nhiều giọng AI + tốc độ")
    reset()
    api.clear()
    await run(app, private("/voice"))
    msg = api.of("sendMessage")
    ok("mở được bảng chọn giọng", bool(msg), str(api.methods())[:160])
    text = str(msg[0].get("text") if msg else "")
    kb = str(msg[0].get("reply_markup") if msg else "")
    ok("bảng có nhóm tiếng Việt", "Tiếng Việt" in text, text[:200])
    ok("bảng có nhóm AI đa ngữ", "vc:g:ai" in kb, kb[:300])
    ok("có nút CapCut", "cc_nu" in kb, kb[:300])

    api.clear()
    await run(app, cb("vc:nu"))
    ok("chọn giọng nữ được lưu", main.USER_VOICE.get(USER_ID) == "nu",
       str(main.USER_VOICE.get(USER_ID)))

    api.clear()
    await run(app, cb("vc:s:nhanh"))
    ok("chọn tốc độ nhanh", main.USER_SPEED.get(USER_ID) == "nhanh",
       str(main.USER_SPEED.get(USER_ID)))

    api.clear()
    await run(app, cb("vc:g:en"))
    ok("đổi nhóm sang English", main.VOICE_GROUP.get(USER_ID) == "en", str(main.VOICE_GROUP.get(USER_ID)))

    api.clear()
    await run(app, cb("vc:all"))
    text = "\n".join(api.texts() + api.texts("editMessageText"))
    ok("mở được danh sách tất cả giọng", "TẤT CẢ GIỌNG AI" in text, text[:200])


async def test_tts_voice(app, api):
    print("\n[10] /tts dùng đúng giọng đã chọn + đổi giọng ngay trong lệnh")
    reset()
    main.USER_VOICE[USER_ID] = "nu"
    main.USER_SPEED[USER_ID] = "vua"
    told = {}

    async def fake_tts(text, voice_key="nam", speed="vua"):
        told.update(text=text, voice=voice_key, speed=speed)
        return b"ID3" + b"\x00" * 4000, voice_key, "Nữ · mềm mại"

    original = tts.text_to_speech
    tts.text_to_speech = fake_tts
    try:
        api.clear()
        await run(app, private("/tts xin chào các bạn"))
        ok("dùng giọng đã chọn (nu)", told.get("voice") == "nu", str(told))
        ok("gửi voice thật", bool(api.of("sendVoice")), str(api.methods())[:160])
        cap = (api.of("sendVoice") or [{}])[0].get("caption", "")
        ok("caption có tên giọng", "Nữ" in str(cap), str(cap)[:120])

        told.clear()
        api.clear()
        await run(app, private("/tts en hello world"))
        ok("đổi giọng ngay trong lệnh", told.get("voice") == "en", str(told))
        ok("nội dung không dính tên giọng", told.get("text") == "hello world", str(told))
    finally:
        tts.text_to_speech = original


# ------------------------------------------------------------------ 8. help
def test_help_text():
    print("\n[11] Menu/help: đã bỏ card chào & giờ ngủ khỏi mục người dùng")
    h = main.HELP_TEXT
    ok("help không còn /welcome", "/welcome" not in h)
    ok("help không còn /goodbye", "/goodbye" not in h)
    ok("help không còn /sleep", "/sleep" not in h)
    ok("help không còn /status", "/status" not in h)
    ok("help có /cover", "/cover" in h)
    ok("help có /fb", "/fb" in h)
    ok("help có mẹo chỉnh tốc độ war", "0.5" in h)
    ok("help nhắc captcha 10 lệnh", "10 lệnh" in h)
    d = main.dashboard("Khoa An")
    ok("dashboard không còn card chào", "Card chào" not in d)
    ok("dashboard có Cover + Facebook", "/cover" in d and "/fb" in d)
    kb = main.main_menu(False)
    labels = [b.text for row in kb.inline_keyboard for b in row]
    ok("menu user không có Giờ ngủ", not any("Giờ ngủ" in x for x in labels), str(labels))
    ok("menu user không có Card", not any("Card" in x for x in labels), str(labels))
    # v18: nút có emoji THUONG phía trước (bỏ icon premium)
    ok("menu user gộp nút Tải xuống",
       any(x.strip().endswith("Tải xuống") for x in labels)
       and not any(x in ("YouTube", "Instagram", "Facebook") for x in labels), str(labels))
    kb_adm = main.main_menu(True)
    labels = [b.text for row in kb_adm.inline_keyboard for b in row]
    ok("menu admin có Giờ ngủ", any("Giờ ngủ" in x for x in labels), str(labels))


# ------------------------------------------------------------------ 9. lệnh đã bỏ
async def test_removed_commands(app, api):
    print("\n[12] /welcome và /goodbye đã bỏ hẳn")
    reset()
    api.clear()
    await run(app, private("/welcome"))
    joined = "\n".join(api.texts())
    ok("/welcome không còn phản hồi card", "CARD" not in joined and "welcome" not in joined.lower(),
       joined[:200])
    api.clear()
    await run(app, private("/goodbye"))
    ok("/goodbye không còn phản hồi", not api.of("sendMessage"), str(api.texts())[:200])
    leftover = {c for c in main.BOT_COMMANDS if c in ("welcome", "goodbye", "chaomung", "tambiet")}
    ok("BOT_COMMANDS không còn welcome/goodbye", not leftover, str(leftover))


# ------------------------------------------------------------------ main
async def amain():
    global API
    api = FakeAPI()
    await api.start()
    API = api
    app = await make_app(api)
    captcha.set_enabled(False)
    try:
        test_id_parse()
        await test_id_username(app, api)
        await test_admin_only(app, api)
        await test_captcha_after_10(app, api)
        test_war_interval()
        test_cover()
        test_fb_parse()
        await test_fb_cmd(app, api)
        await test_voice_panel(app, api)
        await test_tts_voice(app, api)
        test_help_text()
        await test_removed_commands(app, api)
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
            print(f"  ❌ {name}  {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))

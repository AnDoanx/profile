#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test end-to-end thật: dựng Application như main.py, chặn đúng tầng HTTP của Telegram.

Chạy: .venv-test/bin/python tests/test_v10.py
"""

import asyncio
import os
import re
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))

TMP = tempfile.mkdtemp(prefix="khoaan-test-")
os.environ.update({
    "BOT_TOKEN": "123456:TEST-TOKEN",
    "DATA_DIR": os.path.join(TMP, "data"),
    "TMP_DIR": os.path.join(TMP, "tmp"),
    "GIF_MIN_GAP": "0",
    "PORT": "0",
    "TZ": "Asia/Ho_Chi_Minh",
})
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)
os.makedirs(os.environ["TMP_DIR"], exist_ok=True)

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton  # noqa: E402
from telegram.ext import Application  # noqa: E402

import config  # noqa: E402
import main  # noqa: E402
from core import captcha, downloader, gifpool, premium, settings  # noqa: E402
from fake_api import BOT_USERNAME, FakeAPI  # noqa: E402

ADMIN_ID = int(config.ROOT_ADMIN)
BOT = None  # set sau khi Application khởi tạo
USER_ID = 555000111
RESULTS = []


def ok(name, cond=True, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  → {detail}" if detail and not cond else ""))


EMOJI_RE = re.compile(r"(<tg-emoji[^>]*>.*?</tg-emoji>)|([\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF])", re.S)


def unwrapped_emoji(text: str) -> list[str]:
    """Emoji nào chưa được bọc thẻ premium (bỏ qua emoji nằm trong <code>)."""
    bad = []
    for m in EMOJI_RE.finditer(text):
        if m.group(1):
            continue
        bad.append(m.group(2))
    return bad


def count_premium(text: str) -> int:
    return text.count("<tg-emoji")


def html_balanced(text: str) -> bool:
    """Mọi thẻ HTML (kể cả <tg-emoji>) đóng mở đúng cặp — không để Telegram vỡ caption."""
    from html.parser import HTMLParser

    class Chk(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []
            self.err = False

        def handle_starttag(self, tag, attrs):
            self.stack.append(tag)

        def handle_endtag(self, tag):
            if not self.stack or self.stack.pop() != tag:
                self.err = True

    c = Chk()
    c.feed(text)
    return not c.err and not c.stack


# ------------------------------------------------------------------ update builder
def msg_update(text, user_id=USER_ID, chat_type="private", is_bot=False, chat_id=None,
               message_id=None):
    mid = message_id or int(time.time() * 1000) % 1000000
    return Update.de_json({
        "update_id": mid,
        "message": {
            "message_id": mid,
            "date": 1730000000,
            "text": text,
            "chat": {"id": chat_id or user_id, "type": chat_type},
            "from": {"id": user_id, "is_bot": is_bot, "first_name": "Khoa An",
                     "username": "chiencuto18m"},
            "entities": ([{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
                         if text.startswith("/") else []),
        },
    }, bot=BOT)


def callback_update(data, from_bot_id, user_id=USER_ID, chat_id=None, message_id=555):
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
                "from": {"id": from_bot_id, "is_bot": True, "first_name": "Bot",
                         "username": BOT_USERNAME},
                "text": "nút",
            },
        },
    }, bot=BOT)


# ------------------------------------------------------------------ setup
async def make_app(api: FakeAPI):
    captcha.set_enabled(False)                    # v11: tắt captcha, test các tính năng cũ
    app = main.build_app(base_url=api.base_url)   # đăng ký handler THẬT như lúc chạy
    await app.initialize()
    global BOT
    BOT = app.bot
    st = await premium.probe(app.bot)
    print(f"  · premium probe: {st}")
    return app


async def run(app, update):
    await app.process_update(update)


# ------------------------------------------------------------------ fixtures
GIF_BYTES = None


async def get_gif_bytes():
    global GIF_BYTES
    if GIF_BYTES:
        return GIF_BYTES
    from core import http
    for q in gifpool.QUERIES:
        links = await gifpool._tenor(q)  # noqa: SLF001
        for link in links[:3]:
            raw = await http.download_bytes(link, max_bytes=5 * 1024 * 1024, timeout=15)
            if raw:
                GIF_BYTES = raw
                return raw
    raise RuntimeError("không lấy được gif chiikawa từ tenor")


def patch_gif(raw):
    async def _pick_bytes(timeout: float = 3.0):
        return raw
    gifpool.pick_bytes = _pick_bytes  # noqa: SLF001


def reset_gif_state():
    main.LAST_GIF.clear()
    main.GIF_FLOOD_UNTIL.clear()
    main.COOLDOWN.clear()          # bỏ chặn 1.2s giữa các test
    gifpool._file_ids.clear()  # noqa: SLF001


# ------------------------------------------------------------------ tests
async def test_getinfo(app, api, gif):
    """/getinfo: caption ảnh phải CÓ icon premium (đây là lỗi user báo)."""
    print("\n[1] /getinfo — icon premium trong kết quả")
    api.clear()
    reset_gif_state()

    async def fake_newapi(user):
        return {
            "user": {
                "nickname": "Khoa An", "uniqueId": user, "id": "6999999999999999999",
                "signature": "làm bot & web", "region": "VN", "verified": True,
                "privateAccount": False, "ttSeller": True, "createTime": 1600000000,
                "avatarLarger": "https://example.com/a.jpg",
            },
            "stats": {"followerCount": 1234, "followingCount": 12, "videoCount": 56,
                      "heartCount": 7890},
        }, ""

    orig_newapi, orig_region = main._info_newapi, main._region_for
    orig_dl = main.http.download_bytes
    main._info_newapi = fake_newapi

    async def fake_region(user, u):
        return "VI"

    async def fake_dl(url, **kw):
        return b"\xff\xd8\xff" + b"0" * 400

    main._region_for = fake_region
    main.http.download_bytes = fake_dl
    try:
        await run(app, msg_update("/getinfo khoaan"))
    finally:
        main._info_newapi, main._region_for = orig_newapi, orig_region
        main.http.download_bytes = orig_dl

    caps = api.texts("sendPhoto")
    ok("gửi ảnh avatar kèm caption", len(caps) == 1, f"sendPhoto={api.methods()}")
    cap = caps[0] if caps else ""
    ok("caption CÓ thẻ <tg-emoji>", count_premium(cap) > 0, f"{count_premium(cap)} icon premium")
    plain = unwrapped_emoji(cap)
    ok("không còn emoji thường", not plain, f"emoji thường: {plain}")
    ok("nội dung info còn nguyên", "TIKTOK PROFILE STATS" in cap and "Khoa An" in cap)
    ok("HTML cân thẻ (không vỡ caption)", html_balanced(cap), cap[:120])
    ok("gif đứng trước ảnh", api.methods().index("sendAnimation") < api.methods().index("sendPhoto")
       if "sendAnimation" in api.methods() else False, str(api.methods()))


async def test_tiktok_video(app, api, gif):
    """Tải video TikTok: caption video phải CÓ icon premium."""
    print("\n[2] /dl — caption video TikTok CÓ icon premium")
    api.clear()
    reset_gif_state()
    path = os.path.join(TMP, "v.mp4")
    with open(path, "wb") as f:
        f.write(b"\x00\x00\x00\x18ftypmp42" + b"0" * 2000)

    orig = downloader.download

    async def fake_download(url, kind="video"):
        return downloader.DlResult(items=[downloader.MediaItem(
            path=path, kind="video", title="Video test <đẹp>", uploader="khoaan",
            duration=12, views=9999, url=url,
        )])

    downloader.download = fake_download
    try:
        await run(app, msg_update("/dl https://www.tiktok.com/@khoaan/video/123456"))
    finally:
        downloader.download = orig

    caps = api.texts("sendVideo")
    ok("gửi video", len(caps) == 1, str(api.methods()))
    cap = caps[0] if caps else ""
    ok("caption video có premium", count_premium(cap) > 0, f"{count_premium(cap)} icon")
    ok("caption video sạch emoji thường", not unwrapped_emoji(cap), str(unwrapped_emoji(cap)))
    ok("escape HTML vẫn đúng", "&lt;đẹp&gt;" in cap)
    ok("caption video HTML cân thẻ", html_balanced(cap), cap[:120])


async def test_ping(app, api, gif):
    print("\n[3] /ping")
    api.clear()
    reset_gif_state()
    await run(app, msg_update("/ping"))
    msgs = api.texts("sendMessage") + api.texts("editMessageText")
    joined = "\n".join(msgs)
    ok("có chữ PONG", "PONG" in joined, joined[:120])
    ok("hiện độ trễ ms", re.search(r"\b\d+ ms\b", joined) is not None)
    ok("hiện uptime", ("Uptime" in joined) or ("Bot chạy" in joined))
    ok("icon premium trong /ping", count_premium(joined) > 0, str(count_premium(joined)))
    ok("gif trước câu trả lời", api.methods()[:1] == ["sendAnimation"], str(api.methods()))


async def test_gif_gating(app, api, gif):
    print("\n[4] Gif chỉ chạy cho lệnh / nút của bot này")

    async def gif_count(txt_update):
        api.clear()
        reset_gif_state()
        await run(app, txt_update)
        return api.methods().count("sendAnimation")

    n = await gif_count(msg_update("xin chào bạn ơi"))
    ok("tin nhắn thường -> KHÔNG gif", n == 0, f"gif={n}")

    n = await gif_count(msg_update("/start@other_bot"))
    ok("lệnh của bot khác (@other_bot) -> KHÔNG gif", n == 0, f"gif={n}")

    n = await gif_count(msg_update("/help", user_id=999111, is_bot=True))
    ok("tin từ bot khác -> KHÔNG gif", n == 0, f"gif={n}")

    n = await gif_count(msg_update("/status"))
    ok("lệnh của bot (/status) -> CÓ gif", n == 1, f"gif={n}")

    n = await gif_count(msg_update("/start@" + BOT_USERNAME))
    ok("lệnh @đúng bot -> CÓ gif", n == 1, f"gif={n}")

    api.clear()
    reset_gif_state()
    await run(app, callback_update("p_home", from_bot_id=api_bot_id(app)))
    ok("bấm nút của bot -> CÓ gif", api.methods().count("sendAnimation") == 1, str(api.methods()))

    api.clear()
    reset_gif_state()
    await run(app, callback_update("p_home", from_bot_id=424242))
    ok("nút của bot khác -> KHÔNG gif", api.methods().count("sendAnimation") == 0, str(api.methods()))


def api_bot_id(app):
    return app.bot.id


async def test_gif_admin_toggle(app, api, gif):
    print("\n[5] /gif on | off (admin)")
    api.clear()
    reset_gif_state()
    await run(app, msg_update("/gif off", user_id=ADMIN_ID))
    msgs = "\n".join(api.texts("sendMessage") + api.texts("editMessageText"))
    ok("admin tắt được", "Đã TẮT gif" in msgs, msgs[:120])
    ok("đã lưu vào settings", settings.get("gif.enabled") == "0", settings.get("gif.enabled"))

    api.clear()
    reset_gif_state()
    await run(app, msg_update("/start"))
    ok("sau khi tắt: lệnh không gif", api.methods().count("sendAnimation") == 0, str(api.methods()))

    api.clear()
    reset_gif_state()
    await run(app, msg_update("/gif on", user_id=ADMIN_ID))
    ok("admin bật lại", settings.get("gif.enabled") == "1")

    api.clear()
    reset_gif_state()
    await run(app, msg_update("/start"))
    ok("bật lại -> gif quay về", api.methods().count("sendAnimation") == 1, str(api.methods()))

    api.clear()
    await run(app, msg_update("/gif off", user_id=USER_ID))
    msgs = "\n".join(api.texts("sendMessage") + api.texts("editMessageText"))
    ok("user thường không tắt được", "chỉ dành cho admin" in msgs, msgs[:120])
    ok("trạng thái không đổi", settings.get("gif.enabled") == "1")


async def test_help_menu(app, api, gif):
    print("\n[6] /help + menu: có /ping, /gif, icon premium")
    api.clear()
    reset_gif_state()
    await run(app, msg_update("/help"))
    txt = "\n".join(api.texts("sendMessage") + api.texts("editMessageText"))
    ok("/help có /ping", "/ping" in txt)
    ok("/help có /gif", "/gif on | off" in txt)
    ok("/help không còn nhắc anime phong cảnh", "anime phong cảnh" not in txt)
    ok("/help icon premium", count_premium(txt) > 0, str(count_premium(txt)))
    ok("/help có emoji thường", not unwrapped_emoji(txt), str(unwrapped_emoji(txt)))
    ok("/help HTML cân thẻ", html_balanced(txt), txt[:120])


async def test_dashboard(app, api, gif):
    print("\n[7] /start dashboard")
    api.clear()
    reset_gif_state()
    await run(app, msg_update("/start"))
    txt = "\n".join(api.texts("sendMessage") + api.texts("editMessageText"))
    ok("dashboard có /ping", "/ping" in txt)
    ok("dashboard icon premium", count_premium(txt) > 0, str(count_premium(txt)))
    ok("dashboard sạch emoji thường", not unwrapped_emoji(txt), str(unwrapped_emoji(txt)))


async def test_reaction_delete(app, api, gif):
    print("\n[8] Reaction -> tự xoá tin")
    api.clear()
    upd = Update.de_json({
        "update_id": 999001,
        "message_reaction": {
            "chat": {"id": USER_ID, "type": "private"},
            "message_id": 4321,
            "date": 1730000000,
            "old_reaction": [],
            "new_reaction": [{"type": "emoji", "emoji": "❤"}],
            "user": {"id": USER_ID, "is_bot": False, "first_name": "Khoa An"},
        },
    }, bot=BOT)
    await run(app, upd)
    dels = api.of("deleteMessage")
    ok("tin bị reaction đã bị xoá", any(str(d.get("message_id")) == "4321" for d in dels), str(dels))


async def test_gifpool_chiikawa():
    print("\n[9] Pool gif = 100% chiikawa (mạng thật)")
    bad = [q for q in gifpool.QUERIES if "chiikawa" not in q]
    ok("mọi query đều là chiikawa", not bad, str(bad))
    ok("không còn anime scenery", not hasattr(gifpool, "NEKOS"))
    n = await gifpool._refresh()  # noqa: SLF001
    ok("lấy được gif thật", n >= 20, f"{n} link")
    links = gifpool._all()  # noqa: SLF001
    ok("link đều là .gif", all(u.endswith(".gif") for u in links[:20]))


async def main_async():
    api = await FakeAPI().start()
    print(f"Fake Telegram API: {api.base_url}")
    gif = await get_gif_bytes()
    print(f"gif mẫu: {len(gif)} bytes, header={gif[:6]}")
    patch_gif(gif)
    app = await make_app(api)
    print(f"BOT_COMMANDS: {sorted(main.BOT_COMMANDS)}")
    try:
        await test_getinfo(app, api, gif)
        await test_tiktok_video(app, api, gif)
        await test_ping(app, api, gif)
        await test_gif_gating(app, api, gif)
        await test_gif_admin_toggle(app, api, gif)
        await test_help_menu(app, api, gif)
        await test_dashboard(app, api, gif)
        await test_reaction_delete(app, api, gif)
        await test_gifpool_chiikawa()
    finally:
        await app.shutdown()
        await api.stop()

    passed = sum(1 for _, c, _ in RESULTS if c)
    print("\n" + "=" * 52)
    for name, c, detail in RESULTS:
        if not c:
            print(f"FAIL: {name}  {detail}")
    print(f"{passed}/{len(RESULTS)} PASS")
    print("=" * 52)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main_async()))
    except Exception:
        traceback.print_exc()
        sys.exit(2)

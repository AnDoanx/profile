#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test v17: đa nền tảng (TikTok · Instagram · Facebook · YouTube), tải song song,
TikTok 'mp3' -> cả 2, /fb link share, cookie, giao diện console.

Chạy: ../.venv-v17/bin/python tests/test_v17.py
"""

import asyncio
import inspect
import os
import re
import subprocess
import sys
import tempfile
import time

from pathlib import Path

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))

TMP = tempfile.mkdtemp(prefix="khoaan-v17-")
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
from core import captcha, console, cookies as ck, social, ytdl  # noqa: E402
from core.downloader import DlResult, MediaItem  # noqa: E402
from fake_api import FakeAPI  # noqa: E402

USER_ID = 555000111
OTHER_ID = 666000222
GROUP = -1001234567890
BOT = None
API = None
RESULTS = []


def ok(name, cond=True, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  → {detail}" if detail and not cond else ""))


# ------------------------------------------------------------------ update builders
def _msg(text=None, user_id=USER_ID, chat_id=None, chat_type="private", title=None,
         extra=None, reply_to=None):
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
    if reply_to:
        msg["reply_to_message"] = reply_to
    if extra:
        msg.update(extra)
    return {"update_id": mid, "message": msg}


def upd(payload):
    return Update.de_json(payload, bot=BOT)


def private(text, user_id=USER_ID):
    main.COOLDOWN.clear()          # mỗi tin là 1 lượt mới, không dính giãn cách
    return upd(_msg(text, user_id=user_id))


def cb(data, user_id=USER_ID):
    main.COOLDOWN.clear()
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
    return app


async def run(app, update):
    await app.process_update(update)


def reset():
    main.COOLDOWN.clear()


# ------------------------------------------------------------------ file giả
def fake_files(kinds=("video",)):
    """Tạo file tạm thật ứng với từng loại (video/audio/photo)."""
    out = []
    d = Path(os.environ["TMP_DIR"]) / f"fake{int(time.time() * 1000) % 100000}"
    d.mkdir(parents=True, exist_ok=True)
    for i, kind in enumerate(kinds):
        ext = {"video": "mp4", "audio": "mp3", "photo": "jpg"}[kind]
        f = d / f"file{i}.{ext}"
        f.write_bytes(b"x" * 2048)
        out.append(MediaItem(path=str(f), kind=kind, title=f"Bài {i}", uploader="khoaan",
                             duration=12, views=1000, platform="", url=""))
    return out


def stub_download(items_fn, calls=None):
    """Thay social.download_any bằng bản giả (không lên mạng)."""
    async def _fake(url, kind="video", *, height=1080, on_progress=None):
        if calls is not None:
            calls.append({"url": url, "kind": kind, "height": height})
        res = items_fn(url, kind)
        for it in res.items:
            it.platform = social.platform(url) or it.platform
            it.url = url
        return res
    main.social.download_any = _fake


def restore_download():
    main.social.download_any = _ORIG_DOWNLOAD


_ORIG_DOWNLOAD = social.download_any


# ------------------------------------------------------------------ 1. nhận diện
def test_detect():
    print("\n[1] Nhận diện nền tảng + ý người dùng")
    ok("TikTok", social.platform("https://www.tiktok.com/@a/video/123") == "tiktok")
    ok("TikTok rút gọn", social.platform("https://vt.tiktok.com/ZS123/") == "tiktok")
    ok("Instagram reel", social.platform("https://www.instagram.com/reel/C9xYz-1/") == "instagram")
    ok("Instagram p", social.platform("https://instagram.com/p/C9xYz-1/") == "instagram")
    ok("FB share", social.platform("https://www.facebook.com/share/1KJhFVgHRt/?mibextid=wwXIfr")
       == "facebook")
    ok("fb.watch", social.platform("https://fb.watch/abcdEFG/") == "facebook")
    ok("YouTube youtu.be", social.platform("https://youtu.be/dQw4w9WgXcQ") == "youtube")
    ok("YouTube watch", social.platform("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "youtube")
    ok("link lạ", social.platform("https://vnexpress.net/bai-viet") == "")

    ok("any_link bỏ link lạ, lấy link hợp lệ",
       social.any_link("xem cái này https://vnexpress.net/a rồi https://youtu.be/dQw4w9WgXcQ")
       == "https://youtu.be/dQw4w9WgXcQ")

    ok("'mp3' -> audio", social.want_kind("tải bài này mp3") == "audio")
    ok("'cả 2' -> both", social.want_kind("lấy cả 2 nhé") == "both")
    ok("không nói gì -> video", social.want_kind("https://youtu.be/x") == "video")
    ok("TikTok + mp3 -> CẢ 2", main.auto_kind("https://vt.tiktok.com/x mp3", "tiktok") == "both")
    ok("YouTube + mp3 -> chỉ audio", main.auto_kind("https://youtu.be/x mp3", "youtube") == "audio")
    ok("IG + cả 2", main.auto_kind("https://instagram.com/reel/x cả 2", "instagram") == "both")


# ------------------------------------------------------------------ 2. nút + caption
def test_buttons():
    print("\n[2] Nút tải bản còn lại + caption có tên nền tảng")
    tok = main.dl_remember("https://youtu.be/dQw4w9WgXcQ")
    kb = main.dl_buttons(tok, "video")
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    ok("video -> có nút MP3", any(d.startswith("tk:m:") for d in datas), str(datas))
    ok("video -> không có nút Video trùng", not any(d.startswith("tk:v:") for d in datas), str(datas))

    ok("video -> KHÔNG còn nút 'Cả 2'", not any(d.startswith("tk:b:") for d in datas), str(datas))
    ok("video -> chỉ 1 nút (gọn)", len(datas) == 1, str(datas))

    # v18: gửi audio / cả 2 xong thì không kèm nút nữa
    ok("audio -> không kèm nút", main.dl_buttons(tok, "audio") is None)
    ok("both -> không kèm nút", main.dl_buttons(tok, "both") is None)

    # nút chỉ bấm được 1 lần
    src = inspect.getsource(main.cb_tiktok_button)
    ok("nút audio dùng 1 lần", 'item.get("used")' in src and 'item["used"] = True' in src)
    ok("bấm lần 2 -> gỡ bàn phím", "edit_message_reply_markup(reply_markup=None)" in src)

    item = MediaItem(path="/tmp/x.mp4", kind="video", title="Thử", platform="youtube")
    cap = main.media_caption(item, time.time())
    ok("caption ghi YouTube", "YouTube" in cap, cap)
    item2 = MediaItem(path="/tmp/x.mp3", kind="audio", title="Nhạc", platform="instagram")
    ok("audio caption có 🎵", "🎵" in main.media_caption(item2, time.time()))


# ------------------------------------------------------------------ 3. /ytb
async def test_ytb(app, api):
    print("\n[3] /ytb — tải YouTube, chọn 720, mp3, cả 2")
    reset()
    calls = []
    stub_download(lambda u, k: DlResult(items=fake_files(
        ("video",) if k == "video" else (("video", "audio") if k == "both" else ("audio",)))), calls)

    api.clear()
    await run(app, private("/ytb https://youtu.be/dQw4w9WgXcQ"))
    ok("gọi engine với kind=video", calls[-1]["kind"] == "video", str(calls[-1:]))
    ok("mặc định 1080p", calls[-1]["height"] == 1080, str(calls[-1:]))
    ok("gửi sendVideo", bool(api.of("sendVideo")), str(api.methods()))
    joined = "\n".join(api.texts() + [m.get("caption", "") for m in api.of("sendVideo")])
    ok("caption có nhãn YouTube", "YouTube" in joined, joined[:200])

    api.clear()
    await run(app, private("/ytb https://youtu.be/dQw4w9WgXcQ 720"))
    ok("đọc được 720", calls[-1]["height"] == 720, str(calls[-1:]))

    api.clear()
    await run(app, private("/ytb https://youtu.be/dQw4w9WgXcQ mp3"))
    ok("mp3 -> kind=audio", calls[-1]["kind"] == "audio", str(calls[-1:]))
    ok("mp3 -> gửi sendAudio", bool(api.of("sendAudio")), str(api.methods()))

    api.clear()
    await run(app, private("/ytb https://youtu.be/dQw4w9WgXcQ cả 2"))
    ok("cả 2 -> kind=both", calls[-1]["kind"] == "both", str(calls[-1:]))
    ok("cả 2 -> gửi video + audio", bool(api.of("sendVideo")) and bool(api.of("sendAudio")),
       str(api.methods()))

    api.clear()
    await run(app, private("/ytb https://www.tiktok.com/@a/video/1"))
    ok("link không phải YouTube bị chặn", "không phải link YouTube" in "\n".join(api.texts()),
       "\n".join(api.texts())[:200])

    api.clear()
    await run(app, private("/ytb"))
    ok("/ytb trống -> hướng dẫn", "/ytb" in "\n".join(api.texts()))
    restore_download()


# ------------------------------------------------------------------ 4. Instagram
async def test_ig(app, api):
    print("\n[4] /ig — Reel, album ảnh, link sai")
    reset()
    calls = []
    stub_download(lambda u, k: DlResult(items=fake_files(("video",))), calls)

    api.clear()
    await run(app, private("/ig https://www.instagram.com/reel/C9xYz-1/"))
    ok("gọi engine IG", calls and calls[-1]["url"].startswith("https://www.instagram.com/reel/"),
       str(calls[-1:]))
    ok("IG: gửi video", bool(api.of("sendVideo")), str(api.methods()))
    joined = "\n".join(api.texts() + [m.get("caption", "") for m in api.of("sendVideo")])
    ok("caption có Instagram", "Instagram" in joined, joined[:200])

    api.clear()
    stub_download(lambda u, k: DlResult(items=fake_files(("photo", "photo", "photo"))), calls)
    await run(app, private("/ig https://www.instagram.com/p/C9xYz-1/"))
    ok("album ảnh -> sendMediaGroup", bool(api.of("sendMediaGroup")) or bool(api.of("sendPhoto")),
       str(api.methods()))

    api.clear()
    await run(app, private("/ig https://youtu.be/dQw4w9WgXcQ"))
    ok("link YouTube vào /ig bị chặn", "không phải link Instagram" in "\n".join(api.texts()),
       "\n".join(api.texts())[:200])
    restore_download()


# ------------------------------------------------------------------ 5. TikTok link + mp3
async def test_tiktok_both(app, api):
    print("\n[5] Dán link TikTok + 'mp3' -> tải luôn CẢ video và nhạc")
    reset()
    calls = []
    stub_download(lambda u, k: DlResult(items=fake_files(
        ("video", "audio") if k == "both" else (("video",) if k == "video" else ("audio",)))), calls)

    api.clear()
    await run(app, private("https://www.tiktok.com/@a/video/123 mp3"))
    ok("TikTok + mp3 -> gọi kind=both", calls and calls[-1]["kind"] == "both", str(calls[-1:]))
    ok("gửi cả video lẫn audio",
       bool(api.of("sendVideo")) and bool(api.of("sendAudio")), str(api.methods()))

    api.clear()
    calls.clear()
    await run(app, private("https://vt.tiktok.com/ZS123/"))
    ok("không nói gì -> chỉ video", calls[-1]["kind"] == "video", str(calls[-1:]))

    api.clear()
    await run(app, private("/mp3 https://www.tiktok.com/@a/video/123"))
    ok("/mp3 TikTok -> cả 2", calls[-1]["kind"] == "both", str(calls[-1:]))
    restore_download()


# ------------------------------------------------------------------ 6. nút bấm
async def test_callback(app, api):
    print("\n[6] Bấm nút 'Cả 2' / 'MP3' dưới tệp")
    reset()
    calls = []
    stub_download(lambda u, k: DlResult(items=fake_files(
        ("video", "audio") if k == "both" else ("video",))), calls)
    tok = main.dl_remember("https://youtu.be/dQw4w9WgXcQ")

    api.clear()
    await run(app, cb(f"tk:b:{tok}"))
    ok("nút Cả 2 -> kind=both", calls and calls[-1]["kind"] == "both", str(calls[-1:]))
    ok("nút Cả 2 -> gửi 2 tệp", bool(api.of("sendVideo")) and bool(api.of("sendAudio")),
       str(api.methods()))

    api.clear()
    await run(app, cb("tk:m:khongtontai"))
    ok("token hết hạn -> báo rõ", any("hết hạn" in t for t in api.texts())
       or bool(api.of("answerCallbackQuery")), str(api.methods()))
    restore_download()


# ------------------------------------------------------------------ 7. lỗi tải
async def test_fail(app, api):
    print("\n[7] Tải lỗi -> thông báo việc cần làm (không treo)")
    reset()
    stub_download(lambda u, k: DlResult(error="Video riêng tư"))
    api.clear()
    await run(app, private("/ig https://www.instagram.com/reel/C9xYz-1/"))
    joined = "\n".join(api.texts() + api.texts("editMessageText"))
    ok("báo tải thất bại + tên nền tảng", "Tải thất bại" in joined and "Instagram" in joined, joined[:200])
    ok("có gợi ý xử lý", "thử lại" in joined.lower() or "kiểm tra" in joined.lower(), joined[:300])
    ok("không gửi tệp rỗng", not api.of("sendVideo") and not api.of("sendDocument"))

    # link lạ
    api.clear()
    await run(app, private("https://vnexpress.net/bai-viet-moi"))
    ok("link lạ -> nói rõ nền tảng hỗ trợ", "chưa hỗ trợ" in "\n".join(api.texts()).lower(),
       "\n".join(api.texts())[:200])
    restore_download()


# ------------------------------------------------------------------ 8. /fb link
def test_fb_video_detect():
    print("\n[8] /fb nhận link video")
    ok("fb watch", social.fb_is_video("https://www.facebook.com/watch/?v=1234567890"))
    ok("fb reel", social.fb_is_video("https://www.facebook.com/reel/1234567890"))
    ok("fb.watch", social.fb_is_video("https://fb.watch/abcdEFG/"))
    ok("link hồ sơ không phải video",
       not social.fb_is_video("https://www.facebook.com/profile.php?id=100012345678"))


async def test_fb_link(app, api):
    print("\n[9] /fb với link tải được video (không đi tra ID nữa)")
    reset()
    calls = []
    stub_download(lambda u, k: DlResult(items=fake_files(("video",))), calls)
    api.clear()
    await run(app, private("/fb https://www.facebook.com/watch/?v=1234567890"))
    ok("fb watch -> gọi engine tải", bool(calls), str(calls))
    ok("fb watch -> gửi video", bool(api.of("sendVideo")), str(api.methods()))
    restore_download()


# ------------------------------------------------------------------ 10. cookie
def test_cookie_store():
    print("\n[10] Lưu / đọc / xoá cookie Instagram")
    good = ("# Netscape HTTP Cookie File\n"
            ".instagram.com\tTRUE\t/\tTRUE\t1735689600\tsessionid\tabc123\n"
            ".instagram.com\tTRUE\t/\tTRUE\t1735689600\tds_user_id\t12345")
    n, err = ck.save("instagram", good)
    ok("lưu cookie hợp lệ", n >= 1, f"{n} {err}")
    got_path = ck.get("instagram") or ""
    ok("có đường dẫn cookie dùng được", got_path.startswith("/") and "cookies_ig" in got_path, got_path)
    ok("nội dung lưu đúng",
       "sessionid" in Path(got_path).read_text(encoding="utf-8") if got_path else False)
    ok("đếm đúng số dòng", ck.count("instagram") >= 1, str(ck.count("instagram")))
    bad, err2 = ck.save("instagram", "không phải cookie")
    ok("cookie rác bị từ chối", bad == 0 and bool(err2), f"{bad} {err2}")
    ok("xoá sạch", ck.clear("instagram") is not False and ck.count("instagram") == 0)


# ------------------------------------------------------------------ 11. console
def test_console():
    print("\n[11] Giao diện console")
    b = console.banner("v17", "Test")
    ok("banner có ASCII art + phiên bản", "v17" in b and "█" in b and len(b.splitlines()) >= 8,
       b[:80].replace("\n", " "))
    boxed = console.box("THỐNG KÊ", [("Bot", "1"), ("Lệnh", "/ytb")])
    ok("khung box vẽ ra", len(boxed.splitlines()) >= 5, str(len(boxed.splitlines())))
    ok("grad() trả chuỗi", isinstance(console.grad("abc"), str) and "abc" in console.grad("abc"))
    ok("style() không làm mất chữ", "x" in console.style("x", console.PALETTE["red"], bold=True))
    console.event("youtube", "video · khoaan · 12 MB", ok=True, secs=1.2, size="12 MB")
    console.event("instagram", "lỗi", ok=False, secs=0.0)
    console.ok("test ok"); console.info("test info"); console.warn("test warn"); console.err("test err")
    ok("event/ok/info/warn/err không crash", True)


def test_console_layout():
    print("\n[11b] Giao diện console: canh khung")
    b = console.banner("v17", animate=False)
    lines = [l for l in b.splitlines() if l.strip()]
    widths = {console._vis(l) for l in lines}
    ok("mọi dòng banner bằng nhau (không lệch khung)", len(widths) == 1, str(sorted(widths)))
    ok("logo chữ khối 6 dòng", len(console._art("KHOA AN")) == 6, str(len(console._art("KHOA AN"))))
    ok("có 6 nền tảng trong banner",
       all(n in b for n in ("TikTok", "Instagram", "Facebook", "YouTube", "Music", "Voice")),
       "")
    ok("không dùng emoji trong banner (cmd.exe an toàn)",
       not any(ch in b for ch in "📸📘⚡🔊🎵"), "")
    boxed = console.box("X", [("A", "1"), ("B", "2")])
    bw = {console._vis(l) for l in boxed.splitlines() if l.strip()}
    ok("khung box bằng nhau", len(bw) == 1, str(sorted(bw)))


async def test_social_guards():
    print("\n[11c] Chống vỡ khi 1 nguồn Instagram chết")
    import inspect
    params = inspect.signature(social.http.fetch).parameters
    ok("http.fetch nhận json_data", "json_data" in params, str(list(params)))

    orig = social.http.fetch
    seen = {}

    async def fake_fetch(url, **kw):
        seen.update(kw)
        return 0, b"", {}

    async def run():
        social.http.fetch = fake_fetch
        try:
            r = await social._ig_downloadgram("https://www.instagram.com/p/XyZ123abc/")
            return r
        finally:
            social.http.fetch = orig

    r = await run()
    ok("_ig_downloadgram không crash (chỉ trả lỗi)", bool(r.error), r.error[:60])
    ok("gửi body qua json_data, không phải json_body", "json_data" in seen, str(list(seen)))

    # lớp 2..5 lỗi hết -> không được ném exception ra ngoài
    async def boom(*a, **k):
        raise RuntimeError("nguồn chết")

    async def run2():
        origs = (social._ig_embed, social._ig_mirror, social._ig_downloadgram, social._ig_api)
        social._ig_embed = social._ig_mirror = social._ig_downloadgram = social._ig_api = boom
        try:
            return await social.instagram("https://www.instagram.com/p/XyZ123abc/")
        finally:
            social._ig_embed, social._ig_mirror, social._ig_downloadgram, social._ig_api = origs

    r2 = await run2()
    ok("mọi nguồn IG chết vẫn trả DlResult có thông báo", bool(r2.error), r2.error[:60])


# ------------------------------------------------------------------ 12. engine
def test_engine_units():
    print("\n[12] Engine: format, client, cache, mp3")
    fmts = ytdl._formats("video", 720)
    ok("video 720 có giới hạn chiều cao", any("720" in f for f in fmts), str(fmts[:3]))
    fmts_a = ytdl._formats("audio", 1080)
    ok("audio ưu tiên bestaudio", any("ba[" in f or "bestaudio" in f for f in fmts_a),
       str(fmts_a[:2]))
    ok("clients có nhiều bộ", len(ytdl._clients()) >= 3, str(len(ytdl._clients())))
    ok("has_ffmpeg", ytdl.has_ffmpeg())

    if ytdl.has_ffmpeg():
        src = Path(os.environ["TMP_DIR"]) / "tone.mp4"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                        "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
                        "-shortest", str(src)], capture_output=True)
        mp3 = ytdl.to_mp3(src)
        ok("to_mp3 tạo file .mp3 có dữ liệu",
           mp3.suffix.lower() == ".mp3" and mp3.stat().st_size > 1000,
           str(mp3) + " " + str(mp3.stat().st_size if mp3.exists() else 0))
    ok("purge() không nổ", isinstance(ytdl.purge(force=True), int))


# ------------------------------------------------------------------ 13. lệnh & help
def test_commands():
    print("\n[13] Lệnh / menu / help")
    for cmd in ("ytb", "youtube", "yt", "ig", "insta", "instagram", "cookie"):
        ok(f"đăng ký /{cmd}", cmd in main.BOT_COMMANDS, str(sorted(main.BOT_COMMANDS)))
    ok("help có mục TẢI XUỐNG", "TẢI XUỐNG" in main.HELP_TEXT)
    ok("help có /ytb", "/ytb" in main.HELP_TEXT)
    ok("help có /ig", "/ig" in main.HELP_TEXT)
    for cmd in ("tiktok", "tt", "console"):
        ok(f"đăng ký /{cmd}", cmd in main.BOT_COMMANDS, str(sorted(main.BOT_COMMANDS)))
    kb = main.main_menu(False)
    labels = [b.text for row in kb.inline_keyboard for b in row]
    # v18: nút có thêm emoji THUONG phía trước (bỏ icon premium) -> so khớp phần chữ
    ok("menu gộp 1 nút Tải xuống", any(t.strip().endswith("Tải xuống") for t in labels), str(labels))
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    ok("menu không còn nút nền tảng riêng",
       not any(c in ("p_ytb", "p_ig", "p_tt") for c in cbs), str(cbs))


async def test_panels(app, api):
    print("\n[14] Bấm menu YouTube / Instagram")
    api.clear()
    await run(app, cb("p_ytb"))
    ok("panel YouTube có /ytb 720", "720" in "\n".join(api.texts()), "\n".join(api.texts())[:200])
    api.clear()
    await run(app, cb("p_ig"))
    ok("panel IG có /ig", "/ig" in "\n".join(api.texts()), "\n".join(api.texts())[:200])


# ------------------------------------------------------------------ main
async def amain():
    global API
    api = FakeAPI()
    await api.start()
    API = api
    app = await make_app(api)
    captcha.set_enabled(False)
    try:
        test_detect()
        test_buttons()
        await test_ytb(app, api)
        await test_ig(app, api)
        await test_tiktok_both(app, api)
        await test_callback(app, api)
        await test_fail(app, api)
        test_fb_video_detect()
        await test_fb_link(app, api)
        test_cookie_store()
        test_console()
        test_console_layout()
        await test_social_guards()
        test_engine_units()
        test_commands()
        await test_panels(app, api)
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

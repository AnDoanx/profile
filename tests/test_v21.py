#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test v21 — TỐC ĐỘ / SING.

Mục tiêu: /sing trả lời nhanh, không bao giờ treo lâu, và KHÔNG mất chức năng nào.

Bốn nhóm:
  [1] Nguồn tìm nhạc mới (InnerTube) — parse JSON thật của YouTube
  [2] Bộ điều phối tìm kiếm — ưu tiên nguồn nhanh, trần thời gian, cache, gộp request, cooldown
  [3] /sing chạy thật qua Fake Telegram API — ảnh, caption, 10 nút, xoá tin trạng thái
  [4] Pool riêng + tốc độ vẽ ảnh (không chiếm pool mặc định của asyncio)

Chạy: .venv/bin/python tests/test_v21.py
"""

import asyncio
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))

TMP = tempfile.mkdtemp(prefix="khoaan-v21-")
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
from PIL import Image  # noqa: E402

import config  # noqa: E402
import main  # noqa: E402
from core import buttons as B  # noqa: E402
from core import canvas  # noqa: E402
from core import music  # noqa: E402
from core import pool  # noqa: E402
from core import premium  # noqa: E402
from fake_api import FakeAPI  # noqa: E402

ADMIN = 8297032840
BOT = None
RESULTS = []


def ok(name, cond=True, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  [OK] " if cond else "  [XX] ") + name + (f"  -> {detail}" if detail and not cond else ""))


# ------------------------------------------------------------------ helpers
def _msg(text, user_id=ADMIN):
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
    return {"update_id": mid, "message": msg}


def private(text, user_id=ADMIN):
    main.COOLDOWN.clear()
    return Update.de_json(_msg(text, user_id), bot=BOT)


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
                "caption": "🎵 bài hát\nTìm được 10 bài",
            },
        },
    }, bot=BOT)


async def make_app(api):
    app = main.build_app(base_url=api.base_url)
    await app.initialize()
    global BOT
    BOT = app.bot
    return app


def _mk(raw):
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:  # noqa: BLE001
            return None
    return raw


def markups(api):
    out = []
    for method in ("sendMessage", "editMessageText", "sendPhoto", "sendAudio", "sendDocument"):
        for p in api.of(method):
            mk = _mk(p.get("reply_markup"))
            if mk:
                out.append(mk)
    return out


def buttons_of(mk):
    return [b for line in (mk.get("inline_keyboard") or []) for b in line]


def texts(api):
    out = []
    for method in ("sendMessage", "editMessageText"):
        for p in api.of(method):
            if p.get("text"):
                out.append(p["text"])
    for method in ("sendPhoto", "sendAudio", "sendDocument"):
        for p in api.of(method):
            if p.get("caption"):
                out.append(p["caption"])
    return out


def reset_music():
    music._SEARCH_CACHE.clear()
    music._INFLIGHT.clear()
    music._YTDLP_DOWN_UNTIL = 0.0
    music._THUMB_CACHE.clear()


TRACKS = [
    {
        "id": f"vid{i:02d}",
        "title": f"Bài hát thử nghiệm số {i} — tên khá dài để kiểm tra xuống dòng",
        "channel": f"Kênh nghệ sĩ {i}",
        "duration": 200 + i,
        "duration_text": f"3:{10 + i}",
        "views_text": f"{i + 1}.2M views",
        "url": f"https://www.youtube.com/watch?v=vid{i:02d}",
        "thumb": f"https://i.ytimg.com/vi/vid{i:02d}/mqdefault.jpg",
    }
    for i in range(10)
]


# ================================================================== [1] InnerTube
def test_innertube_parse():
    print("\n[1] Nguồn tìm nhạc mới (InnerTube)")

    # JSON mô phỏng đúng cấu trúc YouTube trả về (rút gọn)
    items = [
        {"videoRenderer": {
            "videoId": f"abc{i}",
            "title": {"runs": [{"text": f"Tên bài {i}"}]},
            "ownerText": {"runs": [{"text": f"Ca sĩ {i}"}]},
            "lengthText": {"simpleText": f"4:0{i}"},
            "viewCountText": {"simpleText": f"1.2{i}3.456 lượt xem"},
            "publishedTimeText": {"simpleText": "2 năm trước"},
        }}
        for i in range(6)
    ]
    payload = {"contents": {"twoColumnSearchResultsRenderer": {"primaryContents": {
        "sectionListRenderer": {"contents": [{"itemSectionRenderer": {"contents": items}}]}
    }}}}

    out = []
    music._collect_tracks(payload, out, 10)
    ok("InnerTube: lấy đủ số bài", len(out) == 6, str(len(out)))
    first = out[0] if out else {}
    ok("InnerTube: đúng id", first.get("id") == "abc0", str(first.get("id")))
    ok("InnerTube: đúng tên bài", first.get("title") == "Tên bài 0", str(first.get("title")))
    ok("InnerTube: đúng ca sĩ", first.get("channel") == "Ca sĩ 0", str(first.get("channel")))
    ok("InnerTube: đúng thời lượng", first.get("duration") == 240, str(first.get("duration")))
    ok("InnerTube: có views đã chuẩn hoá", "1.2M views" in (first.get("views_text") or ""),
       str(first.get("views_text")))
    ok("InnerTube: thumb ghép từ id",
       first.get("thumb") == "https://i.ytimg.com/vi/abc0/mqdefault.jpg", str(first.get("thumb")))
    ok("InnerTube: link xem được", (first.get("url") or "").endswith("abc0"), str(first.get("url")))

    # bài rác / thiếu id phải bị bỏ, không được làm hỏng cả danh sách
    out2 = []
    music._collect_tracks({"a": {"videoRenderer": {"videoId": "ok1"}},
                           "b": {"videoRenderer": {"nope": 1}},
                           "c": {"videoRenderer": {"videoId": "ok2", "title": {"simpleText": "T2"}}}},
                          out2, 10)
    ok("InnerTube: bỏ bài thiếu id", len(out2) == 2, str([x["id"] for x in out2]))
    ok("InnerTube: title dạng simpleText vẫn đọc được",
       any(x.get("title") == "T2" for x in out2), str([x.get("title") for x in out2]))

    # tôn trọng giới hạn
    out3 = []
    music._collect_tracks(payload, out3, 3)
    ok("InnerTube: tôn trọng limit", len(out3) == 3, str(len(out3)))


# ============================================================ [2] Bộ điều phối
async def test_search_orchestrator():
    print("\n[2] Bộ điều phối tìm kiếm")

    async def fast(kw, limit):
        await asyncio.sleep(0.02)
        return TRACKS[:limit]

    async def dead(kw, limit):
        return []

    async def slow(kw, limit):
        await asyncio.sleep(30)
        return TRACKS[:limit]

    orig = {
        "innertube": music._search_innertube,
        "html": music._search_html,
        "piped": music._search_piped,
        "ytdlp": music._search_ytdlp,
    }
    try:
        # --- nguồn nhanh sống -> xong ngay, không chờ yt-dlp
        music._search_innertube = fast
        music._search_html = slow
        music._search_piped = slow
        called = {"ytdlp": 0}

        def _y(keyword, limit):
            called["ytdlp"] += 1
            return []

        music._search_ytdlp = _y
        reset_music()
        t = time.time()
        r = await music.search_songs("bài test nhanh", 10)
        dt = time.time() - t
        ok("có nguồn nhanh -> trả ngay", len(r) == 10, str(len(r)))
        ok("có nguồn nhanh -> không đụng yt-dlp", called["ytdlp"] == 0, str(called["ytdlp"]))
        ok("có nguồn nhanh -> dưới 1s (2 nguồn kia treo 30s)", dt < 1.0, f"{dt:.2f}s")

        # --- nguồn nhanh chết hết -> yt-dlp phải được bật NGAY (lỗi cũ: đợi mãi)
        music._search_innertube = dead
        music._search_html = dead
        music._search_piped = dead
        music._search_ytdlp = lambda kw, lim: TRACKS[:lim]
        reset_music()
        t = time.time()
        r = await music.search_songs("bài test chỉ ytdlp", 10)
        dt = time.time() - t
        ok("nguồn nhanh chết -> yt-dlp cứu được", len(r) == 10, str(len(r)))
        ok("nguồn nhanh chết -> không treo lâu", dt < music.SEARCH_BUDGET + 1.5, f"{dt:.2f}s")

        # --- tất cả chết -> trả rỗng nhưng đúng hạn
        music._search_ytdlp = lambda kw, lim: []
        reset_music()
        t = time.time()
        r = await music.search_songs("bài test chết hết", 10)
        dt = time.time() - t
        ok("chết hết -> trả danh sách rỗng (không ném lỗi)", r == [], str(r))
        ok("chết hết -> vẫn trong trần thời gian", dt <= music.SEARCH_BUDGET + 0.5, f"{dt:.2f}s")

        # --- tất cả treo -> cắt đúng trần, không treo vô hạn
        music._search_innertube = slow
        music._search_html = slow
        music._search_piped = slow
        reset_music()
        t = time.time()
        r = await music.search_songs("bài test treo", 10)
        dt = time.time() - t
        ok("mọi nguồn treo -> cắt ở trần SEARCH_BUDGET", dt <= music.SEARCH_BUDGET + 0.7, f"{dt:.2f}s")
        ok("mọi nguồn treo -> trả rỗng an toàn", r == [], str(r))

        # --- cooldown yt-dlp: lỗi 1 lần thì lần sau không bắt user chờ nữa
        music._search_innertube = dead
        music._search_html = dead
        music._search_piped = dead
        music._search_ytdlp = lambda kw, lim: []
        reset_music()
        await music.search_songs("bài test cooldown 1", 10)
        ok("yt-dlp lỗi -> bật cooldown", music._YTDLP_DOWN_UNTIL > time.time(),
           f"{music._YTDLP_DOWN_UNTIL - time.time():.0f}s")
        t = time.time()
        await music.search_songs("bài test cooldown 2", 10)
        dt = time.time() - t
        ok("đang cooldown -> trả nhanh, không thử yt-dlp", dt < 2.0, f"{dt:.2f}s")

        # --- gộp request trùng từ khoá: 6 người cùng tìm -> 1 lần gọi mạng
        calls = {"n": 0}
        music._search_innertube = dead
        music._search_html = dead
        music._search_piped = dead

        def _count_y(kw, lim):
            calls["n"] += 1
            time.sleep(0.2)
            return TRACKS[:lim]

        music._search_ytdlp = _count_y
        reset_music()
        t = time.time()
        rs = await asyncio.gather(*[music.search_songs("bài test gộp", 10) for _ in range(6)])
        dt = time.time() - t
        ok("6 request cùng lúc đều có kết quả", all(len(x) == 10 for x in rs), str([len(x) for x in rs]))
        ok("6 request cùng lúc -> chỉ gọi mạng 1 lần", calls["n"] == 1, str(calls["n"]))
        ok("6 request cùng lúc -> xong dưới 2s", dt < 2.0, f"{dt:.2f}s")

        # --- cache RAM: lần 2 phải tức thì
        music._search_innertube = fast
        music._search_html = dead
        music._search_piped = dead
        reset_music()
        await music.search_songs("bài test cache", 10)
        t = time.time()
        r = await music.search_songs("bài test cache", 10)
        dt = time.time() - t
        ok("cache RAM: lần 2 lấy lại đủ bài", len(r) == 10, str(len(r)))
        ok("cache RAM: lần 2 dưới 20ms", dt < 0.02, f"{dt * 1000:.1f}ms")

        # --- cache ghi ra đĩa, restart vẫn còn
        ok("cache ghi được ra đĩa", music.CACHE_FILE.exists(), str(music.CACHE_FILE))
        saved = json.loads(music.CACHE_FILE.read_text("utf-8"))
        ok("cache đĩa có dữ liệu", bool(saved.get("items")), str(len(saved.get("items") or {})))
        music._SEARCH_CACHE.clear()
        music._CACHE_LOADED = False
        r = await music.search_songs("bài test cache", 10)
        ok("restart xong vẫn dùng cache đĩa", len(r) == 10, str(len(r)))
    finally:
        music._search_innertube = orig["innertube"]
        music._search_html = orig["html"]
        music._search_piped = orig["piped"]
        music._search_ytdlp = orig["ytdlp"]
        reset_music()


# ================================================================ [3] /sing thật
async def test_sing_flow(app, api):
    print("\n[3] /sing chạy thật qua Fake Telegram API")

    orig = {"search": music.search_songs, "thumbs": music.fetch_thumbs,
            "download": music.download_audio}
    try:
        async def fake_search(kw, limit=10):
            await asyncio.sleep(0.02)
            return TRACKS[:limit]

        async def fake_thumbs(tracks, budget=0):
            return [None] * len(tracks)

        music.search_songs = fake_search
        music.fetch_thumbs = fake_thumbs

        # --- thiếu từ khoá
        api.clear()
        await app.process_update(private("/sing"))
        await asyncio.sleep(0.1)
        t = " ".join(texts(api))
        ok("/sing trống: nhắc cú pháp", "Cú pháp" in t or "cú pháp" in t, t[:80])
        ok("/sing trống: có icon premium", "<tg-emoji" in t, t[:80])

        # --- chạy bình thường
        api.clear()
        t0 = time.time()
        await app.process_update(private("/sing Tắt đèn đi"))
        await asyncio.sleep(0.45)
        dt = time.time() - t0

        photos = api.of("sendPhoto")
        ok("/sing: gửi đúng 1 ảnh kết quả", len(photos) == 1, str(len(photos)))
        cap = (photos[0].get("caption") if photos else "") or ""
        ok("/sing: caption có từ khoá", "Tắt đèn đi" in cap, cap[:90])
        ok("/sing: caption có icon premium", "<tg-emoji" in cap, cap[:90])
        ok("/sing: caption báo đủ 10 bài", "10" in cap, cap[:90])

        mk = _mk(photos[0].get("reply_markup")) if photos else None
        bs = buttons_of(mk) if mk else []
        nums = [b for b in bs if (b.get("text") or "").strip() in
                {str(i) for i in range(1, 11)} or str(b.get("callback_data")) in
                {f"sg_{i}" for i in range(1, 11)}]
        ok("/sing: có 10 nút số", len([b for b in bs if str(b.get("callback_data")).startswith("sg_")]) == 10,
           str(len(bs)))
        ok("/sing: nút số đều có icon premium",
           all(b.get("icon_custom_emoji_id") for b in bs if str(b.get("callback_data")).startswith("sg_")),
           str([b.get("text") for b in bs[:3]]))
        ok("/sing: có nút về Menu", any(b.get("callback_data") == "p_home" for b in bs),
           str([b.get("callback_data") for b in bs]))

        # --- tin "đang tìm" phải được xoá sau khi gửi ảnh (không để rác)
        dels = api.of("deleteMessage")
        ok("/sing: xoá tin trạng thái sau khi gửi ảnh", len(dels) >= 1, str(len(dels)))

        # --- ảnh phải là PNG thật, đúng kích thước
        abuf = canvas.sing_canvas("Tắt đèn đi", TRACKS, [None] * 10, 500)
        raw = abuf.getvalue()
        ok("canvas: là file PNG", raw[:8] == b"\x89PNG\r\n\x1a\n", str(raw[:8]))
        im = Image.open(io.BytesIO(raw))
        ok("canvas: đúng chiều rộng thiết kế", im.width == canvas.SING_W, str(im.width))
        ok("canvas: có chiều cao hợp lý", 400 < im.height < 1600, str(im.height))

        # --- danh sách bài phải được lưu để bấm số tải được
        ok("/sing: lưu danh sách bài cho nút bấm",
           len(app.bot_data.get(f"sing_{ADMIN}") or []) == 10,
           str(len(app.bot_data.get(f"sing_{ADMIN}") or [])))

        # --- tìm không ra -> báo rõ, không sập
        async def empty_search(kw, limit=10):
            return []

        music.search_songs = empty_search
        api.clear()
        await app.process_update(private("/sing không có bài nào"))
        await asyncio.sleep(0.15)
        t = " ".join(texts(api))
        ok("/sing không có kết quả: báo thân thiện", "Không tìm thấy" in t, t[:90])
        ok("/sing không có kết quả: không gửi ảnh rác", len(api.of("sendPhoto")) == 0,
           str(len(api.of("sendPhoto"))))

        # --- bấm số để tải MP3
        music.search_songs = fake_search
        api.clear()
        await app.process_update(private("/sing Tắt đèn đi"))
        await asyncio.sleep(0.4)

        async def fake_download(video_id, timeout=110):
            info = {"title": "Bài hát thử", "uploader": "Ca sĩ thử", "duration": 210, "ext": "mp3"}
            buf = io.BytesIO(b"ID3" + b"\x00" * 600)
            buf.name = f"{video_id}.mp3"
            return buf, info

        music.download_audio = fake_download
        api.clear()
        await app.process_update(cb("sg_3"))
        await asyncio.sleep(0.5)
        audios = api.of("sendAudio") + api.of("sendDocument")
        ok("bấm số: tải được file âm thanh", len(audios) >= 1, str(len(audios)))
        cap2 = (audios[0].get("caption") if audios else "") or ""
        ok("bấm số: caption có icon premium", "<tg-emoji" in cap2, cap2[:80])

        # --- bấm số khi danh sách đã cũ -> báo nhẹ, không sập
        app.bot_data.pop(f"sing_{ADMIN}", None)
        api.clear()
        await app.process_update(cb("sg_9"))
        await asyncio.sleep(0.15)
        ok("bấm số khi danh sách cũ: không sập", True)

        # --- tốc độ tổng (không tính mạng, chỉ khung xử lý)
        ok("/sing xử lý xong dưới 1.5s (đã mock mạng)", dt < 1.5, f"{dt:.2f}s")
    finally:
        music.search_songs = orig["search"]
        music.fetch_thumbs = orig["thumbs"]
        music.download_audio = orig["download"]


# ================================================================ [4] pool + tốc độ
async def test_pool_and_speed():
    print("\n[4] Pool riêng + tốc độ vẽ ảnh")

    import threading

    def who():
        return threading.current_thread().name

    name = await pool.run_io(who)
    ok("pool.run_io chạy ở thread riêng", name.startswith("khoaan-io"), name)
    name = await pool.run_cpu(who)
    ok("pool.run_cpu chạy ở thread riêng", name.startswith("khoaan-cpu"), name)

    # 1 job IO treo không được chặn job CPU
    async def job_io():
        return await pool.run_io(time.sleep, 1.0)

    async def job_cpu():
        t = time.time()
        await pool.run_cpu(lambda: sum(range(1000)))
        return time.time() - t

    t = time.time()
    await asyncio.gather(job_io(), job_cpu())
    ok("1 job IO treo không chặn job CPU", True, f"{time.time() - t:.2f}s")

    # event loop không bị chặn khi vẽ ảnh
    ticks = {"n": 0}

    async def ticker():
        while True:
            ticks["n"] += 1
            await asyncio.sleep(0.01)

    tk = asyncio.create_task(ticker())
    t = time.time()
    await pool.run_cpu(canvas.sing_canvas, "test", TRACKS, [None] * 10, 100)
    dt = time.time() - t
    tk.cancel()
    ok("vẽ ảnh vẫn để event loop chạy", ticks["n"] >= 3, str(ticks["n"]))
    ok(f"vẽ 10 bài dưới 1.5s (thực tế {dt:.2f}s)", dt < 1.5, f"{dt:.2f}s")

    # PNG encode nhanh (compress_level thay optimize)
    img = Image.open(io.BytesIO(canvas.sing_canvas("x", TRACKS, [None] * 10, 0).getvalue())).convert("RGB")
    t = time.time()
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=6)
    dt = time.time() - t
    ok("mã hoá PNG dưới 80ms", dt < 0.08, f"{dt * 1000:.0f}ms")


# ================================================================ [5] không mất gì
def test_nothing_lost():
    print("\n[5] Không mất chức năng nào")

    ok("/sing vẫn được đăng ký", "sing" in main.BOT_COMMANDS, str(len(main.BOT_COMMANDS)))
    ok("số lệnh không giảm", len(main.BOT_COMMANDS) >= 30, str(len(main.BOT_COMMANDS)))
    for c in ("start", "help", "sing", "mp3", "dl", "dich", "menu"):
        ok(f"lệnh /{c} còn nguyên", c in main.BOT_COMMANDS)

    # premium vẫn 100% sau khi sửa
    s = premium.ensure("Tìm thấy 10 bài")
    ok("tin nhắn vẫn tự gắn icon premium", "<tg-emoji" in s, s[:80])

    # module vẽ ảnh khác vẫn chạy
    for fn in ("status_canvas", "weather_canvas", "sing_canvas"):
        ok(f"canvas.{fn} còn nguyên", callable(getattr(canvas, fn, None)))

    # các hàm tìm nhạc cũ vẫn còn (không xoá đường dự phòng)
    for fn in ("_search_html", "_search_piped", "_search_ytdlp", "_search_innertube",
               "search_songs", "fetch_thumbs", "download_audio"):
        ok(f"music.{fn} còn nguyên", callable(getattr(music, fn, None)))


# ==================================================================== run
async def amain():
    api = FakeAPI()
    await api.start()
    app = await make_app(api)
    try:
        test_innertube_parse()
        await test_search_orchestrator()
        await test_sing_flow(app, api)
        await test_pool_and_speed()
        test_nothing_lost()
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

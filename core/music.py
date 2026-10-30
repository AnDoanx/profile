#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tìm nhạc YouTube (10 kết quả) + tải audio nhiều tuyến dự phòng."""

import asyncio
import io
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time

import yt_dlp

from config import DATA_DIR, MAX_AUDIO_MB, TMP_DIR
from core import http, pool
from core.text import human_duration, parse_duration

log = logging.getLogger("khoaan.music")

UA_MOBILE = "com.google.android.youtube/19.29.35 (Linux; U; Android 14) gzip"

PIPED_INSTANCES = [
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.adminforge.de",
    "https://api.piped.private.coffee",
    "https://pipedapi.reallyaweso.me",
]
INVIDIOUS_INSTANCES = [
    "https://inv.nadeko.net",
    "https://invidious.nerdvpn.de",
    "https://yewtu.be",
]

# ---- hiệu năng: cache + ngân sách thời gian (giây)
SEARCH_TTL = 3600          # cache kết quả tìm kiếm 1 giờ (trước 30 phút)
SEARCH_BUDGET = 7          # trần cứng cho khâu tìm bài (trước 12s)
FAST_BUDGET = 2.8          # cửa sổ ưu tiên cho 2 nguồn nhanh (InnerTube + HTML)
THUMB_BUDGET = 2.5         # chờ ảnh bìa (thiếu thì canvas tự vẽ ô trống)
YTDLP_COOLDOWN = 150       # yt-dlp lỗi -> nghỉ 150s, khỏi bắt user chờ vô ích
CACHE_FILE = DATA_DIR / "search_cache.json"
CACHE_DISK_MAX = 300       # giữ tối đa 300 từ khoá trên đĩa
CACHE_DISK_TTL = 3 * 86400 # cache đĩa sống 3 ngày

# InnerTube = API tìm kiếm chính thức của YouTube (JSON, không cần cookie).
# Đây là đường nhanh + ổn định nhất trên IP máy chủ: trang HTML thường bị
# chặn/consent còn endpoint này thì không.
INNERTUBE_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
INNERTUBE_URL = "https://www.youtube.com/youtubei/v1/search"
INNERTUBE_CLIENT = {
    "clientName": "WEB",
    "clientVersion": "2.20240801.00.00",
}

_SEARCH_CACHE: dict[str, tuple[float, list[dict]]] = {}
_THUMB_CACHE: dict[str, bytes] = {}
_INFLIGHT: dict[str, asyncio.Task] = {}   # gộp request trùng từ khoá
_YTDLP_DOWN_UNTIL = 0.0                   # mốc hết cooldown của yt-dlp
_CACHE_DIRTY = False                      # có thay đổi chưa ghi ra đĩa
_CACHE_LOADED = False

# Cache audio đã tải: bấm lại 1 bài trong 30 phút là trả ngay, không gọi YouTube nữa
AUDIO_TTL = 1800
_AUDIO_CACHE: dict[str, tuple[float, bytes, dict]] = {}
_AUDIO_CACHE_BYTES = 0
_AUDIO_CACHE_MAX = 120 * 1024 * 1024     # giữ tối đa ~120MB trong RAM


def _audio_cache_get(video_id: str):
    hit = _AUDIO_CACHE.get(video_id)
    if not hit:
        return None
    ts, data, info = hit
    if time.time() - ts > AUDIO_TTL:
        _AUDIO_CACHE.pop(video_id, None)
        return None
    return data, dict(info)


def _audio_cache_put(video_id: str, data: bytes, info: dict) -> None:
    global _AUDIO_CACHE_BYTES
    if not data or len(data) > 40 * 1024 * 1024:
        return
    if _AUDIO_CACHE_BYTES + len(data) > _AUDIO_CACHE_MAX:
        _AUDIO_CACHE.clear()
        _AUDIO_CACHE_BYTES = 0
    _AUDIO_CACHE[video_id] = (time.time(), data, dict(info))
    _AUDIO_CACHE_BYTES += len(data)


def _load_disk_cache() -> None:
    """Nạp cache đã lưu trên đĩa -> từ khoá hot trả ngay cả sau khi restart."""
    global _CACHE_LOADED
    if _CACHE_LOADED:
        return
    _CACHE_LOADED = True
    try:
        if not CACHE_FILE.exists():
            return
        raw = json.loads(CACHE_FILE.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return
    now = time.time()
    for k, v in (raw.get("items") or {}).items():
        try:
            ts, items = float(v[0]), list(v[1])
        except Exception:  # noqa: BLE001
            continue
        if not items or now - ts > CACHE_DISK_TTL:
            continue
        _SEARCH_CACHE[k] = (ts, items)


def _save_disk_cache() -> None:
    """Ghi cache ra đĩa. Chạy trong pool IO nên không chặn event loop."""
    global _CACHE_DIRTY
    if not _CACHE_DIRTY:
        return
    _CACHE_DIRTY = False
    try:
        now = time.time()
        keep = {k: [ts, items] for k, (ts, items) in _SEARCH_CACHE.items()
                if now - ts <= CACHE_DISK_TTL}
        if len(keep) > CACHE_DISK_MAX:
            keep = dict(sorted(keep.items(), key=lambda kv: kv[1][0], reverse=True)[:CACHE_DISK_MAX])
        tmp = CACHE_FILE.with_name(CACHE_FILE.name + ".tmp")
        tmp.write_text(json.dumps({"saved": now, "items": keep}, ensure_ascii=False), "utf-8")
        tmp.replace(CACHE_FILE)
    except Exception as e:  # noqa: BLE001
        log.info("lưu cache tìm nhạc lỗi (bỏ qua): %s", str(e)[:80])


def _cache_get(keyword: str) -> list[dict] | None:
    _load_disk_cache()
    hit = _SEARCH_CACHE.get(keyword)
    if not hit:
        return None
    ts, items = hit
    if time.time() - ts > SEARCH_TTL:
        _SEARCH_CACHE.pop(keyword, None)
        return None
    return list(items)


def _cache_put(keyword: str, items: list[dict]) -> None:
    global _CACHE_DIRTY
    if len(_SEARCH_CACHE) > 400:
        _SEARCH_CACHE.clear()
    _SEARCH_CACHE[keyword] = (time.time(), list(items))
    _CACHE_DIRTY = True


def _fmt_views(txt: str) -> str:
    """Chuẩn hoá '186.584.525 lượt xem' / '1.2M views' -> '186.6M views'."""
    if not txt:
        return ""
    low = txt.lower()
    m = re.search(r"([\d][\d.,]*)\s*(k|m|b|tr|n|nghìn|triệu)?", low)
    if not m:
        return ""
    raw = m.group(1).rstrip(".,")
    unit = (m.group(2) or "").strip()
    if raw.count(".") > 1:
        raw = raw.replace(".", "")
    elif raw.count(",") > 1:
        raw = raw.replace(",", "")
    elif "." in raw:
        head, _, tail = raw.rpartition(".")
        raw = raw.replace(".", "") if len(tail) == 3 else raw
    elif "," in raw:
        head, _, tail = raw.rpartition(",")
        raw = raw.replace(",", "") if len(tail) == 3 else raw.replace(",", ".")
    try:
        num = float(raw)
    except ValueError:
        return ""
    mult = {"k": 1_000, "n": 1_000, "nghìn": 1_000, "m": 1_000_000, "tr": 1_000_000,
            "triệu": 1_000_000, "b": 1_000_000_000}.get(unit, 1)
    val = num * mult
    if val >= 1_000_000_000:
        return f"{val / 1_000_000_000:.1f}B views".replace(".0B", "B")
    if val >= 1_000_000:
        return f"{val / 1_000_000:.1f}M views".replace(".0M", "M")
    if val >= 1_000:
        return f"{val / 1_000:.0f}K views"
    return f"{val:.0f} views"


def _parse_initial_data(html: str, limit: int) -> list[dict]:
    match = re.search(r"var ytInitialData\s*=\s*({.+?});</script>", html)
    if not match:
        match = re.search(r'window\["ytInitialData"\]\s*=\s*({.+?});', html)
    if not match:
        return []
    try:
        data = json.loads(match.group(1))
    except Exception:  # noqa: BLE001
        return []

    sections = (
        data.get("contents", {})
        .get("twoColumnSearchResultsRenderer", {})
        .get("primaryContents", {})
        .get("sectionListRenderer", {})
        .get("contents", [])
    )
    out: list[dict] = []
    for sec in sections:
        for item in sec.get("itemSectionRenderer", {}).get("contents", []):
            v = item.get("videoRenderer") or item.get("compactVideoRenderer")
            if not v or "videoId" not in v:
                continue
            title = ""
            runs = (v.get("title") or {}).get("runs") or []
            if runs:
                title = "".join(r.get("text", "") for r in runs)
            if not title:
                title = (v.get("title") or {}).get("simpleText", "")
            ch_runs = ((v.get("ownerText") or {}).get("runs")
                       or (v.get("longBylineText") or {}).get("runs") or [])
            channel = ch_runs[0].get("text", "YouTube") if ch_runs else "YouTube"
            dur_text = (v.get("lengthText") or {}).get("simpleText", "")
            views = _fmt_views((v.get("viewCountText") or {}).get("simpleText", "")
                               or (v.get("shortViewCountText") or {}).get("simpleText", ""))
            published = (v.get("publishedTimeText") or {}).get("simpleText", "")
            vid = v["videoId"]
            out.append({
                "id": vid,
                "title": title or "Không rõ",
                "channel": channel,
                "duration_text": dur_text or human_duration(parse_duration(dur_text)),
                "duration": parse_duration(dur_text),
                "views_text": views,
                "published": published,
                "url": f"https://www.youtube.com/watch?v={vid}",
                "thumb": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
            })
            if len(out) >= limit:
                return out
    return out


def _make_track(v: dict) -> dict | None:
    """Đổi 1 videoRenderer (InnerTube / HTML) sang dict nội bộ của bot."""
    vid = v.get("videoId")
    if not vid:
        return None
    title = "".join(r.get("text", "") for r in ((v.get("title") or {}).get("runs") or []))
    if not title:
        title = (v.get("title") or {}).get("simpleText", "")
    ch_runs = ((v.get("ownerText") or {}).get("runs")
               or (v.get("longBylineText") or {}).get("runs") or [])
    channel = ch_runs[0].get("text", "YouTube") if ch_runs else "YouTube"
    dur_text = (v.get("lengthText") or {}).get("simpleText", "")
    views = _fmt_views((v.get("viewCountText") or {}).get("simpleText", "")
                       or (v.get("shortViewCountText") or {}).get("simpleText", ""))
    published = (v.get("publishedTimeText") or {}).get("simpleText", "")
    return {
        "id": vid,
        "title": title or "Không rõ",
        "channel": channel,
        "duration_text": dur_text or human_duration(parse_duration(dur_text)),
        "duration": parse_duration(dur_text),
        "views_text": views,
        "published": published,
        "url": f"https://www.youtube.com/watch?v={vid}",
        "thumb": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
    }


def _collect_tracks(obj, out: list[dict], limit: int) -> None:
    """Đi đệ quy tìm videoRenderer trong JSON InnerTube (cấu trúc hay đổi)."""
    if len(out) >= limit:
        return
    if isinstance(obj, dict):
        v = obj.get("videoRenderer") or obj.get("compactVideoRenderer")
        if v:
            tr = _make_track(v)
            if tr:
                out.append(tr)
                if len(out) >= limit:
                    return
        for val in obj.values():
            _collect_tracks(val, out, limit)
            if len(out) >= limit:
                return
    elif isinstance(obj, list):
        for val in obj:
            _collect_tracks(val, out, limit)
            if len(out) >= limit:
                return


async def _search_innertube(keyword: str, limit: int) -> list[dict]:
    """Nguồn nhanh nhất: API InnerTube của YouTube (JSON, không cần cookie).

    Trên IP máy chủ, trang `/results` hay bị consent/403; endpoint này thì không.
    Thực đo: ~0.4s cho 10–13 kết quả.
    """
    body = {
        "context": {"client": {**INNERTUBE_CLIENT, "hl": "vi", "gl": "VN"}},
        "query": keyword,
    }
    status, data, _ = await http.fetch(
        f"{INNERTUBE_URL}?key={INNERTUBE_KEY}&prettyPrint=false",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        },
        json_data=body,
        timeout=4,
        retries=0,
    )
    if status != 200 or not data:
        return []
    try:
        payload = json.loads(data.decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return []
    out: list[dict] = []
    _collect_tracks(payload, out, limit)
    return out


async def _search_html(keyword: str, limit: int) -> list[dict]:
    import urllib.parse

    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(keyword) + "&hl=vi&gl=VN&sp=EgIQAQ%3D%3D"
    status, body, _ = await http.fetch(
        url, headers={"Accept-Language": "vi-VN,vi;q=0.9"}, timeout=4, retries=0,
    )
    if status != 200 or not body:
        return []
    return _parse_initial_data(body.decode("utf-8", errors="replace"), limit)


def _search_ytdlp(keyword: str, limit: int) -> list[dict]:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "no_cache_dir": True,
        "socket_timeout": 8,
        "noprogress": True,
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    }
    out = []
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{limit}:{keyword}", download=False)
    except Exception as err:  # noqa: BLE001
        log.info("yt-dlp search fail: %s", str(err)[:120])
        return []
    for ent in (info or {}).get("entries") or []:
        if not ent or not ent.get("id"):
            continue
        dur = int(ent.get("duration") or 0)
        vid = ent["id"]
        out.append({
            "id": vid,
            "title": ent.get("title") or "Không rõ",
            "channel": ent.get("channel") or ent.get("uploader") or "YouTube",
            "duration": dur,
            "duration_text": human_duration(dur),
            "views_text": f"{ent.get('view_count'):,} views" if ent.get("view_count") else "",
            "url": f"https://www.youtube.com/watch?v={vid}",
            "thumb": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
        })
    return out


async def _search_piped(keyword: str, limit: int) -> list[dict]:
    import urllib.parse

    q = urllib.parse.quote_plus(keyword)

    async def one(base: str) -> list[dict]:
        data = await http.fetch_json(f"{base}/search?q={q}&filter=videos", timeout=4, retries=0)
        if not data or not data.get("items"):
            return []
        out = []
        for it in data["items"]:
            if it.get("type") != "stream":
                continue
            vid = (it.get("url") or "").split("v=")[-1]
            if not vid:
                continue
            out.append({
                "id": vid,
                "title": it.get("title") or "Không rõ",
                "channel": it.get("uploaderName") or "YouTube",
                "duration": int(it.get("duration") or 0),
                "duration_text": human_duration(it.get("duration") or 0),
                "views_text": f"{int(it.get('views') or 0):,} views",
                "url": f"https://www.youtube.com/watch?v={vid}",
                "thumb": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
            })
            if len(out) >= limit:
                break
        return out

    # Các instance piped chết rất khác nhau -> lấy cái nào trả lời trước,
    # không đợi tất cả (trước đây gather làm chậm thêm vài giây vì 1 instance treo).
    return await _race_lists([one(b) for b in PIPED_INSTANCES], timeout=4.5, need=3)


async def _race_lists(coros: list, timeout: float, need: int = 3) -> list[dict]:
    """Chạy nhiều nguồn song song; nguồn nào xong trước mà đủ `need` bài thì lấy luôn."""
    tasks = [asyncio.ensure_future(c) for c in coros]
    best: list[dict] = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    pending = set(tasks)
    try:
        while pending:
            left = deadline - loop.time()
            if left <= 0:
                break
            done, pending = await asyncio.wait(
                pending, timeout=left, return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                break
            for t in done:
                if t.cancelled() or t.exception() is not None:
                    continue
                res = t.result() or []
                if len(res) > len(best):
                    best = res
            if len(best) >= need:
                break
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
    return best


async def _collect(jobs: dict, need: int, deadline: float) -> list[dict]:
    """Chờ các job trong `jobs`, trả về danh sách DÀI NHẤT lấy được trước `deadline`.

    Dừng ngay khi có nguồn đạt `need` bài -> không bắt người dùng chờ nguồn chậm.
    """
    best: list[dict] = []
    for t in jobs:
        if t.done() and not t.cancelled() and t.exception() is None:
            try:
                res = t.result() or []
            except Exception:  # noqa: BLE001
                continue
            if len(res) > len(best):
                best = res
    if len(best) >= need:
        return best

    pending = {t for t in jobs if not t.done()}
    while pending:
        left = deadline - time.time()
        if left <= 0:
            break
        done, pending = await asyncio.wait(
            pending, timeout=left, return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            break
        for t in done:
            if t.cancelled() or t.exception() is not None:
                continue
            res = t.result() or []
            if len(res) > len(best):
                best = res
        if len(best) >= need:
            break
    return best


async def _search_race(keyword: str, limit: int) -> list[dict]:
    """InnerTube + HTML + Piped chạy song song ngay từ đầu (cửa sổ FAST_BUDGET).

    Chỉ khi 2.8s mà vẫn chưa đủ bài mới bật yt-dlp (pool IO riêng, có cooldown).
    Trần cứng SEARCH_BUDGET giây. Luôn trả list (rỗng nếu chết hết).

    Khác bản cũ: yt-dlp giờ được bật ngay khi hết cửa sổ nhanh, không phải đợi
    "không có job nào xong" — đó là lý do bản cũ hay treo tới 12s.
    """
    global _YTDLP_DOWN_UNTIL
    need = min(3, limit)
    deadline = time.time() + SEARCH_BUDGET
    fast_until = time.time() + FAST_BUDGET

    fast_jobs = {
        asyncio.ensure_future(_search_innertube(keyword, limit)): "innertube",
        asyncio.ensure_future(_search_html(keyword, limit)): "html",
        asyncio.ensure_future(_search_piped(keyword, limit)): "piped",
    }
    ytdlp_job = None
    best: list[dict] = []
    try:
        best = await _collect(fast_jobs, need, fast_until)
        if len(best) >= need:
            return best[:limit]

        # Nguồn nhanh chưa đủ -> mới cần yt-dlp (trừ khi nó vừa lỗi, đang nghỉ)
        if time.time() >= _YTDLP_DOWN_UNTIL:
            ytdlp_job = asyncio.ensure_future(pool.run_io(_search_ytdlp, keyword, limit))

        rest = {t: n for t, n in fast_jobs.items() if not t.done()}
        if ytdlp_job is not None:
            rest[ytdlp_job] = "ytdlp"
        more = await _collect(rest, need, deadline)
        if len(more) > len(best):
            best = more

        # yt-dlp im lặng/lỗi -> cho nghỉ một lúc, lần sau khỏi bắt user chờ
        if ytdlp_job is not None:
            failed = False
            if ytdlp_job.done() and not ytdlp_job.cancelled():
                try:
                    failed = not (ytdlp_job.result() or [])
                except Exception:  # noqa: BLE001
                    failed = True
            else:
                failed = True
            if failed:
                _YTDLP_DOWN_UNTIL = time.time() + YTDLP_COOLDOWN
        return best[:limit]
    finally:
        for t in fast_jobs:
            if not t.done():
                t.cancel()
        if ytdlp_job is not None and not ytdlp_job.done():
            ytdlp_job.cancel()


async def _search_and_cache(keyword: str, key: str, limit: int) -> list[dict]:
    results = await _search_race(keyword, limit)
    if results:
        _cache_put(key, results)
        try:
            await pool.run_io(_save_disk_cache)
        except Exception as e:  # noqa: BLE001
            log.info("ghi cache lỗi (bỏ qua): %s", str(e)[:80])
    return results


async def search_songs(keyword: str, limit: int = 10) -> list[dict]:
    """Tìm bài hát: cache (RAM + đĩa) -> nguồn nhanh -> yt-dlp.

    Nhiều người cùng tìm 1 từ khoá thì chỉ gọi mạng 1 lần (gộp request).
    Luôn trả list (rỗng nếu chết hết).
    """
    keyword = (keyword or "").strip()
    if not keyword:
        return []

    key = keyword.lower()
    cached = _cache_get(key)
    if cached:
        return cached[:limit]

    task = _INFLIGHT.get(key)
    if task is None or task.done():
        task = asyncio.ensure_future(_search_and_cache(keyword, key, limit))
        _INFLIGHT[key] = task

        def _cleanup(t, _k=key):
            if _INFLIGHT.get(_k) is t:
                _INFLIGHT.pop(_k, None)

        task.add_done_callback(_cleanup)

    try:
        # shield: người bấm trước huỷ (gõ lại lệnh) thì lần gọi mạng vẫn chạy tiếp,
        # người sau dùng lại kết quả đó thay vì gọi lại từ đầu.
        results = await asyncio.shield(task)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("search_songs lỗi: %s", str(e)[:120])
        return []
    return (results or [])[:limit]


async def fetch_thumbs(tracks: list[dict], budget: float = THUMB_BUDGET) -> list[bytes | None]:
    """Lấy ảnh bìa song song, tối đa `budget` giây. Thiếu ảnh -> None (canvas tự vẽ ô)."""
    if not tracks:
        return []

    async def one(t: dict) -> bytes | None:
        vid = (t.get("id") or "").strip()
        if vid:
            hit = _THUMB_CACHE.get(vid)
            if hit:
                return hit
        # Ghép URL từ video id: nhanh và không lệ thuộc trường `thumb` của từng nguồn.
        url = f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg" if vid else (t.get("thumb") or "")
        if not url:
            return None
        data = await http.download_bytes(url, max_bytes=400_000, timeout=3)
        if not data and vid:
            data = await http.download_bytes(
                f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg", max_bytes=700_000, timeout=3,
            )
        if data and vid:
            if len(_THUMB_CACHE) > 500:
                _THUMB_CACHE.clear()
            _THUMB_CACHE[vid] = data
        return data

    # 10 ảnh chạy cùng lúc (trước giới hạn 8 -> phải chờ 2 lượt)
    jobs = [asyncio.create_task(one(t)) for t in tracks]
    if jobs:
        await asyncio.wait(jobs, timeout=budget)
    out: list[bytes | None] = []
    for j in jobs:
        if j.done() and not j.cancelled() and j.exception() is None:
            out.append(j.result())
        else:
            j.cancel()
            out.append(None)
    return out


# ---------------------------------------------------------------- download
def _ytdlp_attempt(video_id: str, client: list[str], ext_pref: str = "m4a") -> tuple[bytes | None, dict]:
    url = f"https://www.youtube.com/watch?v={video_id}"
    out_dir = tempfile.mkdtemp(dir=str(TMP_DIR))
    out_tmpl = os.path.join(out_dir, "%(id)s.%(ext)s")
    opts = {
        # dùng chuỗi dự phòng rộng: client nào cũng lấy được 1 luồng audio
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": out_tmpl,
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": True,
        "no_cache_dir": True,
        "max_filesize": MAX_AUDIO_MB * 1024 * 1024,
        "retries": 1,
        "socket_timeout": 10,
        "noprogress": True,
        "concurrent_fragment_downloads": 8,
        **({"extractor_args": {"youtube": {"player_client": client}}} if client else {}),
        "http_headers": {"User-Agent": UA_MOBILE},
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            meta = ydl.extract_info(url, download=True) or {}
            files = [f for f in os.listdir(out_dir) if not f.endswith(".part")]
            if not files:
                return None, {}
            path = os.path.join(out_dir, files[0])
            with open(path, "rb") as fh:
                data = fh.read()
            ext = path.rsplit(".", 1)[-1].lower()
            info = {
                "title": meta.get("title") or "YouTube Audio",
                "uploader": meta.get("uploader") or meta.get("channel") or "YouTube",
                "duration": meta.get("duration") or 0,
                "thumb": meta.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
                "ext": ext,
                "id": video_id,
            }
            shutil.rmtree(out_dir, ignore_errors=True)
            return data, info
    except Exception as e:  # noqa: BLE001
        log.warning("yt-dlp audio fail (%s): %s", client, str(e)[:160])
        shutil.rmtree(out_dir, ignore_errors=True)
        return None, {}


async def _piped_audio(video_id: str) -> tuple[bytes | None, dict]:
    async def one(base: str) -> tuple[bytes | None, dict]:
        data = await http.fetch_json(f"{base}/streams/{video_id}", timeout=8, retries=0)
        if not data:
            return None, {}
        streams = [s for s in (data.get("audioStreams") or []) if s.get("url")]
        if not streams:
            return None, {}
        streams.sort(key=lambda s: s.get("bitrate") or 0, reverse=True)
        for s in streams[:2]:
            raw = await http.download_bytes(s["url"], max_bytes=MAX_AUDIO_MB * 1024 * 1024, timeout=90)
            if raw:
                return raw, {
                    "title": data.get("title") or "YouTube Audio",
                    "uploader": data.get("uploader") or "YouTube",
                    "duration": data.get("duration") or 0,
                    "thumb": data.get("thumbnailUrl") or "",
                    "ext": "m4a" if "mp4" in (s.get("mimeType") or "") else "webm",
                    "id": video_id,
                }
        return None, {}

    results = await asyncio.gather(*(one(b) for b in PIPED_INSTANCES), return_exceptions=True)
    for r in results:
        if isinstance(r, tuple) and r[0]:
            return r
    return None, {}


async def _invidious_audio(video_id: str) -> tuple[bytes | None, dict]:
    async def one(base: str) -> tuple[bytes | None, dict]:
        data = await http.fetch_json(
            f"{base}/api/v1/videos/{video_id}?fields=title,author,lengthSeconds,adaptiveFormats",
            timeout=8, retries=0,
        )
        if not data:
            return None, {}
        fmts = [f for f in (data.get("adaptiveFormats") or []) if str(f.get("type", "")).startswith("audio")]
        fmts.sort(key=lambda f: int(f.get("bitrate") or 0), reverse=True)
        for f in fmts[:2]:
            u = f.get("url")
            if not u:
                continue
            raw = await http.download_bytes(u, max_bytes=MAX_AUDIO_MB * 1024 * 1024, timeout=90)
            if raw:
                mime = str(f.get("type") or "")
                return raw, {
                    "title": data.get("title") or "YouTube Audio",
                    "uploader": data.get("author") or "YouTube",
                    "duration": data.get("lengthSeconds") or 0,
                    "thumb": "",
                    "ext": "m4a" if "mp4" in mime else "webm",
                    "id": video_id,
                }
        return None, {}

    results = await asyncio.gather(*(one(b) for b in INVIDIOUS_INSTANCES), return_exceptions=True)
    for r in results:
        if isinstance(r, tuple) and r[0]:
            return r
    return None, {}


def _to_mp3(data: bytes, ext: str) -> tuple[bytes, str]:
    """Đổi sang mp3 nếu máy có ffmpeg, không thì giữ nguyên."""
    if ext in ("mp3", "m4a"):
        return data, ext
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return data, ext
    tmp_in = tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False, dir=str(TMP_DIR))
    tmp_in.write(data)
    tmp_in.close()
    tmp_out = tmp_in.name.rsplit(".", 1)[0] + ".mp3"
    try:
        subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-i", tmp_in.name, "-vn",
             "-acodec", "libmp3lame", "-b:a", "128k", tmp_out],
            check=True, timeout=120,
        )
        with open(tmp_out, "rb") as fh:
            out = fh.read()
        return out, "mp3"
    except Exception as e:  # noqa: BLE001
        log.warning("ffmpeg convert fail: %s", e)
        return data, ext
    finally:
        for p in (tmp_in.name, tmp_out):
            try:
                os.remove(p)
            except OSError:
                pass


async def download_audio(video_id: str, timeout: int = 110) -> tuple[io.BytesIO | None, dict]:
    """Cache -> yt-dlp (tự chọn client) + piped chạy song song -> client cụ thể."""
    hit = _audio_cache_get(video_id)
    if hit:
        data, info = hit
        return _wrap(data, video_id, info.get("ext", "mp3")), info
    try:
        return await asyncio.wait_for(_download_audio_inner(video_id), timeout=timeout)
    except asyncio.TimeoutError:
        log.warning("download_audio quá thời gian %ss cho %s", timeout, video_id)
        return None, {}


async def _download_audio_inner(video_id: str) -> tuple[io.BytesIO | None, dict]:
    async def finish(data: bytes | None, info: dict):
        if not data:
            return None
        data, ext = await pool.run_io(_to_mp3, data, info.get("ext", "m4a"))
        info["ext"] = ext
        _audio_cache_put(video_id, data, info)
        return _wrap(data, video_id, ext), info

    async def via_ytdlp(clients: list[str]):
        data, info = await pool.run_io(_ytdlp_attempt, video_id, clients)
        return await finish(data, info or {})

    async def via_piped():
        return await finish(*await _piped_audio(video_id))

    async def via_invidious():
        return await finish(*await _invidious_audio(video_id))

    # Vòng 1: chỉ 2 tuyến (yt-dlp tự chọn client + piped) — ít tiến trình, đỡ tranh CPU,
    # thực tế nhanh hơn hẳn kiểu 5 tuyến cùng lúc.
    first = [asyncio.create_task(via_ytdlp([])), asyncio.create_task(via_piped())]
    got = await _race(first, timeout=45)
    if got:
        return got

    # Vòng 2: client cụ thể + invidious
    second = [asyncio.create_task(via_ytdlp(["android", "ios"])),
              asyncio.create_task(via_ytdlp(["web_safari", "mweb"])),
              asyncio.create_task(via_invidious())]
    got = await _race(second, timeout=60)
    if got:
        return got
    return None, {}


async def _race(jobs: list, timeout: int):
    """Chờ job đầu tiên trả kết quả; hết giờ thì dừng hết."""
    pending = set(jobs)
    try:
        while pending:
            done, pending = await asyncio.wait(
                pending, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
            if not done:
                return None
            for d in done:
                if d.cancelled() or d.exception() is not None:
                    continue
                res = d.result()
                if res and res[0]:
                    return res
    finally:
        for j in pending:
            j.cancel()
    return None


def _wrap(data: bytes, video_id: str, ext: str) -> io.BytesIO:
    buf = io.BytesIO(data)
    buf.seek(0)
    buf.name = f"{video_id}.{ext}"
    return buf

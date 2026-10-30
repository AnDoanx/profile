#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Nhân tải video/audio — nhanh, ít lỗi, có cache.

Thiết kế v17.1 (sửa lỗi "chậm / lag"):

  1. Bản cũ chạy SONG SONG 3-4 tiến trình yt-dlp, mỗi tiến trình tải trọn một
     bản video. File trùng nhau, băng thông chia tư -> tải cực chậm và nặng máy.
     Bản này: dò (probe) metadata trước, chọn đúng 1 định dạng rồi tải đúng 1
     lần. Băng thông dồn hết cho một luồng duy nhất.

  2. Probe mà bị YouTube chặn ("Sign in to confirm you're not a bot") thì trả
     lỗi ngay sau ~1-2s kèm hướng dẫn nạp cookie, thay vì bắt người dùng chờ
     cả phút rồi mới báo hỏng.

  3. Nếu IP máy chủ bị YouTube chặn hẳn (VPS thường bị), tự rơi xuống lớp dự
     phòng qua các máy chủ Piped công khai — vẫn tải được video/MP3.

  4. Mọi file đã tải được cache 30 phút -> bấm lại là trả ngay.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
import subprocess
import time
from pathlib import Path

import yt_dlp

import config
from core.downloader import DlResult, MediaItem

log = logging.getLogger("khoaan.ytdl")

CACHE_DIR = Path(config.TMP_DIR) / "ytcache"
CACHE_TTL = 1800          # 30 phút
DEAD_TTL = 600            # client chết thì nghỉ 10 phút
PROBE_TIMEOUT = 14        # 1 lần dò metadata
PROBE_BUDGET = 30         # cả vòng dò
DL_TIMEOUT = 900          # trần thời gian 1 lần tải file
PIPED_TIMEOUT = 12        # 1 máy chủ Piped
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# Máy chủ Piped công khai — lớp dự phòng khi IP máy chủ bị YouTube chặn.
PIPED_HOSTS = (
    "api.piped.private.coffee",
    "pipedapi.kavin.rocks",
    "pipedapi.ducks.party",
)

# Nguồn "tải nhanh": máy chủ khác đã bóc sẵn link CDN (savetube) cho mình.
# Đo thực tế: ~3.6 MB/s mỗi kết nối, hỗ trợ Range -> 8 kết nối ~4.2 MB/s,
# nhanh gấp ~9 lần đường proxy Piped cũ (0.4 MB/s). Đây là lý do /ytb hết lag.
FAST_API = "https://apis.davidcyriltech.my.id/download/{kind}"
FAST_TIMEOUT = 30         # 1 lần hỏi API (API này lúc rảnh 3s, lúc bận 10s)
FAST_SPEED_MIN = 0.35     # MB/s — chậm hơn mức này thì thử nguồn khác
MEASURE_SECS = 1.2        # thời gian đo tốc độ mỗi nguồn

_DEAD: dict[str, float] = {}

YT_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/|v/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{6,20})"
)


# ------------------------------------------------------------------ tiện ích
def has_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg"))


def video_id(url: str) -> str:
    m = YT_RE.search(url or "")
    return m.group(1) if m else ""


def _key(url: str) -> str:
    vid = video_id(url)
    if vid:
        return "yt_" + vid
    return hashlib.sha1((url or "").encode("utf-8")).hexdigest()[:20]


def _mk_cache() -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        pass


def cache_bytes() -> int:
    _mk_cache()
    total = 0
    for p in CACHE_DIR.glob("*"):
        try:
            total += p.stat().st_size
        except Exception:  # noqa: BLE001
            pass
    return total


def purge(force: bool = False) -> int:
    """Xoá cache quá hạn (gọi lúc khởi động)."""
    _mk_cache()
    now = time.time()
    n = 0
    for p in CACHE_DIR.glob("*"):
        try:
            if force or now - p.stat().st_mtime > CACHE_TTL:
                p.unlink()
                n += 1
        except Exception:  # noqa: BLE001
            pass
    return n


def _cached(key: str, kind: str) -> Path | None:
    _mk_cache()
    now = time.time()
    best: Path | None = None
    for p in CACHE_DIR.glob(key + "*"):
        if p.suffix in (".part", ".ytdl", ".tmp"):
            continue
        if p.name.endswith(".info.json"):
            continue
        try:
            if now - p.stat().st_mtime > CACHE_TTL or p.stat().st_size < 1024:
                continue
        except Exception:  # noqa: BLE001
            continue
        # video thì bỏ qua file audio, và ngược lại
        if kind == "audio" and p.suffix.lower() in (".mp4", ".webm", ".mkv"):
            continue
        if kind == "video" and p.suffix.lower() in (".m4a", ".mp3", ".opus"):
            continue
        if best is None or p.stat().st_mtime > best.stat().st_mtime:
            best = p
    return best


def _write_meta(key: str, info: dict) -> None:
    try:
        (CACHE_DIR / f"{key}.info.json").write_text(
            json.dumps({k: info.get(k) for k in
                        ("title", "uploader", "duration", "view_count", "thumbnail")},
                       ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _read_meta(key: str) -> dict:
    try:
        return json.loads((CACHE_DIR / f"{key}.info.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _formats(kind: str, height: int) -> list[str]:
    """Thang format dự phòng (khi không dò được metadata)."""
    ff = has_ffmpeg()
    if kind == "audio":
        return ["ba[ext=m4a]/ba/b", "b"]
    if ff:
        return [
            f"b[height<={height}][ext=mp4]"
            f"/bv*[height<={height}][ext=mp4]+ba[ext=m4a]"
            f"/bv*[height<={height}]+ba"
            f"/b[height<={height}]/bv*+ba/b",
            "b[ext=mp4]/b",
        ]
    return [f"b[height<={height}][ext=mp4]/b[ext=mp4]/b", "b"]


def _clients() -> list[list[str] | None]:
    return [["android", "ios"], ["web_safari", "mweb"], ["tv"], ["android_vr"], None]


def _is_dead(tag: str) -> bool:
    t = _DEAD.get(tag)
    if not t:
        return False
    if time.time() - t > DEAD_TTL:
        _DEAD.pop(tag, None)
        return False
    return True


def _mark_dead(tag: str) -> None:
    _DEAD[tag] = time.time()


FATAL = ("video unavailable", "private video", "removed by the uploader",
         "this video is not available", "has been removed", "account has been terminated",
         "unsupported url", "not a valid url")

BOT_CHECK = ("sign in to confirm", "not a bot", "confirm you", "use --cookies",
             "cookies for the authentication", "please sign in")


def _fatal(msg: str) -> bool:
    low = (msg or "").lower()
    return any(f in low for f in FATAL)


def _bot_check(msg: str) -> bool:
    low = (msg or "").lower()
    return any(f in low for f in BOT_CHECK)


FRIENDLY = (
    (("sign in to confirm", "not a bot", "confirm you", "use --cookies",
      "cookies for the authentication"),
     "YouTube đang bắt xác minh máy chủ (chặn IP). Nạp cookie 1 lần là tải được: "
     "/cookie youtube → gửi file cookies.txt"),
    (("private video", "this video is private"),
     "Video để riêng tư, chỉ chủ tài khoản xem được (nạp /cookie youtube nếu là của bạn)."),
    (("members-only", "join this channel", "membership"),
     "Video dành cho hội viên kênh — cần cookie tài khoản đã tham gia (/cookie youtube)."),
    (("confirm your age", "age-restricted", "inappropriate for some users"),
     "Video giới hạn độ tuổi — cần cookie tài khoản đã đăng nhập (/cookie youtube)."),
    (("not available in your country", "geo restricted", "blocked it in your country"),
     "Video chặn theo quốc gia, máy chủ này không lấy được."),
    (("video unavailable", "this video is unavailable", "removed by the uploader",
      "has been removed", "account has been terminated", "does not exist"),
     "Video đã bị xoá hoặc không còn tồn tại."),
    (("is not a valid url", "unsupported url"),
     "Link không hợp lệ."),
    (("timed out", "timeout", "read operation timed out", "connection reset",
      "temporary failure", "getaddrinfo", "ssl"),
     "Mạng ra ngoài đang chậm/chập chờn — thử lại giúp mình 1 lần nữa."),
)

SAFE = (
    (("requested format", "no video formats", "only images are available"),
     "Nguồn không có định dạng phù hợp."),
    (("ffmpeg", "ffprobe"),
     "Thiếu ffmpeg trên máy chủ (cài ffmpeg là tải được)."),
)


def friendly(msg: str) -> str:
    """Đổi lỗi kỹ thuật của yt-dlp thành câu tiếng Việt dễ hiểu."""
    low = (msg or "").lower()
    for keys, text in FRIENDLY + SAFE:
        if any(k in low for k in keys):
            return text
    first = (msg or "").strip().splitlines()[0][:160] if (msg or "").strip() else ""
    if not first or first.lower().startswith(("không tải được", "khong tai duoc")):
        return "Không tải được — thử lại giúp mình 1 lần nữa."
    return f"Không tải được. {first}"


# ------------------------------------------------------------------ yt-dlp
class _Quiet:
    """Nuốt log của yt-dlp — bot không cần in 'Sign in to confirm…' ra màn hình."""

    def debug(self, msg):  # noqa: D401
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


_QUIET = _Quiet()


def _base_opts() -> dict:
    return {
        "logger": _QUIET,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "nocheckcertificate": True,
        "cachedir": False,
        "socket_timeout": 10,
        "http_headers": {"User-Agent": UA},
        "geo_bypass": True,
    }


def _probe_sync(url: str, opts: dict) -> dict:
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if isinstance(info, dict) and info.get("entries"):
        entries = [e for e in info["entries"] if e]
        if entries:
            info = entries[0]
    return info if isinstance(info, dict) else {}


async def _probe_one(url: str, cl: list[str] | None,
                     cookies_file: str | None) -> tuple[dict, str, list[str] | None]:
    """Dò metadata bằng 1 bộ client (chạy song song với các bộ khác)."""
    tag = ",".join(cl) if cl else "auto"
    opts = _base_opts()
    opts["skip_download"] = True
    if cl:
        opts["extractor_args"] = {"youtube": {"player_client": cl}}
    if cookies_file:
        opts["cookiefile"] = cookies_file
    try:
        info = await asyncio.wait_for(
            asyncio.to_thread(_probe_sync, url, opts), timeout=PROBE_TIMEOUT
        )
    except asyncio.TimeoutError:
        _mark_dead(tag)
        return {}, f"timeout ({tag})", None
    except Exception as e:  # noqa: BLE001
        msg = (str(e).splitlines()[0] if str(e) else "lỗi không rõ")[:200]
        if any(k in msg.lower() for k in ("timed out", "timeout", "connection",
                                          "ssl", "getaddrinfo", "temporary failure")):
            _mark_dead(tag)
        return {}, msg, None
    if info.get("formats") or info.get("url"):
        return info, "", cl
    return info, "", cl


async def _probe(url: str, cookies_file: str | None) -> tuple[dict, str, list[str] | None]:
    """Dò metadata: trả (info, lỗi, bộ client chạy được).

    Các bộ client được dò **song song** thay vì lần lượt: trước đây 5 bộ nối
    tiếp nhau mất hơn 5 giây mới biết là bị chặn, giờ cùng lúc nên chỉ còn ~1
    giây — đây là phần làm /ytb "lag" nhất.
    """
    combos = [c for c in _clients() if not _is_dead(",".join(c) if c else "auto")] or [[]]
    combos = combos[:3]
    tasks = [asyncio.create_task(_probe_one(url, cl, cookies_file)) for cl in combos]
    last = ""
    try:
        for fut in asyncio.as_completed(tasks, timeout=PROBE_BUDGET):
            try:
                info, err, cl = await fut
            except Exception as e:  # noqa: BLE001
                last = (str(e).splitlines()[0] if str(e) else "lỗi")[:150]
                continue
            if info:
                for t in tasks:
                    if not t.done():
                        t.cancel()
                return info, "", cl
            last = err or last
    except asyncio.TimeoutError:
        last = last or "timeout"
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
    return {}, last or "không lấy được metadata", None


def _pick_format(info: dict, kind: str, height: int) -> str:
    """Chọn đúng 1 format tốt nhất từ metadata (1 luồng, tải 1 lần)."""
    fmts = [f for f in (info.get("formats") or []) if f.get("url")]
    if not fmts:
        return _formats(kind, height)[0]

    aud = [f for f in fmts if f.get("acodec") not in (None, "none")]
    if kind == "audio":
        only = [f for f in aud if f.get("vcodec") in (None, "none")]
        pool = only or aud
        if pool:
            best = max(pool, key=lambda f: (f.get("abr") or 0, f.get("tbr") or 0))
            return str(best.get("format_id"))
        return _formats(kind, height)[0]

    vids = [f for f in fmts
            if f.get("vcodec") not in (None, "none") and (f.get("height") or 0) <= height]
    if not vids:
        vids = [f for f in fmts if f.get("vcodec") not in (None, "none")]
    prog = [f for f in vids if f.get("acodec") not in (None, "none")]
    if prog:
        # bản ghép sẵn: tải 1 luồng, không cần ffmpeg -> nhanh nhất
        mp4 = [f for f in prog if (f.get("ext") or "") == "mp4"] or prog
        best = max(mp4, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))
        return str(best.get("format_id"))

    vo = [f for f in vids if f.get("acodec") in (None, "none")]
    ao = [f for f in fmts if f.get("vcodec") in (None, "none")
          and f.get("acodec") not in (None, "none")]
    if vo:
        best_v = max(vo, key=lambda f: (f.get("height") or 0,
                                       1 if (f.get("ext") or "") == "mp4" else 0,
                                       f.get("tbr") or 0))
        if ao and has_ffmpeg():
            best_a = max(ao, key=lambda f: (f.get("abr") or 0))
            return f"{best_v.get('format_id')}+{best_a.get('format_id')}"
        return str(best_v.get("format_id"))
    return _formats(kind, height)[0]


def _size_hint(info: dict) -> int:
    for k in ("filesize", "filesize_approx"):
        if info.get(k):
            return int(info[k])
    total = 0
    for f in info.get("requested_formats") or []:
        total += int(f.get("filesize") or f.get("filesize_approx") or 0)
    return total


def _dl_opts(kind: str, fmt: str, clients: list[str] | None, key: str,
             cookies_file: str | None, on_progress=None) -> dict:
    out = _base_opts()
    out.update({
        "format": fmt,
        "outtmpl": str(CACHE_DIR / f"{key}-0.%(ext)s"),
        "retries": 2,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 16,
        "overwrites": True,
        "continuedl": True,
    })
    if clients:
        out["extractor_args"] = {"youtube": {"player_client": clients}}
    if cookies_file:
        out["cookiefile"] = cookies_file
    if kind == "video" and has_ffmpeg():
        out["merge_output_format"] = "mp4"
    if on_progress is not None:
        def _hook(d: dict) -> None:
            try:
                on_progress(d)
            except Exception:  # noqa: BLE001
                pass
        out["progress_hooks"] = [_hook]
    return out


def _run(url: str, opts: dict) -> tuple[dict, str]:
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if isinstance(info, dict) and info.get("entries"):
            entries = [e for e in info["entries"] if e]
            info = entries[0] if entries else info
        hint = ""
        try:
            hint = ydl.prepare_filename(info)
        except Exception:  # noqa: BLE001
            hint = ""
        return (info if isinstance(info, dict) else {}), hint


def _pick_file(key: str, hint: str) -> Path | None:
    cands: list[Path] = []
    if hint:
        p = Path(hint)
        cands.append(p)
        cands.extend(CACHE_DIR.glob(p.stem + ".*"))
    cands.extend(CACHE_DIR.glob(key + "*"))
    best: Path | None = None
    for p in cands:
        if p.suffix in (".part", ".ytdl", ".tmp") or p.name.endswith(".info.json"):
            continue
        try:
            if not p.exists() or p.stat().st_size < 1024:
                continue
        except Exception:  # noqa: BLE001
            continue
        if best is None or p.stat().st_mtime > best.stat().st_mtime:
            best = p
    return best


def to_mp3(src: str | Path, bitrate: str = "192k") -> Path:
    """Chuyển sang mp3 (nếu có ffmpeg). Không có thì trả file gốc."""
    src = Path(src)
    if src.suffix.lower() == ".mp3" or not has_ffmpeg():
        return src
    dst = src.with_suffix(".mp3")
    if dst.exists() and dst.stat().st_size > 1024 and dst.stat().st_mtime >= src.stat().st_mtime:
        return dst
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
             "-vn", "-b:a", bitrate, str(dst)],
            check=True, timeout=300, capture_output=True,
        )
        if dst.exists() and dst.stat().st_size > 1024:
            return dst
    except Exception as e:  # noqa: BLE001
        log.warning("to_mp3 loi: %s", str(e)[:120])
    return src


def _item(path: Path, info: dict, kind: str, platform: str, url: str) -> MediaItem:
    return MediaItem(
        path=str(path),
        kind="audio" if kind == "audio" else "video",
        title=str(info.get("title") or info.get("description") or "")[:180],
        uploader=str(info.get("uploader") or info.get("channel") or info.get("uploader_id") or "")[:120],
        duration=int(float(info.get("duration") or 0)),
        views=int(info.get("view_count") or 0),
        thumbnail=str(info.get("thumbnail") or ""),
        platform=platform or "youtube",
        url=url,
    )


# ------------------------------------------------------------------ Piped (dự phòng)
async def _piped_json(host: str, vid: str) -> dict | None:
    try:
        import aiohttp
        timeout = aiohttp.ClientTimeout(total=PIPED_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(f"https://{host}/streams/{vid}",
                             headers={"User-Agent": UA}) as r:
                if r.status != 200:
                    return None
                data = await r.json(content_type=None)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict) or data.get("error"):
        return None
    if not (data.get("videoStreams") or data.get("audioStreams") or data.get("hls")):
        return None
    return data


async def _piped_meta(vid: str) -> dict | None:
    tasks = [asyncio.create_task(_piped_json(h, vid)) for h in PIPED_HOSTS]
    try:
        for coro in asyncio.as_completed(tasks, timeout=PIPED_TIMEOUT + 4):
            try:
                data = await coro
            except Exception:  # noqa: BLE001
                continue
            if data:
                for t in tasks:
                    if not t.done():
                        t.cancel()
                return data
    except Exception:  # noqa: BLE001
        pass
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
    return None


def _piped_pick(data: dict, kind: str, height: int) -> dict:
    """Chọn 1 luồng tải được từ dữ liệu Piped."""
    vs = [s for s in (data.get("videoStreams") or []) if s.get("url")]
    au = [s for s in (data.get("audioStreams") or []) if s.get("url")]

    def h_of(s: dict) -> int:
        m = re.search(r"(\d{3,4})p", str(s.get("quality") or ""))
        return int(m.group(1)) if m else 0

    prog = [s for s in vs if not s.get("videoOnly")]
    mp4 = [s for s in prog if "mp4" in str(s.get("mimeType") or "")] or prog
    fit = [s for s in mp4 if not h_of(s) or h_of(s) <= height] or mp4
    video = max(fit, key=h_of) if fit else None

    audio = None
    if au:
        m4a = [s for s in au if "mp4" in str(s.get("mimeType") or "")] or au
        audio = max(m4a, key=lambda s: int(s.get("bitrate") or 0))

    if kind == "audio":
        if audio:
            return {"url": audio["url"], "kind": "audio",
                    "ext": ".m4a" if "mp4" in str(audio.get("mimeType")) else ".webm"}
        if video:
            return {"url": video["url"], "kind": "video", "ext": ".mp4", "from_video": True}
        return {}
    if video:
        return {"url": video["url"], "kind": "video", "ext": ".mp4",
                "audio_url": (audio or {}).get("url")}
    if au:
        return {"url": au[0]["url"], "kind": "audio", "ext": ".m4a"}
    return {}


# ------------------------------------------------------- nguồn tải nhanh (CDN)
def _dig(obj, depth: int = 0):
    """Bới mọi cặp khoá/giá trị trong JSON trả về của API lạ."""
    if depth > 6:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, v
            yield from _dig(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            yield from _dig(v, depth + 1)


def _parse_fast(data, kind: str) -> dict | None:
    """Rút link tải + tiêu đề từ JSON của API (mỗi API một kiểu, nên dò rộng)."""
    if not isinstance(data, dict):
        return None
    if data.get("status") in (False, "false") or data.get("success") is False:
        return None
    want = (".mp4", ".m4a", ".webm") if kind == "video" else (".mp3", ".m4a", ".opus")
    link = ""
    title = ""
    quality = ""
    thumb = ""
    for k, v in _dig(data):
        if isinstance(v, str):
            lk = k.lower()
            if not link and v.startswith("http") and ("url" in lk or "link" in lk or "dl" in lk):
                if any(w in v.lower() for w in want) or "cdn" in v.lower() or "media" in v.lower():
                    link = v
            if not title and lk in ("title", "name", "filename"):
                title = v[:200]
            if not quality and lk in ("quality", "label", "resolution"):
                quality = str(v)[:12]
            if not thumb and lk == "thumbnail" and v.startswith("http"):
                thumb = v
    if not link:
        return None
    return {"url": link, "title": title, "quality": quality, "thumbnail": thumb}


async def _fast_meta(url: str, kind: str) -> dict | None:
    """Hỏi API nhanh để lấy link CDN trực tiếp (không qua proxy Piped)."""
    import aiohttp
    api = FAST_API.format(kind="ytmp4" if kind == "video" else "ytmp3")
    tag = "fast-" + kind
    if _is_dead(tag):
        return None
    data = None
    for attempt in (1, 2):
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=FAST_TIMEOUT, connect=12)
            ) as s:
                async with s.get(api, params={"url": url}, headers={"User-Agent": UA}) as r:
                    if r.status != 200:
                        log.info("fast api %s -> %s", kind, r.status)
                        return None
                    data = await r.json(content_type=None)
            break
        except Exception as e:  # noqa: BLE001
            log.info("fast api loi %s (lan %s): %s", kind, attempt, type(e).__name__)
            if attempt == 2:
                return None
    if data is None:
        return None
    meta = _parse_fast(data, kind)
    if not meta:
        _mark_dead(tag)
        return None
    return meta


async def _measure(url: str, secs: float = MEASURE_SECS) -> float:
    """Đo tốc độ thật (MB/s) bằng 4 kết nối Range trong ~1.2s."""
    import aiohttp
    got = {"n": 0}
    stop = time.time() + secs
    cap = 6 * 1024 * 1024

    async def one(a: int, b: int) -> None:
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=secs + 6, connect=6)
            ) as s:
                async with s.get(url, headers={"User-Agent": UA,
                                               "Range": f"bytes={a}-{b}"},
                                 allow_redirects=True) as r:
                    if r.status not in (200, 206):
                        return
                    async for ch in r.content.iter_chunked(65536):
                        got["n"] += len(ch)
                        if time.time() > stop:
                            return
        except Exception:  # noqa: BLE001
            return

    t0 = time.time()
    await asyncio.gather(*[one(i * cap // 4, (i + 1) * cap // 4 - 1) for i in range(4)])
    return got["n"] / max(time.time() - t0, 0.05) / 1e6


async def _fast(url: str, kind: str, height: int, key: str,
                on_progress=None) -> DlResult | None:
    """Tải qua CDN của API nhanh. None = không dùng được, để lớp khác lo."""
    metas: dict[str, dict] = {}
    if kind in ("video", "both"):
        m = await _fast_meta(url, "video")
        if m:
            metas["video"] = m
    if kind in ("audio", "both"):
        m = await _fast_meta(url, "audio")
        if m:
            metas["audio"] = m
    if not metas:
        return None

    # đo tốc độ nguồn nhanh; quá chậm thì trả None để lớp Piped thử
    speed = await _measure(metas[list(metas)[0]]["url"])
    log.info("nguon nhanh %.2f MB/s (%s)", speed, metas[list(metas)[0]]["quality"])
    if speed < FAST_SPEED_MIN:
        log.info("nguon nhanh cham -> bo qua")
        return None

    _mk_cache()
    items: list[MediaItem] = []
    vmeta = metas.get("video") or metas.get("audio") or {}
    base_meta = {
        "title": vmeta.get("title") or "",
        "thumbnail": vmeta.get("thumbnail") or "",
        "duration": 0, "uploader": "", "view_count": 0,
    }
    for k, m in metas.items():
        ext = Path(m["url"].split("?")[0]).suffix.lower() or (".mp4" if k == "video" else ".mp3")
        dst = CACHE_DIR / f"{key}-q{k}{ext}"
        if not await _stream_to(m["url"], dst, on_progress, f"{k}-cdn", parts=8):
            return None
        if k == "audio" and dst.suffix.lower() != ".mp3":
            mp3 = to_mp3(dst)
            if mp3.suffix.lower() == ".mp3":
                dst = Path(mp3)
        items.append(_item(dst, base_meta, "audio" if k == "audio" else "video",
                           "youtube", url))
    if not items:
        return None
    _write_meta(key, base_meta)
    return DlResult(items=items)


async def _stream_part(sess, url: str, dst: Path, start: int, end: int,
                       counter: dict, total: int, on_progress=None,
                       label: str = "") -> bool:
    """Tải 1 khúc bằng Range header rồi ghi ra file riêng."""
    headers = {"User-Agent": UA, "Referer": "https://www.youtube.com/",
               "Range": f"bytes={start}-{end}"}
    try:
        async with sess.get(url, headers=headers, allow_redirects=True) as r:
            if r.status not in (200, 206):
                log.info("piped range %s -> %s", label, r.status)
                return False
            with open(dst, "wb") as f:
                async for chunk in r.content.iter_chunked(256 * 1024):
                    if not chunk:
                        continue
                    f.write(chunk)
                    counter["done"] += len(chunk)
                    if on_progress is not None:
                        try:
                            on_progress({
                                "status": "downloading",
                                "downloaded_bytes": counter["done"],
                                "total_bytes": total or None,
                                "total_bytes_estimate": total or None,
                            })
                        except Exception:  # noqa: BLE001
                            pass
    except Exception as e:  # noqa: BLE001
        log.info("piped part loi %s: %s", label, str(e)[:100])
        return False
    return dst.exists() and dst.stat().st_size > 0


async def _stream_to(url: str, dst: Path, on_progress=None, label: str = "",
                     parts: int = 4) -> bool:
    """Tải 1 luồng trực tiếp, chia 4 kết nối song song khi máy chủ cho Range.

    googlevideo giới hạn tốc độ mỗi kết nối, nên 4 kết nối cho tốc độ gần gấp
    bốn — đây là lý do chính /ytb từng "tải lâu". Máy chủ nào không hỗ trợ
    Range thì tự động quay về tải 1 luồng như cũ.
    """
    try:
        import aiohttp
        timeout = aiohttp.ClientTimeout(total=None, sock_read=60)
        counter = {"done": 0}
        async with aiohttp.ClientSession(timeout=timeout) as s:
            # 1) xem có bao nhiêu byte + có cho Range không
            total = 0
            ranges = False
            try:
                async with s.get(url, headers={"User-Agent": UA, "Range": "bytes=0-0"},
                                 allow_redirects=True) as r:
                    if r.status == 206:
                        cr = r.headers.get("Content-Range") or ""
                        m = re.search(r"/(\d+)", cr)
                        total = int(m.group(1)) if m else 0
                        ranges = total > 0
                    elif r.status == 200:
                        total = int(r.headers.get("Content-Length") or 0)
            except Exception:  # noqa: BLE001
                pass

            # 2) chia khúc tải song song
            if ranges and total > 6 * 1024 * 1024 and parts > 1:
                step = total // parts
                jobs = []
                chunks: list[Path] = []
                for i in range(parts):
                    a = i * step
                    b = (total - 1) if i == parts - 1 else (a + step - 1)
                    tmp = dst.with_suffix(dst.suffix + f".p{i}")
                    chunks.append(tmp)
                    jobs.append(_stream_part(s, url, tmp, a, b, counter, total,
                                             on_progress, label))
                oks = await asyncio.gather(*jobs, return_exceptions=True)
                if all(o is True for o in oks):
                    with open(dst, "wb") as out:
                        for c in chunks:
                            with open(c, "rb") as f:
                                shutil.copyfileobj(f, out, 1024 * 1024)
                    for c in chunks:
                        try:
                            c.unlink()
                        except Exception:  # noqa: BLE001
                            pass
                    if dst.stat().st_size >= total * 0.98:
                        if on_progress is not None:
                            try:
                                on_progress({"status": "finished"})
                            except Exception:  # noqa: BLE001
                                pass
                        return True
                    log.info("piped part ghep thieu (%s/%s) -> tai lai 1 luong",
                             dst.stat().st_size, total)
                for c in chunks:
                    try:
                        c.unlink()
                    except Exception:  # noqa: BLE001
                        pass

            # 3) tải 1 luồng
            counter["done"] = 0
            if on_progress is not None:
                try:
                    on_progress({"status": "downloading", "downloaded_bytes": 0,
                                 "total_bytes": total or None,
                                 "total_bytes_estimate": total or None})
                except Exception:  # noqa: BLE001
                    pass
            async with s.get(url, headers={"User-Agent": UA,
                                           "Referer": "https://www.youtube.com/"},
                             allow_redirects=True) as r:
                if r.status not in (200, 206):
                    log.info("piped stream %s -> %s", label, r.status)
                    return False
                total = int(r.headers.get("Content-Length") or 0) or total
                with open(dst, "wb") as f:
                    async for chunk in r.content.iter_chunked(256 * 1024):
                        if not chunk:
                            continue
                        f.write(chunk)
                        counter["done"] += len(chunk)
                        if on_progress is not None:
                            try:
                                on_progress({
                                    "status": "downloading",
                                    "downloaded_bytes": counter["done"],
                                    "total_bytes": total or None,
                                    "total_bytes_estimate": total or None,
                                })
                            except Exception:  # noqa: BLE001
                                pass
        ok = dst.exists() and dst.stat().st_size > 1024
        if ok and on_progress is not None:
            try:
                on_progress({"status": "finished"})
            except Exception:  # noqa: BLE001
                pass
        return ok
    except Exception as e:  # noqa: BLE001
        log.info("piped stream loi: %s", str(e)[:120])
        return False


async def _piped(url: str, kind: str, height: int, key: str,
                 on_progress=None) -> DlResult:
    vid = video_id(url)
    if not vid:
        return DlResult(error="Link YouTube không hợp lệ.")
    data = await _piped_meta(vid)
    if not data:
        return DlResult(error="Nguồn phản hồi quá chậm (timeout).")

    meta = {
        "title": data.get("title") or "",
        "uploader": data.get("uploader") or "",
        "duration": data.get("duration") or 0,
        "view_count": data.get("views") or 0,
        "thumbnail": data.get("thumbnailUrl") or "",
    }
    pick = _piped_pick(data, kind, height)
    if not pick:
        return DlResult(error="Nguồn không có định dạng phù hợp.")

    _mk_cache()
    dst = CACHE_DIR / f"{key}-0{pick['ext']}"
    if not await _stream_to(pick["url"], dst, on_progress, "video"):
        return DlResult(error="Không tải được luồng dữ liệu từ nguồn dự phòng.")
    _write_meta(key, meta)

    if kind == "audio" and pick.get("kind") == "video":
        mp3 = to_mp3(dst)
        if mp3.suffix.lower() == ".mp3" and mp3.stat().st_size > 1024:
            if mp3 != dst:
                try:
                    dst.unlink()
                except Exception:  # noqa: BLE001
                    pass
            return DlResult(items=[_item(mp3, meta, "audio", "youtube", url)])
        return DlResult(items=[_item(dst, meta, "audio", "youtube", url)])

    if kind == "both" and pick.get("audio_url") and pick.get("kind") == "video":
        adst = CACHE_DIR / f"{key}-a.m4a"
        if await _stream_to(pick["audio_url"], adst, None, "audio"):
            mp3 = to_mp3(adst)
            if mp3.suffix.lower() == ".mp3":
                try:
                    adst.unlink()
                except Exception:  # noqa: BLE001
                    pass
                v = _item(dst, meta, "video", "youtube", url)
                a = _item(mp3, meta, "audio", "youtube", url)
                return DlResult(items=[v, a])
    return DlResult(items=[_item(dst, meta, kind, "youtube", url)])


# ------------------------------------------------------------------ cửa chính
async def download(url: str, kind: str = "video", *, height: int = 1080,
                   platform: str = "youtube", cookies_site: str | None = None,
                   on_progress=None) -> DlResult:
    """Tải bằng yt-dlp (dò 1 lần, tải 1 lần); YouTube chặn thì rơi xuống Piped.

    Trả DlResult; `error` rỗng nghĩa là thành công.
    """
    key = _key(url)
    hit = _cached(key, kind)
    if hit:
        log.info("cache hit %s (%s)", hit.name, kind)
        return DlResult(items=[_item(hit, _read_meta(key), kind, platform, url)])

    cookies_file = None
    if cookies_site:
        from core import cookies as ck
        cookies_file = ck.get(cookies_site)

    _mk_cache()
    info: dict = {}
    clients: list[str] | None = None
    err = ""
    fast_task: asyncio.Task | None = None

    if platform == "youtube":
        # Không có cookie => gần như chắc chắn bị chặn: chạy song song nguồn
        # nhanh (CDN) với bước dò, để lúc dò xong là có sẵn đường tải.
        if not cookies_file:
            fast_task = asyncio.create_task(_fast(url, kind, height, key, on_progress))
        info, err, clients = await _probe(url, cookies_file)
    else:
        # nền tảng khác: 1 lần dò, không đua client
        opts = _base_opts()
        opts["skip_download"] = True
        if cookies_file:
            opts["cookiefile"] = cookies_file
        try:
            info = await asyncio.wait_for(
                asyncio.to_thread(_probe_sync, url, opts), timeout=PROBE_TIMEOUT
            )
        except Exception as e:  # noqa: BLE001
            info = {}
            err = str(e).splitlines()[0][:200] if str(e) else ""

    if info and info.get("formats"):
        fmt = _pick_format(info, kind, height)
        opts = _dl_opts(kind, fmt, clients, key, cookies_file, on_progress)
        size = _size_hint(info)
        budget = min(DL_TIMEOUT, max(120, int(size / (180 * 1024)) + 60)) if size else DL_TIMEOUT
        try:
            got, hint = await asyncio.wait_for(
                asyncio.to_thread(_run, url, opts), timeout=budget
            )
        except asyncio.TimeoutError:
            err = "timeout"
        except Exception as e:  # noqa: BLE001
            err = str(e).splitlines()[0][:200] if str(e) else "lỗi không rõ"
        else:
            path = _pick_file(key, hint)
            if path:
                if kind == "audio":
                    path = to_mp3(path)
                meta = got or info
                _write_meta(key, meta)
                return DlResult(items=[_item(path, meta, kind, platform, url)])
            err = "Tải xong nhưng không thấy file."

    if platform == "youtube":
        # 1) nguồn nhanh (CDN) — đã chạy song song từ đầu
        if fast_task is not None:
            try:
                res = await asyncio.wait_for(asyncio.shield(fast_task), timeout=240)
            except Exception as e:  # noqa: BLE001
                log.info("nguon nhanh loi: %s", type(e).__name__)
                res = None
            if res is not None and res.items:
                log.info("tải qua CDN: %s", res.items[0].path)
                return res
        # 2) đường proxy Piped
        res = await _piped(url, kind, height, key, on_progress)
        if res.items:
            log.info("tải qua piped: %s", res.items[0].path)
            return res
        if not err:
            err = res.error or "Không tải được."

    return DlResult(error=friendly(err or "Không tải được."))


async def youtube(url: str, kind: str = "video", *, height: int = 1080) -> DlResult:
    return await download(url, kind, height=height, platform="youtube")

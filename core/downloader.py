#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tải video / ảnh / audio TikTok (chỉ TikTok).

Nguồn: tikwm.com (API công khai, không cần cookie) -> dự phòng yt-dlp.
"""

import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import yt_dlp

from config import MAX_UPLOAD_MB, TMP_DIR
from core import pool
from core import http

log = logging.getLogger("khoaan.dl")


class _Quiet:
    """Nuốt log của yt-dlp (bot tự báo lỗi bằng tiếng Việt)."""

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


_QUIET = _Quiet()

# Chỉ TikTok. (Douyin / IG / YouTube / FB / X… đã bỏ theo yêu cầu)
TIKTOK_HOSTS = ("tiktok.com", "tiktokv.com", "tiktokcdn.com")


@dataclass
class MediaItem:
    path: str
    kind: str = "video"          # video | audio | photo
    title: str = ""
    uploader: str = ""
    duration: int = 0
    views: int = 0
    thumbnail: str = ""
    platform: str = "tiktok"
    url: str = ""


@dataclass
class DlResult:
    items: list[MediaItem] = field(default_factory=list)
    error: str = ""
    fatal: bool = False          # lỗi vĩnh viễn (video bị xoá, link sai) -> không thử tiếp


# ------------------------------------------------------------------ nhận diện
def _host(url: str) -> str:
    try:
        host = (urlparse(url).netloc or "").lower().split("@")[-1].split(":")[0]
    except Exception:  # noqa: BLE001
        return ""
    for prefix in ("www.", "m.", "vm.", "vt.", "v.", "t."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    return host


def is_tiktok(url: str) -> bool:
    if not url or not url.lower().startswith(("http://", "https://")):
        return False
    host = _host(url)
    return any(host == h or host.endswith("." + h) for h in TIKTOK_HOSTS)


def is_supported(url: str) -> bool:
    return is_tiktok(url)


def _out_dir(tag: str) -> "os.PathLike":
    p = TMP_DIR / f"{tag}_{int(time.time() * 1000)}"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ------------------------------------------------------------------ tikwm API
API = "https://www.tikwm.com/api/"


async def _tikwm(url: str) -> dict | None:
    data = await http.fetch_json(
        API,
        method="POST",
        data={"url": url, "hd": "1"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=25,
        retries=1,
    )
    if not data or data.get("code") != 0 or not data.get("data"):
        return None
    return data["data"]


def _author(d: dict) -> str:
    a = d.get("author") or {}
    return a.get("nickname") or a.get("unique_id") or "TikTok"


# ------------------------------------------------------------------ video / ảnh
async def tiktok_media(url: str) -> DlResult:
    """Tải video (hoặc bộ ảnh slideshow) TikTok."""
    d = await _tikwm(url)
    if not d:
        log.info("tikwm fail -> yt-dlp")
        return await _ytdlp(url, "video")

    title = (d.get("title") or "TikTok Video").strip()
    author = _author(d)
    out = _out_dir("tt")

    images = d.get("images") or []
    if images:
        items: list[MediaItem] = []
        for i, img in enumerate(images[:10]):
            raw = await http.download_bytes(img, max_bytes=12 * 1024 * 1024)
            if not raw:
                continue
            p = out / f"{i}.jpg"
            p.write_bytes(raw)
            items.append(MediaItem(str(p), "photo", title, author, url=url))
        if items:
            return DlResult(items=items)
        return DlResult(error="Không tải được ảnh từ bài này")

    video_url = d.get("hdplay") or d.get("play") or d.get("wmplay")
    if not video_url:
        return DlResult(error="Bài này không có video")
    raw = await http.download_bytes(video_url, max_bytes=MAX_UPLOAD_MB * 1024 * 1024, timeout=180)
    if not raw:
        return DlResult(error="Video quá lớn hoặc CDN chặn")
    p = out / "video.mp4"
    p.write_bytes(raw)
    return DlResult(items=[MediaItem(
        str(p), "video", title, author,
        duration=int(d.get("duration") or 0),
        views=int(d.get("play_count") or 0),
        thumbnail=d.get("origin_cover") or d.get("cover") or "",
        url=url,
    )])


# ------------------------------------------------------------------ audio
def _ffmpeg_to_mp3(src: str, dst: str) -> bool:
    """Tách audio từ video -> mp3 bằng ffmpeg. Trả về True nếu ra file thật."""
    exe = shutil.which("ffmpeg")
    if not exe:
        return False
    try:
        r = subprocess.run(
            [exe, "-y", "-loglevel", "error", "-i", src, "-vn",
             "-acodec", "libmp3lame", "-b:a", "192k", dst],
            capture_output=True, timeout=180,
        )
        return r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 1024
    except Exception as e:  # noqa: BLE001
        log.info("ffmpeg lỗi: %s", str(e)[:80])
        return False


def _sniff_ext(raw: bytes, default: str = ".mp3") -> str:
    """Đoán đuôi file theo magic bytes để Telegram không từ chối."""
    head = raw[:12]
    if head[4:8] == b"ftyp":
        return ".m4a"
    if head[:4] == b"RIFF":
        return ".wav"
    if head[:4] == b"OggS":
        return ".ogg"
    if head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return ".mp3"
    return default


async def tiktok_audio(url: str) -> DlResult:
    """Lấy âm thanh bài TikTok — 4 lớp dự phòng, lớp nào được trước lấy trước."""
    d = await _tikwm(url)
    out = _out_dir("ta")
    meta_title = (d or {}).get("title") or "TikTok audio"
    meta_author = _author(d or {}) or ""
    meta_cover = (d or {}).get("origin_cover") or (d or {}).get("cover") or ""

    # --- lớp 1: nhạc nền riêng do tikwm trả về
    if d:
        music = d.get("music_info") or {}
        music_url = music.get("play")
        if music_url:
            raw = await http.download_bytes(music_url, max_bytes=MAX_UPLOAD_MB * 1024 * 1024, timeout=120)
            if raw and len(raw) > 1024:
                ext = _sniff_ext(raw)
                p = out / f"audio{ext}"
                p.write_bytes(raw)
                return DlResult(items=[MediaItem(
                    str(p), "audio",
                    title=(music.get("title") or meta_title).strip()[:120],
                    uploader=(music.get("author") or meta_author),
                    duration=int(music.get("duration") or d.get("duration") or 0),
                    thumbnail=meta_cover, url=url,
                )])
            log.info("nhạc nền tikwm tải hụt -> tách từ video")

    # --- lớp 2: tải luôn video rồi tách mp3 (không cần yt-dlp)
    if d:
        video_url = d.get("hdplay") or d.get("play") or d.get("wmplay")
        if video_url:
            raw = await http.download_bytes(video_url, max_bytes=MAX_UPLOAD_MB * 1024 * 1024, timeout=180)
            if raw and len(raw) > 1024:
                src = out / "src.mp4"
                src.write_bytes(raw)
                dst = out / "audio.mp3"
                if _ffmpeg_to_mp3(str(src), str(dst)):
                    try:
                        src.unlink()
                    except OSError:
                        pass
                    return DlResult(items=[MediaItem(
                        str(dst), "audio", title=meta_title.strip()[:120],
                        uploader=meta_author, duration=int(d.get("duration") or 0),
                        thumbnail=meta_cover, url=url,
                    )])
            log.info("tách mp3 từ video hụt -> yt-dlp")

    # --- lớp 3 + 4: yt-dlp (tự tách mp3 nếu máy có ffmpeg)
    res = await _ytdlp(url, "audio")
    return res


# ------------------------------------------------------------------ yt-dlp dự phòng
def _ytdlp_sync(url: str, kind: str) -> DlResult:
    out_dir = tempfile.mkdtemp(dir=str(TMP_DIR))
    opts = {
        "outtmpl": os.path.join(out_dir, "%(id)s.%(ext)s"),
        "logger": _QUIET,
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": True,
        "no_cache_dir": True,
        "noplaylist": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 25,
        "max_filesize": MAX_UPLOAD_MB * 1024 * 1024,
        "restrictfilenames": True,
        "concurrent_fragment_downloads": 4,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
        },
    }
    if kind == "audio":
        opts["format"] = "ba[ext=m4a]/ba/bestaudio"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    else:
        opts["format"] = (
            f"b[ext=mp4][filesize<{MAX_UPLOAD_MB}M]/"
            f"bv*[filesize<{MAX_UPLOAD_MB}M]+ba/b[ext=mp4]/b"
        )
        opts["merge_output_format"] = "mp4"

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True) or {}
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(out_dir, ignore_errors=True)
        msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e) or "")
        first = msg.splitlines()[0] if msg else "lỗi không rõ"
        if "Private" in first or "private" in first:
            return DlResult(error="Video ở chế độ riêng tư")
        return DlResult(error=first[:160])

    files = [f for f in os.listdir(out_dir) if not f.endswith((".part", ".ytdl"))]
    if not files:
        shutil.rmtree(out_dir, ignore_errors=True)
        return DlResult(error="Không tải được file (quá lớn hoặc bị chặn)")

    ext = files[0].rsplit(".", 1)[-1].lower()
    if kind == "audio":
        k = "audio"
    elif ext in ("jpg", "jpeg", "png", "webp"):
        k = "photo"
    else:
        k = "video"
    item = MediaItem(
        path=os.path.join(out_dir, files[0]),
        kind=k,
        title=(info.get("title") or "") if isinstance(info, dict) else "",
        uploader=(info.get("uploader") or "") if isinstance(info, dict) else "",
        duration=int((info.get("duration") or 0) if isinstance(info, dict) else 0),
        views=int((info.get("view_count") or 0) if isinstance(info, dict) else 0),
        thumbnail=(info.get("thumbnail") or "") if isinstance(info, dict) else "",
        url=url,
    )
    return DlResult(items=[item])


async def _ytdlp(url: str, kind: str) -> DlResult:
    loop = asyncio.get_running_loop()
    return await pool.run_io(_ytdlp_sync, url, kind)


# ------------------------------------------------------------------ entry
async def download(url: str, kind: str = "video") -> DlResult:
    url = (url or "").strip()
    if not is_tiktok(url):
        return DlResult(error="Bot chỉ hỗ trợ TikTok")
    if kind == "audio":
        return await tiktok_audio(url)
    return await tiktok_media(url)


def cleanup(items: list[MediaItem]) -> None:
    for it in items:
        try:
            d = os.path.dirname(it.path)
            if d and str(TMP_DIR) in d:
                shutil.rmtree(d, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Đa nền tảng: TikTok · Instagram · Facebook · YouTube.

`download_any(url, kind)` là cửa duy nhất mà main.py gọi:

    kind = "video"  -> chỉ video (hoặc ảnh với bài IG carousel)
    kind = "audio"  -> chỉ mp3
    kind = "both"   -> video + mp3 (mp3 cắt từ chính video vừa tải, không tải 2 lần)

Instagram có 4 lớp dự phòng, Facebook nhận cả link share/profile, YouTube đi qua
core/ytdl.py (có cache). Có cookie thì tự dùng cookie.
"""

from __future__ import annotations

import asyncio
import hashlib
import html as htmllib
import logging
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import config
from core import cookies as ck
from core import facebook
from core import downloader, http, ytdl
from core.downloader import DlResult, MediaItem

log = logging.getLogger("khoaan.social")

IG_RE = re.compile(r"instagram\.com/(?:[A-Za-z0-9_.]+/)?(p|reel|reels|tv)/([A-Za-z0-9_-]{5,})", re.I)
FB_HOST = re.compile(r"(facebook\.com|fb\.watch|fb\.me)", re.I)
YT_HOST = re.compile(r"(youtube\.com|youtu\.be|music\.youtube\.com)", re.I)

MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
DESK_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


# ------------------------------------------------------------------ nhận diện
def platform(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if downloader.is_tiktok(u):
        return "tiktok"
    if IG_RE.search(u):
        return "instagram"
    if FB_HOST.search(u):
        return "facebook"
    if YT_HOST.search(u):
        return "youtube"
    return ""


def is_supported(url: str) -> bool:
    return bool(platform(url))


def any_link(text: str) -> str | None:
    """Link đầu tiên trong tin nhắn thuộc nền tảng bot hỗ trợ."""
    for m in re.finditer(r"https?://[^\s<>\"']+", text or ""):
        u = m.group(0).rstrip(").,]…")
        if is_supported(u):
            return u
    return None


def want_kind(text: str, default: str = "video") -> str:
    """Đọc ý người dùng: 'mp3' -> audio, 'cả 2'/'mp3+mp4' -> both."""
    low = (text or "").lower()
    both = any(k in low for k in ("ca 2", "cả 2", "ca hai", "cả hai", "both",
                                  "mp3+mp4", "mp4+mp3", "video+mp3", "nhac+video"))
    audio = bool(re.search(r"\b(mp3|audio|âm thanh|am thanh|nhac|nhạc|sound)\b", low))
    if both:
        return "both"
    if audio:
        return "audio"
    return default


# ------------------------------------------------------------------ IG
def _out_dir(tag: str) -> Path:
    d = Path(config.TMP_DIR) / tag
    d.mkdir(parents=True, exist_ok=True)
    return d


def _unescape(u: str) -> str:
    u = htmllib.unescape(u or "")
    return u.replace("\\/", "/").replace("\\u0026", "&").replace("\\", "")


def ig_parse_shortcode(url: str) -> tuple[str, str]:
    m = IG_RE.search(url or "")
    if not m:
        return "p", ""
    kind = "reel" if m.group(1).lower() in ("reel", "reels") else m.group(1).lower()
    return kind, m.group(2)


def ig_media_from_html(text: str) -> list[dict]:
    """Rút link media từ HTML embed / og meta (không cần đăng nhập)."""
    found: list[dict] = []
    seen: set[str] = set()

    def add(url: str, kind: str) -> None:
        u = _unescape(url).strip()
        if not u.startswith("http") or u in seen:
            return
        if any(x in u for x in ("/rsrc.php", "static.cdninstagram", "favicon", ".js")):
            return
        seen.add(u)
        found.append({"url": u, "type": kind})

    for pat in (
        r'"video_url"\s*:\s*"([^"]+\.mp4[^"]*)"',
        r'<video[^>]+src="([^"]+)"',
        r'"contentUrl"\s*:\s*"([^"]+)"',
        r'property="og:video(?::secure_url)?"\s+content="([^"]+)"',
    ):
        for m in re.finditer(pat, text or ""):
            add(m.group(1), "video")

    if not found:
        for pat in (
            r'"display_url"\s*:\s*"([^"]+)"',
            r'class="EmbeddedMediaImage"[^>]+background-image:\s*url\(\'?([^\')]+)',
            r'property="og:image"\s+content="([^"]+)"',
        ):
            for m in re.finditer(pat, text or ""):
                add(m.group(1), "photo")

    return found


async def _save_media(remote: str, kind: str, code: str, idx: int) -> MediaItem | None:
    ext = ".mp4" if kind == "video" else ".jpg"
    if kind == "photo" and ".webp" in remote:
        ext = ".webp"
    data = await http.download_bytes(
        remote, max_bytes=280 * 1024 * 1024, timeout=120,
        headers={"User-Agent": DESK_UA, "Referer": "https://www.instagram.com/"},
    )
    if not data or len(data) < 2048:
        return None
    name = f"ig_{code}_{idx}{ext}"
    path = _out_dir("ig") / name
    path.write_bytes(data)
    return MediaItem(path=str(path), kind="video" if kind == "video" else "photo",
                     platform="instagram")


async def _ig_embed(code: str, kind_slug: str) -> DlResult:
    urls = [
        f"https://www.instagram.com/{kind_slug}/{code}/embed/captioned/",
        f"https://www.instagram.com/{kind_slug}/{code}/embed/",
    ]
    for u in urls:
        status, body, _ = await http.fetch(
            u, headers={"User-Agent": MOBILE_UA, "Accept-Language": "en-US,en;q=0.9"},
            timeout=18, retries=1,
        )
        if not body:
            continue
        text = body.decode("utf-8", errors="replace")
        media = ig_media_from_html(text)
        if not media:
            continue
        items: list[MediaItem] = []
        for i, m in enumerate(media[:10]):
            it = await _save_media(m["url"], m["type"], code, i)
            if it:
                items.append(it)
        if items:
            title = ""
            mt = re.search(r'property="og:title"\s+content="([^"]{3,180})"', text)
            if mt:
                title = htmllib.unescape(mt.group(1))
            for it in items:
                it.title = title
                it.url = u
            return DlResult(items=items)
    return DlResult(error="embed không trả media")


async def _ig_mirror(code: str, kind_slug: str) -> DlResult:
    """Mirror đọc hộ (ddinstagram) — nhiều bài công khai đi được đường này."""
    for host in ("www.ddinstagram.com", "ddinstagram.com"):
        u = f"https://{host}/{kind_slug}/{code}/"
        status, body, _ = await http.fetch(u, headers={"User-Agent": DESK_UA}, timeout=16, retries=1)
        if not body:
            continue
        text = body.decode("utf-8", errors="replace")
        media = ig_media_from_html(text)
        if not media:
            continue
        items = []
        for i, m in enumerate(media[:10]):
            it = await _save_media(m["url"], m["type"], code, i)
            if it:
                items.append(it)
        if items:
            return DlResult(items=items)
    return DlResult(error="mirror không có media")


async def _ig_api(code: str, url: str) -> DlResult:
    """2 API công khai — hay bị Cloudflare chặn từ IP máy chủ, nhưng IP nhà thì được."""
    async def snapinsta():
        status, body, _ = await http.fetch(
            "https://snapinsta.app/api/ajaxSearch",
            method="POST",
            data={"q": url, "t": "media", "lang": "en"},
            headers={"User-Agent": DESK_UA, "X-Requested-With": "XMLHttpRequest",
                     "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
            timeout=14, retries=0,
        )
        if not body:
            return None
        payload = body.decode("utf-8", errors="replace")
        return ig_media_from_html(payload) or None

    async def supersave():
        status, body, _ = await http.fetch(
            "https://api.instasupersave.com/api/convert",
            method="POST",
            json_body={"url": url},
            headers={"User-Agent": DESK_UA, "Content-Type": "application/json",
                     "Origin": "https://instasupersave.com",
                     "Referer": "https://instasupersave.com/"},
            timeout=14, retries=0,
        )
        if not body:
            return None
        try:
            import json
            data = json.loads(body.decode("utf-8", errors="replace"))
        except Exception:  # noqa: BLE001
            return None
        urls: list[dict] = []
        for row in (data.get("url") or []):
            if isinstance(row, dict) and row.get("url"):
                k = "video" if str(row.get("type", "")).lower().endswith("video") or ".mp4" in row["url"] else "photo"
                urls.append({"url": row["url"], "type": k})
        return urls or None

    tasks = [asyncio.create_task(snapinsta()), asyncio.create_task(supersave())]
    try:
        for coro in asyncio.as_completed(tasks, timeout=16):
            media = await coro
            if media:
                for t in tasks:
                    if not t.done():
                        t.cancel()
                items = []
                for i, m in enumerate(media[:10]):
                    it = await _save_media(m["url"], m["type"], code, i)
                    if it:
                        items.append(it)
                if items:
                    return DlResult(items=items)
    except Exception:  # noqa: BLE001
        pass
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
    return DlResult(error="API công khai không trả kết quả")



MEDIA_URL_RE = re.compile(
    r"https?://[^\s\"'<>\\]+?\.(?:mp4|m4v|jpg|jpeg|webp|png)"
    r"(?:\?[^\s\"'<>\\]*)?", re.I)


def _scan_media(payload) -> list[dict]:
    """Bới URL media trong bất kỳ JSON/HTML nào (API mỗi nhà một kiểu)."""
    if isinstance(payload, (dict, list)):
        try:
            import json as _json
            text = _json.dumps(payload, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            text = str(payload)
    else:
        text = str(payload or "")
    text = _unescape(text)
    out: list[dict] = []
    seen: set[str] = set()
    for m in MEDIA_URL_RE.finditer(text):
        u = m.group(0)
        if u in seen or any(x in u for x in ("/rsrc.php", "static.cdninstagram",
                                            "favicon", "s150x150", "instagram.com/static")):
            continue
        seen.add(u)
        kind = "video" if re.search(r"\.(mp4|m4v)", u, re.I) else "photo"
        out.append({"url": u, "type": kind})
    # video lên trước để gửi được ngay
    out.sort(key=lambda x: 0 if x["type"] == "video" else 1)
    return out


async def _ig_downloadgram(url: str) -> DlResult:
    status, body, _ = await http.fetch(
        "https://api.downloadgram.org/media",
        method="POST", json_data={"url": url},
        headers={"User-Agent": DESK_UA, "Content-Type": "application/json",
                 "Origin": "https://downloadgram.org",
                 "Referer": "https://downloadgram.org/"},
        timeout=18, retries=0,
    )
    if not body:
        return DlResult(error="downloadgram không phản hồi")
    try:
        import json as _json
        data = _json.loads(body.decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        data = body.decode("utf-8", errors="replace")
    media = _scan_media(data)
    return await _ig_save_all(media, url, "downloadgram")


async def _ig_save_all(media: list[dict], url: str, tag: str) -> DlResult:
    kind_slug, code = ig_parse_shortcode(url)
    items: list[MediaItem] = []
    for i, m in enumerate(media[:10]):
        it = await _save_media(m["url"], m["type"], code or tag, i)
        if it:
            items.append(it)
    if not items:
        return DlResult(error=f"{tag} không trả media")
    for it in items:
        it.url = url
    return DlResult(items=items)


async def instagram(url: str, kind: str = "video") -> DlResult:
    kind_slug, code = ig_parse_shortcode(url)
    if not code:
        return DlResult(error="Link Instagram không hợp lệ.")

    # lớp 1: yt-dlp (nhanh nhất khi bài công khai)
    res = await ytdl.download(url, "video", platform="instagram", cookies_site="ig")
    if res.items:
        return await _finish(res, kind, "instagram", url)

    # lớp 2..5 (mỗi lớp bọc try riêng: 1 nguồn chết không được làm sập cả lệnh)
    jobs = [(_ig_embed, (code, kind_slug)), (_ig_mirror, (code, kind_slug)),
            (_ig_downloadgram, (url,)), (_ig_api, (code, url))]
    for job, args in jobs:
        try:
            res = await job(*args)
        except Exception as e:  # noqa: BLE001
            res = DlResult(error=str(e)[:120])
        if res.items:
            for it in res.items:
                it.url = url
            return await _finish(res, kind, "instagram", url)
    return DlResult(error=(
        "Bài này Instagram bắt đăng nhập. Nạp cookie 1 lần là tải được: "
        "mở instagram.com trên máy tính → extension 'Get cookies.txt' → "
        "gửi file đó bằng lệnh /cookie ig"
    ))


# ------------------------------------------------------------------ FB
FB_DOMAINS = ("facebook.com", "fbcdn.net", "fb.watch")


def _fb_headers(ua: str | None = None) -> dict[str, str]:
    """Header cho request tới Facebook, kèm cookie nếu admin đã nạp (/cookie fb).

    FB chặn IP máy chủ rất gắt: không cookie thì trang trả về rỗng, có cookie
    thì thấy được cả video trong nhóm kín.
    """
    h = {"User-Agent": ua or MOBILE_UA, "Accept-Language": "vi,en;q=0.8"}
    try:
        from core import cookies as ck
        h.update(ck.header("fb", FB_DOMAINS))
    except Exception:  # noqa: BLE001
        pass
    return h


def _mp4_in(text: str) -> str:
    """Bới link mp4 trực tiếp trong HTML/JSON của FB (nếu có)."""
    for pat in (
        r'"browser_native_hd_url"\s*:\s*"([^"]+)"',
        r'"browser_native_sd_url"\s*:\s*"([^"]+)"',
        r'"playable_url_quality_hd"\s*:\s*"([^"]+)"',
        r'"playable_url"\s*:\s*"([^"]+)"',
        r'property="og:video(?::secure_url|:url)?"\s+content="([^"]+)"',
    ):
        m = re.search(pat, text or "")
        if m:
            u = _unescape(m.group(1))
            if u.startswith("http") and ".mp4" in u:
                return u
    return ""


FB_VIDEO_PATTERNS = (
    re.compile(r"/videos?/", re.I),
    re.compile(r"/share/[rv]/", re.I),
    re.compile(r"/reels?/[A-Za-z0-9_-]+", re.I),
    re.compile(r"/reel/", re.I),
    re.compile(r"/watch/?\?", re.I),
    re.compile(r"[?&]v=\d+", re.I),
    re.compile(r"video\.php", re.I),
    re.compile(r"fb\.watch/", re.I),
)


def fb_is_video(url: str) -> bool:
    return any(p.search(url or "") for p in FB_VIDEO_PATTERNS)


async def fb_real_url(url: str) -> str:
    """Link share -> link thật. Đi redirect trước, rồi mới đọc og:url/canonical.

    /share/… của Facebook luôn nhảy qua /login/?next=<link thật đã mã hoá>,
    nên phải bóc mã hoá rồi mới biết đó là video hay hồ sơ.
    """
    try:
        real = await facebook.real_url(url)
    except Exception:  # noqa: BLE001
        real = ""
    if real and real != url:
        if fb_is_video(real):
            return real
        if re.search(r"/videos/|/reel/|fb\.watch", real, re.I):
            return real
    status, body, headers = await http.fetch(
        real or url, headers=_fb_headers(), timeout=18, retries=1, allow_redirects=True,
    )
    text = body.decode("utf-8", errors="replace") if body else ""
    for pat in (
        r'property="og:video(?::secure_url|:url)?"\s+content="([^"]+)"',
        r'property="og:url"\s+content="([^"]+)"',
        r'rel="canonical"\s+href="([^"]+)"',
        r'content="fb://(?:profile|page|video)/(\d{8,})"',
    ):
        m = re.search(pat, text)
        if not m:
            continue
        got = htmllib.unescape(m.group(1)).replace("\\/", "/")
        if got.isdigit():
            return f"https://www.facebook.com/{got}"
        if fb_is_video(got):
            return got
    return real or url


FB_ID_IN_URL = re.compile(
    r"(?:profile\.php\?[^#]*\bid=|/people/[^/?#]+/|facebook\.com/)(\d{6,})", re.I)


async def fb_page_video(page_id: str) -> str:
    """Link share trỏ về Page/Profile -> bới video mới nhất trong trang.

    FB chặn IP máy chủ ở nhiều giao diện, nên thử lần lượt: mbasic (nhẹ, ít
    chặn) -> m -> www (tab video). Có video thì trả link chuẩn để tải.
    """
    pages = (
        f"https://mbasic.facebook.com/{page_id}/videos",
        f"https://m.facebook.com/{page_id}/videos",
        f"https://www.facebook.com/{page_id}/videos",
        f"https://www.facebook.com/profile.php?id={page_id}&sk=videos",
    )
    for u in pages:
        status, body, _ = await http.fetch(
            u, headers=_fb_headers(), timeout=15, retries=0, allow_redirects=True,
        )
        if not body:
            continue
        text = body.decode("utf-8", errors="replace")
        ids = re.findall(r"/videos/(\d{6,})", text) + re.findall(r'"video_id":"(\d+)"', text)
        ids += re.findall(r"/reel/(\d{6,})", text)
        for vid in ids:
            if vid != page_id:
                return f"https://www.facebook.com/{page_id}/videos/{vid}"
    return ""


async def _fb_direct(url: str, kind: str = "video") -> DlResult | None:
    """Tải thẳng link mp4 nằm trong HTML của FB (bài viết / share / reel).

    Đường này nhanh và hay ăn hơn yt-dlp khi FB đã trả HTML có playable_url
    (đặc biệt lúc đã nạp cookie). Không thấy mp4 thì trả None.
    """
    for page in dict.fromkeys([url]):
        if not page:
            continue
        status, body, _ = await http.fetch(
            page, headers=_fb_headers(DESK_UA), timeout=20, retries=1, allow_redirects=True,
        )
        text = body.decode("utf-8", errors="replace") if body else ""
        mp4 = _mp4_in(text)
        if not mp4:
            continue
        data = await http.download_bytes(
            mp4, max_bytes=380 * 1024 * 1024, timeout=240,
            headers=_fb_headers(DESK_UA) | {"Referer": "https://www.facebook.com/"},
        )
        if not data or len(data) < 20000:
            continue
        d = _out_dir("fb")
        path = d / f"fb_{int(time.time())}.mp4"
        path.write_bytes(data)
        return DlResult(items=[MediaItem(path=str(path), kind="video", platform="facebook",
                                         url=url)])
    return None


async def facebook(url: str, kind: str = "video") -> DlResult:
    real = await fb_real_url(url)
    # 1) link share / bài viết có video: bới mp4 ngay trong HTML trước đã
    if fb_is_video(url) or "/share/" in url or "/reel/" in url:
        got = await _fb_direct(url, kind)
        if got and got.items:
            return await _finish(got, kind, "facebook", url)
    target = real if fb_is_video(real) else url
    if not fb_is_video(target):
        # link share của Page / trang cá nhân: bới video mới nhất trong trang
        m = FB_ID_IN_URL.search(real or "") or FB_ID_IN_URL.search(url or "")
        if m:
            found = await fb_page_video(m.group(1))
            if found:
                target = found
    if not fb_is_video(target):
        return DlResult(error=(
            "Link này là trang cá nhân / Page, không phải video nên không có gì để tải. "
            "Gửi link video (dạng facebook.com/reel/… · /videos/… · fb.watch/…), "
            "hoặc dán link bài viết có video."
        ))

    res = await ytdl.download(target, "video", platform="facebook", cookies_site="fb")
    if res.items:
        for it in res.items:
            it.url = target
        return await _finish(res, kind, "facebook", target)
    return DlResult(error=(
        "Video này Facebook chặn khi chưa đăng nhập (video trong nhóm/kín hoặc của trang riêng). "
        "Nạp cookie 1 lần: extension 'Get cookies.txt' trên facebook.com → gửi file bằng /cookie fb"
    ))


# ------------------------------------------------------------------ chung
async def _finish(res: DlResult, kind: str, site: str, url: str) -> DlResult:
    """Đổi 'video'/'audio'/'both' cho đúng yêu cầu sau khi đã có media."""
    if not res.items:
        return res
    first = res.items[0]
    if first.kind == "photo":
        return res   # ảnh thì trả nguyên (IG carousel)

    if kind == "audio":
        mp3 = ytdl.to_mp3(first.path)
        if mp3.suffix.lower() == ".mp3":
            first.path = str(mp3)
            first.kind = "audio"
            return DlResult(items=[first])
        return res
    if kind == "both":
        mp3 = ytdl.to_mp3(first.path)
        if mp3.suffix.lower() == ".mp3" and Path(mp3).stat().st_size > 1024:
            res.items.append(MediaItem(
                path=str(mp3), kind="audio", title=first.title, uploader=first.uploader,
                duration=first.duration, views=first.views, platform=site, url=url,
            ))
    return res


async def download_any(url: str, kind: str = "video", *, height: int = 1080,
                       on_progress=None) -> DlResult:
    """Cửa duy nhất: tự nhận nền tảng rồi tải.

    `on_progress(dict)` (tuỳ chọn): nhận tiến trình tải thật từ yt-dlp
    (status / downloaded_bytes / total_bytes / speed / eta) để bot hiện %.
    """
    site = platform(url)
    if site == "tiktok":
        res = await downloader.download(url, "video" if kind != "audio" else "audio")
        if kind == "both" and res.items:
            return await _finish(res, "both", "tiktok", url)
        if kind == "audio" and res.items:
            return await _finish(res, "audio", "tiktok", url)
        return res
    if site == "youtube":
        res = await ytdl.download(url, "audio" if kind == "audio" else "video",
                                  height=height, platform="youtube",
                                  cookies_site="youtube", on_progress=on_progress)
        if kind == "both" and res.items:
            return await _finish(res, "both", "youtube", url)
        return res
    if site == "instagram":
        return await instagram(url, kind)
    if site == "facebook":
        return await facebook(url, kind)
    return DlResult(error="Link này chưa hỗ trợ. Bot nhận TikTok · Instagram · Facebook · YouTube.")

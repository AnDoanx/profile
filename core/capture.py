#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Chụp ảnh website (/capwed).

2 lớp:
  1. Trình duyệt headless có sẵn trong máy (chromium / chrome / edge) — nhanh, không giới hạn.
  2. Dự phòng dịch vụ ảnh công khai (thum.io) — dùng khi image không có chromium
     hoặc khi trình duyệt lỗi.
"""

import asyncio
import ipaddress
import logging
import os
import shutil
import socket
import subprocess
import time
from io import BytesIO
from urllib.parse import urlparse

from config import CAPTURE_H, CAPTURE_TIMEOUT, CAPTURE_W, CHROME_PATH, TMP_DIR
from core import http, pool

log = logging.getLogger("khoaan.capture")

CHROME_CANDIDATES = [
    CHROME_PATH,
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)


class CaptureError(Exception):
    pass


# ------------------------------------------------------------------ tiện ích
def normalize_url(raw: str) -> str:
    """'example.com' -> 'https://example.com'."""
    raw = (raw or "").strip().strip("<>\"'")
    if not raw:
        return ""
    if not raw.lower().startswith(("http://", "https://")):
        raw = "https://" + raw.lstrip("/")
    return raw


def _blocked(url: str) -> bool:
    """Chặn địa chỉ nội bộ (tránh bot bị lợi dụng quét mạng riêng của server)."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return True
    if not host:
        return True
    if host in ("localhost", "localhost.localdomain") or host.endswith(".local"):
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:  # noqa: BLE001
        return False  # để trình duyệt tự báo lỗi DNS
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except Exception:  # noqa: BLE001
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return True
    return False


def _proxy_args() -> list[str]:
    """Chromium cần biết proxy nếu môi trường có (Railway thì không có)."""
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        val = (os.getenv(key) or "").strip()
        if val:
            return [f"--proxy-server={val}"]
    return ["--no-proxy-server"]


def find_browser() -> str:
    for cand in CHROME_CANDIDATES:
        if not cand:
            continue
        if os.path.sep in cand or "/" in cand or "\\" in cand:
            if os.path.exists(cand):
                return cand
            continue
        found = shutil.which(cand)
        if found:
            return found
    return ""


# ------------------------------------------------------------------ lớp 1: chromium
ERROR_MARKERS = ("main-frame-error", "neterror", "ERR_NO_SUPPORTED_PROXIES",
                 "ERR_NAME_NOT_RESOLVED", "ERR_CONNECTION", "ERR_PROXY")


def _base_args(browser: str, width: int, height: int) -> list[str]:
    return [
        browser,
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--disable-software-rasterizer",
        "--hide-scrollbars",
        "--force-device-scale-factor=1",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-background-networking",
        "--ignore-certificate-errors",
        *_proxy_args(),
        f"--user-agent={UA}",
        f"--window-size={width},{height}",
    ]


def _page_loaded(browser: str, url: str, width: int, height: int, timeout: int) -> bool:
    """Chrome không báo lỗi qua exit code -> đọc DOM để biết trang có tải được không."""
    args = _base_args(browser, width, height) + ["--dump-dom", "--virtual-time-budget=8000", url]
    try:
        proc = subprocess.run(  # noqa: S603
            args, capture_output=True, timeout=min(timeout, 30),
        )
    except subprocess.TimeoutExpired:
        return False
    dom = (proc.stdout or b"").decode("utf-8", "ignore")
    if not dom.strip():
        return False
    low = dom.lower()
    return not any(marker.lower() in low for marker in ERROR_MARKERS)


def _chromium_sync(url: str, width: int, height: int, timeout: int) -> bytes:
    browser = find_browser()
    if not browser:
        raise CaptureError("no-browser")
    if not _page_loaded(browser, url, width, height, timeout):
        raise CaptureError("trình duyệt không mở được trang")
    out_dir = TMP_DIR / f"cap_{int(time.time() * 1000)}"
    out_dir.mkdir(parents=True, exist_ok=True)
    shot = out_dir / "shot.png"
    args = _base_args(browser, width, height) + [
        "--virtual-time-budget=12000",
        f"--screenshot={shot}",
        url,
    ]
    try:
        proc = subprocess.run(  # noqa: S603
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
        if not shot.exists() or shot.stat().st_size < 1024:
            raise CaptureError(f"ảnh rỗng (mã {proc.returncode})")
        return shot.read_bytes()
    except subprocess.TimeoutExpired as e:
        raise CaptureError("trình duyệt quá thời gian") from e
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


# ------------------------------------------------------------------ lớp 2: dịch vụ ngoài
async def _probe(url: str) -> int:
    """Kiểm tra trang có truy cập được không (0 = không kết nối được)."""
    status, _, _ = await http.fetch(url, method="HEAD", timeout=12, retries=0)
    if not status:
        status, _, _ = await http.fetch(
            url, timeout=12, retries=0, headers={"Range": "bytes=0-2048"},
        )
    return int(status or 0)


async def _thumio(url: str, width: int, height: int) -> bytes:
    src = f"https://image.thum.io/get/width/{width}/crop/{height}/noanimate/{url}"
    return await http.download_bytes(src, max_bytes=15 * 1024 * 1024, timeout=90) or b""


# ------------------------------------------------------------------ entry
async def screenshot(raw_url: str, width: int = CAPTURE_W, height: int = CAPTURE_H,
                     timeout: int = CAPTURE_TIMEOUT) -> tuple[bytes, dict]:
    """Trả (ảnh PNG/JPEG, thông tin). Ném CaptureError nếu không chụp được."""
    url = normalize_url(raw_url)
    if not url:
        raise CaptureError("Thiếu link")
    if _blocked(url):
        raise CaptureError("Link này trỏ vào địa chỉ nội bộ, không chụp được")

    width = max(320, min(int(width), 1920))
    height = max(400, min(int(height), 4000))
    info: dict = {"url": url, "width": width, "height": height, "source": "", "status": 0}
    t0 = time.time()

    status = await _probe(url)
    info["status"] = status
    if not status:
        raise CaptureError("không kết nối được tới trang này")

    loop = asyncio.get_running_loop()
    try:
        data = await pool.run_io(_chromium_sync, url, width, height, timeout)
        info.update(source="chromium", elapsed_ms=int((time.time() - t0) * 1000))
        return data, info
    except CaptureError as e:
        if str(e) != "no-browser":
            log.info("chromium fail: %s -> thum.io", e)
    except Exception as e:  # noqa: BLE001
        log.info("chromium lỗi: %s -> thum.io", e)

    try:
        data = await _thumio(url, width, height)
        if data:
            info.update(source="thum.io", elapsed_ms=int((time.time() - t0) * 1000))
            return data, info
    except Exception as e:  # noqa: BLE001
        log.warning("thum.io lỗi: %s", e)

    raise CaptureError("Không chụp được trang này")


def is_image(data: bytes) -> bool:
    return data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff" or data[:4] == b"RIFF"


def to_png(data: bytes) -> bytes:
    """Chuẩn hoá về PNG (thum.io có thể trả JPEG)."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return data
    try:
        from PIL import Image

        img = Image.open(BytesIO(data)).convert("RGB")
        buf = BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return data


async def page_title(url: str) -> str:
    """Lấy <title> để hiện trong caption (lỗi thì trả rỗng)."""
    try:
        res = await http.fetch(url, timeout=15, retries=0)
        _status, body, _c = res
        if not body:
            return ""
        text = body.decode("utf-8", "ignore") if isinstance(body, bytes) else str(body)
        import re

        m = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
        if not m:
            return ""
        return re.sub(r"\s+", " ", m.group(1)).strip()[:120]
    except Exception:  # noqa: BLE001
        return ""

    return f"https://image.thum.io/get/width/{w}/crop/{h}/noanimate/{quote(url, safe='')}"

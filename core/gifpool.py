#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GIF CHIIKAWA (100%), gửi TRƯỚC mỗi câu trả lời.

Nguồn công khai, KHÔNG cần API key: tenor.com (trang tìm kiếm -> link .gif thật).
Toàn bộ pool chỉ gồm chiikawa (chiikawa / hachiware / usagi / momonga…), không
trộn anime khác nữa.

Tối ưu tốc độ: pool link cache 3 giờ + prefetch sẵn vài gif vào RAM.
Nhờ vậy lúc gửi chỉ còn upload, không phải chờ tải → gif hiện trước câu trả lời
mà gần như không làm chậm lệnh.
"""

import asyncio
import io
import logging
import random
import re
import time
from collections import deque

from core import http

log = logging.getLogger("khoaan.gif")

TENOR_SEARCH = "https://tenor.com/search/{q}-gifs"
TENOR_RE = re.compile(r'https://media\d?\.tenor\.com/[A-Za-z0-9_\-]+/[^"\\\s]+?\.gif')

# 100% chiikawa
QUERIES = [
    "chiikawa",
    "chiikawa-cute",
    "chiikawa-happy",
    "chiikawa-dance",
    "chiikawa-hachiware",
    "chiikawa-usagi",
    "chiikawa-momonga",
    "chiikawa-kawaii",
    "chiikawa-anime",
    "chiikawa-funny",
    "chiikawa-love",
    "chiikawa-sad",
]

TTL = 3 * 3600
MAX_PER_QUERY = 30
PREFETCH = 3                      # giữ sẵn bao nhiêu gif trong RAM
MAX_GIF_BYTES = 6 * 1024 * 1024

_pool: dict[str, tuple[float, list[str]]] = {}
_lock = asyncio.Lock()
_refreshing = False
_scenery: list[str] = []          # link chiikawa lấy từ tenor
_fallback: list[str] = []         # dự phòng (chỉ khi tenor trả rỗng)

_bytes: deque[bytes] = deque()    # gif đã tải sẵn vào RAM
_filling = False


async def _tenor(query: str) -> list[str]:
    status, body, _ = await http.fetch(
        TENOR_SEARCH.format(q=query),
        headers={"Accept-Language": "en-US,en;q=0.9", "Referer": "https://tenor.com/"},
        timeout=8,
        retries=0,
    )
    if status != 200 or not body:
        return []
    text = body.decode("utf-8", errors="replace")
    found = TENOR_RE.findall(text)
    out, seen = [], set()
    for u in found:
        base = u.rsplit("/", 1)[-1]
        if base in seen:
            continue
        seen.add(base)
        out.append(u.replace("&amp;", "&"))
    return out[:MAX_PER_QUERY]


async def _refresh() -> int:
    """Nạp lại toàn bộ pool. Trả về số link lấy được."""
    global _refreshing, _scenery, _fallback
    _refreshing = True
    try:
        results = await asyncio.gather(
            *(_tenor(q) for q in QUERIES), return_exceptions=True,
        )
        total = 0
        now = time.time()
        scenery: list[str] = []
        for idx, res in enumerate(results):
            if isinstance(res, Exception) or not res:
                continue
            key = QUERIES[idx]
            scenery.extend(res)
            _pool[key] = (now, list(res))
            total += len(res)
        # bỏ link trùng (cùng 1 gif xuất hiện ở nhiều từ khoá)
        seen: set[str] = set()
        unique: list[str] = []
        for u in scenery:
            base = u.rsplit("/", 1)[-1]
            if base in seen:
                continue
            seen.add(base)
            unique.append(u)
        _scenery = unique
        _fallback = []
        log.info("gif pool: %s link chiikawa", len(unique))
        return len(unique)
    finally:
        _refreshing = False


def _all() -> list[str]:
    """Danh sách link chiikawa."""
    if _scenery:
        return list(_scenery)
    if _fallback:
        return list(_fallback)
    now = time.time()
    urls: list[str] = []
    for ts, items in list(_pool.values()):
        if now - ts <= TTL:
            urls.extend(items)
    return urls


def _maybe_refresh_bg() -> None:
    if _refreshing or _scenery:
        return
    try:
        asyncio.create_task(_refresh())
    except RuntimeError:
        pass


async def pick_link() -> str | None:
    """Một link gif chiikawa ngẫu nhiên."""
    urls = _all()
    if not urls:
        async with _lock:
            urls = _all()
            if not urls:
                try:
                    await asyncio.wait_for(_refresh(), timeout=12)
                except Exception as e:  # noqa: BLE001
                    log.info("gif refresh fail: %s", str(e)[:120])
                urls = _all()
    if not urls:
        _maybe_refresh_bg()
        return None
    if len(urls) < 6:
        _maybe_refresh_bg()
    return random.choice(urls)


# ------------------------------------------------------------------ tải sẵn vào RAM
async def _fill() -> None:
    """Tải sẵn vài gif vào RAM để lúc gửi không phải chờ."""
    global _filling
    if _filling:
        return
    _filling = True
    tries = 0
    try:
        while len(_bytes) < PREFETCH and tries < 8:
            tries += 1
            url = await pick_link()
            if not url:
                break
            raw = await http.download_bytes(url, max_bytes=MAX_GIF_BYTES, timeout=10)
            if not raw:
                continue
            _bytes.append(raw)
    except Exception as e:  # noqa: BLE001
        log.info("gif prefetch fail: %s", str(e)[:120])
    finally:
        _filling = False


def _kick_fill() -> None:
    try:
        asyncio.create_task(_fill())
    except RuntimeError:
        pass


async def warmup() -> None:
    """Gọi lúc bot khởi động: có sẵn pool + gif trong RAM cho câu trả lời đầu tiên."""
    try:
        if not _all():
            await _refresh()
        await _fill()
        log.info("gif sẵn sàng: %s gif trong RAM", len(_bytes))
    except Exception as e:  # noqa: BLE001
        log.info("gif warmup fail: %s", str(e)[:120])


async def pick_bytes(timeout: float = 3.0) -> bytes | None:
    """Lấy 1 gif đã tải sẵn (bytes). None nếu không kịp — khi đó bot trả lời luôn."""
    _maybe_refresh_bg()
    if _bytes:
        raw = _bytes.popleft()
        _kick_fill()
        return raw
    # chưa prefetch kịp → tải trực tiếp nhưng có giới hạn thời gian
    url = await pick_link()
    if not url:
        return None
    try:
        raw = await asyncio.wait_for(
            http.download_bytes(url, max_bytes=MAX_GIF_BYTES, timeout=int(timeout) + 2),
            timeout=timeout,
        )
    except Exception:  # noqa: BLE001
        raw = None
    _kick_fill()
    return raw


async def pick() -> str | None:
    """Giữ tương thích code cũ."""
    return await pick_link()


# ------------------------------------------------------- gửi = file_id (nhanh, khỏi upload)
_file_ids: list[str] = []
_rot = 0
MAX_FILE_IDS = 120


def remember(file_id: str | None) -> None:
    """Nhớ file_id của gif vừa gửi thành công -> lần sau gửi lại tức thì."""
    if not file_id or file_id in _file_ids:
        return
    _file_ids.append(file_id)
    if len(_file_ids) > MAX_FILE_IDS:
        del _file_ids[0]


def forget(file_id: str | None) -> None:
    if file_id in _file_ids:
        _file_ids.remove(file_id)


def _next_fid() -> str | None:
    global _rot
    if not _file_ids:
        return None
    _rot = (_rot + 1) % len(_file_ids)
    return _file_ids[_rot]


async def send(bot, chat_id: int, reply_to: int | None = None) -> bool:
    """Gửi 1 gif. Trả về True nếu Telegram nhận.

    Ưu tiên file_id đã biết (Telegram giữ file sẵn -> gửi tức thì), chưa có thì
    dùng gif đã prefetch trong RAM. Mọi lỗi đều nuốt để không chặn câu trả lời.
    """
    from telegram.error import BadRequest, RetryAfter, Forbidden  # import tại chỗ cho gọn

    kw = dict(disable_notification=True, allow_sending_without_reply=True,
              read_timeout=12, write_timeout=12)
    fid = _next_fid()
    if fid:
        try:
            msg = await bot.send_animation(chat_id, fid, reply_to_message_id=reply_to, **kw)
            remember(getattr(getattr(msg, "animation", None), "file_id", None))
            return True
        except RetryAfter:
            raise                      # để caller biết mà nghỉ, tránh bị flood tiếp
        except BadRequest:
            forget(fid)
        except Exception:  # noqa: BLE001
            return False

    raw = await pick_bytes()
    if not raw:
        return False
    buf = io.BytesIO(raw)
    buf.name = "khoaan.gif"
    try:
        msg = await bot.send_animation(chat_id, buf, reply_to_message_id=reply_to, **kw)
        remember(getattr(getattr(msg, "animation", None), "file_id", None))
        return True
    except RetryAfter:
        raise
    except Forbidden:
        return False
    except Exception as e:  # noqa: BLE001
        log.info("gửi gif lỗi: %s", str(e)[:100])
    return False

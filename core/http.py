#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP session dùng chung + retry."""

import asyncio
import logging

import aiohttp

from config import HTTP_TIMEOUT

log = logging.getLogger("khoaan.http")

_session: aiohttp.ClientSession | None = None

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": UA,
    "Accept": "*/*",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}


async def session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=60, ttl_dns_cache=300, ssl=False),
            headers=DEFAULT_HEADERS,
            timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT, connect=10),
            trust_env=True,
        )
    return _session


async def close_session(*_args, **_kwargs):
    global _session
    if _session and not _session.closed:
        await _session.close()
    _session = None


async def fetch(
    url: str,
    *,
    method: str = "GET",
    headers: dict | None = None,
    data=None,
    json_data=None,
    timeout: int | None = None,
    retries: int = 2,
    allow_redirects: bool = True,
):
    """Trả về (status, bytes, headers). Lỗi mạng -> (0, b'', {})."""
    s = await session()
    hdrs = {**DEFAULT_HEADERS, **(headers or {})}
    t = aiohttp.ClientTimeout(total=timeout or HTTP_TIMEOUT, connect=10)
    last = (0, b"", {})
    for attempt in range(retries + 1):
        try:
            async with s.request(
                method,
                url,
                headers=hdrs,
                data=data,
                json=json_data,
                timeout=t,
                allow_redirects=allow_redirects,
                ssl=False,
            ) as r:
                body = await r.read()
                return r.status, body, dict(r.headers)
        except asyncio.TimeoutError:
            last = (0, b"", {"error": "timeout"})
        except Exception as e:  # noqa: BLE001
            last = (0, b"", {"error": str(e)[:180]})
        if attempt < retries:
            await asyncio.sleep(0.7 * (attempt + 1))
    return last


async def fetch_text(url: str, **kwargs) -> str:
    status, body, _ = await fetch(url, **kwargs)
    if status == 0:
        return ""
    try:
        return body.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""


async def fetch_json(url: str, **kwargs):
    status, body, _ = await fetch(url, **kwargs)
    if status == 0 or not body:
        return None
    import json

    try:
        return json.loads(body.decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return None


async def download_bytes(url: str, *, max_bytes: int = 50 * 1024 * 1024, headers: dict | None = None, timeout: int = 90):
    """Tải file về RAM, chặn nếu vượt max_bytes. Trả về bytes hoặc None."""
    s = await session()
    hdrs = {**DEFAULT_HEADERS, **(headers or {})}
    try:
        async with s.get(url, headers=hdrs, timeout=aiohttp.ClientTimeout(total=timeout, connect=12), ssl=False) as r:
            if r.status != 200:
                return None
            buf = bytearray()
            async for chunk in r.content.iter_chunked(65536):
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    return None
            return bytes(buf)
    except Exception as e:  # noqa: BLE001
        log.warning("download_bytes fail %s: %s", url[:90], e)
        return None

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Health-check server nhỏ để Railway không đánh bot là "chết".

Railway (và hầu hết PaaS) chỉ coi service là deploy thành công khi có process
lắng nghe biến $PORT và trả về HTTP 200. Bot Telegram chạy polling không mở
port nào cả → Railway báo "Application failed to respond" / deploy fail.
Module này mở 1 HTTP server tối giản ở $PORT, song song với bot.
"""

import logging
import os
import time

from aiohttp import web

log = logging.getLogger("khoaan.health")

_STARTED = time.time()
_runner: web.AppRunner | None = None
_state: dict = {"bot": None, "requests": 0, "errors": 0}


def set_bot(username: str) -> None:
    _state["bot"] = username


def count_request(errors: int = 0) -> None:
    _state["requests"] += 1
    _state["errors"] += errors


async def _health(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "ok": True,
            "service": "khoaan-tools",
            "bot": _state["bot"],
            "uptime_s": int(time.time() - _STARTED),
            "requests": _state["requests"],
            "errors": _state["errors"],
        }
    )


async def _root(request: web.Request) -> web.Response:
    return web.Response(
        text=(
            "KHOA AN TOOLS — bot đang chạy ✅\n"
            f"uptime: {int(time.time() - _STARTED)}s\n"
            f"bot: @{_state['bot']}\n"
        ),
        content_type="text/plain",
    )


async def start() -> None:
    """Mở HTTP server ở $PORT. Lỗi ở đây không được phép làm chết bot."""
    global _runner
    port = int(os.getenv("PORT", "8080"))
    app = web.Application()
    app.router.add_get("/", _root)
    app.router.add_get("/health", _health)
    app.router.add_get("/healthz", _health)
    app.router.add_get("/ping", _health)
    try:
        _runner = web.AppRunner(app, access_log=None)
        await _runner.setup()
        await web.TCPSite(_runner, "0.0.0.0", port).start()
        log.info("Health server nghe ở 0.0.0.0:%s", port)
    except Exception as e:  # noqa: BLE001
        log.warning("Không mở được health server ở cổng %s: %s", port, e)


async def stop() -> None:
    global _runner
    if _runner:
        try:
            await _runner.cleanup()
        except Exception:  # noqa: BLE001
            pass
        _runner = None

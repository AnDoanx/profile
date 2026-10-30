#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thread pool riêng cho việc nặng — để bot không bị "khựng" khi có job chậm.

Vì sao cần: `loop.run_in_executor(None, ...)` dùng pool mặc định của asyncio
(`min(32, cpu+4)` thread, dùng chung cho MỌI thứ). Chỉ cần 1 job yt-dlp treo
20–30s là các job khác (vẽ canvas, đổi mp3, chụp web, gtts) xếp hàng phía sau
→ người dùng thấy cả bot đứng, không riêng lệnh đang chạy.

Tách làm 2 pool:
  • IO  — việc chờ mạng / tiến trình ngoài (yt-dlp, ffmpeg, gtts, chromium)
  • CPU — vẽ ảnh (canvas, sticker)

Cả hai đều KHÔNG chặn event loop, và không chiếm pool mặc định nữa.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("khoaan.pool")

# IO: 4 thread là đủ cho yt-dlp + ffmpeg + tải file; nhiều hơn chỉ tranh CPU.
IO_EXEC = ThreadPoolExecutor(max_workers=4, thread_name_prefix="khoaan-io")
# CPU: vẽ ảnh là việc nặng nhưng ngắn — 2 thread, xong ngay.
CPU_EXEC = ThreadPoolExecutor(max_workers=2, thread_name_prefix="khoaan-cpu")


async def run_io(fn, *args):
    """Chạy hàm đồng bộ (chờ mạng/tiến trình) trong pool IO."""
    return await asyncio.get_running_loop().run_in_executor(IO_EXEC, fn, *args)


async def run_cpu(fn, *args):
    """Chạy hàm đồng bộ (vẽ ảnh / tính toán) trong pool CPU."""
    return await asyncio.get_running_loop().run_in_executor(CPU_EXEC, fn, *args)


def shutdown(*_args, **_kwargs):
    """Gọi lúc bot tắt. Python < 3.9 không có cancel_futures -> lùi an toàn."""
    for ex in (IO_EXEC, CPU_EXEC):
        try:
            ex.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            ex.shutdown(wait=False)
        except Exception as e:  # noqa: BLE001
            log.info("đóng pool lỗi (bỏ qua): %s", e)

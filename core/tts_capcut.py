#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Giọng CapCut / TikTok (endpoint không chính thức — dùng được thì dùng).

CapCut dùng chung kho giọng với TikTok:
    BV074_streaming  → tiếng Việt, nữ
    BV075_streaming  → tiếng Việt, nam
    BV001_streaming  → English, nữ     BV002_streaming → English, nam
    BV700_streaming  → English, nữ 2   BV701_streaming → English, nam 2

Endpoint này Telegram/TikTok đổi liên tục và bắt chữ ký, nên module chạy theo
kiểu "thử rồi thôi": host nào fail thì nghỉ 15 phút, không làm chậm lệnh /tts.
Không lấy được -> trả None, tts.py tự đọc bằng giọng Microsoft tương đương.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from urllib.parse import quote

from core import http

log = logging.getLogger("khoaan.tts.capcut")

DEAD_FOR = 15 * 60          # giây: host lỗi thì bỏ qua, khỏi chờ mỗi lần
_TIMEOUT = 7

_DEAD: dict[str, float] = {}

UA_TT = ("com.zhiliaoapp.musically/2022600030 (Linux; U; Android 12; vi_VN; "
         "Pixel 5; Build/SQ3A.220705.004)")

# giọng CapCut/TikTok hay dùng cho tiếng Việt + tiếng Anh
CAPCUT_VOICES = {
    "BV074_streaming": "Nữ · CapCut",
    "BV075_streaming": "Nam · CapCut",
    "BV001_streaming": "English nữ · CapCut",
    "BV002_streaming": "English nam · CapCut",
    "BV700_streaming": "English nữ 2 · CapCut",
    "BV701_streaming": "English nam 2 · CapCut",
}


def _alive(host: str) -> bool:
    return _DEAD.get(host, 0) < time.time()


def _kill(host: str) -> None:
    _DEAD[host] = time.time() + DEAD_FOR


def _extract(body: bytes) -> bytes | None:
    """Endpoint trả JSON (audio base64) hoặc trả thẳng mp3."""
    if not body:
        return None
    if body[:3] == b"ID3" or body[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return body
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    body_obj = data.get("data") if isinstance(data.get("data"), dict) else data
    for key in ("v_str", "audio", "audio_base64", "speech", "url_mp3", "base64"):
        val = body_obj.get(key) if isinstance(body_obj, dict) else None
        if not isinstance(val, str) or len(val) < 200:
            continue
        if val.startswith("http"):
            return None                       # để nơi khác tải, tránh 2 bước
        try:
            raw = base64.b64decode(val)
            if len(raw) > 800:
                return raw
        except Exception:  # noqa: BLE001
            continue
    return None


async def synth(text: str, voice: str) -> bytes | None:
    """Trả mp3 bytes của CapCut, hoặc None nếu endpoint chết."""
    text = (text or "").strip()[:600]
    if not text:
        return None
    voice = voice or "BV074_streaming"
    q = quote(text)

    hosts = [
        "api16-normal-c-useast1a.tiktokv.com",
        "api22-normal-c-useast2a.tiktokv.com",
    ]
    for host in hosts:
        if not _alive(host):
            continue
        url = (f"https://{host}/media/api/text/speech/invoke/"
               f"?text_speaker={voice}&req_text={q}&speaker_map_type=0&aid=1180")
        status, body, _ = await http.fetch(
            url, headers={"User-Agent": UA_TT, "Accept": "application/json"},
            timeout=_TIMEOUT, retries=0,
        )
        data = _extract(body) if status == 200 else None
        if data:
            log.info("capcut tts OK (%s)", voice)
            return data
        _kill(host)
        log.info("capcut tts chết (%s → HTTP %s)", host, status)

    for url in ("https://api.capcut.com/lv/v1/tts", "https://editor-api.capcut.com/lv/v1/tts"):
        host = url.split("/")[2]
        if not _alive(host):
            continue
        status, body, _ = await http.fetch(
            url, method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "CapCut/11.0.0"},
            json_data={"text": text, "speaker": voice, "to_type": 1, "resource_id": "7102353496878256648"},
            timeout=_TIMEOUT, retries=0,
        )
        data = _extract(body) if status == 200 else None
        if data:
            log.info("capcut tts OK qua %s", host)
            return data
        _kill(host)

    return None


def status_text() -> str:
    live = [h for h in ("api16-normal-c-useast1a.tiktokv.com", "api.capcut.com", "editor-api.capcut.com")
            if _alive(h)]
    return "đang thử" if live else "tạm nghỉ (tự đọc bằng giọng Microsoft)"

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tiện ích phụ: thời tiết, dịch, rút gọn link, dọn rác."""

import asyncio
import io
import logging
import os
import re
import shutil
import time
import urllib.parse

from config import TMP_DIR
from core import http

log = logging.getLogger("khoaan.utils")


# ------------------------------------------------------------------ weather
WEATHER_VI = {
    0: "Trời quang", 1: "Ít mây", 2: "Có mây", 3: "Nhiều mây",
    45: "Sương mù", 48: "Sương giá", 51: "Mưa phùn nhẹ", 53: "Mưa phùn",
    55: "Mưa phùn nặng", 61: "Mưa nhỏ", 63: "Mưa vừa", 65: "Mưa to",
    66: "Mưa đá nhẹ", 67: "Mưa đá", 71: "Tuyết nhẹ", 73: "Tuyết vừa",
    75: "Tuyết dày", 77: "Hạt tuyết", 80: "Mưa rào nhẹ", 81: "Mưa rào",
    82: "Mưa rào lớn", 85: "Mưa tuyết", 86: "Mưa tuyết lớn",
    95: "Giông bão", 96: "Giông kèm mưa đá", 99: "Giông mạnh",
}


async def weather(city: str) -> dict | None:
    geo = await http.fetch_json(
        "https://geocoding-api.open-meteo.com/v1/search"
        f"?name={city}&count=1&language=vi&format=json",
        timeout=15,
    )
    if not geo or not geo.get("results"):
        return None
    g = geo["results"][0]
    lat, lon = g["latitude"], g["longitude"]
    data = await http.fetch_json(
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m"
        "&daily=temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max"
        "&timezone=auto&forecast_days=3",
        timeout=15,
    )
    if not data:
        return None
    cur = data.get("current") or {}
    daily = data.get("daily") or {}
    days = []
    for i in range(min(3, len(daily.get("time") or []))):
        days.append({
            "date": daily["time"][i],
            "max": daily["temperature_2m_max"][i],
            "min": daily["temperature_2m_min"][i],
            "code": daily["weather_code"][i],
            "rain": (daily.get("precipitation_probability_max") or [None] * 3)[i],
        })
    return {
        "name": ", ".join(x for x in [g.get("name"), g.get("admin1"), g.get("country")] if x),
        "temp": cur.get("temperature_2m"),
        "feels": cur.get("apparent_temperature"),
        "humidity": cur.get("relative_humidity_2m"),
        "wind": cur.get("wind_speed_10m"),
        "code": cur.get("weather_code"),
        "desc": WEATHER_VI.get(cur.get("weather_code"), "Không rõ"),
        "days": days,
    }


def weather_text(w: dict) -> str:
    lines = [
        f"🌤 <b>{w['name']}</b>",
        f"Nhiệt độ: <b>{w['temp']}°C</b> (cảm giác {w['feels']}°C)",
        f"Tình trạng: {w['desc']}",
        f"Độ ẩm: {w['humidity']}% • Gió: {w['wind']} km/h",
        "",
        "<b>3 ngày tới</b>",
    ]
    for d in w["days"]:
        dd = d["date"][5:].replace("-", "/")
        rain = f" • mưa {d['rain']}%" if d["rain"] is not None else ""
        lines.append(f"• {dd}: {WEATHER_VI.get(d['code'], '?')} — {d['min']}°C ↑ {d['max']}°C{rain}")
    return "\n".join(lines)


# ------------------------------------------------------------------ translate
def _vn_score(text: str) -> int:
    vn_chars = "ăâđêôơưàảãáạằẳẵắặầẩẫấậèẻẽéẹềểễếệìỉĩíịòỏõóọồổỗốộờởỡớợùủũúụừửữứựỳỷỹýỵ"
    return sum(1 for c in text.lower() if c in vn_chars)


def guess_lang_pair(text: str, pair: str | None = None) -> tuple[str, str]:
    if pair and ">" in pair:
        a, b = pair.split(">", 1)
        return a.strip() or "auto", b.strip() or "vi"
    if _vn_score(text) >= 2:
        return "vi", "en"
    return "en", "vi"


async def translate(text: str, pair: str | None = None) -> tuple[str, str, str]:
    src, tgt = guess_lang_pair(text, pair)
    if src == "auto":
        src = "en"
    text = text.strip()[:480]
    data = await http.fetch_json(
        "https://api.mymemory.translated.net/get"
        f"?q={urllib.parse.quote(text)}&langpair={src}|{tgt}&de=bot@khoaan.dev",
        timeout=18,
        retries=1,
    )
    if data and isinstance(data.get("responseData"), dict):
        out = data["responseData"].get("translatedText") or ""
        if out and "QUERY LENGTH LIMIT" not in out.upper():
            return out, src, tgt
    # dự phòng Google
    data2 = await http.fetch_json(
        "https://translate.googleapis.com/translate_a/single"
        f"?client=gtx&sl={src}&tl={tgt}&dt=t&q={urllib.parse.quote(text)}",
        timeout=15,
        retries=0,
    )
    if data2 and isinstance(data2, list) and data2[0]:
        return "".join(part[0] for part in data2[0] if part and part[0]), src, tgt
    return "", src, tgt


# ------------------------------------------------------------------ shorten
async def shorten(url: str) -> str | None:
    status, body, _ = await http.fetch(f"https://tinyurl.com/api-create.php?url={url}", timeout=15, retries=1)
    if status == 200 and body:
        out = body.decode("utf-8", errors="replace").strip()
        if out.startswith("http"):
            return out
    data = await http.fetch_json("https://cleanuri.com/api/v1/shorten", method="POST",
                                 data={"url": url}, timeout=15, retries=0)
    if data and data.get("result_url"):
        return data["result_url"]
    return None


# ------------------------------------------------------------------ ip info
async def ip_info(query: str) -> dict | None:
    q = query.strip()
    if not q:
        return None
    data = await http.fetch_json(
        f"http://ip-api.com/json/{q}?fields=status,message,country,regionName,city,isp,org,as,query,timezone,lat,lon,reverse&lang=vi",
        timeout=15,
        retries=1,
    )
    if not data or data.get("status") != "success":
        return None
    return data


def ip_text(d: dict) -> str:
    return (
        f"🌐 <b>{d['query']}</b>\n"
        f"• Quốc gia: {d.get('country', '?')}\n"
        f"• Khu vực: {d.get('regionName', '?')} — {d.get('city', '?')}\n"
        f"• Nhà mạng: {d.get('isp', '?')}\n"
        f"• Tổ chức: {d.get('org', '?')}\n"
        f"• ASN: {d.get('as', '?')}\n"
        f"• Múi giờ: {d.get('timezone', '?')}"
    )


# ------------------------------------------------------------------ cleanup
def clean_temp(max_age_hours: float = 2.0) -> tuple[int, float]:
    removed, freed = 0, 0.0
    now = time.time()
    base = str(TMP_DIR)
    for root, dirs, files in os.walk(base):
        if root != base:
            continue
        for name in files + dirs:
            path = os.path.join(root, name)
            try:
                if now - os.path.getmtime(path) < max_age_hours * 3600:
                    continue
                if os.path.isdir(path):
                    size = sum(os.path.getsize(os.path.join(dp, f))
                               for dp, _, fs in os.walk(path) for f in fs)
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    size = os.path.getsize(path)
                    os.remove(path)
                removed += 1
                freed += size
            except Exception:  # noqa: BLE001
                continue
    return removed, freed / 1024 / 1024


def dir_size() -> float:
    total = 0
    for dp, _, fs in os.walk(str(TMP_DIR)):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return total / 1024 / 1024

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lấy UID của emoji premium (custom emoji) trong tin nhắn -> xuất file."""

import io
import json
import re
import time

from telegram import Message

from core.text import clean_text


def _entities(msg: Message) -> list:
    out = list(msg.entities or [])
    out += list(getattr(msg, "caption_entities", None) or [])
    return out


def _utf16_slice(text: str, offset: int, length: int) -> str:
    """Offset của Telegram tính theo UTF-16 code unit, cắt chuỗi Python phải quy đổi."""
    if not text or offset < 0 or length <= 0:
        return ""
    units = 0
    start = end = None
    for i, ch in enumerate(text):
        if units == offset and start is None:
            start = i
        units += 2 if ord(ch) > 0xFFFF else 1
        if start is not None and units >= offset + length:
            end = i + 1
            break
    if start is None:
        return ""
    return text[start:end if end is not None else len(text)]


def _from_entities(msg: Message) -> list[dict]:
    found: list[dict] = []
    text = msg.text or msg.caption or ""
    for ent in _entities(msg):
        etype = str(getattr(ent, "type", "")).lower()
        custom_id = getattr(ent, "custom_emoji_id", None)
        if etype != "custom_emoji" and not custom_id:
            continue
        if not custom_id:
            continue
        found.append({
            "emoji": _utf16_slice(text, int(ent.offset or 0), int(ent.length or 0)),
            "id": str(custom_id),
        })
    return found


_TG_EMOJI_RE = re.compile(r'<tg-emoji[^>]*emoji-id="(\d+)"[^>]*>(.*?)</tg-emoji>', re.S)


def _from_html(text: str) -> list[dict]:
    return [{"emoji": m.group(2).strip(), "id": m.group(1)} for m in _TG_EMOJI_RE.finditer(text or "")]


_ID_RE = re.compile(r"\b(5\d{18}|5\d{17})\b")


def _from_plain(text: str) -> list[dict]:
    return [{"emoji": "", "id": m.group(1)} for m in _ID_RE.finditer(text or "")]


def extract(message: Message) -> list[dict]:
    """Trả [{'emoji': '😀', 'id': '5368324170671202286'}, …] (bỏ trùng, giữ thứ tự)."""
    items = _from_entities(message)
    raw = message.text or message.caption or ""
    if not items:
        items = _from_html(raw)
    if not items:
        items = _from_plain(raw)

    seen: set[str] = set()
    unique: list[dict] = []
    for it in items:
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        unique.append(it)
    return unique


def build_txt(items: list[dict], source: str = "") -> tuple[bytes, str]:
    """Tạo file .txt: bảng chi tiết + danh sách UID trần để copy vào code."""
    stamp = time.strftime("%d/%m/%Y %H:%M:%S")
    lines = [
        "════════════════════════════════════════════",
        "  EMOJI PREMIUM — DANH SÁCH UID",
        "════════════════════════════════════════════",
        f"  Nguồn   : {clean_text(source) or 'tin nhắn Telegram'}",
        f"  Xuất lúc: {stamp}",
        f"  Tổng    : {len(items)} emoji",
        "────────────────────────────────────────────",
        "",
        "CHI TIẾT",
        "",
    ]
    for i, it in enumerate(items, 1):
        emo = it.get("emoji") or "(?)"
        lines.append(f"{i:>2}. {emo}   →   {it['id']}")

    lines += [
        "",
        "────────────────────────────────────────────",
        "CHỈ UID (copy dán vào code)",
        "────────────────────────────────────────────",
    ]
    lines += [it["id"] for it in items]

    lines += [
        "",
        "────────────────────────────────────────────",
        "DÙNG TRONG HTML CỦA BOT",
        "────────────────────────────────────────────",
        "Cú pháp: <tg-emoji emoji-id=\"UID\">😀</tg-emoji>",
        "Ví dụ  :",
        "  <tg-emoji emoji-id=\"" + (items[0]["id"] if items else "5368324170671202286") + "\">😀</tg-emoji>",
        "",
        "(Cần parse_mode=HTML khi gửi tin để Telegram hiện emoji động.)",
        "",
    ]
    return ("\n".join(lines) + "\n").encode("utf-8"), f"emoji_uid_{len(items)}_{int(time.time())}.txt"


def build_json(items: list[dict]) -> tuple[bytes, str]:
    payload = {
        "total": len(items),
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "emojis": [{"emoji": it.get("emoji", ""), "id": it["id"]} for it in items],
        "ids": [it["id"] for it in items],
    }
    return (
        json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        f"emoji_uid_{len(items)}_{int(time.time())}.json",
    )

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bộ đếm dùng cho panel admin."""

from core.store import JsonStore

STORE = JsonStore("stats.json", {
    "start": 0,
    "tiktok_dl": 0,
    "sing": 0,
    "tts": 0,
    "capwed": 0,
    "weather": 0,
    "emoji": 0,
    "banner": 0,
    "captcha": 0,
})


def bump(key: str, n: int = 1) -> None:
    STORE.data[key] = int(STORE.data.get(key) or 0) + n
    STORE.save()


def all_stats() -> dict:
    return dict(STORE.data)

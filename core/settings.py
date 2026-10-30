#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cài đặt nhỏ lưu bền (giọng đọc mặc định…)."""

from core.store import JsonStore

_STORE = JsonStore("settings.json", {"voice.default": "nam"})


def get(key: str, default: str = "") -> str:
    return str(_STORE.data.get(key) or default)


def set_value(key: str, value) -> None:
    _STORE.data[key] = value
    _STORE.save()

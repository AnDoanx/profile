#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kho JSON nhỏ, ghi an toàn (atomic) — dùng cho nhóm, admin, cài đặt."""

import json
import logging
import os
import tempfile
import threading

from config import DATA_DIR

log = logging.getLogger("khoaan.store")

_LOCK = threading.RLock()


class JsonStore:
    def __init__(self, filename: str, default: dict | None = None):
        self.path = DATA_DIR / filename
        self._default = default or {}
        self._data = self._load()

    def _load(self) -> dict:
        data = json.loads(json.dumps(self._default))  # copy sâu
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text("utf-8"))
                if isinstance(raw, dict):
                    data.update(raw)
        except Exception as e:  # noqa: BLE001
            log.warning("đọc %s lỗi: %s", self.path.name, e)
        return data

    def reload(self) -> dict:
        with _LOCK:
            self._data = self._load()
            return self._data

    @property
    def data(self) -> dict:
        return self._data

    def save(self) -> None:
        with _LOCK:
            try:
                fd, tmp = tempfile.mkstemp(dir=str(DATA_DIR), suffix=".tmp")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self.path)
            except Exception as e:  # noqa: BLE001
                log.warning("ghi %s lỗi: %s", self.path.name, e)

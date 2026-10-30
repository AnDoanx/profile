#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quản lý admin của bot (panel)."""

from config import ADMIN_IDS, ROOT_ADMIN
from core.store import JsonStore

_STORE = JsonStore("admins.json", {"ids": []})


def _stored() -> set[int]:
    raw = _STORE.data.get("ids") or []
    out: set[int] = set()
    for x in raw:
        try:
            out.add(int(x))
        except Exception:  # noqa: BLE001
            continue
    return out


def all_ids() -> set[int]:
    return set(ADMIN_IDS) | _stored() | {ROOT_ADMIN}


def is_admin(user_id: int | None) -> bool:
    return bool(user_id) and int(user_id) in all_ids()


def is_root(user_id: int | None) -> bool:
    return bool(user_id) and int(user_id) == ROOT_ADMIN


def add(user_id: int) -> bool:
    if is_admin(user_id):
        return False
    ids = sorted(_stored() | {int(user_id)})
    _STORE.data["ids"] = ids
    _STORE.save()
    return True


def remove(user_id: int) -> bool:
    uid = int(user_id)
    if uid == ROOT_ADMIN:
        return False
    ids = _stored()
    if uid not in ids:
        return False
    ids.discard(uid)
    _STORE.data["ids"] = sorted(ids)
    _STORE.save()
    return True

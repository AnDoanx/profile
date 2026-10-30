#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Xác minh người dùng lần đầu bằng phép tính đơn giản (1 + 2 = ?).

- Người chưa xác minh: bot chặn mọi lệnh/nút, chỉ gửi 1 câu hỏi + 4 đáp án bấm.
- Đúng -> lưu vào data/verified.json, từ đó không hỏi lại nữa (restart vẫn nhớ).
- Admin bot luôn được đi qua (không bị chặn).
"""

import random
import secrets
import time

from core import settings
from core.store import JsonStore

KEY = "captcha.enabled"      # công tắc lưu bền trong data/settings.json
MAX_TRIES = 3                # sai 3 lần -> đổi câu khác + cảnh báo
RESEND_GAP = 1.5             # giây, chống spam captcha khi user bấm liên tục

_STORE = JsonStore("verified.json", {"users": {}})
_PENDING: dict[int, dict] = {}   # uid -> {token, q, answer, ts, tries}


# ------------------------------------------------------------------ bật / tắt
def enabled() -> bool:
    """Captcha đang bật? (.env CAPTCHA_ON + công tắc /captcha của admin)."""
    import config
    if not getattr(config, "CAPTCHA_ON", True):
        return False
    return str(settings.get(KEY, "1")).strip().lower() not in ("0", "off", "false", "no")


def set_enabled(on: bool) -> None:
    settings.set_value(KEY, "1" if on else "0")


# ------------------------------------------------------------------ danh sách đã xác minh
def _users() -> dict:
    raw = _STORE.data.get("users")
    if not isinstance(raw, dict):
        raw = {}
        _STORE.data["users"] = raw
    return raw


def is_verified(uid: int | None) -> bool:
    if not uid:
        return False
    return str(int(uid)) in _users()


def mark(uid: int, name: str = "") -> bool:
    """Đánh dấu đã xác minh. Trả True nếu là lần đầu."""
    uid = int(uid)
    users = _users()
    first = str(uid) not in users
    users[str(uid)] = {"at": int(time.time()), "name": (name or "")[:64]}
    _STORE.save()
    return first


def forget(uid: int) -> bool:
    users = _users()
    existed = users.pop(str(int(uid)), None) is not None
    if existed:
        _STORE.save()
    return existed


def total() -> int:
    return len(_users())


# ------------------------------------------------------------------ đếm số lệnh đã dùng
# Ai dùng tới N lệnh (mặc định 10) thì bắt giải captcha 1 lần nữa để chống spam.
_USAGE = JsonStore("usage.json", {"users": {}})


def _usage() -> dict:
    raw = _USAGE.data.get("users")
    if not isinstance(raw, dict):
        raw = {}
        _USAGE.data["users"] = raw
    return raw


def bump_usage(uid: int, every: int | None = None) -> int:
    """+1 lệnh vừa dùng. Trả về số lệnh trong "vòng" hiện tại."""
    import config

    every = int(every or getattr(config, "CAPTCHA_EVERY", 10) or 10)
    if every <= 0:
        return 0
    db = _usage()
    key = str(int(uid))
    rec = db.get(key) or {"n": 0, "block": 0}
    rec["n"] = int(rec.get("n") or 0) + 1
    rec["t"] = int(time.time())
    if rec["n"] >= every:
        rec["block"] = 1
    db[key] = rec
    if len(db) > 50000:
        for k in sorted(db, key=lambda x: db[x].get("t", 0))[: len(db) - 50000]:
            db.pop(k, None)
    _USAGE.save()
    return rec["n"]


def requires_verify(uid: int | None) -> bool:
    """Người này có đang bị bắt giải captcha (do dùng quá N lệnh) không?"""
    if not uid:
        return False
    rec = _usage().get(str(int(uid))) or {}
    return bool(rec.get("block"))


def reset_usage(uid: int) -> int:
    """Giải xong captcha -> đếm lại từ 0."""
    db = _usage()
    key = str(int(uid))
    old = int((db.get(key) or {}).get("n") or 0)
    db[key] = {"n": 0, "block": 0, "t": int(time.time())}
    _USAGE.save()
    return old


def usage_of(uid: int) -> int:
    return int((_usage().get(str(int(uid))) or {}).get("n") or 0)


# ------------------------------------------------------------------ tạo câu hỏi
def _question() -> tuple[str, int]:
    kind = random.choices(("add", "sub", "mul"), weights=(6, 3, 2))[0]
    if kind == "add":
        a, b = random.randint(2, 19), random.randint(2, 19)
        return f"{a} + {b}", a + b
    if kind == "sub":
        a, b = random.randint(9, 25), random.randint(2, 9)
        return f"{a} − {b}", a - b
    a, b = random.randint(2, 9), random.randint(2, 9)
    return f"{a} × {b}", a * b


def _options(answer: int) -> list[str]:
    """4 đáp án, đúng 1 cái là đáp án thật, xếp ngẫu nhiên."""
    pool: set[int] = {answer}
    tries = 0
    while len(pool) < 4 and tries < 60:
        tries += 1
        delta = random.choice((-3, -2, -1, 1, 2, 3, 4, -4, 5))
        cand = answer + delta
        if cand >= 0:
            pool.add(cand)
    while len(pool) < 4:                       # cực hiếm, vẫn phải đủ 4 nút
        pool.add(answer + len(pool) + 1)
    opts = list(pool)
    random.shuffle(opts)
    return [str(x) for x in opts]


def issue(uid: int, force: bool = False) -> dict | None:
    """Tạo câu hỏi mới. Trả None nếu vừa gửi xong (< RESEND_GAP giây) để chống spam."""
    uid = int(uid)
    old = _PENDING.get(uid)
    if old and not force and time.time() - old.get("ts", 0) < RESEND_GAP:
        return None
    q, answer = _question()
    item = {
        "token": secrets.token_hex(3),
        "q": q,
        "answer": answer,
        "options": _options(answer),
        "ts": time.time(),
        "tries": 0,
    }
    _PENDING[uid] = item
    return item


def pending(uid: int) -> dict | None:
    return _PENDING.get(int(uid))


def bump_try(uid: int) -> int:
    item = _PENDING.get(int(uid))
    if not item:
        return 0
    item["tries"] = int(item.get("tries") or 0) + 1
    return item["tries"]


def check(uid: int, token: str, value: str) -> str:
    """Kiểm tra đáp án: ok | wrong | stale | none."""
    uid = int(uid)
    item = _PENDING.get(uid)
    if not item:
        return "none"
    if token != item.get("token"):
        return "stale"
    try:
        if int(str(value).strip()) == int(item["answer"]):
            return "ok"
    except Exception:  # noqa: BLE001
        return "wrong"
    return "wrong"


def clear(uid: int) -> None:
    _PENDING.pop(int(uid), None)


# ------------------------------------------------------------------ giao diện
def text(item: dict, tries: int = 0, reason: str = "first") -> str:
    """Câu hỏi + hướng dẫn (emoji sẽ được main.py bọc icon premium).

    `reason`: "first" = người mới, "spam" = đã dùng nhiều lệnh nên xác minh lại.
    """
    warn = ""
    if tries:
        warn = f"\n⚠️ <i>Sai {tries} lần rồi — bình tĩnh, tính lại nhé.</i>"
    if reason == "spam":
        head = "🛡 <b>XÁC MINH CHỐNG SPAM</b>"
        sub = "<i>Bạn đã dùng khá nhiều lệnh — giải phép tính để dùng tiếp</i>"
        tail = "✅ Đúng là dùng tiếp bình thường (10 lệnh nữa mới hỏi lại)."
    else:
        head = "🛡 <b>XÁC MINH LẦN ĐẦU</b>"
        sub = "<i>Để chống spam, bạn giải giúp mình phép tính này</i>"
        tail = "✅ Đúng là dùng bot luôn, không hỏi lại lần nữa."
    return (
        f"{head}\n"
        f"{sub}\n"
        "\n"
        f"      ✨ <b>{item['q']} = ?</b> ✨\n"
        "\n"
        "⏳ Chọn 1 đáp án bên dưới.\n"
        f"{tail}"
        f"{warn}"
    )


def keyboard(item: dict):
    """4 đáp án → 2 hàng, mỗi nút mang token để biết câu nào."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    from core import buttons as B

    opts = list(item.get("options") or [])
    token = item.get("token", "")
    rows, row = [], []
    for value in opts:
        row.append(B.btn(str(value), cb=f"cp_{token}_{value}", style=B.BLUE, icon="✅"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)

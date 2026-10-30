#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dịch tin nhắn — admin reply dấu "." là bot tự dịch, kèm bảng chọn ngôn ngữ.

Cách dùng:
• Admin reply (trả lời) một tin nhắn bất kỳ rồi gửi đúng một dấu ".".
• Bot tự nhận ra ngôn ngữ gốc, dịch sang ngôn ngữ admin đã chọn (mặc định tiếng Việt)
  và gắn bên dưới một bảng chọn ~80 ngôn ngữ (mỗi nút có icon premium riêng).
• Bấm nút ngôn ngữ khác -> dịch lại ngay tại chỗ, không gửi tin mới.

Máy dịch: Google (client=gtx, tự nhận ngôn ngữ) là chính, MyMemory là dự phòng.
Văn bản dài được cắt thành khối ~1600 ký tự theo dấu câu để không bị giới hạn.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import time
import urllib.parse
from pathlib import Path

from telegram.error import BadRequest

import config
from core import buttons as B
from core import http
from core import premium

log = logging.getLogger("khoaan.dich")

STORE = Path(config.DATA_DIR) / "translate.json"
DEFAULT_TARGET = "vi"
PAGE_SIZE = 12
MAX_TEXT = 6000

# ─────────────────────────────────────────────── danh mục ngôn ngữ
# (code Google, nhãn ngắn) — xếp theo mức dùng thực tế, ngôn ngữ hiếm để sau.
LANGS: list[tuple[str, str]] = [
    ("vi", "Việt"), ("en", "English"), ("zh-CN", "Trung"), ("zh-TW", "Trung (Đài)"),
    ("ja", "Nhật"), ("ko", "Hàn"), ("th", "Thái"), ("lo", "Lào"),
    ("km", "Khmer"), ("my", "Myanmar"), ("id", "Indonesia"), ("ms", "Mã Lai"),
    ("fil", "Philippines"), ("hi", "Hindi"), ("bn", "Bengal"), ("ta", "Tamil"),
    ("te", "Telugu"), ("ml", "Malayalam"), ("kn", "Kannada"), ("gu", "Gujarat"),
    ("mr", "Marathi"), ("ne", "Nepal"), ("si", "Sinhala"), ("ur", "Urdu"),
    ("ar", "Ả Rập"), ("fa", "Ba Tư"), ("he", "Do Thái"), ("tr", "Thổ"),
    ("ru", "Nga"), ("uk", "Ukraina"), ("be", "Belarus"), ("kk", "Kazakh"),
    ("uz", "Uzbek"), ("mn", "Mông Cổ"), ("ka", "Gruzia"), ("hy", "Armenia"),
    ("az", "Azerbaijan"), ("de", "Đức"), ("fr", "Pháp"), ("es", "Tây Ban Nha"),
    ("pt", "Bồ Đào Nha"), ("it", "Ý"), ("nl", "Hà Lan"), ("pl", "Ba Lan"),
    ("cs", "Séc"), ("sk", "Slovak"), ("hu", "Hungary"), ("ro", "Rumani"),
    ("bg", "Bulgaria"), ("sr", "Serbia"), ("hr", "Croatia"), ("sl", "Slovenia"),
    ("el", "Hy Lạp"), ("sv", "Thụy Điển"), ("no", "Na Uy"), ("da", "Đan Mạch"),
    ("fi", "Phần Lan"), ("is", "Iceland"), ("et", "Estonia"), ("lv", "Latvia"),
    ("lt", "Litva"), ("sw", "Swahili"), ("af", "Afrikaans"), ("sq", "Albania"),
    ("mk", "Macedonia"), ("bs", "Bosnia"), ("ca", "Catalan"), ("eu", "Basque"),
    ("gl", "Galicia"), ("mt", "Malta"), ("ga", "Ireland"), ("cy", "Wales"),
    ("am", "Amharic"), ("yo", "Yoruba"), ("ig", "Igbo"), ("ha", "Hausa"),
    ("zu", "Zulu"), ("so", "Somali"), ("eo", "Esperanto"),
]
NAME = {code: label for code, label in LANGS}
EXTRA_NAME = {
    "auto": "Tự nhận", "zh": "Trung", "pt-BR": "Bồ Đào Nha (BR)", "en-US": "Anh (Mỹ)",
    "en-GB": "Anh (Anh)", "iw": "Do Thái", "jw": "Java", "tl": "Philippines",
    "nb": "Na Uy", "nn": "Na Uy",
}


def lang_name(code: str) -> str:
    if not code:
        return "?"
    if code in NAME:
        return NAME[code]
    base = code.split("-")[0]
    return NAME.get(base) or EXTRA_NAME.get(code) or EXTRA_NAME.get(base) or code



# ─────────────────────────────────────────────── icon riêng cho từng ngôn ngữ
# Mỗi ngôn ngữ 1 icon premium. Cờ nào chưa có id premium thì dùng icon địa lý /
# văn hoá gần nhất (v20) — KHÔNG bao giờ để trống, KHÔNG bao giờ lộ emoji thường.
# Muốn thay bằng cờ thật: thêm mã vào data/premium_flags.json (xem chú thích cuối).
_WANT_FLAG = {
    "vi": "🇻🇳", "en": "🇬🇧", "zh-CN": "🇨🇳", "zh-TW": "🇹🇼", "ja": "🇯🇵", "ko": "🇰🇷",
    "th": "🇹🇭", "lo": "🇱🇦", "km": "🇰🇭", "my": "🇲🇲", "id": "🇮🇩", "ms": "🇲🇾",
    "fil": "🇵🇭", "hi": "🇮🇳", "bn": "🇧🇩", "ta": "🇮🇳", "te": "🇮🇳", "ml": "🇮🇳",
    "kn": "🇮🇳", "gu": "🇮🇳", "mr": "🇮🇳", "ne": "🇳🇵", "si": "🇱🇰", "ur": "🇵🇰",
    "ar": "🇸🇦", "fa": "🇮🇷", "he": "🇮🇱", "tr": "🇹🇷", "ru": "🇷🇺", "uk": "🇺🇦",
    "be": "🇧🇾", "kk": "🇰🇿", "uz": "🇺🇿", "mn": "🇲🇳", "ka": "🇬🇪", "hy": "🇦🇲",
    "az": "🇦🇿", "de": "🇩🇪", "fr": "🇫🇷", "es": "🇪🇸", "pt": "🇵🇹", "it": "🇮🇹",
    "nl": "🇳🇱", "pl": "🇵🇱", "cs": "🇨🇿", "sk": "🇸🇰", "hu": "🇭🇺", "ro": "🇷🇴",
    "bg": "🇧🇬", "sr": "🇷🇸", "hr": "🇭🇷", "sl": "🇸🇮", "el": "🇬🇷", "sv": "🇸🇪",
    "no": "🇳🇴", "da": "🇩🇰", "fi": "🇫🇮", "is": "🇮🇸", "et": "🇪🇪", "lv": "🇱🇻",
    "lt": "🇱🇹", "sw": "🇰🇪", "af": "🇿🇦", "sq": "🇦🇱", "mk": "🇲🇰", "bs": "🇧🇦",
    "ca": "🇪🇸", "eu": "🇪🇸", "gl": "🇪🇸", "mt": "🇲🇹", "ga": "🇮🇪", "cy": "🇬🇧",
    "am": "🇪🇹", "yo": "🇳🇬", "ig": "🇳🇬", "ha": "🇳🇬", "zu": "🇿🇦", "so": "🇸🇴",
    "eo": "🌐",
}

# v20: cờ chưa có id premium -> icon premium thay thế theo khu vực (đều CÓ icon)
_FLAG_ALT = {
    # Đông Nam Á
    "lo": "🌏", "km": "🌏", "my": "🌏", "ms": "🌏",
    # Nam Á
    "hi": "🌏", "bn": "🌏", "ta": "🌏", "te": "🌏", "ml": "🌏", "kn": "🌏",
    "gu": "🌏", "mr": "🌏", "ne": "🌏", "si": "🌏", "ur": "🌏",
    # Trung Đông
    "ar": "☪️", "fa": "☪️",
    # châu Phi
    "sw": "🌍", "af": "🌍", "am": "🌍", "yo": "🌍", "ig": "🌍", "ha": "🌍",
    "zu": "🌍", "so": "🌍",
    # châu Âu (dùng cờ EU cho các nước chưa có id riêng)
    "fi": "🇪🇺", "hr": "🇪🇺", "hu": "🇪🇺", "lt": "🇪🇺", "mk": "🇪🇺",
    "mt": "🇪🇺", "rs": "🇪🇺", "si": "🇪🇺", "et": "🇪🇺", "sq": "🇪🇺", "bs": "🇪🇺",
    # Việt Nam: cờ chưa có id -> ngôi sao đỏ (đúng tinh thần cờ VN)
    "vi": "⭐",
}


def _custom_flags() -> dict[str, str]:
    """data/premium_flags.json: {"vi": "🇻🇳"} hoặc {"vi": "5042…"} để thay icon.

    Chủ bot dán id cờ thật (lấy bằng /js từ bộ emoji của mình) là bot dùng ngay,
    không phải sửa code. Sai định dạng -> bỏ qua, không làm chết bot.
    """
    try:
        raw = json.loads((Path(config.DATA_DIR) / "premium_flags.json").read_text("utf-8"))
        if isinstance(raw, dict):
            return {str(k): str(v) for k, v in raw.items() if isinstance(v, (str, int))}
    except Exception:  # noqa: BLE001
        pass
    return {}


_CUSTOM = _custom_flags()
# id dán tay cũng phải được probe kiểm tra (id hỏng -> tự bỏ, không làm chết nút)
premium.register_ids(*[v for v in _CUSTOM.values() if v.isdigit()])


def _usable(icon: str) -> str | None:
    """Chuỗi icon dùng được: emoji có id premium, hoặc chính id số (custom)."""
    ic = (icon or "").strip()
    if not ic:
        return None
    if ic.isdigit():
        return ic          # id thô -> buttons dùng trực tiếp
    if ic in premium.RAW or premium.id_of(ic):
        return ic
    return None


def lang_icon(code: str) -> str:
    """Icon cho 1 ngôn ngữ: cờ thật nếu có id premium, không thì icon khu vực."""
    code = code or ""
    base = code.split("-")[0]
    # 1) chủ bot tự nạp cờ -> ưu tiên tuyệt đối
    for key in (code, base):
        got = _usable(_CUSTOM.get(key, ""))
        if got:
            return got
    # 2) cờ có id premium thật
    want = _WANT_FLAG.get(code) or _WANT_FLAG.get(base) or ""
    got = _usable(want)
    if got:
        return got
    # 3) icon khu vực (vẫn là premium) -> không bao giờ trống
    alt = _FLAG_ALT.get(base) or _FLAG_ALT.get(code)
    got = _usable(alt) if alt else None
    if got:
        return got
    # 4) chốt: icon premium chắc chắn có
    for cand in ("🌐", "🌏", "📄"):
        got = _usable(cand)
        if got:
            return got
    return "🌐"


# ─────────────────────────────────────────────── lưu trữ nhỏ
def _load() -> dict:
    try:
        data = json.loads(STORE.read_text("utf-8"))
        if isinstance(data, dict):
            data.setdefault("pref", {})
            data.setdefault("items", {})
            return data
    except Exception:  # noqa: BLE001
        pass
    return {"pref": {}, "items": {}}


_DB = _load()


def _save() -> None:
    try:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        # chỉ giữ 300 bản dịch gần nhất để file không phình
        items = _DB.get("items", {})
        if len(items) > 300:
            keep = list(items.items())[-300:]
            _DB["items"] = dict(keep)
        STORE.write_text(json.dumps(_DB, ensure_ascii=False), "utf-8")
    except Exception as e:  # noqa: BLE001
        log.info("không ghi được translate.json: %s", str(e)[:80])


def pref(uid: int) -> str:
    return (_DB.get("pref") or {}).get(str(uid)) or DEFAULT_TARGET


def set_pref(uid: int, code: str) -> None:
    _DB.setdefault("pref", {})[str(uid)] = code
    _save()


def remember(text: str, src: str = "") -> str:
    token = hashlib.sha1((src + "\x00" + text).encode("utf-8")).hexdigest()[:12]
    _DB.setdefault("items", {})[token] = {"t": text[:MAX_TEXT], "s": src}
    _save()
    return token


def recall(token: str) -> dict | None:
    item = (_DB.get("items") or {}).get(token)
    if isinstance(item, dict) and item.get("t"):
        return item
    return None


# ─────────────────────────────────────────────── gửi tin (premium 100%)
async def _send(fn, text: str, **kwargs):
    """Gửi kèm icon premium; Telegram từ chối thì tự lùi về emoji thường."""
    kwargs.setdefault("parse_mode", "HTML")
    try:
        return await fn(premium.ensure(text), **kwargs)
    except BadRequest as e:
        err = str(e).lower()
        if "message is not modified" in err:
            return None
        if "custom_emoji" in err or "custom emoji" in err:
            premium.reset()
            return await fn(premium.strip(text), **kwargs)
        if "can't parse" in err or "parse entities" in err:
            kwargs.pop("parse_mode", None)
            return await fn(premium.plain_all(text), **kwargs)
        log.info("dich: gửi lỗi %s", str(e)[:80])
        return None
    except Exception as e:  # noqa: BLE001
        log.info("dich: gửi lỗi %s", str(e)[:80])
        return None


# ─────────────────────────────────────────────── cache bản dịch
_CACHE: dict[tuple, tuple] = {}
_CACHE_TTL = 7200
_CACHE_MAX = 400


def _ckey(text: str, target: str, source: str) -> tuple:
    return (hashlib.sha1(text.encode("utf-8")).hexdigest(), target, source)


def _cache_get(text: str, target: str, source: str):
    item = _CACHE.get(_ckey(text, target, source))
    if item and time.time() - item[2] < _CACHE_TTL:
        return item[0], item[1]
    return None


def _cache_put(text: str, target: str, source: str, body: str, det: str) -> None:
    if not body:
        return
    if len(_CACHE) > _CACHE_MAX:
        for k in list(_CACHE)[:80]:
            _CACHE.pop(k, None)
    _CACHE[_ckey(text, target, source)] = (body, det, time.time())


# ─────────────────────────────────────────────── máy dịch
def _chunks(text: str, size: int = 1600) -> list[str]:
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    out, buf = [], ""
    for piece in re.split(r"(?<=[.!?。！？\n])\s*", text):
        if not piece:
            continue
        while len(piece) > size:                 # câu quá dài -> cắt cứng
            if buf:
                out.append(buf)
                buf = ""
            out.append(piece[:size])
            piece = piece[size:]
        if len(buf) + len(piece) + 1 > size and buf:
            out.append(buf)
            buf = piece
        else:
            buf = (buf + " " + piece).strip()
    if buf:
        out.append(buf)
    return out


async def _google(chunk: str, target: str, source: str = "auto") -> tuple[str, str]:
    url = (
        "https://translate.googleapis.com/translate_a/single"
        f"?client=gtx&sl={urllib.parse.quote(source)}&tl={urllib.parse.quote(target)}"
        f"&dt=t&ie=UTF-8&oe=UTF-8&q={urllib.parse.quote(chunk)}"
    )
    data = await http.fetch_json(url, timeout=16, retries=1)
    if isinstance(data, list) and data and isinstance(data[0], list):
        out = "".join(part[0] for part in data[0] if part and part[0])
        detected = data[2] if len(data) > 2 and isinstance(data[2], str) else ""
        if out.strip():
            return out, detected
    return "", ""


# Bing (máy dịch của Microsoft) — nguồn dự phòng chắc chắn, tự nhận ngôn ngữ.
# Dùng endpoint công khai của trang bing.com/translator: lấy IG + key/token trong HTML
# rồi POST ttranslatev3. Token sống ~1 giờ -> tự làm mới sau 25 phút hoặc khi bị 4xx.
_BING_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
_BING = {"ig": "", "iid": "translator.5023", "key": "", "token": "", "ts": 0.0, "uses": 0}
_BING_CODE = {"zh": "zh-Hans", "zh-CN": "zh-Hans", "zh-TW": "zh-Hant",
              "zh-HK": "zh-Hant", "no": "nb", "fil": "fil"}


async def _bing_prep(force: bool = False) -> bool:
    now = time.time()
    if (not force and _BING["key"] and _BING["token"]
            and now - _BING["ts"] < 1500 and _BING["uses"] < 18):
        return True
    page = await http.fetch_text(
        "https://www.bing.com/translator?from=auto&to=vi",
        timeout=20, retries=1, headers={"User-Agent": _BING_UA},
    )
    if not page:
        return False
    m_ig = re.search(r'IG:"([^"]+)"', page)
    m_p = re.search(r"params_AbusePreventionHelper\s*=\s*\[([^\]]+)\]", page)
    m_iid = re.search(r'data-iid="(translator\.\d+)"', page)
    if not (m_ig and m_p):
        return False
    parts = [p.strip().strip('"') for p in m_p.group(1).split(",")]
    if len(parts) < 2:
        return False
    _BING.update({
        "ig": m_ig.group(1),
        "iid": (m_iid.group(1) if m_iid else "translator.5023"),
        "key": parts[0], "token": parts[1],
        "ts": now, "uses": 0,
    })
    return True


async def _bing(chunk: str, target: str, source: str = "auto") -> tuple[str, str]:
    if not await _bing_prep():
        return "", ""
    body = {
        "fromLang": source if source and source != "auto" else "auto-detect",
        "text": chunk,
        "to": _BING_CODE.get(target, target),
        "token": _BING["token"],
        "key": _BING["key"],
    }
    url = (
        "https://www.bing.com/ttranslatev3?isVertical=1&&"
        f"IG={urllib.parse.quote(_BING['ig'])}&IID={urllib.parse.quote(_BING['iid'])}.1"
    )
    status, raw, _ = await http.fetch(
        url, method="POST", data=body, timeout=20, retries=0,
        headers={"User-Agent": _BING_UA, "Content-Type": "application/x-www-form-urlencoded",
                 "Referer": "https://www.bing.com/translator"},
    )
    _BING["uses"] += 1
    if status != 200 or not raw:
        await _bing_prep(force=True)
        return "", ""
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return "", ""
    if not isinstance(data, list) or not data:
        return "", ""
    first = data[0] if isinstance(data[0], dict) else {}
    trans = (first.get("translations") or [{}])[0]
    out = trans.get("text") or ""
    det = ((first.get("detectedLanguage") or {}).get("language") or "")
    if out.strip():
        if det == "zh-Hans":
            det = "zh-CN"
        elif det == "zh-Hant":
            det = "zh-TW"
        return out, det
    return "", ""


async def _mymemory(chunk: str, target: str, source: str) -> tuple[str, str]:
    pair = f"{source or 'en'}|{target}"
    url = (
        "https://api.mymemory.translated.net/get"
        f"?q={urllib.parse.quote(chunk[:500])}&langpair={urllib.parse.quote(pair)}&de=bot@khoaan.dev"
    )
    data = await http.fetch_json(url, timeout=16, retries=0)
    if data and isinstance(data.get("responseData"), dict):
        out = data["responseData"].get("translatedText") or ""
        if out and "QUERY LENGTH LIMIT" not in out.upper() and "MYMEMORY WARNING" not in out.upper():
            return html.unescape(out), source
    return "", ""


async def translate(text: str, target: str, source: str = "auto") -> tuple[str, str]:
    """Trả về (bản dịch, ngôn ngữ gốc nhận được). Lỗi mạng -> ("", "") giữ nguyên. """
    text = (text or "").strip()
    if not text:
        return "", ""
    hit = _cache_get(text, target, source)
    if hit:
        return hit
    pieces = _chunks(text)
    results: list[tuple[str, str]] = []
    for chunk in pieces:
        out, detected = await _google(chunk, target, source)
        if not out:
            out, detected = await _bing(chunk, target, source)
        if not out:
            for src in ([source] if source != "auto" else []) + ["en", "vi"]:
                out, detected = await _mymemory(chunk, target, src)
                if out:
                    break
        results.append((out, detected))
        if not out:
            break
    if not any(r[0] for r in results):
        return "", results[0][1] if results else ""
    detected = next((r[1] for r in results if r[1]), source if source != "auto" else "")
    body = "\n".join(r[0] for r in results)
    body = body.strip()
    _cache_put(text, target, source, body, detected)
    return body, detected


# ─────────────────────────────────────────────── bàn phím chọn ngôn ngữ
def lang_kb(token: str, current: str, page: int = 0) -> "B.InlineKeyboardMarkup":
    pages = max(1, (len(LANGS) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    slice_ = LANGS[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    rows, row = [], []
    for code, label in slice_:
        mark = (code == current)
        row.append(B.btn(
            label,
            cb=f"tr:set:{token}:{code}",
            style=B.GREEN if mark else B.BLUE,
            icon="✅" if mark else lang_icon(code),
        ))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    nav = []
    if page > 0:
        nav.append(B.btn("Trước", cb=f"tr:pg:{token}:{page - 1}", style=B.BLUE, icon="⬅️"))
    nav.append(B.btn(f"{page + 1}/{pages}", cb=f"tr:noop:{token}", style=B.BLUE, icon="📄"))
    if page < pages - 1:
        nav.append(B.btn("Sau", cb=f"tr:pg:{token}:{page + 1}", style=B.BLUE, icon="➡️"))
    rows.append(nav)
    rows.append([B.btn("Đóng", cb=f"tr:x:{token}", style=B.RED, icon="✖️")])
    return B.kb(rows)


def mini_kb(token: str) -> "B.InlineKeyboardMarkup":
    """v20: thanh 1 nút dưới bản dịch — bấm mới mở bảng chọn ngôn ngữ.

    Yêu cầu của chủ bot: reply "." là bot dịch luôn, KHÔNG bày bảng 15 nút ra;
    chỉ khi người dùng muốn đổi ngôn ngữ thì mới hiện lựa chọn.
    """
    return B.kb([B.row(B.btn("Đổi ngôn ngữ", cb=f"tr:open:{token}", style=B.BLUE, icon="🌐"))])


def render(item: dict, src: str, tgt: str, body: str) -> str:
    return (
        "🌐 <b>DỊCH TỰ ĐỘNG</b>\n"
        f"<i>Ngôn ngữ gốc: {html.escape(lang_name(src) if src else 'không rõ')}"
        f" → {html.escape(lang_name(tgt))}</i>\n"
        "────────────────────\n"
        f"{html.escape(body)}"
    )


async def _no_text_msg() -> str:
    return "❌ Tin bạn reply không có chữ để dịch."



PANEL_TEXT = (
    "🧭 <b>DỊCH ĐA NGÔN NGỮ</b>\n"
    "──────────────\n"
    "Cách dùng nhanh:\n"
    "• Reply tin nhắn bất kỳ rồi gửi dấu <b>.</b> — bot dịch luôn vào ngôn ngữ đang chọn\n"
    "• <code>/dich nội dung</code> — dịch ngay, không cần reply\n"
    "• Bản dịch chỉ có 1 nút <b>Đổi ngôn ngữ</b> — bấm mới mở bảng chọn (không làm rối chat)\n"
    "──────────────\n"
    f"🌐 Ngôn ngữ đích hiện tại: <b>{{lang}}</b>\n"
    f"🗣 Có <b>{{total}}</b> ngôn ngữ — bấm nút bên dưới để đổi, mọi nút đều có icon riêng."
)


def panel_text(uid: int) -> str:
    return PANEL_TEXT.format(lang=lang_name(pref(uid)), total=len(LANGS))


async def panel(update, context) -> None:
    """Mở bảng chọn ngôn ngữ đích (nút menu 'Dịch')."""
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    await _send(msg.reply_text, panel_text(user.id),
                reply_markup=lang_kb("home", pref(user.id), 0))


async def on_dot_reply(update, context) -> bool:
    """Admin reply tin nhắn + gửi "." -> dịch. Trả về True nếu đã xử lý."""
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return False
    rep = msg.reply_to_message
    if not rep:
        await _send(msg.reply_text, await _no_text_msg())
        return True
    text = (rep.text or rep.caption or "").strip()
    if not text:
        await _send(msg.reply_text, await _no_text_msg())
        return True

    target = pref(user.id)
    body, detected = await translate(text, target)
    if not body:
        await _send(
            msg.reply_text,
            "❌ Không dịch được lúc này (máy dịch đang bận). Thử lại sau vài giây nhé.",
        )
        return True
    # tin gốc đã đúng ngôn ngữ đích -> đổi hướng cho khỏi ra kết quả trùng
    if detected and detected.split("-")[0] == target.split("-")[0]:
        alt = "en" if not target.startswith("en") else "vi"
        body2, detected = await translate(text, alt)
        if body2:
            body, target = body2, alt
    token = remember(text, detected)
    try:
        await msg.delete()
    except Exception:  # noqa: BLE001
        pass
    # v20: dịch tự động -> chỉ gửi bản dịch + 1 nút đổi ngôn ngữ (không bày bảng)
    await _send(rep.reply_text, render({"t": text}, detected, target, body),
                reply_markup=mini_kb(token))
    return True


async def cb_translate(update, context) -> None:
    """Bấm nút trong bảng dịch: mở bảng / đổi ngôn ngữ / chuyển trang / đóng.

    Callback v20: tr:set:<token>:<code> · tr:pg:<token>:<page> · tr:open:<token>
    · tr:x:<token> · tr:noop:<x>. Vẫn nhận callback cũ (tr:<token>:<code>,
    tr:p:<token>:<page>) để tin nhắn cũ còn trên chat vẫn bấm được.
    """
    q = update.callback_query
    if not q:
        return
    parts = (q.data or "").split(":")
    action = parts[1] if len(parts) > 1 else ""
    token = parts[2] if len(parts) > 2 else ""
    arg = parts[3] if len(parts) > 3 else ""

    async def answer(text: str = "", alert: bool = False) -> None:
        try:
            await q.answer(text, show_alert=alert)
        except Exception:  # noqa: BLE001
            pass

    # --- callback cũ: tr:<token>:<code> (token là hash 12 ký tự) ---
    if action not in ("set", "pg", "open", "x", "noop", "home", "p", "t") and len(action) == 12:
        action, token, arg = "set", action, token

    # ---------- mở bảng chọn ngôn ngữ (nút "Đổi ngôn ngữ") ----------
    if action == "open":
        try:
            await q.message.edit_reply_markup(
                reply_markup=lang_kb(token, pref(q.from_user.id), 0)
            )
        except Exception as e:  # noqa: BLE001
            log.info("mở bảng ngôn ngữ lỗi: %s", str(e)[:80])
        await answer()
        return

    # ---------- đổi ngôn ngữ (panel trang chủ: token = "home") ----------
    if action == "home" or (action == "set" and token == "home"):
        code = (token if action == "home" else arg) or DEFAULT_TARGET
        set_pref(q.from_user.id, code)
        await answer(f"Đã chọn {lang_name(code)}")
        try:
            await _send(q.message.edit_text, panel_text(q.from_user.id),
                        reply_markup=lang_kb("home", code, 0))
        except Exception as e:  # noqa: BLE001
            log.info("đổi ngôn ngữ mặc định lỗi: %s", str(e)[:80])
        return

    if action == "noop":
        await answer()
        return

    item = recall(token)
    if not item:
        await answer("Bản dịch này đã hết hạn, reply \".\" lại nhé.", alert=True)
        return
    text = item["t"]
    src = item.get("s") or ""

    if action == "x":
        await answer("Đã đóng")
        try:
            await q.message.edit_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001
            pass
        return

    if action in ("p", "pg"):
        try:
            page = int(arg or 0)
        except ValueError:
            page = 0
        try:
            await q.message.edit_reply_markup(
                reply_markup=lang_kb(token, pref(q.from_user.id), page)
            )
        except Exception as e:  # noqa: BLE001
            log.info("đổi trang dịch lỗi: %s", str(e)[:80])
        await answer()
        return

    # ---------- dịch lại sang ngôn ngữ vừa chọn ----------
    if action in ("set", "t") or (action and arg):
        target = arg or DEFAULT_TARGET
        body, detected = await translate(text, target, src or "auto")
        if not body:
            await answer("Không dịch được lúc này, thử lại nhé.", alert=True)
            return
        set_pref(q.from_user.id, target)
        await answer(f"Đã dịch sang {lang_name(target)}")
        try:
            await _send(q.message.edit_text,
                        render(item, detected or src, target, body),
                        reply_markup=lang_kb(token, target, 0))
        except Exception as e:  # noqa: BLE001
            log.info("sửa tin dịch lỗi: %s", str(e)[:80])
        return


async def cmd_translate(update, context) -> None:
    """/dich <chữ> — dịch nhanh không cần reply."""
    msg = update.effective_message
    user = update.effective_user
    if not msg:
        return
    text = " ".join((msg.text or "").split()[1:]).strip()
    if not text:
        await _send(
            msg.reply_text,
            "🧭 <b>DỊCH TIN NHẮN</b>\n"
            "• Reply một tin rồi gửi dấu \".\" — bot tự nhận ngôn ngữ và dịch\n"
            "• <code>/dich nội dung</code> — dịch nhanh\n"
            f"🌐 Ngôn ngữ đích: <b>{lang_name(pref(user.id))}</b>",
            reply_markup=lang_kb("home", pref(user.id), 0),
        )
        return
    target = pref(user.id)
    body, detected = await translate(text, target)
    if not body:
        await _send(msg.reply_text, "❌ Không dịch được lúc này, thử lại sau vài giây.")
        return
    token = remember(text, detected)
    await _send(msg.reply_text, render({"t": text}, detected, target, body),
                reply_markup=mini_kb(token))

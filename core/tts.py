#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Đổi chữ thành giọng nói — nhiều "giọng AI" thay vì chỉ 2 giọng như trước.

Nguồn giọng:
• edge      — Microsoft neural (hơn 300 giọng, 40+ ngôn ngữ). Mặc định, miễn phí.
• capcut    — giọng của CapCut/TikTok (BV074 · BV075 …). Endpoint không chính thức,
              lúc sống lúc chết → tự lùi về giọng edge cùng ngôn ngữ, không báo lỗi.
• gtts      — Google Translate (1 giọng/ngôn ngữ), chỉ dùng khi 2 nguồn trên hỏng.

Người dùng đổi giọng bằng /voice (có nút + phân trang), tốc độ đọc chỉnh được
chậm / vừa / nhanh.
"""

import asyncio
import io
import logging
import re

from config import TMP_DIR
from core import pool

log = logging.getLogger("khoaan.tts")


class Voice:
    __slots__ = ("key", "engine", "vid", "desc", "lang", "group")

    def __init__(self, key, engine, vid, desc, lang, group):
        self.key = key
        self.engine = engine
        self.vid = vid
        self.desc = desc
        self.lang = lang
        self.group = group

    def __iter__(self):                     # cho code cũ dùng VOICES[k][1]
        return iter((self.engine, self.vid, self.desc, self.lang, self.group))


# ─────────────────────────────────────────────── danh mục giọng
_CATALOG = [
    # key        engine    voice id                        mô tả                    lang  nhóm
    ("nam",      "edge", "vi-VN-NamMinhNeural",            "Nam · trầm ấm",          "vi", "vi"),
    ("nu",       "edge", "vi-VN-HoaiMyNeural",             "Nữ · mềm mại",           "vi", "vi"),
    ("nam_tre",  "edge", "vi-VN-NamMinhNeural",            "Nam · kể chuyện",        "vi", "vi"),
    ("nu_tre",   "edge", "vi-VN-HoaiMyNeural",             "Nữ · đọc tin",           "vi", "vi"),
    ("cc_nu",    "capcut", "BV074_streaming",              "Nữ · CapCut AI",         "vi", "vi"),
    ("cc_nam",   "capcut", "BV075_streaming",              "Nam · CapCut AI",        "vi", "vi"),
    ("ai_nam",   "edge", "en-US-AndrewMultilingualNeural", "AI nam · đa ngữ",        "vi", "ai"),
    ("ai_nu",    "edge", "en-US-AvaMultilingualNeural",    "AI nữ · đa ngữ",         "vi", "ai"),
    ("ai_nu2",   "edge", "en-US-EmmaMultilingualNeural",   "AI nữ 2 · đa ngữ",       "vi", "ai"),
    ("ai_nam2",  "edge", "en-US-BrianMultilingualNeural",  "AI nam 2 · đa ngữ",      "vi", "ai"),
    ("en",       "edge", "en-US-AriaNeural",               "English · nữ",           "en", "en"),
    ("en_nam",   "edge", "en-US-GuyNeural",                "English · nam",          "en", "en"),
    ("en_jenny", "edge", "en-US-JennyNeural",              "English · Jenny",        "en", "en"),
    ("en_uk",    "edge", "en-GB-LibbyNeural",              "English UK · nữ",        "en", "en"),
    ("en_au",    "edge", "en-AU-NatashaNeural",            "English Úc · nữ",        "en", "en"),
    ("en_in",    "edge", "en-IN-NeerjaNeural",             "English Ấn · nữ",        "en", "en"),
    ("zh",       "edge", "zh-CN-XiaoxiaoNeural",           "Trung · nữ",             "zh", "zh"),
    ("zh_nam",   "edge", "zh-CN-YunxiNeural",              "Trung · nam",            "zh", "zh"),
    ("zh_nu2",   "edge", "zh-CN-XiaoyiNeural",             "Trung · nữ trẻ",         "zh", "zh"),
    ("zh_tw",    "edge", "zh-TW-HsiaoChenNeural",          "Đài Loan · nữ",          "zh", "zh"),
    ("zh_yue",   "edge", "zh-HK-HiuGaaiNeural",            "Quảng Đông · nữ",        "zh", "zh"),
    ("ja",       "edge", "ja-JP-NanamiNeural",             "Nhật · nữ",              "ja", "ja_ko"),
    ("ja_nam",   "edge", "ja-JP-KeitaNeural",              "Nhật · nam",             "ja", "ja_ko"),
    ("ko",       "edge", "ko-KR-SunHiNeural",              "Hàn · nữ",               "ko", "ja_ko"),
    ("ko_nam",   "edge", "ko-KR-InJoonNeural",             "Hàn · nam",              "ko", "ja_ko"),
    ("th",       "edge", "th-TH-PremwadeeNeural",          "Thái · nữ",              "th", "th_id"),
    ("th_nam",   "edge", "th-TH-NiwatNeural",              "Thái · nam",             "th", "th_id"),
    ("id",       "edge", "id-ID-GadisNeural",              "Indonesia · nữ",         "id", "th_id"),
    ("id_nam",   "edge", "id-ID-ArdiNeural",               "Indonesia · nam",        "id", "th_id"),
    ("fr",       "edge", "fr-FR-DeniseNeural",             "Pháp · nữ",              "fr", "eu"),
    ("fr_nam",   "edge", "fr-FR-HenriNeural",              "Pháp · nam",             "fr", "eu"),
    ("de",       "edge", "de-DE-KatjaNeural",              "Đức · nữ",               "de", "eu"),
    ("de_nam",   "edge", "de-DE-ConradNeural",             "Đức · nam",              "de", "eu"),
    ("es",       "edge", "es-ES-ElviraNeural",             "Tây Ban Nha · nữ",       "es", "eu"),
    ("es_nam",   "edge", "es-ES-AlvaroNeural",             "Tây Ban Nha · nam",      "es", "eu"),
    ("ru",       "edge", "ru-RU-SvetlanaNeural",           "Nga · nữ",               "ru", "eu"),
    ("ru_nam",   "edge", "ru-RU-DmitryNeural",             "Nga · nam",              "ru", "eu"),
    ("hi",       "edge", "hi-IN-SwaraNeural",              "Hindi · nữ",             "hi", "eu"),
    ("hi_nam",   "edge", "hi-IN-MadhurNeural",             "Hindi · nam",            "hi", "eu"),
    ("pt",       "edge", "pt-BR-FranciscaNeural",          "Bồ Đào Nha · nữ",        "pt", "eu"),
    ("pt_nam",   "edge", "pt-BR-AntonioNeural",            "Bồ Đào Nha · nam",       "pt", "eu"),
]

VOICES: dict[str, Voice] = {
    key: Voice(key, engine, vid, desc, lang, group)
    for key, engine, vid, desc, lang, group in _CATALOG
}

GROUPS = [
    ("vi", "Tiếng Việt"),
    ("ai", "🤖 AI đa ngữ"),
    ("en", "English"),
    ("zh", "Trung"),
    ("ja_ko", "Nhật · Hàn"),
    ("th_id", "Thái · Indo"),
    ("eu", "Châu Âu"),
]

SPEEDS = {"cham": ("-20%", "Chậm"), "vua": ("+0%", "Vừa"), "nhanh": ("+25%", "Nhanh")}

DEFAULT_VOICE = "nam"
DEFAULT_SPEED = "vua"


def resolve(key: str) -> Voice:
    """Lấy Voice từ key (hỗ trợ cả key động 'edge:vi-VN-…' lấy từ danh sách đầy đủ)."""
    key = (key or "").strip()
    if key in VOICES:
        return VOICES[key]
    if key.startswith("edge:") and len(key) > 6:
        vid = key[5:]
        lang = vid.split("-")[0].lower() if "-" in vid else "en"
        return Voice(key, "edge", vid, vid, lang, "all")
    return VOICES[DEFAULT_VOICE]


def desc_of(key: str) -> str:
    return resolve(key).desc


def group_voices(group: str) -> list[Voice]:
    return [v for v in VOICES.values() if v.group == group]


# ─────────────────────────────────────────────── xử lý text
def _has(pred, text: str) -> bool:
    return any(pred(ch) for ch in text)


def auto_voice(text: str, current: str = DEFAULT_VOICE) -> str:
    """Đoán giọng theo nội dung nếu người dùng chưa chọn."""
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    kana = sum(1 for c in text if "\u3040" <= c <= "\u30ff")
    hangul = sum(1 for c in text if "\uac00" <= c <= "\ud7af")
    thai = sum(1 for c in text if "\u0e00" <= c <= "\u0e7f")
    total = max(len(text.strip()), 1)
    if kana / total > 0.15:
        return "ja"
    if hangul / total > 0.15:
        return "ko"
    if thai / total > 0.15:
        return "th"
    if cjk / total > 0.2:
        return "zh"
    return current


def clean_for_tts(text: str) -> str:
    text = re.sub(r"https?://\S+", " ", text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[*_`#>|]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:1200]


# ─────────────────────────────────────────────── engine edge
async def _edge(text: str, voice: str, rate: str = "+0%", pitch: str = "+0Hz") -> bytes | None:
    import edge_tts

    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    buf = bytearray()
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            buf.extend(chunk["data"])
    return bytes(buf) if buf else None


def _gtts(text: str, lang: str) -> bytes | None:
    try:
        from gtts import gTTS

        fp = io.BytesIO()
        gTTS(text=text, lang=lang).write_to_fp(fp)
        return fp.getvalue()
    except Exception as e:  # noqa: BLE001
        log.warning("gTTS fail: %s", e)
        return None


def _fallback_edge_for(v: Voice) -> str:
    """Giọng edge thay thế khi engine capcut chết."""
    if v.lang == "vi":
        return "vi-VN-NamMinhNeural" if any(x in v.vid.lower() for x in ("075", "male", "nam")) else "vi-VN-HoaiMyNeural"
    return {
        "en": "en-US-AriaNeural", "zh": "zh-CN-XiaoxiaoNeural", "ja": "ja-JP-NanamiNeural",
        "ko": "ko-KR-SunHiNeural", "th": "th-TH-PremwadeeNeural", "id": "id-ID-GadisNeural",
        "fr": "fr-FR-DeniseNeural", "de": "de-DE-KatjaNeural", "es": "es-ES-ElviraNeural",
        "ru": "ru-RU-SvetlanaNeural", "hi": "hi-IN-SwaraNeural", "pt": "pt-BR-FranciscaNeural",
    }.get(v.lang, "en-US-AriaNeural")


async def text_to_speech(text: str, voice_key: str = DEFAULT_VOICE,
                         speed: str = DEFAULT_SPEED) -> tuple[bytes | None, str, str]:
    """Trả về (mp3_bytes, voice_key, mô_tả)."""
    text = clean_for_tts(text)
    if not text:
        return None, voice_key, ""

    if voice_key not in VOICES and not voice_key.startswith("edge:"):
        voice_key = auto_voice(text, voice_key)
    v = resolve(voice_key)
    rate = SPEEDS.get(speed, SPEEDS[DEFAULT_SPEED])[0]
    desc = v.desc + ("" if speed == DEFAULT_SPEED else f" · {SPEEDS.get(speed, ('', ''))[1]}")

    if v.engine == "capcut":
        try:
            from core import tts_capcut

            data = await tts_capcut.synth(text, v.vid)
            if data and len(data) > 800:
                return data, voice_key, desc
        except Exception as e:  # noqa: BLE001
            log.info("capcut tts lỗi: %s", str(e)[:90])
        # CapCut chết -> đọc bằng giọng edge cùng ngôn ngữ, không làm hỏng lệnh
        edge_voice = _fallback_edge_for(v)
        desc += " (dự phòng)"
    else:
        edge_voice = v.vid

    for attempt in [edge_voice, "vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"]:
        try:
            data = await _edge(text, attempt, rate=rate)
            if data and len(data) > 800:
                return data, voice_key, desc
        except Exception as e:  # noqa: BLE001
            log.warning("edge-tts fail (%s): %s", attempt, e)

    loop = asyncio.get_running_loop()
    lang = v.lang if v.lang != "vi" else "vi"
    try:
        data = await asyncio.wait_for(pool.run_io(_gtts, text, lang), timeout=18)
    except Exception:  # noqa: BLE001
        data = None
    if data:
        return data, voice_key, desc + " (Google)"
    return None, voice_key, desc


def split_script(text: str, max_len: int = 480) -> list[str]:
    """Chia văn bản dài thành nhiều đoạn cho giọng đọc tự nhiên."""
    text = clean_for_tts(text)
    if len(text) <= max_len:
        return [text] if text else []
    parts, cur = [], ""
    for sentence in re.split(r"(?<=[.!?。！？;])\s+", text):
        if len(cur) + len(sentence) + 1 <= max_len:
            cur = (cur + " " + sentence).strip()
        else:
            if cur:
                parts.append(cur)
            cur = sentence
    if cur:
        parts.append(cur)
    return parts[:6]


_EDGE_LIST: list[dict] | None = None


async def edge_voices(lang_prefix: str = "") -> list[dict]:
    """Danh sách đầy đủ giọng edge (lấy 1 lần, cache RAM)."""
    global _EDGE_LIST
    if _EDGE_LIST is None:
        try:
            import edge_tts

            _EDGE_LIST = list(await asyncio.wait_for(edge_tts.list_voices(), timeout=20))
        except Exception as e:  # noqa: BLE001
            log.info("list_voices lỗi: %s", str(e)[:90])
            _EDGE_LIST = []
    if not lang_prefix:
        return _EDGE_LIST
    return [v for v in _EDGE_LIST if str(v.get("Locale", "")).lower().startswith(lang_prefix.lower())]

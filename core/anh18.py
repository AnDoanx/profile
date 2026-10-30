#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kho ảnh 18+ theo chủ đề (/anh18) — tự tìm ảnh, chia thư mục riêng từng chủ đề.

Nguồn (đều không cần key, đã test thật):
  • eporner API      — ảnh gái thật (thumb video, 540×360)
  • lolicon API      — tranh anime pixiv (proxy i.pixiv.re)
  • nekos.best       — anime SFW dự phòng
  • redgifs API      — video ngắn (mp4 sd) cho /vdgai

Mỗi chủ đề giữ 1 pool 50–70 ảnh trong data/anh18/<chủ đề>/, ảnh trùng bị loại
theo sha1, ảnh đã gửi được đánh dấu để không lặp cho tới khi hết pool.
"""

import asyncio
import hashlib
import io
import json
import logging
import random
import time
from pathlib import Path
from urllib.parse import quote

from config import DATA_DIR

from core import http

log = logging.getLogger("khoaan.anh18")

POOL_DIR = DATA_DIR / "anh18"
POOL_TARGET = 60          # 50–70 ảnh / chủ đề
MAX_SIDE = 1440
MIN_SIDE = 320
DL_CONCURRENCY = 8
MAX_IMG_BYTES = 12 * 1024 * 1024

# ─────────────────────────────────────────────────────── chủ đề
CATS: dict[str, dict] = {
    "mong": {
        "label": "Mông", "btn": "Mông", "emoji": "🍑", "src": "eporner",
        "queries": ["big ass", "booty", "thick ass", "big booty"],
        "video": ["big ass"],
    },
    "girl": {
        "label": "Girl", "btn": "Girl", "emoji": "💃", "src": "eporner",
        "queries": ["hot girl", "sexy girl", "beautiful girl", "amateur girl"],
        "video": ["hot girl"],
    },
    "bu": {
        "label": "Bú", "btn": "Bú", "emoji": "💦", "src": "eporner",
        "queries": ["blowjob", "deepthroat", "sucking dick"],
        "video": ["blowjob"],
    },
    "gainhat": {
        "label": "Gái Nhật", "btn": "Gái Nhật", "emoji": "🌸", "src": "eporner",
        "queries": ["japanese", "japanese amateur", "japanese milf"],
        "video": ["japanese"],
    },
    "japcosplay": {
        "label": "Cosplay", "btn": "Cosplay", "emoji": "🎭", "src": "eporner",
        "queries": ["cosplay", "cosplay sex", "costume"],
        "video": ["cosplay"],
    },
    "tw": {
        "label": "Gái Tài Khựa", "btn": "Tài Khựa", "emoji": "🎀", "src": "eporner",
        "queries": ["chinese", "taiwanese", "chinese amateur", "asian amateur"],
        "video": ["chinese"],
    },
    "anime": {
        "label": "Anime", "btn": "Anime", "emoji": "🌸", "src": "lolicon",
        "r18": True, "keywords": ["", "ecchi", "anime girl"],
    },
    "sagiri": {
        "label": "Sagiri", "btn": "Sagiri", "emoji": "🎀", "src": "lolicon",
        "r18": False,  # ảnh anime SFW (không lấy bản 18+)
        "keywords": ["sagiri", "和泉紗霧", "エロマンガ先生", "sagiri izumi"],
    },
}

ORDER = ["mong", "girl", "bu", "gainhat", "japcosplay", "tw", "anime", "sagiri"]

# nhãn cấm: bỏ ảnh có dấu hiệu nhân vật nhỏ tuổi
BANNED_TAGS = {
    "loli", "lolicon", "shota", "shotacon", "child", "children", "kids", "kid",
    "toddler", "kindergarten", "elementary", "幼女", "萝莉", "羅莉", "ロリ",
    "ショタ", "小学生", "中学生", "child_on_child", "underage",
}


def cat_label(key: str) -> str:
    return (CATS.get(key) or {}).get("label", key)


# ─────────────────────────────────────────────────────── nguồn: eporner
async def _eporner_thumbs(query: str, want: int) -> list[str]:
    url = (
        "https://www.eporner.com/api/v2/video/search/"
        f"?query={quote(query)}&per_page=100&thumbsize=big&format=json"
    )
    data = await http.fetch_json(url, timeout=30)
    if not data or not data.get("videos"):
        return []
    out: list[str] = []
    for v in data["videos"]:
        for t in (v.get("thumbs") or [])[:4]:
            src = t.get("src")
            if src:
                out.append(src)
        d = (v.get("default_thumb") or {}).get("src")
        if d:
            out.append(d)
    random.shuffle(out)
    seen, uniq = set(), []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq[:want]


# ─────────────────────────────────────────────────────── nguồn: lolicon
def _tags_ok(tags: list[str]) -> bool:
    for t in tags or []:
        if str(t).strip().lower() in BANNED_TAGS:
            return False
    return True


_LOLI_LOCK = asyncio.Lock()
_LOLI_LAST = 0.0


async def _loli_gate() -> None:
    """lolicon chỉ cho ~1 request/giây -> xếp hàng, tránh 429."""
    global _LOLI_LAST
    async with _LOLI_LOCK:
        gap = time.time() - _LOLI_LAST
        if gap < 1.4:
            await asyncio.sleep(1.4 - gap)
        _LOLI_LAST = time.time()


async def _lolicon_urls(r18: bool, keyword: str, want: int) -> list[str]:
    """lolicon giới hạn 20 ảnh/lần + 1 request/giây -> gọi nhiều lượt rồi gộp."""
    out: list[str] = []
    rounds = 0
    while len(out) < want and rounds < 5:
        rounds += 1
        params = f"num={min(max(want - len(out), 1), 20)}&size=regular&r18={1 if r18 else 0}&excludeAI=true"
        if keyword:
            params += f"&keyword={quote(keyword)}"
        await _loli_gate()
        data = await http.fetch_json(f"https://api.lolicon.app/setu/v2?{params}", timeout=28)
        if not data:
            continue
        if data.get("error"):            # 429 "quá nhanh" -> nghỉ rồi thử lại
            await asyncio.sleep(2.2)
            continue
        if not data.get("data"):
            break
        for item in data["data"]:
            if not _tags_ok(item.get("tags") or []):
                continue
            urls = item.get("urls") or {}
            u = urls.get("regular") or urls.get("original") or ""
            if u and u not in out:
                out.append(u)
    random.shuffle(out)
    return out


# ─────────────────────────────────────────────────────── nguồn: nekos.best
async def _nekos_urls(want: int) -> list[str]:
    data = await http.fetch_json(f"https://nekos.best/api/v2/neko?amount={min(want, 20)}", timeout=20)
    if not data:
        return []
    return [x["url"] for x in (data.get("results") or []) if x.get("url")]


async def _gather_urls(key: str, want: int) -> list[str]:
    cfg = CATS[key]
    src = cfg.get("src")
    if src == "eporner":
        out: list[str] = []
        for q in cfg["queries"]:
            if len(out) >= want:
                break
            out += await _eporner_thumbs(q, want - len(out))
        random.shuffle(out)
        return out[:want]
    # lolicon (anime / sagiri)
    out: list[str] = []
    r18 = bool(cfg.get("r18"))
    for kw in (cfg.get("keywords") or [cfg.get("keyword", "")]):
        if len(out) >= want:
            break
        out += await _lolicon_urls(r18, kw, want - len(out))
    if len(out) < want:
        for kw in (cfg.get("keywords") or [""]):
            if len(out) >= want:
                break
            out += await _lolicon_urls(False, kw, want - len(out))
    if len(out) < want // 2:
        out += await _nekos_urls(want - len(out))
    random.shuffle(out)
    return out[:want]


# ─────────────────────────────────────────────────────── tải + chuẩn hoá
async def _fetch_image(url: str, sem: asyncio.Semaphore) -> bytes | None:
    from PIL import Image

    async with sem:
        raw = await http.download_bytes(
            url,
            max_bytes=MAX_IMG_BYTES,
            headers={"Referer": "https://www.eporner.com/", "Accept": "image/*,*/*"},
            timeout=35,
        )
    if not raw or len(raw) < 4000:
        return None
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
        if im.width < MIN_SIDE or im.height < MIN_SIDE:
            return None
        if getattr(im, "n_frames", 1) > 60:      # bỏ gif động quá dài
            return None
        im = im.convert("RGB")
        im.thumbnail((MAX_SIDE, MAX_SIDE))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=88, optimize=True)
        return out.getvalue()
    except Exception:  # noqa: BLE001
        return None


# ─────────────────────────────────────────────────────── pool
def _dir(key: str) -> Path:
    d = POOL_DIR / key
    d.mkdir(parents=True, exist_ok=True)
    return d


def count(key: str) -> int:
    try:
        return sum(1 for _ in _dir(key).glob("*.jpg"))
    except Exception:  # noqa: BLE001
        return 0


async def ensure_pool(key: str, target: int = POOL_TARGET) -> int:
    """Bảo đảm thư mục chủ đề có ~target ảnh. Trả về số ảnh hiện có."""
    if key not in CATS:
        return 0
    d = _dir(key)
    have = count(key)
    if have >= target:
        return have

    urls = await _gather_urls(key, (target - have) + 15)
    if not urls:
        return have

    sem = asyncio.Semaphore(DL_CONCURRENCY)
    seen = {p.stem for p in d.glob("*.jpg")}    # tên tệp = sha1
    got = 0
    tasks = [asyncio.create_task(_fetch_image(u, sem)) for u in urls]

    try:
        for fut in asyncio.as_completed(tasks):
            if have + got >= target:
                break
            data = await fut
            if not data:
                continue
            sha = hashlib.sha1(data).hexdigest()
            if sha in seen:
                continue
            seen.add(sha)
            try:
                (d / f"{sha}.jpg").write_bytes(data)
                got += 1
            except Exception as e:  # noqa: BLE001
                log.info("ghi ảnh lỗi: %s", str(e)[:60])
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
    log.info("anh18/%s: +%d ảnh (tổng %d)", key, got, have + got)
    return have + got


def _used_path(key: str) -> Path:
    return _dir(key) / "used.json"


def _load_used(key: str) -> set[str]:
    try:
        return set(json.loads(_used_path(key).read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        return set()


def _save_used(key: str, used: set[str]) -> None:
    try:
        _used_path(key).write_text(json.dumps(sorted(used)[-800:]), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


_FILLING: set[str] = set()


def _bg_fill(key: str, target: int = POOL_TARGET) -> None:
    """Đủ ảnh để trả ngay rồi vẫn âm thầm gom tiếp cho đủ pool 50–70."""
    if key in _FILLING:
        return
    _FILLING.add(key)

    async def run():
        try:
            await ensure_pool(key, target)
        finally:
            _FILLING.discard(key)

    try:
        asyncio.create_task(run())
    except RuntimeError:
        _FILLING.discard(key)


async def pick(key: str, n: int = 10) -> list[Path]:
    """Lấy n ảnh chưa gửi (hết thì quay vòng), đảm bảo pool đủ trước khi lấy."""
    if key not in CATS:
        return []
    if count(key) < 12:
        try:
            await asyncio.wait_for(ensure_pool(key, 20), timeout=40)
        except asyncio.TimeoutError:
            log.info("anh18/%s: gom ảnh quá lâu, dùng những gì đang có", key)
    files = sorted(_dir(key).glob("*.jpg"))
    if not files:
        return []
    used = _load_used(key)
    fresh = [f for f in files if f.name not in used]
    if len(fresh) < n:                       # hết ảnh mới -> quay vòng
        used = set()
        fresh = files
    random.shuffle(fresh)
    chosen = fresh[:n]
    used |= {f.name for f in chosen}
    _save_used(key, used)
    if count(key) < POOL_TARGET:
        _bg_fill(key)
    return chosen


# ─────────────────────────────────────────────────────── video (redgifs)
_rg_token = {"value": "", "exp": 0.0}


async def _redgifs_token() -> str:
    if _rg_token["value"] and time.time() < _rg_token["exp"]:
        return _rg_token["value"]
    data = await http.fetch_json("https://api.redgifs.com/v2/auth/temporary", timeout=20)
    tok = (data or {}).get("token") or ""
    if tok:
        _rg_token["value"] = tok
        _rg_token["exp"] = time.time() + 40 * 60
    return tok


async def _redgifs_mp4(key: str, want: int = 8) -> list[str]:
    tok = await _redgifs_token()
    if not tok:
        return []
    queries = (CATS.get(key) or {}).get("video") or (CATS.get(key) or {}).get("queries") or []
    out: list[str] = []
    for q in queries[:2]:
        data = await http.fetch_json(
            "https://api.redgifs.com/v2/gifs/search"
            f"?search_text={quote(q)}&count=40&order=trending",
            headers={"Authorization": f"Bearer {tok}"},
            timeout=25,
        )
        for g in (data or {}).get("gifs") or []:
            urls = g.get("urls") or {}
            u = urls.get("sd") or urls.get("hd") or ""
            if u.endswith(".mp4"):
                out.append(u)
        if len(out) >= want * 3:
            break
    random.shuffle(out)
    return out[: want * 3]


async def take_videos(key: str, n: int = 5, max_mb: int = 24) -> list[bytes]:
    """Tải n video ngắn (mp4) về RAM. Ảnh lỗi/quá nặng thì bỏ, lấy cái khác."""
    urls = await _redgifs_mp4(key, n * 3)
    out: list[bytes] = []
    for u in urls:
        if len(out) >= n:
            break
        raw = await http.download_bytes(
            u, max_bytes=max_mb * 1024 * 1024,
            headers={"Referer": "https://www.redgifs.com/"}, timeout=60,
        )
        if not raw or len(raw) < 80_000:
            continue
        if not raw[4:8] == b"ftyp":          # phải là mp4
            continue
        out.append(raw)
    return out


def pool_status() -> list[dict]:
    return [{"key": k, "label": cat_label(k), "count": count(k), "target": POOL_TARGET} for k in ORDER]

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Facebook: lấy **ID + ảnh đại diện** từ link hồ sơ / username / ID.

Vì sao phải nhiều lớp: Facebook chặn xem hồ sơ khi chưa đăng nhập (trả về
trang login), nên không có lớp nào là "chắc chắn 100%" cho username lạ.
Bot thử lần lượt và trả về cái gì lấy được:

1. Link có sẵn số ID            -> profile.php?id=… · /people/ten-1000xxxx · /1000xxxx
2. Link share / link rút gọn    -> đi theo redirect để lấy ID
3. Username                     -> Graph API nếu bạn khai FB_TOKEN (.env)
                                   -> đọc HTML 4 giao diện FB (www · m · mbasic · free)
                                      và 2 proxy đọc hộ khi IP bị chặn
4. Ảnh đại diện                 -> https://graph.facebook.com/<id>/picture (không cần token)

Kết quả ID được nhớ vào data/fb_cache.json (username -> id) để lần sau tức thì.
"""

from __future__ import annotations

import asyncio
import html
import html as htmllib
import json
import re
import time

import config
from core import http, settings
from core.store import JsonStore

_CACHE = JsonStore("fb_cache.json", {"users": {}})
TOKEN_KEY = "fb.token"

NUM_RE = re.compile(r"^\d{5,}$")
PROFILE_PHP = re.compile(r"profile\.php\?[^#]*\bid=(\d+)", re.I)
PEOPLE_URL = re.compile(r"/people/[^/?#]+/(\d+)", re.I)
P_PAGE_URL = re.compile(r"facebook\.com/p/[^/?#]*?-(\d{8,})", re.I)
VANITY_URL = re.compile(r"facebook\.com/([A-Za-z0-9._-]{2,80})(?:[/?#]|$)", re.I)
SHORT_URL = re.compile(r"(?:facebook\.com/share|fb\.com|m\.me|messenger\.com/t)/([A-Za-z0-9._-]+)", re.I)

META_PATTERNS = [
    re.compile(r'"userID"\s*:\s*"(\d+)"'),
    re.compile(r'"profile_id"\s*:\s*"?(\d+)"?'),
    re.compile(r'"entity_id"\s*:\s*"(\d+)"'),
    re.compile(r'"delegate_page"\s*:\s*\{[^}]*"id"\s*:\s*"(\d+)"'),
    re.compile(r'fb://profile/(\d+)'),
    re.compile(r'"id"\s*:\s*"(\d{8,})"'),
]
NAME_PATTERNS = [
    re.compile(r'<meta property="og:title" content="([^"]{1,120})"'),
    re.compile(r"<title[^>]*>([^<]{1,120})</title>", re.I),
]
AVATAR_PATTERNS = [
    re.compile(r'<meta property="og:image" content="([^"]+)"'),
    re.compile(r'"profile_pic_url"\s*:\s*"([^"]+)"'),
]

MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 10; SM-G975F) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Mobile Safari/537.36"
)
LITE_UA = "Mozilla/5.0 (Linux; Android 4.4.2; Nexus 4) AppleWebKit/537.36 Chrome/34 Mobile Safari/537.36"

PROXY_READERS = (
    "https://api.allorigins.win/raw?url={q}",
    "https://corsproxy.io/?{q}",
)


def _cache() -> dict:
    raw = _CACHE.data.get("users")
    if not isinstance(raw, dict):
        raw = {}
        _CACHE.data["users"] = raw
    return raw


def _cached(uname: str) -> dict | None:
    rec = _cache().get(uname.lower())
    if rec and time.time() - rec.get("t", 0) < 7 * 86400:
        return rec
    return None


def _save(uname: str, uid: str, name: str = "") -> None:
    if not uname or not uid:
        return
    db = _cache()
    db[uname.lower()] = {"id": str(uid), "n": name[:80], "t": int(time.time())}
    if len(db) > 5000:
        for k in sorted(db, key=lambda x: db[x].get("t", 0))[: len(db) - 5000]:
            db.pop(k, None)
    _CACHE.save()


# ------------------------------------------------------------------ phân tích input
def parse(raw: str) -> dict:
    """Tách input thành {'id': …, 'username': …, 'url': …, 'share': …}."""
    out = {"id": "", "username": "", "url": "", "share": "", "raw": (raw or "").strip()}
    text = out["raw"]
    if not text:
        return out

    m = SHORT_URL.search(text)
    if m:
        link = m.group(0)
        if not link.startswith("http"):
            link = "https://" + link.lstrip("/")
        out["share"] = link
        return out

    m = PROFILE_PHP.search(text) or PEOPLE_URL.search(text) or P_PAGE_URL.search(text)
    if m:
        out["id"] = m.group(1)
        return out

    bare = text.lstrip("@").strip()
    if NUM_RE.match(bare):
        out["id"] = bare
        return out

    if re.fullmatch(r"[A-Za-z0-9._-]{2,80}", bare):       # người dùng gõ mỗi username
        out["username"] = bare
        return out

    m = VANITY_URL.search(text)
    if m:
        name = m.group(1)
        if NUM_RE.match(name):          # v18.4: facebook.com/1000446343626 -> ID
            out["id"] = name
            return out
        if name.lower() in ("profile.php", "people", "share", "watch", "groups",
                            "photo", "p", "reel", "videos", "video.php"):
            out["url"] = text
        else:
            out["username"] = name
            out["url"] = text
        return out

    if "facebook.com" in text or "fb.com" in text:
        out["url"] = text
        return out

    out["username"] = bare
    return out


def _unpack(url: str) -> str:
    """Bóc link bị mã hoá nhiều lớp (login/?next=...%3Fid%3D...) để đọc được ID."""
    from urllib.parse import unquote

    out = url or ""
    for _ in range(4):
        nxt = html.unescape(unquote(out))
        if nxt == out:
            break
        out = nxt
    return out


SHARE_ID_PATTERNS = (
    PROFILE_PHP,
    PEOPLE_URL,
    P_PAGE_URL,
    re.compile(r"/videos/(?:[^/?#]+/)?(\d{8,})", re.I),
    re.compile(r"/reel/(\d{8,})", re.I),
    re.compile(r"story_fbid=(\d{8,})", re.I),
    re.compile(r"facebook\.com/(\d{8,})(?:[/?#]|$)", re.I),
)


async def _follow_share(url: str) -> str:
    """Đi theo redirect của link share (/share/…) để tìm ID hoặc username.

    Facebook thường trả về /login/?next=<link thật đã mã hoá> nên phải bóc
    nhiều lớp mã hoá rồi mới đọc được id.
    """
    url = (url or "").strip()
    if not url.startswith("http"):
        bare = re.sub(r"^(?:(?:www|m|mbasic|web)\.)?facebook\.com/", "", url.lstrip("/"))
        url = "https://www.facebook.com/" + bare
    cur = url
    for _ in range(4):
        status, body, headers = await http.fetch(
            cur, method="GET", allow_redirects=False, timeout=14, retries=1,
            headers={"User-Agent": "facebookexternalhit/1.1"},
        )
        loc = headers.get("Location") or headers.get("location") or ""
        if not loc and body:
            text = body.decode("utf-8", "replace")
            m = re.search(
                r"""(?:location\.(?:replace|href)\s*=?\s*\(?\s*|url=|u=)["'](https?[^"']+)["']""",
                text,
            )
            loc = m.group(1) if m else ""
        if not loc:
            return ""
        flat = _unpack(loc)
        for pat in SHARE_ID_PATTERNS:
            m = pat.search(flat)
            if m:
                return m.group(1)
        m = VANITY_URL.search(flat)
        if m and m.group(1).lower() not in ("login", "share", "watch", "reel", "p", "people"):
            return m.group(1)
        cur = loc if loc.startswith("http") else "https://www.facebook.com" + loc
    return ""


async def real_url(url: str) -> str:
    """Đi hết chuỗi redirect của link share, trả URL thật (đã bóc mã hoá).

    Dùng cho phần tải video: /share/… có thể trỏ tới /videos/…, /reel/… hoặc
    một hồ sơ. Không đọc được thì trả lại link gốc.
    """
    url = (url or "").strip()
    if not url.startswith("http"):
        bare = re.sub(r"^(?:(?:www|m|mbasic|web)\.)?facebook\.com/", "", url.lstrip("/"))
        url = "https://www.facebook.com/" + bare
    cur = url
    for _ in range(4):
        _st, body, headers = await http.fetch(
            cur, method="GET", allow_redirects=False, timeout=14, retries=1,
            headers={"User-Agent": "facebookexternalhit/1.1"},
        )
        loc = headers.get("Location") or headers.get("location") or ""
        if not loc:
            text = body.decode("utf-8", errors="replace") if body else ""
            m = re.search(r'property="og:url"\s+content="([^"]+)"', text) or \
                re.search(r'rel="canonical"\s+href="([^"]+)"', text)
            got = htmllib.unescape(m.group(1)).replace("\\/", "/") if m else ""
            return got or cur
        cur = _unpack(loc)
        if not cur.startswith("http"):
            cur = "https://www.facebook.com" + ("" if cur.startswith("/") else "/") + cur
        if "/login" not in cur and "/checkpoint" not in cur:
            return cur
    return cur


def _pick(patterns, text: str) -> str:
    for pat in patterns:
        m = pat.search(text)
        if m:
            return m.group(1)
    return ""


# ------------------------------------------------------------------ lấy ID từ username
def token() -> str:
    """Token Graph API: ưu tiên token admin nhập bằng /fb token, rồi tới .env FB_TOKEN."""
    return str(settings.get(TOKEN_KEY, "") or getattr(config, "FB_TOKEN", "") or "").strip()


def set_token(raw: str) -> str:
    """Lưu / xoá token. Trả về token đang dùng."""
    raw = (raw or "").strip()
    settings.set_value(TOKEN_KEY, raw)
    return raw


async def _from_graph(uname: str) -> dict | None:
    token_ = token()
    if not token_:
        return None
    url = ("https://graph.facebook.com/v21.0/"
           f"{uname}?fields=id,name,picture.type(large)&access_token={token_}")
    data = await http.fetch_json(url, timeout=15, retries=1)
    if not isinstance(data, dict) or data.get("error"):
        return None
    uid = str(data.get("id") or "")
    if not uid:
        return None
    pic = ((data.get("picture") or {}).get("data") or {}).get("url", "")
    return {"id": uid, "name": data.get("name", ""), "avatar_url": pic, "how": "Graph API"}


async def _graph_id(uid: str) -> dict | None:
    """v18b: có token -> tra thẳng ID: tên + ảnh thật (100%, không dính login wall)."""
    token_ = token()
    if not token_ or not str(uid).isdigit():
        return None
    url = ("https://graph.facebook.com/v21.0/"
           f"{uid}?fields=id,name,picture.width(720).height(720)&access_token={token_}")
    data = await http.fetch_json(url, timeout=15, retries=1)
    if not isinstance(data, dict) or data.get("error"):
        return None
    pic = ((data.get("picture") or {}).get("data") or {}).get("url", "")
    return {"id": str(data.get("id") or uid), "name": data.get("name", "") or "",
            "avatar_url": pic, "how": "Graph API (token)"}


async def _cookie_id(uid: str) -> dict | None:
    """v18b: đã nạp /cookie fb -> đọc trang hồ sơ bằng ID, ra tên + ảnh THẬT."""
    try:
        from core import cookies as ck
        hdr = dict(ck.header("fb", ("facebook.com",)))
    except Exception:  # noqa: BLE001
        return None
    if not hdr:
        return None
    hdr.update({"User-Agent": DESKTOP_UA,
                "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8"})
    for url in (f"https://www.facebook.com/profile.php?id={uid}",
                f"https://www.facebook.com/{uid}",
                f"https://m.facebook.com/profile.php?id={uid}"):
        try:
            _st, body, _hd = await http.fetch(url, headers=hdr, timeout=14, retries=0)
        except Exception:  # noqa: BLE001
            continue
        if not body:
            continue
        text = body.decode("utf-8", errors="replace")
        name = _clean_name(_pick(NAME_PATTERNS, text))
        av = html.unescape(_pick(AVATAR_PATTERNS, text) or "")
        if name or av:
            return {"id": str(uid), "name": name, "avatar_url": av,
                    "how": "cookie Facebook"}
    return None


DESKTOP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
FB_CRAWLER_UA = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"
# v18: Googlebot duoc FB tra HTML cong khai (khong bi chan dang nhap) -> lay ten + avatar that
GOOGLEBOT_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
BINGBOT_UA = "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)"
BROWSER_UA = DESKTOP_UA


async def _from_cookie(uname: str) -> dict | None:
    """Đã nạp cookie (/cookie fb) -> đọc trang hồ sơ như người đã đăng nhập.

    Đây là đường CHẮC NHẤT để đổi @username -> ID: trang HTML trả về có
    "userID":"1000…" + tên + ảnh đại diện.
    """
    try:
        from core import cookies as ck
        hdr = dict(ck.header("fb", ("facebook.com",)))
    except Exception:  # noqa: BLE001
        hdr = {}
    if not hdr:
        return None
    hdr["User-Agent"] = DESKTOP_UA
    hdr["Accept-Language"] = "vi-VN,vi;q=0.9,en;q=0.8"
    for url in (f"https://www.facebook.com/{uname}",
                f"https://m.facebook.com/{uname}?v=timeline"):
        try:
            _st, body, _hd = await http.fetch(url, headers=hdr, timeout=12, retries=0)
        except Exception:  # noqa: BLE001
            continue
        if not body:
            continue
        text = body.decode("utf-8", errors="replace")
        uid = _pick(META_PATTERNS, text)
        if not uid:
            continue
        return {
            "id": uid,
            "name": html.unescape(_pick(NAME_PATTERNS, text)).replace(" | Facebook", "").strip(),
            "avatar_url": html.unescape(_pick(AVATAR_PATTERNS, text)),
            "how": "cookie Facebook",
        }
    return None


async def _from_html(uname: str) -> dict | None:
    """Đọc song song nhiều giao diện FB + proxy đọc hộ, lấy cái nào trả ID trước."""
    import asyncio

    paths = [f"https://www.facebook.com/{uname}",
             f"https://m.facebook.com/{uname}",
             f"https://mbasic.facebook.com/{uname}",
             f"https://free.facebook.com/{uname}",
             # v18b: trang /about tra ve HTML day du (co ten + ID) ca khi trang chu
             # bi chan / tra trang dang nhap -> username la van ra duoc ho so.
             f"https://www.facebook.com/{uname}/about"]

    targets: list[tuple[str, dict]] = []
    for url in paths:
        targets.append((url, {"User-Agent": MOBILE_UA}))
        targets.append((url, {"User-Agent": LITE_UA}))
    # UA của chính Facebook (dùng để xem trước link) đôi lúc được trả HTML công khai
    targets.append((paths[0], {"User-Agent": FB_CRAWLER_UA}))
    targets.append((paths[0] + "/", {"User-Agent": FB_CRAWLER_UA}))
    # v18: Googlebot doc duoc ca ten lan avatar (FB khong tra trang dang nhap)
    # v18b: Googlebot cho CA 4 giao dien + Bingbot -> username la van ra duoc ID
    #       (truoc day chi thu 2 host nen @username hay truot).
    for _u in paths:
        targets.append((_u, {"User-Agent": GOOGLEBOT_UA}))
    targets.append((paths[0], {"User-Agent": BINGBOT_UA}))
    targets.append((paths[1], {"User-Agent": BINGBOT_UA}))
    for url in paths[:2]:
        for tpl in PROXY_READERS:
            targets.append((tpl.format(q=http_quote(url)), {}))

    async def _one(url: str, headers: dict):
        try:
            status, body, _ = await http.fetch(url, headers=headers, timeout=10, retries=0)
            if not body or status in (0, 404):
                return None
            text = body.decode("utf-8", errors="replace")
            uid = _pick(META_PATTERNS, text)
            if not uid:
                return None
            return {
                "id": uid,
                "name": html.unescape(_pick(NAME_PATTERNS, text)).replace(" | Facebook", "").strip(),
                "avatar_url": html.unescape(_pick(AVATAR_PATTERNS, text)),
                "how": "đọc trang Facebook",
            }
        except Exception:  # noqa: BLE001
            return None

    tasks = [asyncio.create_task(_one(u, h)) for u, h in targets]
    try:
        for coro in asyncio.as_completed(tasks, timeout=12):
            res = await coro
            if res:
                for t in tasks:
                    if not t.done():
                        t.cancel()
                return res
    except (asyncio.TimeoutError, Exception):  # noqa: BLE001
        pass
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
    return None


_FB_SESSION = None


async def _fb_session():
    """Session riêng cho Facebook: KHÔNG dùng proxy môi trường.

    Session dùng chung (core.http) đi qua proxy của máy chủ -> FB trả trang
    đăng nhập (không có og:title/og:image) nên tra tên + avatar luôn rỗng.
    """
    global _FB_SESSION
    import aiohttp

    if _FB_SESSION is None or _FB_SESSION.closed:
        _FB_SESSION = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=24, ssl=False, ttl_dns_cache=300),
            timeout=aiohttp.ClientTimeout(total=20, connect=10),
            trust_env=False,
        )
    return _FB_SESSION


async def _fb_get(url: str, ua: str, *, timeout: int = 15) -> bytes | None:
    """GET thẳng, trả bytes (None nếu lỗi)."""
    try:
        s = await _fb_session()
        async with s.get(url, headers={"User-Agent": ua,
                                       "Accept-Language": "en-US,en;q=0.9",
                                       "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
                         timeout=__import__("aiohttp").ClientTimeout(total=timeout, connect=10),
                         allow_redirects=True) as r:
            if r.status >= 400:
                return None
            return await r.read()
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------- reader (v18c)
_READER = "https://r.jina.ai/"
_TITLE_RE = re.compile(r"^Title:\s*(.{1,120})$", re.M)
_MD_IMG_RE = re.compile(r"!\[[^\]]*\]\((https://scontent[^)\s]{20,400})\)")


async def _from_jina_head(tgt: str) -> dict | None:
    """v18.4: đọc riêng thẻ <head> qua reader -> og:title (tên) + og:image (ảnh THẬT).

    Đây là đường NGẮN nhất lấy được ảnh thật: chỉ ~100 KB thay vì cả trang 19 MB,
    và reader đi từ IP khác nên FB không chặn. Chỉ áp dụng cho trang hồ sơ CÔNG KHAI
    (nick để riêng tư thì FB không xuất og:image -> đành cần cookie).
    """
    import aiohttp
    import re as _re

    tgt = (tgt or "").rstrip("/")
    if not tgt:
        return None
    hdrs = {"User-Agent": BROWSER_UA, "x-target-selector": "head",
            "x-respond-with": "html", "Accept-Language": "en-US,en;q=0.9"}
    body = ""
    try:
        async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30), trust_env=False) as s:
            for url in (tgt + "/about", tgt):
                try:
                    async with s.get(_READER + url, headers=hdrs) as r:
                        if r.status >= 400:
                            continue
                        body = (await r.read()).decode("utf-8", "replace")
                except Exception:  # noqa: BLE001
                    continue
                if "og:image" in body or "og:title" in body:
                    break
    except Exception:  # noqa: BLE001
        return None
    if not body or len(body) < 500:
        return None

    name = ""
    m = _re.search(r'property="og:title"\s+content="([^"]*)"', body)
    if not m:
        m = _re.search(r'content="([^"]*)"\s+property="og:title"', body)
    if m:
        name = _clean_name(html.unescape(m.group(1)))

    av = ""
    m = _re.search(r'property="og:image"\s+content="([^"]*)"', body)
    if not m:
        m = _re.search(r'content="([^"]*)"\s+property="og:image"', body)
    if m:
        cand = html.unescape(m.group(1))
        if not is_default_url(cand):
            av = (cand.replace("s320x320", "s720x720")
                      .replace("p320x320", "p720x720"))
    if not (name or av):
        return None
    return {"id": "", "name": name, "avatar_url": av, "how": "og:image (ảnh thật)"}


async def _from_reader(url: str) -> dict | None:
    """Đường dự phòng cuối: đọc trang qua reader công khai (IP khác, FB không chặn).

    FB hay chặn IP máy chủ -> trang hồ sơ trả HTML rỗng. Reader trả bản đã render,
    trong đó có tên (Title: …) và ảnh đại diện (link scontent.fbcdn).
    """
    import aiohttp

    text = ""
    try:
        async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=45), trust_env=False) as s:
            # trang /about mới có ảnh đại diện trong bản render; trang chính thì không
            for target in (url.rstrip("/") + "/about", url):
                try:
                    async with s.get(_READER + target,
                                     headers={"User-Agent": BROWSER_UA,
                                              "Accept-Language": "en-US,en;q=0.9"}) as r:
                        if r.status >= 400:
                            continue
                        body = (await r.read()).decode("utf-8", "replace")
                except Exception:  # noqa: BLE001
                    continue
                if body and (_TITLE_RE.search(body) or _MD_IMG_RE.search(body)):
                    text = body
                    break
    except Exception:  # noqa: BLE001
        return None
    if not text:
        return None

    name = ""
    m = _TITLE_RE.search(text)
    if m:
        name = _clean_name(m.group(1))
    uid = _pick(META_PATTERNS, text)
    av = ""
    for cand in _MD_IMG_RE.findall(text)[:6]:
        if is_default_url(cand):
            continue
        av = cand.replace("s320x320", "s720x720").replace("p320x320", "p720x720")
        break
    if not (name or uid or av):
        return None
    return {"id": uid or "", "name": name, "avatar_url": av,
            "how": "đọc qua reader (IP khác)"}


def _clean_name(name: str) -> str:
    name = html.unescape(name or "").replace(" | Facebook", "").strip()
    bad = {"facebook", "log in", "đăng nhập", "login", "facebook - log in or sign up",
           "watch", "error", ""}
    return "" if name.lower() in bad else name


_PROFILE_CACHE: dict[str, dict] = {}
PROFILE_TTL = 1800
# FB chặn IP khi bị gọi liên tục -> nghỉ 90s rồi thử lại, khỏi treo mỗi lượt 15s
_COOLDOWN: dict[str, float] = {}   # v18c: nghỉ theo từng ID, không khoá cả bot


async def _from_page_id(uid: str, *, force: bool = False) -> dict | None:
    """Đã có số ID vẫn lấy thêm TÊN + ẢNH THẬT từ trang hồ sơ.

    FB trả trang đăng nhập cho UA thường nhưng trả HTML đầy đủ cho Googlebot
    -> og:title = tên, og:image = ảnh đại diện.
    v18: tối đa 2 lần thử (8s/lần), có cache 30 phút + nghỉ 90s khi bị chặn,
    nên /fb không bao giờ treo lâu.
    """
    uid = str(uid or "")
    if not uid.isdigit():
        return None
    hit = _PROFILE_CACHE.get(uid)
    if hit and not force and time.time() - hit["t"] < PROFILE_TTL:
        return hit["v"]
    if not force and time.time() < _COOLDOWN.get(uid, 0.0):
        return None

    # v18: m.facebook.com + Googlebot la duong DUY NHAT hien tai tra ve ten that
    # (www.facebook.com tra trang login rong, mbasic tra "Log in or sign up").
    # v18c: trang CHÍNH (/{id}) trả HTML rỗng không có og:*; trang /about thì
    # CÓ og:title (tên) + og:image (ảnh thật) -> đây là đường chắc ăn nhất.
    tries = [(f"https://www.facebook.com/{uid}/about", GOOGLEBOT_UA),
             (f"https://m.facebook.com/{uid}/about", GOOGLEBOT_UA),
             (f"https://www.facebook.com/profile.php?id={uid}&sk=about", GOOGLEBOT_UA),
             (f"https://www.facebook.com/{uid}", GOOGLEBOT_UA),
             (f"https://m.facebook.com/{uid}", GOOGLEBOT_UA)]
    for url, ua in tries:
        body = await _fb_get(url, ua, timeout=15)
        if not body:
            continue
        text = body.decode("utf-8", errors="replace")
        name = _clean_name(_pick(NAME_PATTERNS, text))
        av = html.unescape(_pick(AVATAR_PATTERNS, text) or "")
        if not av and not name:
            continue
        got = {"id": uid, "name": name, "avatar_url": av, "how": "đọc trang hồ sơ (ID)"}
        _PROFILE_CACHE[uid] = {"v": got, "t": time.time()}
        return got
    _COOLDOWN[uid] = time.time() + 60
    return None


def looks_default(data: bytes) -> bool:
    """Ảnh đại diện mặc định của FB rất nhẹ (~7–9 KB) — nhận ra để báo cho đúng."""
    return not data or len(data) < 9000


# v18: og:image của nick không công khai ảnh trỏ về bộ ảnh mặc định của FB
_DEFAULT_URL_HINTS = ("t1.30497-1", "static.xx.fbcdn.net", "rsrc.php", "/images/silhouette")


def is_default_url(url: str) -> bool:
    """True nếu URL ảnh này chắc chắn là ảnh xám mặc định của Facebook."""
    u = (url or "").lower()
    return any(h in u for h in _DEFAULT_URL_HINTS)


def http_quote(url: str) -> str:
    from urllib.parse import quote
    return quote(url, safe="")


async def _enrich(uid: str) -> dict:
    """v18.4: lấy tên + ẢNH THẬT cho 1 ID bằng nhiều lớp (chạy song song cho nhanh).

    Dùng chung cho cả 2 đường: link có sẵn ID và link chia sẻ -> ID.
    """
    uid = (uid or "").strip()
    if not uid:
        return {}
    deadline = time.monotonic() + 13.0
    jobs: list = []
    if token():
        jobs.append(_graph_id(uid))
    jobs.append(_from_page_id(uid))
    jobs.append(_cookie_id(uid))
    extra: dict = {}
    if jobs:
        try:
            got = await asyncio.wait_for(asyncio.gather(*jobs, return_exceptions=True),
                                        timeout=max(deadline - time.monotonic(), 3))
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            got = []
        for item in got:
            if isinstance(item, Exception) or not isinstance(item, dict):
                continue
            if not extra.get("name") and item.get("name"):
                extra["name"] = item["name"]
            if not extra.get("avatar_url") and item.get("avatar_url") \
                    and not is_default_url(item["avatar_url"]):
                extra["avatar_url"] = item["avatar_url"]
            if not extra.get("how") and item.get("how"):
                extra["how"] = item["how"]
            if extra.get("avatar_url") and extra.get("name"):
                break
    if (not extra.get("avatar_url") or not extra.get("name")) \
            and time.monotonic() < deadline:
        for probe in (
            lambda: _from_jina_head(f"https://www.facebook.com/{uid}"),
            lambda: _from_reader(f"https://www.facebook.com/{uid}"),
        ):
            try:
                alt = await probe()
            except Exception:  # noqa: BLE001
                alt = None
            if not isinstance(alt, dict):
                continue
            if not extra.get("name") and alt.get("name"):
                extra["name"] = alt["name"]
            if not extra.get("avatar_url") and alt.get("avatar_url") \
                    and not is_default_url(alt["avatar_url"]):
                extra["avatar_url"] = alt["avatar_url"]
            if not extra.get("how") and alt.get("how"):
                extra["how"] = alt["how"]
            if extra.get("avatar_url") and extra.get("name"):
                break
    return extra


async def resolve(raw: str) -> dict:
    """Trả {'ok', 'id', 'username', 'name', 'avatar_url', 'how', 'err'}."""
    info = parse(raw)
    out = {"ok": False, "id": "", "username": info["username"], "name": "",
           "avatar_url": "", "how": "", "err": "", "share": info["share"]}

    if info["id"]:
        out.update(ok=True, id=info["id"], how="có sẵn ID trong link")
        extra = await _enrich(info["id"])
        if extra:
            out.update(name=extra.get("name", "") or out["name"],
                       avatar_url=extra.get("avatar_url", "") or out["avatar_url"],
                       how=extra.get("how") or "link có ID + đọc hồ sơ")
        return out

    if info["share"]:
        uid = await _follow_share(info["share"])
        if uid and uid.isdigit():
            out.update(ok=True, id=uid, how="link chia sẻ → ID")
            extra = await _enrich(uid)
            if extra:
                out.update(name=extra.get("name", "") or out["name"],
                           avatar_url=extra.get("avatar_url", "") or out["avatar_url"],
                           how=(extra.get("how") or "") + " · từ link chia sẻ"
                           if extra.get("how") else "link chia sẻ → ID + hồ sơ")
            return out
        if uid:
            info["username"] = uid

    uname = info["username"]
    if not uname:
        out["err"] = "Không đọc được username/ID từ nội dung bạn gửi."
        return out

    hit = _cached(uname)
    if hit:
        out.update(ok=True, id=hit["id"], name=hit.get("n", ""), how="đã lưu trước đó")
        # v18.4: bản lưu cũ chỉ có ID + tên -> vẫn đi lấy ẢNH THẬT cho đủ hồ sơ
        try:
            meta = await _from_jina_head(f"https://www.facebook.com/{uname}")
        except Exception:  # noqa: BLE001
            meta = None
        if meta and meta.get("avatar_url"):
            out["avatar_url"] = meta["avatar_url"]
            out["how"] = meta["how"]
        return out

    got = await _from_graph(uname)
    if not got:
        got = await _from_cookie(uname)
    if not got:
        got = await _from_html(uname)
    if not got or not got.get("avatar_url"):
        alt = await _from_jina_head(f"https://www.facebook.com/{uname}")
        if alt and (alt.get("avatar_url") or alt.get("name")):
            if not got:
                got = alt
            else:
                got = {**got,
                       "name": got.get("name") or alt.get("name", ""),
                       "avatar_url": got.get("avatar_url") or alt.get("avatar_url", ""),
                       "how": alt.get("how") or got.get("how")}
    if not got:
        got = await _from_reader(f"https://www.facebook.com/{uname}")
    if got:
        if got.get("id"):
            _save(uname, got["id"], got.get("name", ""))
        out.update(ok=True, id=got["id"], name=got.get("name", ""),
                   avatar_url=got.get("avatar_url", ""), how=got.get("how", ""))
        return out

    out["err"] = ("Facebook chặn xem hồ sơ khi chưa đăng nhập — riêng @username thì "
                  "cần cookie hoặc token. Link có số ID thì lấy được 100%.")
    return out


def _is_image(data: bytes | None) -> bool:
    if not data or len(data) < 1200:
        return False
    if data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n" or data[:4] == b"RIFF":
        return True
    return False


_AVA_CACHE: dict[str, tuple[float, bytes]] = {}


async def avatar(uid: str, og_url: str = "") -> bytes | None:
    """Ảnh đại diện: ưu tiên ảnh THẬT trong trang hồ sơ, graph chỉ để chống cháy.

    v18: graph.facebook.com không token giờ trả ảnh xám mặc định cho mọi ID
    (720x720, giống nhau) -> phải lấy og:image của trang hồ sơ trước.
    v18.4: cache 1 giờ -> tra lại cùng ID là trả ngay, không bị FB chặn vì spam.
    """
    key = str(uid or og_url or "")
    if key:
        hit = _AVA_CACHE.get(key)
        if hit and time.time() - hit[0] < 3600:
            return hit[1]
    tries: list[tuple[str, dict]] = []
    # v18b: có token / cookie -> ảnh THẬT đứng đầu hàng (graph không token = ảnh xám)
    if uid and str(uid).isdigit():
        real = None
        try:
            real = await _graph_id(str(uid)) if token() else None
            if not (real and real.get("avatar_url")):
                real = await _cookie_id(str(uid))
        except Exception:  # noqa: BLE001
            real = None
        if real and real.get("avatar_url"):
            tries.append((html.unescape(real["avatar_url"]),
                          {"User-Agent": DESKTOP_UA, "Referer": "https://www.facebook.com/"}))
    og_url = html.unescape(og_url or "")
    if uid and (not og_url or is_default_url(og_url)):
        try:
            meta = await _from_jina_head(f"https://www.facebook.com/{uid}/about")
            if meta and meta.get("avatar_url"):
                og_url = meta["avatar_url"]
        except Exception:  # noqa: BLE001
            pass
    # ảnh mặc định thì để cuối hàng, nhường chỗ cho nguồn khác
    if og_url and not is_default_url(og_url):
        tries.append((og_url, {"User-Agent": GOOGLEBOT_UA,
                               "Referer": "https://www.facebook.com/"}))
    if uid and str(uid).isdigit():
        extra = await _from_page_id(str(uid))
        if extra and extra.get("avatar_url"):
            tries.append((html.unescape(extra["avatar_url"]),
                          {"User-Agent": GOOGLEBOT_UA, "Referer": "https://www.facebook.com/"}))
        tries += [
            (f"https://graph.facebook.com/{uid}/picture?type=large&width=720&height=720",
             {"User-Agent": BROWSER_UA}),
            (f"https://graph.facebook.com/{uid}/picture?type=large", {"User-Agent": BROWSER_UA}),
        ]
    if og_url and is_default_url(og_url):
        tries.append((og_url, {"User-Agent": GOOGLEBOT_UA,
                               "Referer": "https://www.facebook.com/"}))
    for url, _hdrs in tries:
        for ua in (GOOGLEBOT_UA, FB_CRAWLER_UA, BROWSER_UA):
            data = await _fb_get(url, ua, timeout=25)
            if _is_image(data):
                if key and not looks_default(data):
                    _AVA_CACHE[key] = (time.time(), data)
                return data
    return None


def cache_total() -> int:
    return len(_cache())


def cache_clear() -> int:
    n = len(_cache())
    _CACHE.data["users"] = {}
    _CACHE.save()
    return n

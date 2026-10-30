#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render canvas: danh sách bài hát + status máy chủ."""

import io
import os
import platform
import shutil
from datetime import datetime

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from config import BRAND, FONT_BOLD_CANDIDATES, FONT_CANDIDATES
from core.text import VN_TZ
from core.text import clean_text, human_duration, human_int, uptime_str

_FONT_CACHE: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}


def _pick(cands) -> str | None:
    for p in cands:
        if p and os.path.exists(p):
            return p
    return None


_REG = _pick(FONT_CANDIDATES)
_BOLD = _pick(FONT_BOLD_CANDIDATES)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    key = (size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    path = _BOLD if bold else _REG
    try:
        f = ImageFont.truetype(path, size) if path else ImageFont.load_default()
    except Exception:  # noqa: BLE001
        f = ImageFont.load_default()
    _FONT_CACHE[key] = f
    return f


# ---------------------------------------------------------------- helpers
def _gradient(w: int, h: int, top: tuple, bottom: tuple) -> Image.Image:
    base = Image.new("RGB", (1, h))
    px = base.load()
    for y in range(h):
        t = y / max(h - 1, 1)
        px[0, y] = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return base.resize((w, h), Image.BILINEAR)


def _glow(img: Image.Image, cx: int, cy: int, r: int, color: tuple, alpha: int = 70):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*color, alpha))
    layer = layer.filter(ImageFilter.GaussianBlur(r // 2))
    img.alpha_composite(layer)


def _fit_text(draw, text, f, max_w: int) -> str:
    text = clean_text(text)
    if draw.textlength(text, font=f) <= max_w:
        return text
    ell = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if draw.textlength(text[:mid] + ell, font=f) <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return (text[:lo].rstrip() + ell) if lo else ell


def _wrap(draw, text, f, max_w: int, max_lines: int = 2) -> list[str]:
    """Xuống dòng theo từ, tối đa max_lines, dòng cuối thêm … nếu còn chữ."""
    text = clean_text(text)
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=f) <= max_w:
            cur = trial
            continue
        if cur:
            lines.append(cur)
            cur = w
        else:
            lines.append(_fit_text(draw, w, f, max_w))
            cur = ""
        if len(lines) == max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if not lines:
        lines = [text]
    if len(" ".join(lines)) < len(text):
        lines[-1] = _fit_text(draw, lines[-1] + "…", f, max_w)
    return lines[:max_lines]


def _round_rect(draw, box, r, fill=None, outline=None, width=1):
    draw.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)


def _thumb(img: Image.Image, data: bytes | None, box, radius: int = 14) -> Image.Image:
    w = int(box[2] - box[0])
    h = int(box[3] - box[1])
    if data:
        try:
            t = Image.open(io.BytesIO(data)).convert("RGB")
            # cover-crop về đúng tỉ lệ
            tw, th = t.size
            target = w / h
            if tw / th > target:
                nw = int(th * target)
                t = t.crop(((tw - nw) // 2, 0, (tw + nw) // 2, th))
            else:
                nh = int(tw / target)
                t = t.crop((0, (th - nh) // 2, tw, (th + nh) // 2))
            t = t.resize((w, h), Image.LANCZOS)
        except Exception:  # noqa: BLE001
            t = Image.new("RGB", (w, h), (44, 52, 78))
    else:
        t = Image.new("RGB", (w, h), (44, 52, 78))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    t.putalpha(mask)
    img.alpha_composite(t, (int(box[0]), int(box[1])))
    return img


# ---------------------------------------------------------------- sing canvas
SING_W = 1180
SING_CARD_W = 536
SING_CARD_H = 132
SING_GAP_X = 28
SING_GAP_Y = 18
SING_PAD = 40
SING_TOP = 236


def sing_canvas(keyword: str, tracks: list[dict], thumbs: list[bytes | None] | None = None,
                elapsed_ms: int = 0) -> io.BytesIO:
    """tracks: [{title, channel, duration_text, views_text}] (10 bài)."""
    tracks = list(tracks or [])[:10]
    thumbs = list(thumbs or []) + [None] * 10

    rows = max(1, (len(tracks) + 1) // 2)
    H = SING_TOP + rows * SING_CARD_H + (rows - 1) * SING_GAP_Y + 96

    img = _gradient(SING_W, H, (13, 16, 32), (28, 20, 58)).convert("RGBA")
    _glow(img, 120, 60, 220, (129, 90, 255), 60)
    _glow(img, SING_W - 90, 180, 240, (255, 84, 148), 46)
    _glow(img, SING_W // 2, H - 60, 260, (34, 211, 238), 30)

    draw = ImageDraw.Draw(img)

    # header
    f_brand = font(19, True)
    f_small = font(15)
    f_title = font(44, True)
    f_query = font(24, True)
    f_meta = font(16)

    draw.text((SING_PAD + 2, 30), BRAND, font=f_brand, fill=(196, 181, 253))
    draw.text((SING_PAD + 2, 56), "MUSIC ENGINE  •  YOUTUBE", font=f_small, fill=(120, 130, 165))

    # chip bên phải
    chip_txt = f"{len(tracks)} KẾT QUẢ"
    cw = int(draw.textlength(chip_txt, font=f_small)) + 34
    _round_rect(draw, [SING_W - SING_PAD - cw, 34, SING_W - SING_PAD, 70], 18, fill=(30, 41, 70), outline=(90, 80, 150), width=2)
    draw.text((SING_W - SING_PAD - cw / 2, 52), chip_txt, font=f_small, fill=(167, 139, 250), anchor="mm")

    draw.text((SING_PAD, 92), "TÌM NHẠC", font=f_title, fill=(255, 255, 255))

    # pill từ khoá
    kw = clean_text(keyword)[:70] or "—"
    kw_fit = _fit_text(draw, kw, f_query, SING_W - 2 * SING_PAD - 48)
    kw_w = int(draw.textlength(kw_fit, font=f_query)) + 44
    _round_rect(draw, [SING_PAD, 160, SING_PAD + kw_w, 208], 24, fill=(24, 30, 52), outline=(70, 62, 120), width=2)
    # dấu "ngoặc nháy" thay icon kính lúp
    draw.text((SING_PAD + 22, 184), f'“{kw_fit}”', font=f_query, fill=(233, 213, 255), anchor="lm")

    meta = datetime.now(VN_TZ).strftime("%d/%m/%Y %H:%M") + (f"   •   {elapsed_ms} ms" if elapsed_ms else "")
    draw.text((SING_W - SING_PAD, 184), meta, font=f_meta, fill=(120, 130, 165), anchor="rm")

    # cards
    rank_colors = [(250, 204, 21), (203, 213, 225), (217, 119, 6)]
    for i, tr in enumerate(tracks):
        col = i % 2
        row = i // 2
        x = SING_PAD + col * (SING_CARD_W + SING_GAP_X)
        y = SING_TOP + row * (SING_CARD_H + SING_GAP_Y)

        _round_rect(draw, [x, y, x + SING_CARD_W, y + SING_CARD_H], 20, fill=(22, 27, 46), outline=(46, 52, 84), width=2)
        # vạch màu trái
        draw.rounded_rectangle([x, y + 26, x + 6, y + SING_CARD_H - 26], radius=3,
                               fill=rank_colors[i] if i < 3 else (76, 61, 130))

        thumb_box = [x + 18, y + 18, x + 18 + 168, y + 18 + 94]
        _thumb(img, thumbs[i], thumb_box, radius=14)
        draw = ImageDraw.Draw(img)

        # badge thứ tự
        bx, by = x + 18 + 10, y + 18 + 10
        draw.ellipse([bx - 14, by - 14, bx + 14, by + 14], fill=(12, 15, 28), outline=rank_colors[i] if i < 3 else (120, 110, 180), width=2)
        draw.text((bx, by - 1), str(i + 1), font=font(16, True), fill=rank_colors[i] if i < 3 else (226, 232, 240), anchor="mm")

        tx = x + 204
        max_w = SING_CARD_W - 204 - 18
        f_song = font(21, True)
        lines = _wrap(draw, tr.get("title") or "Không rõ", f_song, max_w, 2)
        ty = y + 20
        for ln in lines:
            draw.text((tx, ty), ln, font=f_song, fill=(240, 244, 255))
            ty += 27

        views = tr.get("views_text") or ""
        ch_line = f"{tr.get('channel') or 'YouTube'}" + (f"  •  {views}" if views else "")
        draw.text((tx, y + 78), _fit_text(draw, ch_line, font(16), max_w - 96),
                  font=font(16), fill=(148, 163, 184))

        # duration chip
        dur = tr.get("duration_text") or human_duration(tr.get("duration"))
        f_dur = font(14, True)
        dw = int(draw.textlength(dur, font=f_dur)) + 22
        _round_rect(draw, [x + SING_CARD_W - dw - 16, y + 76, x + SING_CARD_W - 16, y + 102], 13,
                    fill=(38, 30, 66), outline=(96, 79, 150), width=1)
        draw.text((x + SING_CARD_W - dw / 2 - 16, y + 89), dur, font=f_dur, fill=(199, 180, 255), anchor="mm")

    # footer
    fy = H - 52
    draw.text((SING_PAD, fy), "Bấm số thứ tự bên dưới để tải MP3", font=font(17), fill=(148, 163, 184))
    draw.text((SING_W - SING_PAD, fy), BRAND, font=font(17, True), fill=(129, 140, 248), anchor="ra")

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", compress_level=6)
    buf.seek(0)
    buf.name = "sing_result.png"
    return buf


# ---------------------------------------------------------------- status canvas
def status_canvas(start_ts: float, ping_ms: int = 0, providers: dict | None = None) -> io.BytesIO:
    W, H = 1180, 800
    img = _gradient(W, H, (12, 16, 30), (24, 22, 52)).convert("RGBA")
    _glow(img, 140, 90, 230, (56, 189, 248), 55)
    _glow(img, W - 120, H - 120, 250, (168, 85, 247), 50)
    draw = ImageDraw.Draw(img)

    f_brand = font(19, True)
    f_h1 = font(34, True)
    f_head = font(20, True)
    f_key = font(17)
    f_val = font(17, True)
    f_foot = font(15)

    draw.text((40, 30), BRAND, font=f_brand, fill=(196, 181, 253))
    draw.text((40, 62), "SERVER & TOOLKIT STATUS", font=font(15), fill=(120, 130, 165))
    draw.text((40, 92), "TRẠNG THÁI HỆ THỐNG", font=f_h1, fill=(255, 255, 255))

    # uptime chip
    up = uptime_str(start_ts)
    f_up = font(16, True)
    uw = int(draw.textlength(up, font=f_up)) + 54
    _round_rect(draw, [W - 40 - uw, 96, W - 40, 136], 20, fill=(16, 58, 40), outline=(52, 211, 153), width=2)
    draw.ellipse([W - 40 - uw + 16, 111, W - 40 - uw + 26, 121], fill=(74, 222, 128))
    draw.text((W - 40 - uw + 36, 116), up, font=f_up, fill=(209, 250, 229), anchor="lm")

    now = datetime.now(VN_TZ).strftime("%H:%M:%S • %d/%m/%Y")
    try:
        import resource  # chỉ có trên Linux/macOS

        ram = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    except Exception:  # noqa: BLE001
        ram = 0.0
    try:
        total, used, free = shutil.disk_usage("/")
        free_gb = round(free / 1024 ** 3, 2)
        disk_pct = used / total * 100 if total else 0
    except Exception:  # noqa: BLE001
        free_gb, disk_pct = 0.0, 0.0

    cards = [
        (40, 176, "MÁY CHỦ", [
            ("Thời gian", now, (255, 255, 255)),
            ("Ping Telegram", f"{ping_ms} ms", (74, 222, 128) if ping_ms < 250 else (251, 191, 36)),
            ("RAM dùng", f"{ram} MB", (251, 146, 60)),
            ("Python", platform.python_version(), (147, 197, 253)),
            ("Hệ điều hành", f"{platform.system()} {platform.machine()}", (226, 232, 240)),
            ("Ổ đĩa trống", f"{free_gb} GB", (74, 222, 128) if free_gb > 1 else (239, 68, 68)),
        ]),
        (W // 2 + 10, 176, "TIỆN ÍCH", [
            ("Tải video", providers.get("dl", "Sẵn sàng") if providers else "Sẵn sàng", (74, 222, 128)),
            ("Tìm nhạc", providers.get("music", "Sẵn sàng") if providers else "Sẵn sàng", (74, 222, 128)),
            ("Chuyển giọng nói", providers.get("tts", "Sẵn sàng") if providers else "Sẵn sàng", (74, 222, 128)),
            ("Chụp website", providers.get("cap", "Sẵn sàng") if providers else "Sẵn sàng", (74, 222, 128)),
            ("Quản lý nhóm", providers.get("grp", "—") if providers else "—", (147, 197, 253)),
            ("Canvas", "Pillow online", (147, 197, 253)),
        ]),
    ]

    for cx, cy, title, rows in cards:
        cw = W // 2 - 50
        ch = 430
        _round_rect(draw, [cx, cy, cx + cw, cy + ch], 22, fill=(20, 25, 43), outline=(46, 52, 84), width=2)
        draw.text((cx + 26, cy + 24), title, font=f_head, fill=(129, 140, 248))
        draw.line([(cx + 26, cy + 58), (cx + cw - 26, cy + 58)], fill=(46, 52, 84), width=2)
        for i, (k, v, col) in enumerate(rows):
            ry = cy + 84 + i * 52
            draw.text((cx + 26, ry), k, font=f_key, fill=(148, 163, 184))
            draw.text((cx + cw - 26, ry), _fit_text(draw, v, f_val, cw - 240), font=f_val, fill=col, anchor="ra")

    # progress
    py = 630
    for i, (label, pct, col) in enumerate([
        ("RAM", min(ram / 512 * 100 if ram else 12, 100), (249, 115, 22)),
        ("Ổ ĐĨA", disk_pct, (56, 189, 248)),
    ]):
        bx = 40 if i == 0 else W // 2 + 10
        bw = W // 2 - 50
        draw.text((bx, py), f"{label}  {pct:.0f}%", font=font(15, True), fill=(203, 213, 225))
        _round_rect(draw, [bx, py + 26, bx + bw, py + 46], 10, fill=(30, 36, 58))
        _round_rect(draw, [bx, py + 26, bx + max(16, int(bw * pct / 100)), py + 46], 10, fill=col)

    draw.text((W // 2, H - 34), f"{BRAND} • Multi-platform engine", font=f_foot, fill=(120, 130, 165), anchor="mm")

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", compress_level=6)
    buf.seek(0)
    buf.name = "status.png"
    return buf


# ---------------------------------------------------------------- weather canvas
def _wicon(draw, code: int, cx: int, cy: int, r: int = 60) -> None:
    """Vẽ icon thời tiết bằng hình khối (không phụ thuộc font emoji)."""
    code = int(code or 0)
    sun = (253, 224, 71)
    cloud = (226, 232, 240)
    grey = (148, 163, 184)
    rain = (96, 165, 250)
    snow = (224, 242, 254)

    def draw_sun(sx, sy, sr):
        for i in range(8):
            import math
            a = math.radians(i * 45)
            x1 = sx + int((sr + 8) * math.cos(a))
            y1 = sy + int((sr + 8) * math.sin(a))
            x2 = sx + int((sr + r * 0.28) * math.cos(a))
            y2 = sy + int((sr + r * 0.28) * math.sin(a))
            draw.line([(x1, y1), (x2, y2)], fill=sun, width=max(3, r // 14))
        draw.ellipse([sx - sr, sy - sr, sx + sr, sy + sr], fill=sun)

    def draw_cloud(ox, oy, cw, ch, col=cloud):
        draw.ellipse([ox, oy + ch * 0.35, ox + cw * 0.55, oy + ch], fill=col)
        draw.ellipse([ox + cw * 0.2, oy, ox + cw * 0.75, oy + ch * 0.8], fill=col)
        draw.ellipse([ox + cw * 0.45, oy + ch * 0.25, ox + cw, oy + ch], fill=col)
        draw.rounded_rectangle([ox, oy + ch * 0.55, ox + cw, oy + ch * 0.98], radius=int(ch * 0.22), fill=col)

    if code in (0, 1):
        draw_sun(cx, cy, int(r * 0.72))
        if code == 1:
            draw_cloud(cx + r * 0.1, cy + r * 0.35, int(r * 1.3), int(r * 0.8))
    elif code in (2, 3):
        draw_sun(cx - r * 0.45, cy - r * 0.45, int(r * 0.45))
        draw_cloud(cx - r * 0.75, cy - r * 0.05, int(r * 1.55), int(r * 0.95))
    elif code in (45, 48):
        draw_cloud(cx - r * 0.8, cy - r * 0.5, int(r * 1.6), int(r * 0.9), grey)
        for i in range(3):
            y = cy + r * 0.35 + i * r * 0.22
            draw.line([(cx - r * 0.75, y), (cx + r * 0.75, y)], fill=grey, width=max(3, r // 12))
    elif 71 <= code <= 77 or code in (85, 86):
        draw_cloud(cx - r * 0.8, cy - r * 0.65, int(r * 1.6), int(r * 0.9))
        for i in range(3):
            x = cx - r * 0.55 + i * r * 0.55
            draw.ellipse([x - 6, cy + r * 0.45, x + 6, cy + r * 0.45 + 12], fill=snow)
    elif 95 <= code <= 99:
        draw_cloud(cx - r * 0.8, cy - r * 0.7, int(r * 1.6), int(r * 0.9), grey)
        draw.polygon([
            (cx + r * 0.05, cy + r * 0.1), (cx - r * 0.25, cy + r * 0.75),
            (cx + r * 0.05, cy + r * 0.72), (cx - r * 0.15, cy + r * 1.2),
            (cx + r * 0.45, cy + r * 0.5), (cx + r * 0.12, cy + r * 0.55),
        ], fill=(250, 204, 21))
    else:  # mưa
        draw_cloud(cx - r * 0.8, cy - r * 0.7, int(r * 1.6), int(r * 0.9), grey)
        for i in range(3):
            x = cx - r * 0.5 + i * r * 0.5
            draw.line([(x, cy + r * 0.35), (x - 10, cy + r * 0.85)], fill=rain, width=max(4, r // 12))


def weather_canvas(w: dict) -> io.BytesIO:
    W, H = 1180, 760
    img = _gradient(W, H, (10, 15, 38), (26, 24, 66)).convert("RGBA")
    _glow(img, 140, 70, 240, (56, 189, 248), 52)
    _glow(img, W - 140, 150, 260, (168, 85, 247), 46)
    _glow(img, W // 2, H - 40, 240, (34, 211, 238), 26)
    draw = ImageDraw.Draw(img)

    f_brand = font(19, True)
    f_small = font(15)
    f_h1 = font(40, True)
    f_city = font(28, True)
    f_desc = font(21)
    f_temp = font(112, True)
    f_chip = font(16, True)
    f_label = font(14)
    f_day = font(19, True)
    f_day2 = font(15)
    f_foot = font(15)

    draw.text((40, 28), BRAND, font=f_brand, fill=(196, 181, 253))
    draw.text((40, 58), "WEATHER ENGINE  •  OPEN-METEO", font=f_small, fill=(120, 130, 165))
    draw.text((40, 88), "THỜI TIẾT", font=f_h1, fill=(255, 255, 255))

    # chip "3 NGÀY"
    chip = "DỰ BÁO 3 NGÀY"
    cw_ = int(draw.textlength(chip, font=f_small)) + 34
    _round_rect(draw, [W - 40 - cw_, 96, W - 40, 132], 18, fill=(24, 34, 62), outline=(90, 80, 150), width=2)
    draw.text((W - 40 - cw_ / 2, 114), chip, font=f_small, fill=(167, 139, 250), anchor="mm")

    # ---------------- card chính
    top, bottom = 156, 452
    _round_rect(draw, [40, top, W - 40, bottom], 26, fill=(19, 24, 43), outline=(48, 55, 88), width=2)

    draw.text((74, top + 26), _fit_text(draw, w.get("name", "—"), f_city, 560), font=f_city, fill=(255, 255, 255))
    draw.text((74, top + 66), _fit_text(draw, w.get("desc", ""), f_desc, 520), font=f_desc, fill=(147, 197, 253))

    temp = w.get("temp")
    temp_txt = f"{round(float(temp))}" if temp is not None else "--"
    draw.text((72, top + 104), temp_txt, font=f_temp, fill=(255, 255, 255))
    tw_ = draw.textlength(temp_txt, font=f_temp)
    draw.text((72 + tw_ + 8, top + 132), "°C", font=font(44, True), fill=(125, 211, 252))

    # 3 chip nhỏ: cảm giác / độ ẩm / gió
    chips = [
        ("CẢM GIÁC", f"{round(float(w.get('feels') or 0))}°C"),
        ("ĐỘ ẨM", f"{w.get('humidity', '--')}%"),
        ("GIÓ", f"{w.get('wind', '--')} km/h"),
    ]
    cw2, gap, cy2 = 168, 14, top + 232
    for i, (label, val) in enumerate(chips):
        x = 74 + i * (cw2 + gap)
        _round_rect(draw, [x, cy2, x + cw2, cy2 + 68], 16, fill=(28, 34, 56), outline=(52, 60, 96), width=1)
        draw.text((x + 16, cy2 + 12), label, font=f_label, fill=(148, 163, 184))
        draw.text((x + 16, cy2 + 34), val, font=f_chip, fill=(255, 255, 255))

    # icon lớn bên phải
    _wicon(draw, w.get("code", 0), W - 250, top + 150, 92)

    # ---------------- 3 ngày
    days = (w.get("days") or [])[:3]
    card_w = (W - 80 - 2 * 22) // 3
    y0 = 484
    for i, d in enumerate(days):
        x = 40 + i * (card_w + 22)
        _round_rect(draw, [x, y0, x + card_w, y0 + 208], 22, fill=(19, 24, 43), outline=(48, 55, 88), width=2)
        try:
            dt = datetime.strptime(d["date"], "%Y-%m-%d")
            label = "HÔM NAY" if i == 0 else dt.strftime("%d/%m")
            weekday = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"][dt.weekday()]
        except Exception:  # noqa: BLE001
            label, weekday = str(d.get("date", "")), ""
        draw.text((x + 22, y0 + 18), f"{label}", font=f_day, fill=(255, 255, 255))
        draw.text((x + 22, y0 + 46), weekday, font=f_day2, fill=(120, 130, 165))
        _wicon(draw, d.get("code", 0), x + card_w // 2, y0 + 96, 34)

        lo, hi = d.get("min"), d.get("max")
        lo = round(float(lo)) if lo is not None else 0
        hi = round(float(hi)) if hi is not None else 0
        draw.text((x + card_w - 22, y0 + 18), f"{hi}°", font=f_day, fill=(252, 165, 165), anchor="ra")
        draw.text((x + card_w - 22, y0 + 46), f"{lo}°", font=f_day2, fill=(147, 197, 253), anchor="ra")

        # thanh nhiệt độ
        bx0, bx1, by = x + 22, x + card_w - 22, y0 + 150
        _round_rect(draw, [bx0, by, bx1, by + 10], 5, fill=(34, 41, 66))
        span = max(1, hi - lo)
        w_pct = min(1.0, max(0.15, span / 20))
        _round_rect(draw, [bx0, by, bx0 + int((bx1 - bx0) * w_pct), by + 10], 5,
                    fill=(129, 140, 248) if i else (52, 211, 153))
        rain = d.get("rain")
        draw.text((x + 22, y0 + 170), f"Mưa: {rain}%" if rain is not None else "Mưa: --", font=f_day2, fill=(148, 163, 184))

    now = datetime.now(VN_TZ).strftime("%H:%M • %d/%m/%Y")
    draw.text((W // 2, H - 30), f"{BRAND}  •  cập nhật {now}  •  nguồn Open-Meteo",
              font=f_foot, fill=(120, 130, 165), anchor="mm")

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", compress_level=6)
    buf.seek(0)
    buf.name = "weather.png"
    return buf

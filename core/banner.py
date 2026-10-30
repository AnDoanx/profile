#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tạo ảnh banner / ảnh bìa theo TÊN (không cần mẫu, không cần mạng).

4 phong cách × 3 kích thước:
  style 0 AURORA (tím–xanh)   1 NEON (đen–hồng)   2 SUNSET (cam–hồng)   3 LIGHT (trắng–xanh)
  size  banner 1200×630 · vuong 1080×1080 · story 1080×1920
"""

import io
import math
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from config import BRAND, SUPPORT_URL
from core.canvas import _round_rect, font as _canvas_font

try:                                    # Pillow >= 9.1
    _RESAMPLE = Image.Resampling.LANCZOS
except Exception:                       # noqa: BLE001
    _RESAMPLE = Image.LANCZOS

SIZES: dict[str, tuple[int, int]] = {
    "banner": (1200, 630),
    "vuong": (1080, 1080),
    "story": (1080, 1920),
}
SIZE_LABEL = {"banner": "Ngang 1200×630", "vuong": "Vuông 1080×1080", "story": "Story 1080×1920"}

PALETTES = [
    {   # 0 — aurora
        "label": "Aurora",
        "top": (13, 16, 42), "bottom": (46, 18, 82),
        "accent": (128, 96, 255), "accent2": (0, 226, 200),
        "title": (247, 248, 255), "sub": (176, 184, 224), "frame": (128, 96, 255),
    },
    {   # 1 — neon
        "label": "Neon",
        "top": (7, 7, 14), "bottom": (30, 6, 34),
        "accent": (255, 61, 129), "accent2": (0, 229, 255),
        "title": (255, 255, 255), "sub": (188, 186, 210), "frame": (255, 61, 129),
    },
    {   # 2 — sunset
        "label": "Sunset",
        "top": (36, 12, 34), "bottom": (138, 42, 66),
        "accent": (255, 138, 76), "accent2": (255, 214, 102),
        "title": (255, 249, 242), "sub": (255, 214, 200), "frame": (255, 138, 76),
    },
    {   # 3 — light
        "label": "Sáng",
        "top": (247, 249, 255), "bottom": (219, 231, 255),
        "accent": (58, 92, 255), "accent2": (0, 190, 190),
        "title": (18, 24, 48), "sub": (92, 104, 140), "frame": (58, 92, 255),
    },
]

MAX_NAME = 42


# ------------------------------------------------------------------ helpers
def _gradient(w: int, h: int, top: tuple, bottom: tuple) -> Image.Image:
    base = Image.new("RGB", (1, h))
    px = base.load()
    for y in range(h):
        t = y / max(h - 1, 1)
        px[0, y] = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return base.resize((w, h), Image.BILINEAR).convert("RGBA")


def _blob(img: Image.Image, cx: int, cy: int, r: int, color: tuple, alpha: int) -> None:
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*color, alpha))
    layer = layer.filter(ImageFilter.GaussianBlur(max(r // 2, 8)))
    img.alpha_composite(layer)


def _fit(draw, text: str, size: int, max_w: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    """Thu nhỏ font tới khi vừa bề ngang."""
    size = max(size, 12)
    while size > 12:
        f = _canvas_font(size, bold)
        box = draw.textbbox((0, 0), text, font=f)
        if box[2] - box[0] <= max_w:
            return f
        size -= max(2, size // 18)
    return _canvas_font(12, bold)


def initials(name: str) -> str:
    words = [w for w in str(name).replace("-", " ").split() if w]
    if not words:
        return "KA"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[-1][0]).upper()


def _circle_photo(logo: bytes, d: int) -> Image.Image | None:
    try:
        raw = Image.open(io.BytesIO(logo)).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None
    side = min(raw.size)
    raw = raw.crop(((raw.width - side) // 2, (raw.height - side) // 2,
                    (raw.width + side) // 2, (raw.height + side) // 2))
    raw = raw.resize((d, d), _RESAMPLE)
    mask = Image.new("L", (d * 4, d * 4), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, d * 4 - 1, d * 4 - 1], fill=255)
    mask = mask.resize((d, d), _RESAMPLE)
    out = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    out.paste(raw, (0, 0), mask)
    return out


def _sparkles(img: Image.Image, pal: dict, seed: str, n: int = 26) -> None:
    rnd = random.Random(seed)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    w, h = img.size
    for _ in range(n):
        x, y = rnd.randint(0, w), rnd.randint(0, h)
        r = rnd.choice((1, 1, 2, 2, 3))
        color = pal["accent2"] if rnd.random() < 0.6 else pal["accent"]
        a = rnd.randint(60, 190)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(*color, a))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(0.6)))


def _grid(img: Image.Image, pal: dict, step: int = 64) -> None:
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    w, h = img.size
    col = (*pal["sub"], 16)
    for x in range(0, w, step):
        d.line([(x, 0), (x, h)], fill=col, width=1)
    for y in range(0, h, step):
        d.line([(0, y), (w, y)], fill=col, width=1)
    img.alpha_composite(layer)


def _spaced(text: str) -> str:
    return " ".join(text.upper())


# ------------------------------------------------------------------ render
def render(name: str, style: int = 0, size: str = "banner",
           logo: bytes | None = None) -> io.BytesIO:
    """Trả PNG trong BytesIO. `name` = tên muốn in lên banner."""
    name = " ".join(str(name or "").split())[:MAX_NAME] or "KHOA AN"
    style = int(style) % len(PALETTES)
    size = size if size in SIZES else "banner"
    pal = PALETTES[style]
    W, H = SIZES[size]

    img = _gradient(W, H, pal["top"], pal["bottom"])
    unit = min(W, H)
    if H > W * 1.4:                     # story dọc: phóng to nội dung cho khỏi trống
        unit = int(unit * 1.12)

    # nền: quầng sáng + lưới mờ + đốm sáng
    _blob(img, int(W * 0.16), int(H * 0.86), int(unit * 0.62), pal["accent"], 66)
    _blob(img, int(W * 0.9), int(H * 0.12), int(unit * 0.55), pal["accent2"], 54)
    _grid(img, pal, max(int(unit * 0.06), 40))
    _sparkles(img, pal, seed=f"{name}|{style}|{size}")

    # đo chữ trước (dùng draw tạm, chưa vẽ lên ảnh)
    meas = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    pad = int(unit * 0.055)
    r = int(unit * 0.099)
    title_src = name.upper()
    f_name = _fit(meas, title_src, int(unit * 0.125), int(W - pad * 3.2))
    f_sub = _canvas_font(max(int(unit * 0.031), 14), True)
    sub = _spaced(BRAND)

    h_name = meas.textbbox((0, 0), title_src, font=f_name)[3]
    h_sub = meas.textbbox((0, 0), sub, font=f_sub)[3]
    block = r * 2 + int(unit * 0.055) + h_name + int(unit * 0.035) + h_sub
    y = int((H - block) / 2) - int(unit * 0.01)
    cx = W // 2
    cy = y + r

    # --- lớp trang trí: khung + vòng tròn (vẽ lên layer riêng để alpha đúng)
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    _round_rect(d, [pad, pad, W - pad, H - pad], int(unit * 0.045),
                outline=(*pal["frame"], 90), width=max(2, int(unit * 0.0025)))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 34),
              outline=(*pal["accent"], 235), width=max(3, int(unit * 0.0055)))
    d.ellipse([cx - r - int(unit * 0.012), cy - r - int(unit * 0.012),
               cx + r + int(unit * 0.012), cy + r + int(unit * 0.012)],
              outline=(*pal["accent2"], 90), width=max(1, int(unit * 0.0018)))
    img.alpha_composite(ov)

    # --- logo (nếu có) hoặc chữ viết tắt
    logo_img = _circle_photo(logo, r * 2 - int(unit * 0.012) * 2) if logo else None
    if logo_img is not None:
        img.alpha_composite(logo_img, (cx - logo_img.width // 2, cy - logo_img.height // 2))
    else:
        ini = initials(name)
        f_ini = _fit(meas, ini, int(r * 0.86), int(r * 1.45))
        bb = meas.textbbox((0, 0), ini, font=f_ini)
        ov2 = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(ov2).text(
            (cx - (bb[2] - bb[0]) / 2, cy - (bb[3] - bb[1]) / 2 - bb[1]),
            ini, font=f_ini, fill=pal["title"])
        img.alpha_composite(ov2)

    # --- lớp chữ
    tx = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(tx)

    # tên
    ty = y + r * 2 + int(unit * 0.055)
    bb = meas.textbbox((0, 0), title_src, font=f_name)
    d.text((cx - (bb[2] - bb[0]) / 2, ty - bb[1]), title_src, font=f_name, fill=pal["title"])

    # gạch + phụ đề
    ly = ty + h_name + int(unit * 0.028)
    half = int(unit * 0.11)
    d.line([(cx - half, ly), (cx + half, ly)], fill=(*pal["accent2"], 200),
           width=max(2, int(unit * 0.003)))
    bb = meas.textbbox((0, 0), sub, font=f_sub)
    d.text((cx - (bb[2] - bb[0]) / 2, ly + int(unit * 0.018) - bb[1]), sub,
           font=f_sub, fill=pal["sub"])

    # góc dưới: tên kênh / liên hệ
    f_foot = _canvas_font(max(int(unit * 0.024), 11), False)
    foot = "@" + SUPPORT_URL.rstrip("/").split("/")[-1] if SUPPORT_URL else BRAND
    bb = meas.textbbox((0, 0), foot, font=f_foot)
    foot_gap = int(unit * (0.05 if H > W * 1.3 else 0.018))
    d.text((cx - (bb[2] - bb[0]) / 2, H - pad - bb[3] - foot_gap), foot,
           font=f_foot, fill=(*pal["sub"], 210))
    img.alpha_composite(tx)

    out = io.BytesIO()
    img.convert("RGB").save(out, "PNG", optimize=True)
    out.seek(0)
    return out

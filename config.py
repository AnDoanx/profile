#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cấu hình trung tâm cho toàn bộ bot (KHOA AN TOOLS v5 — TikTok + tiện ích)."""

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Thư mục dữ liệu: trên Railway nên mount volume vào /data và đặt DATA_DIR=/data
# (nếu không, mọi thứ trong data/ sẽ mất sau mỗi lần deploy/restart)
DATA_DIR = Path(os.getenv("DATA_DIR", "").strip() or (BASE_DIR / "data"))
TMP_DIR = Path(os.getenv("TMP_DIR", "").strip() or "/tmp/khoaan-tools")
DATA_DIR.mkdir(parents=True, exist_ok=True)
TMP_DIR.mkdir(parents=True, exist_ok=True)

# ================= TOKEN & QUYỀN =================
BOT_TOKEN = os.getenv(
    "BOT_TOKEN",
    "8949220582:AAE6zt_01TePivwodfw7dVmSWn0prRCvFw8",
).strip()

if not BOT_TOKEN or ":" not in BOT_TOKEN:
    print("❌ Thiếu BOT_TOKEN. Trên Railway: Variables → New Variable → BOT_TOKEN", file=sys.stderr)
    sys.exit(1)


def _parse_ids(raw: str) -> set[int]:
    out: set[int] = set()
    for piece in str(raw or "").replace(";", ",").split(","):
        piece = piece.strip()
        if piece.lstrip("-").isdigit():
            out.add(int(piece))
    return out


# ID admin mặc định (chủ bot). Có thể thêm/bớt bằng /panel trong bot.
_DEFAULT_ADMIN = 8297032840
ADMIN_IDS = _parse_ids(os.getenv("ADMIN_IDS", "")) or {_DEFAULT_ADMIN}
# ID này không bao giờ bị gỡ khỏi danh sách admin (tránh tự khoá mình ra ngoài)
ROOT_ADMIN = _DEFAULT_ADMIN

# ================= THƯƠNG HIỆU =================
BRAND = os.getenv("BRAND", "KHOA AN TOOLS")
OWNER_NAME = os.getenv("OWNER_NAME", "Khoa An")
SUPPORT_URL = os.getenv("SUPPORT_URL", "https://t.me/AnDoanx")

# ================= GIỚI HẠN =================
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "49"))       # giới hạn bot Telegram
MAX_AUDIO_MB = int(os.getenv("MAX_AUDIO_MB", "45"))
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "28"))

# ================= KHU VỰC TIKTOK (tuỳ chọn) =================
# API của bạn không trả "region", bot tự thử tikwm / node-share / trang TikTok.
# Nếu bạn có API khác trả khu vực, khai báo ở đây (dùng {user} thay username):
#   REGION_API=https://api.example.com/tiktok?user={user}
REGION_API = os.getenv("REGION_API", "").strip()

# ================= GIF MỖI CÂU TRẢ LỜI =================
# 1 = sau mỗi câu trả lời bot gửi kèm 1 gif (chiikawa / anime). 0 = tắt.
GIF_REPLY = os.getenv("GIF_REPLY", "1").strip().lower() not in ("0", "false", "off", "no")
GIF_MIN_GAP = float(os.getenv("GIF_MIN_GAP", "5"))   # giây, chống spam cùng 1 chat
# Khi Telegram báo flood (quá nhiều tin), bot tự nghỉ gửi gif ở chat đó bao lâu (giây)
GIF_FLOOD_COOLDOWN = int(os.getenv("GIF_FLOOD_COOLDOWN", "600"))

# ================= ICON PREMIUM (custom emoji Telegram) =================
def _flag(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "off", "no")


# v20: 100% icon premium — tin nhắn, MỌI nút và kết quả mọi lệnh đều có custom emoji.
# Câu nào không có emoji thì core/premium.py tự chèn icon theo nghĩa (không tin nào trống).
#
# ⚠ PREMIUM_FORCED: .env cũ (v18) có thể còn dòng EMOJI_PREMIUM=0 — nếu để nguyên thì
# nâng cấp lên v20 sẽ mất hết icon. Vì vậy mặc định v20 ÉP BẬT; muốn tắt thật thì phải
# đặt PREMIUM_FORCED=0 (rồi mới EMOJI_PREMIUM=0 / BUTTON_ICON_PREMIUM=0 có tác dụng).
PREMIUM_FORCED = _flag("PREMIUM_FORCED", "1")
EMOJI_PREMIUM = True if PREMIUM_FORCED else _flag("EMOJI_PREMIUM")
BUTTON_ICON_PREMIUM = True if PREMIUM_FORCED else _flag("BUTTON_ICON_PREMIUM")

# Nút chỉ dùng icon premium; Telegram từ chối thì mới lùi về chữ không icon.
BUTTON_EMOJI = (not PREMIUM_FORCED) and _flag("BUTTON_EMOJI", "0")

# ================= TỰ XOÁ KHI CÓ CẢM XÚC (reaction) =================
# 1 = ai thả cảm xúc vào tin nhắn nào thì tin đó tự xoá. 0 = tắt.
REACT_DELETE = os.getenv("REACT_DELETE", "1").strip().lower() not in ("0", "false", "off", "no")

# ================= ẢNH 18+ (/anh18) =================
# 1 = cho dùng /anh18 trong nhóm (mặc định: chỉ chat riêng, nhóm thì admin bot mới dùng được)
ANH18_IN_GROUP = os.getenv("ANH18_IN_GROUP", "0").strip().lower() in ("1", "true", "on", "yes")
ANH18_POOL = int(os.getenv("ANH18_POOL", "60"))       # số ảnh giữ mỗi chủ đề (50–70)
ANH18_BATCH = int(os.getenv("ANH18_BATCH", "10"))     # số ảnh gửi mỗi lượt

# ================= WAR / SPAM TAG =================
WAR_MAX = int(os.getenv("WAR_MAX", "100"))            # số lượt tối đa 1 lần war/spam
WAR_INTERVAL = float(os.getenv("WAR_INTERVAL", "1"))  # giây giãn cách mặc định giữa 2 lượt
WAR_MIN_INTERVAL = float(os.getenv("WAR_MIN_INTERVAL", "0.4"))  # sàn an toàn (chống flood)

# ================= CAPTCHA CHỐNG SPAM =================
# 1 = bật xác minh. Sai số lần dùng lệnh thì bắt giải lại captcha.
CAPTCHA_ON = os.getenv("CAPTCHA_ON", "1").strip().lower() not in ("0", "false", "off", "no")
CAPTCHA_EVERY = int(os.getenv("CAPTCHA_EVERY", "10"))  # dùng bao nhiêu lệnh thì bắt xác minh lại

# ================= FACEBOOK (/fb) =================
# Không bắt buộc. Có token (Graph API) thì lấy ID từ username được 100%.
FB_TOKEN = os.getenv("FB_TOKEN", "").strip()

# ================= HEALTH SERVER (Railway bắt buộc) =================
# Railway chỉ coi service là "healthy" khi có process lắng nghe $PORT.
PORT = int(os.getenv("PORT", "8080"))

# ================= CHỤP WEBSITE (/capwed) =================
CAPTURE_W = int(os.getenv("CAPTURE_W", "1280"))
CAPTURE_H = int(os.getenv("CAPTURE_H", "1600"))
CAPTURE_TIMEOUT = int(os.getenv("CAPTURE_TIMEOUT", "60"))
# Đường dẫn trình duyệt headless. Để trống = tự dò chromium / chrome / edge.
CHROME_PATH = os.getenv("CHROME_PATH", "").strip()

# ================= FONT =================
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
    str(BASE_DIR / "assets" / "DejaVuSans.ttf"),
]
FONT_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    str(BASE_DIR / "assets" / "DejaVuSans-Bold.ttf"),
]

# ================= HẠN MỨC /anh18 MỖI NGÀY =================
# Số lượt lấy ảnh/video 18+ của mỗi user trong 1 ngày (admin bot không bị giới hạn).
# Reset lúc 00:00 giờ VN. Đặt 0 = khoá hoàn toàn.
ANH18_DAILY = int(os.getenv("ANH18_DAILY", "3"))

# ================= RẢI QUẢNG CÁO (/qc — userbot) =================
# API_ID / API_HASH lấy ở my.telegram.org → API development tools.
# Có thể bỏ trống rồi nhập trong bot bằng: /qcapi API_ID API_HASH
TG_API_ID = int(os.getenv("TG_API_ID", "0") or 0)
TG_API_HASH = os.getenv("TG_API_HASH", "").strip()
# 0 = chỉ admin bot dùng /qc · 1 = mọi người dùng được
QC_ALLOW_ALL = os.getenv("QC_ALLOW_ALL", "0").strip().lower() in ("1", "true", "on", "yes")
QC_DELAY = float(os.getenv("QC_DELAY", "8"))    # giây giãn cách giữa 2 nhóm
QC_MAX = int(os.getenv("QC_MAX", "200"))        # số nhóm tối đa 1 lần rải

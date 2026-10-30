# KHOA AN TOOLS v4 — image chạy trên Railway / VPS / Docker
# Bao gồm: ffmpeg (tách audio), chromium (chụp website), font Unicode (Việt + emoji + CJK)
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Ho_Chi_Minh \
    DATA_DIR=/data \
    TMP_DIR=/tmp/khoaan-tools \
    PORT=8080 \
    CHROME_PATH=/usr/bin/chromium

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        chromium \
        ca-certificates \
        tzdata \
        curl \
        fonts-dejavu-core \
        fonts-liberation \
        fonts-noto-core \
        fonts-noto-color-emoji \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

# Thư mục ghi dữ liệu (Railway volume nên mount vào /data)
RUN mkdir -p /data /tmp/khoaan-tools

EXPOSE 8080

CMD ["python", "main.py"]

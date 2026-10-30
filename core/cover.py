#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Đổi "bản quyền" bài hát: ghi lại TÊN BÀI + NGHỆ SĨ + ẢNH BÌA vào chính file nhạc.

Hỗ trợ MP3 (ID3v2.3) · M4A/MP4 (covr) · FLAC (picture). File khác thì trả nguyên
kèm ghi chú. Không re-encode nên chất lượng âm thanh giữ nguyên 100%.

Dùng cho lệnh /cover: reply bài vừa tải xong → bot trả lại file đã đổi tên + ảnh bìa.
"""

from __future__ import annotations

import io
import mimetypes
import os
import tempfile

MP3_MAGIC = (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")


class CoverError(Exception):
    pass


def _with_tempfile(data: bytes, suffix: str) -> str:
    """MP4/M4A phải đọc bằng đường dẫn thật (moov có thể nằm cuối file)."""
    fh = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    fh.write(data)
    fh.close()
    return fh.name


def detect(filename: str = "", data: bytes = b"") -> str:
    name = (filename or "").lower()
    if name.endswith(".mp3"):
        return "mp3"
    if name.endswith((".m4a", ".mp4", ".m4b")):
        return "m4a"
    if name.endswith(".flac"):
        return "flac"
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "mp3"
    if data[4:8] == b"ftyp":
        return "m4a"
    if data[:4] == b"fLaC":
        return "flac"
    return ""


def _mime(pic: bytes) -> str:
    if pic[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if pic[:4] == b"RIFF":
        return "image/webp"
    return "image/jpeg"


def _png_to_jpeg(pic: bytes) -> bytes:
    """Telegram hay gửi PNG; ID3/MP4 đọc tốt hơn với JPEG, giữ nguyên nếu lỗi."""
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(pic)).convert("RGB")
        img.thumbnail((1000, 1000))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=92)
        return out.getvalue()
    except Exception:  # noqa: BLE001
        return pic


def probe(data: bytes, filename: str = "") -> dict:
    """Đọc thông tin hiện có: tên bài, nghệ sĩ, album, ảnh bìa, thời lượng."""
    ext = detect(filename, data)
    info = {"ext": ext, "title": "", "artist": "", "album": "", "cover": b"", "duration": 0}
    if not ext:
        return info
    try:
        if ext == "mp3":
            from mutagen.id3 import ID3, ID3NoHeaderError
            from mutagen.mp3 import MP3

            try:
                tags = ID3(io.BytesIO(data))
            except ID3NoHeaderError:
                tags = None
            if tags:
                for key, field in (("TIT2", "title"), ("TPE1", "artist"), ("TALB", "album")):
                    frame = tags.get(key)
                    if frame and str(frame.text):
                        info[field] = str(frame.text[0])
                for frame in tags.getall("APIC"):
                    if frame.data:
                        info["cover"] = bytes(frame.data)
                        break
            try:
                info["duration"] = int(MP3(io.BytesIO(data)).info.length)
            except Exception:  # noqa: BLE001
                pass
        elif ext == "m4a":
            from mutagen.mp4 import MP4

            path = _with_tempfile(data, ".m4a")
            try:
                mp4 = MP4(path)
            except Exception:  # noqa: BLE001
                os.unlink(path)
                raise
            tags = mp4.tags or {}
            info["title"] = str((tags.get("\xa9nam") or [""])[0])
            info["artist"] = str((tags.get("\xa9ART") or [""])[0])
            info["album"] = str((tags.get("\xa9alb") or [""])[0])
            covr = tags.get("covr") or []
            if covr:
                info["cover"] = bytes(covr[0])
            info["duration"] = int(getattr(mp4.info, "length", 0) or 0)
            try:
                os.unlink(path)
            except OSError:
                pass
        else:
            from mutagen.flac import FLAC

            fl = FLAC(io.BytesIO(data))
            info["title"] = (fl.get("title") or [""])[0]
            info["artist"] = (fl.get("artist") or [""])[0]
            info["album"] = (fl.get("album") or [""])[0]
            if fl.pictures:
                info["cover"] = fl.pictures[0].data
            info["duration"] = int(getattr(fl.info, "length", 0) or 0)
    except Exception:  # noqa: BLE001
        pass
    return info


def apply(data: bytes, title: str, artist: str, cover: bytes | None = None,
          album: str = "", filename: str = "") -> tuple[bytes, str]:
    """Ghi tên bài / nghệ sĩ / ảnh bìa vào file. Trả (bytes_mới, ext)."""
    ext = detect(filename, data)
    if not ext:
        raise CoverError("unsupported")
    title = (title or "").strip()[:120]
    artist = (artist or "").strip()[:120]
    album = (album or "").strip()[:120]
    pic = _png_to_jpeg(cover) if cover else None

    if ext == "mp3":
        return _apply_mp3(data, title, artist, album, pic), ext
    if ext == "m4a":
        return _apply_m4a(data, title, artist, album, pic), ext
    return _apply_flac(data, title, artist, album, pic), "flac"


def _apply_mp3(data: bytes, title: str, artist: str, album: str, pic: bytes | None) -> bytes:
    from mutagen.id3 import (APIC, ID3, ID3NoHeaderError, TALB, TIT2, TPE1)

    try:
        tags = ID3(io.BytesIO(data))
    except ID3NoHeaderError:
        tags = ID3()
    if title:
        tags.setall("TIT2", [TIT2(encoding=3, text=[title])])
    if artist:
        tags.setall("TPE1", [TPE1(encoding=3, text=[artist])])
    if album:
        tags.setall("TALB", [TALB(encoding=3, text=[album])])
    if pic:
        tags.delall("APIC")
        tags.add(APIC(encoding=3, mime=_mime(pic), type=3, desc="Cover", data=pic))

    out = io.BytesIO()
    try:
        tags.save(out, v2_version=3, padding=lambda info: 2048)
    except TypeError:                       # mutagen cũ: không nhận lambda
        out = io.BytesIO()
        tags.save(out, v2_version=3)
    except Exception as e:                  # noqa: BLE001
        raise CoverError(str(e)[:120]) from e
    return out.getvalue()


def _apply_m4a(data: bytes, title: str, artist: str, album: str, pic: bytes | None) -> bytes:
    from mutagen.mp4 import MP4, MP4Cover

    path = _with_tempfile(data, ".m4a")
    try:
        mp4 = MP4(path)
        if mp4.tags is None:
            mp4.add_tags()
        if title:
            mp4["\xa9nam"] = [title]
        if artist:
            mp4["\xa9ART"] = [artist]
        if album:
            mp4["\xa9alb"] = [album]
        if pic:
            fmt = MP4Cover.FORMAT_PNG if _mime(pic) == "image/png" else MP4Cover.FORMAT_JPEG
            mp4["covr"] = [MP4Cover(pic, imageformat=fmt)]
        mp4.save()
        with open(path, "rb") as f:
            return f.read()
    except CoverError:
        raise
    except Exception as e:                  # noqa: BLE001
        raise CoverError(str(e)[:120]) from e
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _apply_flac(data: bytes, title: str, artist: str, album: str, pic: bytes | None) -> bytes:
    from mutagen.flac import FLAC, Picture

    fl = FLAC(io.BytesIO(data))
    if title:
        fl["title"] = title
    if artist:
        fl["artist"] = artist
    if album:
        fl["album"] = album
    if pic:
        fl.clear_pictures()
        p = Picture()
        p.type = 3
        p.mime = _mime(pic)
        p.desc = "Cover"
        p.data = pic
        fl.add_picture(p)
    out = io.BytesIO()
    try:
        fl.save(out)
    except Exception as e:                  # noqa: BLE001
        raise CoverError(str(e)[:120]) from e
    return out.getvalue()


def extension_for(ext: str) -> str:
    return {"mp3": ".mp3", "m4a": ".m4a", "flac": ".flac"}.get(ext, ".mp3")


def guess_mime(name: str) -> str:
    return mimetypes.guess_type(name or "")[0] or "audio/mpeg"


def safe_filename(name: str) -> str:
    keep = "".join(ch for ch in (name or "") if ch.isalnum() or ch in " -_().[]")
    keep = " ".join(keep.split()).strip()
    return keep[:80] or "audio"

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fake Telegram Bot API server để test thật đường gửi tin (không đoán).

Mọi request tới /bot<token>/<method> được ghi lại (tên + params + thứ tự),
trả về JSON giả lập giống Telegram thật để PTB parse được.
"""

import itertools
import json
from urllib.parse import parse_qsl

from aiohttp import web

BOT_ID = 7770001
BOT_USERNAME = "khoaan_test_bot"
BOT_NAME = "Khoaan Test"


def _msg(chat_id=1001, message_id=1, **extra):
    data = {
        "message_id": message_id,
        "date": 1730000000,
        "chat": {"id": chat_id, "type": "private"},
        "from": {"id": BOT_ID, "is_bot": True, "first_name": BOT_NAME, "username": BOT_USERNAME},
    }
    data.update(extra)
    return data


class FakeAPI:
    USER_ID = 555000111
    GROUP_ADMIN = 555000111
    BIO = "làm bot & web · Sài Gòn"
    AVATAR_PNG = None

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.unknown_usernames: set[str] = set()   # @name mà "Telegram" không tra được
        self.files: list[tuple[str, int]] = []
        self.reject: set[str] = set()          # emoji bị "Telegram" từ chối
        self.reject_style = False              # giả lập Telegram cũ không hiểu style/icon nút
        self._ids = itertools.count(100)
        self._app = web.Application()
        self._app.router.add_route("*", "/bot{token}/{method}", self._handle)
        # tải tệp: /bot<token>/photos/avatar.png  (nhiều đoạn path)
        self._app.router.add_route("*", "/bot{token}/{tail:.*}", self._handle_file)
        self._runner = None
        self.port = None

    # ------------------------------------------------------------------ server
    async def start(self):
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
        return self

    async def stop(self):
        if self._runner:
            await self._runner.cleanup()

    async def _handle_file(self, request: web.Request):
        self.calls.append(("downloadFile", {"path": request.match_info["tail"]}))
        if FakeAPI.AVATAR_PNG is None:
            import io
            from PIL import Image
            buf = io.BytesIO()
            Image.new("RGB", (96, 96), (120, 90, 255)).save(buf, "PNG")
            FakeAPI.AVATAR_PNG = buf.getvalue()
        return web.Response(body=FakeAPI.AVATAR_PNG, content_type="image/png")

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/bot"

    # ------------------------------------------------------------------ helpers
    def methods(self) -> list[str]:
        return [m for m, _ in self.calls]

    def of(self, method: str) -> list[dict]:
        return [p for m, p in self.calls if m == method]

    def texts(self, method: str = "sendMessage") -> list[str]:
        return [str(p.get("text") or p.get("caption") or "") for p in self.of(method)]

    def clear(self):
        self.calls.clear()
        self.files.clear()

    async def _handle(self, request: web.Request):
        method = request.match_info["method"]
        params: dict = {}
        if request.content_type.startswith("multipart/"):
            reader = await request.multipart()
            while True:
                part = await reader.next()
                if part is None:
                    break
                if part.filename:
                    raw = await part.read()
                    self.files.append((part.name or "file", len(raw)))
                    params[part.name] = f"<file:{part.filename}:{len(raw)}>"
                else:
                    params[part.name] = await part.text()
        else:
            raw = await request.text()
            if raw:
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError:
                    body = dict(parse_qsl(raw))
                if "method" in body:  # PTB gửi kiểu {"method":..., "parameters":...}
                    params = body.get("parameters") or {}
                else:
                    params = body
        self.calls.append((method, params))
        if self.reject_style and "style" in str(params.get("reply_markup") or ""):
            return web.json_response(
                {"ok": False, "error_code": 400,
                 "description": 'Bad Request: can\'t parse inline keyboard button: '
                                'field "style" is not supported'},
                status=400,
            )
        res = self._result(method, params)
        if isinstance(res, dict) and res.get("__error__"):
            return web.json_response(
                {"ok": False, "error_code": res["error_code"],
                 "description": res["description"]},
                status=res["error_code"],
            )
        return web.json_response({"ok": True, "result": res})

    def _result(self, method: str, params: dict):
        mid = next(self._ids)
        chat_id = params.get("chat_id") or 1001
        try:
            chat_id = int(chat_id)
        except (TypeError, ValueError):
            chat_id = str(chat_id)

        if method == "getChat":
            raw = str(params.get("chat_id") or "")
            if raw.startswith("@"):
                name = raw[1:]
                if name.lower() in ("khongco", "notfound", "unknown") or name.lower() in self.unknown_usernames:
                    return {"__error__": True, "error_code": 400,
                            "description": "Bad Request: chat not found"}
                return {
                    "id": FakeAPI.USER_ID, "type": "private",
                    "first_name": "Khoa An", "last_name": "Nguyen",
                    "username": name, "bio": FakeAPI.BIO,
                    "is_premium": True, "language_code": "vi",
                    "accent_color_id": 3, "max_reaction_count": 11,
                }
            if raw.lstrip("-").isdigit() and int(raw) < 0:
                gid = int(raw)
                return {
                    "id": gid, "type": "supergroup", "title": "Miu Store • Anime",
                    "accent_color_id": 3, "max_reaction_count": 11,
                    "permissions": {"can_send_messages": True, "can_send_photos": True,
                                    "can_send_polls": True, "can_invite_users": True},
                }
            if raw.lstrip("-").isdigit():
                uid = int(raw)
                if uid in (FakeAPI.USER_ID, BOT_ID, 8297032840):
                    return {
                        "id": uid, "type": "private",
                        "first_name": "Khoa An", "last_name": "",
                        "username": "chiencuto18m", "bio": FakeAPI.BIO,
                        "is_premium": True, "language_code": "vi",
                        "accent_color_id": 3, "max_reaction_count": 11,
                    }
                return {"__error__": True, "error_code": 400,
                        "description": "Bad Request: chat not found"}
            return {"__error__": True, "error_code": 400,
                    "description": "Bad Request: chat not found"}

        if method == "getUserProfilePhotos":
            return {
                "total_count": 1,
                "photos": [[{"file_id": "AV1", "file_unique_id": "av1",
                            "width": 320, "height": 320}]],
            }

        if method == "sendInvoice":
            try:
                prices = json.loads(params.get("prices") or "[]")
            except json.JSONDecodeError:
                prices = []
            total = sum(int(x.get("amount") or 0) for x in prices if isinstance(x, dict))
            return _msg(chat_id, mid, invoice={
                "title": params.get("title") or "VIP",
                "description": params.get("description") or "",
                "start_parameter": params.get("start_parameter") or "vip",
                "currency": params.get("currency") or "XTR",
                "total_amount": total,
            })

        if method == "getFile":
            return {"file_id": params.get("file_id") or "AV1",
                    "file_unique_id": "av1", "file_size": 512,
                    "file_path": "photos/avatar.png"}

        if method == "setMessageReaction":
            rx = params.get("reaction") or []
            if isinstance(rx, str):
                try:
                    rx = json.loads(rx)
                except json.JSONDecodeError:
                    rx = []
            emoji = rx[0].get("emoji") if rx and isinstance(rx[0], dict) else ""
            if emoji in self.reject:
                return {"__error__": True, "error_code": 400,
                        "description": "Bad Request: REACTION_INVALID"}
            return True

        if method == "getMe":
            return {"id": BOT_ID, "is_bot": True, "first_name": BOT_NAME, "username": BOT_USERNAME,
                    "can_join_groups": True, "can_read_all_group_messages": False,
                    "supports_inline_queries": False}
        if method == "getCustomEmojiStickers":
            ids = params.get("custom_emoji_ids") or []
            if isinstance(ids, str):
                ids = json.loads(ids) if ids.startswith("[") else [ids]
            return [
                {
                    "file_id": f"CE{i}", "file_unique_id": f"ceu{i}", "type": "custom_emoji",
                    "width": 100, "height": 100, "is_animated": True, "is_video": False,
                    "emoji": "🙂", "set_name": "test_pack", "custom_emoji_id": str(i),
                }
                for i in ids
            ]
        if method == "editMessageMedia":
            return _msg(chat_id, params.get("message_id") or mid,
                        photo=[{"file_id": f"F{mid}", "file_unique_id": f"U{mid}",
                                "width": 1200, "height": 630}])
        if method == "getChatMember":
            uid = str(params.get("user_id") or "")
            if uid == str(FakeAPI.GROUP_ADMIN):
                return {"status": "creator", "is_anonymous": False,
                        "user": {"id": int(uid), "is_bot": False, "first_name": "Khoa An"}}
            return {"status": "member",
                    "user": {"id": int(uid or 0), "is_bot": False, "first_name": "Ai đó"}}

        if method == "sendMediaGroup":
            media = params.get("media") or []
            if isinstance(media, str):
                try:
                    media = json.loads(media)
                except json.JSONDecodeError:
                    media = []
            cap = None
            if media and isinstance(media[0], dict):
                cap = media[0].get("caption")
            out = []
            for _ in range(max(1, len(media))):
                out.append(_msg(chat_id, next(self._ids), caption=cap,
                                photo=[{"file_id": f"F{mid}", "file_unique_id": f"U{mid}",
                                        "width": 800, "height": 600}]))
            return out

        if method in ("sendAnimation", "sendVideo", "sendPhoto", "sendDocument", "sendAudio",
                      "sendVoice", "sendMediaGroup"):
            kind = {
                "sendAnimation": "animation",
                "sendVideo": "video",
                "sendPhoto": "photo",
                "sendDocument": "document",
                "sendAudio": "audio",
                "sendVoice": "voice",
            }.get(method, "document")
            media = {"file_id": f"F{mid}", "file_unique_id": f"U{mid}",
                     "width": 320, "height": 320, "duration": 3}
            if kind == "photo":
                media = [dict(media, width=100, height=100)]
            return _msg(chat_id, mid, **{kind: media}, caption=params.get("caption"))
        if method in ("sendMessage", "editMessageText", "editMessageCaption"):
            return _msg(chat_id, params.get("message_id") or mid, text=params.get("text"))
        if method in ("editMessageReplyMarkup", "deleteMessage", "answerCallbackQuery",
                      "sendChatAction", "setMyCommands", "deleteWebhook", "getWebhookInfo",
                      "close", "setMyDescription", "getUpdates"):
            if method == "getUpdates":
                return []
            if method == "getWebhookInfo":
                return {"url": "", "has_custom_certificate": False, "pending_update_count": 0}
            return True
        return True

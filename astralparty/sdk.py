"""Async adaptation of the public upstream SDK signature and SMS login protocol."""

import hashlib
import time
from dataclasses import dataclass

from .errors import AuthExpired, UserError
from .signkeys import SIGN_KEYS

BASE = "https://m-sdk.feimogames.com"
CHANNEL = "test_junhai"
APP_ID = "110001933"
GAME_ID = "120000182"


@dataclass(repr=False)
class LoginTicket:
    sid: str
    token: str


def pick(data, *keys):
    for key in keys:
        value = data
        try:
            for part in key.split("."):
                value = value[int(part)] if isinstance(value, list) else value[part]
            if value:
                return str(value)
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return ""


def signature(params):
    raw = "".join(f"{k}={params[k]}" for k in sorted(params)) + SIGN_KEYS[CHANNEL]
    return hashlib.md5(raw.encode()).hexdigest()


class SDK:
    def __init__(self, http, version="3.2.0", timeout=20):
        self.http, self.version, self.timeout = http, version, timeout

    async def _post(self, path, params, automatic=False):
        body = dict(params)
        # All three SDK endpoints require sign, including /api/init.
        body["sign"] = signature(params)
        headers = {
            "User-Agent": "UnityPlayer/2021.3.45f2 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
            "X-Unity-Version": "2021.3.45f2",
        }
        async with self.http.post(
            BASE + path, data=body, headers=headers, timeout=self.timeout
        ) as res:
            if res.status != 200:
                raise UserError("登录服务暂时不可用，请稍后再试。")
            # SDK responses are small. Avoid unlimited reads and never log the body.
            raw = bytearray()
            async for chunk in res.content.iter_chunked(16384):
                raw.extend(chunk)
                if len(raw) > 128 * 1024:
                    raise UserError("登录服务返回了异常数据。")
            import json

            try:
                out = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                raise UserError("登录服务返回了异常数据。") from None
        if not isinstance(out, dict):
            raise UserError("登录服务返回了异常数据。")
        if str(out.get("ret")) != "1":
            # Map known conditions rather than echoing a raw response containing credentials.
            msg = str(out.get("msg") or "")
            stage = {
                "/api/init": "初始化",
                "/account/sendCode": "发送验证码",
                "/account/authorize": "登录认证",
            }.get(path, "登录服务")
            lowered = msg.casefold()
            if "sign" in lowered:
                if any(w in msg for w in ("不能为空", "缺少", "必填")):
                    raise UserError(f"{stage}请求缺少签名参数，请更新插件。")
                raise UserError(f"{stage}请求签名校验失败，请更新插件或联系管理员。")
            if any(w in lowered for w in ("版本", "clientver", "version")):
                raise UserError(
                    f"{stage}使用的客户端版本不受支持，请联系管理员更新插件。"
                )
            if automatic and any(w in msg for w in ("过期", "重新登录", "token")):
                raise AuthExpired("登录态已失效，请私聊重新登录。")
            for words, text in [
                (("验证码",), "验证码无效或过期，请检查后重试，必要时重新发送。"),
                (("频繁", "频率", "次数"), "请求过于频繁，请稍后再试。"),
                (("手机号",), "手机号未被登录服务接受，请检查手机号。"),
            ]:
                if any(w in msg for w in words):
                    raise UserError(text)
            raise UserError("登录服务拒绝了请求，请稍后重试或重新登录。")
        return out

    async def init(self):
        await self._post(
            "/api/init",
            {
                "app_id": APP_ID,
                "channel": CHANNEL,
                "os": "windows",
                "game_version": self.version,
                "sdk_version": "1.0.0.9",
                "time": str(int(time.time())),
            },
        )

    async def send_code(self, phone):
        await self.init()
        await self._post(
            "/account/sendCode",
            {
                "app_id": APP_ID,
                "channel": CHANNEL,
                "os": "windows",
                "tel_num": phone,
                "time": str(int(time.time())),
                "type": "smslogin",
            },
        )

    async def authorize(self, owner, phone="", code="", token=""):
        await self.init()
        device = hashlib.sha1((owner + "|astralparty-bot").encode()).hexdigest()
        params = {
            "app_id": APP_ID,
            "channel": CHANNEL,
            "sdk_version": "1.0.0.9",
            "device_id": device,
            "time": str(int(time.time())),
            "os": "windows",
            "login_type": "2" if token else "3",
            "os_version": "Windows 10",
            "device_name": "Windows PC",
            "and_id": device,
            "tel_num": phone,
            "smscode": "" if token else code,
        }
        if token:
            params["access_token"] = token
        result = await self._post("/account/authorize", params, automatic=bool(token))
        sid = pick(
            result,
            "content.authorize_code",
            "authorize_code",
            "content.0.authorize_code",
            "content.sid",
        )
        renewed = pick(
            result,
            "content.data.accessToken",
            "content.accessToken",
            "data.accessToken",
            "content.access_token",
            "data.access_token",
            "content.0.accessToken",
            "accessToken",
        )
        if not sid or not (renewed or token):
            raise UserError("登录响应缺少凭据，请联系管理员检查协议。")
        return LoginTicket(sid, renewed or token)

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import aiohttp
from aiohttp import web
import pytest

from astralparty import sdk as sdk_module, service as service_module
from astralparty.errors import AuthExpired, UserError
from astralparty.sdk import SDK, signature
from astralparty.service import PartyService
from astralparty.store import owner_key


@asynccontextmanager
async def http_server(handler):
    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    finally:
        await runner.cleanup()


async def test_sms_sdk_signature_and_token_rotation(monkeypatch):
    requests = []

    async def handler(request):
        params = dict(await request.post())
        requests.append((request.path, params))
        # Match the real SDK contract: initialization also requires sign.
        if "sign" not in params:
            return web.json_response({"ret": 0, "msg": "参数错误:sign参数不能为空"})
        sig = params.pop("sign")
        assert sig == signature(params)
        if request.path == "/account/sendCode":
            assert params["type"] == "smslogin"
            out = {"ret": "1"}
        elif request.path == "/account/authorize":
            if params["login_type"] == "3":
                assert params["smscode"] == "123456" and "access_token" not in params
            else:
                assert params["access_token"] == "old-token" and params["smscode"] == ""
            out = {
                "ret": "1",
                "content": {
                    "authorize_code": "new-sid",
                    "data": {"accessToken": "new-token"},
                },
            }
        else:
            out = {"ret": "1"}
        # Exercise chunked partial responses (content.read(n) alone is insufficient).
        body = json.dumps(out).encode()
        response = web.StreamResponse()
        await response.prepare(request)
        for start in range(0, len(body), 8):
            await response.write(body[start : start + 8])
        await response.write_eof()
        return response

    async with http_server(handler) as base:
        monkeypatch.setattr(sdk_module, "BASE", base)
        async with aiohttp.ClientSession() as http:
            sdk = SDK(http)
            await sdk.send_code("13800000000")
            ticket = await sdk.authorize("owner", phone="13800000000", code="123456")
            assert ticket.sid == "new-sid" and ticket.token == "new-token"
            ticket = await sdk.authorize("owner", token="old-token")
            assert ticket.token == "new-token"
            assert "new-token" not in repr(ticket) and "new-sid" not in repr(ticket)
    assert len(requests) == 6


async def test_sdk_failure_never_echoes_credentials(monkeypatch):
    async def handler(request):
        return web.json_response(
            {
                "ret": "0",
                "msg": "过期 请重新登录 sensitive-secret-phone-token",
                "content": {"accessToken": "TOP_SECRET"},
            }
        )

    async with http_server(handler) as base:
        monkeypatch.setattr(sdk_module, "BASE", base)
        async with aiohttp.ClientSession() as http:
            with pytest.raises(AuthExpired) as error:
                await SDK(http)._post("/account/authorize", {}, automatic=True)
            assert "sensitive-secret" not in str(
                error.value
            ) and "TOP_SECRET" not in str(error.value)


async def test_replay_http_cache_no_login_and_bad_input(
    tmp_path, monkeypatch, replay_bytes
):
    calls = []

    async def handler(request):
        calls.append(request.path)
        return web.Response(body=replay_bytes)

    async with http_server(handler) as base:
        monkeypatch.setattr(service_module, "REPLAY_BASE", base + "/")
        service = PartyService(tmp_path)
        await service.start()
        service.sdk = MagicMock(
            authorize=AsyncMock(side_effect=AssertionError("no login"))
        )
        try:
            review = await service.replay("1234567890123456")
            assert len(review["players"]) == 4
            service.reviews.clear()  # The second request must use the on-disk file.
            assert (await service.replay("1234567890123456"))["rounds"] == 2
            assert len(calls) == 1
            service.sdk.authorize.assert_not_called()
            with pytest.raises(UserError):
                await service.replay("../secret")
            assert len(calls) == 1
        finally:
            await service.close()


async def test_oversized_stream_not_cached(tmp_path, monkeypatch):
    async def handler(request):
        response = web.StreamResponse()
        await response.prepare(request)
        try:
            for _ in range(20):
                await response.write(b"x" * 65536)
        except (ConnectionResetError, aiohttp.ClientConnectionError):
            pass
        return response

    async with http_server(handler) as base:
        monkeypatch.setattr(service_module, "REPLAY_BASE", base + "/")
        service = PartyService(tmp_path, {"max_replay_mb": 1})
        await service.start()
        try:
            with pytest.raises(UserError, match="大小限制"):
                await service.replay("1234567890123456")
            assert not list(service.cache.iterdir())
        finally:
            await service.close()


async def test_invalid_replay_not_cached(tmp_path, monkeypatch):
    async def handler(request):
        return web.Response(body=b"invalid")

    async with http_server(handler) as base:
        monkeypatch.setattr(service_module, "REPLAY_BASE", base + "/")
        service = PartyService(tmp_path)
        await service.start()
        try:
            with pytest.raises(UserError, match="没有可识别"):
                await service.replay("1234567890123456")
            assert not list(service.cache.iterdir())
        finally:
            await service.close()


async def test_service_sms_verification_and_persistence(tmp_path, monkeypatch, player):
    from astralparty.sdk import LoginTicket

    service = PartyService(tmp_path)
    owner = owner_key("qq", "instance", "bot", "user")
    fake = MagicMock()
    fake.alive = True
    fake.connect = AsyncMock()
    fake.login = AsyncMock(return_value=player)
    fake.close = AsyncMock()
    monkeypatch.setattr(service_module, "GameClient", MagicMock(return_value=fake))
    service.sdk = MagicMock(
        send_code=AsyncMock(),
        authorize=AsyncMock(return_value=LoginTicket("sid", "token")),
    )
    try:
        await service.send_code(owner, "13800000000")
        result = await service.verify(owner, "123456")
        assert "登录成功" in result and "UID 1234567" in result
        record = service.store.load(owner)
        assert record["uid"] == 1234567 and record["token"] == "token"
        assert "phone" not in record and "smscode" not in record
        assert service.state(owner).pending is None
        assert (await service.profile(owner))["wins"] == 8
        service.sdk.authorize.assert_awaited_once()
        await service.unbind(owner)
        assert service.store.load(owner) is None
        fake.close.assert_awaited_once()
    finally:
        await service.close()


@pytest.mark.parametrize(
    "server_message,expected,unexpected",
    [
        ("参数错误:sign参数不能为空", "初始化请求缺少签名参数", "版本"),
        ("signError", "初始化请求签名校验失败", "版本"),
        ("SIGN校验失败", "初始化请求签名校验失败", "版本"),
        ("ClientVerErr", "客户端版本不受支持", "签名"),
        ("游戏版本错误", "客户端版本不受支持", "签名"),
    ],
)
async def test_sdk_signature_errors_not_misreported_as_version(
    monkeypatch, server_message, expected, unexpected
):
    async def handler(request):
        return web.json_response(
            {"ret": 0, "msg": server_message, "content": {"accessToken": "TOP_SECRET"}}
        )

    async with http_server(handler) as base:
        monkeypatch.setattr(sdk_module, "BASE", base)
        async with aiohttp.ClientSession() as http:
            with pytest.raises(UserError) as error:
                await SDK(http).init()
            assert expected in str(error.value)
            assert unexpected not in str(error.value)
            assert "TOP_SECRET" not in str(error.value)

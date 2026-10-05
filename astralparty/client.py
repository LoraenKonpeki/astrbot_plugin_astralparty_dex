"""Bounded async TCP RPC client; registers response futures before writing requests."""

import asyncio
import struct
import time

from . import frame, proto_loader as proto
from .errors import SessionConflict, UserError
from .sdk import APP_ID, GAME_ID

MAX_FRAME = 8 * 1024 * 1024
# Independently evidenced send/receive mappings from the RPC handoff report.
RPC_ROUTES = {
    5001: (5002, "protocol.ConnectS2C"),
    5153: (5154, "protocol.GetShowPlayerS2C"),
    5263: (5264, "protocol.GetPlayerSimpleS2C"),
}
KICK_CMD = 1001


class GameClient:
    def __init__(self, host, port=8800, version="3.2.0", timeout=15):
        self.host, self.port, self.version, self.timeout = host, port, version, timeout
        self.reader = self.writer = None
        self.session_id = self.upsn = self.downsn = 0
        self._receiver = self._heartbeats = None
        self._pending = {}
        self._rpc_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self.last_used = time.monotonic()
        self.alive = False
        self.failure = None

    async def connect(self):
        self.reader, self.writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), self.timeout
        )
        self.alive = True
        self._receiver = asyncio.create_task(self._receive())

    async def _receive(self):
        try:
            while self.alive:
                head = await self.reader.readexactly(frame.HEAD_LEN)
                size = frame.peek_length(head)
                if not 0 <= size <= MAX_FRAME:
                    raise UserError("游戏服务器返回了异常协议帧。")
                data = frame.decode(head + await self.reader.readexactly(size))
                self.downsn = data["downsn"] or self.downsn
                if data["cmd_id"] == KICK_CMD:
                    raise SessionConflict(
                        "游戏会话已被服务器结束，可能已在其他客户端登录。请退出游戏后私聊 ~星趴 刷新。"
                    )
                # UPSN=0 is a push, even when CMDID equals a pending response type.
                future = self._pending.get(data["upsn"]) if data["upsn"] else None
                if future is not None and not future.done():
                    future.set_result(data)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            error = (
                exc
                if isinstance(exc, UserError)
                else UserError("游戏连接已断开，请稍后重试。")
            )
            self.failure = error
            for future in tuple(self._pending.values()):
                if not future.done():
                    future.set_exception(error)
        finally:
            self.alive = False
            if self.writer:
                self.writer.close()
            if self._heartbeats:
                self._heartbeats.cancel()

    async def _write(self, cmd, body=b"", pending=None):
        async with self._send_lock:
            if not self.alive or self.writer is None:
                raise UserError("游戏连接已断开，请稍后重试。")
            self.upsn += 1
            if self.upsn >= 2147483647:
                self.upsn = 100
            sn = self.upsn
            if pending is not None:
                # Register under the allocated UPSN before a server can return an immediate reply.
                self._pending[sn] = pending
            self.writer.write(
                frame.encode(cmd, body, session_id=self.session_id, upsn=sn, downsn=0)
            )
            await asyncio.wait_for(self.writer.drain(), self.timeout)
            return sn

    async def rpc(self, cmd, msg, response):
        if cmd not in RPC_ROUTES or RPC_ROUTES[cmd][1] != response:
            raise ValueError("unsupported RPC route")
        response_cmd = RPC_ROUTES[cmd][0]
        async with self._rpc_lock:
            self.last_used = time.monotonic()
            future = asyncio.get_running_loop().create_future()
            try:
                await self._write(cmd, msg.SerializeToString(), pending=future)
                data = await asyncio.wait_for(future, self.timeout)
                # Errors can use a different CMDID; only decode successful expected responses.
                if data["err"] == 10020:
                    raise SessionConflict(
                        "账号已在其他客户端在线。插件未自动重试；请退出游戏后私聊 ~星趴 刷新。"
                    )
                if data["err"] == 10012:
                    raise UserError(
                        "游戏客户端协议版本不受支持，请联系管理员更新插件。"
                    )
                if data["err"]:
                    raise UserError(
                        f"游戏服务拒绝请求（错误码 {data['err']}），请稍后重试。"
                    )
                if data["cmd_id"] != response_cmd:
                    raise UserError("游戏响应类型与请求不匹配，请联系管理员检查协议。")
                result = proto.new_msg(response)
                result.ParseFromString(data["body"])
                return result
            except asyncio.TimeoutError:
                await self.close()
                raise UserError("游戏请求超时，请稍后重试。") from None
            except asyncio.CancelledError:
                await self.close()
                raise
            except Exception:
                # Discard malformed payloads, partial writes and late replies after all failures.
                await self.close()
                raise
            finally:
                for sn, candidate in list(self._pending.items()):
                    if candidate is future:
                        self._pending.pop(sn, None)
                if not future.done():
                    future.cancel()
                elif not future.cancelled():
                    future.exception()

    async def login(self, sid, owner):
        import hashlib

        request = proto.new_msg("protocol.ConnectC2S")
        request.publicKey = proto.PUBLIC_KEY
        request.auth = proto.AUTH_TYPE["China"]
        request.clientVer = self.version
        request.china.gameId = GAME_ID
        request.china.channelId = "2"
        request.china.appId = APP_ID
        request.china.sid = sid
        request.china.deviceId = hashlib.sha1(
            (owner + "|astralparty-bot").encode()
        ).hexdigest()
        request.china.extra = "bn"
        response = await self.rpc(5001, request, "protocol.ConnectS2C")
        self.session_id = response.sessionId
        self._heartbeats = asyncio.create_task(self._heartbeat())
        return response.player

    async def _heartbeat(self):
        try:
            while self.alive:
                await self._write(
                    5003,
                    b"\x09"
                    + struct.pack("<q", int(time.monotonic() * 1000) & 0x7FFFFFFF),
                )
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass
        except Exception:
            await self.close()

    async def show(self, uid):
        request = proto.new_msg("protocol.GetShowPlayerC2S")
        request.player_id = uid
        return await self.rpc(5153, request, "protocol.GetShowPlayerS2C")

    async def simple(self, uid):
        request = proto.new_msg("protocol.GetPlayerSimpleC2S")
        request.player_id = uid
        return await self.rpc(5263, request, "protocol.GetPlayerSimpleS2C")

    async def close(self):
        self.alive = False
        tasks = [
            t
            for t in (self._receiver, self._heartbeats)
            if t and t is not asyncio.current_task()
        ]
        for task in tasks:
            task.cancel()
        if self.writer:
            self.writer.close()
            try:
                await asyncio.wait_for(self.writer.wait_closed(), 2)
            except (OSError, asyncio.TimeoutError):
                pass
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for future in set(self._pending.values()):
            if not future.done():
                future.set_exception(UserError("游戏连接已关闭，请稍后重试。"))

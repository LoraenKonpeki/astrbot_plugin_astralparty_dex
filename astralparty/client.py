"""Bounded async TCP RPC client; registers response futures before writing requests."""

import asyncio
import struct
import time

from . import frame, proto_loader as proto
from .errors import SessionConflict, UserError
from .sdk import APP_ID, GAME_ID

MAX_FRAME = 8 * 1024 * 1024


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
                future = self._pending.get(data["cmd_id"])
                if future is not None and not future.done():
                    future.set_result(data)
        except asyncio.CancelledError:
            pass
        except Exception:
            for future in set(self._pending.values()):
                if not future.done():
                    future.set_exception(UserError("游戏连接已断开，请稍后重试。"))
        finally:
            self.alive = False

    async def _write(self, cmd, body=b""):
        async with self._send_lock:
            if not self.alive or self.writer is None:
                raise UserError("游戏连接已断开，请稍后重试。")
            self.upsn += 1
            self.writer.write(
                frame.encode(
                    cmd,
                    body,
                    session_id=self.session_id,
                    upsn=self.upsn,
                    downsn=self.downsn,
                )
            )
            await asyncio.wait_for(self.writer.drain(), self.timeout)

    async def rpc(self, cmd, msg, response):
        async with self._rpc_lock:
            self.last_used = time.monotonic()
            future = asyncio.get_running_loop().create_future()
            self._pending[cmd] = self._pending[cmd + 1] = future
            try:
                await self._write(cmd, msg.SerializeToString())
                data = await asyncio.wait_for(future, self.timeout)
                if data["err"] == 10020:
                    raise SessionConflict(
                        "账号已在其他客户端在线。插件未自动重试；请退出游戏后私聊 /星趴 刷新。"
                    )
                if data["err"]:
                    raise UserError(
                        f"游戏服务拒绝请求（错误码 {data['err']}），请稍后重试。"
                    )
                result = proto.new_msg(response)
                result.ParseFromString(data["body"])
                return result
            except asyncio.TimeoutError:
                # A late response must never satisfy the next RPC of the same command ID.
                await self.close()
                raise UserError("游戏请求超时，请稍后重试。") from None
            except asyncio.CancelledError:
                await self.close()
                raise
            finally:
                self._pending.pop(cmd, None)
                self._pending.pop(cmd + 1, None)
                if not future.done():
                    future.cancel()
                elif not future.cancelled():
                    future.exception()  # Consume an exception if the writer failed first.

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
                    5003, b"\x09" + struct.pack("<Q", int(time.monotonic() * 1000))
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

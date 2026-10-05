import asyncio
from contextlib import asynccontextmanager

import pytest

from astralparty import frame
from astralparty.client import GameClient
from astralparty.errors import UserError, SessionConflict
from astralparty.proto_loader import new_msg
from astralparty.profile import profile_from_player, public_profile
from astralparty.review import build_review
from astralparty.formatting import match_text, review_text, split_text


@asynccontextmanager
async def tcp_server(handler):
    tasks = set()

    async def handle(reader, writer):
        tasks.add(asyncio.current_task())
        try:
            await handler(reader, writer)
        finally:
            writer.close()
            await writer.wait_closed()
            tasks.discard(asyncio.current_task())

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        server.close()
        await server.wait_closed()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def read_frame(reader):
    head = await reader.readexactly(35)
    return frame.decode(head + await reader.readexactly(frame.peek_length(head)))


async def test_real_tcp_fragmented_immediate_reply_and_push(player):
    async def handler(reader, writer):
        request = await read_frame(reader)
        assert request["cmd_id"] == 5001
        msg = new_msg("protocol.ConnectC2S")
        msg.ParseFromString(request["body"])
        assert msg.china.extra == "bn" and msg.china.sid == "sid"
        reply = new_msg("protocol.ConnectS2C")
        reply.sessionId = 17
        reply.player.CopyFrom(player)
        packet = frame.encode(5002, reply.SerializeToString(), upsn=request["upsn"])
        # Fragmented headers/bodies and unrelated pushes must work.
        writer.write(frame.encode(5100, b"ignored"))
        writer.write(packet[:7])
        await writer.drain()
        await asyncio.sleep(0.01)
        writer.write(packet[7:])
        await writer.drain()
        while True:
            request = await read_frame(reader)
            if request["cmd_id"] == 5153:
                reply = new_msg("protocol.GetShowPlayerS2C")
                reply.showData.statistics.fightCount = 3
                writer.write(
                    frame.encode(5154, reply.SerializeToString(), upsn=request["upsn"])
                )
                await writer.drain()

    async with tcp_server(handler) as port:
        c = GameClient("127.0.0.1", port, timeout=1)
        try:
            await c.connect()
            p = await c.login("sid", "owner")
            assert p.nick == player.nick and c.session_id == 17
            show = await c.show(7654321)
            assert show.showData.statistics.fightCount == 3
        finally:
            await c.close()
        assert not c.alive and c._receiver.done() and c._heartbeats.done()


async def test_conflict_returns_without_automatic_retry():
    received = []

    async def handler(reader, writer):
        req = await read_frame(reader)
        received.append(req["cmd_id"])
        writer.write(frame.encode(5001, b"", err=10020, upsn=req["upsn"]))
        await writer.drain()
        await reader.read()

    async with tcp_server(handler) as port:
        c = GameClient("127.0.0.1", port, timeout=1)
        try:
            await c.connect()
            with pytest.raises(SessionConflict, match="未自动重试"):
                await c.login("sid", "owner")
        finally:
            await c.close()
    assert received == [5001]


async def test_timeout_closes_connection_prevents_late_reply():
    async def handler(reader, writer):
        await read_frame(reader)
        await asyncio.sleep(1)

    async with tcp_server(handler) as port:
        c = GameClient("127.0.0.1", port, timeout=0.05)
        await c.connect()
        with pytest.raises(UserError, match="超时"):
            await c.show(7654321)
        assert not c.alive
        with pytest.raises(UserError, match="断开"):
            await c.show(7654321)
        await c.close()


async def test_oversized_frame_rejected():
    async def handler(reader, writer):
        await read_frame(reader)
        head = frame.HEAD_STRUCT.pack(9 * 1024 * 1024, 0, 5154, 1, 0, 0, 0, 0, 0)
        writer.write(head)
        await writer.drain()
        await reader.read()

    async with tcp_server(handler) as port:
        c = GameClient("127.0.0.1", port, timeout=1)
        await c.connect()
        with pytest.raises(UserError, match="异常协议帧"):
            await c.show(7654321)
        await c.close()


def test_profile_parser_and_public_response_shape(player):
    p = profile_from_player(player)
    assert p["total"] == 20 and p["winrate"] == 40 and p["recent"][0]["rank"] == 1
    assert p["recent"][0]["mapType"] == 2
    assert p["recent"][0]["map"] == "合作挑战"
    assert p["heroes"][0]["n"] == 12 and p["skins"]["sum"] == 2
    show = new_msg("protocol.GetShowPlayerS2C")
    show.showData.statistics.fightCount = 10
    show.showData.statistics.winFightCount = 3
    simple = new_msg("protocol.GetPlayerSimpleS2C")
    simple.PlayerInfo.name = "另一个玩家"
    simple.PlayerInfo.lv = 7
    result = public_profile(show, simple, 7654321)
    assert (
        result["nick"] == "另一个玩家"
        and result["level"] == 7
        and result["winrate"] == 30
    )


def test_synthetic_replay_rounds_chips_and_formatting(replay_bytes):
    review = build_review(replay_bytes, "1234567890123456")
    assert (
        len(review["players"]) == 4
        and review["rounds"] == 2
        and review["map_id"] == 82013
    )
    p = review["players"][0]
    assert (
        p["totals"]["dmg"] == 200
        and p["totals"]["gold"] == 70
        and p["chips"][0]["id"] == 50001
    )
    assert "UID 1234567" in match_text(review)
    text = review_text(review, 1234567)
    assert "第 1 轮" in text and "筹码" in text and "来源" in text
    with pytest.raises(UserError, match="不在"):
        review_text(review, 9876543)


def test_reply_chunk_size():
    text = ("x" * 5000) + "\n" + "\n".join("短行" * 20 for _ in range(100))
    assert all(len(p) <= 1800 for p in split_text(text))


async def test_same_cmd_push_and_stale_sequence_cannot_satisfy_request():
    async def handler(reader, writer):
        request = await read_frame(reader)
        wrong = new_msg("protocol.GetShowPlayerS2C")
        wrong.showData.statistics.fightCount = 999
        writer.write(frame.encode(5154, wrong.SerializeToString(), upsn=0))
        writer.write(
            frame.encode(5154, wrong.SerializeToString(), upsn=request["upsn"] + 100)
        )
        correct = new_msg("protocol.GetShowPlayerS2C")
        correct.showData.statistics.fightCount = 7
        writer.write(
            frame.encode(5154, correct.SerializeToString(), upsn=request["upsn"])
        )
        await writer.drain()
        await reader.read()

    async with tcp_server(handler) as port:
        c = GameClient("127.0.0.1", port, timeout=1)
        try:
            await c.connect()
            assert (await c.show(7654321)).showData.statistics.fightCount == 7
        finally:
            await c.close()


async def test_matched_sequence_wrong_success_cmd_rejected():
    async def handler(reader, writer):
        request = await read_frame(reader)
        writer.write(frame.encode(5264, b"", upsn=request["upsn"]))
        await writer.drain()
        await reader.read()

    async with tcp_server(handler) as port:
        c = GameClient("127.0.0.1", port, timeout=1)
        await c.connect()
        with pytest.raises(UserError, match="响应类型"):
            await c.show(7654321)
        assert not c.alive
        await c.close()


async def test_error_response_uses_sequence_before_payload_or_cmd():
    async def handler(reader, writer):
        request = await read_frame(reader)
        writer.write(
            frame.encode(
                5010, b"invalid-success-payload", upsn=request["upsn"], err=10012
            )
        )
        await writer.drain()
        await reader.read()

    async with tcp_server(handler) as port:
        c = GameClient("127.0.0.1", port, timeout=1)
        await c.connect()
        with pytest.raises(UserError, match="协议版本"):
            await c.show(7654321)
        await c.close()


async def test_sequence_wrap_and_downsn_not_echoed():
    async def handler(reader, writer):
        request = await read_frame(reader)
        assert request["upsn"] == 100 and request["downsn"] == 0
        writer.write(
            frame.encode(
                5154, new_msg("protocol.GetShowPlayerS2C").SerializeToString(), upsn=100
            )
        )
        await writer.drain()
        await reader.read()

    async with tcp_server(handler) as port:
        c = GameClient("127.0.0.1", port, timeout=1)
        c.upsn, c.downsn = 2147483646, 123
        await c.connect()
        await c.show(7654321)
        await c.close()


async def test_kick_push_ends_session_without_waiting_for_timeout():
    async def handler(reader, writer):
        await read_frame(reader)
        writer.write(frame.encode(1001, b"", upsn=0))
        await writer.drain()
        await reader.read()

    async with tcp_server(handler) as port:
        c = GameClient("127.0.0.1", port, timeout=10)
        await c.connect()
        with pytest.raises(SessionConflict, match="服务器结束"):
            await asyncio.wait_for(c.show(7654321), 1)
        assert not c.alive and isinstance(c.failure, SessionConflict)
        await c.close()

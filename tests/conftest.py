import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from astralparty.proto_loader import new_msg


@pytest.fixture
def player():
    p = new_msg("model.Player")
    p.id, p.nick, p.level = 1234567, "测试玩家", 27
    p.task.condition[14], p.task.condition[13] = 20, 8
    p.showPlayer.praiseNum = 5
    p.roleCard[101].isBreakThrough = True
    p.roleCard[101].pve_strengthen.level = 6
    for index, count in [(17, 12), (18, 4)]:
        pair = p.task.condition1[index].params.add()
        pair.param, pair.param1 = count, 101
    p.bag_items.add(item_id=100101001)
    p.bag_items.add(item_id=100101002)
    record = p.showPlayer.record.add()
    record.time, record.replayId = 1700000000, "1234567890123456"
    data = record.data.add()
    data.playerId, data.name, data.heroId, data.rank, data.mapType = (
        p.id,
        p.nick,
        101,
        1,
        2,
    )
    return p


def varint(number):
    out = bytearray()
    while number > 127:
        out.append((number & 127) | 128)
        number >>= 7
    out.append(number)
    return bytes(out)


@pytest.fixture
def replay_bytes():
    frames = []
    for rnd in range(2):
        room = new_msg("model.Room")
        room.id, room.name, room.map_id, room.difficulty = 1, "match", 82013, 2
        for index in range(4):
            p = room.players.add()
            p.id, p.nick, p.slot, p.level = 1234567 + index, f"玩家{index}", index, 10
            p.hero.hero_id, p.hero.round = 101, rnd
            c = p.hero.cond
            c.kill_count, c.total_damage, c.total_injured = (
                rnd + 1,
                (rnd + 1) * 100,
                (rnd + 1) * 20,
            )
            c.gold, c.initGoldCount, c.movePoint = (rnd + 1) * 30, 10, (rnd + 1) * 5
            p.hero.buffs[1].buff_id = 5000101
        data = room.SerializeToString()
        frames.append(b"\x12" + varint(len(data)) + data)
    return b"\x08\x01" + b"".join(frames)

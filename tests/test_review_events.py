import struct
from types import SimpleNamespace

import pytest

from astralparty.proto_loader import new_msg
from astralparty.replay import Packet
from astralparty.review import _offer_events, _player_chip_events, _split_acquisitions


def packet(ids, sn=1):
    payload = (
        b"\x12\x0c"
        + struct.pack("<III", *ids)
        + b"\x2d\x02\x00\x00\x00\x35\x02\x00\x00\x00"
    )
    return Packet(5211, 1234567, payload, sn, sn, 2)


def replay(packets, held=0, mimi=False):
    frames = []
    for index in range(4):
        room = new_msg("model.Room")
        p = room.players.add()
        p.id = 1234567
        p.hero.hero_id = 108 if mimi else 101
        p.hero.lv = 1 if index < 2 else 2
        p.hero.round = 8
        if held:
            p.hero.buffs[1].buff_id = held * 100 + 1
        if mimi:
            p.hero.buffs[2].buff_id = 1081201
            p.hero.buffs[2].progress = 25 if index == 2 else 0
        frames.append({"room": room})
    return SimpleNamespace(
        packets=packets, frames=frames, mission_states=lambda: [{}] * 4
    )


def test_same_snapshot_double_acquisition_has_separate_chains():
    groups = [[50008, 50015, 50013], [50031, 50007, 50002], [50028, 50042, 50033]]
    rp = replay([packet(ids, i + 1) for i, ids in enumerate(groups)], mimi=True)
    events = _player_chip_events(rp, 1234567, {50002: 3, 50028: 3}, {1234567: {2: []}})
    assert [e["refresh"] for e in events] == [1, 0]
    assert [[g["cands"] for g in e["chain"]] for e in events] == [
        groups[:2],
        groups[2:],
    ]
    assert events[1]["source"] == "extra"
    assert "米米潜能（计数达到25）" in events[1]["source_candidates"]
    assert events[0]["chain"][-1]["picked"] == 2
    assert events[1]["chain"][-1]["picked"] == 0


@pytest.mark.parametrize("held,hint", [(50083, "循环往复"), (50067, "彩羽手环")])
def test_extra_chip_mechanisms_keep_events_without_claiming_source(held, hint):
    rp = replay(
        [packet([50001, 50002, 50003], 1), packet([50004, 50005, 50006], 2)], held=held
    )
    events = _player_chip_events(
        rp, 1234567, {held: 0, 50001: 3, 50004: 3}, {1234567: {2: []}}
    )
    extra = next(e for e in events if e["id"] == 50004)
    assert extra["source"] == "extra" and hint in extra["source_candidates"]
    assert len(extra["chain"]) == 1 and extra["refresh"] == 0
    assert all(e["source"] != "cycle" for e in events)


def test_current_chip_is_not_its_own_extra_source():
    rp = replay([packet([50083, 50002, 50003])])
    events = _player_chip_events(rp, 1234567, {50083: 3}, {1234567: {2: []}})
    assert not events[0]["source_candidates"]


def test_refreshed_candidates_can_return_to_same_group_but_duplicate_packet_is_ignored():
    a, b = [50001, 50002, 50003], [50004, 50005, 50006]
    rp = replay([packet(a, 1), packet(a, 1), packet(b, 2), packet(a, 3)])
    offers = _offer_events(rp, 1234567)
    assert [g["cands"] for g in offers[0]["chain"]] == [a, b, a]
    assignment = _split_acquisitions(offers, {50001: 3})
    assert len(assignment[50001]["chain"]) == 3


def test_ambiguous_terminal_is_not_copied_to_two_acquisitions():
    offers = _offer_events(replay([packet([50001, 50002, 50003])]), 1234567)
    assert _split_acquisitions(offers, {50001: 3, 50002: 3}) == {}

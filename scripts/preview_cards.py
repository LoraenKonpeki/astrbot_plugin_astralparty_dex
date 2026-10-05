"""Render synthetic card previews offline; requires requirements-dev.txt, no game login."""

import sys
from pathlib import Path

from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from astralparty.cards import (  # noqa: E402
    CARD_TEMPLATE,
    heroes_card,
    match_card,
    profile_card,
    records_card,
    review_card,
    skins_card,
)
from astralparty.profile import profile_from_player  # noqa: E402
from astralparty.proto_loader import new_msg  # noqa: E402


def demo_data():
    player = new_msg("model.Player")
    player.id, player.nick, player.level = 1234567, "示例玩家", 27
    player.task.condition[14], player.task.condition[13] = 128, 46
    player.showPlayer.praiseNum = 35
    heroes = [107, 101, 112, 119, 302, 128, 123, 115]
    for i, hid in enumerate(heroes):
        player.roleCard[hid].isBreakThrough = i % 2 == 0
        player.roleCard[hid].pve_strengthen.level = 8 - i
        for cond, count in [(17, 35 - i * 4), (18, 12 - i)]:
            pair = player.task.condition1[cond].params.add()
            pair.param, pair.param1 = count, hid
        player.bag_items.add(item_id=int(f"100{hid:03d}001"))
        if i % 2 == 0:
            player.bag_items.add(item_id=int(f"100{hid:03d}002"))
    for i, hid in enumerate(heroes[:5]):
        record = player.showPlayer.record.add()
        record.time, record.replayId = 1791183600 - i * 3600, str(1234567890123456 + i)
        p = record.data.add()
        p.playerId, p.name, p.heroId, p.rank, p.mapType = (
            player.id,
            player.nick,
            hid,
            i % 4 + 1,
            2 if i % 2 == 0 else 1,
        )
    profile = profile_from_player(player)
    profile["fetched_at"] = 1791187200
    review = {
        "replay_id": "1234567890123456",
        "map_id": 82013,
        "difficulty": 3,
        "rounds": 18,
        "players": [],
    }
    for i, hid in enumerate(heroes[:4]):
        rounds = [
            {
                "round": n,
                "kill": n % 3,
                "dmg": n * 12,
                "inj": n * 8,
                "gold": n * 15,
                "move": n + 2,
                "died": n == 3,
            }
            for n in range(1, 6)
        ]
        review["players"].append(
            {
                "uid": 1234567 + i,
                "nick": ["示例玩家", "星币收藏家", "今天也想第一名", "派对旅人"][i],
                "hero_id": hid,
                "level": 27 + i,
                "rounds": rounds,
                "totals": {
                    "kill": 5 + i,
                    "dmg": 256 - i * 20,
                    "inj": 180 + i * 10,
                    "gold": 420 - i * 30,
                    "move": 88 + i * 3,
                    "died": i,
                },
                "chips": [
                    {"name": "招财猫"},
                    {"name": "循环往复"},
                    {"name": "攻守兼备"},
                ],
                "chip_events": [
                    {
                        "round": 2,
                        "name": "招财猫",
                        "source": "star",
                        "refresh": 1,
                        "candidates_name": ["招财猫", "攻守兼备", "循环往复"],
                    }
                ],
            }
        )
    metadata = {"players": [{"id": 1234567 + i, "rank": i + 1} for i in range(4)]}
    return profile, review, metadata


def main():
    destination = ROOT / "output/playwright"
    destination.mkdir(parents=True, exist_ok=True)
    p, r, m = demo_data()
    models = {
        "profile": profile_card(p),
        "records": records_card(p),
        "heroes": heroes_card(p),
        "skins": skins_card(p),
        "match": match_card(r, m),
        "review": review_card(r, 1234567),
    }
    template = Environment(undefined=StrictUndefined, autoescape=True).from_string(
        CARD_TEMPLATE
    )
    for name, model in models.items():
        path = destination / f"{name}-1.html"
        path.write_text(template.render(card=model), encoding="utf-8")
        print(path)


if __name__ == "__main__":
    main()

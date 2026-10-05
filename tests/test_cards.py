import base64
import hashlib
import json
from html.parser import HTMLParser

import pytest
from jinja2 import Environment, StrictUndefined

from astralparty import cards
from astralparty.paths import asset
from astralparty.profile import profile_from_player
from astralparty.review import build_review


class Images(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        if tag == "img":
            self.sources.append(dict(attrs).get("src", ""))


def render(model):
    return (
        Environment(undefined=StrictUndefined, autoescape=True)
        .from_string(
            cards.REVIEW_TEMPLATE
            if model.get("kind") == "review"
            else cards.CARD_TEMPLATE
        )
        .render(card=model)
    )


def test_all_pixel_assets_match_source_manifest():
    manifest = json.loads(asset("assets", "pixel", "source.json").read_text())
    assert len(manifest["files"]) == 32
    for hid, info in manifest["files"].items():
        raw = base64.b64decode(cards.pixel(int(hid)).split(",", 1)[1])
        assert raw.startswith(b"\x89PNG\r\n\x1a\n")
        assert hashlib.sha256(raw).hexdigest() == info["sha256"]


@pytest.mark.parametrize("hid", [None, "../../secret", 0, 999, 9999])
def test_unknown_sprite_returns_placeholder(hid):
    assert cards.pixel(hid) == ""


def test_all_views_use_correct_character_assets(player, replay_bytes):
    p = profile_from_player(player)
    r = build_review(replay_bytes, "1234567890123456")
    models = [
        cards.profile_card(p),
        cards.records_card(p),
        cards.heroes_card(p),
        cards.skins_card(p),
        cards.match_card(r),
        cards.review_card(r),
    ]
    for model in models:
        parser = Images()
        parser.feed(render(model))
        assert cards.pixel(101) in parser.sources
        assert all(src.startswith("data:image/png;base64,") for src in parser.sources)


def test_html_escapes_untrusted_nicknames_and_skin_names(player):
    p = profile_from_player(player)
    attack = '<img src="https://untrusted.test/track" onerror="alert(1)">'
    p["nick"] = attack
    p["skins"]["list"][0]["skins"][0]["name"] = attack
    for model in [cards.profile_card(p), cards.skins_card(p)]:
        html = render(model)
        parser = Images()
        parser.feed(html)
        assert "&lt;img" in html
        assert not any("untrusted.test" in src for src in parser.sources)
        assert "script" not in parser.tags


def test_unknown_hero_is_not_replaced_with_another_character(player):
    p = profile_from_player(player)
    p["recent"][0]["heroId"] = 129
    p["recent"][0]["hero"] = "新角色"
    model = cards.records_card(p)
    assert model["rows"][0]["avatar"] == ""
    assert model["rows"][0]["title"] == "新角色"
    assert "placeholder" in render(model)


def test_single_card_keeps_every_skin_and_event_detail():
    original = cards.row(
        "皮肤",
        101,
        chips=[{"label": f"皮肤{i}", "owned": True} for i in range(61)],
        details=[{"label": f"来源{i}", "value": "测试"} for i in range(25)],
    )
    model = cards.card("测试", "", "TEST", rows=[original])
    html = render(model)
    for i in range(61):
        assert f"皮肤{i}</span>" in html
    for i in range(25):
        assert f"来源{i}</strong>" in html


def test_complete_records_have_continuous_numbering(player):
    p = profile_from_player(player)
    p["recent"] *= 10
    model = cards.records_card(p)
    assert len(model["rows"]) == 10
    assert [r["ordinal"] for r in model["rows"]] == [f"{i:02d}" for i in range(1, 11)]
    assert "页" not in render(model)


def test_full_collections_and_rounds_are_not_truncated(player, replay_bytes):
    p = profile_from_player(player)
    p["heroes"] = [{**p["heroes"][0], "id": 101 + i, "owned": True} for i in range(32)]
    p["skins"]["list"] = [
        {**p["skins"]["list"][0], "id": 101 + i, "owns": True} for i in range(32)
    ]
    assert len(cards.heroes_card(p)["rows"]) == 32
    assert len(cards.skins_card(p)["rows"]) == 32
    review = build_review(replay_bytes, "1234567890123456")
    target = review["players"][0]
    target["rounds"] = [{**target["rounds"][0], "round": i} for i in range(1, 41)]
    target["chip_events"] = []
    model = cards.review_card(review)
    assert len(model["timeline"]) == 40
    assert model["timeline"][-1]["round"] == 40
    assert "下一页" not in render(model)


def test_empty_cards_and_missing_review_records(player, replay_bytes):
    p = profile_from_player(player)
    p["recent"] = []
    model = cards.records_card(p)
    assert not model["rows"] and "暂无可查询数据" in render(model)
    review = build_review(replay_bytes, "1234567890123456")
    review.update(players=[], rounds=0)
    assert "回放缺少逐轮记录" in render(cards.review_card(review))


def test_chip_assets_cover_static_table_with_verified_hashes():
    manifest = json.loads(asset("assets", "chips", "source.json").read_text())
    chips = json.loads(asset("assets", "data", "chips.json").read_text())
    assert {int(i) for i in manifest["files"]} == {c["id"] for c in chips}
    for cid, info in manifest["files"].items():
        raw = base64.b64decode(cards.chip_icon(cid).split(",", 1)[1])
        assert hashlib.sha256(raw).hexdigest() == info["sha256"]
    assert cards.chip_icon("../../secret") == cards.chip_icon(99999) == ""


def test_full_review_keeps_four_players_refresh_chains_and_event_only_rounds(
    replay_bytes,
):
    from astralparty.formatting import review_text

    review = build_review(replay_bytes, "1234567890123456")
    for index, player in enumerate(review["players"]):
        player["chip_events"] = [
            {
                "id": 50001,
                "name": "拳击手套-初级",
                "quality": "蓝",
                "round": 0 if index == 0 else 3,
                "source": "shop",
                "price": 10,
                "refresh": 2,
                "arg": 10,
                "chain": [
                    {
                        "cands": [50001, 50002, 50003],
                        "names": ["拳击手套-初级", "拳击手套-中级", "拳击手套-高级"],
                        "picked": -1,
                    },
                    {
                        "cands": [50004, 50005, 50006],
                        "names": ["速度轮滑-初级", "速度轮滑-中级", "速度轮滑-高级"],
                        "picked": -1,
                    },
                    {
                        "cands": [50001, 50007, 50008],
                        "names": ["拳击手套-初级", "夹心饼干-一般", "夹心饼干-可口"],
                        "picked": 0,
                    },
                ],
            }
        ]
    review["players"].reverse()
    model = cards.review_card(review)
    assert [p["uid"] for p in model["players"]] == [1234567 + i for i in range(4)]
    assert [r["round"] for r in model["timeline"]] == [0, 1, 2, 3]
    first = model["timeline"][0]["cells"][0]["events"][0]
    assert len(first["groups"]) == 3
    assert [c["picked"] for g in first["groups"] for c in g["candidates"]] == [
        False
    ] * 6 + [True, False, False]
    assert first["price"] == 10
    html = render(model)
    assert all(f"玩家{i}" in html for i in range(4))
    assert "刷新 2" in html and "实付 10" in html
    text = review_text(review)
    assert all(f"UID {1234567 + i}" in text for i in range(4))
    assert text.count("初始候选") == text.count("刷新 2：") == 4


def test_review_html_escapes_candidate_names(replay_bytes):
    review = build_review(replay_bytes, "1234567890123456")
    review["players"][0]["nick"] = "<script>attack</script>"
    review["players"][0]["chip_events"][0]["chain"] = [
        {"cands": [50001], "names": ["<img src=x onerror=attack>"], "picked": 0}
    ]
    html = render(cards.review_card(review))
    assert "<script>" not in html and "&lt;img" in html

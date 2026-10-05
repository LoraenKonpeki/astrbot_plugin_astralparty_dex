import base64
import hashlib
import json
from html.parser import HTMLParser

import pytest
from jinja2 import Environment, StrictUndefined

from astralparty import cards
from astralparty.errors import UserError
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
        .from_string(cards.CARD_TEMPLATE)
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
        cards.review_card(r, 1234567),
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


def test_card_pagination_keeps_every_skin_and_event_detail():
    original = cards.row(
        "皮肤",
        101,
        chips=[{"label": f"皮肤{i}", "owned": True} for i in range(61)],
        details=[{"label": f"来源{i}", "value": "测试"} for i in range(25)],
    )
    model = cards.card("测试", "", "TEST", rows=[original])
    pages = list(cards.card_pages(model))
    rows = [row for page in pages for row in page["rows"]]
    assert [c["label"] for row in rows for c in row["chips"]] == [
        f"皮肤{i}" for i in range(61)
    ]
    assert [d["label"] for row in rows for d in row["details"]] == [
        f"来源{i}" for i in range(25)
    ]
    for page in pages:
        render(page)


def test_page_two_numbering_matches_text(player):
    p = profile_from_player(player)
    p["recent"] *= 10
    model = cards.records_card(p, 2)
    assert model["rows"][0]["ordinal"] == "06" and model["rows"][-1]["ordinal"] == "10"
    assert model["page"] == "2 / 2"


def test_empty_cards_and_invalid_review_player(player, replay_bytes):
    p = profile_from_player(player)
    p["recent"] = []
    model = cards.records_card(p)
    assert not model["rows"] and "暂无可查询数据" in render(model)
    with pytest.raises(UserError, match="不在"):
        cards.review_card(build_review(replay_bytes, "1234567890123456"), 7654321)

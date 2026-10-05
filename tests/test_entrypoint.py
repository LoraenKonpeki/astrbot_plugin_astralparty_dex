"""Adapter-level contract tests with minimal AstrBot API doubles (not live platform tests)."""

import importlib.util
import logging
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from astralparty.app import PartyApp, Reply
from astralparty.help import GENERAL


class Result:
    def __init__(self, kind, value):
        self.kind, self.value, self.stopped = kind, value, False

    def stop_event(self):
        self.stopped = True
        return self


class Event:
    unified_msg_origin = "instance:GroupMessage:group"

    def __init__(self, message, private=False):
        self.message, self.private, self.stopped = message, private, False

    def stop_event(self):
        self.stopped = True

    def get_platform_name(self):
        return "qq"

    def get_platform_id(self):
        return "instance"

    def get_self_id(self):
        return "bot"

    def get_sender_id(self):
        return "user"

    def get_message_str(self):
        return self.message

    def is_private_chat(self):
        return self.private

    def plain_result(self, text):
        return Result("text", text)

    def image_result(self, path):
        return Result("image", path)


@pytest.fixture
def entrypoint(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    package = types.ModuleType("plugin_test")
    package.__path__ = [str(root)]
    api = types.ModuleType("astrbot.api")
    api.AstrBotConfig = dict
    api.logger = logging.getLogger("astrbot-test")
    events = types.ModuleType("astrbot.api.event")
    events.AstrMessageEvent = Event

    def decorate(*args, **kwargs):
        return lambda func: func

    events.filter = types.SimpleNamespace(command=decorate)
    star = types.ModuleType("astrbot.api.star")

    class Star:
        def __init__(self, context):
            self.context = context

    star.Star, star.Context, star.register = Star, object, decorate
    star.StarTools = object
    for name, module in [
        ("plugin_test", package),
        ("astrbot", types.ModuleType("astrbot")),
        ("astrbot.api", api),
        ("astrbot.api.event", events),
        ("astrbot.api.star", star),
    ]:
        monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location("plugin_test.main", root / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    plugin = module.AstralPartyPlugin(object(), {"image_cards": True})
    plugin.initialize = AsyncMock()
    return plugin, module


async def test_group_credentials_stop_propagation(entrypoint):
    plugin, _ = entrypoint
    plugin.app = PartyApp(None)
    event = Event("星趴 登录 13800000000")
    results = [r async for r in plugin.party_command(event)]
    assert event.stopped and all(r.stopped for r in results)
    assert "私聊" in results[0].value and "13800000000" not in results[0].value


async def test_help_is_always_text(entrypoint):
    plugin, _ = entrypoint
    plugin.app = PartyApp(None)
    plugin.html_render = AsyncMock()
    results = [r async for r in plugin.party_command(Event("星趴 帮助"))]
    assert results[0].kind == "text" and results[0].value == GENERAL
    plugin.html_render.assert_not_called()


async def test_render_failure_falls_back_and_logs_no_raw_exception(entrypoint, caplog):
    plugin, _ = entrypoint
    plugin.app = types.SimpleNamespace(
        execute=AsyncMock(return_value=Reply("测试档案", True))
    )
    plugin.html_render = AsyncMock(
        side_effect=RuntimeError("SECRET TOKEN SHOULD NOT BE LOGGED")
    )
    with caplog.at_level(logging.WARNING):
        results = [r async for r in plugin.party_command(Event("星趴 我的"))]
    assert (
        results[0].kind == "text"
        and results[0].value == "测试档案"
        and results[0].stopped
    )
    assert "SECRET TOKEN" not in caplog.text


async def test_image_card_uses_local_file_and_escaped_template(entrypoint):
    plugin, module = entrypoint
    plugin.app = types.SimpleNamespace(
        execute=AsyncMock(return_value=Reply("<script>untrusted</script>", True))
    )
    plugin.html_render = AsyncMock(return_value="/tmp/card.png")
    results = [r async for r in plugin.party_command(Event("星趴 我的"))]
    assert results[0].kind == "image" and results[0].value == "/tmp/card.png"
    assert plugin.html_render.await_args.kwargs["return_url"] is False
    assert "{{ card.title | e }}" in module.CARD_TEMPLATE
    assert "{{ detail.value | e }}" in module.CARD_TEMPLATE


async def test_long_query_renders_exactly_one_complete_image(entrypoint):
    from astralparty.cards import card, row

    plugin, _ = entrypoint
    model = card("全部角色", "", "TEST", rows=[row(f"角色{i}", 101) for i in range(32)])
    plugin.app = types.SimpleNamespace(
        execute=AsyncMock(return_value=Reply("完整数据", True, model))
    )
    plugin.html_render = AsyncMock(return_value="/tmp/full-card.png")
    results = [r async for r in plugin.party_command(Event("~星趴 角色"))]
    assert len(results) == 1 and results[0].kind == "image"
    plugin.html_render.assert_awaited_once()
    assert len(plugin.html_render.await_args.args[1]["card"]["rows"]) == 32


async def test_full_review_uses_wide_table_in_one_image(entrypoint, replay_bytes):
    from astralparty.cards import review_card
    from astralparty.review import build_review

    plugin, module = entrypoint
    model = review_card(build_review(replay_bytes, "1234567890123456"))
    plugin.app = types.SimpleNamespace(
        execute=AsyncMock(return_value=Reply("整局复盘", True, model))
    )
    plugin.html_render = AsyncMock(return_value="/tmp/review.png")
    results = [
        r async for r in plugin.party_command(Event("~星趴 复盘 1234567890123456"))
    ]
    assert len(results) == 1 and results[0].kind == "image"
    plugin.html_render.assert_awaited_once()
    assert plugin.html_render.await_args.args[0] == module.REVIEW_TEMPLATE
    assert plugin.html_render.await_args.kwargs["options"]["viewport"]["width"] == 1440
    assert len(plugin.html_render.await_args.args[1]["card"]["players"]) == 4

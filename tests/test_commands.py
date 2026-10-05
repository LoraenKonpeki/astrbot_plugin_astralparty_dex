from unittest.mock import AsyncMock, MagicMock

import pytest

from astralparty.app import PartyApp, PRIVATE_MESSAGE
from astralparty.help import DETAILS
from astralparty.errors import UserError
from astralparty.profile import profile_from_player


@pytest.mark.parametrize("command", ["登录", "验证", "刷新", "解绑"])
@pytest.mark.parametrize("args", ["", " 123456", " malformed too many arguments"])
async def test_group_private_commands_have_no_side_effect(command, args):
    service = MagicMock()
    app = PartyApp(service)
    result = await app.execute("owner", "group", False, f"~星趴 {command}{args}")
    assert result.text == PRIVATE_MESSAGE
    assert not service.mock_calls
    assert "123456" not in result.text


@pytest.mark.parametrize("topic", list(DETAILS))
async def test_each_command_has_detailed_help(topic):
    service = MagicMock()
    app = PartyApp(service)
    a = await app.execute("a", "g", False, f"~星趴 帮助 {topic}")
    b = await app.execute("a", "g", False, f"~星趴 {topic} 帮助")
    assert a.text == b.text == DETAILS[topic]
    assert "用法" in a.text
    assert not service.mock_calls


async def test_general_help_and_empty_command():
    app = PartyApp(None)
    for text in ["~星趴", "星趴", "~星趴 帮助", "astralparty help"]:
        reply = await app.execute("a", "g", False, text)
        assert (
            "登录" in reply.text and "复盘" in reply.text and "详细帮助" in reply.text
        )


async def test_private_login_calls_only_sms():
    service = MagicMock(send_code=AsyncMock(return_value="验证码已发送"))
    reply = await PartyApp(service).execute(
        "a", "private", True, "星趴 登录 13800000000"
    )
    service.send_code.assert_awaited_once_with("a", "13800000000")
    assert "已发送" in reply.text


async def test_missing_login_argument_no_sms():
    service = MagicMock()
    with pytest.raises(UserError, match="用法"):
        await PartyApp(service).execute("a", "private", True, "星趴 登录")
    assert not service.mock_calls


async def test_records_show_and_store_complete_list(player):
    profile = profile_from_player(player)
    profile["recent"] *= 10
    service = MagicMock(profile=AsyncMock(return_value=profile))
    reply = await PartyApp(service).execute("a", "g", False, "~星趴 战绩 我的")
    assert "1." in reply.text and "10." in reply.text
    assert len(reply.visual["rows"]) == 10
    service.remember_records.assert_called_once_with("a", "g", profile["recent"])


async def test_other_uid_does_not_read_own_backpack(player):
    service = MagicMock(query=AsyncMock(return_value=profile_from_player(player)))
    await PartyApp(service).execute("a", "g", False, "星趴 战绩 1234567")
    service.query.assert_awaited_once_with("a", "1234567")
    service.profile.assert_not_called()


@pytest.mark.parametrize(
    "command", ["战绩 我的 2", "角色 2", "皮肤 101 2", "复盘 1 1234567 2"]
)
async def test_removed_page_arguments_do_not_start_io(command):
    service = MagicMock()
    with pytest.raises(UserError, match="用法"):
        await PartyApp(service).execute("a", "g", False, "~星趴 " + command)
    assert not service.mock_calls


async def test_skin_numeric_argument_is_role_filter(player):
    service = MagicMock(profile=AsyncMock(return_value=profile_from_player(player)))
    reply = await PartyApp(service).execute("a", "g", False, "~星趴 皮肤 101")
    assert len(reply.visual["rows"]) == 1
    assert "角色 101" in reply.visual["rows"][0]["meta"][0]


def test_all_help_uses_tilde_and_has_no_pagination():
    from astralparty.help import GENERAL

    for text in [GENERAL, *DETAILS.values()]:
        assert "~星趴" in text
        assert "/星趴" not in text
        assert "页码" not in text and "每页" not in text


async def test_full_review_needs_only_replay_id_and_no_login(replay_bytes):
    from astralparty.review import build_review

    review = build_review(replay_bytes, "1234567890123456")
    service = MagicMock(replay=AsyncMock(return_value=review))
    service.resolve_replay.return_value = "1234567890123456"
    result = await PartyApp(service).execute(
        "a", "g", False, "~星趴 复盘 1234567890123456"
    )
    service.replay.assert_awaited_once_with("1234567890123456")
    service.profile.assert_not_called()
    assert result.visual["kind"] == "review" and len(result.visual["players"]) == 4
    assert all(f"UID {1234567 + i}" in result.text for i in range(4))


async def test_old_review_uid_parameter_shows_new_help_without_downloading():
    service = MagicMock()
    with pytest.raises(UserError, match="不需要玩家 UID"):
        await PartyApp(service).execute(
            "a", "g", False, "~星趴 复盘 1234567890123456 1234567"
        )
    assert not service.mock_calls

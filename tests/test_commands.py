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
    result = await app.execute("owner", "group", False, f"/星趴 {command}{args}")
    assert result.text == PRIVATE_MESSAGE
    assert not service.mock_calls
    assert "123456" not in result.text


@pytest.mark.parametrize("topic", list(DETAILS))
async def test_each_command_has_detailed_help(topic):
    service = MagicMock()
    app = PartyApp(service)
    a = await app.execute("a", "g", False, f"/星趴 帮助 {topic}")
    b = await app.execute("a", "g", False, f"/星趴 {topic} 帮助")
    assert a.text == b.text == DETAILS[topic]
    assert "用法" in a.text
    assert not service.mock_calls


async def test_general_help_and_empty_command():
    app = PartyApp(None)
    for text in ["/星趴", "星趴", "/星趴 帮助", "astralparty help"]:
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


async def test_records_pages_store_complete_list(player):
    profile = profile_from_player(player)
    profile["recent"] *= 10
    service = MagicMock(profile=AsyncMock(return_value=profile))
    reply = await PartyApp(service).execute("a", "g", False, "星趴 战绩 我的 2")
    assert "6." in reply.text and "10." in reply.text and "1." not in reply.text
    service.remember_records.assert_called_once_with("a", "g", profile["recent"])


async def test_other_uid_does_not_read_own_backpack(player):
    service = MagicMock(query=AsyncMock(return_value=profile_from_player(player)))
    await PartyApp(service).execute("a", "g", False, "星趴 战绩 1234567")
    service.query.assert_awaited_once_with("a", "1234567")
    service.profile.assert_not_called()

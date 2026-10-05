import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from astralparty.errors import UserError, SessionConflict
from astralparty.service import PartyService
from astralparty.sdk import LoginTicket
from astralparty.store import CredentialStore, owner_key

A = owner_key("qq", "instance", "bot", "a")
B = owner_key("qq", "instance", "bot", "b")


def test_owner_isolation():
    assert (
        len(
            {
                owner_key("qq", i, b, s)
                for i in ["i", "j"]
                for b in ["b", "c"]
                for s in ["1", "2"]
            }
        )
        == 8
    )
    assert owner_key("qq", "i", "b", "1") != owner_key("discord", "i", "b", "1")


def test_encryption_atomic_replacement_and_unbind(tmp_path):
    store = CredentialStore(tmp_path)
    store.save(
        A,
        {
            "token": "secret-access-token",
            "uid": 1234567,
            "nick": "用户A",
            "saved_at": 0,
            "smscode": "123456",
            "response": "do not save",
        },
    )
    store.save(B, {"token": "other-token", "uid": 7654321})
    raw = (tmp_path / "accounts" / f"{A}.enc").read_bytes()
    assert b"secret-access-token" not in raw and b"123456" not in raw
    assert "smscode" not in store.load(A) and "response" not in store.load(A)
    assert (tmp_path / "credentials.key").stat().st_mode & 0o777 == 0o600
    assert CredentialStore(tmp_path).load(A)["uid"] == 1234567
    store.delete(A)
    assert store.load(A) is None and store.load(B)["uid"] == 7654321
    assert not list(tmp_path.rglob("*.tmp"))


def test_missing_key_never_regenerated_over_accounts(tmp_path):
    store = CredentialStore(tmp_path)
    store.save(A, {"token": "secret"})
    (tmp_path / "credentials.key").unlink()
    with pytest.raises(UserError, match="密钥缺失"):
        CredentialStore(tmp_path)


async def test_sms_cooldown_survives_reload_and_separate_users(tmp_path):
    service = PartyService(tmp_path)
    service.sdk = MagicMock(send_code=AsyncMock())
    try:
        await service.send_code(A, "13800000000")
        with pytest.raises(UserError, match="冷却"):
            await service.send_code(B, "13800000000")
        another = PartyService(tmp_path)
        another.sdk = MagicMock(send_code=AsyncMock())
        try:
            with pytest.raises(UserError, match="冷却"):
                await another.send_code(A, "13900000000")
        finally:
            await another.close()
        raw = json.loads(service.sms_file.read_text())
        assert "13800000000" not in json.dumps(raw)
        assert service.state(B).pending is None
    finally:
        await service.close()


async def test_verification_expiry_and_attempt_limit(tmp_path):
    service = PartyService(tmp_path)
    service.sdk = MagicMock(authorize=AsyncMock(side_effect=UserError("验证码无效")))
    state = service.state(A)
    state.pending = {"phone": "13800000000", "expires": time.time() - 1, "attempts": 0}
    with pytest.raises(UserError, match="过期"):
        await service.verify(A, "123456")
    service.sdk.authorize.assert_not_called()
    state.pending = {
        "phone": "13800000000",
        "expires": time.time() + 300,
        "attempts": 5,
    }
    with pytest.raises(UserError, match="次数过多"):
        await service.verify(A, "123456")
    assert state.pending is None
    await service.close()


async def test_rotated_token_saved_before_failed_game_handshake(tmp_path):
    service = PartyService(tmp_path)
    service.store.save(A, {"token": "old-token", "uid": 1234567})
    service.sdk = MagicMock(
        authorize=AsyncMock(return_value=LoginTicket("sid", "rotated-token"))
    )
    service._connect = AsyncMock(side_effect=SessionConflict("已在线"))
    try:
        with pytest.raises(SessionConflict):
            await service.profile(A, refresh=True)
        assert service.store.load(A)["token"] == "rotated-token"
        service.sdk.authorize.assert_awaited_once()
        service._connect.assert_awaited_once()
    finally:
        await service.close()


async def test_cached_own_profile_never_uses_public_query(tmp_path, player):
    from astralparty.profile import profile_from_player

    service = PartyService(tmp_path)
    service.store.save(A, {"token": "tok", "uid": player.id})
    service.state(A).profile = profile_from_player(player)
    service._session = AsyncMock(side_effect=AssertionError("should use snapshot"))
    result = await service.query(A, str(player.id))
    assert result["uid"] == player.id and result["total"] == 20
    await service.close()


async def test_sequence_isolation_expiry_and_unbind(tmp_path):
    service = PartyService(tmp_path)
    record = [{"replayId": "1234567890123456"}]
    service.remember_records(A, "group", record)
    assert service.resolve_replay(A, "group", "1") == "1234567890123456"
    for owner, conv in [(B, "group"), (A, "private")]:
        with pytest.raises(UserError, match="过期"):
            service.resolve_replay(owner, conv, "1")
    with pytest.raises(UserError, match="范围"):
        service.resolve_replay(A, "group", "2")
    await service.unbind(A)
    with pytest.raises(UserError, match="过期"):
        service.resolve_replay(A, "group", "1")
    with pytest.raises(UserError):
        service.resolve_replay(A, "group", "../../secrets")
    await service.close()


async def test_kicked_session_requires_deliberate_refresh(tmp_path):
    service = PartyService(tmp_path)
    service.store.save(A, {"token": "token", "uid": 1234567})
    client = MagicMock()
    client.alive = False
    client.failure = SessionConflict("服务器结束会话")
    client.close = AsyncMock()
    service.state(A).client = client
    service.sdk = MagicMock(authorize=AsyncMock())
    try:
        with pytest.raises(UserError, match="不会自动接管"):
            await service.query(A, "7654321")
        service.sdk.authorize.assert_not_called()
    finally:
        await service.close()

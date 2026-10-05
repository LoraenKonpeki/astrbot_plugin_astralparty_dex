"""Encrypted, atomic per-user credential storage. No SDK responses are persisted."""

import hashlib
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .errors import UserError


def atomic_write(path: Path, data: bytes):
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        path.chmod(0o600)
    finally:
        tmp.unlink(missing_ok=True)


def owner_key(platform, instance, bot, sender):
    # Include instance and self ID, but not group ID: a private binding works in groups.
    raw = json.dumps(
        [str(v) for v in (platform, instance, bot, sender)], ensure_ascii=False
    )
    return hashlib.sha256(raw.encode()).hexdigest()


class CredentialStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        self.accounts = self.root / "accounts"
        self.accounts.mkdir(exist_ok=True, mode=0o700)
        self.accounts.chmod(0o700)
        keyfile = self.root / "credentials.key"
        if not keyfile.exists():
            if any(self.accounts.glob("*.enc")):
                raise UserError("凭据密钥缺失，请管理员恢复备份；不能使用已有绑定。")
            atomic_write(keyfile, Fernet.generate_key())
        keyfile.chmod(0o600)
        self.cipher = Fernet(keyfile.read_bytes())

    def _path(self, owner):
        if len(owner) != 64 or any(c not in "0123456789abcdef" for c in owner):
            raise ValueError("invalid owner key")
        return self.accounts / f"{owner}.enc"

    def load(self, owner):
        path = self._path(owner)
        if not path.exists():
            return None
        try:
            return json.loads(self.cipher.decrypt(path.read_bytes()))
        except (InvalidToken, ValueError, OSError):
            raise UserError(
                "绑定数据无法读取，请私聊使用 ~星趴 解绑 后重新登录。"
            ) from None

    def save(self, owner, record):
        # Whitelist fields. Never save authorize_code, verification codes or raw responses.
        safe = {
            k: record[k] for k in ("token", "uid", "nick", "saved_at") if k in record
        }
        atomic_write(
            self._path(owner),
            self.cipher.encrypt(json.dumps(safe, ensure_ascii=False).encode()),
        )

    def delete(self, owner):
        self._path(owner).unlink(missing_ok=True)

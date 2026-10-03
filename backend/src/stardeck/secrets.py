"""Encryption for stored connection secrets (database passwords)."""

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from stardeck.config import Settings


class SecretBox:
    def __init__(self, settings: Settings):
        self._fernet = Fernet(self._load_key(settings))

    @staticmethod
    def _load_key(settings: Settings) -> bytes:
        if settings.secret_key:
            return settings.secret_key.encode()
        path: Path = settings.data_dir / "secret.key"
        if path.exists():
            return path.read_bytes().strip()
        key = Fernet.generate_key()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Owner-only from the moment it exists.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
        return key

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise ValueError(
                "Stored password could not be decrypted; was STARDECK_SECRET_KEY changed?"
            ) from exc

"""Application-level field encryption for free-text that may be sensitive
(request descriptions, case messages, internal notes, feedback comments)."""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.config import get_settings

PREFIX = "enc1:"


@lru_cache
def _fernet() -> MultiFernet:
    return MultiFernet([Fernet(k.encode()) for k in get_settings().fernet_keys()])


def reset_crypto_cache() -> None:
    _fernet.cache_clear()


def encrypt_text(value: str) -> str:
    return PREFIX + _fernet().encrypt(value.encode()).decode()


def decrypt_text(value: str) -> str:
    if not value.startswith(PREFIX):
        return value  # legacy / plaintext row
    try:
        return _fernet().decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken:
        return "[unreadable: encryption key mismatch]"


class EncryptedText(TypeDecorator):
    """Text column transparently encrypted with Fernet (AES-128-CBC + HMAC), key-rotation aware."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else encrypt_text(value)

    def process_result_value(self, value, dialect):
        return None if value is None else decrypt_text(value)

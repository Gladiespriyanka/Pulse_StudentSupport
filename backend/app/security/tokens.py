from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt

from app.config import get_settings


def create_access_token(user_id: uuid.UUID, token_version: int, now: datetime | None = None) -> tuple[str, int]:
    s = get_settings()
    now = now or datetime.now(timezone.utc)
    exp = now + timedelta(minutes=s.access_token_minutes)
    claims = {
        "sub": str(user_id), "ver": token_version, "iss": s.jwt_issuer, "aud": s.jwt_audience,
        "iat": int(now.timestamp()), "exp": int(exp.timestamp()), "jti": secrets.token_hex(8),
    }
    return jwt.encode(claims, s.jwt_secret, algorithm="HS256"), s.access_token_minutes * 60


def decode_access_token(token: str) -> dict:
    s = get_settings()
    return jwt.decode(
        token, s.jwt_secret, algorithms=["HS256"], audience=s.jwt_audience, issuer=s.jwt_issuer,
        options={"require": ["exp", "iat", "sub", "ver", "iss", "aud"]},
    )


def new_opaque_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(48)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_csrf_token() -> str:
    return secrets.token_urlsafe(24)

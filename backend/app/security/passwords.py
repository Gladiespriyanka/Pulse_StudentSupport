from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.config import get_settings

COMMON = {
    "password", "password1", "password123", "123456789", "1234567890", "qwertyuiop", "iloveyou12", "welcome123",
    "admin12345", "letmein123", "pulse123", "pulse12345", "campus123", "student123", "changeme123", "qwerty12345",
}

_hasher: PasswordHasher | None = None


def _ph() -> PasswordHasher:
    global _hasher
    if _hasher is None:
        if get_settings().app_env == "test":  # keep the test-suite fast; production uses argon2id defaults
            _hasher = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
        else:
            _hasher = PasswordHasher()
    return _hasher


def hash_password(password: str) -> str:
    return _ph().hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _ph().verify(hashed, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(hashed: str) -> bool:
    try:
        return _ph().check_needs_rehash(hashed)
    except InvalidHashError:
        return True


_DUMMY = None


def burn_time() -> None:
    """Spend the same time as a real verification so unknown emails are not distinguishable by latency."""
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = hash_password("not-a-real-password")
    verify_password("x", _DUMMY)


def password_problems(password: str, *, email: str = "", name: str = "") -> list[str]:
    s = get_settings()
    out: list[str] = []
    if len(password) < s.password_min_length:
        out.append(f"Use at least {s.password_min_length} characters")
    if len(password) > 128:
        out.append("Use at most 128 characters")
    low = password.lower()
    if low in COMMON or len(set(low)) < 5:
        out.append("This password is too easy to guess")
    local = email.split("@")[0].lower() if email else ""
    if local and len(local) >= 4 and local in low:
        out.append("Do not include your email name in your password")
    if name and any(len(p) >= 4 and p.lower() in low for p in name.split()):
        out.append("Do not include your name in your password")
    return out

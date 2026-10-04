"""Login, lockout, refresh-token rotation with reuse detection, logout, password change."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import Invalid, Unauthorized
from app.models import RefreshToken, User
from app.models.base import utcnow
from app.security.context import get_ctx
from app.security.passwords import burn_time, hash_password, needs_rehash, password_problems, verify_password
from app.security.tokens import create_access_token, hash_token, new_opaque_token
from app.services.audit import audit

GENERIC = "Email or password is incorrect."


def authenticate(db: Session, email: str, password: str, now: datetime | None = None) -> User:
    now = now or utcnow()
    s = get_settings()
    user = db.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))
    if not user:
        burn_time()
        audit(db, "auth.login_failed", outcome="failure", details={"reason": "unknown_email"})
        db.commit()
        raise Unauthorized(GENERIC, code="invalid_credentials")
    if user.locked_until and user.locked_until > now:
        burn_time()
        audit(db, "auth.login_blocked", actor=user, outcome="denied", details={"reason": "locked"})
        db.commit()
        raise Unauthorized("Too many failed attempts. Try again later or ask an administrator to unlock the account.", code="account_locked")
    ok = verify_password(password, user.password_hash)
    if not ok or not user.is_active:
        if ok is False:
            user.failed_login_count += 1
            if user.failed_login_count >= s.login_max_attempts:
                user.locked_until = now + timedelta(minutes=s.lockout_minutes)
                user.failed_login_count = 0
                audit(db, "auth.locked", actor=user, outcome="denied", details={"minutes": s.lockout_minutes})
        audit(db, "auth.login_failed", actor=user, outcome="failure", details={"reason": "bad_password" if not ok else "inactive"})
        db.commit()
        raise Unauthorized(GENERIC, code="invalid_credentials")
    user.failed_login_count, user.locked_until, user.last_login_at = 0, None, now
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    audit(db, "auth.login", actor=user)
    return user


def issue_session(db: Session, user: User, family_id: uuid.UUID | None = None, now: datetime | None = None) -> tuple[str, str, int]:
    """Returns (access_token, refresh_token_raw, access_ttl_seconds)."""
    now = now or utcnow()
    s = get_settings()
    access, ttl = create_access_token(user.id, user.token_version, now)
    raw, hashed = new_opaque_token()
    ctx = get_ctx()
    db.add(RefreshToken(user_id=user.id, family_id=family_id or uuid.uuid4(), token_hash=hashed,
                        expires_at=now + timedelta(days=s.refresh_token_days), ip=ctx.ip, user_agent=(ctx.user_agent or "")[:255]))
    return access, raw, ttl


def rotate(db: Session, raw: str, now: datetime | None = None) -> tuple[User, str, str, int]:
    """Exchange a refresh token for a new pair. Re-use of an already-rotated token revokes the whole family."""
    now = now or utcnow()
    row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw)).with_for_update())
    if not row:
        raise Unauthorized("Please sign in again.", code="refresh_invalid")
    if row.revoked_at is not None:
        db.execute(update(RefreshToken).where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None)).values(revoked_at=now))
        audit(db, "auth.refresh_reuse_detected", actor=db.get(User, row.user_id), outcome="denied", details={"family": str(row.family_id)})
        db.commit()
        raise Unauthorized("Your session was ended for security reasons. Please sign in again.", code="refresh_reused")
    user = db.get(User, row.user_id)
    if row.expires_at <= now or not user or not user.is_active:
        raise Unauthorized("Your session has expired. Please sign in again.", code="refresh_expired")
    access, new_raw, ttl = issue_session(db, user, family_id=row.family_id, now=now)
    new_row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(new_raw)))
    row.revoked_at, row.replaced_by_id = now, new_row.id if new_row else None
    return user, access, new_raw, ttl


def revoke_session(db: Session, raw: str | None) -> None:
    if not raw:
        return
    row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw)))
    if row:
        db.execute(update(RefreshToken).where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None)).values(revoked_at=utcnow()))


def revoke_all_sessions(db: Session, user_id: uuid.UUID) -> None:
    db.execute(update(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)).values(revoked_at=utcnow()))


def change_password(db: Session, user: User, current: str, new: str) -> None:
    if not verify_password(current, user.password_hash):
        audit(db, "auth.password_change_failed", actor=user, outcome="failure")
        db.commit()
        raise Invalid("Your current password is incorrect.", code="wrong_password")
    problems = password_problems(new, email=user.email, name=user.full_name)
    if problems:
        raise Invalid("Choose a stronger password.", code="weak_password", details=problems)
    if verify_password(new, user.password_hash):
        raise Invalid("Choose a password you have not used before.", code="password_reused")
    user.password_hash, user.must_change_password = hash_password(new), False
    user.token_version += 1  # signs out every other session
    revoke_all_sessions(db, user.id)
    audit(db, "auth.password_changed", actor=user, subject=user.id)

"""Authentication + permission dependencies. Every protected endpoint declares what it needs here."""
from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass, field

import jwt
from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import Forbidden, Unauthorized
from app.models import User
from app.security import ratelimit
from app.security.tokens import decode_access_token
from app.config import get_settings

ACCESS_COOKIE = "pulse_at"
REFRESH_COOKIE = "pulse_rt"
CSRF_COOKIE = "pulse_csrf"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass
class Principal:
    user: User
    role: str
    perms: frozenset[str]
    department_id: uuid.UUID | None
    via_cookie: bool = False
    _cache: dict = field(default_factory=dict)

    def can(self, perm: str) -> bool:
        return perm in self.perms

    @property
    def id(self) -> uuid.UUID:
        return self.user.id

    @property
    def is_student(self) -> bool:
        return self.role == "student"


def check_csrf(request: Request) -> None:
    sent = request.headers.get(CSRF_HEADER, "")
    cookie = request.cookies.get(CSRF_COOKIE, "")
    if not sent or not cookie or not hmac.compare_digest(sent, cookie):
        raise Forbidden("CSRF check failed. Reload the page and try again.", code="csrf_failed")


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _authenticate(request: Request, db: Session, allow_password_change: bool) -> Principal:
    auth = request.headers.get("authorization", "")
    token, via_cookie = None, False
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    elif request.cookies.get(ACCESS_COOKIE):
        token, via_cookie = request.cookies[ACCESS_COOKIE], True
    if not token:
        raise Unauthorized("Please sign in.", code="not_authenticated")
    try:
        claims = decode_access_token(token)
        uid = uuid.UUID(claims["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        raise Unauthorized("Your session has expired. Please sign in again.", code="token_invalid")
    user = db.get(User, uid)
    if not user or not user.is_active or user.token_version != claims["ver"]:
        raise Unauthorized("Your session is no longer valid. Please sign in again.", code="token_revoked")
    if via_cookie and request.method not in SAFE_METHODS:
        check_csrf(request)
    if user.must_change_password and not allow_password_change:
        raise Forbidden("You must change your password before continuing.", code="password_change_required")
    ratelimit.hit(f"user:{user.id}", get_settings().rate_limit_per_minute * 2)
    dept = user.staff.department_id if user.staff else None
    return Principal(user=user, role=user.role.code, perms=frozenset(p.code for p in user.role.permissions),
                     department_id=dept, via_cookie=via_cookie)


def current_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    return _authenticate(request, db, allow_password_change=False)


def current_principal_pw(request: Request, db: Session = Depends(get_db)) -> Principal:
    return _authenticate(request, db, allow_password_change=True)


def _deny(db: Session, p: Principal, request: Request, needed: str):
    from app.services.audit import audit

    audit(db, "authz.denied", actor=p, outcome="denied", details={"path": request.url.path, "needs": needed})
    db.commit()
    raise Forbidden("You do not have permission to do that.", code="forbidden")


def require(*perms: str):
    """Caller needs ALL listed permissions."""

    def dep(request: Request, db: Session = Depends(get_db), p: Principal = Depends(current_principal)) -> Principal:
        missing = [x for x in perms if not p.can(x)]
        if missing:
            _deny(db, p, request, ",".join(missing))
        return p

    return dep


def require_any(*perms: str):
    """Caller needs AT LEAST ONE of the listed permissions."""

    def dep(request: Request, db: Session = Depends(get_db), p: Principal = Depends(current_principal)) -> Principal:
        if not any(p.can(x) for x in perms):
            _deny(db, p, request, "|".join(perms))
        return p

    return dep

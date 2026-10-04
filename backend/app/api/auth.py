from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import Unauthorized
from app.db import get_db
from app.api.serializers import user_out
from app.models import User
from app.schemas import LoginIn, PasswordChangeIn
from app.security import ratelimit
from app.security.deps import (
    ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE, Principal, check_csrf, client_ip, current_principal_pw,
)
from app.security.tokens import new_csrf_token
from app.services import auth as A
from app.services.audit import audit

router = APIRouter(prefix="/auth", tags=["auth"])


def _cookie(resp: Response, name: str, value: str, max_age: int, *, http_only: bool, path: str = "/") -> None:
    s = get_settings()
    resp.set_cookie(name, value, max_age=max_age, httponly=http_only, secure=s.cookie_secure, samesite=s.cookie_samesite,
                    path=path, domain=s.cookie_domain)


def _set_session(resp: Response, access: str, refresh: str, ttl: int) -> str:
    s = get_settings()
    csrf = new_csrf_token()
    _cookie(resp, ACCESS_COOKIE, access, ttl, http_only=True)
    _cookie(resp, REFRESH_COOKIE, refresh, s.refresh_token_days * 86400, http_only=True, path="/api/v1/auth")
    _cookie(resp, CSRF_COOKIE, csrf, s.refresh_token_days * 86400, http_only=False)
    return csrf


def _clear(resp: Response) -> None:
    s = get_settings()
    for name, path in ((ACCESS_COOKIE, "/"), (REFRESH_COOKIE, "/api/v1/auth"), (CSRF_COOKIE, "/")):
        resp.delete_cookie(name, path=path, domain=s.cookie_domain)


@router.post("/login", summary="Sign in (sets httpOnly session cookies)")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    s = get_settings()
    ip = client_ip(request)
    ratelimit.hit(f"login:{ip}:{body.email.lower()}", s.login_rate_limit_per_minute)
    ratelimit.hit(f"login-ip:{ip}", s.login_rate_limit_per_minute * 5)
    user = A.authenticate(db, body.email, body.password)
    access, refresh, ttl = A.issue_session(db, user)
    db.commit()
    csrf = _set_session(response, access, refresh, ttl)
    return {"user": user_out(user), "csrf_token": csrf, "expires_in": ttl}


@router.post("/refresh", summary="Rotate the refresh token and issue a new access token")
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    ratelimit.hit(f"refresh:{client_ip(request)}", 60)
    raw = request.cookies.get(REFRESH_COOKIE)
    if not raw:
        _clear(response)
        raise Unauthorized("Please sign in.", code="not_authenticated")
    check_csrf(request)
    try:
        user, access, new_raw, ttl = A.rotate(db, raw)
    except Exception:
        db.rollback()
        raise
    db.commit()
    csrf = _set_session(response, access, new_raw, ttl)
    return {"user": user_out(user), "csrf_token": csrf, "expires_in": ttl}


@router.post("/logout", summary="End the session and revoke the refresh token")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    if request.cookies.get(ACCESS_COOKIE) or request.cookies.get(REFRESH_COOKIE):
        check_csrf(request)
    raw = request.cookies.get(REFRESH_COOKIE)
    A.revoke_session(db, raw)
    audit(db, "auth.logout")
    db.commit()
    _clear(response)
    return {"ok": True}


@router.get("/me", summary="Current user, role and permissions")
def me(p: Principal = Depends(current_principal_pw)):
    return user_out(p.user)


@router.post("/change-password")
def change_password(body: PasswordChangeIn, response: Response, p: Principal = Depends(current_principal_pw),
                    db: Session = Depends(get_db)):
    A.change_password(db, p.user, body.current_password, body.new_password)
    access, refresh, ttl = A.issue_session(db, p.user)
    db.commit()
    csrf = _set_session(response, access, refresh, ttl)
    return {"user": user_out(p.user), "csrf_token": csrf}

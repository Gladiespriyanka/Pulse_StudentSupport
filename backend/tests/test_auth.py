from datetime import timedelta

import jwt
from sqlalchemy import select

from app.config import get_settings
from app.models import AuditLog, RefreshToken, User
from app.models.base import utcnow
from tests.conftest import PW, Api


def test_login_success_sets_httponly_cookies_and_returns_user(world):
    a = Api(world.app)
    r = a.login(world.email("stu_a"))
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["role"] == "student" and "request.create" in body["user"]["permissions"]
    assert "access_token" not in body  # token never exposed to JS
    set_cookie = r.headers.get_list("set-cookie")
    at = next(c for c in set_cookie if c.startswith("pulse_at="))
    rt = next(c for c in set_cookie if c.startswith("pulse_rt="))
    csrf = next(c for c in set_cookie if c.startswith("pulse_csrf="))
    assert "httponly" in at.lower() and "httponly" in rt.lower() and "httponly" not in csrf.lower()
    assert "path=/api/v1/auth" in rt.lower()
    assert "samesite=lax" in at.lower()


def test_wrong_password_and_unknown_email_look_identical(world, anon):
    r1 = anon.login(world.email("stu_a"), "wrong-password-1")
    r2 = anon.login("nobody@campus.edu", "wrong-password-1")
    assert r1.status_code == r2.status_code == 401
    assert r1.json()["error"]["message"] == r2.json()["error"]["message"]
    assert r1.json()["error"]["code"] == r2.json()["error"]["code"] == "invalid_credentials"


def test_lockout_after_repeated_failures_then_unlock_by_admin(world, anon):
    for _ in range(get_settings().login_max_attempts):
        assert anon.login(world.email("stu_a"), "bad-password-xx").status_code == 401
    r = anon.login(world.email("stu_a"), PW)  # even the correct password is refused while locked
    assert r.status_code == 401 and r.json()["error"]["code"] == "account_locked"
    admin = world.api("admin")
    uid = str(world.users.stu_a)
    assert admin.get(f"/admin/users/{uid}").json()["locked"] is True
    assert admin.post(f"/admin/users/{uid}/unlock").status_code == 200
    assert anon.login(world.email("stu_a"), PW).status_code == 200


def test_lockout_expires_with_time(world, anon, db):
    for _ in range(get_settings().login_max_attempts):
        anon.login(world.email("stu_b"), "bad-password-xx")
    u = db.scalar(select(User).where(User.email == world.email("stu_b")))
    u.locked_until = utcnow() - timedelta(seconds=1)
    db.commit()
    assert anon.login(world.email("stu_b"), PW).status_code == 200


def test_protected_endpoint_requires_auth(anon):
    r = anon.get("/auth/me")
    assert r.status_code == 401 and r.json()["error"]["code"] == "not_authenticated"
    assert anon.get("/requests").status_code == 401


def test_me_returns_permissions_for_role(world):
    me = world.api("warden").get("/auth/me").json()
    assert me["role"] == "staff" and me["department_name"] == "Hostel & Facilities"
    assert "request.manage" in me["permissions"] and "admin.users" not in me["permissions"]


def test_session_survives_refresh_and_relogin_flow(world):
    """Simulates browser reload: cookies persist, access token expires, refresh rotates, logout kills session."""
    a = world.api("stu_a")
    assert a.get("/auth/me").status_code == 200
    old_refresh = a.c.cookies.get("pulse_rt")
    # access token expires -> API says 401, client refreshes
    a.c.cookies.delete("pulse_at")
    assert a.get("/auth/me").status_code == 401
    r = a.post("/auth/refresh")
    assert r.status_code == 200
    assert a.c.cookies.get("pulse_rt") != old_refresh  # rotated
    assert a.get("/auth/me").status_code == 200
    # logout revokes
    assert a.post("/auth/logout").status_code == 200
    assert a.get("/auth/me").status_code == 401
    assert a.post("/auth/refresh").status_code == 401
    # re-login works and data is the same user
    assert a.login(world.email("stu_a")).status_code == 200
    assert a.get("/auth/me").json()["email"] == world.email("stu_a")


def test_refresh_token_reuse_revokes_whole_family(world):
    a = world.api("stu_a")
    stolen = a.c.cookies.get("pulse_rt")
    assert a.post("/auth/refresh").status_code == 200  # legit rotation
    # attacker replays the old token
    thief = Api(world.app)
    thief.c.cookies.set("pulse_rt", stolen, path="/api/v1/auth")
    thief.c.cookies.set("pulse_csrf", "x")
    r = thief.c.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": "x"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "refresh_reused"
    # the legitimate (rotated) token is now dead too
    assert a.post("/auth/refresh").status_code == 401


def test_expired_refresh_token_rejected(world, db):
    a = world.api("stu_a")
    row = db.scalar(select(RefreshToken))
    row.expires_at = utcnow() - timedelta(seconds=5)
    db.commit()
    assert a.post("/auth/refresh").status_code == 401


def test_tampered_and_expired_access_tokens_rejected(world, anon):
    s = get_settings()
    uid = str(world.users.stu_a)
    now = utcnow()
    good = dict(sub=uid, ver=1, iss=s.jwt_issuer, aud=s.jwt_audience, iat=int(now.timestamp()), exp=int((now + timedelta(minutes=5)).timestamp()))
    bearer = lambda t: {"Authorization": f"Bearer {t}"}  # noqa: E731
    assert anon.get("/auth/me", headers=bearer(jwt.encode(good, s.jwt_secret, "HS256"))).status_code == 200
    assert anon.get("/auth/me", headers=bearer(jwt.encode(good, "wrong-secret-wrong-secret-wrong-secret!", "HS256"))).status_code == 401
    assert anon.get("/auth/me", headers=bearer(jwt.encode({**good, "exp": int((now - timedelta(minutes=1)).timestamp())}, s.jwt_secret, "HS256"))).status_code == 401
    assert anon.get("/auth/me", headers=bearer(jwt.encode({**good, "aud": "other"}, s.jwt_secret, "HS256"))).status_code == 401
    none_alg = jwt.encode(good, key=None, algorithm="none")
    assert anon.get("/auth/me", headers=bearer(none_alg)).status_code == 401
    assert anon.get("/auth/me", headers=bearer(jwt.encode({**good, "ver": 99}, s.jwt_secret, "HS256"))).status_code == 401


def test_deactivated_user_loses_access_immediately(world):
    a = world.api("stu_a")
    assert a.get("/auth/me").status_code == 200
    admin = world.api("admin")
    assert admin.patch(f"/admin/users/{world.users.stu_a}", {"is_active": False}).status_code == 200
    assert a.get("/auth/me").status_code == 401
    assert a.post("/auth/refresh").status_code == 401
    assert Api(world.app).login(world.email("stu_a")).status_code == 401


def test_csrf_required_for_cookie_authenticated_writes(world):
    a = world.api("stu_a")
    body = {"description": "The lift in hostel B is broken"}
    # cookie session but no CSRF header
    r = a.c.post("/api/v1/requests", json=body)
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    r = a.c.post("/api/v1/requests", json=body, headers={"X-CSRF-Token": "forged"})
    assert r.status_code == 403
    assert a.post("/requests", body).status_code == 201
    # reads never need it
    assert a.c.get("/api/v1/requests").status_code == 200


def test_bearer_clients_do_not_need_csrf(world):
    a = world.api("stu_a")
    token = a.c.cookies.get("pulse_at")
    fresh = Api(world.app)
    r = fresh.c.post("/api/v1/requests", json={"description": "The lift in hostel B is broken"}, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 201


def test_change_password_flow_and_policy(world, anon):
    a = world.api("stu_a")
    old_token = a.c.cookies.get("pulse_at")
    assert a.post("/auth/change-password", {"current_password": "nope-nope-nope", "new_password": "Another#Pass123"}).status_code == 422
    r = a.post("/auth/change-password", {"current_password": PW, "new_password": "short"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "weak_password"
    assert a.post("/auth/change-password", {"current_password": PW, "new_password": "password123"}).status_code == 422
    assert a.post("/auth/change-password", {"current_password": PW, "new_password": "Brand#New#Pass99"}).status_code == 200
    # old tokens die, new session works
    assert anon.get("/auth/me", headers={"Authorization": f"Bearer {old_token}"}).status_code == 401
    assert a.get("/auth/me").status_code == 200
    assert Api(world.app).login(world.email("stu_a"), PW).status_code == 401
    assert Api(world.app).login(world.email("stu_a"), "Brand#New#Pass99").status_code == 200


def test_temp_password_forces_change_before_anything_else(world):
    admin = world.api("admin")
    r = admin.post("/admin/users", {"email": "new.student@campus.edu", "full_name": "New Student", "role": "student", "roll_no": "N100"})
    assert r.status_code == 201
    temp = r.json()["temporary_password"]
    assert temp and r.json()["user"]["must_change_password"] is True
    s = Api(world.app)
    assert s.login("new.student@campus.edu", temp).status_code == 200
    assert s.get("/auth/me").status_code == 200
    blocked = s.get("/requests")
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "password_change_required"
    assert s.post("/auth/change-password", {"current_password": temp, "new_password": "My#Own#Pass2026"}).status_code == 200
    assert s.get("/requests").status_code == 200


def test_login_events_are_audited_without_passwords(world, anon, db):
    anon.login(world.email("stu_a"), "bad-password-xx")
    anon.login(world.email("stu_a"), PW)
    rows = db.scalars(select(AuditLog).where(AuditLog.action.like("auth.%"))).all()
    actions = {r.action for r in rows}
    assert {"auth.login_failed", "auth.login"} <= actions
    assert PW not in str([r.details for r in rows]) and "bad-password-xx" not in str([r.details for r in rows])


def test_password_hashes_are_argon2id(world, db):
    u = db.scalar(select(User).where(User.email == world.email("stu_a")))
    assert u.password_hash.startswith("$argon2id$") and PW not in u.password_hash

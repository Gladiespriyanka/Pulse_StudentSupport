import pytest
from cryptography.fernet import Fernet

from app.config import Settings, get_settings
from tests.conftest import PW, Api


def test_security_headers_and_no_store_on_api(world, anon):
    r = anon.c.get("/api/v1/health")
    h = r.headers
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY" and h["referrer-policy"] == "no-referrer"
    assert h["cache-control"] == "no-store" and h["x-request-id"]


def test_login_rate_limit_returns_429_with_retry_after(world, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
    a = Api(world.app)
    codes = [a.login(world.email("stu_a"), "bad-password-xx").status_code for _ in range(12)]
    assert 429 in codes and codes[0] == 401
    r = a.login(world.email("stu_a"), PW)
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    assert r.json()["error"]["code"] == "rate_limited"


def test_global_ip_rate_limit(world, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "rate_limit_enabled", True)
    monkeypatch.setattr(s, "rate_limit_per_minute", 10)
    a = Api(world.app)
    codes = [a.c.get("/api/v1/health").status_code for _ in range(14)]
    assert codes[:10] == [200] * 10 and 429 in codes[10:]


def test_sos_and_peer_concern_are_throttled(world, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
    a = world.api("stu_a")
    codes = [a.post("/requests/sos", {}).status_code for _ in range(7)]
    assert codes[:5] == [201] * 5 and codes[5] == 429


def test_unhandled_errors_do_not_leak_internals(world):
    from fastapi import APIRouter

    r = APIRouter()

    @r.get("/boom")
    def boom():
        raise RuntimeError("secret db password hunter2")

    world.app.include_router(r)
    resp = Api(world.app).c.get("/boom")
    assert resp.status_code == 500
    body = resp.text
    assert "hunter2" not in body and "Traceback" not in body and resp.json()["error"]["code"] == "internal_error"


def test_input_validation_blocks_oversized_and_malformed_input(world):
    a = world.api("stu_a")
    assert a.post("/requests", {"description": "x" * 4001}).status_code == 422
    assert a.post("/requests", {"description": "ok text here", "service_id": "not-a-uuid"}).status_code == 422
    assert a.c.post("/api/v1/requests", content="{broken", headers={"Content-Type": "application/json", "X-CSRF-Token": a.c.cookies.get("pulse_csrf")}).status_code == 422
    assert world.api("admin").post("/admin/departments", {"code": "ok", "name": "N", "is_active": "maybe"}).status_code == 422


def test_sql_injection_strings_are_inert(world):
    a = world.api("stu_a")
    evil = "'; DROP TABLE requests; --"
    r = a.post("/requests", {"description": evil + " lift is broken"})
    assert r.status_code == 201
    assert world.api("stu_a").get("/requests").json()["total"] == 1
    assert world.api("hostel_head").get("/knowledge?q=" + "'; DROP TABLE users;--").status_code == 200
    assert world.api("admin").get("/admin/users?q=%27%20OR%201%3D1--").json()["total"] == 0


def test_stored_markup_is_returned_verbatim_never_executed_server_side(world):
    r = world.api("stu_a").post("/requests", {"description": "<script>alert(1)</script> lift broken"}).json()
    assert "<script>" in r["description"]  # escaping is the UI's job (see frontend/js/ui.js esc()); JSON is inert


def test_no_cors_by_default(world):
    r = Api(world.app).c.options("/api/v1/requests", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in r.headers


def test_api_docs_available_and_describe_security(world, anon):
    spec = anon.c.get("/api/openapi.json").json()
    assert spec["info"]["title"] == "PULSE API" and len(spec["paths"]) > 80
    assert anon.c.get("/api/docs").status_code == 200
    assert anon.get("/health/ready").json()["status"] == "ready"


# ---- configuration / demo-mode separation -----------------------------------------------------------------------------
def _prod(**kw):
    base = dict(app_env="production", jwt_secret="x" * 40, field_encryption_keys=Fernet.generate_key().decode(),
                cookie_secure=True, database_url="postgresql+psycopg://u:p@db/pulse", demo_mode=False)
    base.update(kw)
    return Settings(_env_file=None, **base)


def test_production_requires_secure_configuration():
    _prod().validate_for_environment()
    for bad in ({"demo_mode": True}, {"jwt_secret": "short"}, {"field_encryption_keys": ""}, {"cookie_secure": False},
                {"serve_frontend": True}, {"database_url": "postgresql+psycopg://u:p@db/pulse_demo"},
                {"database_url": "postgresql+psycopg://u:p@db/pulse_dev"}):
        with pytest.raises(RuntimeError):
            _prod(**bad).validate_for_environment()
    for bad in ({"jwt_secret": "CHANGE_ME_CHANGE_ME_CHANGE_ME_CHANGE_ME_12345"}, {"field_encryption_keys": "not-a-fernet-key"}):
        with pytest.raises(RuntimeError):
            _prod(**bad).validate_for_environment()
    with pytest.raises(RuntimeError) as e:
        Settings(_env_file=None, app_env="production").validate_for_environment()
    assert "FIELD_ENCRYPTION_KEYS" in str(e.value) and "COOKIE_SECURE" in str(e.value)


def test_demo_routes_do_not_exist_when_demo_mode_is_off(world, anon):
    assert anon.get("/demo/accounts").status_code == 404
    assert anon.get("/meta").json()["demo_mode"] is False
    assert not any("/demo" in p for p in anon.c.get("/api/openapi.json").json()["paths"])


def test_production_app_refuses_to_start_in_demo_mode(monkeypatch):
    from app.config import reset_settings_cache
    from app.main import create_app

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEMO_MODE", "true")
    reset_settings_cache()
    with pytest.raises(RuntimeError):
        create_app()
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DEMO_MODE", "false")
    reset_settings_cache()


# ---- encryption -----------------------------------------------------------------------------------------------------------
def test_field_encryption_supports_key_rotation(monkeypatch):
    from app.security import crypto

    k1, k2 = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    s = get_settings()
    monkeypatch.setattr(s, "field_encryption_keys", k1)
    crypto.reset_crypto_cache()
    token = crypto.encrypt_text("private words")
    monkeypatch.setattr(s, "field_encryption_keys", f"{k2},{k1}")  # new key first, old key retained
    crypto.reset_crypto_cache()
    assert crypto.decrypt_text(token) == "private words"
    assert crypto.decrypt_text(crypto.encrypt_text("new")) == "new"
    monkeypatch.setattr(s, "field_encryption_keys", k2)
    crypto.reset_crypto_cache()
    assert "unreadable" in crypto.decrypt_text(token)  # old key removed -> not silently readable
    monkeypatch.setattr(s, "field_encryption_keys", "")
    crypto.reset_crypto_cache()


def test_csv_exports_neutralise_spreadsheet_formulas():
    from app.api.misc import safe_cell

    assert safe_cell("=HYPERLINK(\"http://evil\")") == "'=HYPERLINK(\"http://evil\")"
    assert all(safe_cell(x + "1").startswith("'") for x in "=+-@")
    assert safe_cell("Hostel B") == "Hostel B" and safe_cell(None) == "" and safe_cell(42) == "42"

"""Test harness: real PostgreSQL, schema created by `alembic upgrade head`, tables truncated between tests."""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://pulse:pulse@localhost:5432/pulse_test")
os.environ["APP_ENV"] = "test"
os.environ["DEMO_MODE"] = "false"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["JWT_SECRET"] = "test-secret-test-secret-test-secret-123456"

import uuid  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import get_settings, reset_settings_cache  # noqa: E402
from app.db import SessionLocal, dispose_engine, get_engine  # noqa: E402
from app.models import Department, Location, Service, TriageRule, User  # noqa: E402
from app.rbac import ROLES  # noqa: E402
from app.security import ratelimit  # noqa: E402
from app.services import users as U  # noqa: E402

PW = "Test#Pass12345"
KEEP = {"alembic_version", "roles", "permissions", "role_permissions"}


@pytest.fixture(scope="session", autouse=True)
def _schema():
    """Fresh schema via the real migrations."""
    eng = get_engine()
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    command.upgrade(cfg, "head")
    yield
    dispose_engine()


@pytest.fixture(autouse=True)
def _clean():
    ratelimit.reset()
    eng = get_engine()
    with eng.begin() as c:
        names = [r[0] for r in c.execute(text("select tablename from pg_tables where schemaname='public'")) if r[0] not in KEEP]
        c.execute(text("ALTER TABLE audit_logs DISABLE TRIGGER USER"))
        c.execute(text("TRUNCATE " + ",".join(names) + " RESTART IDENTITY CASCADE"))
        c.execute(text("ALTER TABLE audit_logs ENABLE TRIGGER USER"))
        # restore the baseline role grants (tests may edit them through the admin API)
        c.execute(text("DELETE FROM role_permissions"))
        for role, spec in ROLES.items():
            c.execute(text("INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p "
                           "WHERE r.code = :role AND p.code = ANY(CAST(:codes AS text[]))"), {"role": role, "codes": sorted(set(spec["permissions"]))})
        for prio, a, b in (("critical", 15, 240), ("high", 120, 1440), ("medium", 480, 4320), ("low", 1440, 10080)):
            c.execute(text("INSERT INTO sla_policies (id, service_id, priority, response_minutes, resolution_minutes, at_risk_pct, severe_multiplier, is_active) "
                           "VALUES (gen_random_uuid(), NULL, :p, :a, :b, 75, 2.0, true)"), {"p": prio, "a": a, "b": b})
    yield


class Api:
    """TestClient wrapper that behaves like the browser: keeps cookies and sends the CSRF header."""

    def __init__(self, app):
        self.c = TestClient(app, base_url="http://testserver", raise_server_exceptions=False)

    def _h(self, extra=None):
        h = dict(extra or {})
        tok = self.c.cookies.get("pulse_csrf")
        if tok:
            h.setdefault("X-CSRF-Token", tok)
        return h

    def login(self, email, password=PW):
        r = self.c.post("/api/v1/auth/login", json={"email": email, "password": password})
        return r

    def get(self, url, **kw):
        return self.c.get("/api/v1" + url, headers=self._h(kw.pop("headers", None)), **kw)

    def post(self, url, json=None, **kw):
        return self.c.post("/api/v1" + url, json=json if json is not None else {}, headers=self._h(kw.pop("headers", None)), **kw)

    def patch(self, url, json=None, **kw):
        return self.c.patch("/api/v1" + url, json=json or {}, headers=self._h(kw.pop("headers", None)), **kw)

    def put(self, url, json=None, **kw):
        return self.c.put("/api/v1" + url, json=json or {}, headers=self._h(kw.pop("headers", None)), **kw)

    def delete(self, url, **kw):
        return self.c.delete("/api/v1" + url, headers=self._h(kw.pop("headers", None)), **kw)


@pytest.fixture()
def app():
    from app.main import create_app

    reset_settings_cache()
    return create_app()


@pytest.fixture()
def anon(app):
    return Api(app)


@pytest.fixture()
def world(app):
    """A small, realistic campus: 6 departments, services, rules, 12 users."""
    db = SessionLocal()
    d = {}
    for code, name, triage in [("hostel", "Hostel & Facilities", False), ("academic", "Academic Support", False),
                               ("wellbeing", "Counselling", False), ("safety", "Safety & Security", False),
                               ("finance", "Fees & Scholarships", False), ("desk", "Service Desk", True)]:
        d[code] = Department(code=code, name=name, is_triage_desk=triage)
        db.add(d[code])
    L = {}
    for code, name, kind, al in [("hostel-b", "Hostel B", "hostel", ["hostel b"]), ("library", "Library", "library", ["library"]),
                                 ("block-c", "Block C", "academic", ["block c", "lab"]), ("gate", "Sports Gate", "gate", ["sports gate"])]:
        L[code] = Location(code=code, name=name, kind=kind, aliases=al)
        db.add(L[code])
    db.flush()
    SV = {}
    for code, name, dept, cat, sens, emerg, appr, prio in [
        ("maintenance", "Hostel maintenance", "hostel", "hostel", False, False, False, "medium"),
        ("counselling", "Counselling", "wellbeing", "wellbeing", True, False, False, "medium"),
        ("academic-support", "Academic support", "academic", "academic", False, False, False, "medium"),
        ("safety-report", "Safety report", "safety", "safety", True, False, False, "high"),
        ("sos", "SOS", "safety", "safety", True, True, False, "critical"),
        ("fee-waiver", "Fee waiver", "finance", "finance", False, False, True, "medium")]:
        SV[code] = Service(code=code, name=name, department_id=d[dept].id, category=cat, is_sensitive=sens, is_emergency=emerg,
                           requires_approval=appr, default_priority=prio, student_visible=(code != "sos"))
        db.add(SV[code])
    db.flush()
    for svc, kws in {"maintenance": ["lift", "leak", "wifi", "roommate", "water"], "counselling": ["stress", "overwhelmed", "lonely"],
                     "academic-support": ["attendance", "assignment", "exam"], "safety-report": ["unsafe", "dark"],
                     "fee-waiver": ["scholarship", "fee"]}.items():
        for k in kws:
            db.add(TriageRule(service_id=SV[svc].id, keyword=k))
    db.add(TriageRule(service_id=SV["safety-report"].id, keyword="followed", weight=3, is_urgent=True))
    db.add(TriageRule(service_id=SV["safety-report"].id, keyword="threat", weight=3, is_urgent=True))
    db.commit()

    admin = SimpleNamespace(id=None)
    u = {}

    def mk(key, role, name, dept=None, **kw):
        user, _ = U.create_user(db, None, email=f"{key}@campus.edu", full_name=name, role_code=role, password=PW,
                                department_id=d[dept].id if dept else None, **kw)
        u[key] = user
        return user

    mk("mentor", "mentor", "Prof Nair", "academic")
    mk("warden", "staff", "Mr Iyer", "hostel")
    mk("warden2", "staff", "Mr Rao", "hostel")
    mk("hostel_head", "dept_head", "Dr Hostel Head", "hostel")
    mk("counsellor", "counsellor", "Ms Kapoor", "wellbeing")
    mk("acad_head", "dept_head", "Dr Acad Head", "academic")
    mk("fin_head", "dept_head", "Dr Fin Head", "finance")
    mk("safety", "staff", "Safety Desk", "safety")
    mk("desk", "staff", "Desk Agent", "desk")
    mk("command", "command", "Campus Command")
    mk("admin", "admin", "Sys Admin")
    for k, roll in (("stu_a", "A001"), ("stu_b", "B002"), ("stu_c", "C003")):
        mk(k, "student", f"Student {k[-1].upper()}", roll_no=roll, mentor_id=u["mentor"].id if k == "stu_a" else None,
           residence_location_id=L["hostel-b"].id)
    db.commit()
    ids = SimpleNamespace(**{k: v.id for k, v in u.items()})
    W = SimpleNamespace(
        db=db, dept={k: v.id for k, v in d.items()}, loc={k: v.id for k, v in L.items()}, svc={k: v.id for k, v in SV.items()},
        users=ids, email=lambda k: f"{k}@campus.edu", app=app,
        api=lambda key: _login(app, f"{key}@campus.edu"),
    )
    yield W
    db.close()


def _login(app, email):
    a = Api(app)
    r = a.login(email)
    assert r.status_code == 200, r.text
    return a


@pytest.fixture()
def db():
    s = SessionLocal()
    yield s
    s.close()

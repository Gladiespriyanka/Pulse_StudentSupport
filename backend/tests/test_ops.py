"""Operational tooling: bootstrap admin, starter catalogue, dev seed guards, worker, permission sync."""
import argparse
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import cli, devseed
from app.config import get_settings
from app.models import Department, Permission, Request, Role, RolePermission, Service, TriageRule, User
from app.models.base import utcnow
from app.starter import load_starter_catalog
from tests.conftest import Api
from tests.test_workflow import submit


def test_bootstrap_admin_creates_exactly_one_admin_with_forced_password_change(app, db, capsys):
    cli.cmd_bootstrap_admin(argparse.Namespace(email="first.admin@campus.edu", password=None))
    out = capsys.readouterr().out
    assert "One-time password" in out
    pw = out.split(":")[-1].strip()
    cli.cmd_bootstrap_admin(argparse.Namespace(email="second@campus.edu", password=None))  # refuses silently: an admin exists
    assert "already exists" in capsys.readouterr().out
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(User)) == 1
    a = Api(app)
    assert a.login("first.admin@campus.edu", pw).status_code == 200
    assert a.get("/admin/users").status_code == 403  # must change the temp password first
    assert a.post("/auth/change-password", {"current_password": pw, "new_password": "My#Admin#Pass2026"}).status_code == 200
    assert a.get("/admin/users").status_code == 200


def test_starter_catalogue_is_idempotent_and_never_overwrites_admin_edits(db):
    first = load_starter_catalog(db)
    db.commit()
    assert first["departments"] == 7 and first["services"] == 9 and first["rules"] > 50
    svc = db.scalar(select(Service).where(Service.code == "counselling"))
    svc.name = "Talk to someone"
    db.commit()
    again = load_starter_catalog(db)
    db.commit()
    assert again == {"departments": 0, "locations": 0, "services": 0, "rules": 0}
    db.refresh(svc)
    assert svc.name == "Talk to someone"
    assert db.scalar(select(func.count()).select_from(Department).where(Department.is_triage_desk)) == 1
    # contains no people and no activity: it is configuration only
    assert db.scalar(select(func.count()).select_from(User)) == 0 and db.scalar(select(func.count()).select_from(Request)) == 0


def test_starter_routing_rules_work_end_to_end(app, db):
    load_starter_catalog(db)
    from app.services import users as U

    U.create_user(db, None, email="s1@campus.edu", full_name="S One", role_code="student", password="Test#Pass12345", roll_no="Z1")
    db.commit()
    s = Api(app)
    s.login("s1@campus.edu", "Test#Pass12345")
    p = s.post("/triage/preview", {"description": "I am being followed and feel unsafe near the sports gate"}).json()
    assert p["service"] == "safety-report" and p["urgent"] and p["location"] == "sports-gate"
    p = s.post("/triage/preview", {"description": "My scholarship fee waiver is pending"}).json()
    assert p["service"] == "fee-waiver"


def test_seed_dev_refuses_production_and_non_dev_databases(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "app_env", "production")
    with pytest.raises(SystemExit, match="production"):
        devseed.assert_safe_target()
    monkeypatch.setattr(s, "app_env", "development")
    monkeypatch.setattr(s, "database_url", "postgresql+psycopg://u:p@db/pulse")
    with pytest.raises(SystemExit, match="does not look like"):
        devseed.assert_safe_target()
    monkeypatch.setattr(s, "database_url", "postgresql+psycopg://u:p@db/pulse_prod")
    with pytest.raises(SystemExit):
        devseed.assert_safe_target()
    monkeypatch.setattr(s, "database_url", "postgresql+psycopg://u:p@db/pulse_demo")
    devseed.assert_safe_target()  # allowed


def test_dev_seed_builds_a_consistent_demo_dataset_that_obeys_the_rules(app, db):
    out = devseed.seed(reset=True)
    assert out["users"] >= 30 and out["requests"] >= 30
    with pytest.raises(SystemExit):  # second run without --reset refuses to mix data
        devseed.seed(reset=False)
    a = Api(app)
    assert a.login("aarav@campus.edu", get_settings().demo_password).status_code == 200
    mine = a.get("/requests?mine=true").json()
    assert mine["total"] == 3 and a.get("/cases").json()["items"][0]["status"] == "suggested"
    cmd = Api(app)
    cmd.login("command@campus.edu", get_settings().demo_password)
    s = cmd.get("/reports/summary").json()
    assert s["requests"]["created"] >= 30 and s["feedback"]["responses"] > 5
    assert cmd.get("/intelligence/incident-clusters").json()  # the unlinked Block C cluster
    # data integrity: every request in the seed has a department, an SLA clock or a pending approval
    rows = db.scalars(select(Request)).all()
    assert all(r.department_id for r in rows)
    assert all(r.sla_started_at or r.status in ("pending_approval",) for r in rows)


def test_worker_sweep_runs_and_respects_the_advisory_lock(world, db):
    from sqlalchemy import text

    from app import worker
    from app.models import Request

    req = submit(world.api("stu_a"))
    r = db.get(Request, req["id"])
    r.sla_started_at = utcnow() - timedelta(hours=10)
    r.response_due_at = r.sla_started_at + timedelta(hours=8)
    r.resolution_due_at = r.sla_started_at + timedelta(hours=72)
    db.commit()
    stats = worker.sweep_once()
    assert stats and stats["escalated_level1"] == 1
    # while another session holds the lock, a second worker skips instead of double-sweeping
    other = world.db
    other.execute(text("select pg_advisory_lock(:k)"), {"k": worker.LOCK_ID})
    try:
        assert worker.sweep_once() is None  # skipped: another instance is sweeping
    finally:
        other.execute(text("select pg_advisory_unlock(:k)"), {"k": worker.LOCK_ID})
        other.commit()


def test_sync_permissions_adds_new_codes_without_touching_existing_grants(db, monkeypatch):
    from app import rbac

    monkeypatch.setitem(rbac.PERMISSIONS, "report.export_all", "Export everything")
    monkeypatch.setitem(rbac.ROLES["command"], "permissions", rbac.ROLES["command"]["permissions"] + ["report.export_all"])
    before = db.scalar(select(func.count()).select_from(RolePermission))
    cli.cmd_sync_permissions(None)
    db.expire_all()
    assert db.scalar(select(Permission).where(Permission.code == "report.export_all"))
    cmd = db.scalar(select(Role).where(Role.code == "command"))
    assert "report.export_all" in [p.code for p in cmd.permissions]
    assert db.scalar(select(func.count()).select_from(RolePermission)) == before + 1
    cli.cmd_sync_permissions(None)  # idempotent
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(RolePermission)) == before + 1


def test_key_rotation_reencrypts_rows_so_old_keys_can_be_retired(world, db, monkeypatch):
    from cryptography.fernet import Fernet
    from sqlalchemy import text

    from app.security import crypto

    st = get_settings()
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setattr(st, "field_encryption_keys", old)
    crypto.reset_crypto_cache()
    rid = submit(world.api("stu_a"), "Private words encrypted under the OLD key")["id"]
    raw_old = db.execute(text("select description from requests where id=:i"), {"i": rid}).scalar()
    # rotate: new key first, old key retained
    monkeypatch.setattr(st, "field_encryption_keys", f"{new},{old}")
    crypto.reset_crypto_cache()
    cli.cmd_rotate_keys(None)
    db.expire_all()
    raw_new = db.execute(text("select description from requests where id=:i"), {"i": rid}).scalar()
    assert raw_new != raw_old and raw_new.startswith("enc1:")
    # retire the old key: data is still readable because it now lives under the new key
    monkeypatch.setattr(st, "field_encryption_keys", new)
    crypto.reset_crypto_cache()
    assert "OLD key" in world.api("stu_a").get(f"/requests/{rid}").json()["description"]
    monkeypatch.setattr(st, "field_encryption_keys", "")
    crypto.reset_crypto_cache()

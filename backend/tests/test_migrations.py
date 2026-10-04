"""Migrations are tested on a scratch database: up, constraints, down, up again, no model drift."""
import os
import uuid

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.config import get_settings
from app.models import Base
from app.rbac import PERMISSIONS, ROLES

HERE = os.path.dirname(__file__)


@pytest.fixture()
def scratch():
    base = get_settings().database_url
    name = "pulse_migtest"
    admin = create_engine(base.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f"DROP DATABASE IF EXISTS {name}"))
        c.execute(text(f"CREATE DATABASE {name}"))
    url = base.rsplit("/", 1)[0] + "/" + name
    cfg = Config(os.path.join(HERE, "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(HERE, "..", "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    eng = create_engine(url)
    yield cfg, eng
    eng.dispose()
    with admin.connect() as c:
        c.execute(text(f"DROP DATABASE IF EXISTS {name}"))
    admin.dispose()


def tables(eng):
    with eng.connect() as c:
        return {r[0] for r in c.execute(text("select tablename from pg_tables where schemaname='public'"))}


def test_upgrade_downgrade_upgrade_roundtrip(scratch):
    cfg, eng = scratch
    command.upgrade(cfg, "head")
    t = tables(eng)
    for need in ("users", "roles", "permissions", "role_permissions", "students", "staff", "departments", "services", "incidents", "problems",
                 "changes", "cases", "requests", "tasks", "approvals", "sla_policies", "notifications", "knowledge_articles", "assets",
                 "audit_logs", "consent_grants", "access_requests", "feedback", "refresh_tokens", "request_events", "locations", "triage_rules"):
        assert need in t, need
    command.downgrade(cfg, "base")
    assert tables(eng) == {"alembic_version"}
    command.upgrade(cfg, "head")
    assert "users" in tables(eng)


def test_no_drift_between_models_and_migrated_schema(scratch):
    cfg, eng = scratch
    command.upgrade(cfg, "head")
    with eng.connect() as c:
        diff = compare_metadata(MigrationContext.configure(c, opts={"compare_type": True}), Base.metadata)
    assert diff == [], diff


def test_baseline_seed_matches_rbac_catalogue_and_has_no_demo_data(scratch):
    cfg, eng = scratch
    command.upgrade(cfg, "head")
    with eng.connect() as c:
        assert c.scalar(text("select count(*) from permissions")) == len(PERMISSIONS)
        assert c.scalar(text("select count(*) from roles")) == len(ROLES)
        for code, r in ROLES.items():
            got = {x[0] for x in c.execute(text("select p.code from role_permissions rp join roles r on r.id=rp.role_id join permissions p on p.id=rp.permission_id where r.code=:c"), {"c": code})}
            assert got == set(r["permissions"]), code
        assert c.scalar(text("select count(*) from sla_policies where service_id is null")) == 4
        for t in ("users", "students", "requests", "departments", "services", "incidents", "cases"):
            assert c.scalar(text(f"select count(*) from {t}")) == 0, f"{t} must be empty in a fresh production database"


def _exec_fails(eng, sql, params=None, exc=(IntegrityError, DBAPIError)):
    with pytest.raises(exc):
        with eng.begin() as c:
            c.execute(text(sql), params or {})


def test_database_enforces_integrity_rules(scratch):
    cfg, eng = scratch
    command.upgrade(cfg, "head")
    rid = uuid.uuid4()
    with eng.begin() as c:
        role = c.scalar(text("select id from roles where code='student'"))
        for i in (1, 2):
            c.execute(text("insert into users(id,email,password_hash,full_name,role_id) values (:i,:e,'h','n',:r)"),
                      {"i": uuid.uuid4() if i == 1 else rid, "e": f"u{i}@x.edu", "r": role})
        c.execute(text("insert into departments(id,code,name,is_triage_desk) values (gen_random_uuid(),'d1','D1',true)"))
    # case-insensitive unique email
    _exec_fails(eng, "insert into users(id,email,password_hash,full_name,role_id) values (gen_random_uuid(),'U1@X.EDU','h','n',(select id from roles limit 1))")
    # only one triage desk
    _exec_fails(eng, "insert into departments(id,code,name,is_triage_desk) values (gen_random_uuid(),'d2','D2',true)")
    # invalid enum value rejected by CHECK
    _exec_fails(eng, "insert into departments(id,code,name) values (gen_random_uuid(),'d3','D3')  returning 1 /*ok*/; insert into locations(id,code,name,kind) values (gen_random_uuid(),'l','L','moon')")
    # a task needs exactly one parent
    _exec_fails(eng, "insert into tasks(id,title,status,priority) values (gen_random_uuid(),'t','todo','low')")
    # approval needs exactly one subject; rejection needs a reason
    _exec_fails(eng, "insert into approvals(id,approver_role,status) values (gen_random_uuid(),'dept_head','pending')")
    # one active case per student
    with eng.begin() as c:
        c.execute(text("insert into cases(id,student_id,status,origin,title) values (gen_random_uuid(),:s,'open','rule','t')"), {"s": rid})
    _exec_fails(eng, "insert into cases(id,student_id,status,origin,title) values (gen_random_uuid(),:s,'suggested','rule','t2')", {"s": rid})
    # FK: request must reference real users/services
    _exec_fails(eng, "insert into requests(id,requester_id,source,department_id,title,description,priority,status) values (gen_random_uuid(),gen_random_uuid(),'portal',gen_random_uuid(),'t','d','low','new')")
    # anonymous requests only allowed for the peer channel
    with eng.begin() as c:
        c.execute(text("insert into services(id,code,name,department_id,category,default_priority) select gen_random_uuid(),'s','S',id,'other','low' from departments limit 1"))
    _exec_fails(eng, "insert into requests(id,requester_id,source,department_id,title,description,priority,status) select gen_random_uuid(),NULL,'portal',id,'t','d','low','new' from departments limit 1")
    # SLA: resolution must not be shorter than response
    _exec_fails(eng, "insert into sla_policies(id,priority,response_minutes,resolution_minutes) values (gen_random_uuid(),'low',100,10)")
    # audit log is append-only at the database level
    with eng.begin() as c:
        c.execute(text("insert into audit_logs(action) values ('x')"))
    _exec_fails(eng, "update audit_logs set action='y'")
    _exec_fails(eng, "delete from audit_logs")
    _exec_fails(eng, "truncate audit_logs")

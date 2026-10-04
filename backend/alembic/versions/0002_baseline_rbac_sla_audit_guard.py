"""baseline RBAC + default SLA policies + append-only audit log

Frozen snapshot of the permission catalogue at v1 (later additions go in new migrations or
`python -m app.cli sync-permissions`). Contains system configuration only - no demo data.

Revision ID: 0002
Revises: 0001
"""
import json
import uuid

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SNAPSHOT = json.loads(r'''
{
 "permissions": {
  "access.decide_own": "Approve or deny break-glass requests about oneself",
  "access.request": "Request break-glass access from a student",
  "admin.departments": "Manage departments and locations",
  "admin.import": "Run CSV imports",
  "admin.permissions": "Manage role permissions",
  "admin.services": "Manage the service catalogue and triage rules",
  "admin.sla": "Configure SLA policies",
  "admin.system": "Run maintenance jobs (SLA sweep)",
  "admin.users": "Manage users, students and staff",
  "aggregate.read": "Read campus aggregates (sensitive cells are masked)",
  "approval.decide": "Decide approvals within the user's authority",
  "asset.manage": "Manage assets",
  "asset.read": "Read assets",
  "audit.read": "Read the full audit log",
  "audit.read_own": "See who accessed one's own information",
  "case.manage": "Confirm, dismiss, run and close support cases",
  "case.message": "Post in a support case thread",
  "case.read": "Read support cases the user participates in (or owns as student)",
  "change.manage": "Create and manage changes",
  "change.read": "Read changes",
  "consent.manage_own": "Manage own consent",
  "feedback.create": "Give feedback on own requests",
  "incident.manage": "Create and manage incidents",
  "incident.read": "Read incidents",
  "knowledge.manage": "Create and edit knowledge articles",
  "knowledge.read": "Read knowledge articles",
  "notification.read_own": "Read own notifications",
  "problem.manage": "Create and manage problems",
  "problem.read": "Read problems",
  "report.read": "Read operational reports",
  "request.assign": "Assign / reassign / re-route requests",
  "request.create": "Submit service requests",
  "request.escalate": "Manually escalate a request",
  "request.manage": "Acknowledge, progress, resolve and comment on department requests",
  "request.read_all_meta": "Read request metadata (no free text) across departments",
  "request.read_department": "Read requests of the user's department (consent rules apply)",
  "request.read_own": "Read own requests",
  "task.manage": "Create and update tasks in scope",
  "task.read_own": "Read tasks assigned to the user"
 },
 "roles": {
  "admin": {
   "description": "Users, catalogue, SLA, permissions, imports, audit. Cannot read request text.",
   "name": "System administrator",
   "permissions": [
    "admin.departments",
    "admin.import",
    "admin.permissions",
    "admin.services",
    "admin.sla",
    "admin.system",
    "admin.users",
    "aggregate.read",
    "asset.manage",
    "asset.read",
    "audit.read",
    "knowledge.read",
    "notification.read_own",
    "report.read"
   ]
  },
  "command": {
   "description": "Cross-department oversight: aggregates, SLA, incidents, problems, changes. Cannot read request text.",
   "name": "Campus command",
   "permissions": [
    "aggregate.read",
    "approval.decide",
    "asset.read",
    "change.manage",
    "change.read",
    "incident.manage",
    "incident.read",
    "knowledge.read",
    "notification.read_own",
    "problem.manage",
    "problem.read",
    "report.read",
    "request.assign",
    "request.escalate",
    "request.read_all_meta",
    "task.manage",
    "task.read_own"
   ]
  },
  "counsellor": {
   "description": "Wellbeing staff; runs consent-based support cases.",
   "name": "Counsellor",
   "permissions": [
    "access.request",
    "case.manage",
    "case.message",
    "case.read",
    "knowledge.read",
    "notification.read_own",
    "report.read",
    "request.manage",
    "request.read_department",
    "task.manage",
    "task.read_own"
   ]
  },
  "dept_head": {
   "description": "Staff permissions plus assignment, approvals, problems, changes and knowledge.",
   "name": "Department head",
   "permissions": [
    "access.request",
    "approval.decide",
    "asset.manage",
    "asset.read",
    "case.message",
    "case.read",
    "change.manage",
    "change.read",
    "incident.manage",
    "incident.read",
    "knowledge.manage",
    "knowledge.read",
    "notification.read_own",
    "problem.manage",
    "problem.read",
    "report.read",
    "request.assign",
    "request.escalate",
    "request.manage",
    "request.read_department",
    "task.manage",
    "task.read_own"
   ]
  },
  "mentor": {
   "description": "Sees only support cases the student has consented to share.",
   "name": "Faculty mentor",
   "permissions": [
    "access.request",
    "case.manage",
    "case.message",
    "case.read",
    "knowledge.read",
    "notification.read_own",
    "task.manage",
    "task.read_own"
   ]
  },
  "staff": {
   "description": "Works the queue of their own department.",
   "name": "Department staff",
   "permissions": [
    "access.request",
    "asset.read",
    "case.message",
    "case.read",
    "change.read",
    "incident.manage",
    "incident.read",
    "knowledge.read",
    "notification.read_own",
    "problem.read",
    "report.read",
    "request.manage",
    "request.read_department",
    "task.manage",
    "task.read_own"
   ]
  },
  "student": {
   "description": "Raises requests, controls consent, sees own cases.",
   "name": "Student",
   "permissions": [
    "access.decide_own",
    "audit.read_own",
    "case.message",
    "case.read",
    "consent.manage_own",
    "feedback.create",
    "knowledge.read",
    "notification.read_own",
    "request.create",
    "request.read_own"
   ]
  }
 }
}
''')

# (priority, response_minutes, resolution_minutes) - campus defaults, editable by an admin.
DEFAULT_SLA = [
    ("critical", 15, 240),
    ("high", 120, 1440),
    ("medium", 480, 4320),
    ("low", 1440, 10080),
]


def upgrade() -> None:
    conn = op.get_bind()
    perm_ids = {}
    for code, desc in SNAPSHOT["permissions"].items():
        pid = uuid.uuid4()
        perm_ids[code] = pid
        conn.execute(sa.text("INSERT INTO permissions (id, code, description) VALUES (:i, :c, :d)"), {"i": pid, "c": code, "d": desc})
    for code, r in SNAPSHOT["roles"].items():
        rid = uuid.uuid4()
        conn.execute(sa.text("INSERT INTO roles (id, code, name, description, is_system) VALUES (:i, :c, :n, :d, true)"),
                     {"i": rid, "c": code, "n": r["name"], "d": r["description"]})
        for p in r["permissions"]:
            conn.execute(sa.text("INSERT INTO role_permissions (role_id, permission_id) VALUES (:r, :p)"), {"r": rid, "p": perm_ids[p]})
    for prio, resp, res in DEFAULT_SLA:
        conn.execute(sa.text(
            "INSERT INTO sla_policies (id, service_id, priority, response_minutes, resolution_minutes, at_risk_pct, severe_multiplier, is_active) "
            "VALUES (:i, NULL, :p, :a, :b, 75, 2.0, true)"), {"i": uuid.uuid4(), "p": prio, "a": resp, "b": res})

    # Audit log is append-only: block UPDATE, DELETE and TRUNCATE.
    op.execute("""
    CREATE OR REPLACE FUNCTION audit_logs_immutable() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'audit_logs is append-only (%% not allowed)', TG_OP USING ERRCODE = 'integrity_constraint_violation';
    END;
    $$ LANGUAGE plpgsql;
    """.replace("%%", "%"))
    op.execute("CREATE TRIGGER audit_logs_no_update_delete BEFORE UPDATE OR DELETE ON audit_logs FOR EACH ROW EXECUTE FUNCTION audit_logs_immutable();")
    op.execute("CREATE TRIGGER audit_logs_no_truncate BEFORE TRUNCATE ON audit_logs FOR EACH STATEMENT EXECUTE FUNCTION audit_logs_immutable();")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_logs_no_truncate ON audit_logs")
    op.execute("DROP TRIGGER IF EXISTS audit_logs_no_update_delete ON audit_logs")
    op.execute("DROP FUNCTION IF EXISTS audit_logs_immutable()")
    op.execute("DELETE FROM sla_policies WHERE service_id IS NULL")
    op.execute("DELETE FROM role_permissions")
    op.execute("DELETE FROM roles")
    op.execute("DELETE FROM permissions")

"""Permission catalogue + default role grants.

Authorization has two layers, both enforced server-side:
  1. Permission codes (this file, editable per role by an admin) gate *which endpoints* a role may call.
  2. Row-level scope rules (app/services/access.py) gate *which records* the caller may see:
     own records, own department, case participation, consent grants and break-glass grants.
Command and Admin deliberately have no content-reading permission (least privilege).
"""
from __future__ import annotations

PERMISSIONS: dict[str, str] = {
    # requests
    "request.create": "Submit service requests",
    "request.read_own": "Read own requests",
    "request.read_department": "Read requests of the user's department (consent rules apply)",
    "request.read_all_meta": "Read request metadata (no free text) across departments",
    "request.manage": "Acknowledge, progress, resolve and comment on department requests",
    "request.assign": "Assign / reassign / re-route requests",
    "request.escalate": "Manually escalate a request",
    # tasks
    "task.manage": "Create and update tasks in scope",
    "task.read_own": "Read tasks assigned to the user",
    # approvals
    "approval.decide": "Decide approvals within the user's authority",
    # itsm
    "incident.read": "Read incidents",
    "incident.manage": "Create and manage incidents",
    "problem.read": "Read problems",
    "problem.manage": "Create and manage problems",
    "change.read": "Read changes",
    "change.manage": "Create and manage changes",
    # care
    "case.read": "Read support cases the user participates in (or owns as student)",
    "case.manage": "Confirm, dismiss, run and close support cases",
    "case.message": "Post in a support case thread",
    "consent.manage_own": "Manage own consent",
    "access.request": "Request break-glass access from a student",
    "access.decide_own": "Approve or deny break-glass requests about oneself",
    "feedback.create": "Give feedback on own requests",
    "audit.read_own": "See who accessed one's own information",
    # knowledge / assets / notifications
    "knowledge.read": "Read knowledge articles",
    "knowledge.manage": "Create and edit knowledge articles",
    "asset.read": "Read assets",
    "asset.manage": "Manage assets",
    "notification.read_own": "Read own notifications",
    # reporting
    "report.read": "Read operational reports",
    "aggregate.read": "Read campus aggregates (sensitive cells are masked)",
    # administration
    "admin.users": "Manage users, students and staff",
    "admin.departments": "Manage departments and locations",
    "admin.services": "Manage the service catalogue and triage rules",
    "admin.sla": "Configure SLA policies",
    "admin.permissions": "Manage role permissions",
    "admin.import": "Run CSV imports",
    "admin.system": "Run maintenance jobs (SLA sweep)",
    "audit.read": "Read the full audit log",
}

_COMMON = ["notification.read_own", "knowledge.read"]

ROLES: dict[str, dict] = {
    "student": {
        "name": "Student",
        "description": "Raises requests, controls consent, sees own cases.",
        "permissions": ["request.create", "request.read_own", "feedback.create", "case.read", "case.message",
                        "consent.manage_own", "access.decide_own", "audit.read_own", *_COMMON],
    },
    "staff": {
        "name": "Department staff",
        "description": "Works the queue of their own department.",
        "permissions": ["request.read_department", "request.manage", "task.manage", "task.read_own", "incident.read",
                        "incident.manage", "problem.read", "change.read", "case.read", "case.message", "access.request",
                        "asset.read", "report.read", *_COMMON],
    },
    "dept_head": {
        "name": "Department head",
        "description": "Staff permissions plus assignment, approvals, problems, changes and knowledge.",
        "permissions": ["request.read_department", "request.manage", "request.assign", "request.escalate", "task.manage",
                        "task.read_own", "approval.decide", "incident.read", "incident.manage", "problem.read",
                        "problem.manage", "change.read", "change.manage", "case.read", "case.message", "access.request",
                        "knowledge.manage", "asset.read", "asset.manage", "report.read", *_COMMON],
    },
    "counsellor": {
        "name": "Counsellor",
        "description": "Wellbeing staff; runs consent-based support cases.",
        "permissions": ["request.read_department", "request.manage", "task.manage", "task.read_own", "case.read",
                        "case.manage", "case.message", "access.request", "report.read", *_COMMON],
    },
    "mentor": {
        "name": "Faculty mentor",
        "description": "Sees only support cases the student has consented to share.",
        "permissions": ["task.manage", "task.read_own", "case.read", "case.manage", "case.message", "access.request",
                        *_COMMON],
    },
    "command": {
        "name": "Campus command",
        "description": "Cross-department oversight: aggregates, SLA, incidents, problems, changes. Cannot read request text.",
        "permissions": ["request.read_all_meta", "request.assign", "request.escalate", "task.manage", "task.read_own",
                        "approval.decide", "incident.read", "incident.manage", "problem.read", "problem.manage",
                        "change.read", "change.manage", "asset.read", "report.read", "aggregate.read", *_COMMON],
    },
    "admin": {
        "name": "System administrator",
        "description": "Users, catalogue, SLA, permissions, imports, audit. Cannot read request text.",
        "permissions": ["admin.users", "admin.departments", "admin.services", "admin.sla", "admin.permissions",
                        "admin.import", "admin.system", "audit.read", "asset.read", "asset.manage", "report.read",
                        "aggregate.read", *_COMMON],
    },
}

STAFF_ROLES = {"staff", "dept_head", "counsellor"}  # must belong to a department
CARE_ROLES = {"staff", "dept_head", "counsellor", "mentor"}  # may request break-glass / join cases
ASSIGNABLE_ROLES = {"staff", "dept_head", "counsellor"}

# Guard rails: removing these from the admin role would lock everyone out of administration.
PROTECTED_ADMIN_PERMISSIONS = {"admin.permissions", "admin.users"}

# Student-facing minimum.
SYSTEM_ROLE_CODES = set(ROLES)

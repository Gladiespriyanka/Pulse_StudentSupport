# Role × permission matrix

Generated from `backend/app/rbac.py`. Permissions gate *which endpoints* a role may call; row-level rules
(own records, own department, consent, break-glass) then decide *which records*. See ARCHITECTURE.md. Administrators can edit these grants at runtime (Admin → Permissions).

| Permission | student | staff | dept_head | counsellor | mentor | command | admin |
|---|---|---|---|---|---|---|---|
| `request.create` — Submit service requests | ✔ |  |  |  |  |  |  |
| `request.read_own` — Read own requests | ✔ |  |  |  |  |  |  |
| `request.read_department` — Read requests of the user's department (consent rules apply) |  | ✔ | ✔ | ✔ |  |  |  |
| `request.read_all_meta` — Read request metadata (no free text) across departments |  |  |  |  |  | ✔ |  |
| `request.manage` — Acknowledge, progress, resolve and comment on department requests |  | ✔ | ✔ | ✔ |  |  |  |
| `request.assign` — Assign / reassign / re-route requests |  |  | ✔ |  |  | ✔ |  |
| `request.escalate` — Manually escalate a request |  |  | ✔ |  |  | ✔ |  |
| `task.manage` — Create and update tasks in scope |  | ✔ | ✔ | ✔ | ✔ | ✔ |  |
| `task.read_own` — Read tasks assigned to the user |  | ✔ | ✔ | ✔ | ✔ | ✔ |  |
| `approval.decide` — Decide approvals within the user's authority |  |  | ✔ |  |  | ✔ |  |
| `incident.read` — Read incidents |  | ✔ | ✔ |  |  | ✔ |  |
| `incident.manage` — Create and manage incidents |  | ✔ | ✔ |  |  | ✔ |  |
| `problem.read` — Read problems |  | ✔ | ✔ |  |  | ✔ |  |
| `problem.manage` — Create and manage problems |  |  | ✔ |  |  | ✔ |  |
| `change.read` — Read changes |  | ✔ | ✔ |  |  | ✔ |  |
| `change.manage` — Create and manage changes |  |  | ✔ |  |  | ✔ |  |
| `case.read` — Read support cases the user participates in (or owns as student) | ✔ | ✔ | ✔ | ✔ | ✔ |  |  |
| `case.manage` — Confirm, dismiss, run and close support cases |  |  |  | ✔ | ✔ |  |  |
| `case.message` — Post in a support case thread | ✔ | ✔ | ✔ | ✔ | ✔ |  |  |
| `consent.manage_own` — Manage own consent | ✔ |  |  |  |  |  |  |
| `access.request` — Request break-glass access from a student |  | ✔ | ✔ | ✔ | ✔ |  |  |
| `access.decide_own` — Approve or deny break-glass requests about oneself | ✔ |  |  |  |  |  |  |
| `feedback.create` — Give feedback on own requests | ✔ |  |  |  |  |  |  |
| `audit.read_own` — See who accessed one's own information | ✔ |  |  |  |  |  |  |
| `knowledge.read` — Read knowledge articles | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| `knowledge.manage` — Create and edit knowledge articles |  |  | ✔ |  |  |  |  |
| `asset.read` — Read assets |  | ✔ | ✔ |  |  | ✔ | ✔ |
| `asset.manage` — Manage assets |  |  | ✔ |  |  |  | ✔ |
| `notification.read_own` — Read own notifications | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| `report.read` — Read operational reports |  | ✔ | ✔ | ✔ |  | ✔ | ✔ |
| `aggregate.read` — Read campus aggregates (sensitive cells are masked) |  |  |  |  |  | ✔ | ✔ |
| `admin.users` — Manage users, students and staff |  |  |  |  |  |  | ✔ |
| `admin.departments` — Manage departments and locations |  |  |  |  |  |  | ✔ |
| `admin.services` — Manage the service catalogue and triage rules |  |  |  |  |  |  | ✔ |
| `admin.sla` — Configure SLA policies |  |  |  |  |  |  | ✔ |
| `admin.permissions` — Manage role permissions |  |  |  |  |  |  | ✔ |
| `admin.import` — Run CSV imports |  |  |  |  |  |  | ✔ |
| `admin.system` — Run maintenance jobs (SLA sweep) |  |  |  |  |  |  | ✔ |
| `audit.read` — Read the full audit log |  |  |  |  |  |  | ✔ |

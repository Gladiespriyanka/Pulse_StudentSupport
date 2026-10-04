# API reference

Generated from the OpenAPI schema by `scripts/gen-docs.py`. Interactive docs: `/api/docs` (Swagger) and `/api/redoc`.
**117 operations.**
All paths are under `/api/v1`. See [API.md](API.md) for authentication, errors and pagination.

## admin

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/admin/audit-logs` | Audit Logs |
| `GET` | `/api/v1/admin/audit-logs/export.csv` | Audit Export |
| `POST` | `/api/v1/admin/imports/{kind}` | Validate (dry_run=true, default) or apply a CSV import. Apply is all-or-nothing. |
| `GET` | `/api/v1/admin/imports/{kind}/template` | CSV header template for an import type |
| `GET` | `/api/v1/admin/roles` | Roles with their permissions, plus the full permission catalogue |
| `PUT` | `/api/v1/admin/roles/{code}/permissions` | Replace a role's permissions (guard rails prevent admin lock-out) |
| `POST` | `/api/v1/admin/system/sla-sweep` | Run the SLA/escalation sweep now (the worker also runs it on a timer) |
| `GET` | `/api/v1/admin/users` | List Users |
| `POST` | `/api/v1/admin/users` | Create a user (omit password to get a one-time temporary password) |
| `GET` | `/api/v1/admin/users/{user_id}` | Get User |
| `PATCH` | `/api/v1/admin/users/{user_id}` | Patch User |
| `POST` | `/api/v1/admin/users/{user_id}/reset-password` | Issue a one-time temporary password (user must change it at next sign-in) |
| `POST` | `/api/v1/admin/users/{user_id}/unlock` | Unlock |

## admin-assets

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/admin/assets` | List admin-assets |
| `POST` | `/api/v1/admin/assets` | Create admin-asset |
| `PATCH` | `/api/v1/admin/assets/{item_id}` | Update admin-assets |

## admin-departments

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/admin/departments` | List admin-departments |
| `POST` | `/api/v1/admin/departments` | Create admin-department |
| `PATCH` | `/api/v1/admin/departments/{item_id}` | Update admin-departments |

## admin-locations

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/admin/locations` | List admin-locations |
| `POST` | `/api/v1/admin/locations` | Create admin-location |
| `PATCH` | `/api/v1/admin/locations/{item_id}` | Update admin-locations |

## admin-services

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/admin/services` | List admin-services |
| `POST` | `/api/v1/admin/services` | Create admin-service |
| `PATCH` | `/api/v1/admin/services/{item_id}` | Update admin-services |

## admin-sla

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/admin/sla-policies` | List admin-sla |
| `POST` | `/api/v1/admin/sla-policies` | Create admin-sla |
| `PATCH` | `/api/v1/admin/sla-policies/{item_id}` | Update admin-sla |
| `DELETE` | `/api/v1/admin/sla-policies/{item_id}` | Delete admin-sla |

## admin-triage

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/admin/triage-rules` | List admin-triage |
| `POST` | `/api/v1/admin/triage-rules` | Create admin-triage |
| `PATCH` | `/api/v1/admin/triage-rules/{item_id}` | Update admin-triage |
| `DELETE` | `/api/v1/admin/triage-rules/{item_id}` | Delete admin-triage |

## auth

| Method | Path | Summary |
|---|---|---|
| `POST` | `/api/v1/auth/change-password` | Change Password |
| `POST` | `/api/v1/auth/login` | Sign in (sets httpOnly session cookies) |
| `POST` | `/api/v1/auth/logout` | End the session and revoke the refresh token |
| `GET` | `/api/v1/auth/me` | Current user, role and permissions |
| `POST` | `/api/v1/auth/refresh` | Rotate the refresh token and issue a new access token |

## care

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/access-requests` | List Access |
| `POST` | `/api/v1/access-requests` | Request Access |
| `POST` | `/api/v1/access-requests/{access_id}/decision` | Decide Access |
| `POST` | `/api/v1/access-requests/{access_id}/revoke` | Revoke Access |
| `GET` | `/api/v1/cases` | Support cases you may see |
| `POST` | `/api/v1/cases` | Open a case for a student who has consented to you |
| `GET` | `/api/v1/cases/{case_id}` | Get Case |
| `GET` | `/api/v1/cases/{case_id}/candidates` | People the student has allowed who could join this case |
| `POST` | `/api/v1/cases/{case_id}/close` | Close |
| `POST` | `/api/v1/cases/{case_id}/confirm` | A person confirms the suggestion (nothing happens until they do) |
| `POST` | `/api/v1/cases/{case_id}/dismiss` | Dismiss |
| `POST` | `/api/v1/cases/{case_id}/messages` | Message |
| `POST` | `/api/v1/cases/{case_id}/owner` | Owner |
| `POST` | `/api/v1/cases/{case_id}/participants` | Add Participant |
| `DELETE` | `/api/v1/cases/{case_id}/participants/{user_id}` | Remove Participant |
| `POST` | `/api/v1/cases/{case_id}/response` | Student accepts or declines outreach |
| `POST` | `/api/v1/cases/{case_id}/status` | Status |
| `POST` | `/api/v1/cases/{case_id}/tasks` | Add a support action (intervention) |
| `GET` | `/api/v1/consents/me` | Who may join my support team |
| `PUT` | `/api/v1/consents/me` | Set Consent |
| `GET` | `/api/v1/me/access-log` | Who looked at my information |

## catalog

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/catalog/assets` | Assets |
| `GET` | `/api/v1/catalog/departments` | Departments |
| `GET` | `/api/v1/catalog/locations` | Locations |
| `GET` | `/api/v1/catalog/services` | Service catalogue (students see only student-visible services) |
| `GET` | `/api/v1/catalog/staff` | Assignable staff (for assignment pickers) |

## itsm

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/changes` | List Changes |
| `POST` | `/api/v1/changes` | Problem -> Change: raise a change (starts as a draft) |
| `GET` | `/api/v1/changes/{change_id}` | Get Change |
| `PATCH` | `/api/v1/changes/{change_id}` | Edit a draft change |
| `POST` | `/api/v1/changes/{change_id}/cancel` | Cancel |
| `POST` | `/api/v1/changes/{change_id}/complete` | Complete |
| `POST` | `/api/v1/changes/{change_id}/start` | Start |
| `POST` | `/api/v1/changes/{change_id}/submit` | Send for approval |
| `GET` | `/api/v1/incidents` | List Incidents |
| `POST` | `/api/v1/incidents` | Create Incident |
| `GET` | `/api/v1/incidents/{incident_id}` | Get Incident |
| `PATCH` | `/api/v1/incidents/{incident_id}` | Patch Incident |
| `POST` | `/api/v1/incidents/{incident_id}/problem` | Incident -> Problem: open or link a problem |
| `POST` | `/api/v1/incidents/{incident_id}/requests` | Link Requests |
| `GET` | `/api/v1/intelligence/incident-clusters` | Locations where open requests are piling up (rule-based) |
| `GET` | `/api/v1/intelligence/problem-suggestions` | Repeat incidents without a problem record |
| `GET` | `/api/v1/problems` | List Problems |
| `POST` | `/api/v1/problems` | Open a problem directly (usually opened from an incident) |
| `GET` | `/api/v1/problems/{problem_id}` | Get Problem |
| `PATCH` | `/api/v1/problems/{problem_id}` | Patch Problem |

## knowledge

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/knowledge` | Search Kb |
| `POST` | `/api/v1/knowledge` | Create Kb |
| `GET` | `/api/v1/knowledge/{article_id}` | Get Kb |
| `PATCH` | `/api/v1/knowledge/{article_id}` | Patch Kb |

## meta

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/health` | Liveness |
| `GET` | `/api/v1/health/ready` | Readiness (checks the database) |
| `GET` | `/api/v1/meta` | Public, non-sensitive app information for the UI |

## notifications

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/notifications` | List Notifications |
| `POST` | `/api/v1/notifications/read` | Mark some (ids) or all notifications as read |
| `GET` | `/api/v1/notifications/unread-count` | Unread |

## reports

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/reports/briefing` | Weekly briefing generated from real aggregates |
| `GET` | `/api/v1/reports/heatmap` | Location x category x time-of-day counts (small sensitive cells masked) |
| `GET` | `/api/v1/reports/summary` | Operational summary (department roles only ever see their own department) |
| `GET` | `/api/v1/reports/summary.csv` | Department table as CSV (aggregates only) |

## requests

| Method | Path | Summary |
|---|---|---|
| `POST` | `/api/v1/requests` | Submit a request (catalogue choice or free text routed by rules) |
| `GET` | `/api/v1/requests` | List requests in your scope |
| `POST` | `/api/v1/requests/peer-concern` | Anonymous concern about a friend (reporter is not stored) |
| `POST` | `/api/v1/requests/sos` | Ask for immediate help (critical priority, emergency service) |
| `GET` | `/api/v1/requests/{request_id}` | Request detail (text and timeline only if you are entitled to them) |
| `POST` | `/api/v1/requests/{request_id}/assign` | Assign |
| `POST` | `/api/v1/requests/{request_id}/comments` | Comment |
| `POST` | `/api/v1/requests/{request_id}/consent` | Consent |
| `POST` | `/api/v1/requests/{request_id}/escalate` | Escalate |
| `POST` | `/api/v1/requests/{request_id}/feedback` | Feedback |
| `POST` | `/api/v1/requests/{request_id}/reroute` | Reroute |
| `POST` | `/api/v1/requests/{request_id}/status` | Change Status |
| `POST` | `/api/v1/triage/preview` | Show where free text would be routed and which words matched (nothing is stored) |

## tasks

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/approvals` | Approvals waiting for you (or recent decisions) |
| `POST` | `/api/v1/approvals/{approval_id}/decision` | Decide |
| `GET` | `/api/v1/tasks` | Tasks in your scope |
| `POST` | `/api/v1/tasks` | Create Task |
| `PATCH` | `/api/v1/tasks/{task_id}` | Patch Task |
| `POST` | `/api/v1/tasks/{task_id}/feedback` | Task Feedback |

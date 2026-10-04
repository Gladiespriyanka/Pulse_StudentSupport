# Operations

## Processes
| Service | Role | Scale |
|---|---|---|
| `api` | FastAPI under gunicorn (`WEB_CONCURRENCY` workers) | horizontal; stateless |
| `worker` | every `SLA_SWEEP_INTERVAL_SECONDS` (60): at-risk warnings, breach escalation, severe escalation (priority raise), auto-close of resolved requests, case-rule evaluation | run 1; a PostgreSQL advisory lock makes extra instances harmless |
| `web` | nginx: UI, `/api` proxy, edge limits | horizontal |
| `db` | PostgreSQL 16 | single primary |
Run a sweep on demand: Admin → System, or `docker compose run --rm api python -m app.cli sla-sweep`. If the worker is down SLAs are still *displayed* correctly (computed from timestamps); only notifications/escalations pause, and they catch up on the next sweep.

## Logs and monitoring
* JSON-style single-line logs on stdout (`docker compose logs -f api worker web`); every response carries `X-Request-ID`, shown in UI error boxes — ask users for it.
* Health: `GET /api/v1/health` (liveness), `/api/v1/health/ready` (checks the database), `nginx /healthz`; containers have `HEALTHCHECK`.
* Watch the audit log (Admin → Audit, filter `outcome=denied`/`failure`) for `authz.denied`, `auth.locked`, `auth.refresh_reuse_detected`, `auth.login_failed` bursts.

## Common tasks
| Task | How |
|---|---|
| Add users | Admin → Users or CSV import; one-time temporary passwords are shown once |
| Locked-out user | Admin → Users → *Unlock* (or wait 15 minutes) |
| Forgotten password | Admin → Users → *Reset password* (signs the user out everywhere) |
| Staff member leaves | untick *Active* (all their tokens die immediately); reassign their open requests from the queue ("Assign") |
| Change a department's head / role | Admin → Users → Edit (role change signs the user out so permissions refresh) |
| Tune SLAs / routing | Admin → SLA, Admin → Services → Keywords (live, audited) |
| Change what a role can do | Admin → Permissions (live, audited, with lock-out guard rails) |
| Rotate encryption key | 1) generate a new key; 2) set `FIELD_ENCRYPTION_KEYS=<new>,<old>` and restart; 3) `python -m app.cli rotate-keys`; 4) after backups made before the rotation have aged out, remove `<old>` |
| Rotate `JWT_SECRET` | change and restart — everyone signs in again |
| Add a permission shipped in a new release | `python -m app.cli sync-permissions` |
| Verify a database | `python -m app.cli verify-db` |

## Upgrading the schema
Migrations live in `backend/alembic/versions` and run automatically via the `migrate` service. The initial migrations contain only system configuration (roles, permissions, default SLA tiers, the audit trigger) — never demo data.
New migrations: `alembic revision --autogenerate -m "..."`, **review the file**, then `alembic upgrade head`. The test-suite compares the models with the migrated schema and fails on drift.

## Capacity notes
The workload is small (a campus is thousands of users, not millions). Queries use composite and partial indexes (`requests(department_id,status)`, `requests(assignee_id,status)`, partial index on open SLA due dates, GIN on knowledge search). Lists are paginated (max 100); reports aggregate in SQL.

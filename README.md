# PULSE — campus support & early-intervention platform

PULSE helps a college respond to student problems early: a student describes what is happening, rules route it to the
right department, SLAs and escalations keep it moving, and — only with the student's consent — a human check-in can be
suggested when several areas of campus are affected. Command sees aggregates; nobody sees more than their role needs.

This repository is the production refactor of the original single-page prototype (nine JS files, state in `localStorage`,
fake scores and simulations). Everything now runs through a real backend, database, authentication and audit trail.
See [docs/MIGRATION_FROM_PROTOTYPE.md](docs/MIGRATION_FROM_PROTOTYPE.md) for exactly what was kept, rebuilt and removed.

```
 browser ──HTTPS──▶ reverse proxy/TLS ──▶ web (nginx: UI + /api proxy, edge rate limits, CSP)
                                              │
                                              ▼
                                  api (FastAPI, gunicorn)  ◀── worker (SLA sweep, escalation, auto-close, case rule)
                                              │
                                              ▼
                                   PostgreSQL 16 (29 tables, Alembic migrations)
```

| | |
|---|---|
| Backend | FastAPI · SQLAlchemy 2 · Alembic · Pydantic v2 · argon2id · JWT (httpOnly cookies + CSRF) |
| Database | PostgreSQL, 29 normalised tables, FKs / CHECKs / partial-unique indexes, append-only audit log (DB trigger) |
| Auth & RBAC | 7 roles, 39 permissions (editable by an admin), plus row-level rules: own / department / consent / break-glass |
| Workflows | request → triage → (approval) → assignment → SLA → task → resolution → feedback → audit · incident → problem → change · consent-based support cases |
| Frontend | the original look and feel, rebuilt as small ES modules; all data from the API; loading / error / empty states; no browser storage |
| Ops | Docker Compose (prod + isolated demo), migrations, worker, backup/restore + restore drill, `.env.example`, generated API docs |

## Quick start

**Try it (Demo Mode, fake data, isolated database):**
```bash
docker compose -f docker-compose.demo.yml up -d --build      # → http://localhost:8081
# sign in with any chip on the login page; password PulseDemo#2026
```
**Deploy for real:** follow [docs/SETUP.md](docs/SETUP.md) (≈10 minutes):
```bash
./scripts/gen-secrets.sh        # creates .env with fresh secrets (needs only openssl)
docker compose up -d --build    # db → migrations → api + worker → web
docker compose run --rm api python -m app.cli bootstrap-admin --email you@college.edu
docker compose run --rm api python -m app.cli load-starter-catalog     # optional: starter departments/services/keywords
```
Then sign in as the admin, import users by CSV, and adjust departments, services, keywords and SLAs in the Admin screens.

**Develop without Docker:** [docs/SETUP.md § Local development](docs/SETUP.md#local-development-without-docker).

## Documentation

| | |
|---|---|
| [SETUP](docs/SETUP.md) | deploy, TLS, first admin, import, upgrade, local development, demo mode |
| [ARCHITECTURE](docs/ARCHITECTURE.md) | data model, state machines, access rules |
| [RBAC matrix](docs/RBAC_MATRIX.md) | generated role × permission table |
| [SECURITY](docs/SECURITY.md) | controls, threat model, privacy design, hardening checklist, **what is not covered** |
| [INTELLIGENCE](docs/INTELLIGENCE.md) | every rule, its parameters, how quality is measured, why there is no ML |
| [API](docs/API.md) · [reference](docs/API_REFERENCE.md) | auth flow, errors, pagination · all 117 operations (also live at `/api/docs`) |
| [BACKUP_RESTORE](docs/BACKUP_RESTORE.md) | backups, restore, restore drill, key custody |
| [OPERATIONS](docs/OPERATIONS.md) | worker, logs, key rotation, SLA tuning, user lifecycle |
| [TESTING](docs/TESTING.md) | what is tested, how to run it, what is *not* tested |
| [MIGRATION_FROM_PROTOTYPE](docs/MIGRATION_FROM_PROTOTYPE.md) | feature-by-feature mapping, removed features and why |

## Honest status

Verified in development: 140+ backend tests on real PostgreSQL (auth, RBAC matrix, CRUD, persistence, workflows, migrations up/down,
CSV import, error handling, refresh/re-login), 100+ UI checks driving the real frontend against the live API, a full backup → restore →
verify drill, nginx and gunicorn configuration run for real.
**Not verified here:** the Docker images were not built (no Docker in the build environment — Compose files are parsed and
their safety properties asserted, the commands inside them were run directly), and the UI was exercised in jsdom, not a real browser
(visual layout needs a human pass). Known gaps (no email/SSO/MFA, no data-retention job, per-process app rate limiter) are listed in
[docs/SECURITY.md](docs/SECURITY.md#not-covered--known-limitations). Have your institution's data-protection lead review
consent and retention before go-live; this is engineering, not legal advice.

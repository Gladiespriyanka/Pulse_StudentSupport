# Setup

## Production deployment (Docker Compose)

**Prerequisites:** a Linux host with Docker + the Compose plugin, a DNS name, and a TLS terminator (Caddy, Traefik, nginx, a cloud
load balancer) in front of the `web` container. PULSE sets `Secure` cookies, so serve it over HTTPS.

1. **Configure.**
   ```bash
   ./scripts/gen-secrets.sh        # creates .env (mode 600) from .env.example with fresh secrets; needs only openssl
   ```
   Then edit `BOOTSTRAP_ADMIN_EMAIL` and any policy values in `.env`. **Back up `.env` and especially `FIELD_ENCRYPTION_KEYS` somewhere safe and separate from database backups**:
   without that key, encrypted request text cannot be read.
2. **Start.** `docker compose up -d --build`. Order is enforced: `db` (healthy) → `migrate` (runs `alembic upgrade head`, exits) → `api` + `worker` → `web`.
   The stack refuses to start with placeholder secrets, short secrets, malformed keys, `COOKIE_SECURE=false`, or a database named dev/demo/test.
3. **Create the first administrator.**
   ```bash
   docker compose run --rm api python -m app.cli bootstrap-admin --email admin@your-college.edu
   ```
   It prints a one-time password (or uses `BOOTSTRAP_ADMIN_PASSWORD`). The admin must change it at first sign-in. The command does nothing if an admin already exists.
4. **Load configuration.** Either `docker compose run --rm api python -m app.cli load-starter-catalog` (starter departments, locations,
   services and routing keywords — configuration only, no people) **or** build your own in Admin → Departments / Locations / Services, or by CSV
   ([templates](import-templates/)). Exactly one department should be marked *triage desk* (it receives requests no rule can route).
5. **Import people.** Admin → Import → *Users*: validate first (dry run), then apply. Each new account gets a one-time temporary
   password shown once (download it, distribute securely); users must change it at first sign-in. Students need a roll number; staff,
   department heads and counsellors need a department; assign mentors with `mentor_email`.
6. **Tune.** Admin → SLA (targets per priority or per service), Services (sensitive / approval / emergency flags, keywords), Permissions.
7. **Put TLS in front.** Point your proxy at the `web` container (`WEB_PORT`, default 8080). Forward `X-Forwarded-Proto`. Uncomment the HSTS header in
   `frontend/nginx.conf` if nginx itself terminates TLS. Do **not** publish the `api` or `db` ports (the compose file does not).
8. **Schedule backups** — [BACKUP_RESTORE.md](BACKUP_RESTORE.md) — and run a restore drill before go-live.

Trying it over plain HTTP on your laptop: browsers accept `Secure` cookies on `http://localhost`, so `http://localhost:8080` works.

### Upgrading
```bash
git pull && ./scripts/backup.sh && docker compose up -d --build      # migrate runs automatically before api/worker restart
```
If a migration fails the `migrate` container exits non-zero and `api`/`worker` do not start; restore from the backup you just took.
New permission codes added in a release are applied with `python -m app.cli sync-permissions` (adds codes and default grants, never removes your edits).

## Demo Mode (development and testing only)

```bash
docker compose -f docker-compose.demo.yml up -d --build        # http://localhost:8081
```
A separate Compose project (`pulse-demo`) with its own Postgres container, volume, secrets and port. It seeds a fake campus (37 users, ~40 requests, an
incident/problem/change history, a support-case suggestion, a request cluster) and shows demo account chips on the login page. Guards, all enforced in code
and covered by tests:
- the demo routes (`/api/v1/demo/*`) are **not mounted** unless `DEMO_MODE=true` **and** `APP_ENV` is not `production`;
- the application **refuses to start** with `APP_ENV=production` and `DEMO_MODE=true`;
- `seed-dev` refuses to run in production **or** against any database whose name does not contain `dev`, `demo` or `test`;
- the production compose file hard-wires `APP_ENV=production`, `DEMO_MODE=false` and has no seed service.
Reset demo data from Admin → System or `docker compose -f docker-compose.demo.yml down -v`.

## Local development (without Docker)

```bash
# PostgreSQL 15+ running locally; create a database whose name contains "dev"
createdb pulse_dev
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
cat > .env <<'ENV'
DATABASE_URL=postgresql+psycopg://USER:PASS@localhost:5432/pulse_dev
APP_ENV=development
DEMO_MODE=true
SERVE_FRONTEND=true
ENV
alembic upgrade head
python -m app.cli seed-dev            # demo data (password PulseDemo#2026)
uvicorn app.main:app --reload         # UI + API on http://localhost:8000, docs at /api/docs
python -m app.cli worker              # in a second terminal: SLA sweeps (or use Admin → System → run sweep)
```
Run the tests: [TESTING.md](TESTING.md).

## Configuration reference
All settings are environment variables (see `.env.example` and `backend/app/config.py`). Notable ones: `AGGREGATE_MIN_CELL` (small-cell masking),
`CASE_*` (support-case rule thresholds), `BREAK_GLASS_HOURS`, `AUTO_CLOSE_RESOLVED_DAYS`, `CAMPUS_TIMEZONE`, `CRISIS_RESOURCES`, `DOCS_ENABLED`.
Set the crisis helplines to your region's real numbers; the defaults are India's emergency number and Tele-MANAS.

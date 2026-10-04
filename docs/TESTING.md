# Testing

## Backend (pytest, real PostgreSQL)
```bash
createdb pulse_test        # any database whose name contains "test"; the suite DROPS and recreates its schema
cd backend && pip install -r requirements-dev.txt
DATABASE_URL=postgresql+psycopg://USER:PASS@localhost:5432/pulse_test python -m pytest -q
```
The suite builds the schema with **`alembic upgrade head`** (so every run exercises the migrations), truncates tables between tests, and restores baseline role grants. 140 tests, ~80 s.

| File | What it proves |
|---|---|
| `test_auth.py` | login/cookies (httpOnly, no token in body), identical failure for unknown email vs wrong password, lockout + unlock + expiry, expired/tampered/`alg=none`/wrong-audience tokens, deactivation = immediate lock-out, **refresh rotation, reuse detection, expiry, logout, re-login after reload**, CSRF required for cookie writes (Bearer exempt), password policy & change, temp-password forced change, argon2id, no secrets in audit rows |
| `test_rbac.py` | **permission matrix**: 30 gated endpoints × 7 roles (allowed roles pass the gate, every other role gets 403); denials are audited; **IDOR** (student ↔ student: list, detail, comment, status, feedback, consent), department isolation, Command sees metadata only, Admin/mentor cannot read requests, sensitive-service consent in both directions, internal notes hidden from students, anonymous peer concern (no actor/IP), PII redaction, encryption at rest, notification privacy |
| `test_workflow.py` | rule triage with explanation (no invented scores), triage-desk fallback + re-route keeping SLA start, urgent text (critical, crisis resources, never blocked on consent), SOS, load-balanced auto-assignment, SLA policy resolution/override/validation, SLA states over time, **sweep: warn/escalate/severe, idempotent, audited**, listing filters, **full lifecycle request → feedback with persistence and audit**, unhelpful feedback reopens, invalid transitions, student reopen, assignment rules, manual escalation, **approval gate (SLA starts after approval)**, rejection with reason, cancel cancels approval, error envelope |
| `test_itsm.py` | cluster suggestion scoped by visibility; **incident → problem → change → approval → completion → problem resolved → knowledge draft → incident closes linked requests**; plans required for risk; separation of duties; failed change; cross-department guards; problem suggestion; reports from real rows and department scoping; heatmap small-cell masking |
| `test_cases.py` | rule thresholds, suggestion invisible until consent, evidence without text, consent persistence across re-login, confirm/dismiss, **consent gates on adding participants, revocation removes access immediately**, thread, interventions with required outcome, student feedback loop, decline blocks outreach, **student access log**, **break-glass** (approve/deny/expire/revoke, flagged use), measured rule quality, manual case |
| `test_admin.py` | CRUD for departments/services/rules/SLA/assets with duplicate/validation errors; live effect of config changes; user management & guards (self, last admin, role change signs out); **permission edits enforced live + guard rails**; audit filters/export/immutability (API **and** DB); **CSV import** dry-run vs apply, all-or-nothing, row errors, formula injection, size/row limits, user import with one-time passwords, asset import affecting triage |
| `test_security.py` | headers, rate limits (login, global, SOS) → 429 + `Retry-After`, no leakage on 500, validation bounds, SQL-injection strings inert, markup returned verbatim (UI escapes), no CORS by default, docs, **production config validator** (secrets, keys, cookie flag, demo mode, DB name), **demo routes absent when Demo Mode off**, production refuses to start in demo mode, **key rotation**, CSV formula neutralising |
| `test_migrations.py` | scratch DB: **up → down → up**, **no model/schema drift**, baseline seed equals the RBAC catalogue and contains no demo data, database-level constraints (unique email, single triage desk, CHECKs, one parent per task, one active case, FK, anonymous only for peer, SLA bounds, **audit trigger blocks UPDATE/DELETE/TRUNCATE**) |
| `test_ops.py` | bootstrap-admin, idempotent starter catalogue (never overwrites edits), starter keywords route correctly, **seed guards**, the dev seed builds a consistent dataset and works through the API, worker sweep + advisory lock, permission sync, **re-encryption on key rotation** |

## UI smoke tests (jsdom driving the real frontend against a live API)
```bash
# terminal 1: demo-mode server that also serves the UI
cd backend && export DATABASE_URL=postgresql+psycopg://USER:PASS@localhost:5432/pulse_demo APP_ENV=development DEMO_MODE=true SERVE_FRONTEND=true RATE_LIMIT_ENABLED=false
alembic upgrade head && python -m app.cli seed-dev --reset && uvicorn app.main:app --port 8000
# terminal 2
cd frontend/tests/ui-smoke && npm install && node run.mjs           # 106 checks
```
It logs in as each of the 7 roles, opens **every navigation route** (fails on any error box or uncaught JS error), and drives flows: live triage preview, submit with a hostile `<script>/<img onerror>` payload
(must render as text), staff acknowledge/resolve (with a regression check that handlers do not stack), counsellor confirm + care thread + student sees the reply, admin creates a department/user (temp password shown once),
student feedback "did not help" reopening, Command metadata-only views, silent refresh after the access cookie is removed, knowledge editor, and static assertions (no browser storage APIs, no inline handlers/eval).
jsdom cannot run ES modules, so the harness bundles the source with esbuild — test-only.

## Manual / environment checks performed during development
nginx config validated and run in front of the live API (proxying, SPA fallback, CSP/headers, login limit → 429, spoofed `X-Forwarded-For` ignored) · the container's exact gunicorn command and `HEALTHCHECK`
run (2 workers, proxy IP honoured) · compose files parsed with assertions (prod hard-wires `APP_ENV=production`, `DEMO_MODE=false`; db/api publish no ports; demo is a separate project/volume) ·
backup → tampered-file refusal → restore into a fresh DB → `verify-db` · generated `.env` accepted by the production validator.

## Not tested (be aware)
* Docker images / `docker compose up` themselves (no Docker in the build environment).
* Real-browser rendering, responsiveness, accessibility audit with assistive technology, cross-browser behaviour.
* Load and concurrency testing; penetration testing; multi-instance rate limiting.
* Email/SSO/MFA (not implemented).

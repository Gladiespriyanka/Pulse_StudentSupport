# Security & privacy

## Controls
Automated tests cover everything below except rows marked **†** (not exercised: needs Docker) or **‡** (edge/nginx behaviour, verified by hand against a running nginx during development — re-check after changing `frontend/nginx.conf`).
| Area | Implementation |
|---|---|
| Passwords | argon2id; minimum length, common-password and name/email checks; never logged; temporary passwords force a change before any other API call |
| Sessions | short-lived JWT access token (15 min) and rotating opaque refresh token (7 days, only its SHA-256 is stored) in **httpOnly** cookies; reuse of a rotated refresh token revokes the whole family; password change / deactivation / role change bump `token_version` and invalidate every outstanding token immediately |
| CSRF | double-submit token (`pulse_csrf` cookie + `X-CSRF-Token` header) required for every cookie-authenticated write; `SameSite=Lax`; Bearer clients are exempt |
| Brute force | per-account lockout (5 failures → 15 min, admin can unlock), per-IP+email and per-IP limits, identical response and timing for unknown email vs wrong password |
| Authorization | every endpoint declares a permission (`security/deps.py`); row-level scope in `services/access.py`; denials are audited; **no authorization decision is made in the browser** (the UI hides what you cannot use, the API refuses it) |
| Least privilege | Command: aggregates + metadata, **no request text**. Admin: configuration, **no request or case content**. Mentors/counsellors: only cases the student consented to. Students: only their own data |
| Consent | student-controlled grants (mentor / each department) gate case participation; revocation takes effect on the next request; sensitive services require an explicit share/keep-private choice (except urgent messages, which are never blocked on a form); break-glass access is student-approved, time-limited (default 48 h), revocable, and every use is shown to the student |
| Sensitive data | request text, case messages, internal notes, feedback comments and task notes are **encrypted at the application layer** (Fernet, key rotation supported — `app.cli rotate-keys`); phone numbers, emails and ID-like numbers are masked before storage; notification bodies never contain request text; aggregates hide small cells in sensitive categories (`AGGREGATE_MIN_CELL`) |
| Anonymous channel | peer concerns store **no requester, no actor, no IP and no request id** in the application database (the web server's access log still records addresses; shorten its retention if anonymity matters to you) |
| Audit | append-only table enforced by a database trigger (UPDATE/DELETE/TRUNCATE rejected); records who/what/when/outcome/IP/request id; students can see who accessed their information; passwords and request text are never recorded |
| Input validation | Pydantic schemas reject unknown fields and bound every length; parameterised SQL only; CSV import rejects formula-injection prefixes, bad headers, oversize files, and applies all-or-nothing |
| Output safety | UI escapes every interpolated value by default (the prototype did not); strict CSP at the edge ‡ (`script-src 'self'`, no inline script); CSV exports neutralise formulas; `Cache-Control: no-store` on API responses |
| Rate limiting | nginx zones ‡ (authoritative across workers: login 10/min, API 20 r/s, writes 120/min) **and** an in-app limiter (login, triage preview, request create, SOS, peer concern, comments, imports) |
| Errors | uniform envelope with a request id; unhandled errors return a generic message — no stack traces or SQL |
| Configuration | production refuses to start with default/short/placeholder secrets, invalid keys, `COOKIE_SECURE=false`, demo mode, or a dev/demo/test database name |
| Demo separation | separate compose project, database and volume; demo routes not mounted outside Demo Mode; seed refuses production and non-dev database names |
| Browser storage | none: no `localStorage`, `sessionStorage` or IndexedDB (asserted by a test over the source) |
| Containers | non-root user, read-only filesystem, all capabilities dropped, no-new-privileges †, database and API ports not published † |

## Privacy design
* **The system suggests; people decide.** A support-case suggestion is created only by a transparent count rule; nobody is told until a consented person confirms it, and the student is told first.
* **Evidence is counts, not content.** A case shows "3 requests across 3 areas in 21 days" and request numbers — never message text. Participants see request text only if they would see it anyway (their department) or have break-glass.
* **Consent is default-off.** A new student is taken to a consent screen before anything else; choosing nobody is valid.
* **No individual ranking, no risk scores, no predictions.** Aggregates are for planning, with small-cell masking.
* **Measured, not claimed.** Rule quality is the confirm/dismiss rate from real reviewers, shown with its sample size.

## Threats considered
| Threat | Mitigation |
|---|---|
| Student A reads student B's request (IDOR) | row-level scope on every endpoint; out-of-scope returns 404; tested for requests, tasks, cases, approvals, notifications, access requests |
| Staff browsing outside their department | department scope; sensitive-service consent; every detail read of someone else's record is audited and visible to the student |
| Insider with database access reading welfare data | application-layer encryption; keys are not in the database or its backups |
| Stolen refresh token | rotation + reuse detection revokes the family |
| XSS stealing a session | tokens are httpOnly; CSP; escaping by default; (CSRF token is readable by script but useless without the cookie) |
| CSRF | header token + SameSite |
| Admin privilege escalation | admin cannot read content; the admin role cannot lose user/permission management; only the admin role may hold `admin.permissions`; students cannot be granted staff permissions; you cannot deactivate yourself or the last admin |
| Tampering with the audit log | database trigger; export is read-only |
| Spoofed client IP | nginx overwrites `X-Forwarded-For` ‡; the api port is not published † |

## Not covered / known limitations
Be explicit about these before go-live:
1. **No SSO, MFA or self-service password reset.** Accounts use passwords; admins issue one-time temporary passwords. For a college, integrate your identity provider (OIDC/SAML) — the token layer is isolated in `security/` for this.
2. **No email/SMS delivery.** Notifications are in-app only (the SLA worker creates them). Add a delivery adapter if staff must be paged.
3. **No data-retention / erasure job.** Users are deactivated, never deleted (audit integrity), and requests are kept indefinitely. Define a retention policy (e.g. under the DPDP Act 2023 or your local law) and implement archival/erasure with your data-protection lead.
4. **The in-app rate limiter is per process.** nginx is the shared limit; with several nginx instances use a gateway or Redis-backed limiter.
5. **The audit log records IP addresses**, which are personal data; set a retention schedule for it.
6. **Break-glass is trust-based after approval:** the student approves; staff then see that student's requests for the period. Every read is logged and shown to the student, but it is not prevented.
7. **† Container hardening and image contents were not exercised** (no Docker in the build environment). Review the Dockerfiles and run a vulnerability scan (e.g. `trivy`) in CI.
8. **No file uploads.** Requests are text only, so there is no malware-scanning surface; if you add attachments you must add scanning and size/type controls.
9. **jsdom, not a real browser, drove the UI tests.** Run a manual pass in the browsers your students use.
10. Penetration testing has not been performed. Commission one before handling real welfare data.

## Hardening checklist for go-live
- [ ] HTTPS everywhere; HSTS enabled; only the proxy port exposed
- [ ] `.env` and `FIELD_ENCRYPTION_KEYS` stored in a secrets manager, backed up separately from DB backups
- [ ] Backups scheduled, copied off-host, **restore drill passed** (`docs/BACKUP_RESTORE.md`)
- [ ] `DOCS_ENABLED=false` if you do not want the public API explorer
- [ ] Crisis helplines set to your region's real numbers; reviewed by counselling staff
- [ ] Departments, services, sensitive flags and SLAs reviewed by the owning teams
- [ ] Data-protection review of consent wording, retention and the audit log's IP retention
- [ ] Log shipping + alerting on `authz.denied`, `auth.refresh_reuse_detected`, `auth.locked` audit actions

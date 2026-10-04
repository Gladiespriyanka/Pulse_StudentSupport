# API guide

Base path `/api/v1`. Interactive docs at `/api/docs` (Swagger) and `/api/redoc`; the machine-readable schema at `/api/openapi.json`. A generated list of every operation is in [API_REFERENCE.md](API_REFERENCE.md).

## Authentication
Browsers use httpOnly cookies set by `POST /auth/login`; JavaScript never sees a token. Writes need the CSRF header.
```bash
# sign in (cookies saved to jar.txt)
curl -c jar.txt -X POST https://pulse.example.edu/api/v1/auth/login -H 'Content-Type: application/json' \
     -d '{"email":"asha@college.edu","password":"..."}'
# call with cookies; send the CSRF cookie value as a header for POST/PUT/PATCH/DELETE
CSRF=$(awk '$6=="pulse_csrf"{print $7}' jar.txt)
curl -b jar.txt -H "X-CSRF-Token: $CSRF" -X POST https://pulse.example.edu/api/v1/requests -H 'Content-Type: application/json' \
     -d '{"description":"The lift in Hostel B is broken"}'
```
* `POST /auth/refresh` rotates the refresh token (cookie, path `/api/v1/auth`) and issues a new access token; the UI does this transparently on a 401.
* `POST /auth/logout` revokes the refresh token family. `POST /auth/change-password` signs out other sessions.
* Server-to-server clients may send `Authorization: Bearer <access token>` (no CSRF header needed); tokens last 15 minutes.
* A user with a temporary password can only call `/auth/me`, `/auth/change-password`, `/auth/logout` until they change it (`403 password_change_required`).

## Errors
Every error has the same shape; `request_id` matches the `X-Request-ID` header and the server log.
```json
{ "error": { "code": "validation_error", "message": "Some fields are invalid",
             "details": [ { "field": "description", "message": "String should have at least 3 characters" } ], "request_id": "9f2c…" } }
```
| Status | Meaning | Typical `code` |
|---|---|---|
| 401 | not signed in / token expired or revoked | `not_authenticated`, `token_invalid`, `token_revoked`, `invalid_credentials`, `account_locked` |
| 403 | signed in but not allowed | `forbidden`, `csrf_failed`, `password_change_required`, `no_consent`, `self_approval` |
| 404 | not found **or not visible to you** | `not_found` |
| 409 | state conflict / duplicate | `invalid_transition`, `invalid_state`, `conflict`, `open_tasks`, `already_linked` |
| 422 | validation / business rule | `validation_error`, `needs_service`, `consent_required`, `weak_password`, `resolution_note_required` |
| 429 | rate limited (`Retry-After` header) | `rate_limited` |
| 500 | unexpected; nothing internal is leaked | `internal_error` |

## Conventions
* Lists: `?limit=` (max 100) and `?offset=`; response `{ items, total, limit, offset }`. Filters are query parameters (see each endpoint in the docs).
* IDs are UUIDs; humans see references such as `REQ-1042`, `INC-101`, `PRB-101`, `CHG-101`, `CASE-101`, `TSK-1001`.
* Times are ISO-8601 UTC.
* Request text is returned only when you are entitled to it; otherwise `restricted: true` and the text/requester/timeline fields are absent.
* Admin CSV import: `POST /admin/imports/{kind}` (multipart `file`, `dry_run=true|false`). Always validate first; applying is all-or-nothing.

## Demo-only endpoints
`/demo/accounts` and `/demo/reset` exist **only** when Demo Mode is on (never in production).

# Backup & restore

## What to protect
| Asset | How | Notes |
|---|---|---|
| Database | `./scripts/backup.sh` (custom-format `pg_dump`, verified with `pg_restore --list`, SHA-256 checksum, optional GPG) | contains encrypted request text, audit log, everything |
| **`FIELD_ENCRYPTION_KEYS`** | secrets manager / sealed envelope — **separate from the database backups** | **without it, encrypted text in any backup is unrecoverable.** Keep every key you have ever used until `rotate-keys` has run |
| `.env` (JWT secret, DB password) | secrets manager | losing the JWT secret only signs everyone out |

## Back up
```bash
./scripts/backup.sh                      # → backups/pulse-<UTC>.dump (+ .sha256), keeps 14 days
KEEP_DAYS=30 BACKUP_GPG_RECIPIENT=ops@college.edu ./scripts/backup.sh
```
Schedule it ([`scripts/backup-cron.example`](../scripts/backup-cron.example)) and **copy the files off the host**. A backup on the same disk is not a backup.

## Restore (production)
```bash
./scripts/restore.sh backups/pulse-<UTC>.dump          # asks you to type RESTORE
```
It verifies the checksum (refuses a tampered or truncated file), stops `web/api/worker`, **replaces** the database, runs any newer migrations, restarts, and runs `verify-db`.
Encrypted dumps: `gpg --decrypt file.dump.gpg > file.dump` first.

## Restore drill (do this before go-live and every quarter)
Restore into a scratch database and prove it is readable — without touching production:
```bash
PULSE_PG_MODE=local PG_URL=postgresql://user:pass@host/pulse TARGET_URL=postgresql://user:pass@host/pulse_drill \
  ./scripts/restore.sh backups/pulse-<UTC>.dump --yes
DATABASE_URL=postgresql+psycopg://user:pass@host/pulse_drill FIELD_ENCRYPTION_KEYS=<your keys> APP_ENV=development python -m app.cli verify-db
```
`verify-db` prints the migration revision and row counts, confirms the audit append-only trigger survived, and **decrypts a sample of stored text with the keys you supplied** (fails loudly if the keys are wrong).
This exact sequence was run during development: dump verified → tampered copy refused → restore into a fresh database → `verify-db: OK`.

## Disaster-recovery notes
* RPO = time since last backup; RTO = a few minutes plus restore time (`pg_restore` of a campus-sized database is seconds to minutes).
* PostgreSQL point-in-time recovery (WAL archiving) is the right upgrade if you need an RPO of minutes; this repository ships logical backups only.
* After restoring an older backup, users' sessions remain valid (they are signed tokens); if you must force sign-out, rotate `JWT_SECRET`.

#!/usr/bin/env bash
# Restore a backup made by backup.sh.
#   docker mode (production): ./scripts/restore.sh backups/pulse-XXXX.dump [--yes]
#       stops api/worker/web, REPLACES the database, re-applies migrations, restarts, verifies.
#   local mode (restore drill): PULSE_PG_MODE=local TARGET_URL=postgresql://... ./scripts/restore.sh file.dump --yes
#       restores into TARGET_URL (created if missing) and does not touch any running stack.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"; . scripts/_lib.sh
FILE="${1:-}"; [ -f "$FILE" ] || die "usage: restore.sh <backup.dump> [--yes]"
YES="${2:-}"
case "$FILE" in *.gpg) die "decrypt first:  gpg --decrypt $FILE > ${FILE%.gpg}";; esac
if [ -f "$FILE.sha256" ]; then ( cd "$(dirname "$FILE")" && sha256sum -c "$(basename "$FILE").sha256" ) || die "checksum mismatch - refusing to restore"; fi
pg_restore --list "$FILE" >/dev/null || die "not a valid backup archive"
if [ "$YES" != "--yes" ]; then
  echo "This will REPLACE the current PULSE database with $FILE."
  read -r -p "Type RESTORE to continue: " ans; [ "$ans" = RESTORE ] || die "aborted"
fi
if [ "$PULSE_PG_MODE" = docker ]; then
  echo "Stopping application containers ..."; $DC stop web api worker
  $DC exec -T db sh -c 'dropdb -U "$POSTGRES_USER" --if-exists --force "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
  pg_restore_stdin < "$FILE"
  echo "Applying any newer migrations ..."; $DC run --rm migrate
  $DC up -d
  echo "Verifying ..."; $DC run --rm api python -m app.cli verify-db
else
  base="${TARGET_URL%/*}"; name="${TARGET_URL##*/}"
  psql "$base/postgres" -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS \"$name\" WITH (FORCE)" -c "CREATE DATABASE \"$name\""
  pg_restore_stdin < "$FILE"
fi
echo "Restore complete."

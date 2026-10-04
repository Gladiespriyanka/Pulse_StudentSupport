#!/usr/bin/env bash
# Logical backup of the PULSE database (custom format, compressed), verified and checksummed.
#   ./scripts/backup.sh                 -> backups/pulse-<UTC timestamp>.dump (+ .sha256)
# Env: BACKUP_DIR (default ./backups), KEEP_DAYS (default 14, 0 = keep all), BACKUP_GPG_RECIPIENT (encrypt the dump)
# NOTE: this does NOT contain your secrets. Back up .env / FIELD_ENCRYPTION_KEYS separately (see docs/BACKUP_RESTORE.md).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"; . scripts/_lib.sh
OUT="${BACKUP_DIR:-$ROOT/backups}"; KEEP_DAYS="${KEEP_DAYS:-14}"
umask 077; mkdir -p "$OUT"
FILE="$OUT/pulse-$(date -u +%Y%m%dT%H%M%SZ).dump"
echo "Dumping database to $FILE ..."
pg_dump_stdout > "$FILE.partial" || { rm -f "$FILE.partial"; die "pg_dump failed"; }
mv "$FILE.partial" "$FILE"
# prove the archive is readable before we trust it
pg_restore --list "$FILE" > /dev/null || die "backup is not a valid archive"
( cd "$OUT" && sha256sum "$(basename "$FILE")" > "$(basename "$FILE").sha256" )
if [ -n "${BACKUP_GPG_RECIPIENT:-}" ]; then
  gpg --batch --yes --encrypt --recipient "$BACKUP_GPG_RECIPIENT" --output "$FILE.gpg" "$FILE" && shred -u "$FILE" 2>/dev/null || rm -f "$FILE"
  FILE="$FILE.gpg"; echo "Encrypted for $BACKUP_GPG_RECIPIENT"
fi
if [ "$KEEP_DAYS" != 0 ]; then find "$OUT" -name 'pulse-*.dump*' -mtime +"$KEEP_DAYS" -delete; fi
echo "OK  $(du -h "$FILE" | cut -f1)  $FILE"

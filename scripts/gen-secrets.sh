#!/usr/bin/env bash
# Create .env from .env.example with freshly generated secrets (needs only openssl). Refuses to overwrite an existing .env.
#   ./scripts/gen-secrets.sh            -> writes .env (mode 600)
#   ./scripts/gen-secrets.sh --print    -> just print the three secrets
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
JWT="$(openssl rand -base64 48 | tr -d '\n/+=')"
FERNET="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n')"      # a valid Fernet key is url-safe base64 of 32 random bytes
PGPW="$(openssl rand -hex 24)"
if [ "${1:-}" = "--print" ]; then printf 'JWT_SECRET=%s\nFIELD_ENCRYPTION_KEYS=%s\nPOSTGRES_PASSWORD=%s\n' "$JWT" "$FERNET" "$PGPW"; exit 0; fi
[ -e .env ] && { echo "Refusing to overwrite existing .env" >&2; exit 1; }
umask 077
sed -e "s|^JWT_SECRET=CHANGE_ME.*|JWT_SECRET=$JWT|" -e "s|^FIELD_ENCRYPTION_KEYS=CHANGE_ME.*|FIELD_ENCRYPTION_KEYS=$FERNET|" \
    -e "s|^POSTGRES_PASSWORD=CHANGE_ME.*|POSTGRES_PASSWORD=$PGPW|" .env.example > .env
echo ".env created. BACK UP FIELD_ENCRYPTION_KEYS (and .env) somewhere safe: encrypted request text is unreadable without it."
echo "Next: edit BOOTSTRAP_ADMIN_EMAIL, then  docker compose up -d --build"

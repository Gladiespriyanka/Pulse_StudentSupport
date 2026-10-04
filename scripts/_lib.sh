# Shared helpers. PULSE_PG_MODE=docker (default) talks to the compose `db` service;
# PULSE_PG_MODE=local uses pg_dump/pg_restore directly (PG_URL for the source, TARGET_URL for restores).
PULSE_PG_MODE="${PULSE_PG_MODE:-docker}"
DC="${DOCKER_COMPOSE:-docker compose}"

pg_dump_stdout() {
  if [ "$PULSE_PG_MODE" = docker ]; then
    $DC exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --no-owner --no-privileges'
  else
    pg_dump "${PG_URL:?set PG_URL}" -Fc --no-owner --no-privileges
  fi
}
pg_restore_stdin() {   # reads a custom-format dump on stdin
  if [ "$PULSE_PG_MODE" = docker ]; then
    $DC exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges --exit-on-error'
  else
    pg_restore -d "${TARGET_URL:?set TARGET_URL}" --no-owner --no-privileges --exit-on-error
  fi
}
die() { echo "ERROR: $*" >&2; exit 1; }

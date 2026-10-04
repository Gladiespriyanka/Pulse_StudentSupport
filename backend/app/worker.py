"""Background worker: runs the SLA / escalation / auto-close / case-rule sweep on a timer.

Safe to run more than once: a PostgreSQL advisory lock ensures only one instance sweeps at a time.
"""
from __future__ import annotations

import logging
import signal
import time

from sqlalchemy import text

from app.config import get_settings
from app.db import SessionLocal
from app.services import sla

log = logging.getLogger("pulse.worker")
_stop = False
LOCK_ID = 727_001


def sweep_once() -> dict | None:
    db = SessionLocal()
    try:
        if not db.scalar(text("select pg_try_advisory_xact_lock(:k)"), {"k": LOCK_ID}):
            return None  # another worker is sweeping
        stats = sla.sweep(db)
        db.commit()
        return stats
    except Exception:
        db.rollback()
        log.exception("sweep failed")
        return None
    finally:
        db.close()


def main() -> None:
    s = get_settings()
    logging.basicConfig(level=s.log_level, format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')

    def _term(*_):
        global _stop
        _stop = True

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    log.info("worker started, interval=%ss", s.sla_sweep_interval_seconds)
    while not _stop:
        stats = sweep_once()
        if stats and any(stats.values()):
            log.info("sweep %s", stats)
        for _ in range(s.sla_sweep_interval_seconds):
            if _stop:
                break
            time.sleep(1)
    log.info("worker stopped")


if __name__ == "__main__":
    main()

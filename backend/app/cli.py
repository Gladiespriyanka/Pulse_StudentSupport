"""Operations CLI:  python -m app.cli <command>"""
from __future__ import annotations

import argparse
import secrets
import sys

from cryptography.fernet import Fernet
from sqlalchemy import select

from app.config import get_settings


def cmd_gen_keys(_):
    print("# Paste into your .env (keep secret, back them up - encrypted data is unreadable without FIELD_ENCRYPTION_KEYS)")
    print(f"JWT_SECRET={secrets.token_urlsafe(48)}")
    print(f"FIELD_ENCRYPTION_KEYS={Fernet.generate_key().decode()}")
    print(f"POSTGRES_PASSWORD={secrets.token_urlsafe(24)}")


def cmd_bootstrap_admin(args):
    from app.db import SessionLocal
    from app.models import Role, User
    from app.services import users as U

    s = get_settings()
    email = args.email or s.bootstrap_admin_email
    if not email:
        sys.exit("Provide --email or BOOTSTRAP_ADMIN_EMAIL")
    db = SessionLocal()
    try:
        if db.scalar(select(User.id).join(Role).where(Role.code == "admin").limit(1)):
            print("An administrator already exists - nothing to do.")
            return
        pw = args.password or s.bootstrap_admin_password
        user, temp = U.create_user(db, None, email=email, full_name=s.bootstrap_admin_name, role_code="admin", password=pw)
        db.commit()
        print(f"Created administrator {user.email}")
        if temp:
            print(f"One-time password (you must change it at first sign-in): {temp}")
    finally:
        db.close()


def cmd_load_starter(_):
    from app.db import SessionLocal
    from app.starter import load_starter_catalog

    db = SessionLocal()
    try:
        print("Loaded:", load_starter_catalog(db))
        db.commit()
    finally:
        db.close()


def cmd_seed_dev(args):
    from app import devseed

    devseed.assert_safe_target()
    n = devseed.seed(reset=args.reset)
    print("Seeded development data:", n)
    print(f"All demo accounts use the password in DEMO_PASSWORD (default: {get_settings().demo_password})")


def cmd_sync_permissions(_):
    from app.db import SessionLocal
    from app.models import Permission, Role
    from app.rbac import PERMISSIONS, ROLES

    db = SessionLocal()
    try:
        have = {p.code: p for p in db.scalars(select(Permission))}
        added = []
        for code, desc in PERMISSIONS.items():
            if code not in have:
                p = Permission(code=code, description=desc)
                db.add(p)
                have[code] = p
                added.append(code)
        db.flush()
        for rcode, spec in ROLES.items():
            role = db.scalar(select(Role).where(Role.code == rcode))
            if role:
                for code in added:
                    if code in spec["permissions"] and have[code] not in role.permissions:
                        role.permissions.append(have[code])
        db.commit()
        print("New permissions:", added or "none")
    finally:
        db.close()


def cmd_sweep(_):
    from app.worker import sweep_once

    print(sweep_once())


def cmd_worker(_):
    from app.worker import main

    main()


def cmd_rotate_keys(args):
    """Re-encrypt every encrypted column under the FIRST key in FIELD_ENCRYPTION_KEYS.

    Procedure: put the NEW key first and keep the old key(s) after it, run this, then retire the old keys.
    """
    from sqlalchemy.orm.attributes import flag_modified

    from app.db import SessionLocal
    from app.models import Base
    from app.security.crypto import EncryptedText

    db = SessionLocal()
    total = 0
    try:
        for mapper in Base.registry.mappers:
            cols = [c.key for c in mapper.columns if isinstance(c.type, EncryptedText)]
            if not cols:
                continue
            n = 0
            pk = mapper.primary_key[0]
            ids = list(db.scalars(select(pk)))
            for i in range(0, len(ids), 500):  # batches by primary key (models use joined eager loading)
                for obj in db.scalars(select(mapper.class_).where(pk.in_(ids[i:i + 500]))).unique():
                    for c in cols:
                        if getattr(obj, c) is not None:
                            flag_modified(obj, c)
                    n += 1
                db.flush()
                db.expire_all()
            db.commit()
            total += n
            print(f"  {mapper.class_.__tablename__:16} {n} rows re-encrypted ({', '.join(cols)})")
        print(f"rotate-keys: done ({total} rows). You can now remove the old key(s) from FIELD_ENCRYPTION_KEYS.")
    finally:
        db.close()


def cmd_verify_db(_):
    """Prove a (restored) database is healthy and readable with the current keys."""
    from sqlalchemy import text

    from app.db import SessionLocal
    from app.models import Request

    db = SessionLocal()
    ok = True
    try:
        rev = db.scalar(text("select version_num from alembic_version"))
        print("migration revision:", rev)
        for t in ("users", "requests", "request_events", "cases", "audit_logs", "incidents", "problems", "changes"):
            print(f"  {t:16} {db.scalar(text(f'select count(*) from {t}'))}")
        trig = db.scalar(text("select count(*) from pg_trigger where tgname='audit_logs_no_update_delete'"))
        print("audit append-only trigger:", "present" if trig else "MISSING")
        ok &= bool(trig)
        sample = db.scalars(select(Request).limit(5)).all()
        bad = [r.number for r in sample if "unreadable" in (r.description or "")]
        print("encrypted fields readable with current keys:", "yes" if not bad else f"NO (requests {bad})")
        ok &= not bad
    finally:
        db.close()
    if not ok:
        sys.exit("verify-db: FAILED")
    print("verify-db: OK")


def cmd_check(_):
    s = get_settings()
    print(f"env={s.app_env} demo_mode={s.demo_mode} db={s.database_url.rsplit('@', 1)[-1]}")
    print("configuration OK")


def main():
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("gen-keys").set_defaults(fn=cmd_gen_keys)
    p = sub.add_parser("bootstrap-admin")
    p.add_argument("--email")
    p.add_argument("--password")
    p.set_defaults(fn=cmd_bootstrap_admin)
    sub.add_parser("load-starter-catalog").set_defaults(fn=cmd_load_starter)
    p = sub.add_parser("seed-dev", help="DEV/DEMO ONLY: refuses to run in production or against a non dev/demo/test database")
    p.add_argument("--reset", action="store_true", help="wipe all data first")
    p.set_defaults(fn=cmd_seed_dev)
    sub.add_parser("sync-permissions").set_defaults(fn=cmd_sync_permissions)
    sub.add_parser("sla-sweep").set_defaults(fn=cmd_sweep)
    sub.add_parser("worker").set_defaults(fn=cmd_worker)
    sub.add_parser("rotate-keys", help="re-encrypt stored free text under the newest key").set_defaults(fn=cmd_rotate_keys)
    sub.add_parser("check-config").set_defaults(fn=cmd_check)
    sub.add_parser("verify-db", help="check migration revision, audit trigger and that encrypted data is readable").set_defaults(fn=cmd_verify_db)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

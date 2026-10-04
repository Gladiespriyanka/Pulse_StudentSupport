from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.misc import safe_cell
from app.config import get_settings
from app.db import get_db
from app.errors import Conflict, Invalid, NotFound
from app.models import AuditLog, Permission, Role, RolePermission, Staff, Student, User
from app.models.base import utcnow
from app.rbac import PROTECTED_ADMIN_PERMISSIONS
from app.schemas import RolePermsIn, UserCreateIn, UserPatchIn
from app.security import ratelimit
from app.security.deps import Principal, require
from app.services import imports as IM, sla, users as U
from app.services.audit import audit

router = APIRouter(prefix="/admin", tags=["admin"])


def _user_out(u: User) -> dict:
    return {"id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role.code, "is_active": u.is_active,
            "must_change_password": u.must_change_password, "locked": bool(u.locked_until and u.locked_until > utcnow()),
            "department_id": u.staff.department_id if u.staff else None,
            "department_name": u.staff.department.name if u.staff and u.staff.department else None,
            "roll_no": u.student.roll_no if u.student else None, "mentor_id": u.student.mentor_id if u.student else None,
            "last_login_at": u.last_login_at, "created_at": u.created_at}


# ---- users --------------------------------------------------------------------------------------------
@router.get("/users")
def list_users(p: Principal = Depends(require("admin.users")), db: Session = Depends(get_db), q: str | None = None,
               role: str | None = None, department_id: uuid.UUID | None = None, active: bool | None = None,
               limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0)):
    conds = []
    if q:
        like = f"%{q.strip().lower()}%"
        conds.append(or_(func.lower(User.email).like(like), func.lower(User.full_name).like(like)))
    if role:
        conds.append(User.role.has(Role.code == role))
    if department_id:
        conds.append(User.staff.has(Staff.department_id == department_id))
    if active is not None:
        conds.append(User.is_active == active)
    total = db.scalar(select(func.count()).select_from(User).where(*conds)) or 0
    rows = db.scalars(select(User).where(*conds).order_by(User.full_name).limit(limit).offset(offset)).unique().all()
    return {"items": [_user_out(u) for u in rows], "total": total, "limit": limit, "offset": offset}


@router.post("/users", status_code=201, summary="Create a user (omit password to get a one-time temporary password)")
def create_user(body: UserCreateIn, p: Principal = Depends(require("admin.users")), db: Session = Depends(get_db)):
    data = body.model_dump()
    user, temp = U.create_user(db, p, email=data.pop("email"), full_name=data.pop("full_name"), role_code=data.pop("role"),
                                password=data.pop("password"), **data)
    db.commit()
    return {"user": _user_out(user), "temporary_password": temp}


def _get(db: Session, uid: uuid.UUID) -> User:
    u = db.get(User, uid)
    if not u:
        raise NotFound("User not found")
    return u


@router.get("/users/{user_id}")
def get_user(user_id: uuid.UUID, p: Principal = Depends(require("admin.users")), db: Session = Depends(get_db)):
    return _user_out(_get(db, user_id))


@router.patch("/users/{user_id}")
def patch_user(user_id: uuid.UUID, body: UserPatchIn, p: Principal = Depends(require("admin.users")), db: Session = Depends(get_db)):
    u = _get(db, user_id)
    data = body.model_dump(exclude_unset=True)
    if u.id == p.id and (data.get("is_active") is False or ("role" in data and data["role"] != u.role.code)):
        raise Conflict("You cannot deactivate or change the role of your own account.", code="self_change")
    if u.role.code == "admin" and u.is_active and (data.get("is_active") is False or data.get("role", "admin") != "admin"):
        others = db.scalar(select(func.count()).select_from(User).join(Role).where(Role.code == "admin", User.is_active, User.id != u.id))
        if not others:
            raise Conflict("This is the last active administrator.", code="last_admin")
    U.update_user(db, p, u, full_name=data.get("full_name"), role_code=data.get("role"), is_active=data.get("is_active"),
                  department_id=data.get("department_id"), set_department="department_id" in data,
                  mentor_id=data.get("mentor_id"), set_mentor="mentor_id" in data, programme=data.get("programme"), year=data.get("year"))
    db.commit()
    return _user_out(u)


@router.post("/users/{user_id}/reset-password", summary="Issue a one-time temporary password (user must change it at next sign-in)")
def reset_password(user_id: uuid.UUID, p: Principal = Depends(require("admin.users")), db: Session = Depends(get_db)):
    u = _get(db, user_id)
    pw = U.reset_password(db, p, u)
    db.commit()
    return {"temporary_password": pw}


@router.post("/users/{user_id}/unlock")
def unlock(user_id: uuid.UUID, p: Principal = Depends(require("admin.users")), db: Session = Depends(get_db)):
    u = _get(db, user_id)
    u.locked_until, u.failed_login_count = None, 0
    audit(db, "user.unlocked", actor=p, entity_type="user", entity_id=u.id, subject=u.id)
    db.commit()
    return _user_out(u)


# ---- roles & permissions -------------------------------------------------------------------------------
@router.get("/roles", summary="Roles with their permissions, plus the full permission catalogue")
def roles(p: Principal = Depends(require("admin.permissions")), db: Session = Depends(get_db)):
    rs = db.scalars(select(Role).order_by(Role.code)).unique().all()
    return {"roles": [{"code": r.code, "name": r.name, "description": r.description, "is_system": r.is_system,
                       "permissions": [x.code for x in r.permissions],
                       "user_count": db.scalar(select(func.count()).select_from(User).where(User.role_id == r.id))} for r in rs],
            "permissions": [{"code": x.code, "description": x.description} for x in db.scalars(select(Permission).order_by(Permission.code))]}


@router.put("/roles/{code}/permissions", summary="Replace a role's permissions (guard rails prevent admin lock-out)")
def set_role_permissions(code: str, body: RolePermsIn, p: Principal = Depends(require("admin.permissions")), db: Session = Depends(get_db)):
    role = db.scalar(select(Role).where(Role.code == code))
    if not role:
        raise NotFound("Role not found")
    wanted = set(body.permissions)
    known = {x.code: x for x in db.scalars(select(Permission))}
    unknown = sorted(wanted - set(known))
    if unknown:
        raise Invalid("Unknown permission codes.", code="unknown_permission", details=unknown)
    if code == "admin" and not PROTECTED_ADMIN_PERMISSIONS <= wanted:
        raise Conflict(f"The admin role must keep: {', '.join(sorted(PROTECTED_ADMIN_PERMISSIONS))}.", code="protected_permission")
    if code != "admin" and "admin.permissions" in wanted:
        raise Conflict("Only the admin role may manage permissions.", code="protected_permission")
    if code == "student" and any(x.startswith("admin.") or x in ("audit.read", "request.read_all_meta", "request.read_department") for x in wanted):
        raise Conflict("Students cannot be granted staff or administrative permissions.", code="protected_permission")
    before = {x.code for x in role.permissions}
    role.permissions = [known[c] for c in sorted(wanted)]
    audit(db, "role.permissions_changed", actor=p, entity_type="role", entity_id=role.id,
          details={"role": code, "added": sorted(wanted - before), "removed": sorted(before - wanted)})
    db.commit()
    return {"code": code, "permissions": sorted(wanted)}


# ---- audit log ------------------------------------------------------------------------------------------
def _audit_query(actor_id, subject_id, action, entity_type, outcome, date_from, date_to):
    c = []
    if actor_id:
        c.append(AuditLog.actor_id == actor_id)
    if subject_id:
        c.append(AuditLog.subject_user_id == subject_id)
    if action:
        c.append(AuditLog.action.like(action.replace("%", "") + "%"))
    if entity_type:
        c.append(AuditLog.entity_type == entity_type)
    if outcome:
        c.append(AuditLog.outcome == outcome)
    if date_from:
        c.append(AuditLog.ts >= date_from)
    if date_to:
        c.append(AuditLog.ts <= date_to)
    return c


def _audit_out(a: AuditLog) -> dict:
    return {"id": a.id, "ts": a.ts, "actor": {"id": a.actor_id, "name": a.actor.full_name if a.actor else None, "role": a.actor_role},
            "action": a.action, "entity_type": a.entity_type, "entity_id": a.entity_id, "subject_user_id": a.subject_user_id,
            "outcome": a.outcome, "details": a.details, "ip": a.ip, "request_id": a.request_id}


@router.get("/audit-logs")
def audit_logs(p: Principal = Depends(require("audit.read")), db: Session = Depends(get_db), actor_id: uuid.UUID | None = None,
               subject_id: uuid.UUID | None = None, action: str | None = None, entity_type: str | None = None,
               outcome: str | None = None, date_from: datetime | None = None, date_to: datetime | None = None,
               limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    c = _audit_query(actor_id, subject_id, action, entity_type, outcome, date_from, date_to)
    total = db.scalar(select(func.count()).select_from(AuditLog).where(*c)) or 0
    rows = db.scalars(select(AuditLog).where(*c).order_by(AuditLog.id.desc()).limit(limit).offset(offset)).unique().all()
    return {"items": [_audit_out(a) for a in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/audit-logs/export.csv")
def audit_export(p: Principal = Depends(require("audit.read")), db: Session = Depends(get_db), action: str | None = None,
                 date_from: datetime | None = None, date_to: datetime | None = None, limit: int = Query(5000, le=20000)):
    c = _audit_query(None, None, action, None, None, date_from, date_to)
    rows = db.scalars(select(AuditLog).where(*c).order_by(AuditLog.id.desc()).limit(limit)).unique().all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "timestamp", "actor", "role", "action", "entity_type", "entity_id", "outcome", "ip", "request_id"])
    for a in rows:
        w.writerow([a.id, a.ts.isoformat(), safe_cell(a.actor.full_name if a.actor else ""), a.actor_role, a.action, a.entity_type,
                    a.entity_id, a.outcome, a.ip, a.request_id])
    audit(db, "audit.exported", actor=p, details={"rows": len(rows)})
    db.commit()
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="pulse-audit.csv"'})


# ---- CSV import ---------------------------------------------------------------------------------------------
@router.get("/imports/{kind}/template", summary="CSV header template for an import type")
def import_template(kind: str, p: Principal = Depends(require("admin.import"))):
    if kind not in IM.SPECS:
        raise NotFound("Unknown import type")
    req, opt = IM.SPECS[kind]
    return Response(",".join(req + opt) + "\n", media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{kind}-template.csv"'})


@router.post("/imports/{kind}", summary="Validate (dry_run=true, default) or apply a CSV import. Apply is all-or-nothing.")
async def import_csv(kind: str, file: UploadFile = File(...), dry_run: bool = Form(True), p: Principal = Depends(require("admin.import")),
                     db: Session = Depends(get_db)):
    ratelimit.hit(f"import:{p.id}", 10)
    content = await file.read(get_settings().import_max_bytes + 1)
    rep = IM.run(db, p, kind, content, dry_run=dry_run)
    applied = (not dry_run) and rep.ok
    if applied:
        audit(db, "import.applied", actor=p, details={"kind": kind, "created": rep.created, "updated": rep.updated, "rows": rep.rows})
        db.commit()
    else:
        db.rollback()
        audit(db, "import.dry_run" if dry_run else "import.rejected", actor=p,
              details={"kind": kind, "rows": rep.rows, "errors": len(rep.errors)})
        db.commit()
    return {"kind": kind, "dry_run": dry_run, "applied": applied, "rows": rep.rows, "created": rep.created, "updated": rep.updated,
            "errors": rep.errors[:200], "error_count": len(rep.errors),
            "temporary_passwords": rep.temp_passwords if applied else []}


# ---- maintenance ----------------------------------------------------------------------------------------------
@router.post("/system/sla-sweep", summary="Run the SLA/escalation sweep now (the worker also runs it on a timer)")
def run_sweep(p: Principal = Depends(require("admin.system")), db: Session = Depends(get_db)):
    stats = sla.sweep(db)
    audit(db, "system.sla_sweep", actor=p, details=stats)
    db.commit()
    return stats

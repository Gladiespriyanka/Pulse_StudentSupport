"""CSV imports. Always validate first (dry run); applying is all-or-nothing inside one transaction."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import Invalid
from app.models import Asset, Department, Location, Service, Student, User
from app.models.enums import AssetStatus, LocationKind, Priority, ServiceCategory
from app.services import users as U

SPECS = {
    "departments": (["code", "name"], ["description", "is_triage_desk"]),
    "locations": (["code", "name"], ["kind", "aliases"]),
    "services": (["code", "name", "department_code"], ["description", "category", "default_priority", "requires_approval",
                                                      "is_sensitive", "is_emergency", "student_visible"]),
    "assets": (["code", "name"], ["asset_type", "location_code", "department_code", "status", "notes"]),
    "users": (["email", "full_name", "role"], ["department_code", "roll_no", "programme", "year", "mentor_email",
                                              "residence_location_code", "employee_no", "title"]),
}
TRUE = {"1", "true", "yes", "y", "t"}
FALSE = {"0", "false", "no", "n", "f", ""}


@dataclass
class Report:
    kind: str
    dry_run: bool
    rows: int = 0
    created: int = 0
    updated: int = 0
    errors: list[dict] = field(default_factory=list)
    temp_passwords: list[dict] = field(default_factory=list)  # returned once, never stored in plain text

    @property
    def ok(self) -> bool:
        return not self.errors


def _bool(v: str, col: str) -> bool:
    v = (v or "").strip().lower()
    if v in TRUE:
        return True
    if v in FALSE:
        return False
    raise ValueError(f"{col}: expected true/false")


def _choice(v: str, enum_cls, col: str, default: str | None = None) -> str:
    v = (v or "").strip().lower() or (default or "")
    if v not in {m.value for m in enum_cls}:
        raise ValueError(f"{col}: must be one of {', '.join(m.value for m in enum_cls)}")
    return v


def parse(content: bytes, kind: str) -> list[dict]:
    s = get_settings()
    if kind not in SPECS:
        raise Invalid(f"Unknown import type '{kind}'.", code="unknown_import")
    if len(content) > s.import_max_bytes:
        raise Invalid(f"File is larger than {s.import_max_bytes // 1000} KB.", code="file_too_large")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise Invalid("The file must be UTF-8 encoded CSV.", code="bad_encoding")
    rdr = csv.DictReader(io.StringIO(text))
    required, optional = SPECS[kind]
    header = [h.strip().lower() for h in (rdr.fieldnames or [])]
    missing = [c for c in required if c not in header]
    unknown = [h for h in header if h not in required + optional]
    if missing or unknown:
        raise Invalid("The header row does not match the template.", code="bad_header",
                      details={"missing": missing, "unknown": unknown, "expected": required + optional})
    rows = []
    for raw in rdr:
        rows.append({(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()})
        if len(rows) > s.import_max_rows:
            raise Invalid(f"At most {s.import_max_rows} rows per import.", code="too_many_rows")
    return rows


def run(db: Session, actor, kind: str, content: bytes, dry_run: bool = True) -> Report:
    rows = parse(content, kind)
    rep = Report(kind=kind, dry_run=dry_run, rows=len(rows))
    required = SPECS[kind][0]
    handler = {"departments": _dept, "locations": _loc, "services": _svc, "assets": _asset, "users": _user}[kind]
    for i, row in enumerate(rows, start=2):  # row 1 is the header
        try:
            for col, val in row.items():
                if val[:1] in ("=", "+", "@", "\t", "\r"):
                    raise ValueError(f"{col}: values may not start with '{val[:1]}' (spreadsheet formula risk)")
            for col in required:
                if not row.get(col):
                    raise ValueError(f"{col} is required")
            with db.begin_nested():  # row-level savepoint: a bad row does not poison the session
                handler(db, actor, row, rep)
        except (ValueError, Invalid) as e:
            rep.errors.append({"row": i, "message": e.message if isinstance(e, Invalid) else str(e)})
        except Exception as e:  # constraint violations etc.
            rep.errors.append({"row": i, "message": f"Could not save this row ({type(e).__name__})"})
    return rep


def _dept(db, actor, r, rep):
    d = db.scalar(select(Department).where(Department.code == r["code"]))
    new = d is None
    d = d or Department(code=r["code"])
    d.name, d.description = r["name"], r.get("description") or None
    if r.get("is_triage_desk"):
        d.is_triage_desk = _bool(r["is_triage_desk"], "is_triage_desk")
    db.add(d)
    db.flush()
    rep.created += new
    rep.updated += not new


def _loc(db, actor, r, rep):
    x = db.scalar(select(Location).where(Location.code == r["code"]))
    new = x is None
    x = x or Location(code=r["code"])
    x.name, x.kind = r["name"], _choice(r.get("kind", ""), LocationKind, "kind", "other")
    x.aliases = [a.strip().lower() for a in (r.get("aliases") or "").split("|") if a.strip()]
    db.add(x)
    db.flush()
    rep.created += new
    rep.updated += not new


def _svc(db, actor, r, rep):
    dept = db.scalar(select(Department).where(Department.code == r["department_code"]))
    if not dept:
        raise ValueError(f"department_code: unknown department '{r['department_code']}'")
    x = db.scalar(select(Service).where(Service.code == r["code"]))
    new = x is None
    x = x or Service(code=r["code"])
    x.name, x.department_id, x.description = r["name"], dept.id, r.get("description") or None
    x.category = _choice(r.get("category", ""), ServiceCategory, "category", "other")
    x.default_priority = _choice(r.get("default_priority", ""), Priority, "default_priority", "medium")
    for col in ("requires_approval", "is_sensitive", "is_emergency"):
        setattr(x, col, _bool(r.get(col, ""), col))
    x.student_visible = _bool(r.get("student_visible", "true") or "true", "student_visible")
    db.add(x)
    db.flush()
    rep.created += new
    rep.updated += not new


def _asset(db, actor, r, rep):
    loc = dept = None
    if r.get("location_code"):
        loc = db.scalar(select(Location).where(Location.code == r["location_code"]))
        if not loc:
            raise ValueError(f"location_code: unknown location '{r['location_code']}'")
    if r.get("department_code"):
        dept = db.scalar(select(Department).where(Department.code == r["department_code"]))
        if not dept:
            raise ValueError(f"department_code: unknown department '{r['department_code']}'")
    x = db.scalar(select(Asset).where(Asset.code == r["code"]))
    new = x is None
    x = x or Asset(code=r["code"])
    x.name, x.asset_type, x.notes = r["name"], r.get("asset_type") or None, r.get("notes") or None
    x.location_id, x.department_id = loc.id if loc else None, dept.id if dept else None
    x.status = _choice(r.get("status", ""), AssetStatus, "status", "active")
    db.add(x)
    db.flush()
    rep.created += new
    rep.updated += not new


def _user(db, actor, r, rep):
    email = r["email"].lower()
    dept = db.scalar(select(Department).where(Department.code == r["department_code"])) if r.get("department_code") else None
    if r.get("department_code") and not dept:
        raise ValueError(f"department_code: unknown department '{r['department_code']}'")
    mentor = None
    if r.get("mentor_email"):
        mentor = db.scalar(select(User).where(User.email == r["mentor_email"].lower()))
        if not mentor:
            raise ValueError(f"mentor_email: unknown user '{r['mentor_email']}'")
    loc = None
    if r.get("residence_location_code"):
        loc = db.scalar(select(Location).where(Location.code == r["residence_location_code"]))
        if not loc:
            raise ValueError(f"residence_location_code: unknown location '{r['residence_location_code']}'")
    year = None
    if r.get("year"):
        try:
            year = int(r["year"])
        except ValueError:
            raise ValueError("year: must be a number")
    existing = db.scalar(select(User).where(User.email == email))
    if existing:
        U.update_user(db, actor, existing, full_name=r["full_name"], role_code=r["role"],
                      department_id=dept.id if dept else None, set_department=bool(dept and existing.staff),
                      mentor_id=mentor.id if mentor else None, set_mentor=bool(mentor and existing.student),
                      programme=r.get("programme") or None, year=year)
        rep.updated += 1
        return
    user, pw = U.create_user(db, actor, email=email, full_name=r["full_name"], role_code=r["role"],
                             department_id=dept.id if dept else None, roll_no=r.get("roll_no") or None,
                             programme=r.get("programme") or None, year=year, mentor_id=mentor.id if mentor else None,
                             residence_location_id=loc.id if loc else None, employee_no=r.get("employee_no") or None,
                             title=r.get("title") or None, academic_department_id=dept.id if dept and r["role"] == "student" else None)
    rep.created += 1
    if pw:
        rep.temp_passwords.append({"email": email, "temporary_password": pw})

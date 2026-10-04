"""User lifecycle (admin): create, update, deactivate, reset password. Users are never hard-deleted (audit integrity)."""
from __future__ import annotations

import secrets
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import Conflict, Invalid, NotFound
from app.models import Department, Role, Staff, Student, User
from app.models.base import utcnow
from app.rbac import STAFF_ROLES
from app.security.passwords import hash_password, password_problems
from app.services.audit import audit


def temp_password() -> str:
    return secrets.token_urlsafe(12)


def get_role(db: Session, code: str) -> Role:
    r = db.scalar(select(Role).where(Role.code == code))
    if not r:
        raise Invalid(f"Unknown role '{code}'.", code="unknown_role")
    return r


def create_user(db: Session, actor, *, email: str, full_name: str, role_code: str, password: str | None = None,
                department_id: uuid.UUID | None = None, roll_no: str | None = None, programme: str | None = None,
                year: int | None = None, mentor_id: uuid.UUID | None = None, residence_location_id=None,
                academic_department_id=None, employee_no: str | None = None, title: str | None = None) -> tuple[User, str | None]:
    email = email.strip().lower()
    if db.scalar(select(func.count()).select_from(User).where(func.lower(User.email) == email)):
        raise Conflict(f"A user with email {email} already exists.", code="email_taken")
    role = get_role(db, role_code)
    generated = None
    if password is None:
        password = generated = temp_password()
    else:
        problems = password_problems(password, email=email, name=full_name)
        if problems:
            raise Invalid("Password is too weak.", code="weak_password", details=problems)
    if role_code in STAFF_ROLES and not department_id:
        raise Invalid("Staff, department heads and counsellors need a department.", code="department_required")
    if department_id and not db.get(Department, department_id):
        raise Invalid("Unknown department.", code="invalid_department")
    user = User(email=email, password_hash=hash_password(password), full_name=full_name.strip(), role=role, role_id=role.id,
                must_change_password=generated is not None)
    db.add(user)
    db.flush()
    if role_code == "student":
        if not roll_no:
            raise Invalid("Students need a roll number.", code="roll_no_required")
        if db.scalar(select(func.count()).select_from(Student).where(Student.roll_no == roll_no)):
            raise Conflict(f"Roll number {roll_no} already exists.", code="roll_no_taken")
        if mentor_id:
            m = db.get(User, mentor_id)
            if not m or m.role.code != "mentor":
                raise Invalid("The mentor must be an active user with the mentor role.", code="invalid_mentor")
        db.add(Student(user_id=user.id, roll_no=roll_no, programme=programme, year=year, mentor_id=mentor_id,
                       residence_location_id=residence_location_id, academic_department_id=academic_department_id))
    else:
        db.add(Staff(user_id=user.id, department_id=department_id, employee_no=employee_no, title=title))
    db.flush()
    db.refresh(user)
    audit(db, "user.created", actor=actor, entity_type="user", entity_id=user.id, subject=user.id,
          details={"role": role_code, "email": email})
    return user, generated


def update_user(db: Session, actor, user: User, *, full_name=None, role_code=None, is_active=None, department_id=None,
                set_department=False, mentor_id=None, set_mentor=False, programme=None, year=None) -> User:
    changes: dict = {}
    if full_name is not None and full_name.strip() != user.full_name:
        user.full_name = full_name.strip()
        changes["full_name"] = True
    if role_code and role_code != user.role.code:
        new = get_role(db, role_code)
        if (user.role.code == "student") != (role_code == "student"):
            raise Invalid("Changing between student and staff roles is not supported. Create a new account.", code="role_change_unsupported")
        user.role, user.role_id = new, new.id
        user.token_version += 1
        changes["role"] = role_code
    if set_department:
        if user.staff is None:
            raise Invalid("Students do not belong to a staff department.", code="not_staff")
        if department_id and not db.get(Department, department_id):
            raise Invalid("Unknown department.", code="invalid_department")
        user.staff.department_id = department_id
        user.token_version += 1
        changes["department"] = str(department_id) if department_id else None
    if user.role.code in STAFF_ROLES and user.staff and not user.staff.department_id:
        raise Invalid("Staff, department heads and counsellors need a department.", code="department_required")
    if user.student:
        if set_mentor:
            if mentor_id and (db.get(User, mentor_id) is None or db.get(User, mentor_id).role.code != "mentor"):
                raise Invalid("The mentor must have the mentor role.", code="invalid_mentor")
            user.student.mentor_id = mentor_id
            changes["mentor"] = True
        if programme is not None:
            user.student.programme = programme
        if year is not None:
            user.student.year = year
    if is_active is not None and is_active != user.is_active:
        user.is_active = is_active
        user.token_version += 1  # invalidates every outstanding access token
        if not is_active:
            from app.services.auth import revoke_all_sessions

            revoke_all_sessions(db, user.id)
        changes["is_active"] = is_active
    audit(db, "user.updated", actor=actor, entity_type="user", entity_id=user.id, subject=user.id, details=changes)
    return user


def reset_password(db: Session, actor, user: User) -> str:
    from app.services.auth import revoke_all_sessions

    pw = temp_password()
    user.password_hash, user.must_change_password = hash_password(pw), True
    user.failed_login_count, user.locked_until = 0, None
    user.token_version += 1
    revoke_all_sessions(db, user.id)
    audit(db, "user.password_reset", actor=actor, entity_type="user", entity_id=user.id, subject=user.id)
    return pw

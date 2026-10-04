"""Row-level authorization (who may see which record).

Rules are expressed as SQL criteria so that list endpoints and single-record endpoints use the
*same* logic. Summary:

Requests (detail = includes free text, events, attachments to the person):
  * requester always sees own
  * department staff see their department's requests, except for sensitive services where the
    student withheld consent (`consent_share = false`)
  * anyone holding an unexpired, student-approved break-glass grant sees that student's requests
  * Command sees metadata only (no text) across departments; Admin sees none
Cases:
  * the student sees own cases
  * a participant sees a case only while the student's consent for them (mentor / department) holds
  * a *suggested* case is visible to consented reviewers (`case.manage`) so they can confirm/dismiss it
Tasks follow their parent.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import and_, exists, false, or_, select
from sqlalchemy.orm import Session

from app.models import (
    AccessRequest, Case, CaseParticipant, ConsentGrant, Request, Service, Student, Task, User,
)
from app.models.base import utcnow
from app.models.enums import AccessStatus
from app.security.deps import Principal


# ---- break glass ------------------------------------------------------------------------
def break_glass_students(user_id: uuid.UUID, now: datetime | None = None):
    now = now or utcnow()
    return select(AccessRequest.student_id).where(
        AccessRequest.requester_id == user_id,
        AccessRequest.status == AccessStatus.APPROVED.value,
        AccessRequest.expires_at > now,
    )


def has_break_glass(db: Session, user_id: uuid.UUID, student_id: uuid.UUID, now: datetime | None = None) -> bool:
    return bool(db.scalar(select(exists(break_glass_students(user_id, now).where(AccessRequest.student_id == student_id)))))


# ---- requests -----------------------------------------------------------------------------
def request_detail_criterion(p: Principal, now: datetime | None = None):
    conds = []
    if p.can("request.read_own"):
        conds.append(Request.requester_id == p.id)
    if p.can("request.read_department") and p.department_id:
        conds.append(and_(
            Request.department_id == p.department_id,
            or_(Request.consent_share.is_(True), ~Request.service.has(Service.is_sensitive.is_(True))),
        ))
    if p.role in ("staff", "dept_head", "counsellor", "mentor") and p.can("access.request"):
        conds.append(Request.requester_id.in_(break_glass_students(p.id, now)))
    return or_(*conds) if conds else false()


def request_scope_criterion(p: Principal, now: datetime | None = None):
    """Rows the caller may list at all (detail or metadata only)."""
    if p.can("request.read_all_meta"):
        return Request.id.is_not(None)  # all rows, but detail flag decides text visibility
    return request_detail_criterion(p, now)


def request_has_detail(db: Session, p: Principal, request_id: uuid.UUID, now: datetime | None = None) -> bool:
    return bool(db.scalar(select(exists().where(Request.id == request_id, request_detail_criterion(p, now)))))


# ---- consent ------------------------------------------------------------------------------
def consent_exists_for_case(p: Principal):
    """Correlated criterion on Case: does the student's current consent cover this caller?"""
    if p.role == "mentor":
        return and_(
            Case.student_id.in_(select(Student.user_id).where(Student.mentor_id == p.id)),
            exists().where(ConsentGrant.student_id == Case.student_id, ConsentGrant.kind == "mentor",
                           ConsentGrant.scope == "case_support", ConsentGrant.is_granted.is_(True)),
        )
    if p.department_id:
        return exists().where(
            ConsentGrant.student_id == Case.student_id, ConsentGrant.kind == "department",
            ConsentGrant.department_id == p.department_id, ConsentGrant.scope == "case_support",
            ConsentGrant.is_granted.is_(True),
        )
    return false()


def user_has_consent(db: Session, student_id: uuid.UUID, user: User) -> bool:
    """Is `user` (mentor or department staff) currently consented-to by the student?"""
    if user.role.code == "mentor":
        st = db.get(Student, student_id)
        if not st or st.mentor_id != user.id:
            return False
        return bool(db.scalar(select(exists().where(
            ConsentGrant.student_id == student_id, ConsentGrant.kind == "mentor", ConsentGrant.is_granted.is_(True)))))
    dept = user.staff.department_id if user.staff else None
    if not dept:
        return False
    return bool(db.scalar(select(exists().where(
        ConsentGrant.student_id == student_id, ConsentGrant.kind == "department",
        ConsentGrant.department_id == dept, ConsentGrant.is_granted.is_(True)))))


# ---- cases --------------------------------------------------------------------------------
def case_scope_criterion(p: Principal):
    if not p.can("case.read"):
        return false()
    if p.is_student:
        return Case.student_id == p.id
    conds = [and_(
        Case.id.in_(select(CaseParticipant.case_id).where(CaseParticipant.user_id == p.id, CaseParticipant.removed_at.is_(None))),
        consent_exists_for_case(p),
    )]
    if p.can("case.manage"):
        conds.append(and_(Case.status == "suggested", consent_exists_for_case(p)))
    return or_(*conds)


def get_case(db: Session, p: Principal, case_id: uuid.UUID) -> Case | None:
    return db.scalar(select(Case).where(Case.id == case_id, case_scope_criterion(p)))


# ---- tasks --------------------------------------------------------------------------------
def task_scope_criterion(p: Principal):
    conds = [Task.assignee_id == p.id, Task.created_by == p.id]
    if p.can("task.manage") and p.department_id:
        conds.append(and_(Task.department_id == p.department_id, Task.case_id.is_(None)))
    if p.role == "command":
        conds.append(and_(Task.case_id.is_(None), Task.request_id.is_(None)))
    if p.can("case.read"):
        conds.append(Task.case_id.in_(select(Case.id).where(case_scope_criterion(p))))
    return or_(*conds)

"""Support cases (formerly "Emerging Support Cases").

Design principles carried over from the prototype and now enforced server-side:
  * the system only *suggests*; a person must confirm before anyone is asked to reach out
  * the student's consent decides who may join; revoking consent removes access immediately
  * the evidence is explainable: counts and request numbers, never a made-up "pattern strength"
  * the confirm/dismiss decisions are stored and reported, so rule quality is measured, not claimed
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import Conflict, Forbidden, Invalid, NotFound
from app.models import (
    AccessRequest, Case, CaseMessage, CaseParticipant, ConsentGrant, Department, Request, Student, Task, User,
)
from app.models.base import utcnow
from app.models.enums import ACTIVE_CASE, AccessStatus, CaseOutcome, CaseStatus, ConsentKind
from app.security.deps import Principal
from app.services import access, notify as N
from app.services.audit import audit
from app.services.redact import redact_pii

DISMISS_REASONS = {
    "known_temporary_event": "Known temporary event",
    "already_handled": "Already handled",
    "insufficient_evidence": "Insufficient evidence",
    "not_a_concern": "Not a concern",
}
RULE_CODE = "multi_area_pattern"
LIVE = (CaseStatus.OPEN.value, CaseStatus.MONITORING.value)


def case_ref(c: Case) -> str:
    return f"CASE-{c.number}"


# ---- rule engine ----------------------------------------------------------------------------
def evaluate_student(db: Session, student_id: uuid.UUID, now: datetime | None = None) -> Case | None:
    """Rule `multi_area_pattern`: >= N requests across >= M departments inside a window.

    Parameters live in settings (CASE_WINDOW_DAYS / CASE_MIN_REQUESTS / CASE_MIN_DEPARTMENTS).
    A dismissed suggestion suppresses re-suggestion for one window.
    """
    now = now or utcnow()
    s = get_settings()
    if not db.get(Student, student_id):
        return None
    if db.scalar(select(func.count()).select_from(Case).where(Case.student_id == student_id, Case.status.in_(ACTIVE_CASE))):
        return None
    window_start = now - timedelta(days=s.case_window_days)
    if db.scalar(select(func.count()).select_from(Case).where(
            Case.student_id == student_id, Case.status.in_([CaseStatus.DISMISSED.value, CaseStatus.CLOSED.value]),
            Case.updated_at > window_start)):
        return None
    reqs = db.scalars(select(Request).where(
        Request.requester_id == student_id, Request.created_at >= window_start,
        Request.status.notin_(["cancelled", "rejected"]))).all()
    depts = {r.department_id for r in reqs}
    if len(reqs) < s.case_min_requests or len(depts) < s.case_min_departments:
        return None

    names = sorted({r.department.name for r in reqs})
    case = Case(
        student_id=student_id, status=CaseStatus.SUGGESTED.value, origin="rule", rule_code=RULE_CODE,
        title="Requests across several areas", evidence={
            "rule": RULE_CODE, "window_days": s.case_window_days, "request_count": len(reqs),
            "department_count": len(depts), "departments": names, "request_numbers": sorted(r.number for r in reqs),
            "thresholds": {"min_requests": s.case_min_requests, "min_departments": s.case_min_departments},
        })
    db.add(case)
    db.flush()
    for r in reqs:
        r.case_id = case.id
    audit(db, "case.suggested", entity_type="case", entity_id=case.id, subject=student_id,
          details={"rule": RULE_CODE, "requests": len(reqs), "departments": len(depts)})
    reviewers = _eligible_reviewers(db, student_id)
    if reviewers:
        N.notify(db, reviewers, "case.suggested", f"A support case suggestion is waiting for review ({case_ref(case)})",
                 entity_type="case", entity_id=case.id)
    N.notify(db, [student_id], "case.offer",
             "You have raised requests in several areas. Would you like someone to check in? You decide who can see this "
             "in Privacy & consent.", entity_type="case", entity_id=case.id)
    return case


def evaluate_all(db: Session, now: datetime | None = None) -> int:
    now = now or utcnow()
    s = get_settings()
    q = (select(Request.requester_id).where(Request.requester_id.is_not(None), Request.created_at >= now - timedelta(days=s.case_window_days),
                                            Request.status.notin_(["cancelled", "rejected"]))
         .group_by(Request.requester_id)
         .having(func.count() >= s.case_min_requests, func.count(distinct(Request.department_id)) >= s.case_min_departments))
    n = 0
    for sid in db.scalars(q).all():
        if evaluate_student(db, sid, now):
            n += 1
    return n


def _eligible_reviewers(db: Session, student_id: uuid.UUID) -> list[uuid.UUID]:
    """Users who may review a suggestion: consented mentor + `case.manage` staff of consented departments."""
    out: list[uuid.UUID] = []
    st = db.get(Student, student_id)
    grants = db.scalars(select(ConsentGrant).where(ConsentGrant.student_id == student_id, ConsentGrant.is_granted)).all()
    if st and st.mentor_id and any(g.kind == ConsentKind.MENTOR.value for g in grants):
        out.append(st.mentor_id)
    for g in grants:
        if g.kind == ConsentKind.DEPARTMENT.value:
            for uid in N.dept_user_ids(db, g.department_id, roles=("counsellor",)):
                out.append(uid)
    return list(dict.fromkeys(out))


# ---- human decisions ----------------------------------------------------------------------------
def _require_reviewer(p: Principal) -> None:
    if not p.can("case.manage"):
        raise Forbidden("You cannot manage support cases.")


def confirm(db: Session, p: Principal, case: Case) -> Case:
    _require_reviewer(p)
    if case.status != CaseStatus.SUGGESTED.value:
        raise Conflict("Only a suggested case can be confirmed.", code="invalid_state")
    case.status, case.confirmed_by, case.confirmed_at, case.owner_id = CaseStatus.OPEN.value, p.id, utcnow(), p.id
    case.owner = p.user
    db.add(CaseParticipant(case_id=case.id, user_id=p.id, added_by=p.id))
    N.notify(db, [case.student_id], "case.confirmed",
             "Someone you chose to share with is getting ready to check in. You can accept or decline - nothing is required.",
             entity_type="case", entity_id=case.id)
    audit(db, "case.confirmed", actor=p, entity_type="case", entity_id=case.id, subject=case.student_id)
    return case


def dismiss(db: Session, p: Principal, case: Case, reason: str) -> Case:
    _require_reviewer(p)
    if case.status != CaseStatus.SUGGESTED.value:
        raise Conflict("Only a suggested case can be dismissed.", code="invalid_state")
    if reason not in DISMISS_REASONS:
        raise Invalid("Choose one of the listed reasons.", code="invalid_reason", details=list(DISMISS_REASONS))
    case.status, case.dismissed_reason = CaseStatus.DISMISSED.value, reason
    for r in db.scalars(select(Request).where(Request.case_id == case.id)):
        r.case_id = None
    audit(db, "case.dismissed", actor=p, entity_type="case", entity_id=case.id, subject=case.student_id, details={"reason": reason})
    return case


def create_manual(db: Session, p: Principal, student_id: uuid.UUID, title: str) -> Case:
    """A mentor/counsellor opens a case themselves - still only possible for a student who consented to them."""
    _require_reviewer(p)
    if not db.get(Student, student_id):
        raise NotFound("Student not found")
    if not access.user_has_consent(db, student_id, p.user):
        raise Forbidden("This student has not allowed you to open a support case.", code="no_consent")
    if db.scalar(select(func.count()).select_from(Case).where(Case.student_id == student_id, Case.status.in_(ACTIVE_CASE))):
        raise Conflict("This student already has an active case.", code="already_active")
    case = Case(student_id=student_id, status=CaseStatus.OPEN.value, origin="manual", title=title.strip()[:200] or "Support case",
                evidence={"manual": True}, owner_id=p.id, confirmed_by=p.id, confirmed_at=utcnow())
    db.add(case)
    db.flush()
    db.add(CaseParticipant(case_id=case.id, user_id=p.id, added_by=p.id))
    N.notify(db, [student_id], "case.confirmed", "Someone you chose to share with has opened a support case for you.",
             entity_type="case", entity_id=case.id)
    audit(db, "case.created_manual", actor=p, entity_type="case", entity_id=case.id, subject=student_id)
    return case


def _require_live(case: Case) -> None:
    if case.status not in LIVE:
        raise Conflict("This case is not open.", code="invalid_state")


def add_participant(db: Session, p: Principal, case: Case, user_id: uuid.UUID) -> CaseParticipant:
    _require_reviewer(p)
    _require_live(case)
    u = db.get(User, user_id)
    if not u or not u.is_active or u.role.code == "student":
        raise Invalid("Choose an active staff member.", code="invalid_user")
    if not access.user_has_consent(db, case.student_id, u):
        raise Forbidden("The student has not allowed this person or department to join their support team.", code="no_consent")
    part = db.get(CaseParticipant, (case.id, user_id))
    if part and part.removed_at is None:
        raise Conflict("Already part of this case.", code="already_member")
    if part:
        part.removed_at, part.added_by = None, p.id
    else:
        part = CaseParticipant(case_id=case.id, user_id=user_id, added_by=p.id)
        db.add(part)
    N.notify(db, [user_id], "case.invited", f"You were added to support case {case_ref(case)}", entity_type="case", entity_id=case.id)
    N.notify(db, [case.student_id], "case.team", "Someone you allowed has joined your support team.", entity_type="case", entity_id=case.id)
    audit(db, "case.participant_added", actor=p, entity_type="case", entity_id=case.id, subject=case.student_id,
          details={"user_id": str(user_id)})
    return part


def remove_participant(db: Session, p: Principal, case: Case, user_id: uuid.UUID) -> None:
    _require_reviewer(p)
    part = db.get(CaseParticipant, (case.id, user_id))
    if not part or part.removed_at:
        raise NotFound("Participant not found")
    if case.owner_id == user_id:
        raise Conflict("Hand over primary ownership before removing the owner.", code="is_owner")
    part.removed_at = utcnow()
    audit(db, "case.participant_removed", actor=p, entity_type="case", entity_id=case.id, subject=case.student_id,
          details={"user_id": str(user_id)})


def set_owner(db: Session, p: Principal, case: Case, user_id: uuid.UUID) -> Case:
    _require_reviewer(p)
    _require_live(case)
    part = db.get(CaseParticipant, (case.id, user_id))
    if not part or part.removed_at:
        raise Invalid("The primary owner must already be on the case.", code="not_participant")
    case.owner_id, case.owner = user_id, db.get(User, user_id)
    others = [x.user_id for x in db.scalars(select(CaseParticipant).where(CaseParticipant.case_id == case.id, CaseParticipant.removed_at.is_(None)))]
    N.notify(db, others, "case.owner", f"{case.owner.full_name} now coordinates outreach for {case_ref(case)}; route contact through them.",
             entity_type="case", entity_id=case.id, exclude=[p.id])
    N.notify(db, [case.student_id], "case.owner", "One person will coordinate your support from now on.", entity_type="case", entity_id=case.id)
    audit(db, "case.owner_set", actor=p, entity_type="case", entity_id=case.id, subject=case.student_id, details={"owner": str(user_id)})
    return case


def set_status(db: Session, p: Principal, case: Case, status: str) -> Case:
    _require_reviewer(p)
    if status != CaseStatus.MONITORING.value or case.status != CaseStatus.OPEN.value:
        raise Conflict("A case can only move from open to monitoring here; use close to finish it.", code="invalid_transition")
    case.status = status
    audit(db, "case.status_changed", actor=p, entity_type="case", entity_id=case.id, subject=case.student_id, details={"to": status})
    return case


def close(db: Session, p: Principal, case: Case, outcome: str, note: str | None, now: datetime | None = None) -> Case:
    now = now or utcnow()
    if p.is_student:
        if case.student_id != p.id:
            raise Forbidden("Not your case.")
        outcome, note = CaseOutcome.DECLINED.value, note or "Closed by the student"
    else:
        _require_reviewer(p)
    if outcome not in {o.value for o in CaseOutcome}:
        raise Invalid("Choose a valid outcome.", code="invalid_outcome")
    if case.status not in LIVE:
        raise Conflict("Only an open case can be closed.", code="invalid_state")
    case.status, case.outcome, case.closed_at = CaseStatus.CLOSED.value, outcome, now
    case.closure_note = redact_pii(note)[0] if note else None
    for t in db.scalars(select(Task).where(Task.case_id == case.id, Task.status.in_(["todo", "in_progress", "blocked"]))):
        t.status, t.completed_at = "cancelled", now
    N.notify(db, [x.user_id for x in db.scalars(select(CaseParticipant).where(CaseParticipant.case_id == case.id, CaseParticipant.removed_at.is_(None)))],
             "case.closed", f"{case_ref(case)} was closed ({outcome})", entity_type="case", entity_id=case.id, exclude=[p.id])
    if not p.is_student:
        N.notify(db, [case.student_id], "case.closed", "Your support case was closed. You can ask for help again at any time.",
                 entity_type="case", entity_id=case.id)
    audit(db, "case.closed", actor=p, entity_type="case", entity_id=case.id, subject=case.student_id, details={"outcome": outcome})
    return case


def student_response(db: Session, p: Principal, case: Case, response: str) -> Case:
    if case.student_id != p.id:
        raise Forbidden("Not your case.")
    if response not in ("accepted", "declined"):
        raise Invalid("Choose accepted or declined.")
    _require_live(case)
    case.student_response = response
    N.notify(db, [x.user_id for x in db.scalars(select(CaseParticipant).where(CaseParticipant.case_id == case.id, CaseParticipant.removed_at.is_(None)))],
             "case.response", f"The student {response} outreach for {case_ref(case)}", entity_type="case", entity_id=case.id)
    audit(db, "case.student_response", actor=p, entity_type="case", entity_id=case.id, subject=p.id, details={"response": response})
    return case


def post_message(db: Session, p: Principal, case: Case, text: str) -> CaseMessage:
    if not p.can("case.message"):
        raise Forbidden("You cannot post in this case.")
    _require_live(case)
    body, _ = redact_pii(text.strip())
    if not body:
        raise Invalid("Write a message first.")
    msg = CaseMessage(case_id=case.id, author_id=p.id, body=body)
    db.add(msg)
    recipients = [x.user_id for x in db.scalars(select(CaseParticipant).where(CaseParticipant.case_id == case.id, CaseParticipant.removed_at.is_(None)))]
    N.notify(db, recipients + [case.student_id], "case.message", f"New message in {case_ref(case)}",
             entity_type="case", entity_id=case.id, exclude=[p.id])
    audit(db, "case.message", actor=p, entity_type="case", entity_id=case.id, subject=case.student_id)
    return msg


def create_intervention(db: Session, p: Principal, case: Case, title: str, assignee_id: uuid.UUID | None,
                        due_at: datetime | None, description: str | None) -> Task:
    if not p.can("task.manage"):
        raise Forbidden("You cannot create tasks.")
    _require_live(case)
    if case.student_response == "declined":
        raise Conflict("The student declined outreach, so no outreach tasks can be created.", code="student_declined")
    assignee_id = assignee_id or p.id
    part = db.get(CaseParticipant, (case.id, assignee_id))
    if not part or part.removed_at:
        raise Invalid("Assign the task to someone on the case.", code="not_participant")
    asg = db.get(User, assignee_id)
    task = Task(title=title.strip()[:200], description=redact_pii(description)[0] if description else None, case_id=case.id,
                assignee_id=assignee_id, assignee=asg, created_by=p.id, due_at=due_at,
                department_id=asg.staff.department_id if asg and asg.staff else None, priority="high")
    db.add(task)
    db.flush()
    N.notify(db, [assignee_id], "task.assigned", f"New action on {case_ref(case)}", entity_type="task", entity_id=task.id, exclude=[p.id])
    audit(db, "task.created", actor=p, entity_type="task", entity_id=task.id, subject=case.student_id, details={"case": case.number})
    return task


# ---- consent ------------------------------------------------------------------------------------
def set_consents(db: Session, p: Principal, mentor: bool, department_ids: list[uuid.UUID]) -> None:
    """Replace the student's full set of case-support grants. Revocation is immediate (access checks read this table)."""
    if not p.is_student:
        raise Forbidden("Only students manage their own consent.")
    wanted = {(ConsentKind.MENTOR.value, None): mentor}
    valid = {d.id for d in db.scalars(select(Department).where(Department.is_active))}
    for d in department_ids:
        if d not in valid:
            raise Invalid("Unknown department.", code="invalid_department")
    for d in valid:
        wanted[(ConsentKind.DEPARTMENT.value, d)] = d in set(department_ids)
    existing = {(g.kind, g.department_id): g for g in db.scalars(select(ConsentGrant).where(ConsentGrant.student_id == p.id))}
    changed: list[dict] = []
    for key, flag in wanted.items():
        g = existing.get(key)
        if g is None:
            if flag:
                db.add(ConsentGrant(student_id=p.id, kind=key[0], department_id=key[1], is_granted=True))
                changed.append({"kind": key[0], "department": str(key[1]) if key[1] else None, "granted": True})
        elif g.is_granted != flag:
            g.is_granted, g.changed_at = flag, utcnow()
            changed.append({"kind": key[0], "department": str(key[1]) if key[1] else None, "granted": flag})
    st = db.get(Student, p.id)
    if st and st.consent_onboarded_at is None:
        st.consent_onboarded_at = utcnow()
    audit(db, "consent.updated", actor=p, subject=p.id, entity_type="student", entity_id=p.id, details={"changes": changed})


# ---- break glass ----------------------------------------------------------------------------------
def request_access(db: Session, p: Principal, student_id: uuid.UUID, reason: str) -> AccessRequest:
    if not p.can("access.request"):
        raise Forbidden("You cannot request extra access.")
    if not db.get(Student, student_id):
        raise NotFound("Student not found")
    reason = reason.strip()
    if len(reason) < 10:
        raise Invalid("Explain why you need extra access (at least 10 characters).", code="reason_required")
    if db.scalar(select(func.count()).select_from(AccessRequest).where(
            AccessRequest.student_id == student_id, AccessRequest.requester_id == p.id, AccessRequest.status == AccessStatus.PENDING.value)):
        raise Conflict("You already have a pending request for this student.", code="already_pending")
    ar = AccessRequest(student_id=student_id, requester_id=p.id, reason=reason)
    db.add(ar)
    db.flush()
    N.notify(db, [student_id], "access.requested", f"{p.user.full_name} asked for extra access. You decide.",
             entity_type="access_request", entity_id=ar.id)
    audit(db, "access.requested", actor=p, entity_type="access_request", entity_id=ar.id, subject=student_id)
    return ar


def decide_access(db: Session, p: Principal, ar: AccessRequest, approve: bool, now: datetime | None = None) -> AccessRequest:
    now = now or utcnow()
    if ar.student_id != p.id:
        raise Forbidden("This request is not about you.")
    if ar.status != AccessStatus.PENDING.value:
        raise Conflict("This request was already decided.", code="invalid_state")
    ar.decided_at = now
    if approve:
        ar.status, ar.expires_at = AccessStatus.APPROVED.value, now + timedelta(hours=get_settings().break_glass_hours)
    else:
        ar.status = AccessStatus.DENIED.value
    N.notify(db, [ar.requester_id], "access.decided", f"Your extra-access request was {ar.status}.", entity_type="access_request", entity_id=ar.id)
    audit(db, "access.decided", actor=p, entity_type="access_request", entity_id=ar.id, subject=p.id, details={"approved": approve})
    return ar


def revoke_access(db: Session, p: Principal, ar: AccessRequest) -> AccessRequest:
    if ar.student_id != p.id:
        raise Forbidden("This request is not about you.")
    if ar.status != AccessStatus.APPROVED.value:
        raise Conflict("Only an approved grant can be revoked.", code="invalid_state")
    ar.status, ar.expires_at = AccessStatus.REVOKED.value, utcnow()
    N.notify(db, [ar.requester_id], "access.revoked", "The student ended your extra access.", entity_type="access_request", entity_id=ar.id)
    audit(db, "access.revoked", actor=p, entity_type="access_request", entity_id=ar.id, subject=p.id)
    return ar

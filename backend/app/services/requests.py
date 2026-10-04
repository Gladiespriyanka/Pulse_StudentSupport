"""Request lifecycle: submit -> triage -> (approval) -> assignment -> SLA -> work -> resolution -> feedback.

Rules live here (not in routers) so they are testable and consistent. Every mutation writes a timeline
event, notifications (without free text) and an audit record. Callers commit.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import Conflict, Forbidden, Invalid, NotFound
from app.models import (
    Approval, Feedback, Role, Request, Service, Staff, User,
)
from app.models.base import utcnow
from app.models.enums import (
    ApprovalStatus, Priority, PRIORITY_ORDER, RequestSource, RequestStatus, SLA_RUNNING,
)
from app.rbac import ASSIGNABLE_ROLES
from app.security.deps import Principal
from app.services import access, notify as N, sla, triage as T
from app.services.audit import audit
from app.services.redact import redact_pii
from app.services.timeline import add_event

S = RequestStatus

# status -> statuses reachable by ordinary transitions (approval decisions / feedback handle the rest)
TRANSITIONS: dict[str, set[str]] = {
    S.NEW.value: {S.ACKNOWLEDGED.value, S.IN_PROGRESS.value, S.CANCELLED.value},
    S.PENDING_APPROVAL.value: {S.CANCELLED.value},
    S.ACKNOWLEDGED.value: {S.IN_PROGRESS.value, S.RESOLVED.value, S.CANCELLED.value},
    S.IN_PROGRESS.value: {S.RESOLVED.value, S.CANCELLED.value},
    S.RESOLVED.value: {S.CLOSED.value, S.IN_PROGRESS.value},
}
STAFF_TARGETS = {S.ACKNOWLEDGED.value, S.IN_PROGRESS.value, S.RESOLVED.value}
STUDENT_TARGETS = {S.CANCELLED.value, S.IN_PROGRESS.value}  # in_progress == re-open from resolved


@dataclass
class NewRequest:
    description: str
    service_id: uuid.UUID | None = None
    location_id: uuid.UUID | None = None
    consent_share: bool | None = None  # None = student has not answered (required for sensitive services)
    source: str | None = None


def ref(req: Request) -> str:
    return f"REQ-{req.number}"


# ---- helpers --------------------------------------------------------------------------------
def _title(text: str) -> str:
    first = " ".join(text.split())[:160]
    return first if len(" ".join(text.split())) <= 160 else first[:157] + "..."


def can_manage(db: Session, p: Principal, req: Request) -> bool:
    return (p.can("request.manage") and p.department_id is not None and p.department_id == req.department_id
            and access.request_has_detail(db, p, req.id))


def get_request(db: Session, p: Principal, request_id: uuid.UUID) -> Request:
    """Fetch a request the caller may at least see metadata of; 404 otherwise (do not reveal existence)."""
    req = db.scalar(select(Request).where(Request.id == request_id, access.request_scope_criterion(p)))
    if not req:
        raise NotFound("Request not found")
    return req


def pick_assignee(db: Session, department_id: uuid.UUID) -> uuid.UUID | None:
    """Least-loaded active staff member of the department (ties: non-heads first, then name). Explainable, no scores."""
    open_cnt = (select(Request.assignee_id, func.count().label("n")).where(Request.status.in_(SLA_RUNNING))
                .group_by(Request.assignee_id).subquery())
    q = (select(User.id).join(Staff, Staff.user_id == User.id).join(Role, Role.id == User.role_id)
         .outerjoin(open_cnt, open_cnt.c.assignee_id == User.id)
         .where(Staff.department_id == department_id, User.is_active, Role.code.in_(list(ASSIGNABLE_ROLES)))
         .order_by(func.coalesce(open_cnt.c.n, 0), (Role.code == "dept_head"), User.full_name))
    return db.scalars(q).first()


def _assign(db: Session, req: Request, assignee_id: uuid.UUID | None, actor, *, auto: bool) -> None:
    prev = req.assignee_id
    req.assignee_id = assignee_id
    req.assignee = db.get(User, assignee_id) if assignee_id else None
    add_event(db, req, "assigned", actor=actor, meta={"assignee_id": str(assignee_id) if assignee_id else None,
                                                      "previous": str(prev) if prev else None, "auto": auto})


# ---- creation -------------------------------------------------------------------------------
def create_request(db: Session, p: Principal | None, data: NewRequest, now: datetime | None = None) -> tuple[Request, T.TriageResult]:
    now = now or utcnow()
    text, redactions = redact_pii(data.description.strip())
    if not text:
        raise Invalid("Please describe what is happening.")
    source = data.source or (RequestSource.PORTAL.value if data.service_id else RequestSource.TRIAGE.value)

    if data.service_id:
        svc = db.get(Service, data.service_id)
        if not svc or not svc.is_active or (not svc.student_visible and source == RequestSource.PORTAL.value):
            raise Invalid("That service is not available.", code="service_unavailable")
    res = T.triage(db, text, service_id=data.service_id, location_id=data.location_id)
    if res.department is None:
        raise Invalid("We could not route this automatically. Please choose a service from the catalogue.",
                      code="needs_service")

    svc = res.service
    if svc and svc.is_sensitive and p and p.is_student and data.consent_share is None and not res.is_urgent:
        raise Invalid("This service handles sensitive information. Please choose whether to share the details with the department.",
                      code="consent_required")
    # An urgent message is never blocked on a privacy form: it is shared with the responding department by default.
    consent = True if (data.consent_share is None or res.is_urgent) else bool(data.consent_share)

    approval_needed = bool(svc and svc.requires_approval and not res.is_urgent)
    req = Request(
        requester_id=p.id if p else None, source=source, service_id=svc.id if svc else None, service=svc,
        department_id=res.department.id, department=res.department,
        location_id=res.location.id if res.location else None, location=res.location,
        asset_id=res.asset.id if res.asset else None, asset=res.asset,
        title=_title(text), description=text, priority=res.priority,
        status=S.PENDING_APPROVAL.value if approval_needed else S.NEW.value,
        consent_share=consent, is_urgent=res.is_urgent,
        triage={**res.explanation(), "pii_redactions": redactions},
    )
    db.add(req)
    db.flush()
    add_event(db, req, "created", actor=p, to_status=req.status, meta={"routed_by": res.routed_by})

    if approval_needed:
        db.add(Approval(request_id=req.id, approver_role="dept_head", department_id=req.department_id,
                        requested_by=req.requester_id, status=ApprovalStatus.PENDING.value))
        N.notify(db, N.dept_user_ids(db, req.department_id, roles=("dept_head",)), "approval.requested",
                 f"Approval needed for {ref(req)}", entity_type="request", entity_id=req.id)
        if p:
            N.notify(db, [p.id], "request.pending_approval", f"{ref(req)} is waiting for approval",
                     entity_type="request", entity_id=req.id)
    else:
        _start_work(db, req, now)

    audit(db, "request.created", actor=p, entity_type="request", entity_id=req.id, subject=req.requester_id,
          details={"service": svc.code if svc else None, "department": res.department.code, "source": source,
                   "urgent": res.is_urgent, "routed_by": res.routed_by})
    if p and p.is_student:
        from app.services import cases  # local import to avoid a cycle

        cases.evaluate_student(db, p.id, now)
    return req, res


def _start_work(db: Session, req: Request, now: datetime) -> None:
    """SLA clock + automatic assignment + notifications. Used on creation and after approval."""
    sla.start_sla(db, req, now)
    assignee = pick_assignee(db, req.department_id)
    if assignee:
        _assign(db, req, assignee, None, auto=True)
    targets = [assignee] if assignee else N.dept_user_ids(db, req.department_id)
    kind, title = ("request.urgent", f"URGENT {ref(req)} needs immediate attention") if req.is_urgent else \
                  ("request.new", f"New request {ref(req)}")
    N.notify(db, targets, kind, title, entity_type="request", entity_id=req.id)
    if req.is_urgent:
        N.notify(db, N.dept_user_ids(db, req.department_id, roles=("dept_head",)) + N.role_user_ids(db, "command"),
                 "request.urgent", f"URGENT {ref(req)} raised", entity_type="request", entity_id=req.id)


def create_sos(db: Session, p: Principal, location_id: uuid.UUID | None, note: str | None, now: datetime | None = None) -> Request:
    svc = db.scalars(select(Service).where(Service.is_emergency, Service.is_active)).first()
    if not svc:
        raise Conflict("The SOS service is not configured. Call the emergency number shown on screen.", code="sos_unconfigured")
    text = "SOS: the student asked for immediate help." + (f" Note: {note.strip()}" if note and note.strip() else "")
    req, _ = create_request(db, p, NewRequest(description=text, service_id=svc.id, location_id=location_id,
                                              consent_share=True, source=RequestSource.SOS.value), now)
    req.priority, req.is_urgent = Priority.CRITICAL.value, True
    sla.start_sla(db, req, now or utcnow())
    N.notify(db, N.dept_user_ids(db, req.department_id) + N.role_user_ids(db, "command"), "request.urgent",
             f"SOS {ref(req)} - immediate response needed", entity_type="request", entity_id=req.id)
    audit(db, "request.sos", actor=p, entity_type="request", entity_id=req.id, subject=p.id)
    return req


def create_peer_concern(db: Session, p: Principal, text: str, location_id: uuid.UUID | None, now: datetime | None = None) -> Request:
    """Anonymous: the reporter is NOT stored on the request and not named in the audit log."""
    svc = db.scalars(select(Service).where(Service.category == "wellbeing", Service.is_active)
                     .order_by(Service.code)).first()
    if not svc:
        raise Conflict("Peer concerns are not configured yet. Please contact a counsellor directly.", code="peer_unconfigured")
    body, _ = redact_pii(text.strip())
    if not body:
        raise Invalid("Please write a few words about what you noticed.")
    req = Request(requester_id=None, source=RequestSource.PEER.value, service_id=svc.id, service=svc,
                  department_id=svc.department_id, department=svc.department, title="Anonymous concern about a friend",
                  description="Anonymous peer concern: " + body, priority=Priority.HIGH.value,
                  status=S.NEW.value, consent_share=True, triage={"routed_by": "peer_channel"})
    db.add(req)
    db.flush()
    add_event(db, req, "created", to_status=req.status, meta={"anonymous": True})
    _start_work(db, req, now or utcnow())
    audit(db, "request.peer_concern", entity_type="request", entity_id=req.id,
          details={"note": "reporter not recorded"}, anonymous=True)  # deliberately no actor, ip or request id
    return req


# ---- transitions ----------------------------------------------------------------------------
def transition(db: Session, p: Principal, req: Request, to: str, note: str | None = None, now: datetime | None = None) -> Request:
    now = now or utcnow()
    frm = req.status
    is_requester = req.requester_id == p.id
    manager = can_manage(db, p, req)
    if to not in TRANSITIONS.get(frm, set()):
        raise Conflict(f"A request that is '{frm}' cannot move to '{to}'.", code="invalid_transition")
    if is_requester and not manager:
        if to not in STUDENT_TARGETS or (to == S.IN_PROGRESS.value and frm != S.RESOLVED.value):
            raise Forbidden("You can only cancel a request or reopen a resolved one.")
    elif manager:
        if to not in STAFF_TARGETS or (to == S.IN_PROGRESS.value and frm not in (S.NEW.value, S.ACKNOWLEDGED.value, S.RESOLVED.value)):
            raise Forbidden("Staff cannot make that change.")
        if to == S.RESOLVED.value and not (note and note.strip()):
            raise Invalid("Add a short resolution note for the student.", code="resolution_note_required")
    else:
        raise Forbidden("You cannot change this request.")

    req.status = to
    if manager:
        if req.first_response_at is None and frm == S.NEW.value:
            req.first_response_at = now
        if req.assignee_id is None:
            _assign(db, req, p.id, p, auto=False)
    if to == S.RESOLVED.value:
        req.resolved_at, req.feedback_requested_at = now, now
        N.notify(db, [req.requester_id], "request.resolved", f"{ref(req)} was marked resolved - did it help?",
                 entity_type="request", entity_id=req.id)
    if to == S.IN_PROGRESS.value and frm == S.RESOLVED.value:
        req.resolved_at, req.feedback_requested_at = None, None
        req.reopened_count += 1
        N.notify(db, [req.assignee_id] if req.assignee_id else N.dept_user_ids(db, req.department_id), "request.reopened",
                 f"{ref(req)} was reopened", entity_type="request", entity_id=req.id)
    if to == S.CANCELLED.value:
        req.closed_at = now
        if req.assignee_id:
            N.notify(db, [req.assignee_id], "request.cancelled", f"{ref(req)} was cancelled by the student",
                     entity_type="request", entity_id=req.id)
        for a in db.scalars(select(Approval).where(Approval.request_id == req.id, Approval.status == ApprovalStatus.PENDING.value)):
            a.status = ApprovalStatus.CANCELLED.value
    if manager and req.requester_id and to in (S.ACKNOWLEDGED.value, S.IN_PROGRESS.value) and frm == S.NEW.value:
        N.notify(db, [req.requester_id], "request.update", f"{ref(req)} is now {to.replace('_', ' ')}",
                 entity_type="request", entity_id=req.id)

    add_event(db, req, "status_changed", actor=p, from_status=frm, to_status=to,
              note=redact_pii(note)[0] if note else None)
    audit(db, "request.status_changed", actor=p, entity_type="request", entity_id=req.id, subject=req.requester_id,
          details={"from": frm, "to": to})
    return req


def assign(db: Session, p: Principal, req: Request, assignee_id: uuid.UUID | None) -> Request:
    allowed = (p.can("request.assign") and (p.role == "command" or p.department_id == req.department_id)) or \
              (assignee_id == p.id and can_manage(db, p, req))
    if not allowed:
        raise Forbidden("You cannot assign this request.")
    if req.status not in (S.NEW.value, S.ACKNOWLEDGED.value, S.IN_PROGRESS.value, S.PENDING_APPROVAL.value):
        raise Conflict("Only open requests can be assigned.", code="invalid_state")
    if assignee_id:
        u = db.get(User, assignee_id)
        if not u or not u.is_active or u.role.code not in ASSIGNABLE_ROLES or not u.staff or u.staff.department_id != req.department_id:
            raise Invalid("The assignee must be an active member of the request's department.", code="invalid_assignee")
    _assign(db, req, assignee_id, p, auto=False)
    if assignee_id and assignee_id != p.id:
        N.notify(db, [assignee_id], "request.assigned", f"{ref(req)} was assigned to you", entity_type="request", entity_id=req.id)
    audit(db, "request.assigned", actor=p, entity_type="request", entity_id=req.id, subject=req.requester_id,
          details={"assignee": str(assignee_id) if assignee_id else None})
    return req


def reroute(db: Session, p: Principal, req: Request, service_id: uuid.UUID, now: datetime | None = None) -> Request:
    now = now or utcnow()
    allowed = (p.can("request.assign") and (p.role == "command" or p.department_id == req.department_id)) or \
              (req.service_id is None and can_manage(db, p, req))
    if not allowed:
        raise Forbidden("You cannot re-route this request.")
    if req.status not in (S.NEW.value, S.ACKNOWLEDGED.value, S.IN_PROGRESS.value):
        raise Conflict("Only open requests can be re-routed.", code="invalid_state")
    svc = db.get(Service, service_id)
    if not svc or not svc.is_active:
        raise Invalid("That service is not available.", code="service_unavailable")
    old_dept = req.department.code
    req.service, req.service_id = svc, svc.id
    req.department, req.department_id = svc.department, svc.department_id
    req.assignee_id, req.assignee = None, None
    sla.start_sla(db, req, now, started_at=req.sla_started_at or req.created_at)  # keep the student's original wait
    assignee = pick_assignee(db, req.department_id)
    if assignee:
        _assign(db, req, assignee, p, auto=True)
    add_event(db, req, "rerouted", actor=p, meta={"from_department": old_dept, "to_department": svc.department.code, "service": svc.code})
    N.notify(db, [assignee] if assignee else N.dept_user_ids(db, req.department_id), "request.new",
             f"{ref(req)} was routed to your department", entity_type="request", entity_id=req.id)
    audit(db, "request.rerouted", actor=p, entity_type="request", entity_id=req.id, subject=req.requester_id,
          details={"from": old_dept, "to": svc.department.code})
    return req


def escalate(db: Session, p: Principal, req: Request, note: str | None = None) -> Request:
    if not p.can("request.escalate") or not (p.role == "command" or p.department_id == req.department_id):
        raise Forbidden("You cannot escalate this request.")
    if req.status not in SLA_RUNNING:
        raise Conflict("Only open requests can be escalated.", code="invalid_state")
    old = req.priority
    req.priority = PRIORITY_ORDER[min(PRIORITY_ORDER.index(old) + 1, len(PRIORITY_ORDER) - 1)]
    req.escalation_level = max(req.escalation_level, 1)
    heads = N.dept_user_ids(db, req.department_id, roles=("dept_head",))
    N.notify(db, heads + ([req.assignee_id] if req.assignee_id else []), "request.escalated",
             f"{ref(req)} was escalated to {req.priority}", entity_type="request", entity_id=req.id, exclude=[p.id])
    add_event(db, req, "escalated", actor=p, internal=True, note=note, meta={"manual": True, "priority_from": old, "priority_to": req.priority})
    audit(db, "request.escalated", actor=p, entity_type="request", entity_id=req.id, subject=req.requester_id,
          details={"manual": True, "priority_from": old, "priority_to": req.priority})
    return req


def add_comment(db: Session, p: Principal, req: Request, text: str, internal: bool = False, now: datetime | None = None):
    now = now or utcnow()
    body, _ = redact_pii(text.strip())
    if not body:
        raise Invalid("Write a message first.")
    is_requester = req.requester_id == p.id
    manager = can_manage(db, p, req)
    if not (is_requester or manager):
        raise Forbidden("You cannot comment on this request.")
    if internal and not manager:
        raise Forbidden("Only staff can write internal notes.")
    if req.status in (S.CANCELLED.value, S.REJECTED.value, S.CLOSED.value):
        raise Conflict("This request is finished. Raise a new request if you still need help.", code="invalid_state")
    ev = add_event(db, req, "comment", actor=p, note=body, internal=internal)
    if manager and not internal:
        if req.first_response_at is None and req.status in SLA_RUNNING:
            req.first_response_at = now
        N.notify(db, [req.requester_id], "request.comment", f"New message on {ref(req)}", entity_type="request", entity_id=req.id)
    elif is_requester:
        N.notify(db, [req.assignee_id] if req.assignee_id else N.dept_user_ids(db, req.department_id), "request.comment",
                 f"The student replied on {ref(req)}", entity_type="request", entity_id=req.id)
    audit(db, "request.comment", actor=p, entity_type="request", entity_id=req.id, subject=req.requester_id,
          details={"internal": internal})
    return ev


def set_consent_share(db: Session, p: Principal, req: Request, share: bool) -> Request:
    if req.requester_id != p.id:
        raise Forbidden("Only the student who raised this request can change sharing.")
    if req.consent_share != share:
        req.consent_share = share
        add_event(db, req, "consent_changed", actor=p, internal=True, meta={"share": share})
        if share:
            N.notify(db, [req.assignee_id] if req.assignee_id else N.dept_user_ids(db, req.department_id), "request.shared",
                     f"The student now shares {ref(req)} with your department", entity_type="request", entity_id=req.id)
        audit(db, "consent.request_share", actor=p, entity_type="request", entity_id=req.id, subject=p.id, details={"share": share})
    return req


def give_feedback(db: Session, p: Principal, req: Request, helpful: bool, comment: str | None, now: datetime | None = None) -> Feedback:
    if req.requester_id != p.id:
        raise Forbidden("Only the student who raised this request can give feedback.")
    if req.status not in (S.RESOLVED.value, S.CLOSED.value):
        raise Conflict("Feedback is possible once the request is resolved.", code="invalid_state")
    if db.scalar(select(func.count()).select_from(Feedback).where(Feedback.request_id == req.id, Feedback.student_id == p.id)):
        raise Conflict("You already gave feedback on this request.", code="already_given")
    now = now or utcnow()
    fb = Feedback(student_id=p.id, request_id=req.id, helpful=helpful, comment=redact_pii(comment)[0] if comment else None)
    db.add(fb)
    add_event(db, req, "feedback", actor=p, meta={"helpful": helpful})
    if req.status == S.RESOLVED.value:
        if helpful:
            req.status, req.closed_at = S.CLOSED.value, now
            add_event(db, req, "status_changed", actor=p, from_status=S.RESOLVED.value, to_status=S.CLOSED.value, note="Closed after positive feedback")
        else:
            req.status, req.resolved_at, req.feedback_requested_at = S.IN_PROGRESS.value, None, None
            req.reopened_count += 1
            add_event(db, req, "status_changed", actor=p, from_status=S.RESOLVED.value, to_status=S.IN_PROGRESS.value,
                      note="Reopened because the student said it did not help")
            N.notify(db, [req.assignee_id] if req.assignee_id else N.dept_user_ids(db, req.department_id), "request.reopened",
                     f"{ref(req)} did not help the student - please revisit", entity_type="request", entity_id=req.id)
    audit(db, "request.feedback", actor=p, entity_type="request", entity_id=req.id, subject=p.id, details={"helpful": helpful})
    return fb

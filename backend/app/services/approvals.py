"""Approvals for requests (service requires approval) and changes. Separation of duties is enforced."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.errors import Conflict, Forbidden, Invalid
from app.models import Approval, Change, Request
from app.models.base import utcnow
from app.models.enums import ApprovalStatus, ChangeStatus, RequestStatus
from app.security.deps import Principal
from app.services import notify as N
from app.services.audit import audit
from app.services.timeline import add_event


def eligible(p: Principal, a: Approval) -> bool:
    if not p.can("approval.decide") or p.role != a.approver_role:
        return False
    return a.department_id is None or p.department_id == a.department_id


def pending_query(p: Principal):
    q = select(Approval).where(Approval.status == ApprovalStatus.PENDING.value, Approval.approver_role == p.role)
    if p.role != "command":
        q = q.where(Approval.department_id == p.department_id)
    return q.order_by(Approval.created_at)


def decide(db: Session, p: Principal, a: Approval, approve: bool, reason: str | None, now: datetime | None = None) -> Approval:
    from app.services import requests as R  # local import: requests <-> approvals

    now = now or utcnow()
    if not eligible(p, a):
        raise Forbidden("You are not an approver for this item.")
    if a.status != ApprovalStatus.PENDING.value:
        raise Conflict("This approval was already decided.", code="invalid_state")
    reason = (reason or "").strip() or None
    if not approve and not reason:
        raise Invalid("Give a reason when rejecting.", code="reason_required")

    req = db.get(Request, a.request_id) if a.request_id else None
    chg = db.get(Change, a.change_id) if a.change_id else None
    if p.id in {a.requested_by, req.requester_id if req else None, chg.requested_by if chg else None}:
        raise Forbidden("You cannot approve your own request (separation of duties).", code="self_approval")

    a.status = ApprovalStatus.APPROVED.value if approve else ApprovalStatus.REJECTED.value
    a.decided_by, a.decided_at, a.reason = p.id, now, reason

    if req:
        if req.status != RequestStatus.PENDING_APPROVAL.value:
            raise Conflict("The request is no longer waiting for approval.", code="invalid_state")
        if approve:
            req.status = RequestStatus.NEW.value
            add_event(db, req, "approval", actor=p, from_status=RequestStatus.PENDING_APPROVAL.value, to_status=RequestStatus.NEW.value,
                      note=reason or "Approved")
            R._start_work(db, req, now)  # SLA clock starts at approval, then assignment
        else:
            req.status, req.closed_at = RequestStatus.REJECTED.value, now
            add_event(db, req, "approval", actor=p, from_status=RequestStatus.PENDING_APPROVAL.value, to_status=RequestStatus.REJECTED.value, note=reason)
        N.notify(db, [req.requester_id], "approval.decided", f"{R.ref(req)} was {'approved' if approve else 'not approved'}",
                 entity_type="request", entity_id=req.id)
        subject = req.requester_id
    else:
        assert chg is not None
        if chg.status != ChangeStatus.AWAITING_APPROVAL.value:
            raise Conflict("The change is no longer waiting for approval.", code="invalid_state")
        chg.status = ChangeStatus.APPROVED.value if approve else ChangeStatus.REJECTED.value
        N.notify(db, [chg.requested_by], "approval.decided", f"CHG-{chg.number} was {'approved' if approve else 'rejected'}",
                 entity_type="change", entity_id=chg.id)
        subject = None
    audit(db, "approval.decided", actor=p, entity_type="approval", entity_id=a.id, subject=subject,
          details={"approved": approve, "request": str(a.request_id) if a.request_id else None,
                   "change": str(a.change_id) if a.change_id else None})
    return a

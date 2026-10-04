"""SLA clocks, state computation and the escalation sweep. All time-dependent functions accept `now`."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Request, SlaPolicy
from app.models.base import utcnow
from app.models.enums import PRIORITY_ORDER, RequestStatus, SLA_RUNNING
from app.services import notify as N
from app.services.audit import audit
from app.services.timeline import add_event


def resolve_policy(db: Session, service_id, priority: str) -> SlaPolicy | None:
    """Service-specific policy wins over the campus default for that priority."""
    q = (select(SlaPolicy).where(SlaPolicy.priority == priority, SlaPolicy.is_active,
                                 or_(SlaPolicy.service_id == service_id, SlaPolicy.service_id.is_(None)))
         .order_by(SlaPolicy.service_id.is_(None)))  # False (specific) sorts before True (default)
    return db.scalars(q).first()


def start_sla(db: Session, req: Request, now: datetime, started_at: datetime | None = None) -> None:
    pol = resolve_policy(db, req.service_id, req.priority)
    req.sla_policy_id = pol.id if pol else None
    req.sla_policy = pol
    req.sla_started_at = started_at or now
    if pol:
        req.response_due_at = req.sla_started_at + timedelta(minutes=pol.response_minutes)
        req.resolution_due_at = req.sla_started_at + timedelta(minutes=pol.resolution_minutes)
    else:
        req.response_due_at = req.resolution_due_at = None


def _clock(started, due, done_at, now, pct: int) -> str:
    if due is None or started is None:
        return "n/a"
    if done_at is not None:
        return "met" if done_at <= due else "met_late"
    if now > due:
        return "breached"
    if now >= started + (due - started) * pct / 100:
        return "at_risk"
    return "on_track"


def sla_view(req: Request, now: datetime | None = None) -> dict:
    now = now or utcnow()
    st = req.status
    if st == RequestStatus.PENDING_APPROVAL.value:
        resp = res = "paused"
    elif st in (RequestStatus.CANCELLED.value, RequestStatus.REJECTED.value) or req.sla_started_at is None:
        resp = res = "n/a"
    else:
        pct = req.sla_policy.at_risk_pct if req.sla_policy else 75
        resp = _clock(req.sla_started_at, req.response_due_at, req.first_response_at, now, pct)
        res = _clock(req.sla_started_at, req.resolution_due_at, req.resolved_at, now, pct)
    mins = None
    if req.resolution_due_at and st in SLA_RUNNING:
        mins = int((req.resolution_due_at - now).total_seconds() // 60)
    return {"response": resp, "resolution": res, "response_due_at": req.response_due_at,
            "resolution_due_at": req.resolution_due_at, "minutes_to_resolution_due": mins,
            "escalation_level": req.escalation_level}


def _raise(priority: str) -> str:
    i = PRIORITY_ORDER.index(priority)
    return PRIORITY_ORDER[min(i + 1, len(PRIORITY_ORDER) - 1)]


def sweep(db: Session, now: datetime | None = None) -> dict:
    """Idempotent: warn when at risk, escalate on breach (L1: dept heads), severe overdue (L2: priority raised + command)."""
    now = now or utcnow()
    s = get_settings()
    stats = {"at_risk_notified": 0, "escalated_level1": 0, "escalated_level2": 0, "auto_closed": 0, "case_suggestions": 0}

    for r in db.scalars(select(Request).where(Request.status.in_(SLA_RUNNING), Request.sla_started_at.is_not(None))):
        view = sla_view(r, now)
        breached = "breached" in (view["response"], view["resolution"])
        at_risk = "at_risk" in (view["response"], view["resolution"])
        owners = [r.assignee_id] if r.assignee_id else N.dept_user_ids(db, r.department_id)
        heads = N.dept_user_ids(db, r.department_id, roles=("dept_head",))
        ref = f"REQ-{r.number}"

        if at_risk and not breached and r.at_risk_notified_at is None:
            r.at_risk_notified_at = now
            N.notify(db, owners, "sla.at_risk", f"{ref} is close to its SLA deadline", entity_type="request", entity_id=r.id)
            stats["at_risk_notified"] += 1

        if breached and r.escalation_level < 1:
            r.escalation_level = 1
            N.notify(db, heads + owners, "sla.breached", f"{ref} has breached its SLA", entity_type="request", entity_id=r.id)
            add_event(db, r, "escalated", internal=True, meta={"level": 1, "reason": "sla_breached"})
            audit(db, "request.escalated", entity_type="request", entity_id=r.id, subject=r.requester_id, details={"level": 1})
            stats["escalated_level1"] += 1

        pol = r.sla_policy
        if pol and r.escalation_level < 2 and r.sla_started_at:
            severe_at = r.sla_started_at + timedelta(minutes=float(pol.resolution_minutes) * float(pol.severe_multiplier))
            if now > severe_at:
                old = r.priority
                r.priority = _raise(r.priority)
                r.escalation_level = 2
                N.notify(db, heads + N.role_user_ids(db, "command"), "sla.escalated",
                         f"{ref} is severely overdue and was raised to {r.priority}", entity_type="request", entity_id=r.id)
                add_event(db, r, "escalated", internal=True, meta={"level": 2, "priority_from": old, "priority_to": r.priority})
                audit(db, "request.escalated", entity_type="request", entity_id=r.id, subject=r.requester_id,
                      details={"level": 2, "priority_from": old, "priority_to": r.priority})
                stats["escalated_level2"] += 1

    cutoff = now - timedelta(days=s.auto_close_resolved_days)
    for r in db.scalars(select(Request).where(Request.status == RequestStatus.RESOLVED.value, Request.resolved_at < cutoff)):
        r.status = RequestStatus.CLOSED.value
        r.closed_at = now
        add_event(db, r, "status_changed", from_status=RequestStatus.RESOLVED.value, to_status=RequestStatus.CLOSED.value,
                  note="Closed automatically after the feedback window", meta={"auto": True})
        audit(db, "request.auto_closed", entity_type="request", entity_id=r.id, subject=r.requester_id)
        stats["auto_closed"] += 1

    from app.services import cases  # local import: cases -> requests -> sla

    stats["case_suggestions"] = cases.evaluate_all(db, now)
    return stats

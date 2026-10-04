"""Incident -> Problem -> Change.

  Incident : a disruption affecting one or more requests (e.g. "Hostel B lift out of service").
  Problem  : the investigation of the underlying cause of one or more incidents.
  Change   : the approved fix. Completing a successful change resolves its problem and drafts a
             knowledge article (cause, workaround, resolution) for staff to review and publish.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import Conflict, Forbidden, Invalid, NotFound
from app.models import Approval, Change, Incident, KnowledgeArticle, Problem, Request, Task, User
from app.models.base import utcnow
from app.models.enums import (
    ApprovalStatus, ChangeOutcome, ChangeRisk, ChangeStatus, IncidentStatus, PRIORITY_ORDER, ProblemStatus,
    RequestStatus, SLA_RUNNING,
)
from app.security.deps import Principal
from app.services import notify as N, requests as R
from app.services.audit import audit
from app.services.timeline import add_event

I, P_, C = IncidentStatus, ProblemStatus, ChangeStatus

INCIDENT_NEXT = {
    I.OPEN.value: {I.INVESTIGATING.value, I.MITIGATED.value, I.RESOLVED.value},
    I.INVESTIGATING.value: {I.MITIGATED.value, I.RESOLVED.value},
    I.MITIGATED.value: {I.INVESTIGATING.value, I.RESOLVED.value},
    I.RESOLVED.value: {I.CLOSED.value, I.INVESTIGATING.value},
    I.CLOSED.value: set(),
}
PROBLEM_NEXT = {
    P_.OPEN.value: {P_.INVESTIGATING.value, P_.CLOSED.value},
    P_.INVESTIGATING.value: {P_.KNOWN_ERROR.value, P_.RESOLVED.value, P_.CLOSED.value},
    P_.KNOWN_ERROR.value: {P_.INVESTIGATING.value, P_.RESOLVED.value, P_.CLOSED.value},
    P_.RESOLVED.value: {P_.CLOSED.value, P_.INVESTIGATING.value},
    P_.CLOSED.value: set(),
}


def _own_dept_or_command(p: Principal, dept_id: uuid.UUID) -> bool:
    return p.role == "command" or (p.department_id is not None and p.department_id == dept_id)


# ---- incidents -----------------------------------------------------------------------------------
def create_incident(db: Session, p: Principal, *, title: str, description: str | None, department_id: uuid.UUID | None,
                    location_id, asset_id, priority: str | None, request_ids: list[uuid.UUID]) -> Incident:
    if not p.can("incident.manage"):
        raise Forbidden("You cannot create incidents.")
    reqs = [R.get_request(db, p, rid) for rid in dict.fromkeys(request_ids)]
    for r in reqs:
        if r.incident_id:
            raise Conflict(f"REQ-{r.number} is already linked to an incident.", code="already_linked")
    dept = department_id or p.department_id or (reqs[0].department_id if reqs else None)
    if not dept:
        raise Invalid("Choose the lead department.", code="department_required")
    if p.role != "command" and p.department_id != dept:
        raise Forbidden("You can only lead incidents for your own department.")
    prio = priority or (max((r.priority for r in reqs), key=PRIORITY_ORDER.index) if reqs else "medium")
    inc = Incident(title=title.strip()[:200], description=description, department_id=dept, priority=prio, created_by=p.id,
                   owner_id=p.id if p.department_id == dept else None,
                   location_id=location_id or next((r.location_id for r in reqs if r.location_id), None),
                   asset_id=asset_id or next((r.asset_id for r in reqs if r.asset_id), None))
    db.add(inc)
    db.flush()
    for r in reqs:
        r.incident_id = inc.id
        add_event(db, r, "linked_incident", actor=p, internal=True, meta={"incident": inc.number})
    N.notify(db, [r.assignee_id for r in reqs], "incident.linked", f"REQ linked to INC-{inc.number}", entity_type="incident", entity_id=inc.id, exclude=[p.id])
    audit(db, "incident.created", actor=p, entity_type="incident", entity_id=inc.id,
          details={"linked_requests": len(reqs), "department": str(dept)})
    return inc


def link_requests(db: Session, p: Principal, inc: Incident, request_ids: list[uuid.UUID]) -> Incident:
    _require_incident_manager(p, inc)
    if inc.status in (I.RESOLVED.value, I.CLOSED.value):
        raise Conflict("This incident is finished.", code="invalid_state")
    for rid in dict.fromkeys(request_ids):
        r = R.get_request(db, p, rid)
        if r.incident_id and r.incident_id != inc.id:
            raise Conflict(f"REQ-{r.number} is linked to another incident.", code="already_linked")
        r.incident_id = inc.id
        add_event(db, r, "linked_incident", actor=p, internal=True, meta={"incident": inc.number})
    audit(db, "incident.requests_linked", actor=p, entity_type="incident", entity_id=inc.id, details={"count": len(request_ids)})
    return inc


def _require_incident_manager(p: Principal, inc: Incident) -> None:
    if not p.can("incident.manage") or not _own_dept_or_command(p, inc.department_id):
        raise Forbidden("You cannot manage this incident.")


def update_incident(db: Session, p: Principal, inc: Incident, *, status: str | None = None, note: str | None = None,
                    owner_id: uuid.UUID | None = None, set_owner: bool = False, resolve_linked: bool = False,
                    force: bool = False, now: datetime | None = None) -> Incident:
    now = now or utcnow()
    _require_incident_manager(p, inc)
    if set_owner:
        if owner_id:
            u = db.get(User, owner_id)
            if not u or not u.is_active or not u.staff or u.staff.department_id != inc.department_id:
                raise Invalid("The owner must be an active member of the lead department.", code="invalid_owner")
        inc.owner_id, inc.owner = owner_id, (db.get(User, owner_id) if owner_id else None)
    if status and status != inc.status:
        if status not in INCIDENT_NEXT[inc.status]:
            raise Conflict(f"An incident that is '{inc.status}' cannot move to '{status}'.", code="invalid_transition")
        if status == I.RESOLVED.value:
            if not (note and note.strip()):
                raise Invalid("Add a resolution note.", code="resolution_note_required")
            open_tasks = db.scalar(select(Task.id).where(Task.incident_id == inc.id, Task.status.in_(["todo", "in_progress", "blocked"])).limit(1))
            if open_tasks and not force:
                raise Conflict("This incident still has open tasks. Finish them or resolve with force.", code="open_tasks")
            inc.resolved_at, inc.resolution_note = now, note.strip()
            if resolve_linked:
                for r in db.scalars(select(Request).where(Request.incident_id == inc.id, Request.status.in_(SLA_RUNNING))):
                    r.status, r.resolved_at, r.feedback_requested_at = RequestStatus.RESOLVED.value, now, now
                    r.first_response_at = r.first_response_at or now
                    add_event(db, r, "status_changed", actor=p, from_status=RequestStatus.IN_PROGRESS.value, to_status=RequestStatus.RESOLVED.value,
                              note=f"Resolved through incident INC-{inc.number}")
                    N.notify(db, [r.requester_id], "request.resolved", f"{R.ref(r)} was resolved - did it help?", entity_type="request", entity_id=r.id)
        if status == I.INVESTIGATING.value and inc.status == I.RESOLVED.value:
            inc.resolved_at = None
        inc.status = status
    audit(db, "incident.updated", actor=p, entity_type="incident", entity_id=inc.id, details={"status": inc.status, "resolve_linked": resolve_linked})
    return inc


def link_problem(db: Session, p: Principal, inc: Incident, *, problem_id: uuid.UUID | None, title: str | None) -> Problem:
    """Incident -> Problem: link an existing problem or open a new one from the incident."""
    if not p.can("problem.manage") or not _own_dept_or_command(p, inc.department_id):
        raise Forbidden("You cannot open or link problems for this incident.")
    if inc.problem_id:
        raise Conflict("This incident already has a problem.", code="already_linked")
    if problem_id:
        prob = db.get(Problem, problem_id)
        if not prob:
            raise NotFound("Problem not found")
    else:
        prob = Problem(title=(title or f"Cause of: {inc.title}")[:200], description=inc.description, department_id=inc.department_id,
                       owner_id=p.id if p.department_id == inc.department_id else None, created_by=p.id)
        db.add(prob)
        db.flush()
    inc.problem_id = prob.id
    audit(db, "problem.linked", actor=p, entity_type="problem", entity_id=prob.id, details={"incident": inc.number})
    return prob


# ---- problems ------------------------------------------------------------------------------------
def update_problem(db: Session, p: Principal, prob: Problem, *, status: str | None, root_cause: str | None,
                   workaround: str | None, resolution: str | None, owner_id: uuid.UUID | None, set_owner: bool,
                   now: datetime | None = None) -> Problem:
    now = now or utcnow()
    if not p.can("problem.manage") or not _own_dept_or_command(p, prob.department_id):
        raise Forbidden("You cannot manage this problem.")
    if root_cause is not None:
        prob.root_cause = root_cause
    if workaround is not None:
        prob.workaround = workaround
    if resolution is not None:
        prob.resolution = resolution
    if set_owner:
        prob.owner_id, prob.owner = owner_id, (db.get(User, owner_id) if owner_id else None)
    if status and status != prob.status:
        if status not in PROBLEM_NEXT[prob.status]:
            raise Conflict(f"A problem that is '{prob.status}' cannot move to '{status}'.", code="invalid_transition")
        if status == P_.KNOWN_ERROR.value and not (prob.root_cause and prob.workaround):
            raise Invalid("A known error needs a root cause and a workaround.", code="incomplete")
        if status == P_.RESOLVED.value:
            if not (prob.resolution and prob.resolution.strip()):
                raise Invalid("Describe the resolution first.", code="resolution_required")
            prob.resolved_at = now
        elif prob.status == P_.RESOLVED.value:
            prob.resolved_at = None
        prob.status = status
    audit(db, "problem.updated", actor=p, entity_type="problem", entity_id=prob.id, details={"status": prob.status})
    return prob


# ---- changes -------------------------------------------------------------------------------------
def create_change(db: Session, p: Principal, *, title: str, description: str | None, problem_id: uuid.UUID | None,
                  department_id: uuid.UUID | None, risk: str, implementation_plan: str | None, rollback_plan: str | None,
                  planned_start: datetime | None, planned_end: datetime | None) -> Change:
    if not p.can("change.manage"):
        raise Forbidden("You cannot create changes.")
    prob = db.get(Problem, problem_id) if problem_id else None
    if problem_id and not prob:
        raise NotFound("Problem not found")
    dept = department_id or (prob.department_id if prob else None) or p.department_id
    if not dept:
        raise Invalid("Choose the owning department.", code="department_required")
    if not _own_dept_or_command(p, dept):
        raise Forbidden("You can only raise changes for your own department.")
    ch = Change(title=title.strip()[:200], description=description, problem_id=problem_id, department_id=dept, risk=risk,
                implementation_plan=implementation_plan, rollback_plan=rollback_plan, planned_start=planned_start,
                planned_end=planned_end, requested_by=p.id)
    db.add(ch)
    db.flush()
    audit(db, "change.created", actor=p, entity_type="change", entity_id=ch.id, details={"risk": risk, "problem": prob.number if prob else None})
    return ch


def _require_change_manager(p: Principal, ch: Change) -> None:
    if not p.can("change.manage") or not (p.role == "command" or p.id == ch.requested_by or _own_dept_or_command(p, ch.department_id)):
        raise Forbidden("You cannot manage this change.")


def submit_change(db: Session, p: Principal, ch: Change) -> Change:
    _require_change_manager(p, ch)
    if ch.status != C.DRAFT.value:
        raise Conflict("Only a draft change can be submitted.", code="invalid_state")
    if ch.risk != ChangeRisk.LOW.value and not (ch.implementation_plan and ch.rollback_plan):
        raise Invalid("Medium and high risk changes need an implementation plan and a rollback plan.", code="plans_required")
    requester = db.get(User, ch.requested_by)
    # low risk: the department head approves; otherwise (or if the head is the requester) Command approves
    role = "dept_head" if (ch.risk == ChangeRisk.LOW.value and requester.role.code != "dept_head") else "command"
    ch.status = C.AWAITING_APPROVAL.value
    db.add(Approval(change_id=ch.id, approver_role=role, department_id=ch.department_id if role == "dept_head" else None,
                    requested_by=ch.requested_by))
    approvers = N.dept_user_ids(db, ch.department_id, roles=("dept_head",)) if role == "dept_head" else N.role_user_ids(db, "command")
    N.notify(db, approvers, "approval.requested", f"Approval needed for CHG-{ch.number}", entity_type="change", entity_id=ch.id, exclude=[p.id])
    audit(db, "change.submitted", actor=p, entity_type="change", entity_id=ch.id, details={"approver_role": role})
    return ch


def start_change(db: Session, p: Principal, ch: Change, now: datetime | None = None) -> Change:
    _require_change_manager(p, ch)
    if ch.status != C.APPROVED.value:
        raise Conflict("Only an approved change can be started.", code="invalid_state")
    ch.status, ch.started_at, ch.implemented_by = C.IMPLEMENTING.value, now or utcnow(), p.id
    audit(db, "change.started", actor=p, entity_type="change", entity_id=ch.id)
    return ch


def cancel_change(db: Session, p: Principal, ch: Change) -> Change:
    _require_change_manager(p, ch)
    if ch.status not in (C.DRAFT.value, C.AWAITING_APPROVAL.value, C.APPROVED.value):
        raise Conflict("This change can no longer be cancelled.", code="invalid_state")
    ch.status = C.CANCELLED.value
    for a in db.scalars(select(Approval).where(Approval.change_id == ch.id, Approval.status == ApprovalStatus.PENDING.value)):
        a.status = ApprovalStatus.CANCELLED.value
    audit(db, "change.cancelled", actor=p, entity_type="change", entity_id=ch.id)
    return ch


def complete_change(db: Session, p: Principal, ch: Change, outcome: str, note: str | None, now: datetime | None = None) -> Change:
    now = now or utcnow()
    _require_change_manager(p, ch)
    if ch.status != C.IMPLEMENTING.value:
        raise Conflict("Only a change that is being implemented can be completed.", code="invalid_state")
    if outcome not in {o.value for o in ChangeOutcome}:
        raise Invalid("Outcome must be successful or failed.", code="invalid_outcome")
    ch.status, ch.outcome, ch.outcome_note, ch.completed_at = C.COMPLETED.value, outcome, note, now
    kb_id = None
    if outcome == ChangeOutcome.SUCCESSFUL.value and ch.problem_id:
        prob = db.get(Problem, ch.problem_id)
        pending = db.scalar(select(Change.id).where(Change.problem_id == prob.id, Change.id != ch.id,
                                                    Change.status.in_([C.DRAFT.value, C.AWAITING_APPROVAL.value, C.APPROVED.value, C.IMPLEMENTING.value])).limit(1))
        if not pending and prob.status not in (P_.RESOLVED.value, P_.CLOSED.value):
            prob.status, prob.resolved_at = P_.RESOLVED.value, now
            prob.resolution = (note or ch.title).strip()
            art = KnowledgeArticle(
                title=f"{prob.title}", department_id=prob.department_id, audience="staff", status="draft",
                source_problem_id=prob.id, created_by=p.id, updated_by=p.id,
                body=f"**Problem:** {prob.description or prob.title}\n\n**Root cause:** {prob.root_cause or 'Not recorded'}\n\n"
                     f"**Workaround:** {prob.workaround or 'None'}\n\n**Resolution:** {prob.resolution}\n\n"
                     f"_Drafted automatically from PRB-{prob.number} / CHG-{ch.number}. Review before publishing._")
            db.add(art)
            db.flush()
            kb_id = art.id
            owners = [i.owner_id for i in db.scalars(select(Incident).where(Incident.problem_id == prob.id))]
            N.notify(db, owners + [prob.owner_id], "problem.resolved", f"PRB-{prob.number} was resolved - review its incidents",
                     entity_type="problem", entity_id=prob.id, exclude=[p.id])
    audit(db, "change.completed", actor=p, entity_type="change", entity_id=ch.id, details={"outcome": outcome, "knowledge_draft": str(kb_id) if kb_id else None})
    return ch

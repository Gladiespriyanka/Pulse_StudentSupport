"""Turn ORM rows into API dicts. Visibility decisions are made here, once, from the access layer."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Approval, Case, CaseMessage, CaseParticipant, Change, Incident, Notification, Problem, Request, RequestEvent, Task, User,
)
from app.models.base import utcnow
from app.security.deps import Principal
from app.services import access, sla


def user_out(u: User) -> dict:
    perms = sorted(p.code for p in u.role.permissions)
    return {
        "id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role.code, "role_name": u.role.name,
        "department_id": u.staff.department_id if u.staff else None,
        "department_name": u.staff.department.name if u.staff and u.staff.department else None,
        "must_change_password": u.must_change_password, "permissions": perms,
        "consent_onboarded": (u.student.consent_onboarded_at is not None) if u.student else None,
    }


def person(u: User | None) -> dict | None:
    if not u:
        return None
    return {"id": u.id, "name": u.full_name, "role": u.role.code,
            "department": u.staff.department.name if u.staff and u.staff.department else None}


def detail_ids(db: Session, p: Principal, ids: list[uuid.UUID], now: datetime | None = None) -> set[uuid.UUID]:
    if not ids:
        return set()
    return set(db.scalars(select(Request.id).where(Request.id.in_(ids), access.request_detail_criterion(p, now))))


def request_out(req: Request, p: Principal, detail: bool, now: datetime | None = None) -> dict:
    now = now or utcnow()
    out = {
        "id": req.id, "number": req.number, "ref": f"REQ-{req.number}",
        "title": req.title if detail else (req.service.name if req.service else "Unrouted request"),
        "status": req.status, "priority": req.priority, "source": req.source, "is_urgent": req.is_urgent,
        "service": {"id": req.service.id, "name": req.service.name, "category": req.service.category,
                    "is_sensitive": req.service.is_sensitive} if req.service else None,
        "department": {"id": req.department.id, "name": req.department.name},
        "location": {"id": req.location.id, "name": req.location.name} if req.location else None,
        "asset": {"id": req.asset.id, "name": req.asset.name} if req.asset else None,
        "assignee": person(req.assignee), "incident_id": req.incident_id, "case_id": req.case_id if detail else None,
        "created_at": req.created_at, "updated_at": req.updated_at, "resolved_at": req.resolved_at,
        "sla": sla.sla_view(req, now), "restricted": not detail, "reopened_count": req.reopened_count,
        "feedback_requested": req.feedback_requested_at is not None and req.status == "resolved",
    }
    if detail:
        out.update({
            "description": req.description, "consent_share": req.consent_share, "triage": req.triage,
            "requester": ({"id": req.requester.id, "name": req.requester.full_name,
                           "roll_no": req.requester.student.roll_no if req.requester.student else None}
                          if req.requester and not p.is_student else None),
        })
    return out


def event_out(ev: RequestEvent) -> dict:
    return {"id": ev.id, "kind": ev.kind, "from_status": ev.from_status, "to_status": ev.to_status, "note": ev.note,
            "internal": ev.is_internal, "meta": ev.meta, "created_at": ev.created_at,
            "actor": ({"name": ev.actor.full_name, "role": ev.actor.role.code} if ev.actor else None)}


def events_for(db: Session, req: Request, p: Principal) -> list[dict]:
    q = select(RequestEvent).where(RequestEvent.request_id == req.id).order_by(RequestEvent.created_at, RequestEvent.id)
    rows = db.scalars(q).all()
    return [event_out(e) for e in rows if not (e.is_internal and p.is_student)]


def task_out(t: Task, p: Principal) -> dict:
    parent = next((k for k in ("request", "incident", "problem", "change", "case") if getattr(t, f"{k}_id")), None)
    out = {"id": t.id, "number": t.number, "ref": f"TSK-{t.number}", "title": t.title, "status": t.status, "priority": t.priority,
           "parent_type": parent, "parent_id": getattr(t, f"{parent}_id") if parent else None, "due_at": t.due_at,
           "completed_at": t.completed_at, "outcome": t.outcome, "assignee": person(t.assignee), "created_at": t.created_at,
           "overdue": bool(t.due_at and t.due_at < utcnow() and t.status in ("todo", "in_progress", "blocked"))}
    if not p.is_student:
        out["description"], out["outcome_note"] = t.description, t.outcome_note
    return out


def incident_out(db: Session, i: Incident) -> dict:
    reqs = db.scalars(select(Request).where(Request.incident_id == i.id)).all()
    return {"id": i.id, "number": i.number, "ref": f"INC-{i.number}", "title": i.title, "description": i.description,
            "status": i.status, "priority": i.priority, "department": {"id": i.department.id, "name": i.department.name},
            "location_id": i.location_id, "asset_id": i.asset_id, "problem_id": i.problem_id, "owner": person(i.owner),
            "resolved_at": i.resolved_at, "resolution_note": i.resolution_note, "created_at": i.created_at,
            "linked_request_count": len(reqs), "linked_request_ids": [r.id for r in reqs]}


def problem_out(db: Session, pr: Problem) -> dict:
    incs = db.scalars(select(Incident).where(Incident.problem_id == pr.id)).all()
    chs = db.scalars(select(Change).where(Change.problem_id == pr.id)).all()
    return {"id": pr.id, "number": pr.number, "ref": f"PRB-{pr.number}", "title": pr.title, "description": pr.description,
            "status": pr.status, "root_cause": pr.root_cause, "workaround": pr.workaround, "resolution": pr.resolution,
            "department": {"id": pr.department.id, "name": pr.department.name}, "owner": person(pr.owner),
            "resolved_at": pr.resolved_at, "created_at": pr.created_at,
            "incidents": [{"id": i.id, "ref": f"INC-{i.number}", "title": i.title, "status": i.status} for i in incs],
            "changes": [{"id": c.id, "ref": f"CHG-{c.number}", "title": c.title, "status": c.status} for c in chs]}


def change_out(db: Session, c: Change) -> dict:
    ap = db.scalars(select(Approval).where(Approval.change_id == c.id).order_by(Approval.created_at.desc())).first()
    return {"id": c.id, "number": c.number, "ref": f"CHG-{c.number}", "title": c.title, "description": c.description,
            "problem_id": c.problem_id, "department": {"id": c.department.id, "name": c.department.name}, "risk": c.risk,
            "status": c.status, "implementation_plan": c.implementation_plan, "rollback_plan": c.rollback_plan,
            "planned_start": c.planned_start, "planned_end": c.planned_end, "requested_by": c.requested_by,
            "started_at": c.started_at, "completed_at": c.completed_at, "outcome": c.outcome, "outcome_note": c.outcome_note,
            "created_at": c.created_at,
            "approval": ({"id": ap.id, "status": ap.status, "approver_role": ap.approver_role, "reason": ap.reason,
                          "decided_at": ap.decided_at} if ap else None)}


def approval_out(db: Session, a: Approval) -> dict:
    subj: dict = {}
    if a.request_id:
        r = db.get(Request, a.request_id)
        subj = {"type": "request", "id": r.id, "ref": f"REQ-{r.number}",
                "label": r.service.name if r.service else "Request", "department": r.department.name}
    else:
        c = db.get(Change, a.change_id)
        subj = {"type": "change", "id": c.id, "ref": f"CHG-{c.number}", "label": c.title, "risk": c.risk,
                "department": c.department.name}
    return {"id": a.id, "status": a.status, "approver_role": a.approver_role, "subject": subj, "reason": a.reason,
            "decided_at": a.decided_at, "created_at": a.created_at}


def notification_out(n: Notification) -> dict:
    return {"id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "entity_type": n.entity_type,
            "entity_id": n.entity_id, "created_at": n.created_at, "read": n.read_at is not None}


def case_summary(c: Case, p: Principal) -> dict:
    return {"id": c.id, "number": c.number, "ref": f"CASE-{c.number}", "status": c.status, "origin": c.origin,
            "title": c.title, "student": ({"id": c.student.id, "name": c.student.full_name,
                                            "roll_no": c.student.student.roll_no if c.student.student else None}
                                           if not p.is_student else None),
            "owner": person(c.owner), "student_response": c.student_response, "created_at": c.created_at,
            "closed_at": c.closed_at, "outcome": c.outcome}


def case_detail(db: Session, c: Case, p: Principal, now: datetime | None = None) -> dict:
    out = case_summary(c, p)
    out["evidence"] = c.evidence
    out["dismissed_reason"] = c.dismissed_reason
    out["closure_note"] = c.closure_note
    parts = db.scalars(select(CaseParticipant).where(CaseParticipant.case_id == c.id, CaseParticipant.removed_at.is_(None))).all()
    out["participants"] = [{**person(x.user), "is_owner": x.user_id == c.owner_id, "added_at": x.added_at} for x in parts]
    reqs = db.scalars(select(Request).where(Request.case_id == c.id).order_by(Request.created_at)).all()
    ids = detail_ids(db, p, [r.id for r in reqs], now) if not p.is_student else {r.id for r in reqs}
    out["requests"] = [{"id": r.id, "ref": f"REQ-{r.number}", "service": r.service.name if r.service else None,
                        "department": r.department.name, "status": r.status, "created_at": r.created_at,
                        "description": r.description if r.id in ids else None, "restricted": r.id not in ids} for r in reqs]
    if c.status in ("open", "monitoring", "closed"):
        msgs = db.scalars(select(CaseMessage).where(CaseMessage.case_id == c.id).order_by(CaseMessage.created_at)).all()
        out["messages"] = [{"id": m.id, "body": m.body, "created_at": m.created_at,
                            "author": {"id": m.author.id, "name": m.author.full_name, "role": m.author.role.code}} for m in msgs]
        tasks = db.scalars(select(Task).where(Task.case_id == c.id).order_by(Task.created_at)).all()
        out["tasks"] = [task_out(t, p) for t in tasks]
    else:
        out["messages"], out["tasks"] = [], []
    return out

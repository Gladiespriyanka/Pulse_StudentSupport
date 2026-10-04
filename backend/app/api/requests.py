from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Float, cast, func, select
from sqlalchemy.orm import Session

from app.api import serializers as S
from app.config import get_settings
from app.db import get_db
from app.errors import NotFound
from app.models import Request, SlaPolicy
from app.models.base import utcnow
from app.models.enums import SLA_RUNNING
from app.schemas import (
    AssignIn, CommentIn, ConsentShareIn, EscalateIn, FeedbackIn, PeerIn, RequestCreateIn, RerouteIn, SosIn, StatusIn,
    TriagePreviewIn,
)
from app.security import ratelimit
from app.security.deps import Principal, current_principal, require, require_any
from app.services import access, intelligence, requests as R, triage as T
from app.services.audit import audit
from app.services.redact import redact_pii

router = APIRouter(tags=["requests"])


def _full(db: Session, p: Principal, req: Request) -> dict:
    detail = access.request_has_detail(db, p, req.id)
    out = S.request_out(req, p, detail)
    if detail:
        out["events"] = S.events_for(db, req, p)
    return out


@router.post("/triage/preview", summary="Show where free text would be routed and which words matched (nothing is stored)")
def triage_preview(body: TriagePreviewIn, p: Principal = Depends(require("request.create")), db: Session = Depends(get_db)):
    ratelimit.hit(f"triage:{p.id}", 60)
    text, _ = redact_pii(body.description)
    res = T.triage(db, text, service_id=body.service_id)
    arts = intelligence.knowledge_suggest(db, text)
    out = res.explanation()
    out.update({
        "service_name": res.service.name if res.service else None,
        "department_name": res.department.name if res.department else None,
        "location_name": res.location.name if res.location else None,
        "is_sensitive": bool(res.service and res.service.is_sensitive),
        "needs_service_choice": res.department is None,
        "priority": res.priority,
        "articles": [{"id": a.id, "title": a.title, "body": a.body[:400]} for a in arts],
        "crisis_resources": get_settings().crisis_resource_list if res.is_urgent else [],
    })
    return out


@router.post("/requests", status_code=201, summary="Submit a request (catalogue choice or free text routed by rules)")
def create_request(body: RequestCreateIn, p: Principal = Depends(require("request.create")), db: Session = Depends(get_db)):
    ratelimit.hit(f"req-create:{p.id}", 20)
    req, res = R.create_request(db, p, R.NewRequest(body.description, body.service_id, body.location_id, body.consent_share))
    db.commit()
    out = _full(db, p, req)
    out["crisis_resources"] = get_settings().crisis_resource_list if req.is_urgent else []
    return out


@router.post("/requests/sos", status_code=201, summary="Ask for immediate help (critical priority, emergency service)")
def sos(body: SosIn, p: Principal = Depends(require("request.create")), db: Session = Depends(get_db)):
    ratelimit.hit(f"sos:{p.id}", 5)
    req = R.create_sos(db, p, body.location_id, body.note)
    db.commit()
    out = _full(db, p, req)
    out["crisis_resources"] = get_settings().crisis_resource_list
    return out


@router.post("/requests/peer-concern", status_code=201, summary="Anonymous concern about a friend (reporter is not stored)")
def peer(body: PeerIn, p: Principal = Depends(require("request.create")), db: Session = Depends(get_db)):
    ratelimit.hit(f"peer:{p.id}", 5, window=3600)
    R.create_peer_concern(db, p, body.description, body.location_id)
    db.commit()
    return {"ok": True, "message": "Sent anonymously. Thank you for looking out for someone."}


@router.get("/requests", summary="List requests in your scope")
def list_requests(
    p: Principal = Depends(require_any("request.read_own", "request.read_department", "request.read_all_meta")),
    db: Session = Depends(get_db), status: str | None = None, priority: str | None = None, department_id: uuid.UUID | None = None,
    service_id: uuid.UUID | None = None, location_id: uuid.UUID | None = None, assignee_id: uuid.UUID | None = None,
    mine: bool = False, unassigned: bool = False, sla: str | None = Query(None, pattern="^(breached|at_risk)$"),
    open_only: bool = False, sort: str = Query("newest", pattern="^(newest|oldest|sla)$"),
    limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0),
):
    now = utcnow()
    conds = [access.request_scope_criterion(p, now)]
    if status:
        conds.append(Request.status.in_(status.split(",")))
    if open_only:
        conds.append(Request.status.in_(SLA_RUNNING + ("pending_approval",)))
    if priority:
        conds.append(Request.priority == priority)
    if department_id:
        conds.append(Request.department_id == department_id)
    if service_id:
        conds.append(Request.service_id == service_id)
    if location_id:
        conds.append(Request.location_id == location_id)
    if assignee_id:
        conds.append(Request.assignee_id == assignee_id)
    if mine:
        conds.append((Request.requester_id == p.id) if p.is_student else (Request.assignee_id == p.id))
    if unassigned:
        conds.append(Request.assignee_id.is_(None))
        conds.append(Request.status.in_(SLA_RUNNING))
    if sla == "breached":
        conds += [Request.status.in_(SLA_RUNNING), Request.resolution_due_at < now]
    elif sla == "at_risk":
        pct = select(SlaPolicy.at_risk_pct).where(SlaPolicy.id == Request.sla_policy_id).scalar_subquery()
        conds += [Request.status.in_(SLA_RUNNING), Request.resolution_due_at >= now,
                  Request.sla_started_at + (Request.resolution_due_at - Request.sla_started_at) * (cast(pct, Float) / 100.0) <= now]
    total = db.scalar(select(func.count()).select_from(Request).where(*conds)) or 0
    order = {"newest": Request.created_at.desc(), "oldest": Request.created_at.asc(),
             "sla": Request.resolution_due_at.asc().nulls_last()}[sort]
    rows = db.scalars(select(Request).where(*conds).order_by(order, Request.number.desc()).limit(limit).offset(offset)).unique().all()
    dids = S.detail_ids(db, p, [r.id for r in rows], now)
    return {"items": [S.request_out(r, p, r.id in dids, now) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/requests/{request_id}", summary="Request detail (text and timeline only if you are entitled to them)")
def get_request(request_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    req = R.get_request(db, p, request_id)
    out = _full(db, p, req)
    if not out["restricted"] and req.requester_id != p.id:
        bg = p.department_id != req.department_id
        audit(db, "request.viewed", actor=p, entity_type="request", entity_id=req.id, subject=req.requester_id,
              details={"break_glass": True} if bg else None)
        db.commit()
    return out


@router.post("/requests/{request_id}/status")
def change_status(request_id: uuid.UUID, body: StatusIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    req = R.get_request(db, p, request_id)
    R.transition(db, p, req, body.status, body.note)
    db.commit()
    return _full(db, p, req)


@router.post("/requests/{request_id}/assign")
def assign(request_id: uuid.UUID, body: AssignIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    req = R.get_request(db, p, request_id)
    R.assign(db, p, req, body.assignee_id)
    db.commit()
    return _full(db, p, req)


@router.post("/requests/{request_id}/reroute")
def reroute(request_id: uuid.UUID, body: RerouteIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    req = R.get_request(db, p, request_id)
    R.reroute(db, p, req, body.service_id)
    db.commit()
    return _full(db, p, req)


@router.post("/requests/{request_id}/escalate")
def escalate(request_id: uuid.UUID, body: EscalateIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    req = R.get_request(db, p, request_id)
    R.escalate(db, p, req, body.note)
    db.commit()
    return _full(db, p, req)


@router.post("/requests/{request_id}/comments", status_code=201)
def comment(request_id: uuid.UUID, body: CommentIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    ratelimit.hit(f"comment:{p.id}", 30)
    req = R.get_request(db, p, request_id)
    R.add_comment(db, p, req, body.text, body.internal)
    db.commit()
    return _full(db, p, req)


@router.post("/requests/{request_id}/consent")
def consent(request_id: uuid.UUID, body: ConsentShareIn, p: Principal = Depends(require("request.read_own")), db: Session = Depends(get_db)):
    req = R.get_request(db, p, request_id)
    R.set_consent_share(db, p, req, body.share)
    db.commit()
    return _full(db, p, req)


@router.post("/requests/{request_id}/feedback", status_code=201)
def feedback(request_id: uuid.UUID, body: FeedbackIn, p: Principal = Depends(require("feedback.create")), db: Session = Depends(get_db)):
    req = R.get_request(db, p, request_id)
    R.give_feedback(db, p, req, body.helpful, body.comment)
    db.commit()
    return _full(db, p, req)

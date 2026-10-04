from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import serializers as S
from app.db import get_db
from app.errors import NotFound
from app.models import Change, Incident, Problem
from app.schemas import (
    ChangeCompleteIn, ChangeCreateIn, ChangePatchIn, IncidentCreateIn, IncidentPatchIn, IncidentProblemIn, LinkRequestsIn,
    ProblemPatchIn, In,
)
from app.security.deps import Principal, require
from app.services import intelligence, itsm as X
from app.services.audit import audit
from pydantic import Field

router = APIRouter(tags=["itsm"])


def _page(db, model, rows_fn, total_q, limit, offset):
    return {"items": rows_fn(), "total": db.scalar(total_q) or 0, "limit": limit, "offset": offset}


# ---- intelligence (suggestions only; humans decide) ---------------------------------------------------
@router.get("/intelligence/incident-clusters", tags=["intelligence"], summary="Locations where open requests are piling up (rule-based)")
def clusters(p: Principal = Depends(require("incident.read")), db: Session = Depends(get_db)):
    return intelligence.incident_clusters(db, p)


@router.get("/intelligence/problem-suggestions", tags=["intelligence"], summary="Repeat incidents without a problem record")
def problem_suggestions(p: Principal = Depends(require("problem.read")), db: Session = Depends(get_db)):
    return intelligence.problem_suggestions(db, p)


# ---- incidents -----------------------------------------------------------------------------------------
@router.get("/incidents")
def list_incidents(p: Principal = Depends(require("incident.read")), db: Session = Depends(get_db), status: str | None = None,
                   open_only: bool = False, department_id: uuid.UUID | None = None, limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0)):
    c = []
    if status:
        c.append(Incident.status.in_(status.split(",")))
    if open_only:
        c.append(Incident.status.in_(["open", "investigating", "mitigated"]))
    if department_id:
        c.append(Incident.department_id == department_id)
    rows = db.scalars(select(Incident).where(*c).order_by(Incident.created_at.desc()).limit(limit).offset(offset)).unique().all()
    return {"items": [S.incident_out(db, i) for i in rows], "total": db.scalar(select(func.count()).select_from(Incident).where(*c)) or 0,
            "limit": limit, "offset": offset}


@router.post("/incidents", status_code=201)
def create_incident(body: IncidentCreateIn, p: Principal = Depends(require("incident.manage")), db: Session = Depends(get_db)):
    inc = X.create_incident(db, p, title=body.title, description=body.description, department_id=body.department_id,
                            location_id=body.location_id, asset_id=body.asset_id, priority=body.priority, request_ids=body.request_ids)
    db.commit()
    return S.incident_out(db, inc)


def _inc(db, iid):
    i = db.get(Incident, iid)
    if not i:
        raise NotFound("Incident not found")
    return i


@router.get("/incidents/{incident_id}")
def get_incident(incident_id: uuid.UUID, p: Principal = Depends(require("incident.read")), db: Session = Depends(get_db)):
    return S.incident_out(db, _inc(db, incident_id))


@router.patch("/incidents/{incident_id}")
def patch_incident(incident_id: uuid.UUID, body: IncidentPatchIn, p: Principal = Depends(require("incident.manage")), db: Session = Depends(get_db)):
    inc = _inc(db, incident_id)
    X.update_incident(db, p, inc, status=body.status, note=body.note, owner_id=body.owner_id, set_owner="owner_id" in body.model_fields_set,
                      resolve_linked=body.resolve_linked_requests, force=body.force)
    db.commit()
    return S.incident_out(db, inc)


@router.post("/incidents/{incident_id}/requests")
def link_requests(incident_id: uuid.UUID, body: LinkRequestsIn, p: Principal = Depends(require("incident.manage")), db: Session = Depends(get_db)):
    inc = _inc(db, incident_id)
    X.link_requests(db, p, inc, body.request_ids)
    db.commit()
    return S.incident_out(db, inc)


@router.post("/incidents/{incident_id}/problem", status_code=201, summary="Incident -> Problem: open or link a problem")
def incident_problem(incident_id: uuid.UUID, body: IncidentProblemIn, p: Principal = Depends(require("problem.manage")), db: Session = Depends(get_db)):
    inc = _inc(db, incident_id)
    pr = X.link_problem(db, p, inc, problem_id=body.problem_id, title=body.title)
    db.commit()
    return S.problem_out(db, pr)


# ---- problems --------------------------------------------------------------------------------------------
@router.get("/problems")
def list_problems(p: Principal = Depends(require("problem.read")), db: Session = Depends(get_db), status: str | None = None,
                  open_only: bool = False, limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0)):
    c = []
    if status:
        c.append(Problem.status.in_(status.split(",")))
    if open_only:
        c.append(Problem.status.in_(["open", "investigating", "known_error"]))
    rows = db.scalars(select(Problem).where(*c).order_by(Problem.created_at.desc()).limit(limit).offset(offset)).unique().all()
    return {"items": [S.problem_out(db, x) for x in rows], "total": db.scalar(select(func.count()).select_from(Problem).where(*c)) or 0,
            "limit": limit, "offset": offset}


def _prob(db, pid):
    x = db.get(Problem, pid)
    if not x:
        raise NotFound("Problem not found")
    return x


@router.get("/problems/{problem_id}")
def get_problem(problem_id: uuid.UUID, p: Principal = Depends(require("problem.read")), db: Session = Depends(get_db)):
    return S.problem_out(db, _prob(db, problem_id))


@router.patch("/problems/{problem_id}")
def patch_problem(problem_id: uuid.UUID, body: ProblemPatchIn, p: Principal = Depends(require("problem.manage")), db: Session = Depends(get_db)):
    pr = _prob(db, problem_id)
    X.update_problem(db, p, pr, status=body.status, root_cause=body.root_cause, workaround=body.workaround, resolution=body.resolution,
                     owner_id=body.owner_id, set_owner="owner_id" in body.model_fields_set)
    db.commit()
    return S.problem_out(db, pr)


# ---- changes --------------------------------------------------------------------------------------------
@router.get("/changes")
def list_changes(p: Principal = Depends(require("change.read")), db: Session = Depends(get_db), status: str | None = None,
                 limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0)):
    c = [Change.status.in_(status.split(","))] if status else []
    rows = db.scalars(select(Change).where(*c).order_by(Change.created_at.desc()).limit(limit).offset(offset)).unique().all()
    return {"items": [S.change_out(db, x) for x in rows], "total": db.scalar(select(func.count()).select_from(Change).where(*c)) or 0,
            "limit": limit, "offset": offset}


def _chg(db, cid):
    x = db.get(Change, cid)
    if not x:
        raise NotFound("Change not found")
    return x


@router.post("/changes", status_code=201, summary="Problem -> Change: raise a change (starts as a draft)")
def create_change(body: ChangeCreateIn, p: Principal = Depends(require("change.manage")), db: Session = Depends(get_db)):
    ch = X.create_change(db, p, **body.model_dump())
    db.commit()
    return S.change_out(db, ch)


@router.get("/changes/{change_id}")
def get_change(change_id: uuid.UUID, p: Principal = Depends(require("change.read")), db: Session = Depends(get_db)):
    return S.change_out(db, _chg(db, change_id))


@router.patch("/changes/{change_id}", summary="Edit a draft change")
def patch_change(change_id: uuid.UUID, body: ChangePatchIn, p: Principal = Depends(require("change.manage")), db: Session = Depends(get_db)):
    from app.errors import Conflict

    ch = _chg(db, change_id)
    X._require_change_manager(p, ch)
    if ch.status != "draft":
        raise Conflict("Only a draft can be edited. Cancel and raise a new change instead.", code="invalid_state")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(ch, k, v)
    audit(db, "change.updated", actor=p, entity_type="change", entity_id=ch.id)
    db.commit()
    return S.change_out(db, ch)


@router.post("/changes/{change_id}/submit", summary="Send for approval")
def submit(change_id: uuid.UUID, p: Principal = Depends(require("change.manage")), db: Session = Depends(get_db)):
    ch = X.submit_change(db, p, _chg(db, change_id))
    db.commit()
    return S.change_out(db, ch)


@router.post("/changes/{change_id}/start")
def start(change_id: uuid.UUID, p: Principal = Depends(require("change.manage")), db: Session = Depends(get_db)):
    ch = X.start_change(db, p, _chg(db, change_id))
    db.commit()
    return S.change_out(db, ch)


@router.post("/changes/{change_id}/complete")
def complete(change_id: uuid.UUID, body: ChangeCompleteIn, p: Principal = Depends(require("change.manage")), db: Session = Depends(get_db)):
    ch = X.complete_change(db, p, _chg(db, change_id), body.outcome, body.note)
    db.commit()
    return S.change_out(db, ch)


@router.post("/changes/{change_id}/cancel")
def cancel(change_id: uuid.UUID, p: Principal = Depends(require("change.manage")), db: Session = Depends(get_db)):
    ch = X.cancel_change(db, p, _chg(db, change_id))
    db.commit()
    return S.change_out(db, ch)


class ProblemCreate(In):
    title: str = Field(min_length=3, max_length=200)
    description: str | None = Field(None, max_length=4000)
    department_id: uuid.UUID | None = None


@router.post("/problems", status_code=201, summary="Open a problem directly (usually opened from an incident)")
def create_problem(body: ProblemCreate, p: Principal = Depends(require("problem.manage")), db: Session = Depends(get_db)):
    from app.errors import Forbidden, Invalid

    dept = body.department_id or p.department_id
    if not dept:
        raise Invalid("Choose the owning department.", code="department_required")
    if p.role != "command" and p.department_id != dept:
        raise Forbidden("You can only open problems for your own department.")
    pr = Problem(title=body.title, description=body.description, department_id=dept, created_by=p.id,
                 owner_id=p.id if p.department_id == dept else None)
    db.add(pr)
    db.flush()
    audit(db, "problem.created", actor=p, entity_type="problem", entity_id=pr.id)
    db.commit()
    db.refresh(pr)
    return S.problem_out(db, pr)

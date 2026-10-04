from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import serializers as S
from app.db import get_db
from app.errors import NotFound
from app.models import Approval, Task
from app.schemas import DecisionIn, FeedbackIn, TaskCreateIn, TaskPatchIn
from app.security.deps import Principal, current_principal, require, require_any
from app.services import access, approvals as A, tasks as T

router = APIRouter(tags=["tasks"])


@router.get("/tasks", summary="Tasks in your scope")
def list_tasks(p: Principal = Depends(require_any("task.read_own", "task.manage", "case.read")), db: Session = Depends(get_db),
               mine: bool = False, status: str | None = None, parent_type: str | None = None, parent_id: uuid.UUID | None = None,
               open_only: bool = False, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    conds = [access.task_scope_criterion(p)]
    if mine:
        conds.append(Task.assignee_id == p.id)
    if status:
        conds.append(Task.status.in_(status.split(",")))
    if open_only:
        conds.append(Task.status.in_(["todo", "in_progress", "blocked"]))
    if parent_type and parent_id:
        if parent_type not in T.PARENTS and parent_type != "case":
            return {"items": [], "total": 0, "limit": limit, "offset": offset}
        conds.append(getattr(Task, f"{parent_type}_id") == parent_id)
    total = db.scalar(select(func.count()).select_from(Task).where(*conds)) or 0
    rows = db.scalars(select(Task).where(*conds).order_by(Task.due_at.asc().nulls_last(), Task.number.desc()).limit(limit).offset(offset)).unique().all()
    return {"items": [S.task_out(t, p) for t in rows], "total": total, "limit": limit, "offset": offset}


def _get(db: Session, p: Principal, task_id: uuid.UUID) -> Task:
    t = db.scalar(select(Task).where(Task.id == task_id, access.task_scope_criterion(p)))
    if not t:
        raise NotFound("Task not found")
    return t


@router.post("/tasks", status_code=201)
def create_task(body: TaskCreateIn, p: Principal = Depends(require("task.manage")), db: Session = Depends(get_db)):
    t = T.create(db, p, body.parent_type, body.parent_id, body.title, body.assignee_id, body.due_at, body.description, body.priority)
    db.commit()
    return S.task_out(t, p)


@router.patch("/tasks/{task_id}")
def patch_task(task_id: uuid.UUID, body: TaskPatchIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    t = _get(db, p, task_id)
    data = body.model_dump(exclude_unset=True)
    T.update(db, p, t, status=data.get("status"), outcome=data.get("outcome"), outcome_note=data.get("outcome_note"),
             assignee_id=data.get("assignee_id"), set_assignee="assignee_id" in data, due_at=data.get("due_at"))
    db.commit()
    return S.task_out(t, p)


@router.post("/tasks/{task_id}/feedback", status_code=201)
def task_feedback(task_id: uuid.UUID, body: FeedbackIn, p: Principal = Depends(require("feedback.create")), db: Session = Depends(get_db)):
    t = _get(db, p, task_id)
    T.task_feedback(db, p, t, body.helpful, body.comment)
    db.commit()
    return S.task_out(t, p)


# ---- approvals -----------------------------------------------------------------------------------
@router.get("/approvals", summary="Approvals waiting for you (or recent decisions)")
def list_approvals(p: Principal = Depends(require("approval.decide")), db: Session = Depends(get_db), history: bool = False):
    if history:
        q = select(Approval).where(Approval.decided_by == p.id).order_by(Approval.decided_at.desc()).limit(50)
    else:
        q = A.pending_query(p)
    return [S.approval_out(db, a) for a in db.scalars(q)]


@router.post("/approvals/{approval_id}/decision")
def decide(approval_id: uuid.UUID, body: DecisionIn, p: Principal = Depends(require("approval.decide")), db: Session = Depends(get_db)):
    a = db.get(Approval, approval_id)
    if not a or not A.eligible(p, a):
        raise NotFound("Approval not found")  # do not reveal approvals the caller is not entitled to
    A.decide(db, p, a, body.approve, body.reason)
    db.commit()
    return S.approval_out(db, a)

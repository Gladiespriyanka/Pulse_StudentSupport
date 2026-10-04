"""Notifications, knowledge base, reports."""
from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import serializers as S
from app.db import get_db
from app.errors import Forbidden, NotFound
from app.models import KnowledgeArticle, Notification
from app.models.base import utcnow
from app.schemas import KnowledgeIn, KnowledgePatch, NotificationsReadIn
from app.security.deps import Principal, require
from app.services import reports as RP
from app.services.audit import audit

router = APIRouter()
notif = APIRouter(prefix="/notifications", tags=["notifications"])
kb = APIRouter(prefix="/knowledge", tags=["knowledge"])
rep = APIRouter(prefix="/reports", tags=["reports"])


# ---- notifications ----------------------------------------------------------------------------------
@notif.get("")
def list_notifications(p: Principal = Depends(require("notification.read_own")), db: Session = Depends(get_db),
                       unread_only: bool = False, limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0)):
    c = [Notification.user_id == p.id] + ([Notification.read_at.is_(None)] if unread_only else [])
    rows = db.scalars(select(Notification).where(*c).order_by(Notification.created_at.desc()).limit(limit).offset(offset)).all()
    unread = db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == p.id, Notification.read_at.is_(None)))
    return {"items": [S.notification_out(n) for n in rows], "unread": unread or 0, "limit": limit, "offset": offset}


@notif.get("/unread-count")
def unread(p: Principal = Depends(require("notification.read_own")), db: Session = Depends(get_db)):
    return {"unread": db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == p.id, Notification.read_at.is_(None))) or 0}


@notif.post("/read", summary="Mark some (ids) or all notifications as read")
def mark_read(body: NotificationsReadIn, p: Principal = Depends(require("notification.read_own")), db: Session = Depends(get_db)):
    q = select(Notification).where(Notification.user_id == p.id, Notification.read_at.is_(None))
    if body.ids is not None:
        q = q.where(Notification.id.in_(body.ids))
    n = 0
    for x in db.scalars(q):
        x.read_at = utcnow()
        n += 1
    db.commit()
    return {"marked": n}


# ---- knowledge ----------------------------------------------------------------------------------------
def _kb_out(a: KnowledgeArticle) -> dict:
    return {"id": a.id, "title": a.title, "body": a.body, "department_id": a.department_id, "audience": a.audience,
            "status": a.status, "source_problem_id": a.source_problem_id, "published_at": a.published_at, "updated_at": a.updated_at}


def _kb_visible(p: Principal):
    if p.can("knowledge.manage"):
        return KnowledgeArticle.id.is_not(None)
    if p.is_student:
        return (KnowledgeArticle.status == "published") & (KnowledgeArticle.audience == "public")
    return KnowledgeArticle.status == "published"


@kb.get("")
def search_kb(p: Principal = Depends(require("knowledge.read")), db: Session = Depends(get_db), q: str | None = None,
              status: str | None = None, limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0)):
    conds = [_kb_visible(p)]
    if status:
        conds.append(KnowledgeArticle.status == status)
    order = [KnowledgeArticle.updated_at.desc()]
    if q and q.strip():
        tsq = func.websearch_to_tsquery("english", q.strip()[:200])
        conds.append(KnowledgeArticle.search_vector.op("@@")(tsq))
        order = [func.ts_rank(KnowledgeArticle.search_vector, tsq).desc()]
    rows = db.scalars(select(KnowledgeArticle).where(*conds).order_by(*order).limit(limit).offset(offset)).all()
    return {"items": [_kb_out(a) for a in rows], "total": db.scalar(select(func.count()).select_from(KnowledgeArticle).where(*conds)) or 0,
            "limit": limit, "offset": offset}


@kb.get("/{article_id}")
def get_kb(article_id: uuid.UUID, p: Principal = Depends(require("knowledge.read")), db: Session = Depends(get_db)):
    a = db.scalar(select(KnowledgeArticle).where(KnowledgeArticle.id == article_id, _kb_visible(p)))
    if not a:
        raise NotFound("Article not found")
    return _kb_out(a)


def _kb_guard(p: Principal, dept_id):
    if p.role == "dept_head" and dept_id not in (None, p.department_id):
        raise Forbidden("You can only manage articles for your own department or campus-wide articles.")


@kb.post("", status_code=201)
def create_kb(body: KnowledgeIn, p: Principal = Depends(require("knowledge.manage")), db: Session = Depends(get_db)):
    _kb_guard(p, body.department_id)
    a = KnowledgeArticle(**body.model_dump(), created_by=p.id, updated_by=p.id,
                         published_at=utcnow() if body.status == "published" else None)
    db.add(a)
    db.flush()
    audit(db, "knowledge.created", actor=p, entity_type="knowledge", entity_id=a.id)
    db.commit()
    db.refresh(a)
    return _kb_out(a)


@kb.patch("/{article_id}")
def patch_kb(article_id: uuid.UUID, body: KnowledgePatch, p: Principal = Depends(require("knowledge.manage")), db: Session = Depends(get_db)):
    a = db.get(KnowledgeArticle, article_id)
    if not a:
        raise NotFound("Article not found")
    _kb_guard(p, a.department_id)
    data = body.model_dump(exclude_unset=True)
    _kb_guard(p, data.get("department_id", a.department_id))
    for k, v in data.items():
        setattr(a, k, v)
    if data.get("status") == "published" and a.published_at is None:
        a.published_at = utcnow()
    a.updated_by = p.id
    audit(db, "knowledge.updated", actor=p, entity_type="knowledge", entity_id=a.id, details={"fields": sorted(data)})
    db.commit()
    db.refresh(a)
    return _kb_out(a)


# ---- reports ------------------------------------------------------------------------------------------
@rep.get("/summary", summary="Operational summary (department roles only ever see their own department)")
def summary(p: Principal = Depends(require("report.read")), db: Session = Depends(get_db), date_from: datetime | None = None,
            date_to: datetime | None = None, department_id: uuid.UUID | None = None):
    return RP.summary(db, p, date_from, date_to, department_id)


@rep.get("/heatmap", summary="Location x category x time-of-day counts (small sensitive cells masked)")
def heatmap(p: Principal = Depends(require("aggregate.read")), db: Session = Depends(get_db), date_from: datetime | None = None,
            date_to: datetime | None = None):
    return RP.heatmap(db, p, date_from, date_to)


@rep.get("/briefing", summary="Weekly briefing generated from real aggregates")
def briefing(p: Principal = Depends(require("aggregate.read")), db: Session = Depends(get_db)):
    return RP.weekly_briefing(db, p)


def safe_cell(v):
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@rep.get("/summary.csv", summary="Department table as CSV (aggregates only)")
def summary_csv(p: Principal = Depends(require("report.read")), db: Session = Depends(get_db), date_from: datetime | None = None,
                date_to: datetime | None = None, department_id: uuid.UUID | None = None):
    s = RP.summary(db, p, date_from, date_to, department_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["department", "created", "resolved", "open", "breached_open"])
    for r in s["by_department"]:
        w.writerow([safe_cell(r["department"]), r["created"], r["resolved"], r["open"], r["breached"]])
    audit(db, "report.exported", actor=p, details={"report": "summary"})
    db.commit()
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="pulse-summary.csv"'})

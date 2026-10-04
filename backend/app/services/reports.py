"""Operational reports and k-anonymised aggregates computed from real rows."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import Float, case, cast, extract, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    Case, Change, Department, Feedback, Incident, Location, Problem, Request, Service, Task,
)
from app.models.base import utcnow
from app.models.enums import SLA_RUNNING
from app.security.deps import Principal
from app.services import intelligence

BUCKETS = {"day": (6, 18), "evening": (18, 22)}  # everything else = night


def _bucket(hour: float) -> str:
    h = int(hour)
    return "day" if 6 <= h < 18 else "evening" if 18 <= h < 22 else "night"


def _scope_dept(p: Principal, department_id):
    if p.role in ("command", "admin"):
        return department_id
    return p.department_id  # department roles only ever see their own department


def _avg(x) -> float | None:
    return round(float(x), 1) if x is not None else None


def summary(db: Session, p: Principal, date_from: datetime | None, date_to: datetime | None, department_id=None, now: datetime | None = None) -> dict:
    now = now or utcnow()
    date_to = date_to or now
    date_from = date_from or (date_to - timedelta(days=30))
    dept = _scope_dept(p, department_id)
    k = get_settings().aggregate_min_cell
    base = [Request.created_at >= date_from, Request.created_at <= date_to]
    if dept:
        base.append(Request.department_id == dept)

    created = db.scalar(select(func.count()).select_from(Request).where(*base)) or 0
    res_f = [Request.resolved_at >= date_from, Request.resolved_at <= date_to] + ([Request.department_id == dept] if dept else [])
    resolved = db.scalar(select(func.count()).select_from(Request).where(*res_f)) or 0
    avg_first = db.scalar(select(func.avg(extract("epoch", Request.first_response_at - Request.created_at) / 60)).where(*base, Request.first_response_at.is_not(None)))
    avg_res = db.scalar(select(func.avg(extract("epoch", Request.resolved_at - Request.created_at) / 3600)).where(*res_f))
    due_rows = db.execute(select(func.count(), func.count().filter(Request.resolved_at <= Request.resolution_due_at))
                          .where(*res_f, Request.resolution_due_at.is_not(None))).one()
    resp_rows = db.execute(select(func.count(), func.count().filter(Request.first_response_at <= Request.response_due_at))
                           .where(*base, Request.response_due_at.is_not(None), Request.first_response_at.is_not(None))).one()
    open_f = [Request.status.in_(SLA_RUNNING)] + ([Request.department_id == dept] if dept else [])
    open_now = dict(db.execute(select(Request.status, func.count()).where(*open_f).group_by(Request.status)).all())
    breached_open = db.scalar(select(func.count()).select_from(Request).where(*open_f, Request.resolution_due_at < now)) or 0
    escalated = db.scalar(select(func.count()).select_from(Request).where(*base, Request.escalation_level > 0)) or 0
    by_priority = dict(db.execute(select(Request.priority, func.count()).where(*base).group_by(Request.priority)).all())

    fb = db.execute(select(func.count(), func.count().filter(Feedback.helpful)).select_from(Feedback)
                    .join(Request, Request.id == Feedback.request_id)
                    .where(Feedback.request_id.is_not(None), *base)).one()

    by_dept = []
    drows = db.execute(
        select(Department.name, func.count(Request.id), func.count().filter(Request.resolved_at.is_not(None)),
               func.count().filter(Request.status.in_(SLA_RUNNING)),
               func.count().filter(Request.status.in_(SLA_RUNNING), Request.resolution_due_at < now))
        .join(Request, Request.department_id == Department.id).where(*base).group_by(Department.name).order_by(Department.name)).all()
    for n, c, r, o, b in drows:
        by_dept.append({"department": n, "created": c, "resolved": r, "open": o, "breached": b})

    out = {
        "period": {"from": date_from, "to": date_to}, "scope": "department" if dept else "campus",
        "requests": {"created": created, "resolved": resolved, "open_by_status": open_now, "open_total": sum(open_now.values()),
                     "breached_open": breached_open, "escalated": escalated, "by_priority": by_priority},
        "timings": {"avg_first_response_minutes": _avg(avg_first), "avg_resolution_hours": _avg(avg_res)},
        "sla": {"resolution_compliance_pct": round(100 * due_rows[1] / due_rows[0], 1) if due_rows[0] else None, "resolution_sample": due_rows[0],
                "response_compliance_pct": round(100 * resp_rows[1] / resp_rows[0], 1) if resp_rows[0] else None, "response_sample": resp_rows[0]},
        "feedback": {"responses": fb[0], "helpful": fb[1], "helpful_pct": round(100 * fb[1] / fb[0], 1) if fb[0] else None},
        "by_department": by_dept,
        "k_min_cell": k,
    }
    if p.can("aggregate.read") and not dept:
        out["itsm"] = {
            "incidents_open": db.scalar(select(func.count()).select_from(Incident).where(Incident.status.in_(["open", "investigating", "mitigated"]))),
            "problems_open": db.scalar(select(func.count()).select_from(Problem).where(Problem.status.in_(["open", "investigating", "known_error"]))),
            "changes_in_flight": db.scalar(select(func.count()).select_from(Change).where(Change.status.in_(["awaiting_approval", "approved", "implementing"]))),
            "changes_successful": db.scalar(select(func.count()).select_from(Change).where(Change.outcome == "successful")),
            "changes_failed": db.scalar(select(func.count()).select_from(Change).where(Change.outcome == "failed")),
        }
        out["care"] = _care(db, k)
    return out


def _care(db: Session, k: int) -> dict:
    """Case-rule quality is measured from human decisions; intervention outcomes are masked below k."""
    st = dict(db.execute(select(Case.status, func.count()).where(Case.origin == "rule").group_by(Case.status)).all())
    confirmed = sum(v for s, v in st.items() if s in ("open", "monitoring", "closed"))
    dismissed = st.get("dismissed", 0)
    decided = confirmed + dismissed
    reasons = dict(db.execute(select(Case.dismissed_reason, func.count()).where(Case.status == "dismissed").group_by(Case.dismissed_reason)).all())
    out = dict(db.execute(select(Task.outcome, func.count()).where(Task.case_id.is_not(None), Task.outcome.is_not(None)).group_by(Task.outcome)).all())
    total_out = sum(out.values())
    return {
        "rule_suggestions": {"suggested_pending": st.get("suggested", 0), "confirmed": confirmed, "dismissed": dismissed,
                             "confirmation_rate_pct": round(100 * confirmed / decided, 1) if decided else None,
                             "decided": decided, "dismiss_reasons": reasons},
        "interventions": ({"masked": True, "reason": f"fewer than {k} recorded outcomes"} if total_out < k else
                          {"masked": False, "total": total_out, "outcomes": out}),
    }


def heatmap(db: Session, p: Principal, date_from: datetime | None, date_to: datetime | None, now: datetime | None = None) -> dict:
    """Location x service category x time of day (campus timezone). Cells for sensitive categories below k are masked."""
    now = now or utcnow()
    date_to = date_to or now
    date_from = date_from or (date_to - timedelta(days=30))
    s = get_settings()
    local = func.timezone(s.campus_timezone, Request.created_at)
    cat = func.coalesce(Service.category, "unrouted")
    rows = db.execute(
        select(Location.name, cat, extract("hour", local), Service.is_sensitive, func.count())
        .join(Location, Location.id == Request.location_id).outerjoin(Service, Service.id == Request.service_id)
        .where(Request.created_at >= date_from, Request.created_at <= date_to)
        .group_by(Location.name, cat, extract("hour", local), Service.is_sensitive)).all()
    cells: dict = {}
    for loc, c, hour, sens, n in rows:
        key = (loc, c, _bucket(hour))
        d = cells.setdefault(key, {"count": 0, "sensitive": False})
        d["count"] += n
        d["sensitive"] = d["sensitive"] or bool(sens)
    out, masked = [], 0
    for (loc, c, b), d in sorted(cells.items()):
        hide = d["sensitive"] and d["count"] < s.aggregate_min_cell
        masked += hide
        out.append({"location": loc, "category": c, "time_of_day": b, "count": None if hide else d["count"], "masked": hide})
    return {"period": {"from": date_from, "to": date_to}, "timezone": s.campus_timezone, "k_min_cell": s.aggregate_min_cell,
            "cells": out, "masked_cells": masked,
            "note": f"Counts for sensitive services below {s.aggregate_min_cell} are hidden to protect individuals."}


def weekly_briefing(db: Session, p: Principal, now: datetime | None = None) -> dict:
    now = now or utcnow()
    summ = summary(db, p, now - timedelta(days=7), now, None, now)
    clusters = intelligence.incident_clusters(db, p, now)
    r = summ["requests"]
    lines = [
        f"PULSE weekly briefing - {now:%d %b %Y}",
        f"Requests: {r['created']} created, {r['resolved']} resolved, {r['open_total']} open ({r['breached_open']} past SLA).",
        f"Average first response: {summ['timings']['avg_first_response_minutes'] if summ['timings']['avg_first_response_minutes'] is not None else 'n/a'} min; "
        f"average resolution: {summ['timings']['avg_resolution_hours'] if summ['timings']['avg_resolution_hours'] is not None else 'n/a'} h.",
        "SLA compliance (resolution): " + (f"{summ['sla']['resolution_compliance_pct']}% of {summ['sla']['resolution_sample']}" if summ['sla']['resolution_sample'] else "n/a"),
        "Student feedback: " + (f"{summ['feedback']['helpful_pct']}% helpful of {summ['feedback']['responses']}" if summ['feedback']['responses'] else "none yet"),
        "Locations with request clusters: " + ("; ".join(f"{c['location']['name']} ({c['request_count']})" for c in clusters) or "none"),
        "Aggregates only. No individual is named.",
    ]
    return {"generated_at": now, "text": "\n".join(lines), "summary": summ, "clusters": clusters}

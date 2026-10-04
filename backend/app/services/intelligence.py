"""Explainable, rule-based intelligence over real data. No invented scores.

  * incident_clusters   - open requests piling up at the same location (>= N within D days)
  * problem_suggestions - repeat incidents on the same asset/location without a problem record
  * knowledge_suggest   - published public articles matching the words of a draft request (Postgres full-text)
Each suggestion states the rule and the counts that triggered it. Humans decide what to do with it.
ML is intentionally not used: with campus-scale data volumes a keyword/threshold rule is auditable and
sufficient; see docs/INTELLIGENCE.md for the criteria that would justify a model.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Incident, KnowledgeArticle, Request
from app.models.base import utcnow
from app.models.enums import SLA_RUNNING
from app.security.deps import Principal
from app.services import access


def incident_clusters(db: Session, p: Principal, now: datetime | None = None) -> list[dict]:
    now = now or utcnow()
    s = get_settings()
    rows = db.scalars(select(Request).where(
        Request.status.in_(SLA_RUNNING), Request.incident_id.is_(None), Request.location_id.is_not(None),
        Request.created_at >= now - timedelta(days=s.cluster_window_days), access.request_scope_criterion(p, now))).all()
    groups: dict = {}
    for r in rows:
        groups.setdefault(r.location_id, []).append(r)
    out = []
    for lid, reqs in groups.items():
        if len(reqs) < s.cluster_min_requests:
            continue
        out.append({
            "location": {"id": str(lid), "name": reqs[0].location.name},
            "request_count": len(reqs),
            "request_ids": [str(r.id) for r in reqs],
            "request_numbers": sorted(r.number for r in reqs),
            "services": sorted({r.service.name if r.service else "Unrouted" for r in reqs}),
            "departments": sorted({r.department.name for r in reqs}),
            "oldest_open_days": max((now - r.created_at).days for r in reqs),
            "rule": f"{s.cluster_min_requests}+ open, unlinked requests at one location within {s.cluster_window_days} days",
        })
    return sorted(out, key=lambda c: (-c["request_count"], c["location"]["name"]))


def problem_suggestions(db: Session, p: Principal, now: datetime | None = None) -> list[dict]:
    now = now or utcnow()
    s = get_settings()
    rows = db.scalars(select(Incident).where(Incident.problem_id.is_(None),
                                             Incident.created_at >= now - timedelta(days=s.problem_window_days))).all()
    groups: dict = {}
    for i in rows:
        key = ("asset", i.asset_id) if i.asset_id else (("location", i.location_id) if i.location_id else None)
        if key:
            groups.setdefault(key, []).append(i)
    out = []
    for (kind, kid), incs in groups.items():
        if len(incs) < s.problem_min_incidents:
            continue
        out.append({"basis": kind, "ref_id": str(kid), "incident_count": len(incs),
                    "incident_ids": [str(i.id) for i in incs], "incident_numbers": sorted(i.number for i in incs),
                    "department_id": str(incs[0].department_id),
                    "rule": f"{s.problem_min_incidents}+ incidents on the same {kind} within {s.problem_window_days} days with no problem record"})
    return sorted(out, key=lambda c: -c["incident_count"])


def knowledge_suggest(db: Session, text_in: str, limit: int = 3) -> list[KnowledgeArticle]:
    words = list(dict.fromkeys(w for w in re.findall(r"[a-z0-9]+", text_in.lower()) if len(w) >= 4))[:10]
    if not words:
        return []
    q = func.to_tsquery("english", " | ".join(words))
    stmt = (select(KnowledgeArticle).where(KnowledgeArticle.status == "published", KnowledgeArticle.audience == "public",
                                           KnowledgeArticle.search_vector.op("@@")(q))
            .order_by(func.ts_rank(KnowledgeArticle.search_vector, q).desc()).limit(limit))
    return list(db.scalars(stmt))

"""Explainable routing. No scores are invented: a request is routed by matching admin-managed
keyword rules, and the response lists exactly which terms matched which service."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Asset, Department, Location, Service, TriageRule
from app.models.enums import AssetStatus, Priority


def _rx(term: str) -> re.Pattern:
    # word-start match so stems work: "harass" matches "harassment"
    return re.compile(r"(?<![a-z0-9])" + re.escape(term.lower()))


@dataclass
class TriageResult:
    service: Service | None = None
    department: Department | None = None
    location: Location | None = None
    asset: Asset | None = None
    priority: str = Priority.MEDIUM.value
    is_urgent: bool = False
    routed_by: str = "rules"  # student_choice | rules | triage_desk
    matched: list[dict] = field(default_factory=list)

    def explanation(self) -> dict:
        return {
            "routed_by": self.routed_by,
            "service": self.service.code if self.service else None,
            "department": self.department.code if self.department else None,
            "location": self.location.code if self.location else None,
            "asset": self.asset.code if self.asset else None,
            "urgent": self.is_urgent,
            "matches": self.matched,  # [{service, terms, score, urgent_terms}]
        }


def triage(db: Session, text: str, *, service_id=None, location_id=None) -> TriageResult:
    low = text.lower()
    res = TriageResult()

    rules = db.scalars(
        select(TriageRule).join(Service, Service.id == TriageRule.service_id)
        .where(TriageRule.is_active, Service.is_active)
    ).all()
    per: dict = {}
    for r in rules:
        if _rx(r.keyword).search(low):
            d = per.setdefault(r.service_id, {"service": r.service, "terms": [], "urgent_terms": [], "score": 0})
            d["terms"].append(r.keyword)
            d["score"] += r.weight
            if r.is_urgent:
                d["urgent_terms"].append(r.keyword)
    ranked = sorted(per.values(), key=lambda d: (-d["score"], -len(d["urgent_terms"]), d["service"].code))
    res.matched = [{"service": d["service"].code, "name": d["service"].name, "terms": d["terms"],
                    "score": d["score"], "urgent_terms": d["urgent_terms"]} for d in ranked]
    res.is_urgent = any(d["urgent_terms"] for d in ranked)

    if service_id:
        svc = db.get(Service, service_id)
        res.service, res.routed_by = svc, "student_choice"
    elif ranked:
        urgent_first = [d for d in ranked if d["urgent_terms"]]
        res.service = (urgent_first or ranked)[0]["service"]
        res.routed_by = "rules"
    else:
        res.routed_by = "triage_desk"

    if res.service:
        res.department = res.service.department
        res.priority = res.service.default_priority
    else:
        res.department = db.scalars(select(Department).where(Department.is_triage_desk, Department.is_active)).first()
    if res.is_urgent:
        res.priority = Priority.CRITICAL.value

    # location: explicit, else best (longest) alias/name mention
    if location_id:
        res.location = db.get(Location, location_id)
    else:
        best = (0, None)
        for loc in db.scalars(select(Location).where(Location.is_active)):
            for term in [loc.name, *(loc.aliases or [])]:
                if term and len(term) >= 3 and _rx(term).search(low) and len(term) > best[0]:
                    best = (len(term), loc)
        res.location = best[1]

    # asset: attach only when exactly one asset at the location is mentioned by name
    if res.location:
        hits = []
        for a in db.scalars(select(Asset).where(Asset.location_id == res.location.id, Asset.status != AssetStatus.RETIRED.value)):
            words = [w for w in re.findall(r"[a-z0-9]+", a.name.lower()) if len(w) >= 4]
            if words and any(_rx(w).search(low) for w in words):
                hits.append(a)
        if len(hits) == 1:
            res.asset = hits[0]
    return res

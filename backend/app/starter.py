"""Starter campus configuration (departments, locations, services, triage keywords).

This is *configuration*, not demo data: it contains no people and no activity. An administrator can load it
once (`python -m app.cli load-starter-catalog`) and then edit everything through the Admin UI / CSV import.
The keyword lists are the ones the prototype used for its client-side classifier, now stored in the database.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Department, Location, Service, TriageRule

DEPARTMENTS = [
    ("hostel", "Hostel & Facilities", False), ("academic", "Academic Support", False), ("medical", "Medical Centre", False),
    ("wellbeing", "Counselling & Wellbeing", False), ("safety", "Safety & Security", False),
    ("finance", "Fees & Scholarships", False), ("desk", "Service Desk", True),
]
LOCATIONS = [
    ("hostel-a", "Hostel A", "hostel", ["hostel a"]), ("hostel-b", "Hostel B", "hostel", ["hostel b"]),
    ("library", "Library", "library", ["library"]), ("block-c", "Block C", "academic", ["block c", "lab", "classroom"]),
    ("sports-gate", "Sports Gate", "gate", ["sports gate", "gate"]), ("mess", "Mess", "mess", ["mess", "canteen", "dining"]),
    ("medical-centre", "Medical Centre", "medical", ["medical centre", "health centre"]), ("admin-block", "Admin Block", "admin", ["admin block", "administration"]),
]
# code, name, dept, category, sensitive, emergency, approval, priority, student_visible
SERVICES = [
    ("hostel-maintenance", "Hostel & facility maintenance", "hostel", "hostel", False, False, False, "medium", True),
    ("mess-feedback", "Mess & food quality", "hostel", "hostel", False, False, False, "medium", True),
    ("academic-support", "Academic support", "academic", "academic", False, False, False, "medium", True),
    ("medical-help", "Medical help", "medical", "medical", True, False, False, "high", True),
    ("counselling", "Counselling & wellbeing", "wellbeing", "wellbeing", True, False, False, "medium", True),
    ("safety-report", "Safety concern", "safety", "safety", True, False, False, "high", True),
    ("sos", "SOS - immediate help", "safety", "safety", True, True, False, "critical", False),
    ("fee-waiver", "Fee waiver / scholarship help", "finance", "finance", False, False, True, "medium", True),
    ("fee-query", "Fee query", "finance", "finance", False, False, False, "low", True),
]
# service -> (keywords, urgent keywords). Keywords match at word start, so "harass" matches "harassment".
RULES = {
    "hostel-maintenance": (["lift", "leak", "wifi", "wi-fi", "water", "plumb", "fan", "light", "roommate", "noise", "ac ", "broken", "repair"], []),
    "mess-feedback": (["mess", "food", "meal", "canteen", "hygiene"], []),
    "academic-support": (["attendance", "assignment", "exam", "marks", "grade", "class", "lecture", "faculty", "backlog", "deadline", "lab"], []),
    "medical-help": (["fever", "sick", "doctor", "injur", "medicine", "ambulance", "infection", "pain"], ["unconscious", "bleeding", "chest pain"]),
    "counselling": (["stress", "anxi", "overwhelm", "lonely", "alone", "sleep", "panic", "depress", "homesick", "low mood"], ["self-harm", "suicid", "end my life", "kill myself"]),
    "safety-report": (["unsafe", "dark", "harass", "stalk", "theft", "stolen", "ragging", "bully", "assault"], ["followed", "threat", "attack", "weapon"]),
    "fee-waiver": (["scholarship", "waiver", "financial", "afford"], []),
    "fee-query": (["fee", "refund", "receipt", "payment", "invoice"], []),
}


def load_starter_catalog(db: Session) -> dict:
    """Idempotent: creates what is missing, never overwrites an administrator's edits."""
    made = {"departments": 0, "locations": 0, "services": 0, "rules": 0}
    depts = {}
    for code, name, desk in DEPARTMENTS:
        d = db.scalar(select(Department).where(Department.code == code))
        if not d:
            d = Department(code=code, name=name, is_triage_desk=desk and not db.scalar(select(Department).where(Department.is_triage_desk)))
            db.add(d)
            made["departments"] += 1
        depts[code] = d
    for code, name, kind, aliases in LOCATIONS:
        if not db.scalar(select(Location).where(Location.code == code)):
            db.add(Location(code=code, name=name, kind=kind, aliases=aliases))
            made["locations"] += 1
    db.flush()
    svcs = {}
    for code, name, dept, cat, sens, emerg, appr, prio, vis in SERVICES:
        s = db.scalar(select(Service).where(Service.code == code))
        if not s:
            s = Service(code=code, name=name, department_id=depts[dept].id, category=cat, is_sensitive=sens, is_emergency=emerg,
                        requires_approval=appr, default_priority=prio, student_visible=vis)
            db.add(s)
            made["services"] += 1
        svcs[code] = s
    db.flush()
    for code, (kws, urgent) in RULES.items():
        for kw, is_urgent in [(k, False) for k in kws] + [(k, True) for k in urgent]:
            kw = kw.strip().lower()
            if not db.scalar(select(TriageRule).where(TriageRule.service_id == svcs[code].id, TriageRule.keyword == kw)):
                db.add(TriageRule(service_id=svcs[code].id, keyword=kw, weight=3 if is_urgent else 1, is_urgent=is_urgent))
                made["rules"] += 1
    db.flush()
    return made

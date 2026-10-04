"""Demo Mode endpoints. This router is only mounted when DEMO_MODE=true AND APP_ENV != production
(see app/main.py). It does not exist in a production deployment."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app import devseed
from app.config import get_settings
from app.security.deps import Principal, require

router = APIRouter(prefix="/demo", tags=["demo (development only)"])

ACCOUNTS = [
    ("student", "aarav@campus.edu", "Aarav Sharma", "Raises requests, controls consent, sees own cases"),
    ("staff", "warden@campus.edu", "Hostel warden", "Works the hostel queue"),
    ("dept_head", "hostel-head@campus.edu", "Hostel chief warden", "Assigns, approves, runs incidents/problems/changes"),
    ("dept_head", "academic@campus.edu", "Academic head", "Department head"),
    ("staff", "hospital@campus.edu", "Medical officer", "Medical queue"),
    ("counsellor", "counsellor@campus.edu", "Counsellor", "Consent-based support cases"),
    ("staff", "safety@campus.edu", "Safety desk", "Safety & SOS"),
    ("dept_head", "finance@campus.edu", "Finance head", "Approves fee waivers"),
    ("mentor", "mentor@campus.edu", "Faculty mentor", "Cases the student consented to share"),
    ("command", "command@campus.edu", "Campus command", "Aggregates, SLA, approvals - no request text"),
    ("admin", "super@campus.edu", "System administrator", "Users, catalogue, SLA, permissions, audit"),
]


@router.get("/accounts", summary="Demo accounts (only exists in Demo Mode)")
def accounts():
    return {"password": get_settings().demo_password, "accounts": [{"role": r, "email": e, "name": n, "description": d} for r, e, n, d in ACCOUNTS]}


@router.post("/reset", summary="Wipe and re-seed the demo database (admin only; refused unless this is a dev/demo/test database)")
def reset(p: Principal = Depends(require("admin.system"))):
    devseed.assert_safe_target()
    return {"seeded": devseed.seed(reset=True), "note": "All sessions were invalidated; sign in again."}

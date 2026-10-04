from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import serializers as S
from app.db import get_db
from app.errors import NotFound
from app.models import AccessRequest, AuditLog, Case, CaseParticipant, ConsentGrant, Department, Role, Staff, Student, User
from app.models.base import utcnow
from app.models.enums import AccessStatus
from app.schemas import (
    AccessDecisionIn, AccessRequestIn, CaseCloseIn, CaseCreateIn, CaseDismissIn, CaseMessageIn, CaseResponseIn, CaseStatusIn,
    CaseTaskIn, CaseUserIn, ConsentIn,
)
from app.security import ratelimit
from app.security.deps import Principal, current_principal, require, require_any
from app.services import access, cases as C
from app.services.audit import audit

router = APIRouter(tags=["care"])


def _case(db: Session, p: Principal, case_id: uuid.UUID) -> Case:
    c = access.get_case(db, p, case_id)
    if not c:
        raise NotFound("Case not found")
    return c


@router.get("/cases", summary="Support cases you may see")
def list_cases(p: Principal = Depends(require("case.read")), db: Session = Depends(get_db), status: str | None = None,
               limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0)):
    conds = [access.case_scope_criterion(p)]
    if status:
        conds.append(Case.status.in_(status.split(",")))
    rows = db.scalars(select(Case).where(*conds).order_by(Case.created_at.desc()).limit(limit).offset(offset)).unique().all()
    return {"items": [S.case_summary(c, p) for c in rows], "limit": limit, "offset": offset, "total": len(rows)}


@router.post("/cases", status_code=201, summary="Open a case for a student who has consented to you")
def create_case(body: CaseCreateIn, p: Principal = Depends(require("case.manage")), db: Session = Depends(get_db)):
    c = C.create_manual(db, p, body.student_id, body.title)
    db.commit()
    return S.case_detail(db, _case(db, p, c.id), p)


@router.get("/cases/{case_id}")
def get_case(case_id: uuid.UUID, p: Principal = Depends(require("case.read")), db: Session = Depends(get_db)):
    c = _case(db, p, case_id)
    if not p.is_student:
        audit(db, "case.viewed", actor=p, entity_type="case", entity_id=c.id, subject=c.student_id)
        db.commit()
    return S.case_detail(db, c, p)


def _act(fn):
    def run(case_id: uuid.UUID, *a, p: Principal, db: Session, **kw):
        c = _case(db, p, case_id)
        fn(db, p, c, *a, **kw)
        db.commit()
        return S.case_detail(db, c, p)
    return run


@router.post("/cases/{case_id}/confirm", summary="A person confirms the suggestion (nothing happens until they do)")
def confirm(case_id: uuid.UUID, p: Principal = Depends(require("case.manage")), db: Session = Depends(get_db)):
    return _act(C.confirm)(case_id, p=p, db=db)


@router.post("/cases/{case_id}/dismiss")
def dismiss(case_id: uuid.UUID, body: CaseDismissIn, p: Principal = Depends(require("case.manage")), db: Session = Depends(get_db)):
    return _act(C.dismiss)(case_id, body.reason, p=p, db=db)


@router.post("/cases/{case_id}/status")
def status(case_id: uuid.UUID, body: CaseStatusIn, p: Principal = Depends(require("case.manage")), db: Session = Depends(get_db)):
    return _act(C.set_status)(case_id, body.status, p=p, db=db)


@router.post("/cases/{case_id}/close")
def close(case_id: uuid.UUID, body: CaseCloseIn, p: Principal = Depends(require("case.read")), db: Session = Depends(get_db)):
    return _act(C.close)(case_id, body.outcome, body.note, p=p, db=db)


@router.post("/cases/{case_id}/response", summary="Student accepts or declines outreach")
def response(case_id: uuid.UUID, body: CaseResponseIn, p: Principal = Depends(require("case.read")), db: Session = Depends(get_db)):
    return _act(C.student_response)(case_id, body.response, p=p, db=db)


@router.post("/cases/{case_id}/owner")
def owner(case_id: uuid.UUID, body: CaseUserIn, p: Principal = Depends(require("case.manage")), db: Session = Depends(get_db)):
    return _act(C.set_owner)(case_id, body.user_id, p=p, db=db)


@router.post("/cases/{case_id}/participants", status_code=201)
def add_participant(case_id: uuid.UUID, body: CaseUserIn, p: Principal = Depends(require("case.manage")), db: Session = Depends(get_db)):
    return _act(C.add_participant)(case_id, body.user_id, p=p, db=db)


@router.delete("/cases/{case_id}/participants/{user_id}")
def remove_participant(case_id: uuid.UUID, user_id: uuid.UUID, p: Principal = Depends(require("case.manage")), db: Session = Depends(get_db)):
    return _act(C.remove_participant)(case_id, user_id, p=p, db=db)


@router.post("/cases/{case_id}/messages", status_code=201)
def message(case_id: uuid.UUID, body: CaseMessageIn, p: Principal = Depends(require("case.message")), db: Session = Depends(get_db)):
    ratelimit.hit(f"case-msg:{p.id}", 30)
    return _act(C.post_message)(case_id, body.text, p=p, db=db)


@router.post("/cases/{case_id}/tasks", status_code=201, summary="Add a support action (intervention)")
def case_task(case_id: uuid.UUID, body: CaseTaskIn, p: Principal = Depends(require("task.manage")), db: Session = Depends(get_db)):
    return _act(C.create_intervention)(case_id, body.title, body.assignee_id, body.due_at, body.description, p=p, db=db)


@router.get("/cases/{case_id}/candidates", summary="People the student has allowed who could join this case")
def candidates(case_id: uuid.UUID, p: Principal = Depends(require("case.manage")), db: Session = Depends(get_db)):
    c = _case(db, p, case_id)
    have = {x.user_id for x in db.scalars(select(CaseParticipant).where(CaseParticipant.case_id == c.id, CaseParticipant.removed_at.is_(None)))}
    out = []
    st = db.get(Student, c.student_id)
    grants = db.scalars(select(ConsentGrant).where(ConsentGrant.student_id == c.student_id, ConsentGrant.is_granted)).all()
    if st and st.mentor_id and any(g.kind == "mentor" for g in grants) and st.mentor_id not in have:
        out.append(S.person(db.get(User, st.mentor_id)))
    for g in grants:
        if g.kind == "department":
            q = (select(User).join(Staff, Staff.user_id == User.id).join(Role, Role.id == User.role_id)
                 .where(Staff.department_id == g.department_id, User.is_active, Role.code.in_(["staff", "dept_head", "counsellor"])))
            out += [S.person(u) for u in db.scalars(q).unique() if u.id not in have]
    return out


# ---- consent -----------------------------------------------------------------------------------------
@router.get("/consents/me", tags=["consent"], summary="Who may join my support team")
def my_consent(p: Principal = Depends(require("consent.manage_own")), db: Session = Depends(get_db)):
    st = db.get(Student, p.id)
    gr = db.scalars(select(ConsentGrant).where(ConsentGrant.student_id == p.id, ConsentGrant.is_granted)).all()
    mentor = db.get(User, st.mentor_id) if st and st.mentor_id else None
    return {"onboarded": bool(st and st.consent_onboarded_at), "mentor": {"id": mentor.id, "name": mentor.full_name} if mentor else None,
            "mentor_allowed": any(g.kind == "mentor" for g in gr),
            "department_ids": [g.department_id for g in gr if g.kind == "department"],
            "departments": [{"id": d.id, "name": d.name} for d in db.scalars(select(Department).where(Department.is_active).order_by(Department.name))]}


@router.put("/consents/me", tags=["consent"])
def set_consent(body: ConsentIn, p: Principal = Depends(require("consent.manage_own")), db: Session = Depends(get_db)):
    C.set_consents(db, p, body.mentor, body.department_ids)
    db.commit()
    return my_consent(p, db)


@router.get("/me/access-log", tags=["consent"], summary="Who looked at my information")
def access_log(p: Principal = Depends(require("audit.read_own")), db: Session = Depends(get_db), limit: int = Query(50, ge=1, le=200)):
    q = (select(AuditLog).where(AuditLog.subject_user_id == p.id, AuditLog.actor_id.is_not(None), AuditLog.actor_id != p.id,
                                AuditLog.action.in_(["request.viewed", "case.viewed", "case.participant_added", "case.confirmed",
                                                     "access.requested", "request.status_changed", "request.assigned", "request.comment"]))
         .order_by(AuditLog.ts.desc()).limit(limit))
    return [{"at": a.ts, "action": a.action, "by": a.actor.full_name if a.actor else None, "role": a.actor_role,
             "break_glass": bool((a.details or {}).get("break_glass"))} for a in db.scalars(q).unique()]


# ---- break glass ---------------------------------------------------------------------------------------
def _ar_out(a: AccessRequest, p: Principal) -> dict:
    now = utcnow()
    expired = a.status == "approved" and a.expires_at and a.expires_at <= now
    return {"id": a.id, "status": "expired" if expired else a.status, "reason": a.reason, "requested_at": a.requested_at,
            "decided_at": a.decided_at, "expires_at": a.expires_at,
            "requester": S.person(a.requester), "student_id": a.student_id if not p.is_student else None}


@router.get("/access-requests", tags=["consent"])
def list_access(p: Principal = Depends(require_any("access.decide_own", "access.request")), db: Session = Depends(get_db)):
    col = AccessRequest.student_id if p.is_student else AccessRequest.requester_id
    return [_ar_out(a, p) for a in db.scalars(select(AccessRequest).where(col == p.id).order_by(AccessRequest.requested_at.desc()).limit(50)).unique()]


@router.post("/access-requests", status_code=201, tags=["consent"])
def request_access(body: AccessRequestIn, p: Principal = Depends(require("access.request")), db: Session = Depends(get_db)):
    ratelimit.hit(f"access-req:{p.id}", 10, window=3600)
    a = C.request_access(db, p, body.student_id, body.reason)
    db.commit()
    return _ar_out(a, p)


def _ar(db, p, aid):
    a = db.get(AccessRequest, aid)
    if not a or a.student_id != p.id:
        raise NotFound("Request not found")
    return a


@router.post("/access-requests/{access_id}/decision", tags=["consent"])
def decide_access(access_id: uuid.UUID, body: AccessDecisionIn, p: Principal = Depends(require("access.decide_own")), db: Session = Depends(get_db)):
    a = C.decide_access(db, p, _ar(db, p, access_id), body.approve)
    db.commit()
    return _ar_out(a, p)


@router.post("/access-requests/{access_id}/revoke", tags=["consent"])
def revoke_access(access_id: uuid.UUID, p: Principal = Depends(require("access.decide_own")), db: Session = Depends(get_db)):
    a = C.revoke_access(db, p, _ar(db, p, access_id))
    db.commit()
    return _ar_out(a, p)

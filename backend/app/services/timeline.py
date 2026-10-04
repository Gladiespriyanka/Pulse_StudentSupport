from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Request, RequestEvent


def add_event(
    db: Session, req: Request, kind: str, *, actor=None, from_status: str | None = None, to_status: str | None = None,
    note: str | None = None, internal: bool = False, meta: dict | None = None,
) -> RequestEvent:
    user = getattr(actor, "user", actor)
    ev = RequestEvent(request_id=req.id, actor_id=user.id if user else None, kind=kind, from_status=from_status,
                      to_status=to_status, note=note, is_internal=internal, meta=meta)
    db.add(ev)
    return ev

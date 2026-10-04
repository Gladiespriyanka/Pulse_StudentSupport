"""Append-only audit trail. Never put request free text or passwords in `details`."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog, User
from app.security.context import get_ctx


def _actor(actor: Any) -> tuple[uuid.UUID | None, str | None]:
    if actor is None:
        return None, "system"
    user = getattr(actor, "user", actor)
    role = getattr(actor, "role", None)  # Principal.role is a str; User.role is a Role object
    if role is not None and not isinstance(role, str):
        role = role.code
    return user.id, role


def audit(
    db: Session, action: str, *, actor: Any = None, entity_type: str | None = None, entity_id: Any = None,
    subject: Any = None, details: dict | None = None, outcome: str = "success", anonymous: bool = False,
) -> AuditLog:
    """`anonymous=True` omits ip / request id (used for the anonymous peer-concern channel)."""
    ctx = get_ctx()
    actor_id, role = _actor(actor)
    subject_id = None
    if subject is not None:
        subject_id = subject if isinstance(subject, uuid.UUID) else getattr(subject, "id", None)
    row = AuditLog(
        actor_id=actor_id, actor_role=role, action=action, entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None, subject_user_id=subject_id,
        details=details or None, outcome=outcome, ip=None if anonymous else ctx.ip, request_id=None if anonymous else ctx.request_id,
    )
    db.add(row)
    return row

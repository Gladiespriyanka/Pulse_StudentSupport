"""In-app notifications. Bodies never contain request free text (least data)."""
from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Notification, Role, Staff, User


def notify(
    db: Session, user_ids: Iterable[uuid.UUID | None], kind: str, title: str, body: str | None = None,
    entity_type: str | None = None, entity_id=None, exclude: Iterable[uuid.UUID] = (),
) -> int:
    skip = set(exclude)
    n = 0
    for uid in dict.fromkeys(u for u in user_ids if u and u not in skip):
        db.add(Notification(user_id=uid, kind=kind, title=title[:200], body=(body or None) and body[:500],
                            entity_type=entity_type, entity_id=str(entity_id) if entity_id else None))
        n += 1
    return n


def dept_user_ids(db: Session, department_id: uuid.UUID, roles: Iterable[str] = ("staff", "dept_head", "counsellor")) -> list[uuid.UUID]:
    q = (select(User.id).join(Staff, Staff.user_id == User.id).join(Role, Role.id == User.role_id)
         .where(Staff.department_id == department_id, User.is_active, Role.code.in_(list(roles))))
    return list(db.scalars(q))


def role_user_ids(db: Session, role_code: str) -> list[uuid.UUID]:
    q = select(User.id).join(Role, Role.id == User.role_id).where(Role.code == role_code, User.is_active)
    return list(db.scalars(q))

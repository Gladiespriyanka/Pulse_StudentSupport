"""Small generic CRUD router factory for administrative catalogue tables (departments, locations, services, SLA, triage, assets)."""

import uuid
from collections.abc import Callable

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import NotFound
from app.security.deps import Principal, require
from app.services.audit import audit


def crud_router(*, prefix: str, tag: str, model, In, Patch, Out, perm: str, read_perm: str | None = None, entity: str,
                order_by=None, before_save: Callable | None = None, deletable: bool = False,
                filters: dict | None = None) -> APIRouter:
    r = APIRouter(prefix=prefix, tags=[tag])
    read = require(read_perm or perm)
    write = require(perm)

    @r.get("", response_model=list[Out], summary=f"List {tag}")
    def list_(p: Principal = Depends(read), db: Session = Depends(get_db), limit: int = 500, offset: int = 0):
        q = select(model)
        if order_by is not None:
            q = q.order_by(order_by)
        return db.scalars(q.limit(min(limit, 1000)).offset(offset)).unique().all()

    @r.post("", response_model=Out, status_code=201, summary=f"Create {tag[:-1] if tag.endswith('s') else tag}")
    def create(body: In, p: Principal = Depends(write), db: Session = Depends(get_db)):  # type: ignore[valid-type]
        obj = model(**body.model_dump())
        if before_save:
            before_save(db, obj, None)
        db.add(obj)
        db.flush()
        audit(db, f"{entity}.created", actor=p, entity_type=entity, entity_id=obj.id, details={"fields": sorted(body.model_dump().keys())})
        db.commit()
        db.refresh(obj)
        return obj

    @r.patch("/{item_id}", response_model=Out, summary=f"Update {tag}")
    def patch(item_id: uuid.UUID, body: Patch, p: Principal = Depends(write), db: Session = Depends(get_db)):  # type: ignore[valid-type]
        obj = db.get(model, item_id)
        if not obj:
            raise NotFound(f"{entity.title()} not found")
        data = body.model_dump(exclude_unset=True)
        for k, v in data.items():
            setattr(obj, k, v)
        if before_save:
            before_save(db, obj, data)
        db.flush()
        audit(db, f"{entity}.updated", actor=p, entity_type=entity, entity_id=obj.id, details={"fields": sorted(data.keys())})
        db.commit()
        db.refresh(obj)
        return obj

    if deletable:
        @r.delete("/{item_id}", status_code=204, summary=f"Delete {tag}")
        def delete(item_id: uuid.UUID, p: Principal = Depends(write), db: Session = Depends(get_db)):
            obj = db.get(model, item_id)
            if not obj:
                raise NotFound(f"{entity.title()} not found")
            db.delete(obj)
            audit(db, f"{entity}.deleted", actor=p, entity_type=entity, entity_id=item_id)
            db.commit()

    return r

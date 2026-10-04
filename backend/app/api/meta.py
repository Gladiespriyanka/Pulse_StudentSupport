from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db

router = APIRouter(tags=["meta"])


@router.get("/meta", summary="Public, non-sensitive app information for the UI")
def meta():
    s = get_settings()
    return {"app": s.app_name, "demo_mode": s.demo_mode and not s.is_production, "environment": s.app_env,
            "crisis_resources": s.crisis_resource_list, "password_min_length": s.password_min_length}


@router.get("/health", summary="Liveness")
def health():
    return {"status": "ok"}


@router.get("/health/ready", summary="Readiness (checks the database)")
def ready(db: Session = Depends(get_db)):
    db.execute(text("select 1"))
    return {"status": "ready"}

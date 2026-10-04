"""Read-only catalogue for everyday use (any signed-in user) + administrative CRUD for admins."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.crud import crud_router
from app.db import get_db
from app.errors import Invalid
from app.models import Asset, Department, Location, Role, Service, SlaPolicy, Staff, TriageRule, User
from app.rbac import ASSIGNABLE_ROLES
from app.schemas import (
    AssetIn, AssetOut, AssetPatch, DepartmentIn, DepartmentOut, DepartmentPatch, LocationIn, LocationOut, LocationPatch,
    ServiceIn, ServiceOut, ServicePatch, SlaIn, SlaOut, SlaPatch, TriageRuleIn, TriageRuleOut, TriageRulePatch,
)
from app.security.deps import Principal, current_principal, require, require_any

read = APIRouter(prefix="/catalog", tags=["catalog"])


@read.get("/services", response_model=list[ServiceOut], summary="Service catalogue (students see only student-visible services)")
def services(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    q = select(Service).where(Service.is_active).order_by(Service.name)
    if p.is_student:
        q = q.where(Service.student_visible)
    return db.scalars(q).unique().all()


@read.get("/locations", response_model=list[LocationOut])
def locations(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return db.scalars(select(Location).where(Location.is_active).order_by(Location.name)).all()


@read.get("/departments", response_model=list[DepartmentOut])
def departments(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return db.scalars(select(Department).where(Department.is_active).order_by(Department.name)).all()


@read.get("/staff", summary="Assignable staff (for assignment pickers)")
def staff(department_id: str | None = None,
          p: Principal = Depends(require_any("request.assign", "task.manage", "case.manage", "incident.manage", "change.manage", "problem.manage")),
          db: Session = Depends(get_db)):
    q = (select(User).join(Staff, Staff.user_id == User.id).join(Role, Role.id == User.role_id)
         .where(User.is_active, Role.code.in_(list(ASSIGNABLE_ROLES))).order_by(User.full_name))
    if department_id:
        q = q.where(Staff.department_id == department_id)
    return [{"id": u.id, "name": u.full_name, "role": u.role.code, "department_id": u.staff.department_id,
             "department": u.staff.department.name if u.staff.department else None} for u in db.scalars(q).unique()]


@read.get("/assets", response_model=list[AssetOut], summary="Assets")
def assets(p: Principal = Depends(require("asset.read")), db: Session = Depends(get_db)):
    return db.scalars(select(Asset).order_by(Asset.name)).unique().all()


# ---- admin CRUD --------------------------------------------------------------------------------------
def _dept_hook(db: Session, obj: Department, data):
    if obj.is_triage_desk and (data is None or "is_triage_desk" in data):
        for other in db.scalars(select(Department).where(Department.is_triage_desk, Department.id != obj.id)):
            other.is_triage_desk = False
        db.flush()


def _sla_hook(db: Session, obj: SlaPolicy, data):
    if obj.resolution_minutes < obj.response_minutes:
        raise Invalid("Resolution time must be at least the response time.", code="invalid_sla")


def _svc_hook(db: Session, obj: Service, data):
    if db.get(Department, obj.department_id) is None:
        raise Invalid("Unknown department.", code="invalid_department")


def _rule_hook(db: Session, obj: TriageRule, data):
    if db.get(Service, obj.service_id) is None:
        raise Invalid("Unknown service.", code="invalid_service")


admin = APIRouter(prefix="/admin")
admin.include_router(crud_router(prefix="/departments", tag="admin-departments", model=Department, In=DepartmentIn,
                                 Patch=DepartmentPatch, Out=DepartmentOut, perm="admin.departments", entity="department",
                                 order_by=Department.name, before_save=_dept_hook))
admin.include_router(crud_router(prefix="/locations", tag="admin-locations", model=Location, In=LocationIn,
                                 Patch=LocationPatch, Out=LocationOut, perm="admin.departments", entity="location", order_by=Location.name))
admin.include_router(crud_router(prefix="/services", tag="admin-services", model=Service, In=ServiceIn, Patch=ServicePatch,
                                 Out=ServiceOut, perm="admin.services", entity="service", order_by=Service.name, before_save=_svc_hook))
admin.include_router(crud_router(prefix="/sla-policies", tag="admin-sla", model=SlaPolicy, In=SlaIn, Patch=SlaPatch, Out=SlaOut,
                                 perm="admin.sla", entity="sla_policy", order_by=SlaPolicy.priority, before_save=_sla_hook, deletable=True))
admin.include_router(crud_router(prefix="/triage-rules", tag="admin-triage", model=TriageRule, In=TriageRuleIn, Patch=TriageRulePatch,
                                 Out=TriageRuleOut, perm="admin.services", entity="triage_rule", order_by=TriageRule.keyword,
                                 before_save=_rule_hook, deletable=True))
admin.include_router(crud_router(prefix="/assets", tag="admin-assets", model=Asset, In=AssetIn, Patch=AssetPatch, Out=AssetOut,
                                 perm="asset.manage", read_perm="asset.read", entity="asset", order_by=Asset.name))

"""Request/response schemas. Inputs are strict (extra fields rejected, lengths bounded)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

Prio = Literal["low", "medium", "high", "critical"]
Category = Literal["hostel", "academic", "medical", "wellbeing", "safety", "finance", "it", "transport", "other"]
LocKind = Literal["hostel", "academic", "library", "medical", "gate", "mess", "admin", "other"]
Code = Field(min_length=2, max_length=48, pattern=r"^[a-z0-9][a-z0-9_-]*$")


class In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel):
    items: list[Any]
    total: int
    limit: int
    offset: int


# ---- auth ------------------------------------------------------------------------------------
class LoginIn(In):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class PasswordChangeIn(In):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: str
    role_name: str
    department_id: uuid.UUID | None = None
    department_name: str | None = None
    must_change_password: bool
    permissions: list[str]
    consent_onboarded: bool | None = None  # students only


# ---- catalogue / admin entities -------------------------------------------------------------------
class DepartmentIn(In):
    code: str = Code
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(None, max_length=1000)
    is_triage_desk: bool = False
    is_active: bool = True


class DepartmentPatch(In):
    name: str | None = Field(None, min_length=2, max_length=120)
    description: str | None = Field(None, max_length=1000)
    is_triage_desk: bool | None = None
    is_active: bool | None = None


class DepartmentOut(Out):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    is_triage_desk: bool
    is_active: bool


class LocationIn(In):
    code: str = Code
    name: str = Field(min_length=2, max_length=120)
    kind: LocKind = "other"
    aliases: list[str] = Field(default_factory=list, max_length=20)
    is_active: bool = True

    @field_validator("aliases")
    @classmethod
    def _aliases(cls, v):
        return [a.strip().lower() for a in v if a.strip()]


class LocationPatch(In):
    name: str | None = Field(None, min_length=2, max_length=120)
    kind: LocKind | None = None
    aliases: list[str] | None = Field(None, max_length=20)
    is_active: bool | None = None

    @field_validator("aliases")
    @classmethod
    def _aliases(cls, v):
        return None if v is None else [a.strip().lower() for a in v if a.strip()]


class LocationOut(Out):
    id: uuid.UUID
    code: str
    name: str
    kind: str
    aliases: list[str]
    is_active: bool


class ServiceIn(In):
    code: str = Code
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(None, max_length=1000)
    category: Category = "other"
    department_id: uuid.UUID
    default_priority: Prio = "medium"
    requires_approval: bool = False
    is_sensitive: bool = False
    is_emergency: bool = False
    student_visible: bool = True
    is_active: bool = True


class ServicePatch(In):
    name: str | None = Field(None, min_length=2, max_length=120)
    description: str | None = Field(None, max_length=1000)
    category: Category | None = None
    department_id: uuid.UUID | None = None
    default_priority: Prio | None = None
    requires_approval: bool | None = None
    is_sensitive: bool | None = None
    is_emergency: bool | None = None
    student_visible: bool | None = None
    is_active: bool | None = None


class ServiceOut(Out):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    category: str
    department_id: uuid.UUID
    default_priority: str
    requires_approval: bool
    is_sensitive: bool
    is_emergency: bool
    student_visible: bool
    is_active: bool


class SlaIn(In):
    service_id: uuid.UUID | None = None
    priority: Prio
    response_minutes: int = Field(gt=0, le=525600)
    resolution_minutes: int = Field(gt=0, le=525600)
    at_risk_pct: int = Field(75, ge=1, le=99)
    severe_multiplier: float = Field(2.0, ge=1, le=10)
    is_active: bool = True


class SlaPatch(In):
    response_minutes: int | None = Field(None, gt=0, le=525600)
    resolution_minutes: int | None = Field(None, gt=0, le=525600)
    at_risk_pct: int | None = Field(None, ge=1, le=99)
    severe_multiplier: float | None = Field(None, ge=1, le=10)
    is_active: bool | None = None


class SlaOut(Out):
    id: uuid.UUID
    service_id: uuid.UUID | None
    priority: str
    response_minutes: int
    resolution_minutes: int
    at_risk_pct: int
    severe_multiplier: float
    is_active: bool


class TriageRuleIn(In):
    service_id: uuid.UUID
    keyword: str = Field(min_length=2, max_length=80)
    weight: int = Field(1, ge=1, le=10)
    is_urgent: bool = False
    is_active: bool = True

    @field_validator("keyword")
    @classmethod
    def _kw(cls, v):
        return v.strip().lower()


class TriageRulePatch(In):
    weight: int | None = Field(None, ge=1, le=10)
    is_urgent: bool | None = None
    is_active: bool | None = None


class TriageRuleOut(Out):
    id: uuid.UUID
    service_id: uuid.UUID
    keyword: str
    weight: int
    is_urgent: bool
    is_active: bool


class AssetIn(In):
    code: str = Field(min_length=2, max_length=48, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    name: str = Field(min_length=2, max_length=120)
    asset_type: str | None = Field(None, max_length=60)
    location_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None
    status: Literal["active", "maintenance", "retired"] = "active"
    notes: str | None = Field(None, max_length=1000)


class AssetPatch(In):
    name: str | None = Field(None, min_length=2, max_length=120)
    asset_type: str | None = Field(None, max_length=60)
    location_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None
    status: Literal["active", "maintenance", "retired"] | None = None
    notes: str | None = Field(None, max_length=1000)


class AssetOut(Out):
    id: uuid.UUID
    code: str
    name: str
    asset_type: str | None
    location_id: uuid.UUID | None
    department_id: uuid.UUID | None
    status: str
    notes: str | None


class UserCreateIn(In):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=120)
    role: str = Field(max_length=32)
    password: str | None = Field(None, max_length=256)  # omit to generate a temporary one
    department_id: uuid.UUID | None = None
    roll_no: str | None = Field(None, max_length=32)
    programme: str | None = Field(None, max_length=80)
    year: int | None = Field(None, ge=1, le=8)
    mentor_id: uuid.UUID | None = None
    residence_location_id: uuid.UUID | None = None
    academic_department_id: uuid.UUID | None = None
    employee_no: str | None = Field(None, max_length=32)
    title: str | None = Field(None, max_length=80)


class UserPatchIn(In):
    full_name: str | None = Field(None, min_length=2, max_length=120)
    role: str | None = Field(None, max_length=32)
    is_active: bool | None = None
    department_id: uuid.UUID | None = None
    mentor_id: uuid.UUID | None = None
    programme: str | None = Field(None, max_length=80)
    year: int | None = Field(None, ge=1, le=8)


class RolePermsIn(In):
    permissions: list[str] = Field(max_length=100)


# ---- requests ---------------------------------------------------------------------------------
class TriagePreviewIn(In):
    description: str = Field(min_length=1, max_length=4000)
    service_id: uuid.UUID | None = None


class RequestCreateIn(In):
    description: str = Field(min_length=3, max_length=4000)
    service_id: uuid.UUID | None = None
    location_id: uuid.UUID | None = None
    consent_share: bool | None = None  # required (true/false) for sensitive services


class SosIn(In):
    location_id: uuid.UUID | None = None
    note: str | None = Field(None, max_length=500)


class PeerIn(In):
    description: str = Field(min_length=3, max_length=2000)
    location_id: uuid.UUID | None = None


class StatusIn(In):
    status: Literal["acknowledged", "in_progress", "resolved", "cancelled"]
    note: str | None = Field(None, max_length=2000)


class AssignIn(In):
    assignee_id: uuid.UUID | None


class RerouteIn(In):
    service_id: uuid.UUID


class EscalateIn(In):
    note: str | None = Field(None, max_length=1000)


class CommentIn(In):
    text: str = Field(min_length=1, max_length=4000)
    internal: bool = False


class ConsentShareIn(In):
    share: bool


class FeedbackIn(In):
    helpful: bool
    comment: str | None = Field(None, max_length=1000)


# ---- tasks / approvals ----------------------------------------------------------------------------
class TaskCreateIn(In):
    parent_type: Literal["request", "incident", "problem", "change"]
    parent_id: uuid.UUID
    title: str = Field(min_length=2, max_length=200)
    description: str | None = Field(None, max_length=2000)
    assignee_id: uuid.UUID | None = None
    due_at: datetime | None = None
    priority: Prio = "medium"


class TaskPatchIn(In):
    status: Literal["todo", "in_progress", "blocked", "done", "cancelled"] | None = None
    outcome: Literal["helped", "partly", "no_change"] | None = None
    outcome_note: str | None = Field(None, max_length=1000)
    assignee_id: uuid.UUID | None = None
    due_at: datetime | None = None


class DecisionIn(In):
    approve: bool
    reason: str | None = Field(None, max_length=1000)


# ---- itsm -------------------------------------------------------------------------------------
class IncidentCreateIn(In):
    title: str = Field(min_length=3, max_length=200)
    description: str | None = Field(None, max_length=4000)
    department_id: uuid.UUID | None = None
    location_id: uuid.UUID | None = None
    asset_id: uuid.UUID | None = None
    priority: Prio | None = None
    request_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)


class IncidentPatchIn(In):
    status: Literal["open", "investigating", "mitigated", "resolved", "closed"] | None = None
    note: str | None = Field(None, max_length=2000)
    owner_id: uuid.UUID | None = None
    resolve_linked_requests: bool = False
    force: bool = False


class LinkRequestsIn(In):
    request_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)


class IncidentProblemIn(In):
    problem_id: uuid.UUID | None = None
    title: str | None = Field(None, max_length=200)


class ProblemPatchIn(In):
    status: Literal["open", "investigating", "known_error", "resolved", "closed"] | None = None
    root_cause: str | None = Field(None, max_length=4000)
    workaround: str | None = Field(None, max_length=4000)
    resolution: str | None = Field(None, max_length=4000)
    owner_id: uuid.UUID | None = None


class ChangeCreateIn(In):
    title: str = Field(min_length=3, max_length=200)
    description: str | None = Field(None, max_length=4000)
    problem_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None
    risk: Literal["low", "medium", "high"] = "low"
    implementation_plan: str | None = Field(None, max_length=4000)
    rollback_plan: str | None = Field(None, max_length=4000)
    planned_start: datetime | None = None
    planned_end: datetime | None = None


class ChangePatchIn(In):
    title: str | None = Field(None, min_length=3, max_length=200)
    description: str | None = Field(None, max_length=4000)
    risk: Literal["low", "medium", "high"] | None = None
    implementation_plan: str | None = Field(None, max_length=4000)
    rollback_plan: str | None = Field(None, max_length=4000)
    planned_start: datetime | None = None
    planned_end: datetime | None = None


class ChangeCompleteIn(In):
    outcome: Literal["successful", "failed"]
    note: str | None = Field(None, max_length=2000)


# ---- cases / consent -----------------------------------------------------------------------------
class CaseCreateIn(In):
    student_id: uuid.UUID
    title: str = Field(min_length=3, max_length=200)


class CaseDismissIn(In):
    reason: str = Field(max_length=60)


class CaseCloseIn(In):
    outcome: Literal["stabilised", "partly", "no_change", "declined"] = "stabilised"
    note: str | None = Field(None, max_length=2000)


class CaseStatusIn(In):
    status: Literal["monitoring"]


class CaseResponseIn(In):
    response: Literal["accepted", "declined"]


class CaseUserIn(In):
    user_id: uuid.UUID


class CaseMessageIn(In):
    text: str = Field(min_length=1, max_length=4000)


class CaseTaskIn(In):
    title: str = Field(min_length=2, max_length=200)
    description: str | None = Field(None, max_length=2000)
    assignee_id: uuid.UUID | None = None
    due_at: datetime | None = None


class ConsentIn(In):
    mentor: bool = False
    department_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)


class AccessRequestIn(In):
    student_id: uuid.UUID
    reason: str = Field(min_length=10, max_length=1000)


class AccessDecisionIn(In):
    approve: bool


# ---- knowledge --------------------------------------------------------------------------------
class KnowledgeIn(In):
    title: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=3, max_length=20000)
    department_id: uuid.UUID | None = None
    audience: Literal["public", "staff"] = "staff"
    status: Literal["draft", "published", "archived"] = "draft"


class KnowledgePatch(In):
    title: str | None = Field(None, min_length=3, max_length=200)
    body: str | None = Field(None, min_length=3, max_length=20000)
    department_id: uuid.UUID | None = None
    audience: Literal["public", "staff"] | None = None
    status: Literal["draft", "published", "archived"] | None = None


class NotificationsReadIn(In):
    ids: list[uuid.UUID] | None = None  # None = mark everything read

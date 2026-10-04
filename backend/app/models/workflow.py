"""Core workflow: requests, timeline events, tasks, approvals, feedback."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, ForeignKey, Identity, Index, Integer, SmallInteger, String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.security.crypto import EncryptedText

from .base import utcnow, Base, TimestampMixin, check_in, tstz, uuid_pk
from .catalog import Asset, Service, SlaPolicy
from .enums import (
    ApprovalStatus, Priority, RequestSource, RequestStatus, TaskOutcome, TaskStatus,
)
from .org import Department, Location, User


class Request(TimestampMixin, Base):
    __tablename__ = "requests"
    id: Mapped[uuid.UUID] = uuid_pk()
    number: Mapped[int] = mapped_column(Integer, Identity(start=1001), unique=True)
    requester_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    source: Mapped[str] = mapped_column(String(10), default=RequestSource.PORTAL.value)
    service_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("services.id"), index=True)  # NULL = awaiting triage
    department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("departments.id"))
    location_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("locations.id"))
    asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assets.id"))
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(EncryptedText)
    priority: Mapped[str] = mapped_column(String(10), default=Priority.MEDIUM.value)
    status: Mapped[str] = mapped_column(String(20), default=RequestStatus.NEW.value)
    consent_share: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    is_urgent: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    triage: Mapped[dict | None] = mapped_column(JSONB)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    incident_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("incidents.id", ondelete="SET NULL"))
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id", ondelete="SET NULL"))
    # SLA
    sla_policy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sla_policies.id", ondelete="SET NULL"))
    sla_started_at: Mapped[datetime | None] = tstz(nullable=True)
    response_due_at: Mapped[datetime | None] = tstz(nullable=True)
    resolution_due_at: Mapped[datetime | None] = tstz(nullable=True)
    first_response_at: Mapped[datetime | None] = tstz(nullable=True)
    resolved_at: Mapped[datetime | None] = tstz(nullable=True)
    closed_at: Mapped[datetime | None] = tstz(nullable=True)
    feedback_requested_at: Mapped[datetime | None] = tstz(nullable=True)
    escalation_level: Mapped[int] = mapped_column(SmallInteger, default=0, server_default=text("0"))
    at_risk_notified_at: Mapped[datetime | None] = tstz(nullable=True)
    reopened_count: Mapped[int] = mapped_column(SmallInteger, default=0, server_default=text("0"))

    service: Mapped[Service | None] = relationship(lazy="joined")
    department: Mapped[Department] = relationship(lazy="joined", foreign_keys=[department_id])
    location: Mapped[Location | None] = relationship(lazy="joined")
    asset: Mapped[Asset | None] = relationship(lazy="joined")
    requester: Mapped[User | None] = relationship(foreign_keys=[requester_id], lazy="joined")
    assignee: Mapped[User | None] = relationship(foreign_keys=[assignee_id], lazy="joined")
    sla_policy: Mapped[SlaPolicy | None] = relationship(lazy="joined")

    __table_args__ = (
        check_in("status", RequestStatus, "request_status"),
        check_in("priority", Priority, "request_priority"),
        check_in("source", RequestSource, "request_source"),
        CheckConstraint("requester_id IS NOT NULL OR source = 'peer'", name="request_requester_required"),
        Index("ix_requests_dept_status", "department_id", "status"),
        Index("ix_requests_assignee_status", "assignee_id", "status"),
        Index("ix_requests_location", "location_id"),
        Index("ix_requests_created", "created_at"),
        Index("ix_requests_incident", "incident_id"),
        Index("ix_requests_case", "case_id"),
        Index("ix_requests_resolution_due_open", "resolution_due_at", postgresql_where=text("status IN ('new','acknowledged','in_progress')")),
    )


class RequestEvent(Base):
    """Timeline + comments. `is_internal` events are never shown to the student."""

    __tablename__ = "request_events"
    id: Mapped[uuid.UUID] = uuid_pk()
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("requests.id", ondelete="CASCADE"))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(24))
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str | None] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(EncryptedText)
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    meta: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = tstz(default=utcnow, server_default=func.now(), nullable=False)
    actor: Mapped[User | None] = relationship(lazy="joined")
    __table_args__ = (Index("ix_request_events_request_created", "request_id", "created_at"),)


class Task(TimestampMixin, Base):
    """A unit of work with exactly one parent (request / incident / problem / change / case)."""

    __tablename__ = "tasks"
    id: Mapped[uuid.UUID] = uuid_pk()
    number: Mapped[int] = mapped_column(Integer, Identity(start=1001), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(EncryptedText)
    request_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("requests.id", ondelete="CASCADE"))
    incident_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"))
    problem_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("problems.id", ondelete="CASCADE"))
    change_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("changes.id", ondelete="CASCADE"))
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    department_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("departments.id"))
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(12), default=TaskStatus.TODO.value)
    priority: Mapped[str] = mapped_column(String(10), default=Priority.MEDIUM.value)
    due_at: Mapped[datetime | None] = tstz(nullable=True)
    completed_at: Mapped[datetime | None] = tstz(nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(12))
    outcome_note: Mapped[str | None] = mapped_column(EncryptedText)
    escalated_at: Mapped[datetime | None] = tstz(nullable=True)
    assignee: Mapped[User | None] = relationship(foreign_keys=[assignee_id], lazy="joined")
    __table_args__ = (
        check_in("status", TaskStatus, "task_status"),
        check_in("priority", Priority, "task_priority"),
        check_in("outcome", TaskOutcome, "task_outcome", nullable=True),
        CheckConstraint("num_nonnulls(request_id, incident_id, problem_id, change_id, case_id) = 1", name="task_one_parent"),
        Index("ix_tasks_assignee_status", "assignee_id", "status"),
        Index("ix_tasks_request", "request_id"),
        Index("ix_tasks_incident", "incident_id"),
        Index("ix_tasks_problem", "problem_id"),
        Index("ix_tasks_change", "change_id"),
        Index("ix_tasks_case", "case_id"),
    )


class Approval(TimestampMixin, Base):
    __tablename__ = "approvals"
    id: Mapped[uuid.UUID] = uuid_pk()
    request_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("requests.id", ondelete="CASCADE"))
    change_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("changes.id", ondelete="CASCADE"))
    approver_role: Mapped[str] = mapped_column(String(32))  # role code allowed to decide
    department_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("departments.id"))  # dept scope for dept_head
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(12), default=ApprovalStatus.PENDING.value)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = tstz(nullable=True)
    reason: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        check_in("status", ApprovalStatus, "approval_status"),
        CheckConstraint("num_nonnulls(request_id, change_id) = 1", name="approval_one_subject"),
        CheckConstraint("status <> 'rejected' OR (reason IS NOT NULL AND length(trim(reason)) > 0)", name="approval_reject_reason"),
        Index("ix_approvals_status", "status"),
        Index("ix_approvals_request", "request_id"),
        Index("ix_approvals_change", "change_id"),
    )


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[uuid.UUID] = uuid_pk()
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    request_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("requests.id", ondelete="CASCADE"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    helpful: Mapped[bool] = mapped_column(Boolean)
    comment: Mapped[str | None] = mapped_column(EncryptedText)
    created_at: Mapped[datetime] = tstz(server_default=func.now(), nullable=False)
    __table_args__ = (
        CheckConstraint("num_nonnulls(request_id, task_id) = 1", name="feedback_one_subject"),
        UniqueConstraint("student_id", "request_id", name="uq_feedback_student_request"),
        UniqueConstraint("student_id", "task_id", name="uq_feedback_student_task"),
    )

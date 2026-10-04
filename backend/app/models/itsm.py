"""Incident -> Problem -> Change management."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Identity, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, check_in, tstz, uuid_pk
from .enums import ChangeOutcome, ChangeRisk, ChangeStatus, IncidentStatus, Priority, ProblemStatus
from .org import Department, User


class Problem(TimestampMixin, Base):
    __tablename__ = "problems"
    id: Mapped[uuid.UUID] = uuid_pk()
    number: Mapped[int] = mapped_column(Integer, Identity(start=101), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=ProblemStatus.OPEN.value)
    root_cause: Mapped[str | None] = mapped_column(Text)
    workaround: Mapped[str | None] = mapped_column(Text)
    resolution: Mapped[str | None] = mapped_column(Text)
    department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("departments.id"), index=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None] = tstz(nullable=True)
    department: Mapped[Department] = relationship(lazy="joined")
    owner: Mapped[User | None] = relationship(foreign_keys=[owner_id], lazy="joined")
    __table_args__ = (check_in("status", ProblemStatus, "problem_status"),)


class Incident(TimestampMixin, Base):
    __tablename__ = "incidents"
    id: Mapped[uuid.UUID] = uuid_pk()
    number: Mapped[int] = mapped_column(Integer, Identity(start=101), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=IncidentStatus.OPEN.value)
    priority: Mapped[str] = mapped_column(String(10), default=Priority.MEDIUM.value)
    department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("departments.id"), index=True)
    location_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("locations.id"), index=True)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assets.id"), index=True)
    problem_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("problems.id", ondelete="SET NULL"), index=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None] = tstz(nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text)
    department: Mapped[Department] = relationship(lazy="joined")
    owner: Mapped[User | None] = relationship(foreign_keys=[owner_id], lazy="joined")
    __table_args__ = (
        check_in("status", IncidentStatus, "incident_status"),
        check_in("priority", Priority, "incident_priority"),
    )


class Change(TimestampMixin, Base):
    __tablename__ = "changes"
    id: Mapped[uuid.UUID] = uuid_pk()
    number: Mapped[int] = mapped_column(Integer, Identity(start=101), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    problem_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("problems.id", ondelete="SET NULL"), index=True)
    department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("departments.id"), index=True)
    risk: Mapped[str] = mapped_column(String(8), default=ChangeRisk.LOW.value)
    status: Mapped[str] = mapped_column(String(20), default=ChangeStatus.DRAFT.value)
    implementation_plan: Mapped[str | None] = mapped_column(Text)
    rollback_plan: Mapped[str | None] = mapped_column(Text)
    planned_start: Mapped[datetime | None] = tstz(nullable=True)
    planned_end: Mapped[datetime | None] = tstz(nullable=True)
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    implemented_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    started_at: Mapped[datetime | None] = tstz(nullable=True)
    completed_at: Mapped[datetime | None] = tstz(nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(12))
    outcome_note: Mapped[str | None] = mapped_column(Text)
    department: Mapped[Department] = relationship(lazy="joined")
    __table_args__ = (
        check_in("risk", ChangeRisk, "change_risk"),
        check_in("status", ChangeStatus, "change_status"),
        check_in("outcome", ChangeOutcome, "change_outcome", nullable=True),
        CheckConstraint("planned_end IS NULL OR planned_start IS NULL OR planned_end >= planned_start", name="change_window"),
    )

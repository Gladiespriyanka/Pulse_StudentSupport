"""Care cases, student consent and break-glass access."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Identity, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.security.crypto import EncryptedText

from .base import utcnow, Base, TimestampMixin, check_in, tstz, uuid_pk
from .enums import AccessStatus, CaseOutcome, CaseStatus, ConsentKind
from .org import User


class Case(TimestampMixin, Base):
    """A coordinated, consent-based support case for one student.

    Created as `suggested` by an explainable rule (or manually by a mentor/counsellor);
    a human must confirm before anyone is asked to reach out.
    """

    __tablename__ = "cases"
    id: Mapped[uuid.UUID] = uuid_pk()
    number: Mapped[int] = mapped_column(Integer, Identity(start=101), unique=True)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(12), default=CaseStatus.SUGGESTED.value)
    origin: Mapped[str] = mapped_column(String(8), default="rule")
    rule_code: Mapped[str | None] = mapped_column(String(48))
    title: Mapped[str] = mapped_column(String(200))
    evidence: Mapped[dict | None] = mapped_column(JSONB)  # counts and request ids only, never free text
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    student_response: Mapped[str | None] = mapped_column(String(10))  # accepted | declined
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None] = tstz(nullable=True)
    dismissed_reason: Mapped[str | None] = mapped_column(String(80))
    closed_at: Mapped[datetime | None] = tstz(nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(12))
    closure_note: Mapped[str | None] = mapped_column(EncryptedText)
    student: Mapped[User] = relationship(foreign_keys=[student_id], lazy="joined")
    owner: Mapped[User | None] = relationship(foreign_keys=[owner_id], lazy="joined")
    __table_args__ = (
        check_in("status", CaseStatus, "case_status"),
        check_in("outcome", CaseOutcome, "case_outcome", nullable=True),
        CheckConstraint("origin IN ('rule','manual')", name="case_origin"),
        CheckConstraint("student_response IS NULL OR student_response IN ('accepted','declined')", name="case_student_response"),
        Index("uq_cases_one_active_per_student", "student_id", unique=True,
              postgresql_where=text("status IN ('suggested','open','monitoring')")),
    )


class CaseParticipant(Base):
    __tablename__ = "case_participants"
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    added_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    added_at: Mapped[datetime] = tstz(server_default=func.now(), nullable=False)
    removed_at: Mapped[datetime | None] = tstz(nullable=True)
    user: Mapped[User] = relationship(foreign_keys=[user_id], lazy="joined")


class CaseMessage(Base):
    __tablename__ = "case_messages"
    id: Mapped[uuid.UUID] = uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(EncryptedText)
    created_at: Mapped[datetime] = tstz(default=utcnow, server_default=func.now(), nullable=False)
    author: Mapped[User] = relationship(lazy="joined")
    __table_args__ = (Index("ix_case_messages_case_created", "case_id", "created_at"),)


class ConsentGrant(Base):
    """Standing student consent: who may join a support case. State row; history lives in the audit log."""

    __tablename__ = "consent_grants"
    id: Mapped[uuid.UUID] = uuid_pk()
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(12))
    department_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("departments.id"))
    scope: Mapped[str] = mapped_column(String(24), default="case_support", server_default="case_support")
    is_granted: Mapped[bool] = mapped_column(Boolean, default=False)
    changed_at: Mapped[datetime] = tstz(server_default=func.now(), nullable=False)
    __table_args__ = (
        UniqueConstraint("student_id", "kind", "department_id", "scope", name="uq_consent_target", postgresql_nulls_not_distinct=True),
        check_in("kind", ConsentKind, "consent_kind"),
        CheckConstraint("(kind = 'department') = (department_id IS NOT NULL)", name="consent_dept_matches_kind"),
    )


class AccessRequest(Base):
    """Break-glass: a staff member asks the student for time-limited extra access."""

    __tablename__ = "access_requests"
    id: Mapped[uuid.UUID] = uuid_pk()
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    requester_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default=AccessStatus.PENDING.value)
    requested_at: Mapped[datetime] = tstz(server_default=func.now(), nullable=False)
    decided_at: Mapped[datetime | None] = tstz(nullable=True)
    expires_at: Mapped[datetime | None] = tstz(nullable=True)
    requester: Mapped[User] = relationship(foreign_keys=[requester_id], lazy="joined")
    __table_args__ = (
        check_in("status", AccessStatus, "access_status"),
        CheckConstraint("status <> 'approved' OR expires_at IS NOT NULL", name="access_approved_has_expiry"),
    )

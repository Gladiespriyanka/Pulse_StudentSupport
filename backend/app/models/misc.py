"""Notifications, knowledge base, audit log."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, Computed, ForeignKey, Identity, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import utcnow, Base, TimestampMixin, check_in, tstz, uuid_pk
from .enums import Audience, KnowledgeStatus
from .org import User


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str | None] = mapped_column(String(500))  # never contains request free text
    entity_type: Mapped[str | None] = mapped_column(String(24))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = tstz(default=utcnow, server_default=func.now(), nullable=False)
    read_at: Mapped[datetime | None] = tstz(nullable=True)
    __table_args__ = (Index("ix_notifications_user_read_created", "user_id", "read_at", "created_at"),)


class KnowledgeArticle(TimestampMixin, Base):
    __tablename__ = "knowledge_articles"
    id: Mapped[uuid.UUID] = uuid_pk()
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    department_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("departments.id"), index=True)
    audience: Mapped[str] = mapped_column(String(8), default=Audience.STAFF.value)
    status: Mapped[str] = mapped_column(String(10), default=KnowledgeStatus.DRAFT.value)
    source_problem_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("problems.id", ondelete="SET NULL"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    published_at: Mapped[datetime | None] = tstz(nullable=True)
    search_vector = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(title,'') || ' ' || coalesce(body,''))", persisted=True),
    )
    __table_args__ = (
        check_in("status", KnowledgeStatus, "knowledge_status"),
        check_in("audience", Audience, "knowledge_audience"),
        Index("ix_knowledge_search", "search_vector", postgresql_using="gin"),
    )


class AuditLog(Base):
    """Append-only (enforced by a database trigger)."""

    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ts: Mapped[datetime] = tstz(server_default=func.now(), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    actor_role: Mapped[str | None] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str | None] = mapped_column(String(24))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    subject_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))  # whose data was touched
    outcome: Mapped[str] = mapped_column(String(10), default="success", server_default="success")
    details: Mapped[dict | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(40))
    actor: Mapped[User | None] = relationship(foreign_keys=[actor_id], lazy="joined")
    __table_args__ = (
        Index("ix_audit_ts", "ts"),
        Index("ix_audit_actor_ts", "actor_id", "ts"),
        Index("ix_audit_subject_ts", "subject_user_id", "ts"),
        Index("ix_audit_entity", "entity_type", "entity_id"),
        Index("ix_audit_action", "action"),
        CheckConstraint("outcome IN ('success','denied','failure')", name="audit_outcome"),
    )

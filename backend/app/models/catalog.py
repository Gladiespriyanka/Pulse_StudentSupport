"""Service catalogue: services, SLA policies, triage rules, assets."""
from __future__ import annotations

import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, check_in, uuid_pk
from .enums import AssetStatus, Priority, ServiceCategory
from .org import Department, Location


class Service(TimestampMixin, Base):
    __tablename__ = "services"
    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(48), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(16), default=ServiceCategory.OTHER.value)
    department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("departments.id"), index=True)
    default_priority: Mapped[str] = mapped_column(String(10), default=Priority.MEDIUM.value)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # Sensitive services (counselling, medical, safety...): details visible to the owning
    # department only when the student consented, and aggregates are k-anonymised.
    is_sensitive: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    is_emergency: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    student_visible: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))

    department: Mapped[Department] = relationship(lazy="joined")
    __table_args__ = (
        check_in("category", ServiceCategory, "service_category"),
        check_in("default_priority", Priority, "service_priority"),
    )


class SlaPolicy(TimestampMixin, Base):
    """service_id NULL = campus default for that priority."""

    __tablename__ = "sla_policies"
    id: Mapped[uuid.UUID] = uuid_pk()
    service_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"), index=True)
    priority: Mapped[str] = mapped_column(String(10))
    response_minutes: Mapped[int] = mapped_column(Integer)
    resolution_minutes: Mapped[int] = mapped_column(Integer)
    at_risk_pct: Mapped[int] = mapped_column(Integer, default=75, server_default=text("75"))
    severe_multiplier: Mapped[float] = mapped_column(Numeric(4, 2), default=2.0, server_default=text("2.0"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    __table_args__ = (
        UniqueConstraint("service_id", "priority", name="uq_sla_service_priority", postgresql_nulls_not_distinct=True),
        check_in("priority", Priority, "sla_priority"),
        CheckConstraint("response_minutes > 0 AND resolution_minutes >= response_minutes", name="sla_minutes"),
        CheckConstraint("at_risk_pct BETWEEN 1 AND 99", name="sla_at_risk_pct"),
        CheckConstraint("severe_multiplier >= 1", name="sla_severe_multiplier"),
    )


class TriageRule(TimestampMixin, Base):
    """Explainable routing rule: a keyword/stem that votes for a service."""

    __tablename__ = "triage_rules"
    id: Mapped[uuid.UUID] = uuid_pk()
    service_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"), index=True)
    keyword: Mapped[str] = mapped_column(String(80))
    weight: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    is_urgent: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    service: Mapped[Service] = relationship(lazy="joined")
    __table_args__ = (
        UniqueConstraint("service_id", "keyword", name="uq_triage_service_keyword"),
        CheckConstraint("weight BETWEEN 1 AND 10", name="triage_weight"),
        CheckConstraint("keyword = lower(keyword) AND length(trim(keyword)) >= 2", name="triage_keyword_norm"),
    )


class Asset(TimestampMixin, Base):
    __tablename__ = "assets"
    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(48), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    asset_type: Mapped[str | None] = mapped_column(String(60))
    location_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("locations.id"), index=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("departments.id"), index=True)
    status: Mapped[str] = mapped_column(String(12), default=AssetStatus.ACTIVE.value)
    notes: Mapped[str | None] = mapped_column(Text)
    location: Mapped[Location | None] = relationship(lazy="joined")
    __table_args__ = (check_in("status", AssetStatus, "asset_status"),)

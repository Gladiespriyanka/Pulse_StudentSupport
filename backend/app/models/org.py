"""Organisation & identity: departments, locations, roles/permissions, users, students, staff, sessions."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, ForeignKey, Index, Integer, SmallInteger, String, Text, UniqueConstraint, Uuid, func, text,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, check_in, tstz, uuid_pk
from .enums import LocationKind


class Department(TimestampMixin, Base):
    __tablename__ = "departments"
    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    # The desk that receives free-text requests the rules could not route. At most one.
    is_triage_desk: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    __table_args__ = (
        Index("uq_departments_one_triage_desk", "is_triage_desk", unique=True, postgresql_where=text("is_triage_desk")),
    )


class Location(TimestampMixin, Base):
    __tablename__ = "locations"
    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    kind: Mapped[str] = mapped_column(String(16), default=LocationKind.OTHER.value)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default=text("'{}'"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    __table_args__ = (check_in("kind", LocationKind, "location_kind"),)


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(Text)
    is_system: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    permissions: Mapped[list["Permission"]] = relationship(secondary="role_permissions", lazy="selectin", order_by="Permission.code")


class Permission(Base):
    __tablename__ = "permissions"
    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str | None] = mapped_column(Text)


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True)


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(254))
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(120))
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id"), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    locked_until: Mapped[datetime | None] = tstz(nullable=True)
    last_login_at: Mapped[datetime | None] = tstz(nullable=True)
    # Bumped on password change / deactivation / role change: invalidates all outstanding access tokens.
    token_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))

    role: Mapped[Role] = relationship(lazy="joined")
    student: Mapped["Student | None"] = relationship(back_populates="user", foreign_keys="Student.user_id", uselist=False, lazy="joined")
    staff: Mapped["Staff | None"] = relationship(back_populates="user", uselist=False, lazy="joined")

    __table_args__ = (Index("uq_users_email_lower", func.lower(email), unique=True),)


class Student(TimestampMixin, Base):
    __tablename__ = "students"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    roll_no: Mapped[str] = mapped_column(String(32), unique=True)
    programme: Mapped[str | None] = mapped_column(String(80))
    year: Mapped[int | None] = mapped_column(SmallInteger)
    academic_department_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("departments.id"), index=True)
    residence_location_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("locations.id"))
    mentor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    # NULL until the student has seen and answered the consent screen.
    consent_onboarded_at: Mapped[datetime | None] = tstz(nullable=True)

    user: Mapped[User] = relationship(back_populates="student", foreign_keys=[user_id])
    mentor: Mapped[User | None] = relationship(foreign_keys=[mentor_id])
    __table_args__ = (CheckConstraint("year IS NULL OR year BETWEEN 1 AND 8", name="student_year"),)


class Staff(TimestampMixin, Base):
    __tablename__ = "staff"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("departments.id"), index=True)
    employee_no: Mapped[str | None] = mapped_column(String(32), unique=True)
    title: Mapped[str | None] = mapped_column(String(80))

    user: Mapped[User] = relationship(back_populates="staff")
    department: Mapped[Department | None] = relationship(lazy="joined")


class RefreshToken(Base):
    """Opaque rotating refresh tokens (only the SHA-256 hash is stored)."""

    __tablename__ = "refresh_tokens"
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = tstz(server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = tstz(nullable=False)
    revoked_at: Mapped[datetime | None] = tstz(nullable=True)
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))

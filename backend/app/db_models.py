"""SQLAlchemy ORM models for the Postgres "User Management" tables.

Mirrors the tables defined in schema.sql (project root) exactly — column
names, types, defaults and FKs. Phase 1 covers organisations, users, roles,
permissions, role_permissions, user_roles, user_sessions and audit_logs.
Phase 2 (leases / lease_attributes / lease_files / lease_token_usage) covers
new-upload lease persistence — see app/services/lease_persistence_pg.py.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import CITEXT, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Organisation(Base):
    __tablename__ = "organisations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    plan: Mapped[str] = mapped_column(String, nullable=False, server_default="starter")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    max_users: Mapped[int] = mapped_column(Integer, nullable=False, server_default="10")
    max_leases: Mapped[int] = mapped_column(Integer, nullable=False, server_default="100")
    feature_flags: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))

    users: Mapped[list["User"]] = relationship(back_populates="organisation")


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    first_name: Mapped[str] = mapped_column(String, nullable=False)
    last_name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    department: Mapped[str | None] = mapped_column(String)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    failed_login_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column()
    last_login_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))

    organisation: Mapped["Organisation"] = relationship(back_populates="users")
    user_roles: Mapped[list["UserRole"]] = relationship(
        back_populates="user", foreign_keys="UserRole.user_id"
    )
    sessions: Mapped[list["UserSession"]] = relationship(back_populates="user")


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("name", "org_id", name="roles_name_org_id_key"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organisations.id", ondelete="CASCADE")
    )

    role_permissions: Mapped[list["RolePermission"]] = relationship(back_populates="role")


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True
    )

    role: Mapped["Role"] = relationship(back_populates="role_permissions")
    permission: Mapped["Permission"] = relationship()


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id", ondelete="RESTRICT"), primary_key=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organisations.id", ondelete="RESTRICT"), primary_key=True
    )
    assigned_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    assigned_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))

    user: Mapped["User"] = relationship(back_populates="user_roles", foreign_keys=[user_id])
    role: Mapped["Role"] = relationship()


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    device_info: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))

    user: Mapped["User"] = relationship(back_populates="sessions")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organisations.id", ondelete="SET NULL")
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ip_address: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)
    # "metadata" is reserved on the declarative Base (Base.metadata); map the
    # Python attribute to a different name while keeping the DB column name.
    event_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))


class Lease(Base):
    __tablename__ = "leases"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    store_name: Mapped[str | None] = mapped_column(String)
    store_code: Mapped[str | None] = mapped_column(String)
    landlord_name: Mapped[str | None] = mapped_column(String)
    tenant_name: Mapped[str | None] = mapped_column(String)
    status: Mapped[str | None] = mapped_column(String)
    currency: Mapped[str | None] = mapped_column(String)
    commencement_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    monthly_rent: Mapped[float | None] = mapped_column(Numeric(15, 2))
    portfolio_id: Mapped[str | None] = mapped_column(String)
    city: Mapped[str | None] = mapped_column(String)
    country: Mapped[str | None] = mapped_column(String)
    address: Mapped[str | None] = mapped_column(Text)
    pdf_type: Mapped[str | None] = mapped_column(String)
    page_count: Mapped[int | None] = mapped_column(Integer)
    has_unreviewed_fields: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    dismissed_from_history: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    extraction_status: Mapped[str] = mapped_column(String, nullable=False, server_default="review_pending")
    extraction_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))


class LeaseAttribute(Base):
    __tablename__ = "lease_attributes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    lease_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leases.id", ondelete="CASCADE"), nullable=False
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    category: Mapped[str] = mapped_column(String, nullable=False)
    attribute_key: Mapped[str] = mapped_column(String, nullable=False)
    attribute_name: Mapped[str] = mapped_column(String, nullable=False)
    data_type: Mapped[str] = mapped_column(String, nullable=False)
    extracted_value: Mapped[str | None] = mapped_column(Text)
    user_edited_value: Mapped[str | None] = mapped_column(Text)
    confidence_score: Mapped[int | None] = mapped_column(Integer)
    confidence_level: Mapped[str | None] = mapped_column(String)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    verified_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    verified_at: Mapped[datetime | None] = mapped_column()
    page_number: Mapped[int | None] = mapped_column(Integer)
    source_clause: Mapped[str | None] = mapped_column(Text)
    translation: Mapped[str | None] = mapped_column(Text)
    bbox: Mapped[dict | None] = mapped_column(JSONB)
    bbox_rects: Mapped[dict | None] = mapped_column(JSONB)
    is_key_field: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # Non-schema extractor fields bundled together: confidence_reason,
    # source_document_id, group, slot_index, sub_field.
    extra: Mapped[dict | None] = mapped_column(JSONB)
    schema_path: Mapped[str | None] = mapped_column(String)
    source_type: Mapped[str | None] = mapped_column(String)
    source_file_name: Mapped[str | None] = mapped_column(String)
    s3_file_path: Mapped[str | None] = mapped_column(String)


class LeaseFile(Base):
    __tablename__ = "lease_files"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    lease_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leases.id", ondelete="CASCADE"), nullable=False
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    file_name: Mapped[str] = mapped_column(String, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    pdf_type: Mapped[str | None] = mapped_column(String)
    page_count: Mapped[int | None] = mapped_column(Integer)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    doc_type: Mapped[str | None] = mapped_column(String)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    uploaded_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))


class LeaseTokenUsage(Base):
    __tablename__ = "lease_token_usage"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    lease_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leases.id", ondelete="CASCADE"), nullable=False
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    file_name: Mapped[str | None] = mapped_column(String)
    processed_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))
    pdf_type: Mapped[str | None] = mapped_column(String)
    model: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 6))
    locate_calls: Mapped[list | None] = mapped_column(JSONB)
    total_cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 6))

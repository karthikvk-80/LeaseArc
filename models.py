"""
User Management Module — SQLAlchemy ORM Models (V1)
====================================================
Mirrors the PostgreSQL schema defined in schema.sql.

Depends on:
  app.models.base  →  Base, TimestampMixin, UUIDPrimaryKeyMixin
  app.models.enums →  UserRoleEnum, PdfTypeEnum, DataTypeEnum,
                      ConfidenceLevelEnum

All timestamps are timezone-aware (DateTime(timezone=True)).
All UUID primary keys are auto-generated via uuid.uuid4.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    ConfidenceLevelEnum,
    DataTypeEnum,
    PdfTypeEnum,
)


# =============================================================================
# 1. ORGANISATION
#    Root anchor — every user and lease belongs to an org.
# =============================================================================

class Organisation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "organisations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    # Relationships
    users: Mapped[list["User"]] = relationship(back_populates="organisation")
    leases: Mapped[list["Lease"]] = relationship(back_populates="organisation")
    user_roles: Mapped[list["UserRole"]] = relationship(back_populates="organisation")


# =============================================================================
# 2. USER
# =============================================================================

class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organisations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    first_name: Mapped[str] = mapped_column(String(255), nullable=False)
    last_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # CITEXT in postgres — use String here; citext extension handles case-insensitivity at DB level
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)          # Argon2id
    department: Mapped[str | None] = mapped_column(String(255))               # display-only
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    organisation: Mapped["Organisation"] = relationship(back_populates="users")
    user_roles: Mapped[list["UserRole"]] = relationship(back_populates="user")
    sessions: Mapped[list["UserSession"]] = relationship(back_populates="user")
    password_reset_tokens: Mapped[list["PasswordResetToken"]] = relationship(back_populates="user")
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        back_populates="actor",
        foreign_keys="AuditLog.actor_id",
    )
    created_leases: Mapped[list["Lease"]] = relationship(
        back_populates="created_by",
        foreign_keys="Lease.created_by",
    )
    assigned_leases: Mapped[list["Lease"]] = relationship(
        back_populates="assigned_to_user",
        foreign_keys="Lease.assigned_to",
    )
    verified_attributes: Mapped[list["LeaseAttribute"]] = relationship(
        back_populates="verified_by_user",
        foreign_keys="LeaseAttribute.verified_by",
    )
    uploaded_files: Mapped[list["LeaseFile"]] = relationship(back_populates="uploaded_by")


# =============================================================================
# 3. ROLE
# =============================================================================

class Role(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)  # 'admin', 'user'
    description: Mapped[str | None] = mapped_column(Text)

    # Relationships
    role_permissions: Mapped[list["RolePermission"]] = relationship(back_populates="role")
    user_roles: Mapped[list["UserRole"]] = relationship(back_populates="role")


# =============================================================================
# 4. PERMISSION
# =============================================================================

class Permission(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "permissions"

    key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)  # e.g. 'leases:view_all'
    description: Mapped[str | None] = mapped_column(Text)

    # Relationships
    role_permissions: Mapped[list["RolePermission"]] = relationship(back_populates="permission")


# =============================================================================
# 5. ROLE_PERMISSION  (join table)
# =============================================================================

class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # Relationships
    role: Mapped["Role"] = relationship(back_populates="role_permissions")
    permission: Mapped["Permission"] = relationship(back_populates="role_permissions")


# =============================================================================
# 6. USER_ROLE  (org-scoped assignment)
# =============================================================================

class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", "org_id", name="uq_user_role_org"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organisations.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    assigned_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="user_roles", foreign_keys=[user_id])
    role: Mapped["Role"] = relationship(back_populates="user_roles")
    organisation: Mapped["Organisation"] = relationship(back_populates="user_roles")
    assigned_by_user: Mapped["User"] = relationship(foreign_keys=[assigned_by])


# =============================================================================
# 7. LEASE
# =============================================================================

class Lease(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "leases"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organisations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    store_name: Mapped[str | None] = mapped_column(String(255))
    store_code: Mapped[str | None] = mapped_column(String(100))
    landlord_name: Mapped[str | None] = mapped_column(String(255))
    tenant_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str | None] = mapped_column(String(50), index=True)
    currency: Mapped[str | None] = mapped_column(String(10))
    commencement_date: Mapped[datetime | None] = mapped_column(Date)
    expiry_date: Mapped[datetime | None] = mapped_column(Date)
    monthly_rent: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    portfolio_id: Mapped[str | None] = mapped_column(String(100))
    city: Mapped[str | None] = mapped_column(String(100))
    country: Mapped[str | None] = mapped_column(String(100))
    address: Mapped[str | None] = mapped_column(Text)
    pdf_type: Mapped[str | None] = mapped_column(
        SqlEnum(PdfTypeEnum),
        CheckConstraint("pdf_type IN ('native', 'scanned')", name="ck_leases_pdf_type"),
    )
    page_count: Mapped[int | None] = mapped_column(Integer)
    has_unreviewed_fields: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dismissed_from_history: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )

    # Relationships
    organisation: Mapped["Organisation"] = relationship(back_populates="leases")
    created_by_user: Mapped["User"] = relationship(
        back_populates="created_leases",
        foreign_keys=[created_by],
    )
    assigned_to_user: Mapped["User | None"] = relationship(
        back_populates="assigned_leases",
        foreign_keys=[assigned_to],
    )
    attributes: Mapped[list["LeaseAttribute"]] = relationship(back_populates="lease")
    files: Mapped[list["LeaseFile"]] = relationship(back_populates="lease")
    token_usage: Mapped[list["LeaseTokenUsage"]] = relationship(back_populates="lease")


# =============================================================================
# 8. LEASE_ATTRIBUTE
# =============================================================================

class LeaseAttribute(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "lease_attributes"
    __table_args__ = (
        CheckConstraint("confidence_score BETWEEN 0 AND 100", name="ck_lease_attr_confidence_score"),
        CheckConstraint("data_type IN ('text', 'number', 'date', 'percentage')", name="ck_lease_attr_data_type"),
        CheckConstraint("confidence_level IN ('low', 'high')", name="ck_lease_attr_confidence_level"),
    )

    lease_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # e.g. 'Core Lease Terms', 'Financial Obligations', 'CAM and Operating Expenses',
    #      'Restrictive Clauses', 'Critical Dates'
    attribute_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    attribute_name: Mapped[str] = mapped_column(String(255), nullable=False)
    data_type: Mapped[str] = mapped_column(SqlEnum(DataTypeEnum), nullable=False)
    extracted_value: Mapped[str | None] = mapped_column(Text)           # raw AI output
    user_edited_value: Mapped[str | None] = mapped_column(Text)         # NULL = untouched
    confidence_score: Mapped[int | None] = mapped_column(Integer)       # 0–100
    confidence_level: Mapped[str | None] = mapped_column(SqlEnum(ConfidenceLevelEnum))
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verified_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    page_number: Mapped[int | None] = mapped_column(Integer)
    source_clause: Mapped[str | None] = mapped_column(Text)             # verbatim clause from PDF
    translation: Mapped[str | None] = mapped_column(Text)               # English translation if non-English
    bbox: Mapped[dict | None] = mapped_column(JSONB)                    # {x1, y1, x2, y2, page}
    is_key_field: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Relationships
    lease: Mapped["Lease"] = relationship(back_populates="attributes")
    verified_by_user: Mapped["User | None"] = relationship(
        back_populates="verified_attributes",
        foreign_keys=[verified_by],
    )


# =============================================================================
# 9. LEASE_FILE
# =============================================================================

class LeaseFile(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "lease_files"

    lease_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)        # S3 key or filesystem path
    pdf_type: Mapped[str | None] = mapped_column(SqlEnum(PdfTypeEnum))
    page_count: Mapped[int | None] = mapped_column(Integer)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    # Relationships
    lease: Mapped["Lease"] = relationship(back_populates="files")
    uploaded_by_user: Mapped["User"] = relationship(back_populates="uploaded_files")


# =============================================================================
# 10. LEASE_TOKEN_USAGE
# =============================================================================

class LeaseTokenUsage(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "lease_token_usage"

    lease_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_name: Mapped[str | None] = mapped_column(String(512))
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    pdf_type: Mapped[str | None] = mapped_column(String(20))
    model: Mapped[str | None] = mapped_column(Text)                     # model name used
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    locate_calls: Mapped[list | None] = mapped_column(JSONB)            # array of individual call records
    total_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))

    # Relationships
    lease: Mapped["Lease"] = relationship(back_populates="token_usage")


# =============================================================================
# 11. USER_SESSION
# =============================================================================

class UserSession(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "user_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)  # SHA-256 of refresh token
    device_info: Mapped[str | None] = mapped_column(Text)               # user-agent string
    ip_address: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # NULL = active
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="sessions")


# =============================================================================
# 12. PASSWORD_RESET_TOKEN
# =============================================================================

class PasswordResetToken(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "password_reset_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)  # SHA-256 of one-time token
    ip_address: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # 1-hour window
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))   # NULL = not yet used
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="password_reset_tokens")


# =============================================================================
# 13. AUDIT_LOG
#     Append-only — never UPDATE or DELETE rows in this table.
# =============================================================================

class AuditLog(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_logs"

    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),    # log survives deleted users
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # e.g. 'user.login', 'user.login_failed', 'lease.created', 'attribute.verified'
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    ip_address: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)
    metadata: Mapped[dict | None] = mapped_column(JSON)                 # flexible per-event context
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    # Relationships
    actor: Mapped["User | None"] = relationship(
        back_populates="audit_logs",
        foreign_keys=[actor_id],
    )

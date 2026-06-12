"""
app/models/base.py
------------------
Shared SQLAlchemy base class and mixins used across all models.

Usage:
    from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Central declarative base — all models inherit from this."""
    pass


class UUIDPrimaryKeyMixin:
    """Adds a UUID primary key auto-generated on insert."""
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )


class TimestampMixin:
    """
    Adds created_at and updated_at to any model.
    updated_at is refreshed automatically by the DB trigger defined in schema.sql,
    but server_onupdate here keeps SQLAlchemy's session state in sync.
    """
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

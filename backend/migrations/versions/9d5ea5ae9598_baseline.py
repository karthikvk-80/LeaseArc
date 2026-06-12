"""baseline

Revision ID: 9d5ea5ae9598
Revises: 
Create Date: 2026-06-11 18:43:28.223753

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '9d5ea5ae9598'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op: schema.sql is the source of truth and was already applied to this
    # database directly. This revision exists only to give Alembic a stamped
    # baseline to build future migrations on top of. db_models.py (Phase 1)
    # only declares the auth-related tables, so autogenerate would otherwise
    # try to drop the not-yet-modeled lease*/password_reset_tokens tables and
    # several indexes here — intentionally omitted.
    pass


def downgrade() -> None:
    pass

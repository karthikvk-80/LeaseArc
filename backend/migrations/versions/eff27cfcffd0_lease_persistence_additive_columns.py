"""lease persistence additive columns

Revision ID: eff27cfcffd0
Revises: 9d5ea5ae9598
Create Date: 2026-06-12 09:34:29.392723

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'eff27cfcffd0'
down_revision: Union[str, None] = '9d5ea5ae9598'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('lease_attributes', sa.Column('bbox_rects', postgresql.JSONB, nullable=True))
    op.add_column('lease_attributes', sa.Column('extra', postgresql.JSONB, nullable=True))
    op.add_column('lease_files', sa.Column('doc_type', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('lease_files', 'doc_type')
    op.drop_column('lease_attributes', 'extra')
    op.drop_column('lease_attributes', 'bbox_rects')

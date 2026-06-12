"""lease extraction status

Revision ID: a1b2c3d4e5f6
Revises: 56de152f5455
Create Date: 2026-06-12 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '56de152f5455'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'leases',
        sa.Column('extraction_status', sa.String(), nullable=False, server_default='review_pending'),
    )
    op.add_column('leases', sa.Column('extraction_error', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('leases', 'extraction_error')
    op.drop_column('leases', 'extraction_status')

"""allow medium confidence level

Revision ID: 56de152f5455
Revises: 9a2177775c91
Create Date: 2026-06-12 09:58:14.821066

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '56de152f5455'
down_revision: Union[str, None] = '9a2177775c91'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint('lease_attributes_confidence_level_check', 'lease_attributes', type_='check')
    op.create_check_constraint(
        'lease_attributes_confidence_level_check', 'lease_attributes',
        "confidence_level IN ('low', 'medium', 'high')",
    )


def downgrade() -> None:
    op.drop_constraint('lease_attributes_confidence_level_check', 'lease_attributes', type_='check')
    op.create_check_constraint(
        'lease_attributes_confidence_level_check', 'lease_attributes',
        "confidence_level IN ('low', 'high')",
    )

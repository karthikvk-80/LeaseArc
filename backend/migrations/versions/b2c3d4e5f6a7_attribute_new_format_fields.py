"""attribute new format fields

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-12 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('lease_attributes', sa.Column('schema_path', sa.String(), nullable=True))
    op.add_column('lease_attributes', sa.Column('source_type', sa.String(), nullable=True))
    op.add_column('lease_attributes', sa.Column('source_file_name', sa.String(), nullable=True))
    op.add_column('lease_attributes', sa.Column('s3_file_path', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('lease_attributes', 's3_file_path')
    op.drop_column('lease_attributes', 'source_file_name')
    op.drop_column('lease_attributes', 'source_type')
    op.drop_column('lease_attributes', 'schema_path')

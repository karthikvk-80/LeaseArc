"""fix pdf_type check constraints

Revision ID: 9a2177775c91
Revises: eff27cfcffd0
Create Date: 2026-06-12 09:53:14.447467

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9a2177775c91'
down_revision: Union[str, None] = 'eff27cfcffd0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint('leases_pdf_type_check', 'leases', type_='check')
    op.create_check_constraint('leases_pdf_type_check', 'leases', "pdf_type IN ('typed', 'scanned')")

    op.drop_constraint('lease_files_pdf_type_check', 'lease_files', type_='check')
    op.create_check_constraint('lease_files_pdf_type_check', 'lease_files', "pdf_type IN ('typed', 'scanned')")


def downgrade() -> None:
    op.drop_constraint('lease_files_pdf_type_check', 'lease_files', type_='check')
    op.create_check_constraint('lease_files_pdf_type_check', 'lease_files', "pdf_type IN ('native', 'scanned')")

    op.drop_constraint('leases_pdf_type_check', 'leases', type_='check')
    op.create_check_constraint('leases_pdf_type_check', 'leases', "pdf_type IN ('native', 'scanned')")

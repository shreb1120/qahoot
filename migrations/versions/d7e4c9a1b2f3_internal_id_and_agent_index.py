"""rename calls.alv_id to internal_id; index calls by (org_id, agent_id)

ALV is debt-settlement jargon from the single-tenant predecessor. The product is
positioned horizontally, so the column becomes a neutral internal_id.

Stored values are deliberately NOT rewritten. Nothing in production carries an
"ALV-" prefix today (verified before writing this), and the templates stop
prepending one, so every stored value renders truthfully either way.

The composite index supports the agent profile page, which always filters both
org_id and agent_id.

Revision ID: d7e4c9a1b2f3
Revises: c3f1a2b4d5e6
Create Date: 2026-08-08 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd7e4c9a1b2f3'
down_revision: Union[str, Sequence[str], None] = 'c3f1a2b4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Catalog-only rename: instant, but takes ACCESS EXCLUSIVE and is not
    # backward compatible with running code. Stop the service before running.
    op.alter_column('calls', 'alv_id', new_column_name='internal_id')
    op.create_index('ix_calls_org_agent', 'calls', ['org_id', 'agent_id'])


def downgrade() -> None:
    op.drop_index('ix_calls_org_agent', table_name='calls')
    op.alter_column('calls', 'internal_id', new_column_name='alv_id')

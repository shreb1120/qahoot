"""metadata_and_agents

Revision ID: c3f1a2b4d5e6
Revises: be9630ffec9b
Create Date: 2026-08-07 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = 'c3f1a2b4d5e6'
down_revision: Union[str, Sequence[str], None] = 'be9630ffec9b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create agents table
    op.create_table(
        'agents',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('org_id', sa.String(36),
                  sa.ForeignKey('organizations.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_agents_org_id', 'agents', ['org_id'])

    # New columns on calls
    op.add_column('calls', sa.Column('agent_id', sa.String(36),
                  sa.ForeignKey('agents.id', ondelete='SET NULL'),
                  nullable=True))
    op.add_column('calls', sa.Column('alv_id', sa.String(50), nullable=True))
    op.add_column('calls', sa.Column('call_date', sa.Date(), nullable=True))
    op.add_column('calls', sa.Column('client_phone', sa.String(30), nullable=True))

    # New column on reports
    op.add_column('reports', sa.Column('overrides_json', JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column('reports', 'overrides_json')
    op.drop_column('calls', 'client_phone')
    op.drop_column('calls', 'call_date')
    op.drop_column('calls', 'alv_id')
    op.drop_column('calls', 'agent_id')
    op.drop_index('ix_agents_org_id', table_name='agents')
    op.drop_table('agents')

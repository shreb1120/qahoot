"""usage ledger, period counters, subscriptions, webhook guard, rate limits

Everything needed to answer "how much has this organization used, and may they
upload" — the prerequisite for charging anyone.

Two shapes here are deliberate and worth not undoing:

`usage_events.org_id` is ON DELETE **RESTRICT** and `call_id` is **SET NULL**,
where every other tenant table in this schema cascades. Organization.calls
already carries cascade="all, delete-orphan"; letting that reach the ledger
would delete an invoice's own evidence along with the call it describes. The
retention job removes the *audio file* and nulls audio_file_url — it never
touches a usage row.

`usage_events.idempotency_key` is UNIQUE and is the only thing making metering
exactly-once. The pipeline's in-process dedupe set does not survive a restart,
and recover_stranded deliberately re-runs calls; without this constraint every
rescued call would be billed twice.

Backfilling existing calls is left to scripts/backfill_usage.py rather than
done here, so it can be reviewed and re-run — the ledger is not something to
populate blind inside a schema migration.

Revision ID: f4a91c8e0d27
Revises: e8b3f6c2a71d
Create Date: 2026-08-08 22:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f4a91c8e0d27'
down_revision: Union[str, Sequence[str], None] = 'e8b3f6c2a71d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('organizations',
                  sa.Column('retention_days', sa.Integer(), nullable=True))

    op.create_table(
        'usage_events',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('org_id', sa.String(36), nullable=False),
        sa.Column('call_id', sa.String(36), nullable=True),
        sa.Column('resource_type', sa.String(20), nullable=False),
        sa.Column('billable_seconds', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('vendor_cost_micros', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('meta', postgresql.JSONB(), nullable=True),
        sa.Column('period_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('idempotency_key', sa.String(120), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['call_id'], ['calls.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('idempotency_key', name='uq_usage_events_idempotency_key'),
    )
    op.create_index('ix_usage_events_org_period', 'usage_events',
                    ['org_id', 'period_start', 'resource_type'])

    op.create_table(
        'usage_periods',
        sa.Column('org_id', sa.String(36), primary_key=True),
        sa.Column('period_start', sa.DateTime(timezone=True), primary_key=True),
        sa.Column('period_end', sa.DateTime(timezone=True), nullable=False),
        sa.Column('included_seconds', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('billable_seconds', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('warn_80_sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('warn_100_sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('stripe_invoice_id', sa.String(120), nullable=True),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    )

    op.create_table(
        'subscriptions',
        sa.Column('org_id', sa.String(36), primary_key=True),
        sa.Column('stripe_customer_id', sa.String(120), nullable=True),
        sa.Column('stripe_subscription_id', sa.String(120), nullable=True),
        sa.Column('stripe_price_id', sa.String(120), nullable=True),
        sa.Column('plan_code', sa.String(40), nullable=False, server_default='trial'),
        sa.Column('status', sa.String(40), nullable=False, server_default='trialing'),
        sa.Column('included_minutes', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('overage_micros_per_minute', sa.Integer(), nullable=False,
                  server_default='0'),
        sa.Column('current_period_start', sa.DateTime(timezone=True), nullable=True),
        sa.Column('current_period_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancel_at_period_end', sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column('trial_ends_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    )

    op.create_table(
        'stripe_events',
        sa.Column('id', sa.String(120), primary_key=True),
        sa.Column('type', sa.String(80), nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        'rate_limit_counters',
        sa.Column('scope_key', sa.String(120), primary_key=True),
        sa.Column('window_start', sa.DateTime(timezone=True), primary_key=True),
        sa.Column('count', sa.Integer(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_table('rate_limit_counters')
    op.drop_table('stripe_events')
    op.drop_table('subscriptions')
    op.drop_table('usage_periods')
    op.drop_index('ix_usage_events_org_period', table_name='usage_events')
    op.drop_table('usage_events')
    op.drop_column('organizations', 'retention_days')

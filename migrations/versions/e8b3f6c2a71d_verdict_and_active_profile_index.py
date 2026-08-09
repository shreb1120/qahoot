"""add reports.verdict (backfilled); enforce one active checklist per org

Two invariants the application asserted and the database did not.

`verdict` is the machine-readable form of a report's determination. Every
pass-rate query in the app used to scan `pass_fail_status` with ILIKE '%…%',
which can never use an index, and free text drifts: production still holds a
"FAIL — Both" written before report_normalizer.py existed and which no current
code path emits. The prose column stays for display.

The backfill deliberately derives from the *stored* strings rather than
re-grading, so no call's verdict changes as a side effect of a migration. The
ordering matters — 'CRITICAL FAIL' contains both 'CRITICAL' and 'FAIL', and
'PASS' must not match a determination that also says FAIL.

The partial unique index encodes "one active checklist per org", which
profile_bp maintains by deactivating the previous profile before inserting a
new one. Verified to hold in production before writing this; a concurrent
switch is what it protects against.

Revision ID: e8b3f6c2a71d
Revises: d7e4c9a1b2f3
Create Date: 2026-08-08 21:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e8b3f6c2a71d'
down_revision: Union[str, Sequence[str], None] = 'd7e4c9a1b2f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('reports', sa.Column('verdict', sa.String(16), nullable=True))

    # Order is load-bearing: CRITICAL first (its text also contains FAIL),
    # then FAIL, and only then PASS.
    op.execute("""
        UPDATE reports SET verdict = CASE
            WHEN pass_fail_status ILIKE '%CRITICAL%' THEN 'critical'
            WHEN pass_fail_status ILIKE '%FAIL%'     THEN 'fail'
            WHEN pass_fail_status ILIKE '%PASS%'     THEN 'pass'
            ELSE NULL
        END
    """)

    op.create_index('ix_reports_verdict', 'reports', ['verdict'])

    # Partial unique index — one row per org may carry is_active.
    op.execute("""
        CREATE UNIQUE INDEX ix_profiles_one_active
        ON compliance_profiles (org_id) WHERE is_active
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_profiles_one_active")
    op.drop_index('ix_reports_verdict', table_name='reports')
    op.drop_column('reports', 'verdict')

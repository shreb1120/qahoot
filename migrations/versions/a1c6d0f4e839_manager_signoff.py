"""add manager sign-off to reports

The review queue previously treated "has any per-item override" as "a manager
has decided". That meant a manager who read a call and agreed with every line
could never clear it — there was nothing to override — while correcting a
single item silently cleared a call they were halfway through.

Sign-off becomes its own thing: reviewed_at / reviewed_by / outcome / note.
Overrides keep their existing meaning, which is a factual correction to a
requirement and still what moves the score.

**Existing reports are left unreviewed on purpose.** Backfilling the calls that
happen to carry an override would assert that somebody signed those off, and
nobody did — the old flag never meant that. Better a queue that is honestly
full on the first morning than an audit trail that quietly invents decisions.

Revision ID: a1c6d0f4e839
Revises: f4a91c8e0d27
Create Date: 2026-08-10 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a1c6d0f4e839'
down_revision: Union[str, Sequence[str], None] = 'f4a91c8e0d27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('reports', sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('reports', sa.Column('reviewed_by_user_id', sa.String(36), nullable=True))
    op.add_column('reports', sa.Column('review_outcome', sa.String(16), nullable=True))
    op.add_column('reports', sa.Column('review_note', sa.Text(), nullable=True))

    # SET NULL, not CASCADE: a review has to survive the reviewer leaving the
    # company. It loses the name, not the fact that it happened.
    op.create_foreign_key(
        'fk_reports_reviewed_by_user', 'reports', 'users',
        ['reviewed_by_user_id'], ['id'], ondelete='SET NULL',
    )

    # Partial: the queue only ever asks for unreviewed rows, and reviewed ones
    # are what accumulate.
    op.execute("""
        CREATE INDEX ix_reports_unreviewed ON reports (reviewed_at)
        WHERE reviewed_at IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_reports_unreviewed")
    op.drop_constraint('fk_reports_reviewed_by_user', 'reports', type_='foreignkey')
    op.drop_column('reports', 'review_note')
    op.drop_column('reports', 'review_outcome')
    op.drop_column('reports', 'reviewed_by_user_id')
    op.drop_column('reports', 'reviewed_at')

"""allow a call to have several recordings

One conversation is often split across files — a transfer, a callback, a
dropped line. Graded separately, each part fails the requirements that were
satisfied in another part, which is the false-negative the reviewer then has to
unpick by hand.

`audio_parts` holds the ordered file paths when a call has more than one.
`audio_file_url` keeps pointing at the first, so every existing query, the
retention job and the backup mirror keep working untouched.

Nothing is backfilled: a call with one recording leaves this NULL, and the
pipeline treats NULL and a single-element list identically.

Revision ID: c9e3a72b5d18
Revises: b8d2f61a9c04
Create Date: 2026-08-12 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9e3a72b5d18"
down_revision: Union[str, Sequence[str], None] = "b8d2f61a9c04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("calls", sa.Column("audio_parts", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("calls", "audio_parts")

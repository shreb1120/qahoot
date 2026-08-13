"""add client_name to calls

A client page grouped on a phone number is correct but unreadable — nobody
recognises 9127137912. The name is stored per call rather than on a client
record because there is no client table: a client *is* the number in the
filename, and that is what makes the grouping work retroactively over calls
uploaded long before the feature existed.

Nullable, and nothing is backfilled. Every call already uploaded has no name,
and inventing one from a transcript would be a guess printed as a fact.

Revision ID: b8d2f61a9c04
Revises: a1c6d0f4e839
Create Date: 2026-08-12 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8d2f61a9c04"
down_revision: Union[str, Sequence[str], None] = "a1c6d0f4e839"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("calls", sa.Column("client_name", sa.String(120), nullable=True))


def downgrade() -> None:
    op.drop_column("calls", "client_name")

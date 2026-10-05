"""add chunk embeddings to jobs

Ingestion now derives job fields deterministically and embeds the
description as up to a few chunks.

Revision ID: 013
Revises: 012
Create Date: 2026-10-04
"""
from typing import Sequence, Union
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("chunks", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "chunks")

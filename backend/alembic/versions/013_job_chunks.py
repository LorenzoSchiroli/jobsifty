"""replace the job summary and its embedding with chunk embeddings

Ingestion now derives job fields deterministically and embeds the
description as up to a few chunks; the LLM summary and the single
summary embedding are no longer produced or read.

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
    op.drop_column("jobs", "summary")
    op.drop_column("jobs", "embedding")


def downgrade() -> None:
    op.add_column("jobs", sa.Column("embedding", postgresql.ARRAY(sa.Float()), nullable=True))
    op.add_column("jobs", sa.Column("summary", postgresql.JSONB(), nullable=True))
    op.drop_column("jobs", "chunks")

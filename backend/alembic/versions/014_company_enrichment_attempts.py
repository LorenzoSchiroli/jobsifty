"""track company enrichment attempts

Company enrichment now runs on a small per-run search budget, picking the
companies that most need it. enriched_at / enrich_attempts let it retry a
company that came back empty only after a pause and a bounded number of
times, instead of spending searches on it every run.

Companies that already hold data count as attempted once, so the budget goes
to the empty ones first.

Revision ID: 014
Revises: 013
Create Date: 2026-10-08
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("enriched_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("companies", sa.Column("enrich_attempts", sa.Integer(), server_default="0", nullable=False))
    op.execute(
        "UPDATE companies SET enrich_attempts = 1, enriched_at = updated_at "
        "WHERE founded_year IS NOT NULL OR employee_count IS NOT NULL "
        "OR review_score IS NOT NULL OR industry IS NOT NULL OR topic IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("companies", "enrich_attempts")
    op.drop_column("companies", "enriched_at")

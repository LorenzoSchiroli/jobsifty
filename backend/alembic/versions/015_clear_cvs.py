"""delete stored CVs and advanced-search checkpoints

CVs saved before redaction existed were never cleaned of personal data and
went only to Groq. Every LLM provider may see a CV now, so those stored
texts are deleted; users upload again and the new text is redacted first.
LangGraph checkpoints hold advanced-search state, cv_text included, so they
go too.

Revision ID: 015
Revises: 014
Create Date: 2026-11-05
"""
from typing import Sequence, Union
from alembic import op

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE applicant_profile SET cv_text = NULL")
    # LangGraph checkpoints hold advanced-search state, cv_text included; bootstrap
    # creates these tables after migrations, so a fresh DB doesn't have them yet.
    op.execute("""
        DO $$ BEGIN
          IF to_regclass('public.checkpoints') IS NOT NULL THEN
            TRUNCATE checkpoints, checkpoint_blobs, checkpoint_writes;
          END IF;
        END $$
    """)


def downgrade() -> None:
    pass  # deleted CVs don't come back

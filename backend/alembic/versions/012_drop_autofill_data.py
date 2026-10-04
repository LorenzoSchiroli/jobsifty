"""drop autofill (tailorer) data: applications table and form-fill profile fields

The autofill feature (Chrome extension + tailorer agent) moved to the
`autofill` branch. applicant_profile stays: it holds the CV used by
advanced search and fit scoring.

Revision ID: 012
Revises: 011
Create Date: 2026-10-04
"""
from typing import Sequence, Union
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PROFILE_TEXT_COLUMNS = ("first_name", "last_name", "email", "phone", "city", "country", "work_auth")
_PROFILE_JSONB_COLUMNS = ("urls", "extra_qa")


def upgrade() -> None:
    op.drop_table("applications")
    for name in _PROFILE_TEXT_COLUMNS + _PROFILE_JSONB_COLUMNS:
        op.drop_column("applicant_profile", name)


def downgrade() -> None:
    # Restores the schema only; dropped rows and values are not recoverable.
    for name in _PROFILE_TEXT_COLUMNS:
        op.add_column("applicant_profile", sa.Column(name, sa.Text(), nullable=True))
    for name in _PROFILE_JSONB_COLUMNS:
        op.add_column("applicant_profile", sa.Column(name, postgresql.JSONB(), nullable=True))
    op.create_table(
        "applications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="applied"),
        sa.CheckConstraint(
            "status IN ('applied','interviewing','rejected','offer')",
            name="ck_applications_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "job_id", name="uq_applications_user_job"),
    )

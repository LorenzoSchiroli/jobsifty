"""drop the empty parts of stored job locations

Some boards sent ", Lombardia" or a bare "," when the city was missing
(freehire's jobleads listings); ingestion now cleans them (JobOffer.location),
this cleans the jobs already stored. A location left empty becomes NULL.

Revision ID: 016
Revises: 015
Create Date: 2026-11-07
"""
from typing import Sequence, Union
from alembic import op

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(r"""
        UPDATE jobs SET location = NULLIF(array_to_string(ARRAY(
            SELECT btrim(part) FROM unnest(string_to_array(location, ',')) WITH ORDINALITY AS t(part, n)
            WHERE btrim(part) <> '' ORDER BY n), ', '), '')
        WHERE location ~ '(^\s*,)|(,\s*,)|(,\s*$)'
    """)


def downgrade() -> None:
    pass  # the empty parts carried nothing

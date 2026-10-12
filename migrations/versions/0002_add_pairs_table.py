"""Create the pairs table.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-06

Cycle A: the ``source → target`` pairs move out of ``chats.json`` into
the ``pairs`` table — columns ``id, source_ref, target_ref,
created_at`` (``id`` the sole primary key, everything NOT NULL), with
``UNIQUE (source_ref, target_ref)`` deduplicating a repeated pair, the
exact structure of ``bot.models.Pair``. ``created_at`` carries NO
server default: like ``buffer_messages`` it is filled by the PYTHON
default, so the migrated schema and the model stay byte-identical.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``pairs`` with its unique constraint (chained after ``0001``)."""
    op.create_table(
        "pairs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("target_ref", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_ref",
            "target_ref",
            name="uq_pairs_source_target",
        ),
    )


def downgrade() -> None:
    """Drop the table (the constraint goes with the table)."""
    op.drop_table("pairs")

"""Create the buffer_messages table.

Revision ID: 0001
Revises:
Create Date: 2026-10-05

The schema of item 3: one row per buffered source-chat message, with
``UNIQUE (chat_id, message_id)`` deduplicating re-delivered updates and
a plain ``chat_id`` index serving the per-chat prune and lookups — the
exact structure of ``bot.models.BufferMessage``.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``buffer_messages`` with its unique constraint and index."""
    op.create_table(
        "buffer_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("file_unique_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "chat_id",
            "message_id",
            name="uq_buffer_messages_chat_message",
        ),
    )
    op.create_index(
        "ix_buffer_messages_chat_id",
        "buffer_messages",
        ["chat_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the index and the table (the constraint goes with the table)."""
    op.drop_index("ix_buffer_messages_chat_id", table_name="buffer_messages")
    op.drop_table("buffer_messages")

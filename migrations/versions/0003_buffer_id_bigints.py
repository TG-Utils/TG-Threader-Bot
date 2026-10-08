"""Widen the Telegram id columns to 64-bit.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-06

``buffer_messages.chat_id`` / ``message_id`` / ``user_id`` were created
as ``Integer`` (int32). Telegram ids do not fit: supergroup chat ids
are ``-100…`` (e.g. ``-1004327410333``) and user ids exceed 2**31
(``5692382009``) — on PostgreSQL the insert raises
``OverflowError: value out of int32 range`` (sqlite, with its dynamic
typing, hides the bug in tests). ``batch_alter_table`` issues a plain
``ALTER ... TYPE BIGINT`` on PostgreSQL and a table rebuild on sqlite.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = ("chat_id", "message_id", "user_id")


def upgrade() -> None:
    """Widen ``chat_id``, ``message_id``, ``user_id`` to BIGINT."""
    with op.batch_alter_table("buffer_messages") as batch_op:
        for name in _COLUMNS:
            batch_op.alter_column(
                name,
                existing_type=sa.Integer(),
                type_=sa.BigInteger(),
                existing_nullable=name == "user_id",
            )


def downgrade() -> None:
    """Narrow back to int32 (a reverse overflow may fail by design)."""
    with op.batch_alter_table("buffer_messages") as batch_op:
        for name in _COLUMNS:
            batch_op.alter_column(
                name,
                existing_type=sa.BigInteger(),
                type_=sa.Integer(),
                existing_nullable=name == "user_id",
            )

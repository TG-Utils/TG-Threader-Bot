"""ORM models of the bot (item 1: SQLAlchemy; item 3: the buffer in the DB).

``Base`` is the declarative base every table is mapped from,
``BufferMessage`` is one row of the ``buffer_messages`` table — a
source-chat message seen live, which the step-5 cleanup of the watcher
searches by forward-origin date, sender and payload — and ``Pair`` is
one ``source → target`` row of the ``pairs`` table the chat registry
(``bot.chats``) caches in memory (cycle A: the pairs moved out of
``chats.json`` into the database).

Timestamp convention of the suite: ``sent_at`` and ``created_at`` are
NAIVE UTC (``DateTime`` WITHOUT timezone — alembic's convention for
both PostgreSQL and sqlite), so every representation of one instant
(``10:00+03:00`` == ``07:00+00:00`` == naive ``07:00``) resolves to the
same row. ``created_at`` is filled by a PYTHON default — no server
default, so the migrated schema and the models stay byte-identical.
"""

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow_naive() -> datetime:
    """The current instant as NAIVE UTC (the stored timestamp convention)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    """Declarative base carrying the metadata Alembic migrates."""


class BufferMessage(Base):
    """One source-chat message buffered for the step-5 cleanup.

    ``UNIQUE (chat_id, message_id)`` makes a re-delivered Telegram
    update land on ONE row; the plain (non-unique) ``chat_id`` index
    serves the per-chat prune and the per-chat lookups.
    """

    __tablename__ = "buffer_messages"

    __table_args__ = (
        UniqueConstraint(
            "chat_id",
            "message_id",
            name="uq_buffer_messages_chat_message",
        ),
    )

    #: Surrogate key driving insertion order (first match wins on the lowest id).
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: Source chat the record belongs to (never NULL — lookups are scoped to it).
    #: BIGINT: Telegram supergroup ids are ``-100…`` — outside int32 (PG ``integer``
    #: would raise OverflowError; sqlite tests cannot catch it).
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    #: Telegram message id of the original, paired with ``chat_id`` uniquely.
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Author of the original; NULL when the sender is unknown (F2 skips the check).
    #: BIGINT: user ids (e.g. ``5692382009``) exceed int32 as well.
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: Update timestamp as NAIVE UTC — the instant the lookup compares by.
    sent_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    #: Text payload of a plain text message (NULL for media).
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Caption payload of a media message (NULL for plain text).
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: ``file_unique_id`` of media — its identity when text and caption are empty.
    file_unique_id: Mapped[str | None] = mapped_column(String, nullable=True)
    #: Insertion timestamp (PYTHON default, naive UTC) — diagnostics only.
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)


class Pair(Base):
    """One ``source → target`` chat pair of the registry (cycle A).

    The pairs moved out of ``chats.json`` into this table: both refs
    are stored as TEXT in their recorded form (a numeric ref as its
    text form — the cache of ``bot.chats`` canonicalises it back to an
    ``int`` on ``refresh()``), and ``UNIQUE (source_ref, target_ref)``
    is the deduplication of a repeated pair.
    """

    __tablename__ = "pairs"

    __table_args__ = (
        UniqueConstraint(
            "source_ref",
            "target_ref",
            name="uq_pairs_source_target",
        ),
    )

    #: Surrogate key the settings menu deletes a pair by.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: Source chat of the pair in its recorded form (never NULL).
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)
    #: Target (threaded) chat of the pair in its recorded form (never NULL).
    target_ref: Mapped[str] = mapped_column(Text, nullable=False)
    #: Insertion timestamp (PYTHON default, naive UTC) — diagnostics only.
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)

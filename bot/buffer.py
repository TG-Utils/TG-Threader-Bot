"""Database-backed buffer of source-chat messages (BRIEF v3, step 5; item 3).

The bot records every message it sees LIVE in a configured source chat
— the Bot API history does not exist, so messages sent before the
startup are unfindable. Every record is a ROW of the ``buffer_messages``
table (``bot.models.BufferMessage``) inside the engine configured
through ``bot.database``; the watcher's step-5 cleanup searches that
table for the originals of a moved batch by the forward-origin date,
the forward-origin sender and the payload of the message (text, else
caption, else ``file_unique_id`` for media without both).

A found record is CONSUMED (``find_and_take`` DELETES the row): one
original is never deleted twice. Records of different chats never mix,
the ``maxlen`` cap prunes the OLDEST rows of a chat after every insert
(a pruned original is simply not found — never a wrong match elsewhere),
and a re-delivered update (the same ``(chat_id, message_id)``) lands on
ONE row through the table's unique constraint — never an exception.
"""

from datetime import datetime, timezone

from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError

from bot.database import session
from bot.models import BufferMessage


def as_naive_utc(date: datetime) -> datetime:
    """Read ``date`` under the stored convention: NAIVE UTC.

    An aware timestamp is converted to UTC and stripped of its offset
    (``10:00+03:00`` IS ``07:00+00:00``); a naive one already reads as
    UTC and is kept byte for byte.
    """
    if date.tzinfo is None:
        return date
    return date.astimezone(timezone.utc).replace(tzinfo=None)


class ChatBuffer:
    """Per-chat records of source messages, capped at ``maxlen`` rows each.

    Chats never mix: every insert, prune and lookup is scoped to a
    single ``chat_id``. The instance holds no memory of its own — all
    state lives in the database behind ``bot.database``.
    """

    def __init__(self, maxlen: int = 1000) -> None:
        """Keep at most ``maxlen`` rows per chat."""
        self.maxlen = maxlen

    async def record(
        self,
        chat_id: int,
        message_id: int,
        user_id: int | None,
        date: datetime,
        text: str | None,
        caption: str | None,
        file_unique_id: str | None,
    ) -> None:
        """Insert one source-chat message into the buffer of ``chat_id``.

        The record covers every field the cleanup may need: the message
        id, the author, the update timestamp (stored as naive UTC) and
        the payload (text, caption and the media's ``file_unique_id``).

        A re-delivered ``(chat_id, message_id)`` is a QUIET no-op: the
        unique constraint rejects the duplicate insert, the failed
        transaction is rolled back and only the prune runs afterwards.
        When a chat exceeds the cap, the OLDEST rows are pruned first —
        per chat, never touching another one.
        """
        row = BufferMessage(
            chat_id=chat_id,
            message_id=message_id,
            user_id=user_id,
            sent_at=as_naive_utc(date),
            text=text,
            caption=caption,
            file_unique_id=file_unique_id,
        )
        keep_newest = (
            select(BufferMessage.id)
            .where(BufferMessage.chat_id == chat_id)
            .order_by(BufferMessage.id.desc())
            .limit(self.maxlen)
            .scalar_subquery()
        )
        prune = delete(BufferMessage).where(
            BufferMessage.chat_id == chat_id,
            BufferMessage.id.not_in(keep_newest),
        )
        async with session() as db_session:
            try:
                db_session.add(row)
                await db_session.flush()
            except IntegrityError:
                # Duplicate (chat_id, message_id): a re-delivered update
                # must leave the existing row untouched and stay silent.
                await db_session.rollback()
            await db_session.execute(prune)

    async def find_and_take(
        self,
        chat_id: int,
        date: datetime,
        text: str | None,
        caption: str | None,
        file_unique_id: str | None,
        sender_id: int | None,
    ) -> int | None:
        """Look the original up in ``chat_id`` and CONSUME the first match.

        A match requires an equal instant (``sent_at`` — aware offsets
        are normalised to naive UTC on both sides), an equal SENDER —
        the forward origin's author against the recorded ``user_id``,
        with the condition skipped whenever either side is ``None``
        (F2) — AND the payload: a non-empty ``text`` must be equal;
        else a non-empty ``caption`` must be equal; else an equal
        non-``None`` ``file_unique_id`` (media without a caption is
        identified by date plus file id). The first row in insertion
        order (``id`` ASC) wins; the found row is DELETED, so a repeated
        lookup returns ``None``, and an unknown chat (or a chat without
        the original) is a plain miss.
        """
        if date is None:
            # A date-less forward origin can never identify an instant:
            # a plain miss, exactly like the old ``entry["date"] != date``
            # comparison read it — never a crash of the whole move.
            return None
        sent_at = as_naive_utc(date)
        conditions = [
            BufferMessage.chat_id == chat_id,
            BufferMessage.sent_at == sent_at,
        ]
        if text:
            conditions.append(BufferMessage.text == text)
        elif caption:
            conditions.append(BufferMessage.caption == caption)
        elif file_unique_id is None:
            # No payload on the lookup side: BRIEF step 5 cannot identify
            # an original — a miss, never a guess.
            return None
        else:
            conditions.append(BufferMessage.file_unique_id == file_unique_id)
        if sender_id is not None:
            # Either side unknown skips the condition (F2): a record
            # without an author still matches a known origin sender.
            conditions.append(
                or_(BufferMessage.user_id.is_(None), BufferMessage.user_id == sender_id)
            )
        async with session() as db_session:
            result = await db_session.execute(
                select(BufferMessage)
                .where(*conditions)
                .order_by(BufferMessage.id)
                .limit(1)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            found_id = row.message_id
            await db_session.delete(row)
            return found_id


#: Shared singleton the handlers record into and read from.
buffer = ChatBuffer()

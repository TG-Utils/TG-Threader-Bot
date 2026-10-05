"""In-memory buffer of source-chat messages (BRIEF v3, step 5).

The bot records every message it sees LIVE in a configured source chat
— the Bot API history does not exist, so messages sent before the
startup are unfindable. The watcher's step-5 cleanup searches this
buffer for the originals of a moved batch by the forward-origin date,
the forward-origin sender and the payload of the message (text, else
caption, else ``file_unique_id`` for media without both).

A found record is CONSUMED (``find_and_take``): one original is never
deleted twice. Records of different chats never mix, the ``maxlen``
cap evicts the OLDEST records first (an evicted original is simply not
found — never a wrong match elsewhere), and the buffer is in-memory
only: a restart loses it and the cleanup reports ``Deleted 0 of Y``
honestly (BRIEF «Объём v1»).
"""

from datetime import datetime
from typing import Any

#: One buffered message: id, author, timestamp, text/caption, media file id.
BufferEntry = dict[str, Any]


class ChatBuffer:
    """Per-chat list of recorded messages, capped at ``maxlen`` entries.

    Chats never mix: every record and every lookup is scoped to a
    single ``chat_id``.
    """

    def __init__(self, maxlen: int = 1000) -> None:
        """Keep at most ``maxlen`` records per chat."""
        self.maxlen = maxlen
        self._chats: dict[int, list[BufferEntry]] = {}

    def record(
        self,
        chat_id: int,
        message_id: int,
        user_id: int,
        date: datetime,
        text: str | None,
        caption: str | None,
        file_unique_id: str | None,
    ) -> None:
        """Append one source-chat message to the buffer of ``chat_id``.

        The record covers every field the cleanup may need: the message
        id, the author, the update timestamp and the payload (text,
        caption and the media's ``file_unique_id``). When a chat
        overflows the cap, the OLDEST records are evicted first.
        """
        entries = self._chats.setdefault(chat_id, [])
        entries.append(
            {
                "message_id": message_id,
                "user_id": user_id,
                "date": date,
                "text": text,
                "caption": caption,
                "file_unique_id": file_unique_id,
            }
        )
        overflow = len(entries) - self.maxlen
        if overflow > 0:
            del entries[:overflow]

    def find_and_take(
        self,
        chat_id: int,
        date: datetime,
        text: str | None,
        caption: str | None,
        file_unique_id: str | None,
        sender_id: int | None,
    ) -> int | None:
        """Look the original up in ``chat_id`` and CONSUME the first match.

        A match requires an equal ``date``, an equal SENDER — the
        forward origin's author against the recorded ``user_id``, with
        the condition skipped whenever either side is ``None`` (F2) —
        AND the payload: a non-empty ``text`` must be equal; else a
        non-empty ``caption`` must be equal; else an equal non-``None``
        ``file_unique_id`` (media without a caption is identified by
        date plus file id). The first record in insertion order wins, a
        found record is removed so a repeated lookup returns ``None``,
        and an unknown chat (or a chat without the original) is a plain
        miss.
        """
        entries = self._chats.get(chat_id)
        if not entries:
            return None
        for index, entry in enumerate(entries):
            if entry["date"] != date:
                continue
            if (
                sender_id is not None
                and entry["user_id"] is not None
                and entry["user_id"] != sender_id
            ):
                continue
            if text:
                if entry["text"] != text:
                    continue
            elif caption:
                if entry["caption"] != caption:
                    continue
            elif file_unique_id is None or entry["file_unique_id"] != file_unique_id:
                continue
            del entries[index]
            return entry["message_id"]
        return None


#: Shared singleton the handlers record into and read from.
buffer = ChatBuffer()

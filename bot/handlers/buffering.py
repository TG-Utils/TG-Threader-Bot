"""Buffering router: records source-chat messages into the database (BRIEF v3, §5).

The bot lives in every configured SOURCE chat and records each message
it sees live through ``await buffer.record(...)`` into the
``buffer_messages`` table (item 3: the buffer lives in the DB) — the
step-5 cleanup of the watcher searches those rows for the originals of
a moved batch (the Bot API history does not exist, so only seen
messages are findable).

Only chats present in the source→target registry are buffered; the
threaded chats and strangers are refused by the FILTER — silently:
nothing recorded, nothing replied, no API call. A message without
``from_user`` (anonymous admin, channel post) is ignored silently too
(security review L3). There are no command routers anymore: a ``/…``
text of the source chat is an ordinary message and IS buffered —
``/cancel`` only ever means something inside a pending session of a
threaded chat, which the watcher consumes first.
"""

from aiogram import Router
from aiogram.types import Message

from bot.buffer import buffer
from bot.chats import is_source


def is_source_chat(message: Message) -> bool:
    """Whether the message arrives in a configured source (main) chat."""
    return is_source(message.chat)


async def on_message(message: Message) -> None:
    """Record one source-chat message; senderless messages are ignored."""
    sender = message.from_user
    if sender is None:
        # No sender id to store (anonymous admin / channel post): the
        # message is dropped silently instead of crashing on ``.id`` (L3).
        return
    photo = getattr(message, "photo", None)
    attachment = photo[-1] if photo else None
    await buffer.record(
        message.chat.id,
        message.message_id,
        sender.id,
        message.date,
        message.text,
        message.caption,
        getattr(attachment, "file_unique_id", None),
    )


def create_router() -> Router:
    """Build a fresh buffering router (one router attaches to one parent)."""
    buffering_router = Router()
    buffering_router.message.register(on_message, is_source_chat)
    return buffering_router


#: Module-level router of this module (source-chat observer).
router = create_router()

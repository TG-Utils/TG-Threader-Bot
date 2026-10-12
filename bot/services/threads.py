"""Telegram link builders: plain message links and thread links.

A thread link points at the thread root message::

    https://t.me/<chat>/<message_id>?thread=<message_id>

— the message id is duplicated in the path and in the ``thread`` query
parameter. A plain message link (``build_message_url``) carries no
``?thread=`` parameter: it is built for the header's link back to the
original first message, which exists before the thread does.

Chats are addressed either by username (``@name`` → ``t.me/name/...``,
a leading ``@`` dropped, the rest must match ``[A-Za-z0-9_]{1,64}``) or
by integer id of a private chat (``-100111`` → ``t.me/c/111/...`` — the
``-100`` prefix dropped). Anything else raises ``ValueError``.
"""

import re

#: Telegram username format for the chat part of a public link.
CHAT_NAME_RE = re.compile(r"[A-Za-z0-9_]{1,64}\Z")

#: Internal chat id left after dropping the ``-100`` prefix: digits only.
INTERNAL_ID_RE = re.compile(r"[0-9]+\Z")

#: Prefix of a private supergroup id in t.me/c links.
PRIVATE_ID_PREFIX = "-100"


def _validate_message_id(message_id: int) -> int:
    """Accept only a positive integer message id."""
    if not isinstance(message_id, int) or isinstance(message_id, bool) or message_id <= 0:
        raise ValueError(f"message_id must be a positive integer, got {message_id!r}")
    return message_id


def _public_chat_name(chat: str) -> str:
    """Validate a username-format chat and drop its leading ``@``."""
    if not isinstance(chat, str):
        raise ValueError(f"chat must be a string, got {chat!r}")
    chat_name = chat.lstrip("@")
    if not chat_name:
        raise ValueError(f"chat name must not be empty, got {chat!r}")
    if CHAT_NAME_RE.fullmatch(chat_name) is None:
        raise ValueError(f"chat name must match [A-Za-z0-9_]{{1,64}}, got {chat_name!r}")
    return chat_name


def _private_chat_id(chat: int) -> str:
    """Validate a STRICT ``-100<digits>`` chat id and drop its ``-100`` prefix.

    Strict by design (L1): the prefix must actually be present and must
    leave at least one digit behind it. Ids that never had the prefix —
    ``100555``, ``-12345`` — name a different (or nonexistent) chat and
    raise ``ValueError`` instead of producing a bogus ``t.me/c/...`` link.
    """
    if isinstance(chat, bool) or not isinstance(chat, int):
        raise ValueError(f"private chat id must be an integer, got {chat!r}")
    text = str(chat)
    if not text.startswith(PRIVATE_ID_PREFIX):
        raise ValueError(f"chat id must be -100 followed by digits, got {chat!r}")
    internal_id = text.removeprefix(PRIVATE_ID_PREFIX)
    if not internal_id or INTERNAL_ID_RE.fullmatch(internal_id) is None:
        raise ValueError(f"chat id must be -100 followed by digits, got {chat!r}")
    return internal_id


def _chat_path(chat: str | int) -> str:
    """Path segment of ``chat``: ``<name>`` for a username, ``c/<id>`` for a private id."""
    if isinstance(chat, str):
        return _public_chat_name(chat)
    if isinstance(chat, int):
        return f"c/{_private_chat_id(chat)}"
    raise ValueError(f"chat must be a username or an integer chat id, got {chat!r}")


def build_thread_url(chat: str | int, message_id: int) -> str:
    """Return a thread link for ``message_id`` in ``chat``.

    Args:
        chat: Chat username (optionally ``@``-prefixed) or private id.
        message_id: Positive id of the message forming the thread.

    Returns:
        Thread URL in the form ``https://t.me/<chat>/<id>?thread=<id>``
        (private chats use ``https://t.me/c/<id>/<id>?thread=<id>``).

    Raises:
        ValueError: If the chat is a username outside
            ``[A-Za-z0-9_]{1,64}``, a private id without digits, or
            ``message_id`` is not a positive integer.
    """
    _validate_message_id(message_id)
    return f"https://t.me/{_chat_path(chat)}/{message_id}?thread={message_id}"


def build_message_url(chat: str | int, message_id: int) -> str:
    """Return a plain link to ``message_id`` in ``chat`` (no thread param).

    The header posted into the target chat links back to the original
    first message of the flood through this URL: at that moment the
    thread does not exist yet, so there is no ``?thread=`` parameter.

    Raises:
        ValueError: Under the same contract as ``build_thread_url``.
    """
    _validate_message_id(message_id)
    return f"https://t.me/{_chat_path(chat)}/{message_id}"

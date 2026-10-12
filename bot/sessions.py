"""Pending batch sessions for threaded chats (BRIEF v3, steps 1–3).

One in-memory session per threaded (target) chat: the forward handler
starts it with the batch's first message, every further forward joins
the ordered ``messages`` list, and the question state machine waits in
``stage`` — ``asking_source`` («Which chat did you forward from?») or
``asking_title`` (the topic title). ``prompt_ids`` keeps the ids of the
bot's own question messages so ``/cancel`` and every failure path can
delete them; ``source`` holds the fixed source pair once it is known —
a first message whose forward origin already identifies a configured
pair fixes it right at ``start`` (BRIEF step 2).

The store is in-memory only: a restart loses every pending batch
(BRIEF «Объём v1»).
"""

from typing import Any

from bot.chats import pair_for_origin

#: Stage waiting for the answer to «Which chat did you forward from?».
STAGE_ASKING_SOURCE = "asking_source"
#: Stage waiting for the topic title of the new thread.
STAGE_ASKING_TITLE = "asking_title"


class Session:
    """One chat's pending batch and its question state machine."""

    def __init__(self, first: dict[str, Any]) -> None:
        """Start with the batch's first forwarded message.

        An origin that names a configured source pair fixes ``source``
        immediately and skips the «which chat?» question; any other
        origin (a user forward, a stranger chat) leaves the machine
        waiting at ``asking_source``.
        """
        self.messages: list[dict[str, Any]] = [first]
        self.stage: str = STAGE_ASKING_SOURCE
        self.source: dict[str, Any] | None = None
        self.prompt_ids: list[int] = []
        pair = pair_for_origin(first.get("origin") or {})
        if pair is not None:
            self.source = pair
            self.stage = STAGE_ASKING_TITLE

    def add(self, ref: dict[str, Any]) -> None:
        """Append one forwarded message, keeping the arrival order."""
        self.messages.append(ref)

    def add_prompt_id(self, prompt_id: int) -> None:
        """Remember one question message id for later deletion."""
        self.prompt_ids.append(prompt_id)


class SessionStore:
    """In-memory store of pending sessions, isolated per chat id."""

    def __init__(self) -> None:
        """Start with no pending batches."""
        self._sessions: dict[int, Session] = {}

    def start(self, chat_id: int, first: dict[str, Any]) -> Session:
        """Create the pending session of ``chat_id`` and return it."""
        session = Session(first)
        self._sessions[chat_id] = session
        return session

    def get(self, chat_id: int) -> Session | None:
        """The pending session of ``chat_id``, or ``None``."""
        return self._sessions.get(chat_id)

    def reset(self, chat_id: int) -> None:
        """Drop the session of ``chat_id``, leaving other chats intact."""
        self._sessions.pop(chat_id, None)


#: Shared singleton the watcher handlers use.
sessions = SessionStore()

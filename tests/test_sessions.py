"""Pending batch session tests: module ``bot.sessions`` (BRIEF v3, §3).

RED phase (iteration C): ``bot.sessions`` does not exist yet — every
test here fails with ``ModuleNotFoundError`` (the ``sessions_mod()``
helper imports it inside the tests on purpose: at top level a missing
module would abort pytest collection for the whole suite and would flip
ruff's ``I001`` verdict once the file is created).

Specification (BRIEF.md v3, steps 1–3):
- one in-memory pending state PER THREADED (target) chat: the forward
  handler creates it on the first forward of a batch;
- the session holds
  * the batch as an ordered list of message refs — ``message_id``, the
    forward-origin data (``type``/``chat_id``/``username``/
    ``message_id``/``date``) and the message payload
    (``text``/``caption``/``file_unique_id``);
  * the ``prompt_ids`` — the ids of the bot's own question messages, so
    ``/cancel`` and every failure path can delete them;
  * the ``stage``: ``"asking_source"`` (waiting for «which chat did you
    forward from?») or ``"asking_title"`` (waiting for the topic title);
  * the fixed source ``pair`` (or ``None`` until it is known).
- the API pinned by these tests: ``start(chat_id, ref)`` /
  ``get(chat_id)`` / ``reset(chat_id)`` on the store, ``add(ref)`` /
  ``add_prompt_id(id)`` on the session, the settable ``stage`` and
  ``source`` attributes;
- sessions of different chats never mix: a second batch in another
  chat does not disturb the first one;
- the module exposes the shared singleton ``sessions`` (in-memory:
  a restart loses every pending batch — BRIEF «Объём v1»).
"""

from datetime import datetime, timezone

import pytest

#: Two threaded chats that must stay isolated from each other.
CHAT_A = -100666
CHAT_B = -100777

#: The timestamp travelling inside a forward origin.
D1 = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)

#: The configured source pair a session may fix.
SOURCE_PAIR = {"source": -100111, "target": "@forumgroup"}

#: The two stages of the question state machine (BRIEF steps 2–3).
STAGE_ASKING_SOURCE = "asking_source"
STAGE_ASKING_TITLE = "asking_title"


@pytest.fixture(autouse=True)
def clean_store():
    """Run every test against an empty shared store.

    The singleton persists across tests (it is the object the handlers
    use), so each test drops what earlier tests left in the two chats of
    this file. The import is guarded on purpose: while ``bot.sessions``
    does not exist (RED), the fixture must not swallow the failure —
    the tests then fail with ``ModuleNotFoundError`` on their own first
    helper call instead.
    """
    try:
        import bot.sessions as sessions_module
    except ModuleNotFoundError:
        return
    for chat_id in (CHAT_A, CHAT_B):
        sessions_module.sessions.reset(chat_id)


def sessions_mod():
    """The ``bot.sessions`` module, imported at test time.

    While the module does not exist (RED), every test fails with
    ``ModuleNotFoundError`` on its first helper call. The import lives
    inside this function on purpose: at top level a missing module
    would abort the collection of the whole pytest run and would be
    classified as third-party by ruff, flipping ``I001`` once the file
    exists.
    """
    import bot.sessions as sessions_module

    return sessions_module


def ref(message_id=11, *, origin=None, text="hello", caption=None, file_unique_id=None):
    """One batch message ref as the forward handler stores it."""
    if origin is None:
        origin = {
            "type": "channel",
            "chat_id": -100111,
            "username": "srcgroup",
            "message_id": 501,
            "date": D1,
        }
    return {
        "message_id": message_id,
        "origin": origin,
        "text": text,
        "caption": caption,
        "file_unique_id": file_unique_id,
    }


def start_session(chat_id=CHAT_A, first=None):
    """Start a session for ``chat_id`` and return it."""
    if first is None:
        first = ref()
    sessions = sessions_mod().sessions
    sessions.start(chat_id, first)
    session = sessions.get(chat_id)
    assert session is not None, "start() must make the session retrievable via get()"
    return session


class TestStartCreatesThePendingState:
    """``sessions.start(chat_id, ref)`` — the first forward of a batch."""

    def test_start_creates_a_session_holding_the_first_message(self):
        """A fresh session holds the batch's first ref, the stage, no source."""
        sessions = sessions_mod().sessions
        first = ref()

        sessions.start(CHAT_A, first)
        session = sessions.get(CHAT_A)

        assert session is not None, "start() must register the session under the chat"
        assert session.messages == [first], (
            "the batch starts with exactly the first forwarded message, in order"
        )

    def test_a_new_session_starts_in_the_asking_source_stage(self):
        """Before the origin is resolved the state machine waits for the source."""
        session = start_session()

        assert session.stage == STAGE_ASKING_SOURCE, (
            "a new session starts at the «which chat did you forward from?» stage"
        )

    def test_a_new_session_has_no_source_and_no_prompts(self):
        """No pair fixed yet, no question messages sent yet."""
        session = start_session()

        assert session.source is None, "the source pair is unknown until step 2"
        assert session.prompt_ids == [], "no question has been asked for a new batch"


class TestBatchAccumulation:
    """``session.add(ref)`` — every further forward joins the batch."""

    def test_added_messages_keep_their_order(self):
        """The batch is the sequence of forwards as they arrived."""
        session = start_session()

        session.add(ref(12))
        session.add(ref(13))

        assert [message["message_id"] for message in session.messages] == [11, 12, 13], (
            "the batch must preserve the arrival order of the forwards"
        )

    def test_added_refs_are_stored_verbatim(self):
        """The ref keeps every field the cleanup step will read later."""
        session = start_session()
        second = ref(12, text=None, caption="photo cap", file_unique_id="f-9")

        session.add(second)

        assert session.messages[1] == second, (
            "origin data and payload must survive the round-trip through the session"
        )


class TestStage:
    """The ``stage`` attribute: ``asking_source`` → ``asking_title``."""

    def test_stage_can_be_advanced_to_asking_title(self):
        """Once the source is known the session waits for the title."""
        session = start_session()

        session.stage = STAGE_ASKING_TITLE

        assert session.stage == STAGE_ASKING_TITLE

    def test_stage_can_be_set_back(self):
        """A conflicting origin may send the machine back to the source question."""
        session = start_session()
        session.stage = STAGE_ASKING_TITLE

        session.stage = STAGE_ASKING_SOURCE

        assert session.stage == STAGE_ASKING_SOURCE


class TestSourceFixation:
    """The fixed source pair of the session (BRIEF steps 2 and 5)."""

    def test_source_can_be_fixed_to_a_pair(self):
        """The answer/origin resolves the pair → it is stored on the session."""
        session = start_session()

        session.source = SOURCE_PAIR

        assert session.source == SOURCE_PAIR, (
            "the session must keep the source pair the execution step will use"
        )


class TestPromptIds:
    """The ids of the bot's own question messages."""

    def test_added_prompt_ids_are_kept_in_order(self):
        """Questions accumulate: ``/cancel`` deletes them all."""
        session = start_session()

        session.add_prompt_id(9001)
        session.add_prompt_id(9002)

        assert session.prompt_ids == [9001, 9002], (
            "every question message id must be recorded for later deletion"
        )


class TestGetAndReset:
    """``sessions.get(chat_id)`` and ``sessions.reset(chat_id)``."""

    def test_get_of_an_unknown_chat_is_none(self):
        """No pending batch in that chat → ``None``, never an error."""
        sessions = sessions_mod().sessions

        assert sessions.get(CHAT_A) is None

    def test_reset_drops_the_session_completely(self):
        """After ``reset`` the chat is clean: a later batch starts fresh."""
        sessions = sessions_mod().sessions
        session = start_session()
        session.add_prompt_id(9001)

        sessions.reset(CHAT_A)

        assert sessions.get(CHAT_A) is None, "reset must remove the pending state"
        fresh = start_session()
        assert fresh.prompt_ids == [], (
            "a session started after reset must not inherit prompts or messages"
        )
        assert [message["message_id"] for message in fresh.messages] == [11]

    def test_reset_scopes_to_its_own_chat(self):
        """Resetting one chat must not touch the batch pending in another."""
        sessions = sessions_mod().sessions
        keep = start_session(CHAT_B, ref(21))
        keep.stage = STAGE_ASKING_TITLE
        start_session(CHAT_A, ref(11))

        sessions.reset(CHAT_A)

        remaining = sessions.get(CHAT_B)
        assert remaining is not None, "the other chat's batch must survive"
        assert remaining.messages == [ref(21)]
        assert remaining.stage == STAGE_ASKING_TITLE


class TestChatIsolation:
    """Two batches pending at the same time never interfere."""

    def test_two_batches_do_not_mix(self):
        """Messages, stage and source are per-chat state."""
        sessions = sessions_mod().sessions
        sessions.start(CHAT_A, ref(11))
        sessions.start(CHAT_B, ref(21))
        session_a = sessions.get(CHAT_A)
        session_b = sessions.get(CHAT_B)

        session_a.add(ref(12))
        session_a.stage = STAGE_ASKING_TITLE
        session_a.source = SOURCE_PAIR

        assert [message["message_id"] for message in session_a.messages] == [11, 12]
        assert [message["message_id"] for message in session_b.messages] == [21], (
            "the second batch in another chat must be untouched"
        )
        assert session_b.stage == STAGE_ASKING_SOURCE, (
            "advancing one chat's stage must not advance another's"
        )
        assert session_b.source is None, "one chat's fixed source stays its own"


class TestSingleton:
    """The module-level store the handlers share."""

    def test_singleton_exposes_the_session_api(self):
        """``bot.sessions.sessions`` carries ``start``/``get``/``reset`` and starts empty."""
        mod = sessions_mod()
        store = mod.sessions

        assert store.get(CHAT_A) is None, "the shared store starts without pending batches"
        for name in ("start", "get", "reset"):
            assert callable(getattr(store, name, None)), (
                f"the singleton must provide {name}() for the watcher handlers"
            )

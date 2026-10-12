"""Source-chat buffer tests: module ``bot.buffer`` (BRIEF v3, section 5).

The buffer is DATABASE-backed since item 3 (the buffer lives in the
DB): every
recorded message is a row of the ``buffer_messages`` table
(``bot.models.BufferMessage``) inside the engine configured through
``bot.database`` — the suite-wide autouse fixture of
``tests/conftest.py`` gives every test a fresh
``sqlite+aiosqlite:///:memory:`` database.

RED phase: ``bot.database`` / ``bot.models`` do not exist yet, and the
buffer API is still synchronous. The helpers import those modules
INSIDE the test bodies on purpose (a top-level import would abort
pytest collection for the whole suite), so the tests fail with
``ModuleNotFoundError`` / ``TypeError`` (``await`` on the current
sync ``record``) while the collection stays clean.

Specification (BRIEF.md v3 §5 + item-3 pins):
- ``buffer = ChatBuffer(maxlen=1000)`` — the shared module singleton;
- ``await buffer.record(chat_id, message_id, user_id, date, text,
  caption, file_unique_id)`` INSERTS one row:
  * a repeated record of the same ``(chat_id, message_id)`` leaves ONE
    row and never raises (Telegram may re-deliver an update);
  * ``date`` is stored as NAIVE UTC (``aware → astimezone(UTC)
    .replace(tzinfo=None)``): the same INSTANT in any zone finds it —
    a record seen at ``10:00+03:00`` matches a lookup at
    ``07:00+00:00``, and a naive lookup date reads as UTC;
  * after the insert the chat is PRUNED: only the ``maxlen`` freshest
    rows of that chat survive (the oldest are deleted), other chats
    untouched;
- ``await buffer.find_and_take(chat_id, date, text, caption,
  file_unique_id, sender_id) -> int | None``:
  * dates compare BY INSTANT (aware UTC, naive-UTC, any zone);
  * payload predicate: a non-empty ``text`` → equal text; else a
    non-empty ``caption`` → equal caption; else
    ``file_unique_id is not None`` → equal ``file_unique_id`` (BRIEF
    step 5: media without text is identified by date + file id);
  * sender: both sides non-``None`` → they must be equal; either side
    ``None`` → the condition is skipped;
  * the FIRST match in insertion order (``id`` ASC) wins, lookups are
    scoped to one chat;
  * taking = DELETING the row: a repeated lookup of the same original
    returns ``None`` (one original is never deleted twice);
- the module exposes the shared singleton ``buffer = ChatBuffer()``.
"""

import inspect
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

#: Two chats that must stay isolated from each other.
CHAT_A = -1001
CHAT_B = -1002

#: Two distinct message timestamps (the ``date`` field of an update).
D1 = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
D2 = datetime(2026, 10, 5, 12, 5, 0, tzinfo=timezone.utc)

#: The SAME instant as D_UTC_0700, seen from the +03:00 zone.
D_KZ = datetime(2026, 10, 5, 10, 0, 0, tzinfo=timezone(timedelta(hours=3)))
#: The same instant in UTC — ``D_KZ == D_UTC_0700`` by instant.
D_UTC_0700 = datetime(2026, 10, 5, 7, 0, 0, tzinfo=timezone.utc)
#: Naive: the convention reads a naive timestamp as UTC.
D_NAIVE_0700 = datetime(2026, 10, 5, 7, 0, 0)

#: The text of a plain text message used across the tests.
TEXT = "moderation note"


def buffer_mod():
    """The ``bot.buffer`` module, imported at test time.

    The import lives inside this function on purpose: at top level a
    missing module would abort the collection of the whole pytest run
    and would be classified as third-party by ruff, flipping ``I001``
    once the file exists.
    """
    import bot.buffer as buffer_module

    return buffer_module


async def stored_rows(chat_id) -> list:
    """The ``BufferMessage`` rows of ``chat_id``, oldest first.

    The row STRUCTURE is part of the specification (BRIEF v3 §5: the
    record covers message_id, date, text/caption, file_unique_id), so
    the structure
    tests look at the table itself instead of at an in-memory layout.
    """
    from bot.database import session
    from bot.models import BufferMessage

    async with session() as db_session:
        result = await db_session.execute(
            select(BufferMessage)
            .where(BufferMessage.chat_id == chat_id)
            .order_by(BufferMessage.id)
        )
        return list(result.scalars())


class TestFindAndTake:
    """``await find_and_take(chat_id, date, text, caption, file_unique_id, sender_id)``.

    The search contract of the cleanup step (BRIEF v3, step 5): the
    original is located by the forward-origin date plus the SENDER of
    the forward origin plus the payload of the message — the text, the
    caption, or the ``file_unique_id`` of media without both.
    """

    async def test_text_message_is_found_by_date_and_text(self):
        """date + non-empty text both equal → the record's ``message_id``."""
        buf = buffer_mod().ChatBuffer()
        await buf.record(CHAT_A, 10, 100, D1, TEXT, None, None)

        assert await buf.find_and_take(CHAT_A, D1, TEXT, None, None, 100) == 10

    async def test_a_found_record_is_consumed(self):
        """The taken original is gone from the TABLE: a repeat → ``None``.

        The cleanup deletes the original once — a second lookup of the
        same original (a later duplicate forward, a retry) must not
        produce a second ``delete_message``.
        """
        buf = buffer_mod().ChatBuffer()
        await buf.record(CHAT_A, 10, 100, D1, TEXT, None, None)

        first = await buf.find_and_take(CHAT_A, D1, TEXT, None, None, 100)
        second = await buf.find_and_take(CHAT_A, D1, TEXT, None, None, 100)

        assert first == 10, "sanity: the first lookup finds the original"
        assert second is None, "a taken original must never be found again"
        assert await stored_rows(CHAT_A) == [], "taking means DELETING the row"

    async def test_the_first_match_in_insertion_order_wins(self):
        """Two identical originals → the older one first, then the newer."""
        buf = buffer_mod().ChatBuffer()
        await buf.record(CHAT_A, 10, 100, D1, TEXT, None, None)
        await buf.record(CHAT_A, 11, 100, D1, TEXT, None, None)

        assert await buf.find_and_take(CHAT_A, D1, TEXT, None, None, 100) == 10, (
            "insertion order: the record recorded first must be taken first"
        )
        assert await buf.find_and_take(CHAT_A, D1, TEXT, None, None, 100) == 11
        assert await buf.find_and_take(CHAT_A, D1, TEXT, None, None, 100) is None

    async def test_the_date_must_match(self):
        """A record taken with another instant is not the original."""
        buf = buffer_mod().ChatBuffer()
        await buf.record(CHAT_A, 10, 100, D1, TEXT, None, None)

        assert await buf.find_and_take(CHAT_A, D2, TEXT, None, None, 100) is None, (
            "the forward-origin date must equal the recorded update date"
        )

    async def test_dates_match_by_the_instant_in_any_timezone(self):
        """``10:00+03:00`` IS ``07:00+00:00`` — and a naive date reads as UTC.

        The storage convention of item 3: ``sent_at`` holds NAIVE UTC,
        so every representation of one instant resolves to the same
        row and a naive lookup is read as UTC.
        """
        buf = buffer_mod().ChatBuffer()
        await buf.record(CHAT_A, 10, 100, D_KZ, TEXT, None, None)
        await buf.record(CHAT_A, 11, 100, D_UTC_0700, TEXT, None, None)

        rows = await stored_rows(CHAT_A)
        assert [row.sent_at for row in rows] == [D_NAIVE_0700, D_NAIVE_0700], (
            "sent_at must be stored as naive UTC (no offset information)"
        )
        assert all(row.sent_at.tzinfo is None for row in rows), "stored rows are naive"

        assert await buf.find_and_take(CHAT_A, D_UTC_0700, TEXT, None, None, 100) == 10, (
            "a record seen at 10:00+03:00 must be found at 07:00+00:00"
        )
        assert await buf.find_and_take(CHAT_A, D_NAIVE_0700, TEXT, None, None, 100) == 11, (
            "a naive lookup date is read as UTC"
        )

    async def test_media_without_text_is_matched_by_file_unique_id(self):
        """A captioned photo: no text → date + ``file_unique_id``.

        This is the BRIEF step-5 rule «media without text → (date +
        file_unique_id)»: the caption travels with the row but the
        identity of a media original is its file id.
        """
        buf = buffer_mod().ChatBuffer()
        await buf.record(CHAT_A, 10, 100, D1, None, "photo caption", "f-abc123")

        assert await buf.find_and_take(CHAT_A, D1, None, None, "f-abc123", 100) == 10

    @pytest.mark.parametrize(
        "record, lookup, expected",
        [
            pytest.param(
                {"text": "", "caption": None, "file_unique_id": "f-abc123"},
                {"text": "", "caption": None, "file_unique_id": "f-abc123"},
                10,
                id="empty-text-falls-back-to-the-file-id",
            ),
            pytest.param(
                {"text": None, "caption": "photo caption", "file_unique_id": "f-uploaded"},
                {"text": None, "caption": "photo caption", "file_unique_id": "f-forward"},
                10,
                id="the-caption-identifies-a-captioned-media",
            ),
            pytest.param(
                {"text": None, "caption": "cap A", "file_unique_id": "f-same"},
                {"text": None, "caption": "cap B", "file_unique_id": "f-same"},
                None,
                id="another-caption-is-another-original",
            ),
            pytest.param(
                {"text": None, "caption": None, "file_unique_id": None},
                {"text": None, "caption": None, "file_unique_id": None},
                None,
                id="no-file-id-on-either-side-is-a-miss",
            ),
            pytest.param(
                {"text": None, "caption": None, "file_unique_id": None},
                {"text": None, "caption": None, "file_unique_id": "f-abc123"},
                None,
                id="a-file-id-the-bot-never-recorded-is-a-miss",
            ),
            pytest.param(
                {"text": None, "caption": None, "file_unique_id": "f-abc"},
                {"text": None, "caption": "photo caption", "file_unique_id": "f-abc"},
                None,
                id="caption-branch-against-a-record-without-a-caption",
            ),
            pytest.param(
                {"text": TEXT, "caption": None, "file_unique_id": "f-abc123"},
                {"text": "other text", "caption": None, "file_unique_id": "f-abc123"},
                None,
                id="a-text-record-is-not-matched-by-file-id-alone",
            ),
            pytest.param(
                {"text": TEXT, "caption": "photo caption", "file_unique_id": "f-abc"},
                {"text": "other text", "caption": "photo caption", "file_unique_id": "f-abc"},
                None,
                id="text-branch-misses-even-with-equal-caption-and-file",
            ),
            pytest.param(
                {"text": TEXT, "caption": "photo caption", "file_unique_id": "f-abc"},
                {"text": TEXT, "caption": "another caption", "file_unique_id": "f-other"},
                10,
                id="equal-text-wins-over-a-differing-caption",
            ),
        ],
    )
    async def test_the_payload_predicate_is_text_then_caption_then_file_id(
        self, record, lookup, expected
    ):
        """The BRIEF step-5 payload chain: text → caption → ``file_unique_id``."""
        buf = buffer_mod().ChatBuffer()
        await buf.record(
            CHAT_A, 10, 100, D1, record["text"], record["caption"], record["file_unique_id"]
        )

        found = await buf.find_and_take(
            CHAT_A,
            D1,
            lookup["text"],
            lookup["caption"],
            lookup["file_unique_id"],
            100,
        )

        assert found == expected, f"record {record!r} vs lookup {lookup!r}"


class TestSenderPredicate:
    """The sender half of the predicate (security review F2).

    The forward origin carries the sender (``sender_user.id`` /
    ``sender_chat.id``), the row carries the author (``user_id``), and
    the condition applies only when BOTH sides are known: an unknown
    sender on either side skips it, so senderless origins and records
    keep behaving exactly as before.
    """

    @pytest.mark.parametrize(
        "recorded_user, lookup_sender, expected",
        [
            pytest.param(100, 100, 10, id="equal-senders-match"),
            pytest.param(100, 200, None, id="another-author-is-another-original"),
            pytest.param(100, None, 10, id="unknown-sender-on-the-lookup-side-skips"),
            pytest.param(None, 42, 10, id="unknown-sender-on-the-record-side-skips"),
        ],
    )
    async def test_the_sender_condition_needs_both_sides(
        self, recorded_user, lookup_sender, expected
    ):
        """Both known → equality enforced; either ``None`` → condition skipped."""
        buf = buffer_mod().ChatBuffer()
        await buf.record(CHAT_A, 10, recorded_user, D1, TEXT, None, None)

        found = await buf.find_and_take(CHAT_A, D1, TEXT, None, None, lookup_sender)

        assert found == expected, (
            f"record user_id={recorded_user!r} vs lookup sender={lookup_sender!r}"
        )


class TestChatIsolation:
    """Records of one chat never satisfy a lookup in another chat."""

    async def test_identical_payloads_resolve_to_their_own_chat(self):
        """Unknown chat misses; the same (date, text) in two chats → own ids."""
        buf = buffer_mod().ChatBuffer()
        assert await buf.find_and_take(CHAT_B, D1, TEXT, None, None, 100) is None, (
            "an unknown chat is a plain miss, never an error"
        )

        await buf.record(CHAT_A, 10, 100, D1, TEXT, None, None)
        await buf.record(CHAT_B, 20, 200, D1, TEXT, None, None)

        assert await buf.find_and_take(CHAT_A, D1, TEXT, None, None, 100) == 10, (
            "chat A must resolve to chat A's record"
        )
        assert await buf.find_and_take(CHAT_B, D1, TEXT, None, None, 200) == 20, (
            "chat B must resolve to chat B's record"
        )


class TestDeduplication:
    """Re-delivered updates (item 3): one row, no exception."""

    async def test_recording_the_same_message_twice_keeps_one_row(self):
        """A repeated ``(chat_id, message_id)`` neither crashes nor doubles."""
        buf = buffer_mod().ChatBuffer()
        await buf.record(CHAT_A, 10, 100, D1, TEXT, None, None)
        await buf.record(CHAT_A, 10, 100, D1, "duplicate", None, None)

        rows = await stored_rows(CHAT_A)

        assert len(rows) == 1, "the (chat_id, message_id) uniqueness dedups re-deliveries"
        assert rows[0].message_id == 10


class TestRecordedRowStructure:
    """The stored row covers every field the cleanup may need."""

    async def test_record_carries_every_field(self):
        """``record`` stores chat/message/user ids, date, text, caption, file id."""
        buf = buffer_mod().ChatBuffer()
        await buf.record(CHAT_A, 10, 100, D1, "hello", "cap", "f-1")

        rows = await stored_rows(CHAT_A)

        assert len(rows) == 1, "one record() call → one row"
        row = rows[0]
        assert row.chat_id == CHAT_A
        assert row.message_id == 10
        assert row.user_id == 100, "the author travels with the row"
        assert row.sent_at == D1.replace(tzinfo=None), (
            "the update timestamp drives the search and is stored as naive UTC"
        )
        assert row.text == "hello"
        assert row.caption == "cap", "the caption is part of the record"
        assert row.file_unique_id == "f-1"
        assert row.created_at is not None, "created_at carries a Python default"


class TestCapacity:
    """The cap: ``ChatBuffer(maxlen=...)`` prunes the OLDEST rows per chat.

    In v3 pruning only ever means «the original is not in the buffer»
    (a skipped delete) — never a wrong match elsewhere.
    """

    async def test_oldest_records_are_pruned_at_the_cap(self):
        """A chat over the cap keeps only the newest ``maxlen`` rows."""
        buf = buffer_mod().ChatBuffer(maxlen=3)
        for offset in range(4):
            await buf.record(CHAT_A, 1 + offset, 100 + offset, D1, f"m{offset}", None, None)

        assert await buf.find_and_take(CHAT_A, D1, "m0", None, None, 100) is None, (
            "the record pruned by the cap must not be found"
        )
        assert await buf.find_and_take(CHAT_A, D1, "m3", None, None, 103) == 4, (
            "the newest record must survive the cap"
        )
        assert await buf.find_and_take(CHAT_A, D1, "m1", None, None, 101) == 2, (
            "records inside the cap are untouched"
        )

    async def test_the_cap_is_per_chat(self):
        """Each chat keeps up to ``maxlen`` rows of its own."""
        buf = buffer_mod().ChatBuffer(maxlen=2)
        await buf.record(CHAT_A, 1, 1, D1, "a1", None, None)
        await buf.record(CHAT_A, 2, 1, D1, "a2", None, None)
        await buf.record(CHAT_A, 3, 1, D1, "a3", None, None)
        await buf.record(CHAT_B, 5, 2, D1, "b1", None, None)

        assert await buf.find_and_take(CHAT_A, D1, "a1", None, None, 1) is None, (
            "chat A is at its cap: its oldest record was pruned"
        )
        assert await buf.find_and_take(CHAT_A, D1, "a3", None, None, 1) == 3
        assert await buf.find_and_take(CHAT_B, D1, "b1", None, None, 2) == 5, (
            "chat B is below the cap: unaffected by chat A's overflow"
        )


class TestBufferApi:
    """The public surface of ``bot.buffer`` pinned against the handlers."""

    def test_the_record_and_lookup_signatures_are_unchanged(self):
        """7-argument ``record``, 6-argument ``find_and_take`` — exactly.

        ``user_id`` IS the sender (the forwarding side derives it from
        the forward origin), and ``sender_id`` is the 6th argument of
        the cleanup lookup (F2) — nothing may be added or reordered.
        """
        record_params = list(inspect.signature(buffer_mod().ChatBuffer.record).parameters)
        assert record_params == [
            "self",
            "chat_id",
            "message_id",
            "user_id",
            "date",
            "text",
            "caption",
            "file_unique_id",
        ], f"record must keep exactly its signature: {record_params!r}"

        lookup_params = list(inspect.signature(buffer_mod().ChatBuffer.find_and_take).parameters)
        assert lookup_params == [
            "self",
            "chat_id",
            "date",
            "text",
            "caption",
            "file_unique_id",
            "sender_id",
        ], f"the cleanup lookup must take the forward-origin sender: {lookup_params!r}"

    def test_record_and_find_and_take_are_coroutine_functions(self):
        """The buffer speaks to the database: both methods must be awaited."""
        chat_buffer = buffer_mod().ChatBuffer

        assert inspect.iscoroutinefunction(chat_buffer.record), (
            "record must be an async method (item 3: writes to the database)"
        )
        assert inspect.iscoroutinefunction(chat_buffer.find_and_take), (
            "find_and_take must be an async method (item 3: reads the database)"
        )

    def test_buffer_is_the_module_singleton(self):
        """``bot.buffer.buffer`` is a ``ChatBuffer()`` — what the handlers use."""
        mod = buffer_mod()

        assert isinstance(mod.buffer, mod.ChatBuffer), (
            "bot.buffer must expose the singleton buffer = ChatBuffer()"
        )

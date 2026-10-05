"""Source-chat buffer tests: module ``bot.buffer`` (BRIEF v3, section 5).

RED phase (iteration D, security review F2): ``find_and_take`` still
takes FIVE arguments and ignores the caption and the sender — every
lookup test below fails with ``TypeError`` (the new 6th argument) or
with a wrong predicate verdict (the ``buffer_mod()`` helper imports the
module inside the tests on purpose: at top level a missing or changed
module would abort pytest collection for the whole suite).

Specification (BRIEF.md v3, section «Что возвращается / удаляется из v2»
and section «Шаг 5. Подчистка основного чата»):
- the buffer is the BASE FOR SEARCHING ORIGINALS of the main (source)
  chat — the bot records every source-chat message it sees live (the
  Bot API history does not exist, so pre-start messages are unfindable);
- ``record(chat_id, message_id, user_id, date, text, caption,
  file_unique_id)`` stores one message: its id, the author, the update
  timestamp (a ``datetime``), the text and/or caption of the message and
  the media's ``file_unique_id`` (``None`` for plain text messages);
- ``find_and_take(chat_id, date, text, caption, file_unique_id,
  sender_id)`` looks the original up and returns its ``message_id`` —
  or ``None``:
  * a match requires an equal ``date``, an equal SENDER — the forward
    origin's author (``sender_user.id`` / ``sender_chat.id``) on the
    lookup side against the record's ``user_id``; either side unknown
    → the sender condition is skipped — AND the payload: a non-empty
    ``text`` must be equal; else a non-empty ``caption`` must be equal;
    else a non-``None`` ``file_unique_id`` must be equal (BRIEF step 5:
    media without text is identified by date + file id);
  * the FIRST match in insertion order wins;
  * a found record is CONSUMED (taken): a repeated lookup of the same
    original returns ``None`` — the cleanup must never issue a second
    ``delete_message`` for one original;
  * lookups are scoped to a single chat: another chat's records are
    invisible, an unknown chat is a plain ``None``;
- the cap behaviour survives: the ``maxlen`` constructor parameter
  bounds how many records a chat keeps and the OLDEST records are
  evicted first (an evicted original is simply not found — in v3 that
  only means "nothing to delete", never a wrong delete);
- the module exposes the shared singleton ``buffer = ChatBuffer()``;
- the stored record covers every field listed above (pinned directly
  against the ``_chats`` layout this module has used since v2);
- the buffer is in-memory only: a restart loses it (BRIEF «Объём v1»),
  which the cleanup reports honestly as ``Deleted 0 of Y``.
"""

import inspect
from datetime import datetime, timezone

#: Two chats that must stay isolated from each other.
CHAT_A = -1001
CHAT_B = -1002

#: Two distinct message timestamps (the ``date`` field of an update).
D1 = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
D2 = datetime(2026, 10, 5, 12, 5, 0, tzinfo=timezone.utc)

#: The text of a plain text message used across the tests.
TEXT = "moderation note"


def buffer_mod():
    """The ``bot.buffer`` module, imported at test time.

    While the v3 API does not exist (RED), every test fails with
    ``ModuleNotFoundError`` on its first helper call. The import lives
    inside this function on purpose: at top level a missing module
    would abort the collection of the whole pytest run and would be
    classified as third-party by ruff, flipping ``I001`` once the file
    exists.
    """
    import bot.buffer as buffer_module

    return buffer_module


def stored_records(buf, chat_id) -> list:
    """The raw records of ``chat_id`` as the buffer keeps them.

    The record STRUCTURE is part of the specification (BRIEF v3 §5:
    «записи: message_id, дата, текст/подпись, file_unique_id»), so one
    test of this file looks inside — against the ``_chats`` layout the
    module has documented since v2 (a chat id mapped to a list of dict
    records).
    """
    return buf._chats[chat_id]


class TestFindAndTake:
    """``find_and_take(chat_id, date, text, caption, file_unique_id, sender_id)``.

    The search contract of the cleanup step (BRIEF v3, step 5): the
    original is located by the forward-origin date plus the SENDER of
    the forward origin plus the payload of the message — the text, the
    caption, or the ``file_unique_id`` of media without both.
    """

    def test_text_message_is_found_by_date_and_text(self):
        """date + non-empty text both equal → the record's ``message_id``."""
        buf = buffer_mod().ChatBuffer()
        buf.record(CHAT_A, 10, 100, D1, TEXT, None, None)

        assert buf.find_and_take(CHAT_A, D1, TEXT, None, None, 100) == 10

    def test_a_found_record_is_consumed(self):
        """The taken original is gone: a repeated lookup must be ``None``.

        The cleanup deletes the original once — a second lookup of the
        same original (a later duplicate forward, a retry) must not
        produce a second ``delete_message``.
        """
        buf = buffer_mod().ChatBuffer()
        buf.record(CHAT_A, 10, 100, D1, TEXT, None, None)

        first = buf.find_and_take(CHAT_A, D1, TEXT, None, None, 100)
        second = buf.find_and_take(CHAT_A, D1, TEXT, None, None, 100)

        assert first == 10, "sanity: the first lookup finds the original"
        assert second is None, "a taken original must never be found again"

    def test_the_first_match_in_insertion_order_wins(self):
        """Two identical originals → the older one first, then the newer."""
        buf = buffer_mod().ChatBuffer()
        buf.record(CHAT_A, 10, 100, D1, TEXT, None, None)
        buf.record(CHAT_A, 11, 100, D1, TEXT, None, None)

        assert buf.find_and_take(CHAT_A, D1, TEXT, None, None, 100) == 10, (
            "insertion order: the record recorded first must be taken first"
        )
        assert buf.find_and_take(CHAT_A, D1, TEXT, None, None, 100) == 11
        assert buf.find_and_take(CHAT_A, D1, TEXT, None, None, 100) is None

    def test_the_date_must_match(self):
        """A record taken with another timestamp is not the original."""
        buf = buffer_mod().ChatBuffer()
        buf.record(CHAT_A, 10, 100, D1, TEXT, None, None)

        assert buf.find_and_take(CHAT_A, D2, TEXT, None, None, 100) is None, (
            "the forward-origin date must equal the recorded update date"
        )

    def test_the_text_must_match(self):
        """Equal date alone is not enough — the text differs."""
        buf = buffer_mod().ChatBuffer()
        buf.record(CHAT_A, 10, 100, D1, TEXT, None, None)

        assert buf.find_and_take(CHAT_A, D1, "a different message", None, None, 100) is None

    def test_media_without_text_is_matched_by_file_unique_id(self):
        """A captioned photo: no text → date + ``file_unique_id``.

        This is the BRIEF step-5 rule «медиа без текста → (дата +
        file_unique_id)»: the caption travels with the record but the
        identity of a media original is its file id.
        """
        buf = buffer_mod().ChatBuffer()
        buf.record(CHAT_A, 10, 100, D1, None, "photo caption", "f-abc123")

        assert buf.find_and_take(CHAT_A, D1, None, None, "f-abc123", 100) == 10

    def test_empty_text_also_falls_back_to_the_file_id(self):
        """``text=""`` counts as «no text» exactly like ``text=None``."""
        buf = buffer_mod().ChatBuffer()
        buf.record(CHAT_A, 10, 100, D1, "", None, "f-abc123")

        assert buf.find_and_take(CHAT_A, D1, "", None, "f-abc123", 100) == 10

    def test_media_match_requires_a_file_id_on_both_sides(self):
        """No file id recorded or queried → the media original is not found."""
        buf = buffer_mod().ChatBuffer()
        buf.record(CHAT_A, 10, 100, D1, None, None, None)

        assert buf.find_and_take(CHAT_A, D1, None, None, None, 100) is None, (
            "a record without a file id cannot be identified as media"
        )
        assert buf.find_and_take(CHAT_A, D1, None, None, "f-abc123", 100) is None, (
            "a file id the bot never recorded is a miss, not a guess"
        )

    def test_a_text_record_is_not_matched_by_file_id_alone(self):
        """A non-empty text switches the lookup to the text branch.

        The file id of a text-carrying record must not identify it:
        otherwise a wrong original could be deleted (security review).
        """
        buf = buffer_mod().ChatBuffer()
        buf.record(CHAT_A, 10, 100, D1, TEXT, None, "f-abc123")

        assert buf.find_and_take(CHAT_A, D1, "other text", None, "f-abc123", 100) is None

    def test_unknown_chat_is_a_miss(self):
        """A chat with no records at all → ``None``, never an error."""
        buf = buffer_mod().ChatBuffer()
        buf.record(CHAT_A, 10, 100, D1, TEXT, None, None)

        assert buf.find_and_take(CHAT_B, D1, TEXT, None, None, 100) is None


class TestChatIsolation:
    """Records of one chat never satisfy a lookup in another chat."""

    def test_identical_payloads_resolve_to_their_own_chat(self):
        """The same (date, text) exists in both chats → each lookup its own id."""
        buf = buffer_mod().ChatBuffer()
        buf.record(CHAT_A, 10, 100, D1, TEXT, None, None)
        buf.record(CHAT_B, 20, 200, D1, TEXT, None, None)

        assert buf.find_and_take(CHAT_A, D1, TEXT, None, None, 100) == 10, (
            "chat A must resolve to chat A's record"
        )
        assert buf.find_and_take(CHAT_B, D1, TEXT, None, None, 200) == 20, (
            "chat B must resolve to chat B's record"
        )


class TestRecordedRecordStructure:
    """The stored record covers every field the cleanup may need."""

    def test_record_carries_every_field(self):
        """``record`` stores message_id, user_id, date, text, caption, file id."""
        buf = buffer_mod().ChatBuffer()
        buf.record(CHAT_A, 10, 100, D1, "hello", "cap", "f-1")

        record = stored_records(buf, CHAT_A)[0]

        assert isinstance(record, dict), "a record is a plain dict (v2 layout)"
        expected_keys = {
            "message_id",
            "user_id",
            "date",
            "text",
            "caption",
            "file_unique_id",
        }
        assert expected_keys <= set(record), (
            f"the record must cover every cleanup field {expected_keys}: {record!r}"
        )
        assert record["message_id"] == 10
        assert record["user_id"] == 100, "the author travels with the record"
        assert record["date"] == D1, "the update timestamp drives the search"
        assert record["text"] == "hello"
        assert record["caption"] == "cap", "the caption is part of the record"
        assert record["file_unique_id"] == "f-1"

    def test_the_record_signature_is_unchanged(self):
        """``record`` keeps its 7-argument signature — ``user_id`` IS the sender.

        The forwarding side derives the sender from the forward origin
        (``sender_user.id`` / ``sender_chat.id``) and the buffer already
        stores it as ``user_id``: nothing new has to be recorded (F2).
        """
        params = list(inspect.signature(buffer_mod().ChatBuffer.record).parameters)

        assert params == [
            "self",
            "chat_id",
            "message_id",
            "user_id",
            "date",
            "text",
            "caption",
            "file_unique_id",
        ], f"record must keep exactly its v3 signature: {params!r}"


class TestCapacity:
    """The cap: ``ChatBuffer(maxlen=...)`` evicts the oldest records.

    In v3 eviction only ever means «the original is not in the buffer»
    (a skipped delete) — never a wrong match elsewhere.
    """

    def test_oldest_records_are_evicted_at_the_cap(self):
        """A chat over the cap keeps only the newest ``maxlen`` records."""
        buf = buffer_mod().ChatBuffer(maxlen=3)
        for offset in range(4):
            buf.record(CHAT_A, 1 + offset, 100 + offset, D1, f"m{offset}", None, None)

        assert buf.find_and_take(CHAT_A, D1, "m0", None, None, 100) is None, (
            "the record evicted by the cap must not be found"
        )
        assert buf.find_and_take(CHAT_A, D1, "m3", None, None, 103) == 4, (
            "the newest record must survive the cap"
        )
        assert buf.find_and_take(CHAT_A, D1, "m1", None, None, 101) == 2, (
            "records inside the cap are untouched"
        )

    def test_the_cap_is_per_chat(self):
        """Each chat keeps up to ``maxlen`` records of its own."""
        buf = buffer_mod().ChatBuffer(maxlen=2)
        buf.record(CHAT_A, 1, 1, D1, "a1", None, None)
        buf.record(CHAT_A, 2, 1, D1, "a2", None, None)
        buf.record(CHAT_A, 3, 1, D1, "a3", None, None)
        buf.record(CHAT_B, 5, 2, D1, "b1", None, None)

        assert buf.find_and_take(CHAT_A, D1, "a1", None, None, 1) is None, (
            "chat A is at its cap: its oldest record is evicted"
        )
        assert buf.find_and_take(CHAT_A, D1, "a3", None, None, 1) == 3
        assert buf.find_and_take(CHAT_B, D1, "b1", None, None, 2) == 5, (
            "chat B is below the cap: unaffected by chat A's overflow"
        )


class TestSingleton:
    """The module-level singleton shared by the handlers."""

    def test_buffer_is_a_chat_buffer_instance(self):
        """``bot.buffer.buffer`` is a ``ChatBuffer()`` — what the handlers use."""
        mod = buffer_mod()

        assert isinstance(mod.buffer, mod.ChatBuffer), (
            "bot.buffer must expose the singleton buffer = ChatBuffer()"
        )


class TestSenderAndCaptionPredicate:
    """The 6-argument ``find_and_take`` predicate (security review F2).

    A date plus a text is not a unique identity: the lookup must pin the
    ORIGINAL. The forward origin carries the sender (``sender_user.id``
    / ``sender_chat.id``), the record carries the author (``user_id``),
    and a captioned media original is identified by its CAPTION — the
    ``file_unique_id`` only stays the fallback when neither a text nor
    a caption is queried (BRIEF step 5). An unknown sender on EITHER
    side skips the sender condition, so lookups of senderless origins
    and records keep behaving exactly as before.
    """

    def test_find_and_take_takes_the_sender_as_the_sixth_argument(self):
        """The signature itself: ``sender_id`` after ``file_unique_id``."""
        params = list(inspect.signature(buffer_mod().ChatBuffer.find_and_take).parameters)

        assert params == [
            "self",
            "chat_id",
            "date",
            "text",
            "caption",
            "file_unique_id",
            "sender_id",
        ], f"the cleanup lookup must take the forward-origin sender: {params!r}"

    def test_equal_sender_finds_the_record(self):
        """Same date, text and author → the original is found (the sender is part of identity)."""
        buf = buffer_mod().ChatBuffer()
        buf.record(CHAT_A, 10, 100, D1, TEXT, None, None)

        assert buf.find_and_take(CHAT_A, D1, TEXT, None, None, 100) == 10

    def test_the_same_date_and_text_of_another_sender_is_a_miss(self):
        """(F2) date + text equal, sender differs → ``None`` — never a wrong delete.

        Another author means another original: two messages of the same
        second with the same text still must not resolve to each other.
        """
        buf = buffer_mod().ChatBuffer()
        buf.record(CHAT_A, 10, 100, D1, TEXT, None, None)

        assert buf.find_and_take(CHAT_A, D1, TEXT, None, None, 200) is None, (
            "a message of another author is a different original"
        )

    def test_an_unknown_sender_on_the_lookup_side_skips_the_condition(self):
        """The forward origin hides its sender → the lookup behaves as before (F2 3v)."""
        buf = buffer_mod().ChatBuffer()
        buf.record(CHAT_A, 10, 100, D1, TEXT, None, None)

        assert buf.find_and_take(CHAT_A, D1, TEXT, None, None, None) == 10

    def test_an_unknown_sender_on_the_record_side_skips_the_condition(self):
        """A record without an author stays findable — the condition needs both sides."""
        buf = buffer_mod().ChatBuffer()
        buf.record(CHAT_A, 10, None, D1, TEXT, None, None)

        assert buf.find_and_take(CHAT_A, D1, TEXT, None, None, 42) == 10

    def test_a_media_original_matches_by_its_caption(self):
        """Caption possession: same caption, DIFFERENT file ids → found and consumed.

        A forwarded photo keeps the caption of the original while the
        forward carries its own ``file_unique_id`` — date + caption
        identifies the original; the file id stays the fallback for
        media without a caption.
        """
        buf = buffer_mod().ChatBuffer()
        buf.record(CHAT_A, 10, 100, D1, None, "photo caption", "f-uploaded")

        assert buf.find_and_take(CHAT_A, D1, None, "photo caption", "f-forward", 100) == 10, (
            "the caption, not the file id, identifies a captioned original"
        )
        assert buf.find_and_take(CHAT_A, D1, None, "photo caption", "f-forward", 100) is None, (
            "the found record is consumed like every other match"
        )

    def test_a_differing_caption_is_a_miss_even_with_equal_file_ids(self):
        """A non-empty caption switches the lookup to the caption branch (over the file id)."""
        buf = buffer_mod().ChatBuffer()
        buf.record(CHAT_A, 10, 100, D1, None, "cap A", "f-same")

        assert buf.find_and_take(CHAT_A, D1, None, "cap B", "f-same", 100) is None, (
            "another caption is another original — the shared file id must not match it"
        )

    def test_a_caption_the_record_never_had_is_a_miss(self):
        """Caption branch against a record without a caption: ``None`` never equals text."""
        buf = buffer_mod().ChatBuffer()
        buf.record(CHAT_A, 10, 100, D1, None, None, "f-abc")

        assert buf.find_and_take(CHAT_A, D1, None, "photo caption", "f-abc", 100) is None

    def test_a_non_empty_text_takes_precedence_over_caption_and_file(self):
        """Text branch first: a differing text misses even with equal caption + file id."""
        buf = buffer_mod().ChatBuffer()
        buf.record(CHAT_A, 10, 100, D1, TEXT, "photo caption", "f-abc")

        assert (
            buf.find_and_take(CHAT_A, D1, "other text", "photo caption", "f-abc", 100) is None
        )

    def test_an_equal_text_wins_over_a_differing_caption(self):
        """Text branch first: the caption and the file id do not participate at all."""
        buf = buffer_mod().ChatBuffer()
        buf.record(CHAT_A, 10, 100, D1, TEXT, "photo caption", "f-abc")

        assert buf.find_and_take(CHAT_A, D1, TEXT, "another caption", "f-other", 100) == 10

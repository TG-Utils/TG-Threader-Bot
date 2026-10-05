"""Buffering router tests: module ``bot.handlers.buffering`` (BRIEF v3, §5).

The v3 buffering router is the ONLY observer of the source (main) chat:
it records every message the bot sees live so the step-5 cleanup can
find originals by (date, text/file id). The v2 command filter and the
``window``-based buffer are gone with the v2 flow.

Specification (BRIEF.md v3, sections «Шаг 5», «Что возвращается /
удаляется из v2»):
- module-level ``router`` with the async handler ``on_message``;
- the filter admits messages of a configured SOURCE chat (``is_source``
  of the pair registry) and refuses everything else — threaded chats
  and unknown chats are ignored SILENTLY: nothing recorded, nothing
  replied, no API call;
- recording is ``buffer.record(chat_id, message_id, user_id, date,
  text, caption, file_unique_id)`` keyed by the chat's INTEGER id —
  the payload the cleanup matches against (a media message keeps its
  ``file_unique_id``, a text message its ``text``);
- a message WITHOUT ``from_user`` (anonymous admin, channel post) is
  ignored silently: nothing recorded, no ``AttributeError`` escapes
  (security review L3);
- commands are no longer a special case: with the command routers
  deleted, a ``/…`` text of the source chat is an ordinary message and
  IS buffered (``/cancel`` only ever means something inside a pending
  session of a threaded chat, which the watcher consumes first).

RED phase (security review F2): every ``find_and_take`` call now takes
the forward-origin sender as its 6th argument (``sender_id``) — the
lookup contract fails with ``TypeError`` until the predicate grows the
sender condition; the kept filter/record tests stay green.
"""

import inspect
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Router

#: The registry pair the autouse fixture writes into the tmp cwd.
SOURCE_PAIRS = [{"source": "@sourcename", "target": "@forumgroup"}]

#: The configured source chat of the behaviour tests (username key).
CHAT_ID = -100888
USERNAME = "sourcename"

#: The configured target chat of that same pair (must NOT be buffered).
TARGET_CHAT_ID = -100700
TARGET_USERNAME = "forumgroup"

#: A source chat configured by integer id only (no username).
INT_CHAT_ID = -100999

#: A chat that is not in the registry at all.
UNKNOWN_CHAT_ID = -100555

#: The update timestamp of every message of the behaviour tests.
DATE = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def configured_cwd(tmp_path: Path, monkeypatch):
    """Run every test in an empty cwd holding a valid ``chats.json``.

    The shared buffer singleton is emptied too — it persists across
    tests by design. Pure file/import operations on purpose: while the
    new buffering API does not exist (RED), this fixture must not fail
    on module imports of the implementation under test beyond
    ``bot.buffer``, which exists in every phase.
    """
    monkeypatch.chdir(tmp_path)
    Path.cwd().joinpath("chats.json").write_text(
        json.dumps({"pairs": SOURCE_PAIRS}), encoding="utf-8"
    )
    import bot.buffer as buffer_module

    buffer_module.buffer._chats.clear()


def buffering_mod():
    """The ``bot.handlers.buffering`` module, imported at test time.

    The import lives inside this function on purpose: at top level a
    missing module would abort the collection of the whole pytest run
    and would be classified as third-party by ruff, flipping ``I001``
    once the file exists.
    """
    import bot.handlers.buffering as buffering_module

    return buffering_module


def buffering_handler():
    """The ``HandlerObject`` of ``on_message`` as registered on the router."""
    for handler in buffering_mod().router.message.handlers:
        if getattr(handler.callback, "__name__", None) == "on_message":
            return handler
    raise AssertionError("on_message must be registered on router.message.handlers")


async def dispatch(message) -> bool:
    """Run ``message`` through the buffering handler the way aiogram would."""
    handler = buffering_handler()
    passed, _ = await handler.check(message)
    if not passed:
        return False
    await buffering_mod().on_message(message)
    return True


def make_message(
    text="hello everyone",
    chat_id=CHAT_ID,
    username=USERNAME,
    message_id=5,
    user_id=42,
    date=DATE,
    caption=None,
    file_unique_id=None,
):
    """A minimal ``Message``-shaped object for the buffering handler.

    ``user_id=None`` builds a message WITHOUT ``from_user``; a non-``None``
    ``file_unique_id`` makes the message a media one (photo attached,
    carrying ``caption``).
    """
    message = SimpleNamespace(
        text=text,
        caption=caption,
        date=date,
        chat=SimpleNamespace(id=chat_id, username=username, type="supergroup"),
        from_user=None if user_id is None else SimpleNamespace(id=user_id),
        message_id=message_id,
        answer=AsyncMock(name="answer"),
    )
    if file_unique_id is not None:
        message.photo = [SimpleNamespace(file_id="file-x", file_unique_id=file_unique_id)]
        message.content_type = "photo"
    else:
        message.content_type = "text"
    return message


class TestBufferingRouter:
    """The router and the handler of ``bot.handlers.buffering``."""

    def test_router_is_an_aiogram_router(self):
        """``router`` is an ``aiogram.Router`` instance."""
        assert isinstance(buffering_mod().router, Router), (
            "the module router must be a Router instance"
        )

    def test_on_message_is_an_async_function(self):
        """The handler is declared ``async def`` — aiogram awaits a coroutine."""
        assert inspect.iscoroutinefunction(buffering_mod().on_message), (
            "on_message must be an async handler"
        )

    def test_on_message_is_registered_on_the_router(self):
        """``on_message`` sits in ``router.message.handlers`` — else it is unreachable."""
        on_message = buffering_mod().on_message
        callbacks = [handler.callback for handler in buffering_mod().router.message.handlers]

        assert any(
            callback is on_message
            or getattr(callback, "__name__", None) == "on_message"
            for callback in callbacks
        ), "on_message must be registered on router.message.handlers"


class TestSourceChatFilter:
    """Only configured source chats reach the buffer (the new filter)."""

    async def test_source_chat_message_is_recorded(self):
        """A message of the configured source chat lands in the buffer of that chat."""
        message = make_message()

        delivered = await dispatch(message)

        assert delivered is True, "a source-chat message must pass the filter"
        from bot.buffer import buffer

        assert buffer.find_and_take(CHAT_ID, DATE, "hello everyone", None, None, 42) == 5, (
            "the handler must record(date, text, …) of the incoming message"
        )

    async def test_source_chat_keyed_by_integer_id_is_recorded(self):
        """A registry pair keyed by int id buffers its chat like a username one."""
        Path("chats.json").write_text(
            json.dumps({"pairs": [{"source": INT_CHAT_ID, "target": "@forumgroup"}]}),
            encoding="utf-8",
        )
        message = make_message(chat_id=INT_CHAT_ID, username=None)

        delivered = await dispatch(message)

        assert delivered is True
        from bot.buffer import buffer

        assert buffer.find_and_take(INT_CHAT_ID, DATE, "hello everyone", None, None, 42) == 5

    async def test_command_texts_are_recorded_too(self):
        """No command router exists anymore: ``/cancel`` in the source chat is a message.

        The v2 filter rejected every ``/…`` text for the command router
        — in v3 the watcher only consumes texts while a session is
        pending (threaded chats); in the source chat such a text is
        ordinary flood material and must be buffered.
        """
        message = make_message(text="/cancel")

        delivered = await dispatch(message)

        assert delivered is True, "a leading slash must not disqualify the message"
        from bot.buffer import buffer

        assert buffer.find_and_take(CHAT_ID, DATE, "/cancel", None, None, 42) == 5, (
            "the /cancel-shaped text must be recorded like any other message"
        )

    async def test_threaded_chat_is_not_recorded(self):
        """The configured TARGET chat is not a source → refused by the filter, silently."""
        message = make_message(chat_id=TARGET_CHAT_ID, username=TARGET_USERNAME)

        delivered = await dispatch(message)

        assert delivered is False, (
            "the filter must refuse a threaded chat before the handler runs"
        )
        from bot.buffer import buffer

        assert buffer.find_and_take(TARGET_CHAT_ID, DATE, "hello everyone", None, None, 42) is None
        assert message.answer.await_count == 0, "the refusal is silent"

    async def test_unconfigured_chat_is_not_recorded(self):
        """A chat from no pair at all → refused by the filter, silently."""
        message = make_message(chat_id=UNKNOWN_CHAT_ID, username="stranger")

        delivered = await dispatch(message)

        assert delivered is False, "an unconfigured chat must not pass the filter"
        from bot.buffer import buffer

        assert buffer.find_and_take(UNKNOWN_CHAT_ID, DATE, "hello everyone", None, None, 42) is None
        assert message.answer.await_count == 0


class TestRecordedPayload:
    """What the buffer must know about each source-chat message (step 5)."""

    async def test_media_message_is_findable_by_file_unique_id(self):
        """A photo without text is identified by date + file id (BRIEF step 5)."""
        message = make_message(text=None, caption="photo cap", file_unique_id="ph-1")

        await dispatch(message)

        from bot.buffer import buffer

        assert buffer.find_and_take(CHAT_ID, DATE, None, None, "ph-1", 42) == 5, (
            "the handler must record the media's file_unique_id"
        )

    async def test_the_record_carries_every_payload_field(self):
        """The stored record covers message_id, user_id, date, text, caption, file id."""
        message = make_message(text=None, caption="photo cap", file_unique_id="ph-9",
                               user_id=77)

        await dispatch(message)

        from bot.buffer import buffer

        record = buffer._chats[CHAT_ID][0]
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
        assert record["message_id"] == 5
        assert record["user_id"] == 77, "the author travels with the record"
        assert record["date"] == DATE, "the update timestamp drives the search"
        assert record["caption"] == "photo cap"
        assert record["file_unique_id"] == "ph-9"


class TestSenderlessMessage:
    """A message without ``from_user`` is ignored silently (L3)."""

    async def test_message_without_from_user_is_not_recorded(self):
        """No sender id → nothing recorded, no reply, no ``AttributeError``."""
        message = make_message(user_id=None)

        delivered = await dispatch(message)

        assert delivered is True, "a configured source chat still reaches the handler"
        from bot.buffer import buffer

        assert not buffer._chats.get(CHAT_ID), (
            "a message without a sender must not be recorded into the buffer"
        )
        assert message.answer.await_count == 0, "the ignore must be silent"

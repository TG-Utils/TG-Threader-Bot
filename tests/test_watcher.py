"""Watcher router tests: module ``bot.handlers.watcher`` (BRIEF v3, §3).

RED phase (iteration C): ``bot.handlers.watcher`` does not exist yet —
every test here fails with ``ModuleNotFoundError`` (the helpers import
the module inside the tests on purpose: at top level a missing module
would abort pytest collection for the whole suite and would flip ruff's
``I001`` verdict once the file exists).

Specification (BRIEF.md v3, steps 1–5 and «Отказы и лимиты»):
- module-level ``router = Router()`` with exactly two message handlers,
  registered in this order:
  1. the forward handler ``on_forward`` — filter: the message carries a
     ``forward_origin`` AND the chat is a configured TARGET chat of the
     registry; a sender that is ``None`` or not a chat admin
     (``get_chat_member`` → outside ``creator``/``administrator``) exits
     silently BEFORE any session exists (the admin check runs first);
     * no session → ``start`` one with the batch's first message and
       ask ONE question as a reply to that first message
       (``reply_to_message_id``): the origin's chat resolves a pair of
       THIS chat → ``Thread title? Send the title as a plain message.``
       (stage ``asking_title``, source pair fixed from the origin),
       otherwise → ``Which chat did you forward from? Reply with
       @username or its id.`` (stage ``asking_source``);
     * session already pending → the forward JOINS the batch — unless
       its origin identifies a DIFFERENT configured pair («конфликт
       origin-ов → вопрос», one pair per batch): the fixed pair is
       dropped (``session.source = None``), the stage returns to
       ``asking_source`` and ONE NEW question message is asked as a
       reply to the FIRST batch message (a new ``prompt_id``);
     * the 100-message cap applies while ACCUMULATING: an append that
       would exceed it answers exactly ``Batch too large ({n} messages,
       limit 100). Nothing was moved.`` (the REAL would-be n) with
       ``parse_mode="HTML"``, deletes the question messages and resets
       the session — the refused forward is NOT appended;
  2. the text handler ``on_text`` — filter: a session exists in this
     chat (router order makes a forwarded-with-text message consume the
     forward handler first); an admin-only state machine:
     * sender ``None``/not admin → ignored, the session stays alive;
     * the admin's ``/cancel`` (both stages) → the bot deletes all of
       the session's question messages and answers exactly
       ``Cancelled.`` — the session resets;
     * ``asking_source`` → ``pair_for_source_ref``: found AND the pair
       targets the CURRENT chat → fix the source pair, ask the title
       question (stage ``asking_title``); a pair of ANOTHER chat reads
       as «не настроено» → not found;
       not found → exactly ``This chat is not configured as a source
       chat.``, question messages deleted, session reset (BRIEF step 2:
       «не настроено → СТОП»);
     * ``asking_title`` → an empty title (after ``strip()``) answers
       exactly ``Title is empty — send the thread title.`` and waits;
       a non-empty title EXECUTES.
- every reply goes out with ``parse_mode="HTML"``;
- execution, in order (a failure in steps 2–5 answers exactly
  ``Move failed: {Type}. Check the target chat manually.``, deletes the
  question messages, resets the session and performs NO source cleanup):
  1. guards: batch > 100 → exactly ``Batch too large ({n} messages,
     limit 100). Nothing was moved.`` (the REAL n) with the question
     messages deleted, session reset and no chat mutation at all; then
     ``build_thread_url(target, first.message_id)`` raising → exactly
     ``Invalid target chat in configuration: {target}. Nothing was
     moved.`` with a reset and no mutations;
  2. ``send_message(target, header)`` — ``Topic: <b>{title}</b>``
     (title HTML-escaped and truncated to 128 chars);
  3. ``edit_message_text`` of that same message adds
     ``Please use <a href="{thread_url}">this link</a> to respond to
     this thread.``;
  4. the batch is placed as replies to the header (flat chain, batch
     order): consecutive elements sharing one ``media_group_id`` → a
     single ``send_media_group`` (chunks ≤ 10) with
     ``reply_parameters`` pointing at the header and ``InputMedia``
     built from the original's ``file_id``/``caption``/
     ``caption_entities``; every other element →
     ``copy_message(chat_id=target, from_chat_id=target, message_id,
     reply_to_message_id=header)``; a raising/unsupported media group
     falls back to per-element copies and the flow continues;
  5. ``delete_message(target, mid)`` for EVERY batch element (the
     forwarded copies);
  6. source cleanup per element (y = batch size): an origin that
     resolves to the pair FIXED ON THE SESSION (``session.source``, a
     pair of another chat never qualifies) → ``delete_message(source,
     origin.message_id)`` WITHOUT consulting the buffer; any other
     origin (an unconfigured one, a foreign configured pair) →
     ``await buffer.find_and_take(source, origin.date, text, caption,
     file_unique_id, sender_id)`` — the sender taken from the forward
     origin (``sender_user.id`` / ``sender_chat.id``, unknown →
     ``None``) — and delete the found id, or skip; a raising
     ``delete_message`` counts as «not found» and never breaks the flow;
  7. all question message ids are deleted;
  8. ONE answer of exactly two lines:
     ``Thread created: {n} message(s). {thread_url}`` /
     ``Deleted {x} of {y} original messages.`` (thread URL escaped via
     render/html-escape — L2);
  9. the session resets.

Before anything is built, ``session.source`` is re-validated: the pair
must still exist in the registry AND target the chat the flow runs in —
otherwise exactly ``This chat is not configured as a source chat.``,
prompts deleted, session reset, ZERO chat mutations (F4).

The bot responses are English literals written here on purpose (the
tests import nothing from ``bot/`` for them).

Security review 2 (RED part 2) additionally pins: no orphan session
after a crashing question or final answer (F3), per-chat serialization
of concurrent admins (F6), the HTML-ESCAPED config target inside the
invalid-target refusal (F7), non-text messages and ``/cancel@botname``
(F8), media extraction of every kind (F9) and best-effort deletion of
the forwarded copies — that step sits OUTSIDE the ``Move failed``
failure block (F10).

Placement order (RED, «перемешанные форварды»): aiogram handles the
updates of a batch CONCURRENTLY, so ``session.messages`` reflects the
ARRIVAL order of the forwards, which may be anything (a live run placed
a batch arriving 10:05 → 10:00 → 10:02 scrambled in the thread). Step 4
must therefore place the chain sorted by ``origin.date`` ASCENDING —
ties broken by ``origin.message_id`` (a stable order that keeps
same-date members, album members above all, adjacent), ``date is None``
reading as EARLIER than every dated ref and ``message_id is None``
never breaking the sort — instead of walking the refs in arrival order.
Pinned by ``TestPlacementFollowsOriginDate`` (chain order, equal-date
stability, missing dates) and ``TestAlbumGluedAfterChronologicalSort``
(an interleaved album survives the sort as ONE ``send_media_group``).

Inline source picker (the requested inline buttons that choose a chat
by its TITLE): the «Which chat did you forward from?» question — BOTH
call sites in ``on_forward`` — arrives with an inline keyboard: one
button per registry source whose pair targets the CURRENT chat (the
same ``pair_targets_chat`` filter, registry order, duplicates by
canonical ref dropped at their FIRST occurrence), the label resolved
through ``bot.get_chat`` (``.title`` → ``.username`` rendered as
``@name`` → the ref itself, a RAISING ``get_chat`` reading as the ref
too), ``callback_data = "w:src:" + str(ref)`` (an int id or an
``@username``), and a trailing ``Cancel`` button
(``settings.cancel_button``, the literal ``Cancel``) with
``callback_data="w:cancel"``. The title question stays a plain prompt,
and a registry that names no source of this chat asks WITHOUT a
keyboard (the status quo — that test is GREEN in RED). The router
additionally registers callback handlers (dispatched by
``dispatch_callback`` below, mirroring ``tests/test_settings.py``):

- ``w:src:<ref>`` at ``asking_source`` with a live session re-runs
  exactly the text-answer path: an admin gate on
  ``callback.message.chat`` (a ``from_user`` of ``None`` or a
  non-admin changes NOTHING), then ``pair_for_source_ref`` +
  ``pair_targets_chat`` — a hit fixes the pair, moves to
  ``asking_title`` and asks the title question (announced by the next
  question, never by an extra answer); a miss runs the TERMINAL
  refusal ``not_configured`` (prompts deleted, session reset). During
  ``asking_title`` — and without a session — the click is ignored: a
  TEXT there would become the title, a button must not;
- ``w:cancel`` mirrors ``/cancel``: prompts deleted, exactly
  ``Cancelled.``, session reset (any stage, admin only), ignored
  without a session;
- every consumed ``w:*`` branch ends in an EMPTY
  ``callback.answer()`` (the spinner pin, no alert text).

Pinned by ``TestSourceQuestionKeyboard``, ``TestSourcePickerCallback``
and ``TestCancelCallback``.
"""

import asyncio
import html
import inspect
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select

# --- chats of the fixtures -------------------------------------------------

#: The threaded (target) chat the forwards arrive in.
TARGET_CHAT_ID = -100666
TARGET_USERNAME = "forumgroup"
#: The registry key of that target chat (a pair seeded by the fixture).
TARGET_REF = f"@{TARGET_USERNAME}"

#: A second configured threaded chat (the session-isolation tests).
OTHER_TARGET_ID = -100777
OTHER_TARGET_USERNAME = "othergroup"

#: The configured source (main) chat — keyed by its private id.
SOURCE_ID = -100111
SOURCE_USERNAME = "srcgroup"

#: A group that exists but is in no pair (foreign origins).
STRANGER_ID = -100999

#: A SECOND configured source pair: another source chat pointing at the
#: SAME threaded chat — a batch whose origins span two configured pairs (F1).
SECOND_SOURCE_ID = -100333
SECOND_SOURCE_USERNAME = "seconds"

#: A ref of the OTHER configured threaded chat (pairs that must not
#: resolve as the source pair of ``TARGET_CHAT_ID`` — F4).
OTHER_TARGET_REF = f"@{OTHER_TARGET_USERNAME}"

#: A source used only to make ``TARGET_REF`` configurable in the F4
#: two-targets answer test (that pair never names this chat's source).
THIRD_SOURCE_ID = -100444

#: The admin running the flow, and another admin of the target chat.
ADMIN_ID = 501
OTHER_ADMIN_ID = 707

#: The registry of every fixture-driven test unless a test rewrites it.
PAIRS = [{"source": SOURCE_ID, "target": TARGET_REF}]

# --- fixed ids -----------------------------------------------------

#: ``message_id`` the header gets in the target chat (send mock).
HEADER_ID = 777
#: ``message_id`` the first question message gets (send mock).
PROMPT_ID = 9001

# --- timestamps -------------------------------------------------------------
D1 = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
D2 = datetime(2026, 10, 5, 12, 30, 0, tzinfo=timezone.utc)
D3 = datetime(2026, 10, 5, 13, 0, 0, tzinfo=timezone.utc)

#: Forward-origin dates of the placement-order tests (ascending): a batch
#: may ARRIVE in any order — aiogram processes updates concurrently —
#: yet the chain has to be placed by THESE, ascending.
D1000 = datetime(2026, 10, 5, 10, 0, 0, tzinfo=timezone.utc)
D1002 = datetime(2026, 10, 5, 10, 2, 0, tzinfo=timezone.utc)
D1005 = datetime(2026, 10, 5, 10, 5, 0, tzinfo=timezone.utc)

# --- bot literals ----------------------------------------------------------
TITLE = "Weather discussion"
QUESTION_SOURCE = "Which chat did you forward from? Reply with @username or its id."
QUESTION_TITLE = "Thread title? Send the title as a plain message."
CANCELLED_REPLY = "Cancelled."
NOT_CONFIGURED_REPLY = "This chat is not configured as a source chat."
EMPTY_TITLE_REPLY = "Title is empty — send the thread title."
#: The bot's own username: ``/cancel@{BOT_USERNAME}`` is ``/cancel`` too (F8).
BOT_USERNAME = "vladimirzhrobot"
BATCH_LIMIT = 100
BATCH_TOO_LARGE_TEMPLATE = (
    "Batch too large ({size} messages, limit {limit}). Nothing was moved."
)
INVALID_TARGET_TEMPLATE = "Invalid target chat in configuration: {target}. Nothing was moved."
MOVE_FAILED_TEMPLATE = "Move failed: {error}. Check the target chat manually."
#: The header at step 2 (title already HTML-escaped by the caller).
HEADER_TEMPLATE = "Topic: <b>{title}</b>"
#: The line step 3 adds to the header.
THREAD_LINK_LINE = 'Please use <a href="{thread_url}">this link</a> to respond to this thread.'

#: The thread link of the header message in the target chat.
THREAD_URL = f"https://t.me/{TARGET_USERNAME}/{HEADER_ID}?thread={HEADER_ID}"


def created_reply(count: int, deleted: int, total: int, url: str = THREAD_URL) -> str:
    """The fixed two-line success answer (BRIEF step 4.6).

    Line 1: how many batch messages became the thread + the thread URL;
    line 2: how many of the ``total`` originals the cleanup found (BRIEF
    «частичный успех — норма»).
    """
    return (
        f"Thread created: {count} message(s). {url}\n"
        f"Deleted {deleted} of {total} original messages."
    )


#: The success answer of the three-message flow of the order test.
CREATED_REPLY = created_reply(3, 3, 3)


# --- helpers: modules ------------------------------------------------------
def watcher_mod():
    """The ``bot.handlers.watcher`` module, imported at test time."""
    import bot.handlers.watcher as watcher_module

    return watcher_module


def sessions_mod():
    """The ``bot.sessions`` module, imported at test time."""
    import bot.sessions as sessions_module

    return sessions_module


async def set_pairs(pairs) -> None:
    """Replace the pair registry with ``pairs`` (the database is the source of truth).

    Cycle A: the pairs live in the ``pairs`` table, so a registry
    rewrite is a DELETE of whatever rows stand there followed by
    ``add_pair`` inserts (each of them refreshes the cache). ``Pair``
    is imported lazily so a missing model fails inside the seeding,
    never at collection.
    """
    import bot.chats as chats_module
    from bot.database import session
    from bot.models import Pair

    async with session() as db:
        for row in (await db.execute(select(Pair))).scalars():
            await db.delete(row)
    for pair in pairs:
        added = await chats_module.chats.add_pair(pair["source"], pair["target"])
        assert added is True, f"seeding the pair {pair!r} must be accepted, got {added!r}"


@pytest.fixture(autouse=True)
async def configured_cwd(tmp_path, monkeypatch, fresh_database):
    """Run every test in a tmp cwd with the ``PAIRS`` registry seeded.

    Cycle A: the registry is database-backed — seeding is the async
    ``set_pairs`` (DELETE + ``add_pair``, which refreshes the cache);
    the cwd only keeps the test isolated from the repository files.
    The shared pending-session store is reset too: it persists across
    tests by design — the handlers use it. The DATABASE buffer needs
    no reset here: the suite-wide ``fresh_database`` fixture of
    ``tests/conftest.py`` hands every test a fresh in-memory database
    (and reloads the pair cache for it).
    """
    monkeypatch.chdir(tmp_path)
    await set_pairs(PAIRS)
    try:
        import bot.sessions as sessions_module
    except ModuleNotFoundError:
        sessions_module = None
    if sessions_module is not None:
        for chat_id in (TARGET_CHAT_ID, OTHER_TARGET_ID):
            sessions_module.sessions.reset(chat_id)


# --- helpers: bot api mocks ------------------------------------------------
def make_bot(status="administrator", *, chats=None):
    """A message bot mock with the admin gate pre-configured.

    ``get_chat`` (the label source of the inline-keyboard buttons)
    answers from the optional ``chats`` map — ``{str(ref): chat object
    | Exception}`` — and RAISES for anything else: a chat the map does
    not know must fall back to the ref itself, and an auto-created
    mock attribute would never pass aiogram's string validation anyway.
    An ``Exception`` value is raised as-is (the explicit error case of
    the label fallback chain).
    """
    bot = AsyncMock(name="bot")
    bot.get_chat_member.return_value = SimpleNamespace(status=status)
    bot.known_chats = dict(chats or {})

    def _get_chat(*args, **kwargs):
        ref = args[0] if args else kwargs.get("chat_id")
        value = bot.known_chats.get(str(ref))
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise TelegramBadRequest(method=None, message="chat not found")
        return value

    bot.get_chat.side_effect = _get_chat
    return bot


def question_sender():
    """A ``send_message`` side effect with meaningful ids.

    The header (identified by its topic line) gets ``HEADER_ID``;
    question messages get 9001, 9002, … in call order.
    """
    state = {"next": PROMPT_ID}

    def side_effect(*args, **kwargs):
        text = kwargs.get("text", args[1] if len(args) > 1 else "")
        if isinstance(text, str) and text.startswith("Topic:"):
            return SimpleNamespace(message_id=HEADER_ID)
        current = state["next"]
        state["next"] += 1
        return SimpleNamespace(message_id=current)

    return side_effect


def arg(mock_call, name, position=None):
    """Value of a mocked call argument: keyword first, then positional."""
    if name in mock_call.kwargs:
        return mock_call.kwargs[name]
    if position is not None and len(mock_call.args) > position:
        return mock_call.args[position]
    raise AssertionError(f"missing {name!r} argument in {mock_call!r}")


def reply_text(message) -> str:
    """Text of the first ``message.answer`` reply of ``message``."""
    assert message.answer.await_count, "the handler must reply through message.answer"
    call = message.answer.await_args
    if call.args:
        return call.args[0]
    return call.kwargs.get("text", "")


def media_field(item, name):
    """A field of an ``InputMedia`` item — aiogram object or plain dict."""
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def reply_message_id(params):
    """The ``message_id`` a ``reply_parameters`` payload points at."""
    if isinstance(params, dict):
        return params.get("message_id")
    return getattr(params, "message_id", None)


def keyboard_buttons(markup):
    """``(text, callback_data)`` of every button of ``markup``, row by row.

    Row-major order is the order the spec pins for the pickers (the
    registry order of the chat buttons, ``Cancel`` last) — a set of
    pairs could not express either.
    """
    assert markup is not None, "the inline keyboard must be attached to the question"
    rows = markup["inline_keyboard"] if isinstance(markup, dict) else markup.inline_keyboard
    buttons = []
    for row in rows:
        for button in row:
            if isinstance(button, dict):
                buttons.append((button.get("text"), button.get("callback_data")))
            else:
                buttons.append((button.text, button.callback_data))
    return buttons


def assert_spinner_cleared(callback) -> None:
    """Every consumed ``w:*`` branch must end in an EMPTY ``callback.answer()``.

    The spinner of the pressed button has to stop even when the branch
    sends no visible text (the ignore cases); the pin is the call
    itself, without any alert text.
    """
    assert callback.answer.await_count, "the click must clear the loading spinner"
    call = callback.answer.await_args
    assert not call.args and call.kwargs.get("text") is None, (
        f"callback.answer() must be empty — no alert text: {call!r}"
    )


# --- helpers: messages -----------------------------------------------------
def channel_origin(date=D1, chat_id=SOURCE_ID, username=SOURCE_USERNAME, message_id=501):
    """A ``MessageOriginChannel``-shaped forward origin (type, chat, id)."""
    return SimpleNamespace(
        type="channel",
        date=date,
        chat=SimpleNamespace(id=chat_id, username=username, type="channel"),
        message_id=message_id,
        sender_chat=None,
        sender_user=None,
    )


def chat_origin(date=D1, chat_id=SOURCE_ID, username=SOURCE_USERNAME):
    """A ``MessageOriginChat``-shaped forward origin (group forward, no id)."""
    return SimpleNamespace(
        type="chat",
        date=date,
        sender_chat=SimpleNamespace(id=chat_id, username=username, type="supergroup"),
        sender_user=None,
        chat=None,
        message_id=None,
    )


def user_origin(date=D3, sender_user_id=OTHER_ADMIN_ID):
    """A ``MessageOriginUser``-shaped origin: no chat visible → ask (step 2)."""
    return SimpleNamespace(
        type="user",
        date=date,
        sender_user=SimpleNamespace(id=sender_user_id),
        chat=None,
        sender_chat=None,
        message_id=None,
    )


def make_forward(
    message_id,
    origin,
    *,
    bot=None,
    text=None,
    caption=None,
    file_unique_id=None,
    file_id=None,
    kind="photo",
    media_group_id=None,
    caption_entities=None,
    chat_id=TARGET_CHAT_ID,
    username=TARGET_USERNAME,
    user_id=ADMIN_ID,
    date=D2,
):
    """A forwarded ``Message``-shaped object as it arrives in the target chat.

    ``kind`` names the media attribute that carries ``file_id``/``file_unique_id``
    (``photo`` — a list of sizes, the default; any other kind sets that one
    attribute and the matching ``content_type``), so the watcher's media
    extraction can be pinned for every kind Telegram delivers (F9).
    """
    message = SimpleNamespace(
        message_id=message_id,
        chat=SimpleNamespace(id=chat_id, username=username, type="supergroup"),
        from_user=None if user_id is None else SimpleNamespace(id=user_id),
        forward_origin=origin,
        text=text,
        caption=caption,
        caption_entities=caption_entities,
        media_group_id=media_group_id,
        date=date,
        reply_to_message=None,
        bot=bot if bot is not None else make_bot(),
        answer=AsyncMock(name="answer"),
    )
    if kind == "photo":
        if file_id is not None:
            message.photo = [SimpleNamespace(file_id=file_id, file_unique_id=file_unique_id)]
            message.content_type = "photo"
        else:
            message.content_type = "text"
    else:
        setattr(message, kind, SimpleNamespace(file_id=file_id, file_unique_id=file_unique_id))
        message.content_type = kind
    return message


def make_admin_text(
    text,
    *,
    bot=None,
    user_id=ADMIN_ID,
    chat_id=TARGET_CHAT_ID,
    username=TARGET_USERNAME,
    message_id=501,
):
    """A plain (non-forward) admin message: titles, ``/cancel``, answers."""
    return SimpleNamespace(
        text=text,
        message_id=message_id,
        chat=SimpleNamespace(id=chat_id, username=username, type="supergroup"),
        from_user=None if user_id is None else SimpleNamespace(id=user_id),
        forward_origin=None,
        caption=None,
        date=D2,
        reply_to_message=None,
        bot=bot if bot is not None else make_bot(),
        answer=AsyncMock(name="answer"),
    )


def make_watcher_callback(
    data,
    *,
    bot=None,
    user_id=ADMIN_ID,
    chat_id=TARGET_CHAT_ID,
    username=TARGET_USERNAME,
):
    """A ``CallbackQuery``-shaped click on the prompt's inline keyboard.

    The message it carries is the QUESTION message the keyboard is
    attached to (id ``PROMPT_ID``), so the refusal/cancel branches can
    answer and delete prompts exactly like the text path does.
    """
    bot = bot if bot is not None else make_bot()
    message = SimpleNamespace(
        message_id=PROMPT_ID,
        chat=SimpleNamespace(id=chat_id, username=username, type="supergroup"),
        from_user=None if user_id is None else SimpleNamespace(id=user_id),
        text=QUESTION_SOURCE,
        bot=bot,
        answer=AsyncMock(name="answer"),
    )
    return SimpleNamespace(
        data=data,
        from_user=None if user_id is None else SimpleNamespace(id=user_id),
        bot=bot,
        message=message,
        answer=AsyncMock(name="answer"),
    )


def session_ref(message_id=11, origin_message_id=501, date=D1, text="one"):
    """A batch ref for seeding a session directly through ``bot.sessions``."""
    return {
        "message_id": message_id,
        "origin": {
            "type": "channel",
            "chat_id": SOURCE_ID,
            "username": SOURCE_USERNAME,
            "message_id": origin_message_id,
            "date": date,
        },
        "text": text,
        "caption": None,
        "file_unique_id": None,
    }


# --- helpers: dispatch -----------------------------------------------------
def watcher_handler(name):
    """The ``HandlerObject`` of ``name`` as registered on the watcher router."""
    for handler in watcher_mod().router.message.handlers:
        if getattr(handler.callback, "__name__", None) == name:
            return handler
    raise AssertionError(f"{name} must be registered on router.message.handlers")


async def dispatch(message):
    """Run ``message`` through the watcher router in registration order.

    This mirrors aiogram's semantics: handlers are checked one by one
    and the first whose filters pass consumes the event — which is what
    pins the forward-before-text order of the module router.
    """
    watcher = watcher_mod()
    for handler in watcher.router.message.handlers:
        passed, _ = await handler.check(message)
        if passed:
            await handler.callback(message)
            return handler
    return None


async def dispatch_callback(callback):
    """Run ``callback`` through the watcher router's callback handlers.

    Mirrors ``dispatch`` above and ``dispatch_callback`` of
    ``tests/test_settings.py``: the ``callback_query`` handlers are
    checked in registration order and the first whose filters pass
    consumes the event — ``None`` means no callback handler of this
    router intercepted the click at all.
    """
    for handler in watcher_mod().router.callback_query.handlers:
        passed, _ = await handler.check(callback)
        if passed:
            await handler.callback(callback)
            return handler
    return None


def seed_asking_source(prompt_id=PROMPT_ID):
    """A live batch waiting at the source question, seeded WITHOUT a forward.

    The forward handler runs its own admin gate (and would refuse a
    non-admin bot before any session exists), so the click tests —
    which must pin the CLICK's own gate — start the session directly,
    the same direct seeding ``seed_directly`` below provides for the
    title stage.
    """
    session = sessions_mod().sessions.start(TARGET_CHAT_ID, session_ref())
    session.stage = "asking_source"
    session.source = None
    session.add_prompt_id(prompt_id)
    return session


def pending(chat_id=TARGET_CHAT_ID):
    """The session currently pending in ``chat_id`` (``None`` if none)."""
    return sessions_mod().sessions.get(chat_id)


async def seed_batch(bot, forwards):
    """Dispatch every forward through the watcher; return the first message id.

    The first forward creates the session and asks the first question
    (id ``PROMPT_ID`` via the ``question_sender`` side effect); every
    further forward just joins the batch.
    """
    for message in forwards:
        consumed = await dispatch(message)
        assert consumed is not None, (
            f"forward {message.message_id} must be consumed by the watcher"
        )
    return forwards[0].message_id


def seed_directly(*refs):
    """Start the chat's pending session from raw refs, in the GIVEN order.

    ``bot.sessions`` is seeded without the forward handler so a test can
    pin the exact ARRIVAL order of a batch — under aiogram the arrival
    order of live forwards is decided by concurrent update handling and
    may be non-chronological. The pair is fixed and the stage is
    ``asking_title``, so the admin's next text executes (the same
    direct seeding the guard tests use).
    """
    sessions = sessions_mod().sessions
    session = sessions.start(TARGET_CHAT_ID, refs[0])
    session.stage = "asking_title"
    session.source = {"source": SOURCE_ID, "target": TARGET_REF}
    session.add_prompt_id(PROMPT_ID)
    for ref in refs[1:]:
        session.add(ref)
    return session


async def run_title(bot, title=TITLE, user_id=ADMIN_ID):
    """Send the admin's title (or other text) through the watcher."""
    message = make_admin_text(title, bot=bot, user_id=user_id)
    await dispatch(message)
    return message


def source_deletes(bot, chat_id=SOURCE_ID):
    """Every ``delete_message`` call issued to ``chat_id``, in order."""
    return [
        call_obj
        for call_obj in bot.delete_message.await_args_list
        if arg(call_obj, "chat_id", 0) == chat_id
    ]


def second_pair_origin(date=D1, message_id=888):
    """A channel origin of the SECOND configured pair (other source, SAME target chat)."""
    return channel_origin(
        date=date,
        chat_id=SECOND_SOURCE_ID,
        username=SECOND_SOURCE_USERNAME,
        message_id=message_id,
    )


async def start_conflicting_batch(bot):
    """A pending batch whose second forward identifies ANOTHER configured pair (F1).

    Config: the usual ``PAIRS`` plus a second source pair pointing at
    the SAME threaded chat. The first forward fixes ``session.source``;
    the second carries an origin that resolves to a different pair —
    one pair per batch, so the bot has to re-open the source question
    (BRIEF «конфликт origin-ов в пачке → вопрос»).
    """
    await set_pairs([*PAIRS, {"source": SECOND_SOURCE_ID, "target": TARGET_REF}])
    await dispatch(make_forward(11, channel_origin(date=D1, message_id=501), bot=bot, text="one"))
    await dispatch(make_forward(12, second_pair_origin(date=D2), bot=bot, text="two"))


class RecordingBuffer:
    """A stand-in ``bot.buffer`` whose ``find_and_take`` is an AsyncMock.

    The watcher keeps a module-level ``buffer`` reference and AWAITS
    ``find_and_take`` (item 3: the buffer lives in the database), so
    the stand-in must be awaitable — an ``AsyncMock`` with a capturing
    ``side_effect``. Every lookup is recorded — argument count, order
    and values — while reporting a plain miss (``None``), exactly the
    contract of the old synchronous recorder.
    """

    def __init__(self):
        """Start with no captured calls."""
        self.calls: list[tuple[tuple, dict]] = []
        self.find_and_take = AsyncMock(name="find_and_take", side_effect=self._capture)

    def _capture(self, *args, **kwargs):
        """Capture one lookup and report a plain miss."""
        self.calls.append((args, kwargs))
        return None


def lookup_sender(call):
    """The ``sender_id`` a captured lookup received: 6th positional or keyword.

    Anything SHORT of six arguments is a spec violation (the fifth
    argument of the old signature is ``file_unique_id``) and fails the
    assertion below with the captured call.
    """
    args, kwargs = call
    if "sender_id" in kwargs:
        return kwargs["sender_id"]
    assert len(args) == 6, (
        "find_and_take must receive (chat_id, date, text, caption, "
        f"file_unique_id, sender_id) — got {len(args)} positional args: {args!r}"
    )
    return args[5]


async def buffered_message_ids(chat_id) -> list[int]:
    """``message_id`` of every buffered row of ``chat_id`` (item 3: the DB).

    The buffer state is read from the ``buffer_messages`` table itself;
    the imports live inside the helper on purpose (RED: ``bot.database``
    / ``bot.models`` do not exist yet — a top-level import would abort
    pytest collection for the whole suite).
    """
    from bot.database import session
    from bot.models import BufferMessage

    async with session() as db_session:
        result = await db_session.execute(
            select(BufferMessage)
            .where(BufferMessage.chat_id == chat_id)
            .order_by(BufferMessage.id)
        )
        return [row.message_id for row in result.scalars()]


# ========================================================================
class TestWatcherRouter:
    """The module router and its two handlers."""

    def test_router_is_an_aiogram_router(self):
        """``router`` is an ``aiogram.Router`` instance."""
        assert isinstance(watcher_mod().router, Router), (
            "the module router must be a Router instance"
        )

    def test_router_carries_exactly_two_handlers_forward_first(self):
        """``on_forward`` is registered BEFORE ``on_text`` (router order).

        The order is the conflict resolution of the spec: a forward
        carries text too, and the forward handler must consume it —
        otherwise a batch forward would be mistaken for a title.
        """
        router = watcher_mod().router
        names = [getattr(handler.callback, "__name__", None) for handler in router.message.handlers]

        assert names == ["on_forward", "on_text"], (
            "the router must register exactly on_forward then on_text, "
            f"got {names!r}"
        )

    @pytest.mark.parametrize("name", ["on_forward", "on_text"])
    def test_handlers_are_async(self, name: str):
        """Both handlers are declared ``async def`` — aiogram awaits them."""
        assert inspect.iscoroutinefunction(watcher_handler(name).callback), (
            f"{name} must be an async handler"
        )


class TestForwardFilter:
    """The filters of ``on_forward``: configured target chat + origin present."""

    async def test_forward_in_the_configured_target_chat_passes(self):
        """Sanity: a forward with an origin in the configured threaded chat."""
        message = make_forward(11, channel_origin())

        passed, _ = await watcher_handler("on_forward").check(message)

        assert passed is True

    @pytest.mark.parametrize(
        "chat_id,username",
        [
            pytest.param(SOURCE_ID, SOURCE_USERNAME, id="source-chat"),
            pytest.param(-100555, "stranger", id="unconfigured-chat"),
        ],
    )
    async def test_forwards_outside_configured_targets_do_not_pass(self, chat_id, username):
        """The bot works only in configured threaded chats (BRIEF §2)."""
        message = make_forward(11, channel_origin(), chat_id=chat_id, username=username)

        passed, _ = await watcher_handler("on_forward").check(message)

        assert passed is False, (
            f"a forward in {username!r} must not reach the watcher"
        )

    async def test_a_message_without_forward_origin_does_not_pass(self):
        """Only forwards are batch material — plain text goes to ``on_text``."""
        message = make_admin_text("just chatting")

        passed, _ = await watcher_handler("on_forward").check(message)

        assert passed is False, "the forward handler must require a forward_origin"


class TestForwardAdminGate:
    """The admin check runs BEFORE any session is created (silent exits)."""

    async def test_message_without_from_user_exits_silently(self):
        """``from_user is None`` → no API call, no session, no reply (L3)."""
        bot = make_bot()
        message = make_forward(11, channel_origin(), bot=bot, user_id=None)

        await dispatch(message)

        assert pending() is None, "a senderless forward must not start a batch"
        bot.get_chat_member.assert_not_awaited(), "there is no user id to look up"
        bot.send_message.assert_not_awaited()

    async def test_non_admin_forward_does_not_start_a_session(self):
        """A mere member's forward is ignored — and checked BEFORE ``start``."""
        bot = make_bot(status="member")
        message = make_forward(11, channel_origin(), bot=bot)

        await dispatch(message)

        assert pending() is None, (
            "the admin check must run before the session is created: a "
            "non-admin forward leaves no pending state behind"
        )
        bot.send_message.assert_not_awaited(), "no question for a non-admin"
        member = bot.get_chat_member.await_args
        assert arg(member, "chat_id", 0) == TARGET_CHAT_ID, (
            "admins are checked in the threaded chat the forward arrived in"
        )
        assert arg(member, "user_id", 1) == ADMIN_ID

    @pytest.mark.parametrize(
        "status",
        [pytest.param("creator", id="creator"), pytest.param("administrator", id="administrator")],
    )
    async def test_admin_statuses_pass_the_gate(self, status: str):
        """``creator``/``administrator`` start the batch as specified."""
        bot = make_bot(status=status)
        message = make_forward(11, channel_origin(), bot=bot)

        await dispatch(message)

        assert pending() is not None, f"status {status!r} is an admin of the chat"


class TestForwardStartsTheSession:
    """The first forward: session created + exactly one question (step 2–3)."""

    async def test_user_origin_asks_where_the_batch_came_from(self):
        """No chat in the origin → the «which chat?» question, stage asking_source."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        message = make_forward(11, user_origin(date=D1), bot=bot)

        await dispatch(message)

        session = pending()
        assert session is not None, "the first forward must create the session"
        assert session.stage == "asking_source", (
            "a user origin hides the chat: the bot must ask for the source"
        )
        assert session.source is None, "no pair can be fixed from a chatless origin"
        assert [m["message_id"] for m in session.messages] == [11], (
            "the batch starts with the first forwarded message"
        )
        question = bot.send_message.await_args
        assert arg(question, "text", 1) == QUESTION_SOURCE, (
            f"the exact question literal is required: {question!r}"
        )
        assert arg(question, "chat_id", 0) == TARGET_CHAT_ID
        assert arg(question, "reply_to_message_id") == 11, (
            "every question hangs on the FIRST message of the batch"
        )
        assert question.kwargs.get("parse_mode") == "HTML", (
            f"every watcher reply is HTML: {question!r}"
        )
        assert bot.send_message.await_count == 1, "exactly one question"

    async def test_channel_origin_matching_the_pair_skips_to_the_title(self):
        """Origin carries a configured chat → no question, straight to the title."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        message = make_forward(11, channel_origin(message_id=501), bot=bot)

        await dispatch(message)

        session = pending()
        assert session.stage == "asking_title", (
            "a configured origin must not trigger the «which chat?» question"
        )
        assert session.source == {"source": SOURCE_ID, "target": TARGET_REF}, (
            "the origin already identifies the pair — it is fixed right away"
        )
        question = bot.send_message.await_args
        assert arg(question, "text", 1) == QUESTION_TITLE
        assert arg(question, "reply_to_message_id") == 11
        assert bot.send_message.await_count == 1, "only the title question is asked"

    async def test_group_forward_origin_also_counts_as_a_chat(self):
        """``MessageOriginChat`` (sender_chat, no message id) carries the chat too."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        message = make_forward(11, chat_origin(), bot=bot)

        await dispatch(message)

        session = pending()
        assert session.stage == "asking_title", (
            "a group forward whose sender_chat is configured skips the question"
        )
        assert session.source == {"source": SOURCE_ID, "target": TARGET_REF}
        assert arg(bot.send_message.await_args, "text", 1) == QUESTION_TITLE

    async def test_channel_origin_of_an_unconfigured_chat_asks(self):
        """A channel that is NOT in the pairs → the «which chat?» question."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        message = make_forward(
            11, channel_origin(chat_id=STRANGER_ID, username="othergroup"), bot=bot
        )

        await dispatch(message)

        session = pending()
        assert session.stage == "asking_source"
        assert session.source is None, "an unconfigured origin must not fix a pair"
        assert arg(bot.send_message.await_args, "text", 1) == QUESTION_SOURCE


class TestForwardAppendsToThePendingBatch:
    """Further forwards join the batch: no duplicate questions, stage unchanged."""

    async def test_second_forward_extends_the_batch_without_a_new_question(self):
        """asking_source stage: the batch grows, no additional send, same stage."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        await dispatch(make_forward(11, user_origin(date=D1), bot=bot))
        assert bot.send_message.await_count == 1, "sanity: one question so far"

        await dispatch(make_forward(12, user_origin(date=D2), bot=bot))

        session = pending()
        assert [m["message_id"] for m in session.messages] == [11, 12], (
            "the second forward must join the same batch"
        )
        assert session.stage == "asking_source", "the stage must not change"
        assert bot.send_message.await_count == 1, (
            "a further forward must not duplicate the question"
        )

    async def test_second_forward_in_the_title_stage_stays_quiet(self):
        """asking_title stage: same contract — append only."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        await dispatch(make_forward(11, channel_origin(message_id=501), bot=bot))
        await dispatch(make_forward(12, channel_origin(date=D2, message_id=502), bot=bot))

        session = pending()
        assert [m["message_id"] for m in session.messages] == [11, 12]
        assert session.stage == "asking_title"
        assert bot.send_message.await_count == 1

    async def test_second_batch_in_another_target_chat_is_independent(self):
        """Two configured threaded chats batch at the same time (isolation)."""
        await set_pairs([*PAIRS, {"source": -100222, "target": "@othergroup"}])
        bot = make_bot()
        bot.send_message.side_effect = question_sender()

        await dispatch(make_forward(11, user_origin(date=D1), bot=bot))
        await dispatch(
            make_forward(21, user_origin(date=D1), bot=bot, chat_id=OTHER_TARGET_ID,
                         username=OTHER_TARGET_USERNAME)
        )

        first, second = pending(TARGET_CHAT_ID), pending(OTHER_TARGET_ID)
        assert first is not None and second is not None, (
            "both threaded chats must hold their own pending batch"
        )
        assert [m["message_id"] for m in first.messages] == [11]
        assert [m["message_id"] for m in second.messages] == [21], (
            "the second chat's batch must not absorb the first chat's forwards"
        )


class TestForwardConsumesBeforeText:
    """Router order: a forward never falls into the text handler."""

    async def test_a_forward_with_text_runs_the_forward_handler(self):
        """A text-carrying forward in a pending chat is APPENDED, not executed.

        If ``on_text`` consumed it, the forward's text would be read as
        the thread title and the batch would execute after one message.
        """
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        await dispatch(make_forward(11, channel_origin(message_id=501), bot=bot))
        forwarded_with_text = make_forward(
            12, channel_origin(date=D2, message_id=502), bot=bot, text="Weather discussion"
        )

        consumed = await dispatch(forwarded_with_text)

        assert getattr(consumed.callback, "__name__", None) == "on_forward", (
            "the forward handler is registered first and must consume forwards"
        )
        session = pending()
        assert [m["message_id"] for m in session.messages] == [11, 12], (
            "the forward joined the batch — it was not treated as a title"
        )
        assert bot.send_message.await_count == 1, "no execution, no new question"


class TestTextFilter:
    """The ``on_text`` filter: a session must exist in this chat."""

    async def test_text_without_a_session_does_not_pass(self):
        """No pending batch → the text handler is not even consulted."""
        message = make_admin_text(TITLE)

        passed, _ = await watcher_handler("on_text").check(message)

        assert passed is False

    async def test_text_with_a_session_passes(self):
        """A pending batch makes the chat's text messages part of the flow."""
        sessions_mod().sessions.start(TARGET_CHAT_ID, session_ref())
        message = make_admin_text(TITLE)

        passed, _ = await watcher_handler("on_text").check(message)

        assert passed is True

    async def test_text_in_another_chat_without_a_session_does_not_pass(self):
        """A session in one chat does not activate the text handler elsewhere."""
        sessions_mod().sessions.start(TARGET_CHAT_ID, session_ref())
        message = make_admin_text(TITLE, chat_id=OTHER_TARGET_ID, username=OTHER_TARGET_USERNAME)

        passed, _ = await watcher_handler("on_text").check(message)

        assert passed is False, "the session filter is scoped to its own chat"


class TestTextAdminGate:
    """A non-admin (or senderless) text is ignored; the session stays alive."""

    async def test_non_admin_text_is_ignored(self):
        """A member's answer → no reply, no state change, one admin lookup."""
        bot = make_bot(status="member")
        sessions_mod().sessions.start(TARGET_CHAT_ID, session_ref())
        message = make_admin_text(TITLE, bot=bot)

        await dispatch(message)

        assert message.answer.await_count == 0, "a non-admin gets no reply at all"
        session = pending()
        assert session is not None, "the session must stay alive"
        assert session.stage == "asking_title", "and unchanged"
        member = bot.get_chat_member.await_args
        assert arg(member, "chat_id", 0) == TARGET_CHAT_ID
        assert arg(member, "user_id", 1) == ADMIN_ID

    async def test_text_without_from_user_is_ignored(self):
        """``from_user is None`` → silent: no API call, no reply, session alive."""
        bot = make_bot()
        sessions_mod().sessions.start(TARGET_CHAT_ID, session_ref())
        message = make_admin_text(TITLE, bot=bot, user_id=None)

        await dispatch(message)

        assert message.answer.await_count == 0
        assert pending() is not None, "the session must survive a senderless message"
        bot.get_chat_member.assert_not_awaited(), "no user id → no lookup (L3)"


class TestCancel:
    """``/cancel`` from an admin: delete the questions, answer, reset."""

    @pytest.mark.parametrize(
        "origin,expected_stage",
        [
            pytest.param(user_origin(date=D1), "asking_source", id="asking-source"),
            pytest.param(channel_origin(message_id=501), "asking_title", id="asking-title"),
        ],
    )
    async def test_cancel_deletes_the_prompts_and_resets(self, origin, expected_stage):
        """Both stages: the question message goes away, exactly ``Cancelled.``"""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        await dispatch(make_forward(11, origin, bot=bot))
        assert pending().stage == expected_stage, "sanity: the seeded stage"
        message = make_admin_text("/cancel", bot=bot)

        await dispatch(message)

        assert reply_text(message) == CANCELLED_REPLY, (
            "the fixed English literal for /cancel"
        )
        assert message.answer.await_args.kwargs.get("parse_mode") == "HTML"
        deletes = bot.delete_message.await_args_list
        assert len(deletes) == 1, "exactly the question message is deleted"
        assert arg(deletes[0], "chat_id", 0) == TARGET_CHAT_ID
        assert arg(deletes[0], "message_id", 1) == PROMPT_ID
        assert pending() is None, "/cancel must reset the pending batch"

    async def test_non_admin_cannot_cancel(self):
        """A member's ``/cancel`` leaves the batch untouched."""
        bot = make_bot(status="member")
        sessions_mod().sessions.start(TARGET_CHAT_ID, session_ref())
        message = make_admin_text("/cancel", bot=bot)

        await dispatch(message)

        assert message.answer.await_count == 0, "only admins may cancel"
        assert pending() is not None, "the batch must stay pending"
        bot.delete_message.assert_not_awaited()


class TestAskingSourceStage:
    """The admin's answer to «which chat did you forward from?» (step 2)."""

    async def test_configured_answer_fixes_the_source_and_asks_for_the_title(self):
        """``-100111`` resolves the pair → fix it, ask the title question."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        await dispatch(make_forward(11, user_origin(date=D1), bot=bot))

        message = await run_title(bot, "-100111")

        session = pending()
        assert session is not None, "a configured answer keeps the flow alive"
        assert session.source == {"source": SOURCE_ID, "target": TARGET_REF}, (
            "the configured pair must be fixed on the session"
        )
        assert session.stage == "asking_title"
        assert message.answer.await_count == 0, (
            "the transition is announced by the next question, not an answer"
        )
        assert bot.send_message.await_count == 2, "the title question follows"
        question = bot.send_message.await_args
        assert arg(question, "text", 1) == QUESTION_TITLE
        assert arg(question, "reply_to_message_id") == 11, (
            "the title question also hangs on the FIRST message of the batch"
        )

    async def test_unconfigured_answer_stops_the_flow(self):
        """``@unknown`` → the fixed refusal, questions deleted, session reset."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        await dispatch(make_forward(11, user_origin(date=D1), bot=bot))
        message = make_admin_text("@unknown", bot=bot)

        await dispatch(message)

        assert reply_text(message) == NOT_CONFIGURED_REPLY, (
            "«не настроено → СТОП»: the exact refusal literal"
        )
        deletes = bot.delete_message.await_args_list
        assert len(deletes) == 1, "the question message must be deleted"
        assert arg(deletes[0], "message_id", 1) == PROMPT_ID
        assert pending() is None, "the state resets — no thread is built"
        assert bot.send_message.await_count == 1, "no further question"


class TestAskingTitleStage:
    """The title answer (step 3): emptiness check before anything is built."""

    async def test_blank_title_is_refused_and_the_flow_waits(self):
        """A whitespace-only title → the fixed line, same stage, session alive."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        await dispatch(make_forward(11, channel_origin(message_id=501), bot=bot))
        message = await run_title(bot, "   \t ")

        assert reply_text(message) == EMPTY_TITLE_REPLY, (
            "a blank title must not create an empty topic"
        )
        assert pending().stage == "asking_title", "we keep waiting for a real title"
        assert bot.send_message.await_count == 1, "no header may be sent yet"
        assert bot.delete_message.await_count == 0, "the question stays for the next try"
        bot.edit_message_text.assert_not_awaited()
        bot.copy_message.assert_not_awaited()

    async def test_blank_title_keeps_the_batch(self):
        """The batch itself is untouched by a blank title."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        await dispatch(make_forward(11, channel_origin(message_id=501), bot=bot))
        await dispatch(make_forward(12, channel_origin(date=D2, message_id=502), bot=bot))

        await run_title(bot, " ")

        session = pending()
        assert [m["message_id"] for m in session.messages] == [11, 12], (
            "a blank title must not drop the accumulated batch"
        )


# ========================================================================
# Execution (BRIEF steps 4–5)
# ========================================================================


def build_timeline_spy(bot, answer_message):
    """Install logging side effects on ``bot`` and the pending answer mock.

    Returns the shared timeline: every mutating call of the execution
    appends its tag in call order, so one big assertion can pin the
    whole sequence ``send → edit → placement → target deletes → source
    cleanup → prompt deletes → answer``.
    """
    timeline: list = []
    real_send = bot.send_message.side_effect

    def send_side(*args, **kwargs):
        chat_id = kwargs.get("chat_id", args[0] if args else None)
        text = kwargs.get("text", args[1] if len(args) > 1 else "")
        if isinstance(text, str) and text.startswith("Topic:"):
            timeline.append(("send", chat_id))
            return SimpleNamespace(message_id=HEADER_ID)
        timeline.append(("question", chat_id))
        if real_send is not None:
            return real_send(*args, **kwargs)
        return SimpleNamespace(message_id=PROMPT_ID)

    def edit_side(*args, **kwargs):
        timeline.append(
            (
                "edit",
                kwargs.get("chat_id", args[0] if args else None),
                kwargs.get("message_id", args[1] if len(args) > 1 else None),
            )
        )

    def copy_side(*args, **kwargs):
        timeline.append(("copy", kwargs.get("message_id", args[2] if len(args) > 2 else None)))

    def delete_side(*args, **kwargs):
        timeline.append(
            (
                "delete",
                kwargs.get("chat_id", args[0] if args else None),
                kwargs.get("message_id", args[1] if len(args) > 1 else None),
            )
        )
        return True

    def answer_side(*args, **kwargs):
        text = args[0] if args else kwargs.get("text", "")
        timeline.append(("answer", text))

    bot.send_message.side_effect = send_side
    bot.edit_message_text.side_effect = edit_side
    bot.copy_message.side_effect = copy_side
    bot.delete_message.side_effect = delete_side
    answer_message.answer.side_effect = answer_side
    return timeline


class TestExecutionOrder:
    """The full call sequence of a successful move (steps 2–9, pinned)."""

    async def test_the_whole_flow_in_one_ordered_timeline(self):
        """send → edit → copies → target deletes → source cleanup → prompt → answer.

        The batch mixes both origin kinds: two channel origins (found
        directly by their origin message id, WITHOUT the buffer) and one
        user origin (found in the source buffer by forward-origin date
        + text). All three originals are deletable → ``Deleted 3 of 3``.
        """
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        forwards = [
            make_forward(11, channel_origin(date=D1, message_id=501), bot=bot, text="one"),
            make_forward(12, channel_origin(date=D2, message_id=502), bot=bot, text="two"),
            make_forward(13, user_origin(date=D3), bot=bot, text="three"),
        ]
        await seed_batch(bot, forwards)
        # Only the third original was seen live in the source chat.
        import bot.buffer as buffer_module

        await buffer_module.buffer.record(SOURCE_ID, 403, OTHER_ADMIN_ID, D3, "three", None, None)
        title_message = make_admin_text(TITLE, bot=bot)
        timeline = build_timeline_spy(bot, title_message)

        await dispatch(title_message)

        assert timeline == [
            ("send", TARGET_CHAT_ID),
            ("edit", TARGET_CHAT_ID, HEADER_ID),
            ("copy", 11),
            ("copy", 12),
            ("copy", 13),
            ("delete", TARGET_CHAT_ID, 11),
            ("delete", TARGET_CHAT_ID, 12),
            ("delete", TARGET_CHAT_ID, 13),
            ("delete", SOURCE_ID, 501),
            ("delete", SOURCE_ID, 502),
            ("delete", SOURCE_ID, 403),
            ("delete", TARGET_CHAT_ID, PROMPT_ID),
            ("answer", CREATED_REPLY),
        ], (
            "the mandated order: header, link edit, batch placement, the "
            "forwarded copies deleted, source cleanup, question messages, "
            "and only then the two-line answer"
        )
        assert pending() is None, "a successful move resets the pending batch"

    async def test_the_answer_is_exactly_the_two_contract_lines(self):
        """``Thread created…`` + ``Deleted …`` in ONE HTML answer."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        await seed_batch(
            bot, [make_forward(11, channel_origin(message_id=501), bot=bot, text="one")]
        )
        message = await run_title(bot)

        assert reply_text(message) == created_reply(1, 1, 1)
        assert message.answer.await_count == 1, "one message, not two"
        assert message.answer.await_args.kwargs.get("parse_mode") == "HTML", (
            "the answer embeds a URL and must declare HTML parsing"
        )


class TestHeaderAndLink:
    """Steps 2–3: the header message and its edit with the thread link."""

    async def test_header_is_the_topic_line_and_the_edit_adds_the_link(self):
        """Send the topic line, then edit the SAME message adding the link."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        await seed_batch(
            bot, [make_forward(11, channel_origin(message_id=501), bot=bot, text="one")]
        )

        await run_title(bot)

        header_calls = [
            call_obj
            for call_obj in bot.send_message.await_args_list
            if str(arg(call_obj, "text", 1)).startswith("Topic:")
        ]
        assert len(header_calls) == 1, "exactly one header goes to the target chat"
        header_call = header_calls[0]
        assert arg(header_call, "chat_id", 0) == TARGET_CHAT_ID
        assert arg(header_call, "text", 1) == HEADER_TEMPLATE.format(title=TITLE), (
            "the header is exactly the topic line with the title"
        )
        assert header_call.kwargs.get("parse_mode") == "HTML"

        edits = bot.edit_message_text.await_args_list
        assert len(edits) == 1, "the header must be edited exactly once"
        edit = edits[0]
        assert arg(edit, "chat_id", 0) == TARGET_CHAT_ID
        assert arg(edit, "message_id", 1) == HEADER_ID, (
            "the edit targets the header message just posted"
        )
        edited = arg(edit, "text", 2)
        assert HEADER_TEMPLATE.format(title=TITLE) in edited, (
            "the edit keeps the topic line"
        )
        assert THREAD_LINK_LINE.format(thread_url=THREAD_URL) in edited, (
            f"the edit adds the thread-link line with {THREAD_URL!r}"
        )
        assert edit.kwargs.get("parse_mode") == "HTML"

    async def test_title_is_html_escaped(self):
        """An injecting title never reaches the header raw (cycle-4 contract)."""
        evil_title = '<b>hack</b> & "x"'
        escaped = html.escape(evil_title, quote=True)
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        await seed_batch(
            bot, [make_forward(11, channel_origin(message_id=501), bot=bot, text="one")]
        )

        message = await run_title(bot, evil_title)

        sent_texts = [
            str(arg(call_obj, "text", 1)) for call_obj in bot.send_message.await_args_list
        ]
        edited = arg(bot.edit_message_text.await_args, "text", 2)
        for text in (*sent_texts, edited):
            assert evil_title not in text, f"the raw title must never be sent: {text!r}"
        assert f"Topic: <b>{escaped}</b>" in edited, (
            "the header must embed the escaped title at its exact place"
        )
        assert message.answer.await_count == 1, "the flow completed normally"

    async def test_long_title_is_truncated_to_128(self):
        """The 128-character topic limit is enforced on the way in."""
        long_title = "a" * 129
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        await seed_batch(
            bot, [make_forward(11, channel_origin(message_id=501), bot=bot, text="one")]
        )

        await run_title(bot, long_title)

        header_texts = [
            str(arg(call_obj, "text", 1)) for call_obj in bot.send_message.await_args_list
            if str(arg(call_obj, "text", 1)).startswith("Topic:")
        ]
        assert len(header_texts) == 1
        assert long_title not in header_texts[0], "the full title must not reach the header"
        assert HEADER_TEMPLATE.format(title="a" * 128) in header_texts[0], (
            "the header must embed the title cut to 128 characters"
        )


class TestMediaGroupPlacement:
    """Step 4: albums stay glued via a single ``send_media_group``."""

    async def test_a_shared_media_group_becomes_one_send_media_group(self):
        """Consecutive same-group elements → one call, originals' media, header reply."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        entities = [SimpleNamespace(type="bold", offset=0, length=4)]
        forwards = [
            make_forward(11, channel_origin(date=D1, message_id=501), bot=bot,
                         media_group_id="mg-1", file_id="file-A", file_unique_id="ph-1",
                         caption="cap one", caption_entities=entities),
            make_forward(12, channel_origin(date=D2, message_id=502), bot=bot,
                         media_group_id="mg-1", file_id="file-B", file_unique_id="ph-2"),
            make_forward(13, user_origin(date=D3), bot=bot, text="three"),
        ]
        await seed_batch(bot, forwards)

        await run_title(bot)

        groups = bot.send_media_group.await_args_list
        assert len(groups) == 1, "one album → exactly one send_media_group call"
        group = groups[0]
        assert arg(group, "chat_id", 0) == TARGET_CHAT_ID
        media = arg(group, "media")
        assert len(media) == 2, "the album keeps both of its elements"
        assert media_field(media[0], "media") == "file-A", (
            "media must be placed by the original file_id (no re-upload)"
        )
        assert media_field(media[0], "caption") == "cap one", "the caption travels along"
        assert media_field(media[0], "caption_entities") == entities, (
            "caption entities must be preserved verbatim"
        )
        assert media_field(media[1], "media") == "file-B"
        assert reply_message_id(arg(group, "reply_parameters")) == HEADER_ID, (
            "the album replies to the header (Bot API 7 reply_parameters)"
        )
        copies = bot.copy_message.await_args_list
        assert [arg(call_obj, "message_id", 2) for call_obj in copies] == [13], (
            "only the non-group element is copied individually"
        )
        copy = copies[0]
        assert arg(copy, "chat_id", 0) == TARGET_CHAT_ID
        assert arg(copy, "from_chat_id", 1) == TARGET_CHAT_ID, (
            "copies come from the target chat — the forward already lives there"
        )
        assert arg(copy, "reply_to_message_id") == HEADER_ID

    async def test_long_groups_are_chunked_into_calls_of_at_most_ten(self):
        """A 12-element album → two calls: 10 + 2 (Bot API media-group limit)."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        forwards = [
            make_forward(
                10 + offset,
                channel_origin(date=D1, message_id=501 + offset),
                bot=bot,
                media_group_id="mg-long",
                file_id=f"file-{offset}",
                file_unique_id=f"ph-{offset}",
            )
            for offset in range(12)
        ]
        await seed_batch(bot, forwards)

        message = await run_title(bot)

        sizes = [len(arg(call_obj, "media")) for call_obj in bot.send_media_group.await_args_list]
        assert sizes == [10, 2], f"a 12-element album must be chunked 10+2, got {sizes!r}"
        assert bot.copy_message.await_count == 0, "no element leaves the album"
        assert reply_text(message).startswith("Thread created: 12 message(s).")

    async def test_raising_media_group_falls_back_to_copies(self):
        """``send_media_group`` blowing up → per-element copies, flow continues.

        The fallback delivers the album (without gluing) — the sources
        are still deleted and the moderator still gets the success
        answer, never ``Move failed``.
        """
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        forwards = [
            make_forward(11, channel_origin(date=D1, message_id=501), bot=bot,
                         media_group_id="mg-1", file_id="file-A", file_unique_id="ph-1",
                         caption="cap one"),
            make_forward(12, channel_origin(date=D2, message_id=502), bot=bot,
                         media_group_id="mg-1", file_id="file-B", file_unique_id="ph-2"),
        ]
        await seed_batch(bot, forwards)
        bot.send_media_group.side_effect = RuntimeError("album rejected")
        message = await run_title(bot)

        copies = bot.copy_message.await_args_list
        assert [arg(call_obj, "message_id", 2) for call_obj in copies] == [11, 12], (
            "the album elements fall back to per-element copies, in batch order"
        )
        for call_obj in copies:
            assert arg(call_obj, "chat_id", 0) == TARGET_CHAT_ID
            assert arg(call_obj, "from_chat_id", 1) == TARGET_CHAT_ID
            assert arg(call_obj, "reply_to_message_id") == HEADER_ID, (
                "fallback copies still hang on the header"
            )
        assert reply_text(message) == created_reply(2, 2, 2), (
            "a fallback is not a failure: the moderator gets the success answer"
        )
        assert [arg(call_obj, "message_id", 1) for call_obj in source_deletes(bot)] == [
            501,
            502,
        ], "the originals are cleaned up even after the album fallback"
        target_deletes = [
            call_obj
            for call_obj in bot.delete_message.await_args_list
            if arg(call_obj, "chat_id", 0) == TARGET_CHAT_ID
        ]
        assert [arg(call_obj, "message_id", 1) for call_obj in target_deletes] == [
            11,
            12,
            PROMPT_ID,
        ], "forwarded copies deleted, then the question message"
        assert pending() is None, "the batch was moved: the state resets"


class TestSourceCleanup:
    """Step 5: finding the originals of the source chat."""

    async def test_foreign_channel_origin_is_found_via_the_buffer_origin_date(self):
        """Channel origin NOT in the pairs + an answer fixing the source.

        The two-question flow («which chat?» → «title?») end to end,
        and the cleanup pin: the original is looked up by the
        FORWARD-ORIGIN date (D1), not by the arrival date of the
        forward (D2); the foreign origin's message id (999) must never
        be deleted — it belongs to a chat we are not admin of.
        """
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        foreign = channel_origin(date=D1, chat_id=STRANGER_ID, username="othergroup",
                                 message_id=999)
        await dispatch(make_forward(11, foreign, bot=bot, text="one", date=D2))
        assert pending().stage == "asking_source", "sanity: an unconfigured origin asks"

        answer_message = await run_title(bot, f" {SOURCE_ID} ")

        assert pending().stage == "asking_title", (
            "the configured answer fixes the source and asks for the title"
        )
        assert pending().source == {"source": SOURCE_ID, "target": TARGET_REF}
        import bot.buffer as buffer_module

        await buffer_module.buffer.record(SOURCE_ID, 411, None, D1, "one", None, None)
        message = await run_title(bot, TITLE)

        cleaned = [arg(call_obj, "message_id", 1) for call_obj in source_deletes(bot)]
        assert cleaned == [411], (
            "the original must be found by forward-origin date + text "
            f"(arrival date D2 must NOT be used), got deletes {cleaned!r}"
        )
        assert 999 not in cleaned, (
            "the foreign origin's message id must never be deleted"
        )
        assert message.answer.await_count == 1
        assert reply_text(message) == created_reply(1, 1, 1)
        assert answer_message.answer.await_count == 0, (
            "the source answer is announced by the next question only"
        )
        target_deletes = [
            call_obj
            for call_obj in bot.delete_message.await_args_list
            if arg(call_obj, "chat_id", 0) == TARGET_CHAT_ID
        ]
        assert [arg(call_obj, "message_id", 1) for call_obj in target_deletes] == [
            11,
            PROMPT_ID,
            PROMPT_ID + 1,
        ], (
            "the forwarded copy, then BOTH question messages of the "
            "two-question flow"
        )
        assert pending() is None

    async def test_partial_cleanup_is_reported_honestly(self):
        """One original unreachable → ``Deleted 2 of 3``, the move still succeeds."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        forwards = [
            make_forward(11, channel_origin(date=D1, message_id=501), bot=bot, text="one"),
            make_forward(12, user_origin(date=D2), bot=bot, text="two"),
            make_forward(13, user_origin(date=D3), bot=bot, text="three"),
        ]
        await seed_batch(bot, forwards)
        import bot.buffer as buffer_module

        # The second original was recorded with ANOTHER date → not found;
        # the third matches → found; the first is a configured channel origin.
        await buffer_module.buffer.record(SOURCE_ID, 402, 9, D1, "two", None, None)
        await buffer_module.buffer.record(SOURCE_ID, 403, OTHER_ADMIN_ID, D3, "three", None, None)

        message = await run_title(bot)

        assert reply_text(message) == created_reply(3, 2, 3), (
            "partial success is the norm: honest x/y in the second line"
        )
        assert [arg(call_obj, "message_id", 1) for call_obj in source_deletes(bot)] == [
            501,
            403,
        ], "the mis-dated original is skipped, never guessed at"
        assert pending() is None

    async def test_raising_source_delete_counts_as_not_found_and_keeps_going(self):
        """A ``delete_message`` crash in the cleanup is «not found», not a failure."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        forwards = [
            make_forward(11, channel_origin(date=D1, message_id=501), bot=bot, text="one"),
            make_forward(12, user_origin(date=D2), bot=bot, text="two"),
            make_forward(13, user_origin(date=D3), bot=bot, text="three"),
        ]
        await seed_batch(bot, forwards)
        import bot.buffer as buffer_module

        await buffer_module.buffer.record(SOURCE_ID, 402, OTHER_ADMIN_ID, D2, "two", None, None)
        await buffer_module.buffer.record(SOURCE_ID, 403, OTHER_ADMIN_ID, D3, "three", None, None)

        def delete_side(*args, **kwargs):
            chat_id = kwargs.get("chat_id", args[0] if args else None)
            mid = kwargs.get("message_id", args[1] if len(args) > 1 else None)
            if chat_id == SOURCE_ID and mid == 402:
                raise ValueError("delete rejected")
            return True

        bot.delete_message.side_effect = delete_side
        message = await run_title(bot)

        assert reply_text(message) == created_reply(3, 2, 3), (
            "a cleanup crash must NOT surface as Move failed: the thread is built"
        )
        assert [arg(call_obj, "message_id", 1) for call_obj in source_deletes(bot)] == [
            501,
            402,
            403,
        ], "every original was ATTEMPTED in batch order"
        assert pending() is None, "the flow completed and reset the state"


class TestExecutionGuards:
    """The guards of BRIEF «Отказы и лимиты»: batch cap, target link, failures."""

    @pytest.mark.parametrize("size", [101, 150])
    async def test_oversized_batch_is_refused_with_the_real_size(self, size: int):
        """``Batch too large ({n} messages, limit 100). Nothing was moved.``

        The oversized batch is SEEDED directly into ``bot.sessions`` —
        pushing ``size`` forwards through ``on_forward`` would trip the
        ACCUMULATION cap of ``TestAccumulationLimit`` first (the 101st
        forward is refused before it can join) and the execution guard
        would never see the oversized state.
        """
        bot = make_bot()
        sessions = sessions_mod().sessions
        session = sessions.start(TARGET_CHAT_ID, session_ref(message_id=100))
        session.stage = "asking_title"
        session.source = {"source": SOURCE_ID, "target": TARGET_REF}
        session.add_prompt_id(PROMPT_ID)
        for offset in range(size - 1):
            session.add(session_ref(message_id=101 + offset))
        assert len(session.messages) == size, f"sanity: the seeded batch has {size} messages"

        message = await run_title(bot)

        assert reply_text(message) == BATCH_TOO_LARGE_TEMPLATE.format(
            size=size, limit=BATCH_LIMIT
        ), f"the reply must name the REAL batch size {size}"
        assert message.answer.await_count == 1, "the guard replies exactly once"
        assert pending() is None, "the state resets"
        deletes = bot.delete_message.await_args_list
        assert len(deletes) == 1, "only the question message may be deleted"
        assert arg(deletes[0], "chat_id", 0) == TARGET_CHAT_ID
        assert arg(deletes[0], "message_id", 1) == PROMPT_ID
        bot.send_message.assert_not_awaited(), "no header is sent"
        bot.edit_message_text.assert_not_awaited()
        bot.copy_message.assert_not_awaited()
        bot.send_media_group.assert_not_awaited()
        assert source_deletes(bot) == [], "no mutation of any chat"

    @pytest.mark.parametrize(
        "target",
        [pytest.param("bad target!", id="spaces-and-punctuation"),
         pytest.param("имя канала", id="non-username")],
    )
    async def test_invalid_target_is_refused_before_any_mutation(self, target: str):
        """``build_thread_url`` raising → the fixed line, reset, zero API calls."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        sessions = sessions_mod().sessions
        session = sessions.start(TARGET_CHAT_ID, session_ref())
        session.stage = "asking_title"
        session.source = {"source": SOURCE_ID, "target": target}
        session.add_prompt_id(PROMPT_ID)

        message = await run_title(bot)

        assert reply_text(message) == INVALID_TARGET_TEMPLATE.format(target=target), (
            "the fixed line embeds the offending configured target verbatim"
        )
        assert message.answer.await_count == 1
        assert pending() is None, "the state resets"
        bot.send_message.assert_not_awaited(), "the guard runs BEFORE the header"
        bot.edit_message_text.assert_not_awaited()
        bot.copy_message.assert_not_awaited()
        bot.send_media_group.assert_not_awaited()

    async def test_failure_at_the_header_send_is_answered_not_raised(self):
        """Step 2 raising → ``Move failed: {Type}…``, no placement, no cleanup."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        await seed_batch(
            bot,
            [
                make_forward(11, channel_origin(date=D1, message_id=501), bot=bot, text="one"),
                make_forward(12, channel_origin(date=D2, message_id=502), bot=bot, text="two"),
            ],
        )
        bot.send_message.side_effect = RuntimeError("api down")

        message = await run_title(bot)

        assert reply_text(message) == MOVE_FAILED_TEMPLATE.format(error="RuntimeError"), (
            "the TYPE name of the error, not str()"
        )
        assert message.answer.await_count == 1, "answered instead of crashing the update"
        assert pending() is None, "the state resets — a retry re-sends the batch"
        bot.edit_message_text.assert_not_awaited()
        bot.copy_message.assert_not_awaited()
        bot.send_media_group.assert_not_awaited()
        assert [arg(c, "message_id", 1) for c in bot.delete_message.await_args_list] == [
            PROMPT_ID,
        ], "only the question messages are deleted"
        assert source_deletes(bot) == [], "no source cleanup after a failed move"

    async def test_failure_at_the_edit_is_answered_not_raised(self):
        """Step 3 raising → same contract: fixed line, prompts gone, no cleanup."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        await seed_batch(bot, [make_forward(11, channel_origin(message_id=501), bot=bot,
                                             text="one")])
        bot.edit_message_text.side_effect = RuntimeError("edit rejected")

        message = await run_title(bot)

        assert reply_text(message) == MOVE_FAILED_TEMPLATE.format(error="RuntimeError")
        assert pending() is None
        bot.copy_message.assert_not_awaited(), "placement must not start after a failed edit"
        bot.send_media_group.assert_not_awaited()
        assert [arg(c, "message_id", 1) for c in bot.delete_message.await_args_list] == [
            PROMPT_ID,
        ]
        assert source_deletes(bot) == []

    async def test_failure_at_the_placement_is_answered_not_raised(self):
        """Step 4 raising → same contract; the failing copy is the last call."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        await seed_batch(
            bot,
            [
                make_forward(11, channel_origin(date=D1, message_id=501), bot=bot, text="one"),
                make_forward(12, channel_origin(date=D2, message_id=502), bot=bot, text="two"),
            ],
        )
        bot.copy_message.side_effect = RuntimeError("copy rejected")

        message = await run_title(bot)

        assert reply_text(message) == MOVE_FAILED_TEMPLATE.format(error="RuntimeError")
        assert bot.copy_message.await_count == 1, (
            "no further placement calls after the failure"
        )
        bot.send_media_group.assert_not_awaited()
        assert pending() is None
        assert [arg(c, "message_id", 1) for c in bot.delete_message.await_args_list] == [
            PROMPT_ID,
        ], "the forwarded copies are NOT deleted — the move failed"
        assert source_deletes(bot) == []

    async def test_a_failing_delete_of_a_forwarded_copy_is_not_a_move_failure(self):
        """Step 5's target deletes are best-effort (F10): one raising delete
        skips only itself — placement finished, the rest of the deletes
        run, the source cleanup runs and the answer is the normal
        success line.

        Rewritten pin: the forwarded-copy deletes sit OUTSIDE the
        ``Move failed`` failure block — send/edit/placement failures
        still fail the move (pinned above), a copy delete does not: the
        thread is already built by then.
        """
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        await seed_batch(
            bot,
            [
                make_forward(11, channel_origin(date=D1, message_id=501), bot=bot, text="one"),
                make_forward(12, channel_origin(date=D2, message_id=502), bot=bot, text="two"),
            ],
        )

        def delete_side(*args, **kwargs):
            chat_id = kwargs.get("chat_id", args[0] if args else None)
            mid = kwargs.get("message_id", args[1] if len(args) > 1 else None)
            if chat_id == TARGET_CHAT_ID and mid == 11:
                raise RuntimeError("delete rejected")
            return True

        bot.delete_message.side_effect = delete_side

        message = await run_title(bot)

        reply = reply_text(message)
        assert reply == created_reply(2, 2, 2), (
            "a copy delete must not turn a finished move into a failure: "
            f"the normal two-line success answer is required, got {reply!r}"
        )
        assert "Move failed" not in reply, "the thread was built — no failure answer"
        assert bot.copy_message.await_count == 2, "placement finished before the deletes"
        target_msgs = [
            arg(call_obj, "message_id", 1)
            for call_obj in bot.delete_message.await_args_list
            if arg(call_obj, "chat_id", 0) == TARGET_CHAT_ID
        ]
        assert target_msgs == [11, 12, PROMPT_ID], (
            "the failing delete skips only itself: the second copy is still "
            f"attempted, then the prompt is deleted, got {target_msgs!r}"
        )
        assert [arg(call_obj, "message_id", 1) for call_obj in source_deletes(bot)] == [
            501,
            502,
        ], "the source cleanup runs — the move completed"
        assert pending() is None, "the state resets"


class TestSuccessReplyEscaping:
    """The success reply is built through the escaping renderer (L2)."""

    async def test_success_reply_is_built_through_render_or_escape(self, monkeypatch):
        """The two-line answer embeds the thread URL through render/html.escape.

        Contract (ported from the v2 L2 review): the reply text must be
        an output of ``templates.render`` captured during the call — or
        the result of an ``html.escape`` executed by the watcher module
        itself. A raw f-string skips the escaping step the spec reserves
        for every embedded fragment and must be rejected.
        """
        import bot.handlers.watcher as watcher_module
        import bot.services.templates as templates_module

        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        await seed_batch(bot, [make_forward(11, channel_origin(message_id=501), bot=bot,
                                             text="one")])

        rendered: list[str] = []
        real_render = templates_module.render

        def spy_render(template, variables, link_url):
            output = real_render(template, variables, link_url)
            rendered.append(output)
            return output

        escape_callers: list[str] = []
        real_escape = html.escape

        def spy_escape(text, quote=True):
            frame = inspect.currentframe()
            back = frame.f_back if frame is not None else None
            escape_callers.append(back.f_globals.get("__name__", "") if back else "")
            return real_escape(text, quote=quote)

        monkeypatch.setattr(templates_module, "render", spy_render)
        monkeypatch.setattr(watcher_module, "render", spy_render, raising=False)
        monkeypatch.setattr(html, "escape", spy_escape)

        message = await run_title(bot)
        reply = reply_text(message)

        assert reply == created_reply(1, 1, 1), "sanity: the success reply contract itself"
        escaped_by_watcher = any(
            caller == "bot.handlers.watcher" for caller in escape_callers
        )
        assert reply in rendered or escaped_by_watcher, (
            "the success reply must be produced by templates.render (captured "
            "above) or by html.escape called from bot.handlers.watcher — a "
            f"bare f-string without escaping is not allowed: {reply!r}"
        )


# ========================================================================
# Security review: mixed origins (F1), origin sender (F2),
# chat-bound pairs (F4), accumulation cap (F5)
# ========================================================================


class TestConflictingOriginsReaskTheSource:
    """F1.3: one batch has exactly ONE source pair — a foreign origin re-asks.

    The first forward fixes ``session.source``; a further forward whose
    origin identifies ANOTHER configured pair («конфликт origin-ов в
    пачке → вопрос», BRIEF §4) must drop the pair, return to
    ``asking_source`` and ask ONE new question — and the admin's fresh
    answer is verified against the config all over again.
    """

    async def test_the_conflict_reasks_and_a_configured_answer_repairs_the_source(self):
        """Foreign origin → new «which chat?» prompt; the answer resolves again → title."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()

        await start_conflicting_batch(bot)

        session = pending()
        assert session.stage == "asking_source", (
            "one pair per batch: an origin of another configured pair must "
            "re-open «Which chat did you forward from?»"
        )
        assert session.source is None, "the previously fixed pair must be dropped"
        assert [m["message_id"] for m in session.messages] == [11, 12], (
            "the batch itself keeps both forwards"
        )
        assert session.prompt_ids == [PROMPT_ID, PROMPT_ID + 1], (
            "the conflict adds a NEW prompt id — the old question stays deletable"
        )
        assert bot.send_message.await_count == 2, "exactly ONE new question"
        question = bot.send_message.await_args
        assert arg(question, "text", 1) == QUESTION_SOURCE, (
            "the exact re-question literal is required"
        )
        assert arg(question, "reply_to_message_id") == 11, (
            "the re-question also hangs on the FIRST message of the batch"
        )
        assert question.kwargs.get("parse_mode") == "HTML", (
            f"every watcher reply is HTML: {question!r}"
        )

        answer_message = await run_title(bot, str(SOURCE_ID))

        assert answer_message.answer.await_count == 0, (
            "the transition is announced by the next question, not an answer"
        )
        session = pending()
        assert session.stage == "asking_title", "a configured answer repairs the flow"
        assert session.source == {"source": SOURCE_ID, "target": TARGET_REF}, (
            "the admin's answer resolves the pair against the config again"
        )
        assert bot.send_message.await_count == 3, "the title question follows"
        question = bot.send_message.await_args
        assert arg(question, "text", 1) == QUESTION_TITLE
        assert arg(question, "reply_to_message_id") == 11

    async def test_the_conflict_then_an_unconfigured_answer_stops_the_flow(self):
        """Re-asked answer not in the config → the exact СТОП literal, both prompts gone."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        await start_conflicting_batch(bot)

        message = make_admin_text("@unknown", bot=bot)

        await dispatch(message)

        assert reply_text(message) == NOT_CONFIGURED_REPLY, (
            "«не настроено → СТОП» after the re-ask: the exact refusal literal"
        )
        assert message.answer.await_args.kwargs.get("parse_mode") == "HTML"
        assert pending() is None, "the state resets — no thread is built"
        deletes = bot.delete_message.await_args_list
        assert [(arg(c, "chat_id", 0), arg(c, "message_id", 1)) for c in deletes] == [
            (TARGET_CHAT_ID, PROMPT_ID),
            (TARGET_CHAT_ID, PROMPT_ID + 1),
        ], "BOTH question messages of the two-question flow are deleted"
        assert bot.send_message.await_count == 2, "no title question after the refusal"
        bot.edit_message_text.assert_not_awaited()
        bot.copy_message.assert_not_awaited()


class TestSourceCleanupMixedOrigins:
    """F1.1/F1.2: only the pair fixed on the session may feed the direct delete path.

    ``delete_message(source, origin.message_id)`` is safe ONLY when the
    origin's pair IS ``session.source``: the origin's message id lives
    in the origin's chat, and deleting it from another chat's id space
    (the session's source) would destroy a foreign message. Any other
    origin must go through the buffer — whose records are guaranteed to
    belong to the session's source chat.
    """

    async def test_a_foreign_pair_origin_never_deletes_its_message_id(self):
        """Mixed batch: own pair → direct id, foreign pair → buffer only.

        The full flow incl. the conflict re-ask: the second forward's
        origin belongs to ANOTHER configured pair (same target chat),
        so the source is re-asked and re-resolved; at cleanup its
        ``forward_origin.message_id`` (888) must NEVER reach
        ``delete_message`` — the original is found in the source buffer
        instead, and only the buffer id (412) is deleted from the
        source chat.
        """
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        await start_conflicting_batch(bot)
        session = pending()
        assert session.stage == "asking_source", "sanity: the conflict re-asked"
        assert bot.send_message.await_count == 2, "sanity: one new question"

        answer_message = await run_title(bot, str(SOURCE_ID))

        assert answer_message.answer.await_count == 0, "sanity: source resolved again"
        assert pending().source == {"source": SOURCE_ID, "target": TARGET_REF}
        assert pending().stage == "asking_title"
        # Only the second original was seen live in the session's source chat.
        import bot.buffer as buffer_module

        await buffer_module.buffer.record(SOURCE_ID, 412, None, D2, "two", None, None)

        message = await run_title(bot, TITLE)

        cleaned = [arg(call_obj, "message_id", 1) for call_obj in source_deletes(bot)]
        assert 888 not in cleaned, (
            "the foreign pair origin's message id must never be deleted from "
            "the session's source chat — it belongs to another chat"
        )
        assert cleaned == [501, 412], (
            "the own-pair origin is deleted directly (501), the foreign one "
            f"only via the buffer (412), got {cleaned!r}"
        )
        assert reply_text(message) == created_reply(2, 2, 2), (
            "both originals were found — partial success is not needed here"
        )
        assert await buffered_message_ids(SOURCE_ID) == [], (
            "the buffer record was consumed by the lookup"
        )
        assert pending() is None, "the flow completed and reset the state"

    async def test_the_sessions_own_pair_is_deleted_directly(self):
        """The positive pin: pair == ``session.source`` → direct delete, no buffer.

        Found WITHOUT any buffer record — the success reply says
        ``Deleted 1 of 1``, which only the direct ``origin.message_id``
        path can produce.
        """
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        await dispatch(make_forward(11, channel_origin(date=D1, message_id=501), bot=bot,
                                    text="one"))
        session = pending()
        assert session.source == {"source": SOURCE_ID, "target": TARGET_REF}, (
            "sanity: the origin's pair is the session's own pair"
        )

        message = await run_title(bot)

        cleaned = [arg(call_obj, "message_id", 1) for call_obj in source_deletes(bot)]
        assert cleaned == [501], (
            "the origin id of the session's own pair is directly deletable"
        )
        assert reply_text(message) == created_reply(1, 1, 1), (
            "found WITHOUT any buffer record — the direct path really ran"
        )
        assert not await buffered_message_ids(SOURCE_ID), (
            "the direct path never consults the buffer"
        )
        assert pending() is None


class TestCleanupPassesTheOriginSender:
    """F2: the buffer lookup carries the sender taken from the forward origin.

    ``find_and_take``'s 6th argument comes from the ORIGIN of the
    forwarded ref: ``sender_user.id`` (user/group forwards) or
    ``sender_chat.id`` (anonymous/channel-sender forwards); an origin
    carrying neither passes ``None`` — the predicate then skips the
    sender condition («неизвестен с любой стороны → условие пропускается»).
    """

    @pytest.mark.parametrize(
        "origin,expected_sender",
        [
            pytest.param(
                user_origin(date=D1, sender_user_id=OTHER_ADMIN_ID),
                OTHER_ADMIN_ID,
                id="sender-user-id",
            ),
            pytest.param(
                chat_origin(date=D1, chat_id=STRANGER_ID, username="stranger"),
                STRANGER_ID,
                id="sender-chat-id",
            ),
            pytest.param(
                channel_origin(date=D1, chat_id=STRANGER_ID, username="othergroup",
                               message_id=999),
                None,
                id="origin-without-a-sender",
            ),
        ],
    )
    async def test_the_buffer_lookup_gets_the_origin_sender(
        self, monkeypatch, origin, expected_sender
    ):
        import bot.handlers.watcher as watcher_module

        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        fake_buffer = RecordingBuffer()
        monkeypatch.setattr(watcher_module, "buffer", fake_buffer)

        await dispatch(make_forward(11, origin, bot=bot, text="one"))
        assert pending().stage == "asking_source", (
            "sanity: none of these origins identifies a pair of THIS chat"
        )
        await run_title(bot, str(SOURCE_ID))
        assert pending().stage == "asking_title", "sanity: the source is resolved"
        await run_title(bot, TITLE)

        assert len(fake_buffer.calls) == 1, (
            "one batch element → exactly one buffer lookup"
        )
        assert lookup_sender(fake_buffer.calls[0]) == expected_sender, (
            "the sender must come from the forward origin "
            f"(expected {expected_sender!r})"
        )


class TestSourcePairBoundToCurrentChat:
    """F4: a resolved pair must belong to the threaded chat the flow runs in.

    The registry may hold several pairs; the pair the bot fixes on the
    session (from the origin OR from the admin's answer) is only valid
    when its ``target`` is the CURRENT chat — anything else is «не
    настроено для этого чата».
    """

    async def test_a_pair_targeting_another_chat_is_not_configured_here(self):
        """Two pairs, ONE source, different targets; the first match targets ANOTHER chat.

        The admin's answer «откуда?» in ``TARGET_CHAT_ID`` resolves the
        foreign-target pair → the exact СТОП literal, no title question,
        the state resets (the pair is not configured FOR THIS CHAT).
        """
        await set_pairs([
            {"source": SOURCE_ID, "target": OTHER_TARGET_REF},
            {"source": SOURCE_ID, "target": "@thirdgroup"},
            {"source": THIRD_SOURCE_ID, "target": TARGET_REF},
        ])
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        await dispatch(make_forward(11, user_origin(date=D1), bot=bot))
        assert pending().stage == "asking_source", "sanity: the question is asked"

        message = make_admin_text(str(SOURCE_ID), bot=bot)

        await dispatch(message)

        assert reply_text(message) == NOT_CONFIGURED_REPLY, (
            "the resolved pair targets ANOTHER chat — «не настроено для этого "
            "чата»: the exact СТОП literal"
        )
        assert message.answer.await_args.kwargs.get("parse_mode") == "HTML"
        assert pending() is None, "the state resets — no thread is built"
        assert bot.send_message.await_count == 1, "no title question may follow"
        deletes = bot.delete_message.await_args_list
        assert [(arg(c, "chat_id", 0), arg(c, "message_id", 1)) for c in deletes] == [
            (TARGET_CHAT_ID, PROMPT_ID),
        ], "the question message is deleted with the reset"

    async def test_an_origin_of_a_pair_targeting_another_chat_asks_the_question(self):
        """Origin of a pair whose ``target`` ≠ this chat → the pair is NOT found.

        The batch must start with ``Which chat did you forward from?``—
        never with a silent jump to the title question (F4.2).
        """
        await set_pairs([*PAIRS, {"source": SECOND_SOURCE_ID, "target": OTHER_TARGET_REF}])
        bot = make_bot()
        bot.send_message.side_effect = question_sender()

        await dispatch(make_forward(11, second_pair_origin(date=D1), bot=bot, text="one"))

        session = pending()
        assert session is not None, "sanity: the forward still starts the batch"
        assert session.stage == "asking_source", (
            "a pair whose target is ANOTHER chat does not identify this "
            "chat's source pair"
        )
        assert session.source is None, "such a pair must never be fixed on the session"
        assert bot.send_message.await_count == 1
        question = bot.send_message.await_args
        assert arg(question, "text", 1) == QUESTION_SOURCE, (
            "the «which chat?» question, NOT a silent transition to the title"
        )
        assert arg(question, "reply_to_message_id") == 11
        assert question.kwargs.get("parse_mode") == "HTML"

    @pytest.mark.parametrize(
        "stale_source",
        [
            pytest.param(
                {"source": SOURCE_ID, "target": OTHER_TARGET_REF},
                id="pair-targets-another-chat",
            ),
            pytest.param(
                {"source": STRANGER_ID, "target": "@strangergroup"},
                id="pair-deleted-from-the-config",
            ),
        ],
    )
    async def test_execution_with_a_stale_source_pair_stops(self, stale_source):
        """``_execute`` re-validates ``session.source`` (seeded directly): СТОП + reset.

        No header, no copies, no source cleanup, the batch untouched —
        only the bot's own question messages may be deleted, and the
        answer is exactly the fixed СТОП literal.
        """
        bot = make_bot()
        sessions = sessions_mod().sessions
        session = sessions.start(TARGET_CHAT_ID, session_ref())
        session.stage = "asking_title"
        session.source = stale_source
        session.add_prompt_id(PROMPT_ID)

        message = make_admin_text(TITLE, bot=bot)

        await dispatch(message)

        assert reply_text(message) == NOT_CONFIGURED_REPLY, (
            "a source pair that does not belong to this chat anymore is "
            "«not configured as a source chat»"
        )
        assert message.answer.await_args.kwargs.get("parse_mode") == "HTML"
        assert pending() is None, "the state resets"
        bot.send_message.assert_not_awaited(), "no header, no question — nothing is sent"
        bot.edit_message_text.assert_not_awaited()
        bot.copy_message.assert_not_awaited()
        bot.send_media_group.assert_not_awaited()
        assert source_deletes(bot) == [], "the source chat is never touched"
        touched = [
            (arg(call_obj, "chat_id", 0), arg(call_obj, "message_id", 1))
            for call_obj in bot.delete_message.await_args_list
        ]
        assert (TARGET_CHAT_ID, 11) not in touched, "the batch stays untouched"
        assert all(mid == PROMPT_ID for _chat, mid in touched), (
            f"only the question messages may be deleted, got {touched!r}"
        )


class TestAccumulationLimit:
    """F5: the 100-message cap applies while ACCUMULATING, not only at execution.

    An unbounded pending batch is a memory-amplification vector: every
    admin forward would grow it forever. The refusal answers with the
    REAL would-be size, keeps the batch at the cap (the refused forward
    is not appended), deletes the question messages and resets the
    session; exactly at the cap the forward still joins.
    """

    async def test_the_101st_forward_is_refused_and_the_batch_does_not_grow(self):
        """100 pending + 1 → exactly the fixed refusal, state gone, batch still 100."""
        bot = make_bot()
        sessions = sessions_mod().sessions
        session = sessions.start(TARGET_CHAT_ID, session_ref(message_id=100))
        session.stage = "asking_title"
        session.source = {"source": SOURCE_ID, "target": TARGET_REF}
        session.add_prompt_id(PROMPT_ID)
        for offset in range(BATCH_LIMIT - 1):
            session.add(session_ref(message_id=101 + offset))
        assert len(session.messages) == BATCH_LIMIT, "sanity: the batch sits at the cap"

        incoming = make_forward(500, channel_origin(date=D2, message_id=888), bot=bot,
                                text="m100")

        await dispatch(incoming)

        assert reply_text(incoming) == BATCH_TOO_LARGE_TEMPLATE.format(
            size=BATCH_LIMIT + 1, limit=BATCH_LIMIT
        ), "the refusal names the REAL would-be size: 101 messages, limit 100"
        assert incoming.answer.await_count == 1, "exactly one reply"
        assert incoming.answer.await_args.kwargs.get("parse_mode") == "HTML"
        assert len(session.messages) == BATCH_LIMIT, (
            "the refused forward must NOT be appended — no 101-message state"
        )
        assert pending() is None, "the refusal resets the pending batch"
        deletes = bot.delete_message.await_args_list
        assert [(arg(c, "chat_id", 0), arg(c, "message_id", 1)) for c in deletes] == [
            (TARGET_CHAT_ID, PROMPT_ID),
        ], "the question messages are deleted with the session"
        bot.send_message.assert_not_awaited(), "no header — nothing was moved"
        bot.edit_message_text.assert_not_awaited()
        bot.copy_message.assert_not_awaited()
        bot.send_media_group.assert_not_awaited()
        assert source_deletes(bot) == [], "no mutation of any chat"

    async def test_the_hundredth_forward_still_joins_the_batch(self):
        """Exactly at the cap: the forward that REACHES 100 is appended, silently."""
        bot = make_bot()
        sessions = sessions_mod().sessions
        session = sessions.start(TARGET_CHAT_ID, session_ref(message_id=100))
        session.stage = "asking_title"
        session.source = {"source": SOURCE_ID, "target": TARGET_REF}
        for offset in range(BATCH_LIMIT - 2):
            session.add(session_ref(message_id=101 + offset))
        assert len(session.messages) == BATCH_LIMIT - 1, "sanity: 99 messages pending"

        incoming = make_forward(500, channel_origin(date=D2, message_id=888), bot=bot,
                                text="m99")

        await dispatch(incoming)

        assert len(session.messages) == BATCH_LIMIT, (
            "the 100th message itself must still join the batch"
        )
        assert incoming.answer.await_count == 0, "no refusal at the cap itself"
        assert pending() is session, "the batch stays alive"
        assert pending().stage == "asking_title", "and unchanged"
        assert bot.delete_message.await_count == 0, "nothing is deleted at the cap"
        bot.send_message.assert_not_awaited()


# ========================================================================
# Security review 2: state resilience (F3), per-chat lock (F6),
# config-error escaping (F7), non-text & /cancel@botname (F8),
# media extraction (F9), best-effort target deletes (F10)
# ========================================================================


async def dispatch_tolerating_a_raise(message):
    """Run ``message`` through the watcher even if the handler re-raises.

    F3 pins the STATE after a crashing handler call (the session must be
    reset), not the propagation: swallowing the error and re-raising
    after the reset must satisfy the same assertions.
    """
    try:
        await dispatch(message)
    except Exception:
        # The handler is allowed to re-raise after its reset — F3 cares
        # about the leftover state only, never about the exception itself.
        pass


def yielding_bot(bot):
    """Arm ``bot`` with real suspension points so handlers can interleave.

    The admin gate and every ``send_message`` yield to the event loop
    once (``asyncio.sleep(0)``): plain ``AsyncMock`` awaits resolve
    without suspending, so a concurrency race would never surface under
    ``asyncio.gather`` (F6).
    """
    sender = question_sender()

    async def gate(*args, **kwargs):
        await asyncio.sleep(0)
        return SimpleNamespace(status="administrator")

    async def send_side(*args, **kwargs):
        await asyncio.sleep(0)
        return sender(*args, **kwargs)

    bot.get_chat_member.side_effect = gate
    bot.send_message.side_effect = send_side
    return bot


def pin_filter_scheduling(monkeypatch):
    """Run the router's sync filters IN-LOOP instead of via ``asyncio.to_thread``.

    aiogram executes non-async filters with ``asyncio.to_thread`` — the
    completion order of two thread-pool hops decides which concurrent
    dispatch runs first, which made the race below nondeterministic.
    Running them in-loop keeps the filter semantics identical and pins
    the interleaving to plain FIFO task scheduling, so the concurrency
    pins are stable (F6).
    """

    async def inline_to_thread(func, /, *args, context=None, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", inline_to_thread)


class TestStateResilienceAfterSendFailures:
    """F3: a crashing send leaves NO session behind — no orphans, no double runs."""

    async def test_a_failing_source_question_resets_the_session(self):
        """The «which chat?» question send raises → session gone, flow dead.

        Without the reset the session is an ``asking_source`` orphan:
        the next text would be parsed as the source ref and execute
        (or СТОП) with no question ever visible on screen.
        """
        bot = make_bot()
        bot.send_message.side_effect = RuntimeError("send rejected")

        await dispatch_tolerating_a_raise(make_forward(11, user_origin(date=D1), bot=bot))

        assert pending() is None, (
            "a failed question send must reset the session: an asking_source "
            "orphan would make the next text run blindly"
        )
        bot.send_message.side_effect = question_sender()
        follow_up = make_admin_text(TITLE, bot=bot)

        await dispatch(follow_up)

        assert pending() is None, "the flow must not resurrect on the next text"
        assert follow_up.answer.await_count == 0, "no СТОП, no execution — no reply at all"
        assert bot.send_message.await_count == 1, "only the one failed question attempt"
        assert bot.edit_message_text.await_count == 0, "no thread was built"
        assert bot.copy_message.await_count == 0, "the batch never executed"
        assert bot.delete_message.await_count == 0, "no prompts exist to delete"

    async def test_a_failing_title_question_resets_the_session(self):
        """The asking_source → asking_title question send raises (F3.2).

        The pair is already fixed on the session when the crash hits —
        a kept session would let the next text execute with no title
        question ever shown.
        """
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        await dispatch(make_forward(11, user_origin(date=D1), bot=bot))
        assert pending().stage == "asking_source", "sanity: the source question is out"
        bot.send_message.side_effect = RuntimeError("send rejected")

        await dispatch_tolerating_a_raise(make_admin_text(str(SOURCE_ID), bot=bot))

        assert pending() is None, (
            "the transition crashed mid-way: the half-migrated session "
            "(source fixed, stage asking_title) must reset"
        )
        bot.send_message.side_effect = question_sender()
        follow_up = make_admin_text(TITLE, bot=bot)

        await dispatch(follow_up)

        assert pending() is None, "the flow must not resurrect on the next text"
        assert follow_up.answer.await_count == 0, "no blind execution after the crash"
        assert bot.send_message.await_count == 2, "the source question + the failed one"
        bot.edit_message_text.assert_not_awaited(), "no thread was built"
        bot.copy_message.assert_not_awaited(), "the batch never executed"

    async def test_a_failing_final_answer_still_resets_the_session(self):
        """The success answer raises AFTER everything executed (F3.3).

        Every call before the answer (header, edit, copies, target
        deletes, source cleanup, prompt deletes) already happened and
        must stay; the session resets anyway — otherwise a retry would
        execute the whole move a second time.
        """
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        await seed_batch(
            bot, [make_forward(11, channel_origin(message_id=501), bot=bot, text="one")]
        )
        message = make_admin_text(TITLE, bot=bot)
        message.answer.side_effect = RuntimeError("answer rejected")

        await dispatch_tolerating_a_raise(message)

        assert pending() is None, (
            "the move already executed: a kept session would run it twice"
        )
        headers = [
            call_obj
            for call_obj in bot.send_message.await_args_list
            if str(arg(call_obj, "text", 1)).startswith("Topic:")
        ]
        assert len(headers) == 1, "the header was sent before the answer crashed"
        assert len(bot.edit_message_text.await_args_list) == 1, "and the link edit"
        assert [arg(c, "message_id", 2) for c in bot.copy_message.await_args_list] == [11], (
            "the batch was placed before the answer crashed"
        )
        assert [
            (arg(c, "chat_id", 0), arg(c, "message_id", 1))
            for c in bot.delete_message.await_args_list
        ] == [
            (TARGET_CHAT_ID, 11),
            (SOURCE_ID, 501),
            (TARGET_CHAT_ID, PROMPT_ID),
        ], "forwarded copy, source original and the prompt were all deleted before it"


class TestConcurrentHandlersSerializePerChat:
    """F6: a per-chat ``asyncio.Lock`` closes the two-admins race.

    Both handlers must take the chat's lock BEFORE they read the
    session state; the pins are behavioural (call counts under
    ``asyncio.gather``), never the lock's internals.
    """

    async def test_two_concurrent_titles_execute_exactly_once(self, monkeypatch):
        """Two titles via ``asyncio.gather`` on ONE session → one execution.

        Without the lock both handlers read ``asking_title`` before
        either resets: the batch is built TWICE (two headers, two
        answers). The second pass must instead find the session already
        gone and stay idle.
        """
        pin_filter_scheduling(monkeypatch)
        bot = yielding_bot(make_bot())
        session = sessions_mod().sessions.start(TARGET_CHAT_ID, session_ref())
        session.add_prompt_id(PROMPT_ID)
        first = make_admin_text(TITLE, bot=bot, user_id=ADMIN_ID)
        second = make_admin_text(TITLE, bot=bot, user_id=OTHER_ADMIN_ID)

        await asyncio.gather(dispatch(first), dispatch(second))

        assert bot.get_chat_member.await_count == 2, (
            "sanity: both admins really passed the gate concurrently"
        )
        headers = [
            call_obj
            for call_obj in bot.send_message.await_args_list
            if str(arg(call_obj, "text", 1)).startswith("Topic:")
        ]
        assert len(headers) == 1, (
            f"exactly ONE header may be posted, got {len(headers)} — the batch "
            "was executed twice without serialization"
        )
        assert bot.edit_message_text.await_count == 1, "one link edit, not two"
        assert bot.copy_message.await_count == 1, "one placement, not two"
        answered = [m for m in (first, second) if m.answer.await_count]
        assert len(answered) == 1, "exactly ONE admin gets an answer"
        assert reply_text(answered[0]) == created_reply(1, 1, 1), (
            "the one execution completed normally"
        )
        assert pending() is None, "the single execution resets the session"

    async def test_two_concurrent_forwards_both_join_the_batch(self, monkeypatch):
        """Gather of two forwards → batch of TWO, exactly one question (F6.2).

        The lock must not break accumulation: no forward is lost, no
        duplicate session overwrites the first, and the «which chat?»
        question is asked once as a reply to the FIRST batch message.
        """
        pin_filter_scheduling(monkeypatch)
        await set_pairs([*PAIRS, {"source": SECOND_SOURCE_ID, "target": OTHER_TARGET_REF}])
        bot = yielding_bot(make_bot())
        first = make_forward(
            21, user_origin(date=D1), bot=bot,
            chat_id=OTHER_TARGET_ID, username=OTHER_TARGET_USERNAME,
        )
        second = make_forward(
            22, user_origin(date=D2), bot=bot,
            chat_id=OTHER_TARGET_ID, username=OTHER_TARGET_USERNAME,
        )

        await asyncio.gather(dispatch(first), dispatch(second))

        session = pending(OTHER_TARGET_ID)
        assert session is not None, "the batch must exist in that chat"
        assert [m["message_id"] for m in session.messages] == [21, 22], (
            "neither concurrent forward may be lost or duplicated"
        )
        assert session.stage == "asking_source", "a chatless origin still asks"
        assert bot.send_message.await_count == 1, "exactly ONE question, no duplicates"
        assert arg(bot.send_message.await_args, "reply_to_message_id") == 21, (
            "the question hangs on the FIRST message of the batch"
        )
        assert pending(TARGET_CHAT_ID) is None, "the other chat stays untouched"


class TestInvalidTargetAnswerEscaping:
    """F7: the offending configured target is HTML-escaped in the refusal."""

    async def test_the_offending_target_is_escaped_not_interpolated_raw(self):
        """``target = "<b>x"`` cannot form a thread link → escaped answer.

        The refusal embeds the configured value inside an HTML-parsed
        message: raw ``<b>`` from the config would open a tag (markup
        injection straight from the pair registry), so the answer must carry
        ``&lt;b&gt;`` instead, with ``parse_mode="HTML"`` and a reset.
        """
        target = "<b>x"
        assert html.escape(target) == "&lt;b&gt;x", "sanity: the escape really changes it"
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        session = sessions_mod().sessions.start(TARGET_CHAT_ID, session_ref())
        session.stage = "asking_title"
        session.source = {"source": SOURCE_ID, "target": target}
        session.add_prompt_id(PROMPT_ID)

        message = await run_title(bot)

        reply = reply_text(message)
        assert reply == INVALID_TARGET_TEMPLATE.format(target=html.escape(target)), (
            f"the configured target must be HTML-escaped in the answer: {reply!r}"
        )
        assert "<b>" not in reply, "raw markup of the config must never reach the answer"
        assert "&lt;b&gt;" in reply, "the escaped form must be visible in the answer"
        assert message.answer.await_args.kwargs.get("parse_mode") == "HTML"
        assert message.answer.await_count == 1, "the fixed line is answered exactly once"
        assert pending() is None, "the state resets"
        bot.send_message.assert_not_awaited(), "the guard still runs BEFORE the header"
        bot.edit_message_text.assert_not_awaited()
        bot.copy_message.assert_not_awaited()


class TestNonTextMessagesAndCancelMention:
    """F8: only real TEXT drives the state machine; ``/cancel@botname`` cancels."""

    async def test_a_sticker_is_not_an_answer_and_keeps_the_session(self):
        """A media without text must not read as «не настроено → СТОП».

        The text handler must not treat a missing ``text`` as an empty
        string: that fake answer resolves no pair, answers the fixed
        СТОП line, deletes the prompts and kills the session — while the
        admin only sent a sticker.
        """
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        await dispatch(make_forward(11, user_origin(date=D1), bot=bot))
        assert pending().stage == "asking_source", "sanity: the source question is out"
        sends_so_far = bot.send_message.await_count
        sticker = make_admin_text(None, bot=bot)
        sticker.content_type = "sticker"

        await dispatch(sticker)

        assert sticker.answer.await_count == 0, (
            "a sticker is not an answer: NO reply at all — the СТОП of an "
            "empty text is the bug"
        )
        assert bot.delete_message.await_count == 0, "the question stays on screen"
        assert bot.send_message.await_count == sends_so_far, "and no new question"
        session = pending()
        assert session is not None, "the session survives a non-text message"
        assert session.stage == "asking_source", "and its stage does not move"

    @pytest.mark.parametrize(
        "origin,expected_stage",
        [
            pytest.param(user_origin(date=D1), "asking_source", id="asking-source"),
            pytest.param(channel_origin(message_id=501), "asking_title", id="asking-title"),
        ],
    )
    async def test_cancel_mentioning_the_bot_cancels(self, origin, expected_stage):
        """``/cancel@vladimirzhrobot`` is ``/cancel`` in BOTH stages.

        Prompt deleted, exactly ``Cancelled.``, session reset — and no
        thread: in ``asking_source`` the mention must not read as an
        unconfigured source (СТОП), in ``asking_title`` it must not
        become the thread's title.
        """
        bot = make_bot()
        bot.username = BOT_USERNAME
        bot.send_message.side_effect = question_sender()
        await dispatch(make_forward(11, origin, bot=bot))
        assert pending().stage == expected_stage, "sanity: the seeded stage"
        message = make_admin_text(f"/cancel@{BOT_USERNAME}", bot=bot)

        await dispatch(message)

        assert reply_text(message) == CANCELLED_REPLY, (
            "the bot-mentioned form of /cancel must cancel too"
        )
        assert message.answer.await_args.kwargs.get("parse_mode") == "HTML"
        deletes = bot.delete_message.await_args_list
        assert [(arg(c, "chat_id", 0), arg(c, "message_id", 1)) for c in deletes] == [
            (TARGET_CHAT_ID, PROMPT_ID),
        ], "exactly the question message is deleted"
        assert pending() is None, "the session resets"
        bot.edit_message_text.assert_not_awaited(), "no thread may be built"
        bot.copy_message.assert_not_awaited()
        assert bot.send_message.await_count == 1, "only the seeded question was ever sent"

    async def test_cancelish_is_a_plain_title_not_a_cancel(self):
        """``/cancelish`` must NOT match the command (over-broad prefix bug)."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        await dispatch(make_forward(11, channel_origin(message_id=501), bot=bot))

        message = await run_title(bot, "/cancelish")

        reply = reply_text(message)
        assert CANCELLED_REPLY not in reply, "only the exact command cancels"
        assert reply == created_reply(1, 1, 1), "the text is an ordinary thread title"
        assert pending() is None, "the normal flow completed"

    async def test_a_bare_cancel_word_is_a_plain_title(self):
        """``cancel`` without the slash is a title, never the command."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        await dispatch(make_forward(11, channel_origin(message_id=501), bot=bot))

        message = await run_title(bot, "cancel")

        reply = reply_text(message)
        assert CANCELLED_REPLY not in reply, "no slash → not the command"
        assert reply == created_reply(1, 1, 1), "the word became the thread title"
        assert pending() is None, "the normal flow completed"


class TestMediaExtractionAndAlbums:
    """F9: ``file_unique_id``/``file_id`` come from the message's REAL media.

    The ref builder must look beyond ``photo`` — priority ``photo`` >
    ``video`` > ``audio`` > ``document`` > ``animation`` > ``voice`` —
    so cleanup can find media originals by id and albums travel as
    ``send_media_group`` of original file ids instead of degrading.
    """

    @pytest.mark.parametrize(
        "kind", ["photo", "video", "audio", "document", "animation", "voice"]
    )
    async def test_cleanup_finds_the_original_by_the_media_file_unique_id(self, kind: str):
        """A media forward of every kind finds its buffered original by file id."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        await dispatch(
            make_forward(
                11, user_origin(date=D1), bot=bot, kind=kind,
                file_id=f"file-{kind}", file_unique_id=f"fu-{kind}",
            )
        )
        answer_message = await run_title(bot, str(SOURCE_ID))
        assert pending().stage == "asking_title", "sanity: the source is resolved"
        assert answer_message.answer.await_count == 0, "sanity: the transition is silent"
        import bot.buffer as buffer_module

        await buffer_module.buffer.record(
            SOURCE_ID, 404, OTHER_ADMIN_ID, D1, None, None, f"fu-{kind}"
        )

        message = await run_title(bot, TITLE)

        cleaned = [arg(call_obj, "message_id", 1) for call_obj in source_deletes(bot)]
        assert cleaned == [404], (
            f"a {kind} original must be found by (forward-origin date + "
            f"file_unique_id), got deletes {cleaned!r}"
        )
        assert reply_text(message) == created_reply(1, 1, 1), (
            "found without guessing — the cleanup reports 1 of 1"
        )
        assert pending() is None

    @pytest.mark.parametrize(
        "present,expected",
        [
            pytest.param(
                ("photo", "video", "audio", "document", "animation", "voice"),
                "fu-photo",
                id="photo-wins",
            ),
            pytest.param(
                ("video", "audio", "document", "animation", "voice"),
                "fu-video",
                id="video-wins-without-photo",
            ),
            pytest.param(
                ("audio", "document", "animation", "voice"),
                "fu-audio",
                id="audio-wins-without-photo-video",
            ),
            pytest.param(
                ("document", "animation", "voice"),
                "fu-document",
                id="document-wins-without-the-above",
            ),
            pytest.param(("animation", "voice"), "fu-animation", id="animation-wins"),
            pytest.param(("voice",), "fu-voice", id="voice-only"),
        ],
    )
    async def test_the_cleanup_lookup_takes_the_highest_priority_media(
        self, monkeypatch, present, expected
    ):
        """A message carrying several media attributes → the priority order."""
        import bot.handlers.watcher as watcher_module

        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        fake_buffer = RecordingBuffer()
        monkeypatch.setattr(watcher_module, "buffer", fake_buffer)
        forward = make_forward(11, user_origin(date=D1), bot=bot)
        for media_kind in present:
            payload = SimpleNamespace(
                file_id=f"file-{media_kind}", file_unique_id=f"fu-{media_kind}"
            )
            setattr(
                forward, media_kind, [payload] if media_kind == "photo" else payload
            )

        await dispatch(forward)
        await run_title(bot, str(SOURCE_ID))
        await run_title(bot, TITLE)

        assert len(fake_buffer.calls) == 1, "one batch element → exactly one lookup"
        args, _ = fake_buffer.calls[0]
        assert args[4] == expected, (
            "the extracted file_unique_id must come from the highest-priority "
            f"media present (expected {expected!r}), got {args[4]!r}"
        )

    @pytest.mark.parametrize("kind", ["video", "document"])
    async def test_a_media_album_is_one_send_media_group_of_original_file_ids(self, kind: str):
        """A video/document album stays glued and carries ORIGINAL file ids."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        forwards = [
            make_forward(
                11, channel_origin(date=D1, message_id=501), bot=bot, kind=kind,
                media_group_id=f"mg-{kind}", file_id=f"file-{kind}-1",
                file_unique_id=f"fu-{kind}-1", caption="clip one",
            ),
            make_forward(
                12, channel_origin(date=D2, message_id=502), bot=bot, kind=kind,
                media_group_id=f"mg-{kind}", file_id=f"file-{kind}-2",
                file_unique_id=f"fu-{kind}-2",
            ),
        ]
        await seed_batch(bot, forwards)

        message = await run_title(bot)

        groups = bot.send_media_group.await_args_list
        assert len(groups) == 1, "one shared album → exactly one send_media_group call"
        media = arg(groups[0], "media")
        assert [media_field(item, "type") for item in media] == [kind, kind]
        assert [media_field(item, "media") for item in media] == [
            f"file-{kind}-1",
            f"file-{kind}-2",
        ], "media must travel by the ORIGINAL file_id of that kind (F9)"
        assert media_field(media[0], "caption") == "clip one", "the caption travels along"
        assert bot.copy_message.await_count == 0, (
            "the album must not degrade into per-element copies"
        )
        assert reply_text(message) == created_reply(2, 2, 2)
        assert pending() is None

    @pytest.mark.parametrize("kind", ["animation", "voice"])
    async def test_an_album_holding_a_non_groupable_element_is_placed_as_copies(
        self, kind: str
    ):
        """``sendMediaGroup`` rejects animation/voice → the WHOLE group is copied.

        The fallback behaviour must survive media extraction: no
        ``send_media_group`` attempt leaks through, every element goes
        out as its own copy hanging on the header, and the move still
        completes with the success answer.
        """
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        forwards = [
            make_forward(
                11, channel_origin(date=D1, message_id=501), bot=bot, kind=kind,
                media_group_id="mg-mixed", file_id="file-X1", file_unique_id="fu-X1",
            ),
            make_forward(
                12, channel_origin(date=D2, message_id=502), bot=bot, kind="photo",
                media_group_id="mg-mixed", file_id="file-X2", file_unique_id="fu-X2",
            ),
        ]
        await seed_batch(bot, forwards)

        message = await run_title(bot)

        copies = bot.copy_message.await_args_list
        assert [arg(call_obj, "message_id", 2) for call_obj in copies] == [11, 12], (
            "a group holding a non-groupable element goes out as "
            "per-element copies, in batch order"
        )
        for call_obj in copies:
            assert arg(call_obj, "chat_id", 0) == TARGET_CHAT_ID
            assert arg(call_obj, "from_chat_id", 1) == TARGET_CHAT_ID
            assert arg(call_obj, "reply_to_message_id") == HEADER_ID, (
                "fallback copies still hang on the header"
            )
        reply = reply_text(message)
        assert reply == created_reply(2, 2, 2), f"a fallback is not a failure: {reply!r}"
        assert pending() is None


class TestTargetDeletesAreBestEffort:
    """F10: deleting the forwarded copies never fails an already-built thread.

    The copy deletes (step 4/5, target chat) sit OUTSIDE the
    ``Move failed`` failure block: by that point the header, the link
    and the placement are done and the cleanup still has to run.
    """

    async def test_raising_target_deletes_still_answer_thread_created(self):
        """EVERY batch delete raises → success line + cleanup, no Move failed."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        await seed_batch(
            bot,
            [
                make_forward(11, channel_origin(date=D1, message_id=501), bot=bot, text="one"),
                make_forward(12, channel_origin(date=D2, message_id=502), bot=bot, text="two"),
            ],
        )

        def delete_side(*args, **kwargs):
            chat_id = kwargs.get("chat_id", args[0] if args else None)
            mid = kwargs.get("message_id", args[1] if len(args) > 1 else None)
            if chat_id == TARGET_CHAT_ID and mid in (11, 12):
                raise RuntimeError("delete rejected")
            return True

        bot.delete_message.side_effect = delete_side

        message = await run_title(bot)

        reply = reply_text(message)
        assert reply == created_reply(2, 2, 2), (
            "the thread is already built: the answer is the normal success "
            f"line with honest cleanup counts, got {reply!r}"
        )
        assert "Move failed" not in reply, (
            "a failing delete of the forwarded copies must never surface "
            "as Move failed"
        )
        assert bot.copy_message.await_count == 2, "placement finished before the deletes"
        assert [arg(c, "message_id", 1) for c in source_deletes(bot)] == [501, 502], (
            "the source cleanup still runs to the end"
        )
        target_msgs = [
            arg(call_obj, "message_id", 1)
            for call_obj in bot.delete_message.await_args_list
            if arg(call_obj, "chat_id", 0) == TARGET_CHAT_ID
        ]
        assert target_msgs == [11, 12, PROMPT_ID], (
            "both batch deletes are attempted best-effort, then the prompt "
            f"is deleted, got {target_msgs!r}"
        )
        assert pending() is None, "the state resets"


# ========================================================================
# Placement order: the chain follows the ORIGINAL timeline, not arrival
# ========================================================================


def placement_spy(bot):
    """Record every placement call of ``bot`` in call order.

    Returns the shared log: each ``copy_message`` appends
    ``("copy", message_id)`` and each ``send_media_group`` appends
    ``("group", (media file ids…))`` — enough to pin both the ORDER of
    the chain and the exact membership of every album (the interleaving
    of the two placement primitives is the whole point of the
    chronological-sort tests, which ``build_timeline_spy`` does not
    cover: it never watches ``send_media_group``).
    """
    placed: list = []

    def copy_side(*args, **kwargs):
        placed.append(("copy", kwargs.get("message_id", args[2] if len(args) > 2 else None)))

    def group_side(*args, **kwargs):
        media = kwargs.get("media", args[1] if len(args) > 1 else [])
        placed.append(("group", tuple(media_field(item, "media") for item in media)))

    bot.copy_message.side_effect = copy_side
    bot.send_media_group.side_effect = group_side
    return placed


class TestPlacementFollowsOriginDate:
    """Step 4 places the chain by FORWARD-ORIGIN date, never by arrival.

    aiogram processes the updates of one batch concurrently, so
    ``session.messages`` reflects the ARRIVAL order of the forwards —
    which is NOT the order the originals were sent in. The placement
    must sort the refs by ``origin.date`` ascending, ties broken by
    ``origin.message_id`` (stability: same-date members keep their
    arrival order), a missing ``date`` reading as EARLIER than every
    dated ref and a missing ``message_id`` never breaking the sort.
    """

    async def test_copy_chain_follows_origin_date_not_arrival_order(self):
        """A batch arriving 10:05 → 10:00 → 10:02 is placed 10:00 → 10:02 → 10:05.

        The three single messages are seeded with a NON-chronological
        arrival order (the live-bug shape): the ``copy_message`` chain
        must follow ``origin.date`` — copies of the forwards 1000, 1002,
        1005 — not the order the refs were added in (1005, 1000, 1002).
        The move still succeeds: ``Thread created: 3 message(s)…`` and
        every original cleaned up (``Deleted 3 of 3``).
        """
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        seed_directly(
            session_ref(message_id=1005, origin_message_id=501, date=D1005, text="late"),
            session_ref(message_id=1000, origin_message_id=502, date=D1000, text="early"),
            session_ref(message_id=1002, origin_message_id=503, date=D1002, text="mid"),
        )
        session = pending()
        assert [ref["message_id"] for ref in session.messages] == [1005, 1000, 1002], (
            "sanity: the seeded ARRIVAL order is really non-chronological"
        )

        message = await run_title(bot)

        copies = bot.copy_message.await_args_list
        assert [arg(call_obj, "message_id", 2) for call_obj in copies] == [1000, 1002, 1005], (
            "the chain must run by origin.date ascending (10:00 → 10:02 → 10:05), "
            f"never by arrival order, got {[arg(c, 'message_id', 2) for c in copies]!r}"
        )
        assert reply_text(message) == CREATED_REPLY, (
            "the move succeeds unchanged: Thread created: 3 message(s) + Deleted 3 of 3"
        )
        assert sorted(arg(call_obj, "message_id", 1) for call_obj in source_deletes(bot)) == [
            501,
            502,
            503,
        ], "every original is cleaned up (the cleanup's own order is not pinned here)"
        assert pending() is None, "the state resets"

    async def test_equal_origin_dates_keep_their_arrival_order(self):
        """Two refs sharing one ``origin.date`` are placed in their arrival order.

        The batch arrives as later(10:05) → equal-A(10:00) →
        equal-B(10:00): the sort must pull the equal-date pair to the
        front TOGETHER, A still before B — a non-stable sort, a
        descending one, or a naive reordering would flip the pair.
        """
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        seed_directly(
            session_ref(message_id=21, origin_message_id=601, date=D1005, text="late"),
            session_ref(message_id=22, origin_message_id=602, date=D1000, text="equal A"),
            session_ref(message_id=23, origin_message_id=603, date=D1000, text="equal B"),
        )
        session = pending()
        assert [ref["origin"]["date"] for ref in session.messages] == [D1005, D1000, D1000], (
            "sanity: one later ref, then the equal-date pair"
        )

        message = await run_title(bot)

        copies = bot.copy_message.await_args_list
        assert [arg(call_obj, "message_id", 2) for call_obj in copies] == [22, 23, 21], (
            "the equal-date pair (22, 23) keeps its arrival order AND both "
            f"precede the later original — a stable ascending sort, got "
            f"{[arg(c, 'message_id', 2) for c in copies]!r}"
        )
        assert reply_text(message) == CREATED_REPLY, "the move completes normally"
        assert pending() is None, "the state resets"

    async def test_missing_origin_dates_sort_first_and_never_crash(self):
        """``origin.date = None`` reads as earlier than everything, no exception.

        The dated forward arrives FIRST, so an unsorted placement is
        observable. One date-less ref also carries
        ``origin.message_id = None`` (group forwards have no id): the
        tie-break must tolerate it instead of comparing ``None`` with
        an int, and the naive ``sorted(..., key=date)`` of a fix must
        not raise ``Move failed`` either — every message is placed.
        """
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        undated_with_id = session_ref(
            message_id=32, origin_message_id=702, date=None, text="no date"
        )
        undated_without_id = session_ref(
            message_id=33, origin_message_id=703, date=None, text="no date, no id"
        )
        undated_without_id["origin"].update(type="chat", message_id=None)
        seed_directly(
            session_ref(message_id=31, origin_message_id=701, date=D1000, text="dated"),
            undated_with_id,
            undated_without_id,
        )

        message = await run_title(bot)

        reply = reply_text(message)
        assert reply.startswith("Thread created: 3 message(s)."), (
            "a missing origin date must not abort the move — no exception, "
            f"no Move failed, all three messages placed, got {reply!r}"
        )
        copies = [arg(call_obj, "message_id", 2) for call_obj in bot.copy_message.await_args_list]
        assert len(copies) == 3, f"every batch element is placed, got {copies!r}"
        assert copies[2] == 31, f"the only DATED original goes last, got {copies!r}"
        assert set(copies[:2]) == {32, 33}, (
            "both date-less refs are placed BEFORE the dated one (date is None "
            f"reads as earlier than all); their mutual order is not pinned, got {copies!r}"
        )
        assert pending() is None, "the state resets"


class TestAlbumGluedAfterChronologicalSort:
    """The date sort must not tear albums apart («альбомы остаются склеенными»).

    Album members share their ``origin.date``, so a stable ascending
    sort keeps them ADJACENT — exactly what ``_place_batch`` needs to
    recognise the run and emit ONE ``send_media_group``.
    """

    async def test_an_interleaved_album_is_placed_as_one_group_after_the_sort(self):
        """Album A(10:05) … single(10:00) … album B(10:05) → copy first, ONE group.

        Sorting by ``origin.date`` pulls the single text (10:00) in
        front of the album; the two same-date members must land next to
        each other so the placement emits exactly ONE
        ``send_media_group`` holding both (in message-id order),
        replying to the header — after the copy of the single message.
        """
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        forwards = [
            make_forward(
                41, channel_origin(date=D1005, message_id=801), bot=bot, kind="video",
                media_group_id="g1", file_id="file-A", file_unique_id="fu-A",
                caption="clip A",
            ),
            make_forward(42, channel_origin(date=D1000, message_id=802), bot=bot, text="single"),
            make_forward(
                43, channel_origin(date=D1005, message_id=803), bot=bot, kind="video",
                media_group_id="g1", file_id="file-B", file_unique_id="fu-B",
            ),
        ]
        await seed_batch(bot, forwards)
        placed = placement_spy(bot)

        message = await run_title(bot)

        assert placed == [("copy", 42), ("group", ("file-A", "file-B"))], (
            "the single 10:00 text is copied first, then ONE send_media_group "
            f"holding both album members in message-id order, got {placed!r}"
        )
        groups = bot.send_media_group.await_args_list
        assert len(groups) == 1, (
            "the chronological sort must not split the album into two calls"
        )
        assert reply_message_id(arg(groups[0], "reply_parameters")) == HEADER_ID, (
            "the album replies to the header (Bot API 7 reply_parameters)"
        )
        assert reply_text(message) == CREATED_REPLY, (
            "all three originals cleaned up — the move completed normally"
        )
        assert pending() is None, "the state resets"


# ========================================================================
# Inline source picker: the question's keyboard and its callbacks
# ========================================================================


class TestSourceQuestionKeyboard:
    """The «Which chat…?» question carries the source buttons + Cancel (pin A1).

    Labels come through ``bot.get_chat``: ``.title`` → ``.username``
    rendered as ``@name`` → the ref itself, a RAISING ``get_chat``
    reading as the ref too. Buttons follow the registry order, only
    pairs whose target is the CURRENT chat qualify, ``Cancel`` is last.
    """

    async def test_the_source_question_lists_the_sources_of_this_chat_and_cancel(self):
        """Registry order, the get_chat fallback chain, Cancel last (A1)."""
        await set_pairs(
            [
                {"source": SOURCE_ID, "target": TARGET_REF},
                {"source": SECOND_SOURCE_ID, "target": str(TARGET_CHAT_ID)},
                {"source": "@mychannel", "target": TARGET_REF},
                {"source": THIRD_SOURCE_ID, "target": OTHER_TARGET_REF},
            ]
        )
        bot = make_bot(
            chats={
                str(SOURCE_ID): SimpleNamespace(title="Alpha", username="srcgroup"),
                str(SECOND_SOURCE_ID): SimpleNamespace(
                    title=None, username=SECOND_SOURCE_USERNAME
                ),
                "@mychannel": TelegramBadRequest(method=None, message="chat unavailable"),
            }
        )
        bot.send_message.side_effect = question_sender()

        await dispatch(make_forward(11, user_origin(date=D1), bot=bot))

        question = bot.send_message.await_args
        assert arg(question, "text", 1) == QUESTION_SOURCE, (
            "the question text itself must not change"
        )
        assert arg(question, "reply_to_message_id") == 11, (
            "the keyboard hangs on the question message of the batch"
        )
        assert keyboard_buttons(question.kwargs.get("reply_markup")) == [
            ("Alpha", "w:src:-100111"),
            ("@seconds", "w:src:-100333"),
            ("@mychannel", "w:src:@mychannel"),
            ("Cancel", "w:cancel"),
        ], (
            "registry order of the sources targeting THIS chat (the pair of "
            "another chat excluded), labels through title -> username -> ref "
            "(the raising get_chat falls back to the ref), Cancel last"
        )

    async def test_a_source_configured_by_two_pairs_renders_one_button(self):
        """Duplicates by canonical ref collapse at their FIRST occurrence."""
        await set_pairs(
            [
                {"source": SOURCE_ID, "target": TARGET_REF},
                {"source": SECOND_SOURCE_ID, "target": TARGET_REF},
                {"source": SOURCE_ID, "target": str(TARGET_CHAT_ID)},
            ]
        )
        bot = make_bot(
            chats={
                str(SOURCE_ID): SimpleNamespace(title="Alpha"),
                str(SECOND_SOURCE_ID): SimpleNamespace(title="Gamma"),
            }
        )
        bot.send_message.side_effect = question_sender()

        await dispatch(make_forward(11, user_origin(date=D1), bot=bot))

        assert keyboard_buttons(bot.send_message.await_args.kwargs.get("reply_markup")) == [
            ("Alpha", "w:src:-100111"),
            ("Gamma", "w:src:-100333"),
            ("Cancel", "w:cancel"),
        ], "a source asked by two pairs must offer ONE button, at its first place"

    async def test_the_title_question_carries_no_keyboard(self):
        """Pin A2: only the source question gets the picker (GREEN in RED)."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()

        await dispatch(make_forward(11, channel_origin(message_id=501), bot=bot))

        question = bot.send_message.await_args
        assert arg(question, "text", 1) == QUESTION_TITLE
        assert question.kwargs.get("reply_markup") is None, (
            "the title question must stay a plain prompt — no picker"
        )

    async def test_the_re_asked_source_question_carries_the_keyboard_too(self):
        """The SECOND call site of the question (the F1 conflict re-ask)."""
        await set_pairs([*PAIRS, {"source": SECOND_SOURCE_ID, "target": TARGET_REF}])
        bot = make_bot(
            chats={
                str(SOURCE_ID): SimpleNamespace(title="Alpha"),
                str(SECOND_SOURCE_ID): SimpleNamespace(title="Gamma"),
            }
        )
        bot.send_message.side_effect = question_sender()

        await dispatch(make_forward(11, channel_origin(date=D1, message_id=501), bot=bot))
        await dispatch(make_forward(12, second_pair_origin(date=D2), bot=bot))

        question = bot.send_message.await_args
        assert arg(question, "text", 1) == QUESTION_SOURCE, (
            "the conflict re-asks the very same question"
        )
        assert keyboard_buttons(question.kwargs.get("reply_markup")) == [
            ("Alpha", "w:src:-100111"),
            ("Gamma", "w:src:-100333"),
            ("Cancel", "w:cancel"),
        ], "the re-asked question is a source question and gets the picker as well"

    async def test_an_empty_registry_asks_without_a_keyboard(self):
        """No candidates → no ``reply_markup`` (the status quo, GREEN in RED).

        The forward filter already refuses an unconfigured chat, so the
        no-candidates branch is only reachable by calling the handler
        directly — this pins what the picker must NOT do when the
        registry names no source of this chat.
        """
        await set_pairs([])
        bot = make_bot()
        bot.send_message.side_effect = question_sender()

        await watcher_mod().on_forward(make_forward(11, user_origin(date=D1), bot=bot))

        question = bot.send_message.await_args
        assert arg(question, "text", 1) == QUESTION_SOURCE
        assert question.kwargs.get("reply_markup") is None, (
            "no source buttons to offer — the question must stay keyboardless"
        )


class TestSourcePickerCallback:
    """``w:src:<ref>`` — the click answer of the source question (pin A3)."""

    @pytest.mark.parametrize(
        "ref,pair",
        [
            pytest.param(
                str(SOURCE_ID),
                {"source": SOURCE_ID, "target": TARGET_REF},
                id="numeric-ref",
            ),
            pytest.param(
                "@srcgroup",
                {"source": "@srcgroup", "target": TARGET_REF},
                id="username-ref",
            ),
        ],
    )
    async def test_a_pick_resolves_the_pair_and_asks_the_title_question(self, ref, pair):
        """A resolving button behaves exactly like the typed answer (step 2 → 3)."""
        await set_pairs([pair])
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        seed_asking_source()
        callback = make_watcher_callback(f"w:src:{ref}", bot=bot)

        consumed = await dispatch_callback(callback)

        assert consumed is not None, "w:src:<ref> must be registered on the watcher router"
        session = pending()
        assert session is not None, "a resolving click keeps the batch alive"
        assert session.stage == "asking_title"
        assert session.source == pair, "the clicked source pair is fixed on the session"
        assert bot.send_message.await_count == 1, "the title question is asked next"
        question = bot.send_message.await_args
        assert arg(question, "text", 1) == QUESTION_TITLE
        assert arg(question, "chat_id", 0) == TARGET_CHAT_ID
        assert arg(question, "reply_to_message_id") == 11, (
            "the question hangs on the FIRST message of the batch"
        )
        assert callback.message.answer.await_count == 0, (
            "the transition is announced by the next question, not an answer"
        )
        member = bot.get_chat_member.await_args
        assert arg(member, "chat_id", 0) == TARGET_CHAT_ID, (
            "the click's admin gate runs in the chat the button lives in"
        )
        assert arg(member, "user_id", 1) == ADMIN_ID
        assert bot.get_chat_member.await_count == 1, "the click performs its OWN admin lookup"
        assert_spinner_cleared(callback)

    @pytest.mark.parametrize(
        "ref",
        [
            pytest.param("@unknown", id="ref-not-in-the-registry"),
            pytest.param(str(THIRD_SOURCE_ID), id="pair-of-another-chat"),
        ],
    )
    async def test_a_pick_naming_no_source_of_this_chat_stops_the_flow(self, ref):
        """The terminal refusal of the text path: prompts gone, session reset."""
        await set_pairs([*PAIRS, {"source": THIRD_SOURCE_ID, "target": OTHER_TARGET_REF}])
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        seed_asking_source()
        callback = make_watcher_callback(f"w:src:{ref}", bot=bot)

        consumed = await dispatch_callback(callback)

        assert consumed is not None, "the click is consumed — to refuse the flow"
        assert reply_text(callback.message) == NOT_CONFIGURED_REPLY, (
            "the refusal must be visible in the chat"
        )
        deletes = bot.delete_message.await_args_list
        assert [(arg(c, "chat_id", 0), arg(c, "message_id", 1)) for c in deletes] == [
            (TARGET_CHAT_ID, PROMPT_ID)
        ], "the question message goes away with the session"
        assert pending() is None, "the refusal is terminal — the batch resets"
        assert bot.send_message.await_count == 0, "no title question after the refusal"
        assert_spinner_cleared(callback)

    async def test_a_pick_during_the_title_stage_is_ignored(self):
        """A TEXT here would become the title — a BUTTON must change nothing."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        seed_directly(session_ref())
        callback = make_watcher_callback(f"w:src:{SOURCE_ID}", bot=bot)

        consumed = await dispatch_callback(callback)

        assert consumed is not None, "the click is consumed — and then ignored"
        session = pending()
        assert session.stage == "asking_title", "the stage must not move"
        assert session.source == {"source": SOURCE_ID, "target": TARGET_REF}
        assert callback.message.answer.await_count == 0, (
            "no text may appear — a text answer here would become the title"
        )
        assert bot.send_message.await_count == 0, "no new question"
        assert bot.delete_message.await_count == 0, "the prompt stays for the title"
        assert_spinner_cleared(callback)

    async def test_a_pick_without_a_session_is_ignored(self):
        """No pending batch → the click changes nothing at all."""
        bot = make_bot()
        callback = make_watcher_callback(f"w:src:{SOURCE_ID}", bot=bot)

        consumed = await dispatch_callback(callback)

        assert consumed is not None, "w:src: is consumed even without a session to serve"
        assert pending() is None, "a stray click must not create a session"
        assert callback.message.answer.await_count == 0
        bot.send_message.assert_not_awaited()
        bot.delete_message.assert_not_awaited()
        assert_spinner_cleared(callback)

    async def test_a_non_admins_pick_is_ignored(self):
        """The click's own admin gate: a member's button changes nothing."""
        bot = make_bot(status="member")
        seed_asking_source()
        callback = make_watcher_callback(f"w:src:{SOURCE_ID}", bot=bot)

        consumed = await dispatch_callback(callback)

        assert consumed is not None, "the click is consumed — to be gated"
        session = pending()
        assert session is not None and session.stage == "asking_source"
        assert session.source is None, "the refused click must not fix a pair"
        assert callback.message.answer.await_count == 0
        bot.send_message.assert_not_awaited()
        bot.delete_message.assert_not_awaited()
        member = bot.get_chat_member.await_args
        assert arg(member, "chat_id", 0) == TARGET_CHAT_ID
        assert arg(member, "user_id", 1) == ADMIN_ID
        assert_spinner_cleared(callback)

    async def test_a_senderless_pick_is_ignored(self):
        """``from_user is None`` → silent: no lookup, no reply, session alive."""
        bot = make_bot()
        seed_asking_source()
        callback = make_watcher_callback(f"w:src:{SOURCE_ID}", bot=bot, user_id=None)

        consumed = await dispatch_callback(callback)

        assert consumed is not None, "the click is consumed — to be gated"
        session = pending()
        assert session is not None and session.stage == "asking_source"
        assert callback.message.answer.await_count == 0
        bot.get_chat_member.assert_not_awaited(), "no user id to look up"
        bot.send_message.assert_not_awaited()
        assert_spinner_cleared(callback)


class TestCancelCallback:
    """``w:cancel`` mirrors the ``/cancel`` command (pin A3)."""

    @pytest.mark.parametrize(
        "origin,expected_stage",
        [
            pytest.param(user_origin(date=D1), "asking_source", id="asking-source"),
            pytest.param(channel_origin(message_id=501), "asking_title", id="asking-title"),
        ],
    )
    async def test_the_cancel_button_deletes_the_prompts_and_resets(
        self, origin, expected_stage
    ):
        """Both stages: the question message goes away, exactly ``Cancelled.``"""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        await dispatch(make_forward(11, origin, bot=bot))
        assert pending().stage == expected_stage, "sanity: the seeded stage"
        callback = make_watcher_callback("w:cancel", bot=bot)

        consumed = await dispatch_callback(callback)

        assert consumed is not None, "w:cancel must be registered on the watcher router"
        assert reply_text(callback.message) == CANCELLED_REPLY, (
            "the button mirrors /cancel: the same fixed literal, visible in the chat"
        )
        deletes = bot.delete_message.await_args_list
        assert len(deletes) == 1, "exactly the question message is deleted"
        assert arg(deletes[0], "chat_id", 0) == TARGET_CHAT_ID
        assert arg(deletes[0], "message_id", 1) == PROMPT_ID
        assert pending() is None, "the click resets the pending batch"
        assert bot.send_message.await_count == 1, "no new question"
        assert_spinner_cleared(callback)

    async def test_a_non_admin_cannot_cancel_with_the_button(self):
        """A member's button leaves the batch untouched (like their ``/cancel``)."""
        bot = make_bot(status="member")
        seed_asking_source()
        callback = make_watcher_callback("w:cancel", bot=bot)

        consumed = await dispatch_callback(callback)

        assert consumed is not None, "the click is consumed — to be gated"
        assert callback.message.answer.await_count == 0, "only admins may cancel"
        assert pending() is not None, "the batch must stay pending"
        bot.delete_message.assert_not_awaited()
        assert_spinner_cleared(callback)

    async def test_the_cancel_button_without_a_session_is_ignored(self):
        """No pending batch → no reply, no deletion, nothing to reset."""
        bot = make_bot()
        callback = make_watcher_callback("w:cancel", bot=bot)

        consumed = await dispatch_callback(callback)

        assert consumed is not None, "w:cancel is consumed even without a session"
        assert callback.message.answer.await_count == 0
        bot.delete_message.assert_not_awaited()
        assert pending() is None
        assert_spinner_cleared(callback)


# --- tests: the security-fix pins (fix package, RED phase) -------------------


def patch_admin_cache_ttl(monkeypatch, value) -> None:
    """Point the shared admin cache TTL constant at ``value`` (item 2).

    The cache module is the fix's own contract: ``bot.admin_cache``
    carries ONE module-level TTL constant — this helper accepts the
    documented spellings and fails with a naming message when none of
    them exists, so a wrong contract is a readable test failure.
    """
    try:
        import bot.admin_cache as admin_cache
    except ModuleNotFoundError:
        raise AssertionError(
            "bot.admin_cache must exist and expose a patchable TTL constant"
        ) from None
    for name in ("TTL", "CACHE_TTL", "ADMIN_CACHE_TTL", "TTL_SECONDS"):
        if hasattr(admin_cache, name):
            monkeypatch.setattr(admin_cache, name, value)
            return
    raise AssertionError(
        "bot.admin_cache must expose one of TTL/CACHE_TTL/ADMIN_CACHE_TTL/TTL_SECONDS"
    )


class TestAdminGateTakesTheCachedStatus:
    """H-2 + M-4: the watcher's user checks read the shared TTL cache."""

    async def test_two_forwards_from_one_admin_hit_the_api_once(self):
        """One (chat, user) lookup serves BOTH forwards of the same admin."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()

        await dispatch(make_forward(11, channel_origin(message_id=501), bot=bot))
        await dispatch(make_forward(12, channel_origin(message_id=502), bot=bot))

        assert bot.get_chat_member.await_count == 1, (
            "the second forward must be served from the shared admin cache"
        )
        session = pending()
        assert session is not None, "both forwards belong to one batch"
        assert len(session.messages) == 2, "and both of them joined it"

    async def test_the_cached_status_holds_until_the_ttl_window_closes(
        self, monkeypatch
    ):
        """Inside the window the cached answer wins; after it the API is asked."""
        patch_admin_cache_ttl(monkeypatch, 0.2)
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        await dispatch(make_forward(11, channel_origin(message_id=501), bot=bot))
        bot.get_chat_member.return_value = SimpleNamespace(status="member")

        await dispatch(make_forward(12, channel_origin(message_id=502), bot=bot))

        session = pending()
        assert session is not None, "the first forward starts the batch"
        assert len(session.messages) == 2, (
            "inside the TTL window the CACHED administrator status is served — "
            "the switched fake must not be consulted yet"
        )

        await asyncio.sleep(0.25)
        await dispatch(make_forward(13, channel_origin(message_id=503), bot=bot))

        assert bot.get_chat_member.await_count == 2, (
            "once the TTL window closed the status is looked up again"
        )
        session = pending()
        assert session is not None, "the batch itself survives a refused forward"
        assert len(session.messages) == 2, "the now non-admin forward does NOT join it"

    async def test_a_failing_admin_lookup_ignores_the_forward_without_flooding(self):
        """A raising ``get_chat_member`` = silent ignore, negatively cached."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()

        async def raise_bad_request(*args, **kwargs):
            raise TelegramBadRequest(method=None, message="user not found")

        bot.get_chat_member.side_effect = raise_bad_request

        await dispatch(make_forward(11, channel_origin(message_id=501), bot=bot))

        assert pending() is None, "a failed admin lookup must not start a batch"
        assert bot.send_message.await_count == 0, "and asks no question"
        assert bot.get_chat_member.await_count == 1, "sanity: the lookup was made once"

        await dispatch(make_forward(12, channel_origin(message_id=502), bot=bot))

        assert pending() is None, "the repeat is ignored too"
        assert bot.get_chat_member.await_count == 1, (
            "the failed lookup is negatively cached — no API flood in the TTL window"
        )
        assert bot.send_message.await_count == 0, "still no question"


class TestWatcherCallbacksWithoutAMessage:
    """L-2.3: a ``w:*`` click Telegram delivers without a message is ignored."""

    async def test_a_source_pick_without_a_message_leaves_the_batch_alone(self):
        """No message to act on: spinner dies, the batch stays exactly as it was."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        session = seed_asking_source()
        click = SimpleNamespace(
            data="w:src:-100111",
            from_user=SimpleNamespace(id=ADMIN_ID),
            bot=bot,
            message=None,
            answer=AsyncMock(name="answer"),
        )

        consumed = await dispatch_callback(click)

        assert consumed is not None, "the click still matches the callback router"
        assert_spinner_cleared(click)
        assert pending() is session, "the batch stays where it was"
        assert session.stage == "asking_source", "and still waits for the source"
        assert bot.send_message.await_count == 0, "no question is asked for it"
        assert bot.delete_message.await_count == 0, "and no prompt is deleted"

    async def test_a_cancel_without_a_message_keeps_the_batch(self):
        """No message to act on: the batch is NOT reset by a messageless click."""
        bot = make_bot()
        bot.send_message.side_effect = question_sender()
        session = seed_asking_source()
        click = SimpleNamespace(
            data="w:cancel",
            from_user=SimpleNamespace(id=ADMIN_ID),
            bot=bot,
            message=None,
            answer=AsyncMock(name="answer"),
        )

        consumed = await dispatch_callback(click)

        assert consumed is not None, "the click still matches the callback router"
        assert_spinner_cleared(click)
        assert pending() is session, "the batch must survive a messageless cancel"
        assert bot.delete_message.await_count == 0, "no prompt deletion either"

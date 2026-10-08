"""Settings-menu router tests: module ``bot.handlers.settings`` (cycle B).

RED phase (cycle B): ``bot.handlers.settings`` does not exist yet — every
test below fails INSIDE its body (``ModuleNotFoundError`` /
``AttributeError``): the ``bot.*`` imports are lazy, exactly like the
helpers of the other test modules, so collection stays clean for the
whole suite.

Specification (brief v3, the settings menu layer):

- ``bot.config.Settings`` carries ``owner_id`` (env ``OWNER_ID``) —
  pinned by ``tests/test_config.py``; the ACL below reads the
  MODULE-LEVEL ``bot.config.settings`` instance at call time.
- ACL, ``can_manage(user_id, bot) -> bool`` (pinned here): allowed when
  (a) ``settings.owner_id`` is set and EQUALS ``user_id``, or (b) the
  user is ``creator``/``administrator`` of at least one chat of ANY
  pair — ``get_chat_administrators`` is asked for the source AND the
  target of every pair; a chat whose call RAISES gives that pair no
  rights (fail-safe: the exception never escapes, other pairs still
  count). No pairs and no owner -> nobody (bootstrap: the very first
  pair requires ``OWNER_ID=<id>`` in ``.env``). Refusal ->
  ``settings.forbidden`` and NOTHING else happens.
- ``/settings`` in a private chat: refusal -> ``message.answer`` of
  ``settings.forbidden``; otherwise the menu text is
  ``settings.menu_title`` (n = pair count) plus either one
  ``"{source_title} -> {target_title}"`` line per pair (joined with a
  newline) or ``settings.no_pairs``. Titles come from
  ``await bot.get_chat(ref)``: ``.title``, else ``.username`` rendered
  as ``@name``, else the ref itself (a raising ``get_chat`` reads as
  the ref too). The inline keyboard always carries
  ``settings.add_button`` (``callback_data="st:add"``) and — when pairs
  exist — also ``settings.delete_button`` (``st:del"``).
- Callbacks (menus are re-rendered through
  ``callback.message.edit_text`` (aiogram ``CallbackQuery`` has no edit
  method of its own), texts are delivered through
  ``callback.answer(...)`` where the spec names one):

  * ``st:add`` -> the DM chat state becomes wait_source, the menu is
    edited to ``settings.send_source`` with NO keyboard;
  * ``st:del`` -> no pairs: ``callback.answer(settings.no_pairs)`` and
    NO edit; pairs: the menu is edited to ``settings.delete_prompt`` +
    one ``"{src} -> {tgt}"`` button per pair
    (``callback_data="st:del:{pair_id}"``) + ``settings.back_button``
    (``st:back``);
  * ``st:del:{id}`` -> ``chats.remove_pair(id)`` -> edit into the
    FRESH menu + ``callback.answer(settings.pair_removed)``; the pair
    is already gone (race) -> edit into the fresh menu +
    ``callback.answer(settings.no_pairs)``;
  * ``st:back`` -> edit into the fresh menu;
  * ``st:yes`` / ``st:no`` -> see the add flow below.
- Add flow (module-level ``states`` dict of ``bot/handlers/settings.py``,
  keyed by the DM chat id; this file's fixture clears it): ``None ->
  wait_source -> wait_target -> confirm`` (source + target).
  Incoming DM messages while waiting: a FORWARD goes through
  ``chats.origin_chat_ref(origin)`` (no chat visible, e.g. a user
  origin -> ``settings.invalid_input``, state unchanged), plain TEXT
  through ``chats.parse_chat_ref`` (``None`` -> ``settings.invalid_input``,
  state unchanged); a ref in wait_source -> wait_target +
  ``settings.send_target``. In wait_target the checks run IN THIS
  ORDER:

  1. self-pair via ``chats.is_same_chat`` -> ``settings.self_pair``,
     the state stays wait_target;
  2. bot rights: ``get_chat_administrators`` of BOTH chats —
     ``message.bot.id`` must be in there with status
     ``administrator``/``creator`` AND ``can_delete_messages=True``
     -> otherwise ``settings.bot_not_admin``, the state stays
     wait_target;
  3. all good -> state confirm, ``settings.confirm_prompt`` (titles
     via the get_chat fallback above) + a keyboard with
     ``settings.confirm_button`` (``st:yes``) and
     ``settings.cancel_button`` (``st:no``).

  ``st:yes`` in confirm -> ``chats.add_pair``: ``True`` -> state
  cleared, the confirmation message edited into the FRESH menu,
  ``callback.answer(settings.pair_added)``; ``False`` (duplicate) ->
  ``callback.answer(settings.duplicate_pair)``, the state STAYS
  confirm. ``st:no`` in confirm -> state cleared, the message edited
  to ``watcher.cancelled`` with NO keyboard. ``/cancel`` while any
  state is active -> state cleared, ``message.answer(watcher.cancelled)``.
  Messages outside a state that are not ``/settings`` are not
  intercepted at all.

Pinned helper contracts cycle B must provide: ``bot.chats.is_same_chat``,
``bot.chats.origin_chat_ref`` and ``bot.handlers.settings.can_manage``.

Inline chat pickers (the requested inline buttons that choose a chat
by its TITLE): BOTH waiting prompts — the ``settings.send_source`` edit
of ``st:add`` and the ``settings.send_target`` answer — arrive with an
inline keyboard: one button per chat known to the registry (the UNION
of the sources and the targets of every pair, duplicates by
``str(ref)`` dropped at their FIRST occurrence, registry order), the
label resolved through ``bot.get_chat`` (``.title`` → ``.username``
rendered as ``@name`` → the ref itself, a RAISING ``get_chat``
reading as the ref too), ``callback_data = "st:pick:" + str(ref)``, and
a trailing ``Cancel`` button (``settings.cancel_button``, the literal
``Cancel``) with ``callback_data="st:cancel"``. An EMPTY registry keeps
both prompts keyboardless (the status quo — that test is GREEN in RED).
New callbacks on this router (dispatched by ``dispatch_callback``):

- ``st:pick:<ref>`` — the same ACL gate as ``st:add`` (a refusal
  alerts ``settings.forbidden`` and changes NOTHING), then exactly the
  typed-answer path of the current step: a payload that parses to no
  chat → ``settings.invalid_input``, the step stays; ``wait_source``
  → ``wait_target`` + ``settings.send_target``; ``wait_target`` → the
  same checks in the SAME order (self pair → bot rights → the
  confirmation with its Confirm/Cancel buttons). At ``confirm`` — and
  outside any state — the click is a silent no-op (nothing added,
  nothing moved);
- ``st:cancel`` — the ``/cancel`` mirror: any active step is dropped
  and ``watcher.cancelled`` is sent through
  ``callback.message.answer``; without a state it no-ops;
- every consumed ``st:pick``/``st:cancel`` branch clears the spinner
  with an EMPTY ``callback.answer()`` (the ACL refusal alerts instead).

Pinned by ``TestPickKeyboard``, ``TestPickCallback`` and
``TestSettingsCancelCallback``.

Security-review follow-ups (behavioural pins, same router):

- N1b — a REFUSED ``st:yes`` keeps the ``confirm`` state open, so the
  rechecks of that click are answered from a SHORT-lived result cache
  in ``bot.admin_cache`` (verdict of the pair «caller admins both
  chats» + «bot may move», key ``(caller_id, str(source),
  str(target))``, window ``bot.admin_cache.FRESH_TTL`` = 3.0 s,
  dropped by ``admin_cache.clear()``): repeating the same click inside
  the window adds ZERO ``get_chat_administrators`` calls, another
  caller never inherits the verdict, and the wait_target check
  (``_bot_may_move``) stays fresh — pinned by
  ``TestConfirmRechecksAreCachedForAShortWindow``, together with the
  existing ``TestConfirmAuthorization`` (whose first click is ALWAYS
  fresh);
- N9 — the success alert of ``st:yes`` carries two titles of up to 128
  chars each (272 chars rendered) while Telegram allows 200: the alert
  must be cut down to fit (keeping its pinned ``Pair added`` prefix)
  and a ``TelegramBadRequest`` out of the FINAL ``callback.answer``
  must never escape the handler — pinned by
  ``TestThePairAddedAlertFitsTheSpinner``.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from sqlalchemy import select

# --- chats of the fixtures -------------------------------------------------

#: The private chat the menu runs in — the ``states`` dict is keyed by it.
DM_CHAT_ID = 777

#: The DM user (the OWNER_ID candidate of the owner-driven tests).
USER_ID = 42

#: A user who owns nothing and admins nothing.
STRANGER_ID = 99

#: The bot itself: an admin WITH delete rights in both chats of a pair.
BOT_ID = 555

#: An owner id distinct from USER_ID — the owner branch matches EXACTLY.
OWNER_ID = 31337

#: The configured pair: source by numeric id, target by @username.
SOURCE_ID = -100111
TARGET_REF = "@forumgroup"

#: A second pair — pair counts, menu lines and multi-pair ACL scans.
SECOND_SOURCE_ID = -100333
SECOND_TARGET_REF = "@othergroup"

#: The registry seeding of most tests (the DB is the source of truth).
PAIRS = [{"source": SOURCE_ID, "target": TARGET_REF}]
SECOND_PAIR = {"source": SECOND_SOURCE_ID, "target": SECOND_TARGET_REF}

#: Timestamp of the fake messages (settings never reads it).
D1 = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)

# --- reply literals (msgids pinned into bot/locales/messages.pot by test_i18n) ---

FORBIDDEN = "You are not allowed to manage settings."
NO_PAIRS = "No pairs configured yet."
SEND_SOURCE = "Send a message forwarded from the source chat, or its @username or id."
SEND_TARGET = (
    "Now do the same for the target chat: forward a message or send @username or id."
)
INVALID_INPUT = "Not a chat reference. Send a forwarded message, @username or numeric id."
SELF_PAIR = "Source and target are the same chat."
BOT_NOT_ADMIN = (
    "I must be an admin with the Delete Messages permission in both chats of a pair."
)
DUPLICATE_PAIR = "This pair already exists."
PAIR_REMOVED = "Pair removed."
DELETE_PROMPT = "Select a pair to remove:"
CANCELLED = "Cancelled."
ADD_BUTTON = "Add pair"
DELETE_BUTTON = "Delete pair"
BACK_BUTTON = "Back"
CANCEL_BUTTON = "Cancel"
CONFIRM_BUTTON = "Confirm"

# --- expected composites ---------------------------------------------------


def menu_text(pair_count: int, *lines: str) -> str:
    """The exact menu text: the pinned title line plus the pair lines."""
    return "\n".join([f"Pair settings ({pair_count}):", *lines])


#: ``get_chat`` titles of the four fixture refs (menu lines and prompts).
TITLE_CHATS = {
    str(SOURCE_ID): SimpleNamespace(title="Alpha", username="srcgroup"),
    TARGET_REF: SimpleNamespace(title="Beta", username="forumgroup"),
    str(SECOND_SOURCE_ID): SimpleNamespace(title="Gamma", username=None),
    SECOND_TARGET_REF: SimpleNamespace(title="Delta", username="othergroup"),
}


# --- helpers: modules ------------------------------------------------------


def settings_mod():
    """The ``bot.handlers.settings`` module, imported at test time.

    The import lives inside this function on purpose: at top level a
    missing module would abort the collection of the whole pytest run
    (RED phase: the module does not exist yet).
    """
    import bot.handlers.settings as settings_module

    return settings_module


def chats_mod():
    """The ``bot.chats`` module, imported at test time."""
    import bot.chats as chats_module

    return chats_module


def config_mod():
    """The ``bot.config`` module, imported at test time."""
    from bot import config as config_module

    return config_module


def admin_cache_mod():
    """The ``bot.admin_cache`` module, imported at test time.

    The N1b pins patch its ``FRESH_TTL`` constant and call its
    ``clear()`` — both are part of the contract of the short-lived
    result cache of the ``st:yes`` rechecks.
    """
    from bot import admin_cache

    return admin_cache


# --- helpers: database -----------------------------------------------------


async def set_pairs(pairs) -> None:
    """Replace the pair registry with ``pairs`` (cycle A: DB-backed)."""
    import bot.chats as chats_module
    from bot.database import session
    from bot.models import Pair

    async with session() as db:
        for row in (await db.execute(select(Pair))).scalars():
            await db.delete(row)
    for pair in pairs:
        added = await chats_module.chats.add_pair(pair["source"], pair["target"])
        assert added is True, f"seeding the pair {pair!r} must be accepted, got {added!r}"


async def stored_pairs() -> list:
    """Every ``Pair`` row currently in the database (insertion order)."""
    from bot.database import session
    from bot.models import Pair

    async with session() as db:
        return list((await db.execute(select(Pair).order_by(Pair.id))).scalars())


async def stored_count() -> int:
    """How many pairs the table holds right now."""
    return len(await stored_pairs())


# --- helpers: fake bot -----------------------------------------------------


def member(user_id, status="administrator", can_delete_messages=True):
    """A ``get_chat_administrators`` entry: ``ChatMember``-shaped object."""
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        status=status,
        can_delete_messages=can_delete_messages,
    )


def bot_admins(*refs):
    """A ``get_chat_administrators`` map granting THE BOT rights in ``refs``."""
    return {str(ref): [member(BOT_ID, "administrator", True)] for ref in refs}


def make_bot(*, admins=None, admin_errors=(), chats=None):
    """An AsyncMock bot with the two ``get*`` calls the menu drives.

    Args:
        admins: ``{str(chat ref): [member, ...]}`` returned by
            ``get_chat_administrators``; a ref that is absent reads as
            an EMPTY admin list (the chat exists, nobody rights).
        admin_errors: refs whose ``get_chat_administrators`` RAISES —
            the unavailable-chat case the ACL must fail safe on. The
            mutable ``bot.admins`` / ``bot.admin_errors`` attributes let
            a test fix the rights in the middle of a flow.
        chats: ``{str(chat ref): chat object | Exception}`` returned by
            ``get_chat``; an unknown ref (and an ``Exception`` value)
            raises, which is exactly the fallback-to-ref case.

    Both side effects read their arguments positionally OR as
    ``chat_id=``, so the tests never pin HOW the handler calls Telegram.
    """
    bot = AsyncMock(name="bot")
    bot.id = BOT_ID
    bot.admins = dict(admins or {})
    bot.admin_errors = {str(ref) for ref in admin_errors}
    bot.chats = dict(chats or {})

    def _ref(args, kwargs):
        value = args[0] if args else kwargs.get("chat_id")
        return str(value)

    async def get_chat_administrators(*args, **kwargs):
        key = _ref(args, kwargs)
        if key in bot.admin_errors:
            raise TelegramBadRequest(method=None, message="chat unavailable")
        return bot.admins.get(key, [])

    async def get_chat(*args, **kwargs):
        key = _ref(args, kwargs)
        value = bot.chats.get(key)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise TelegramBadRequest(method=None, message="chat not found")
        return value

    bot.get_chat_administrators.side_effect = get_chat_administrators
    bot.get_chat.side_effect = get_chat
    return bot


# --- helpers: messages and callbacks ---------------------------------------


def dm_message(text=None, *, bot=None, user_id=USER_ID, forward_origin=None, message_id=1):
    """A private-chat (DM) ``Message``-shaped object as the menu sees it."""
    return SimpleNamespace(
        message_id=message_id,
        chat=SimpleNamespace(id=DM_CHAT_ID, type="private", username=None),
        from_user=SimpleNamespace(id=user_id),
        forward_origin=forward_origin,
        text=text,
        caption=None,
        date=D1,
        reply_to_message=None,
        bot=bot if bot is not None else make_bot(),
        answer=AsyncMock(name="answer"),
    )


def channel_origin(chat_id=SOURCE_ID, username=None, message_id=501):
    """A ``MessageOriginChannel``-shaped forward origin (a chat IS visible)."""
    return SimpleNamespace(
        type="channel",
        date=D1,
        chat=SimpleNamespace(id=chat_id, username=username, type="channel"),
        message_id=message_id,
        sender_chat=None,
        sender_user=None,
    )


def group_origin(chat_id=None, username="srcgroup"):
    """A ``MessageOriginChat``-shaped origin: the chat lives in ``sender_chat``."""
    return SimpleNamespace(
        type="chat",
        date=D1,
        chat=None,
        sender_chat=SimpleNamespace(id=chat_id, username=username, type="supergroup"),
        sender_user=None,
        message_id=None,
    )


def user_origin(user_id=STRANGER_ID):
    """A ``MessageOriginUser``-shaped origin: no chat visible -> invalid input."""
    return SimpleNamespace(
        type="user",
        date=D1,
        chat=None,
        sender_chat=None,
        sender_user=SimpleNamespace(id=user_id),
        message_id=None,
    )


def make_callback(data, *, bot=None, user_id=USER_ID, chat_id=DM_CHAT_ID, text=""):
    """A ``CallbackQuery``-shaped object (the menu message it may edit)."""
    bot = bot if bot is not None else make_bot()
    message = SimpleNamespace(
        message_id=10,
        chat=SimpleNamespace(id=chat_id, type="private", username=None),
        from_user=SimpleNamespace(id=user_id),
        text=text,
        bot=bot,
        answer=AsyncMock(name="answer"),
        edit_text=AsyncMock(name="edit_text"),
    )
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id),
        bot=bot,
        message=message,
        answer=AsyncMock(name="answer"),
    )


# --- helpers: dispatch and call inspection ---------------------------------


async def dispatch(message):
    """Run ``message`` through the settings router (first passing handler).

    Mirrors aiogram's semantics: handlers are checked in registration
    order and the first whose filters pass consumes the event —
    ``None`` means nothing intercepted the message.
    """
    for handler in settings_mod().router.message.handlers:
        passed, _ = await handler.check(message)
        if passed:
            await handler.callback(message)
            return handler
    return None


async def dispatch_callback(callback):
    """Run ``callback`` through the settings router's callback handlers."""
    for handler in settings_mod().router.callback_query.handlers:
        passed, _ = await handler.check(callback)
        if passed:
            await handler.callback(callback)
            return handler
    return None


def _first_text(call):
    """The text argument of the first ``call`` (positional or ``text=``)."""
    if call.args:
        return call.args[0]
    return call.kwargs.get("text", "")


def reply_text(message) -> str:
    """Text of the first ``message.answer`` reply."""
    assert message.answer.await_count, "the handler must reply through message.answer"
    return _first_text(message.answer.await_args)


def reply_markup(message):
    """The ``reply_markup`` kwarg of the first ``message.answer`` reply."""
    return message.answer.await_args.kwargs.get("reply_markup")


def answer_text(callback) -> str:
    """Text of the first ``callback.answer`` alert."""
    assert callback.answer.await_count, "the callback must be answered through callback.answer"
    return _first_text(callback.answer.await_args)


def edited_text(callback) -> str:
    """Text of the first ``callback.message.edit_text`` edit."""
    assert callback.message.edit_text.await_count, (
        "the callback must edit the menu through callback.message.edit_text"
    )
    return _first_text(callback.message.edit_text.await_args)


def edited_markup(callback):
    """The ``reply_markup`` kwarg of the first edit call."""
    return callback.message.edit_text.await_args.kwargs.get("reply_markup")


def keyboard_pairs(markup) -> set:
    """``{(button text, callback data), ...}`` of an inline keyboard."""
    if markup is None:
        return set()
    rows = markup["inline_keyboard"] if isinstance(markup, dict) else markup.inline_keyboard
    pairs = set()
    for row in rows:
        for button in row:
            if isinstance(button, dict):
                pairs.add((button.get("text"), button.get("callback_data")))
            else:
                pairs.add((button.text, button.callback_data))
    return pairs


def keyboard_buttons(markup):
    """``(text, callback_data)`` of every button of ``markup``, row by row.

    Row-major order is what the pickers pin (registry order, ``Cancel``
    last) — ``keyboard_pairs`` above only pins membership.
    """
    assert markup is not None, "the inline keyboard must be attached to the prompt"
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
    """Every consumed ``st:pick``/``st:cancel`` branch ends in an EMPTY answer.

    The spinner of the pressed button has to stop even when the branch
    sends no visible text (the no-op cases); an ACL refusal alerts
    ``settings.forbidden`` instead and is pinned through
    ``answer_text``.
    """
    assert callback.answer.await_count, "the click must clear the loading spinner"
    call = callback.answer.await_args
    assert not call.args and call.kwargs.get("text") is None, (
        f"callback.answer() must be empty — no alert text: {call!r}"
    )


async def start_add(bot=None, user_id=USER_ID):
    """Press ``st:add`` — the chat state becomes wait_source."""
    callback = make_callback("st:add", bot=bot, user_id=user_id)
    consumed = await dispatch_callback(callback)
    assert consumed is not None, "st:add must be consumed by the settings router"
    return callback


# --- fixtures --------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path, monkeypatch):
    """Every test runs in an empty cwd (no stray .env / config files)."""
    monkeypatch.chdir(tmp_path)


def _set_owner(monkeypatch, value) -> None:
    """Point ``bot.config.settings.owner_id`` at ``value`` (RED-tolerant).

    While ``Settings`` has no ``owner_id`` field yet the assignment
    raises ``ValueError`` — the tests fail in their own bodies then,
    and this helper must not turn that into a fixture error.
    """
    try:
        monkeypatch.setattr(config_mod().settings, "owner_id", value, raising=False)
    except ValueError:  # RED: the field does not exist yet
        pass


@pytest.fixture(autouse=True)
def owner_unset(monkeypatch):
    """No OWNER_ID by default: only branch (b) of the ACL can pass."""
    _set_owner(monkeypatch, None)


@pytest.fixture
def owner(monkeypatch):
    """The DM user IS the configured owner — branch (a) of the ACL."""
    _set_owner(monkeypatch, USER_ID)


def _clear_states() -> None:
    """Empty the module-level ``states`` dict (RED: no module, no-op)."""
    try:
        import bot.handlers.settings as settings_module
    except ModuleNotFoundError:
        return
    states = getattr(settings_module, "states", None)
    if states is not None:
        states.clear()


@pytest.fixture(autouse=True)
def clean_states():
    """The per-chat menu state starts (and ends) empty for every test."""
    _clear_states()
    yield
    _clear_states()


# --- tests: ACL -------------------------------------------------------------


class TestAccessControl:
    """``can_manage(user_id, bot)``: owner OR admin of any chat of any pair."""

    async def test_the_exact_owner_id_is_allowed_and_anybody_else_is_not(self, monkeypatch):
        """Branch (a): an EQUAL ``owner_id`` grants, a different id does not."""
        _set_owner(monkeypatch, OWNER_ID)
        bot = make_bot()

        assert await settings_mod().can_manage(OWNER_ID, bot) is True, (
            "the configured owner must be allowed"
        )
        assert await settings_mod().can_manage(STRANGER_ID, bot) is False, (
            "only the EXACT owner id may take branch (a)"
        )

    async def test_an_admin_of_a_pair_source_is_allowed(self):
        """Branch (b): creator/administrator of the SOURCE chat of a pair."""
        await set_pairs(PAIRS)
        bot = make_bot(admins={str(SOURCE_ID): [member(USER_ID, "administrator")]})

        assert await settings_mod().can_manage(USER_ID, bot) is True, (
            "an administrator of a pair's source must be allowed"
        )

    async def test_an_admin_of_a_pair_target_is_allowed(self):
        """Branch (b): CREATOR of the TARGET chat of a pair."""
        await set_pairs(PAIRS)
        bot = make_bot(admins={TARGET_REF: [member(USER_ID, "creator")]})

        assert await settings_mod().can_manage(USER_ID, bot) is True, (
            "a creator of a pair's target must be allowed"
        )

    async def test_a_user_who_admins_nothing_is_refused(self):
        """Neither branch holds: the answer is a refusal, not a crash."""
        await set_pairs(PAIRS)
        bot = make_bot(admins={str(SOURCE_ID): [member(USER_ID, "administrator")]})

        assert await settings_mod().can_manage(STRANGER_ID, bot) is False, (
            "rights of ANOTHER user must never leak to a stranger"
        )

    async def test_an_unavailable_chat_gives_no_rights(self):
        """Fail-safe: a raising ``get_chat_administrators`` never grants.

        The admins entry would grant if the call succeeded — the raise
        must win: an unavailable chat gives the pair no rights and the
        exception must not escape.
        """
        await set_pairs(PAIRS)
        bot = make_bot(
            admins={str(SOURCE_ID): [member(USER_ID, "administrator")]},
            admin_errors={str(SOURCE_ID), TARGET_REF},
        )

        assert await settings_mod().can_manage(USER_ID, bot) is False, (
            "a chat whose get_chat_administrators raises must give no rights"
        )

    async def test_a_broken_pair_does_not_block_another_pair(self):
        """Fail-safe per PAIR: one unavailable pair, another that still grants."""
        await set_pairs([*PAIRS, SECOND_PAIR])
        bot = make_bot(
            admin_errors={str(SOURCE_ID), TARGET_REF},
            admins={str(SECOND_SOURCE_ID): [member(USER_ID, "administrator")]},
        )

        assert await settings_mod().can_manage(USER_ID, bot) is True, (
            "an unavailable pair must give no rights without disabling the others"
        )

    async def test_without_pairs_and_without_owner_nobody_is_allowed(self):
        """Bootstrap: the first pair can only come from OWNER_ID in .env."""
        bot = make_bot()

        assert await settings_mod().can_manage(USER_ID, bot) is False, (
            "with no pairs and no owner the settings must refuse everybody"
        )


# --- tests: registry helpers pinned by the flow -----------------------------


class TestOriginChatRef:
    """``chats.origin_chat_ref(origin)`` — the ref a forward origin carries."""

    def test_the_extractor_reads_the_chat_and_refuses_user_origins(self):
        """Channel/group origins resolve to a ref, a user origin to ``None``."""
        chats = chats_mod()

        origin = channel_origin(chat_id=SOURCE_ID, username=None)
        assert chats.origin_chat_ref(origin) == SOURCE_ID, (
            "a channel origin must resolve to its chat id"
        )
        assert chats.origin_chat_ref(group_origin(username="srcgroup")) == "@srcgroup", (
            "a group origin must resolve to its @username"
        )
        assert chats.origin_chat_ref(user_origin()) is None, (
            "a user origin carries no chat — the flow must read it as invalid input"
        )


class TestSameChatHelper:
    """``chats.is_same_chat(a, b)`` — the form-independent self-pair check."""

    def test_is_same_chat_compares_every_recorded_form(self):
        """``@MyChat == @mychat`` and ``-100666 == "-100666"``, nothing else."""
        chats = chats_mod()

        assert chats.is_same_chat("@Loop", "@loop") is True, "usernames compare case-insensitively"
        assert chats.is_same_chat(-100666, "-100666") is True, "ids compare across str/int"
        assert chats.is_same_chat("@src", "@tgt") is False, "different chats never match"


# --- tests: the /settings command -------------------------------------------


class TestSettingsCommand:
    """The ``/settings`` menu: ACL gate, text, titles and buttons."""

    async def test_a_stranger_gets_the_forbidden_refusal_and_no_menu(self):
        """Refusal -> exactly one ``settings.forbidden`` answer, nothing else."""
        await set_pairs(PAIRS)
        bot = make_bot(admins={str(SOURCE_ID): [member(BOT_ID, "administrator")]})
        message = dm_message("/settings", bot=bot, user_id=STRANGER_ID)

        consumed = await dispatch(message)

        assert consumed is not None, "the /settings command must be consumed by the settings router"
        assert message.answer.await_count == 1, "a refusal must be the ONLY reply"
        assert reply_text(message) == FORBIDDEN

    async def test_the_owner_of_an_empty_registry_sees_the_empty_menu(self, owner):
        """Menu = title (n = 0) + ``settings.no_pairs`` + the Add button only."""
        message = dm_message("/settings")

        consumed = await dispatch(message)

        assert consumed is not None, "the owner's /settings must open the menu"
        assert reply_text(message) == menu_text(0, NO_PAIRS)
        assert keyboard_pairs(reply_markup(message)) == {(ADD_BUTTON, "st:add")}, (
            "with no pairs the keyboard carries the Add button alone"
        )

    async def test_a_pair_admin_sees_both_pair_lines_and_both_buttons(self):
        """Titles via ``get_chat``, lines joined with a newline, Add + Delete."""
        await set_pairs([*PAIRS, SECOND_PAIR])
        bot = make_bot(
            admins={str(SECOND_SOURCE_ID): [member(USER_ID, "administrator")]},
            chats=TITLE_CHATS,
        )
        message = dm_message("/settings", bot=bot)

        consumed = await dispatch(message)

        assert consumed is not None, "an admin of a pair chat must open the menu"
        assert reply_text(message) == menu_text(2, "Alpha → Beta", "Gamma → Delta")
        assert keyboard_pairs(reply_markup(message)) == {
            (ADD_BUTTON, "st:add"),
            (DELETE_BUTTON, "st:del"),
        }, "with pairs the keyboard carries Add and Delete"

    @pytest.mark.parametrize(
        "chat,expected_title",
        [
            pytest.param(
                SimpleNamespace(title="Alpha", username="srcgroup"),
                "Alpha",
                id="chat-title",
            ),
            pytest.param(
                SimpleNamespace(title=None, username="srcgroup"),
                "@srcgroup",
                id="username-with-at",
            ),
            pytest.param(
                SimpleNamespace(title=None, username=None),
                str(SOURCE_ID),
                id="bare-ref-fallback",
            ),
            pytest.param(
                TelegramBadRequest(method=None, message="chat unavailable"),
                str(SOURCE_ID),
                id="get-chat-error-falls-back-to-the-ref",
            ),
        ],
    )
    async def test_the_source_title_falls_back_down_to_the_ref(
        self, owner, chat, expected_title
    ):
        """`.title` -> `@username` -> the ref -> the ref on a raising get_chat."""
        await set_pairs(PAIRS)
        bot = make_bot(chats={str(SOURCE_ID): chat, TARGET_REF: SimpleNamespace(title="Beta")})
        message = dm_message("/settings", bot=bot)

        await dispatch(message)

        assert reply_text(message) == menu_text(1, f"{expected_title} → Beta"), (
            "the menu line must fall back step by step down to the raw chat ref"
        )


# --- tests: the add flow ------------------------------------------------------


class TestAddFlow:
    """``st:add`` -> wait_source -> wait_target -> confirm -> ``st:yes``."""

    async def test_add_button_opens_the_source_step_without_a_keyboard(self, owner):
        """``st:add`` edits the menu into ``settings.send_source``, no keyboard."""
        callback = await start_add()

        assert edited_text(callback) == SEND_SOURCE
        assert edited_markup(callback) is None, "the send_source edit must carry no keyboard"

    async def test_a_stranger_cannot_press_add(self):
        """ACL on callbacks: refusal -> ``settings.forbidden``, no state, no edit."""
        await set_pairs(PAIRS)
        bot = make_bot(admins={str(SOURCE_ID): [member(BOT_ID, "administrator")]})
        callback = make_callback("st:add", bot=bot, user_id=STRANGER_ID)

        consumed = await dispatch_callback(callback)

        assert consumed is not None, "st:add must still be consumed — to refuse it"
        assert answer_text(callback) == FORBIDDEN
        assert callback.message.edit_text.await_count == 0, "a refusal must not open the flow"
        late = dm_message("@late", bot=bot, user_id=STRANGER_ID)
        assert await dispatch(late) is None, "a refused st:add must leave NO state behind"

    async def test_a_text_source_advances_to_the_target_step(self, owner):
        """A strict ``parse_chat_ref`` hit moves wait_source -> wait_target."""
        await start_add()
        message = dm_message("-100111")

        consumed = await dispatch(message)

        assert consumed is not None, "the waiting chat must consume the answer"
        assert reply_text(message) == SEND_TARGET

    async def test_not_a_chat_ref_is_refused_and_the_step_stays(self, owner):
        """``settings.invalid_input`` and the state does NOT move."""
        await start_add()
        bad = dm_message("just a chat name")
        await dispatch(bad)
        assert reply_text(bad) == INVALID_INPUT

        good = dm_message("-100111")
        consumed = await dispatch(good)

        assert consumed is not None, "the invalid answer must not clear the wait_source step"
        assert reply_text(good) == SEND_TARGET

    async def test_a_forwarded_source_advances_and_keeps_its_chat_ref(self, owner):
        """A forward resolves through ``origin_chat_ref`` — the ref survives."""
        bot = make_bot(admins=bot_admins(SOURCE_ID, TARGET_REF), chats=TITLE_CHATS)
        await start_add(bot=bot)
        forwarded = dm_message(None, bot=bot, forward_origin=channel_origin(SOURCE_ID, None))
        await dispatch(forwarded)
        assert reply_text(forwarded) == SEND_TARGET

        target = dm_message("@forumgroup", bot=bot)
        await dispatch(target)

        assert reply_text(target) == "Add pair: Alpha → Beta?", (
            "the forwarded source ref must be the origin's chat id (-100111)"
        )

    async def test_a_forward_from_a_user_is_not_a_chat_ref(self, owner):
        """A user origin has no chat -> ``settings.invalid_input``, state unchanged."""
        await start_add()
        bad = dm_message(None, forward_origin=user_origin())
        await dispatch(bad)
        assert reply_text(bad) == INVALID_INPUT

        good = dm_message("-100111")
        consumed = await dispatch(good)

        assert consumed is not None, "the bad forward must not clear the wait_source step"
        assert reply_text(good) == SEND_TARGET

    async def test_a_self_pair_is_refused_in_any_form_and_the_step_stays(self, owner):
        """``is_same_chat`` comparison: ``@Loop`` vs ``@loop`` is still self."""
        await start_add()
        source = dm_message("@Loop")
        await dispatch(source)
        assert reply_text(source) == SEND_TARGET

        same = dm_message("@loop")
        await dispatch(same)
        assert reply_text(same) == SELF_PAIR

        other = dm_message("@forumgroup")
        consumed = await dispatch(other)

        assert consumed is not None, "the self-pair refusal must keep the wait_target step"
        assert reply_text(other) == BOT_NOT_ADMIN, (
            "a DIFFERENT target proceeds to the bot-rights check (state is intact)"
        )

    async def test_the_self_pair_check_runs_before_the_bot_rights_check(self, owner):
        """Check order (1) then (2): a self-pair answers ``self_pair`` even
        when the bot has no rights in either chat."""
        await start_add()
        await dispatch(dm_message("@loop"))
        same = dm_message("@LOOP")

        await dispatch(same)

        assert reply_text(same) == SELF_PAIR, (
            "the self-pair check must run BEFORE the bot-admin check"
        )

    @pytest.mark.parametrize(
        "admins",
        [
            pytest.param({}, id="bot-is-no-admin-anywhere"),
            pytest.param(
                {
                    str(SOURCE_ID): [member(BOT_ID, "administrator", False)],
                    TARGET_REF: [member(BOT_ID, "administrator", True)],
                },
                id="bot-lacks-can_delete-messages",
            ),
        ],
    )
    async def test_without_delete_rights_in_both_chats_the_pair_is_refused(
        self, owner, admins
    ):
        """Check (2): no admin-with-delete-rights -> ``bot_not_admin``, state stays."""
        await start_add()
        bot = make_bot(admins=admins, chats=TITLE_CHATS)
        await dispatch(dm_message("-100111", bot=bot))
        bad = dm_message("@forumgroup", bot=bot)
        await dispatch(bad)
        assert reply_text(bad) == BOT_NOT_ADMIN

        for ref in (str(SOURCE_ID), TARGET_REF):  # rights fixed, SAME target again
            bot.admins[ref] = [member(BOT_ID, "administrator", True)]
        again = dm_message("@forumgroup", bot=bot)
        consumed = await dispatch(again)

        assert consumed is not None, "the refusal must keep the wait_target step"
        assert reply_text(again) == "Add pair: Alpha → Beta?"

    async def test_a_valid_pair_reaches_the_confirmation(self, owner):
        """Check (3): the prompt embeds both titles plus Confirm/Cancel."""
        bot = make_bot(admins=bot_admins(SOURCE_ID, TARGET_REF), chats=TITLE_CHATS)
        await start_add(bot=bot)
        await dispatch(dm_message("-100111", bot=bot))
        message = dm_message("@forumgroup", bot=bot)

        consumed = await dispatch(message)

        assert consumed is not None, "the target step must consume the answer"
        assert reply_text(message) == "Add pair: Alpha → Beta?"
        assert keyboard_pairs(reply_markup(message)) == {
            (CONFIRM_BUTTON, "st:yes"),
            (CANCEL_BUTTON, "st:no"),
        }, "the confirmation carries exactly Confirm and Cancel"

    async def test_confirm_yes_adds_the_pair_and_renders_the_fresh_menu(self, owner):
        """``st:yes`` -> ``True``: pair in the registry + fresh menu + alert."""
        bot = make_bot(admins=bot_admins(SOURCE_ID, TARGET_REF), chats=TITLE_CHATS)
        await start_add(bot=bot)
        await dispatch(dm_message("-100111", bot=bot))
        await dispatch(dm_message("@forumgroup", bot=bot))
        callback = make_callback("st:yes", bot=bot)

        consumed = await dispatch_callback(callback)

        assert consumed is not None, "st:yes must be consumed by the settings router"
        assert await stored_count() == 1, "the confirmed pair must reach the table"
        assert chats_mod().chats.target_for(SOURCE_ID) == TARGET_REF, (
            "add_pair must refresh the registry — the pair is readable right away"
        )
        assert edited_text(callback) == menu_text(1, "Alpha → Beta"), (
            "the confirmation message must be edited into the FRESH menu"
        )
        assert keyboard_pairs(edited_markup(callback)) == {
            (ADD_BUTTON, "st:add"),
            (DELETE_BUTTON, "st:del"),
        }
        assert answer_text(callback) == "Pair added: Alpha → Beta."

    async def test_confirming_a_duplicate_keeps_the_confirmation(self, owner):
        """``st:yes`` -> ``False``: alert only, the state STAYS confirm."""
        await set_pairs(PAIRS)
        bot = make_bot(admins=bot_admins(SOURCE_ID, TARGET_REF), chats=TITLE_CHATS)
        await start_add(bot=bot)
        await dispatch(dm_message("-100111", bot=bot))
        await dispatch(dm_message("@forumgroup", bot=bot))
        callback = make_callback("st:yes", bot=bot)

        await dispatch_callback(callback)

        assert answer_text(callback) == DUPLICATE_PAIR
        assert await stored_count() == 1, "the duplicate must not be stored a second time"
        cancel = make_callback("st:no", bot=bot)
        await dispatch_callback(cancel)
        assert edited_text(cancel) == CANCELLED, (
            "the confirm state must SURVIVE the duplicate — Cancel still works"
        )

    async def test_cancel_button_clears_the_confirmation_without_adding(self, owner):
        """``st:no`` -> cleared state, ``watcher.cancelled``, no keyboard."""
        bot = make_bot(admins=bot_admins(SOURCE_ID, TARGET_REF), chats=TITLE_CHATS)
        await start_add(bot=bot)
        await dispatch(dm_message("-100111", bot=bot))
        await dispatch(dm_message("@forumgroup", bot=bot))
        callback = make_callback("st:no", bot=bot)

        consumed = await dispatch_callback(callback)

        assert consumed is not None, "st:no must be consumed by the settings router"
        assert edited_text(callback) == CANCELLED
        assert edited_markup(callback) is None, "the cancellation edit must carry no keyboard"
        assert await stored_count() == 0, "Cancel must never add a pair"
        late = dm_message("@forumgroup", bot=bot)
        assert await dispatch(late) is None, "the confirm state must be cleared"

    async def test_cancel_command_clears_an_active_step(self, owner):
        """``/cancel`` in a DM state -> cleared + ``watcher.cancelled``."""
        await start_add()
        message = dm_message("/cancel")

        consumed = await dispatch(message)

        assert consumed is not None, "/cancel must be consumed by the settings router"
        assert reply_text(message) == CANCELLED
        late = dm_message("-100111")
        assert await dispatch(late) is None, "the state must be cleared by /cancel"

    async def test_confirm_yes_without_any_state_adds_nothing(self, owner):
        """A stray ``st:yes`` must never create a pair."""
        callback = make_callback("st:yes")

        await dispatch_callback(callback)

        assert await stored_count() == 0, "st:yes outside the flow must add nothing"

    async def test_a_plain_dm_message_is_not_intercepted(self):
        """Messages that are neither in a state nor ``/settings`` pass through."""
        message = dm_message("hello there")

        consumed = await dispatch(message)

        assert consumed is None, "the settings router must not intercept stray messages"
        assert message.answer.await_count == 0


# --- tests: the delete flow ----------------------------------------------------


class TestDeleteFlow:
    """``st:del``, ``st:del:{pair_id}`` and ``st:back``."""

    async def test_delete_on_an_empty_registry_answers_no_pairs(self, owner):
        """No pairs -> ``callback.answer(no_pairs)``, the menu is NOT edited."""
        callback = make_callback("st:del")

        consumed = await dispatch_callback(callback)

        assert consumed is not None, "st:del must be consumed by the settings router"
        assert answer_text(callback) == NO_PAIRS
        assert callback.message.edit_text.await_count == 0, (
            "without pairs there is no prompt to edit the menu into"
        )

    async def test_delete_prompts_one_button_per_pair_plus_back(self, owner):
        """``delete_prompt`` + one ``st:del:{id}`` button per pair + Back."""
        await set_pairs([*PAIRS, SECOND_PAIR])
        first, second = await stored_pairs()
        bot = make_bot(chats=TITLE_CHATS)
        callback = make_callback("st:del", bot=bot)

        await dispatch_callback(callback)

        assert edited_text(callback) == DELETE_PROMPT
        assert keyboard_pairs(edited_markup(callback)) == {
            ("Alpha → Beta", f"st:del:{first.id}"),
            ("Gamma → Delta", f"st:del:{second.id}"),
            (BACK_BUTTON, "st:back"),
        }, "the prompt must list every pair by its real id plus Back"

    async def test_delete_by_id_removes_the_pair_and_renders_the_fresh_menu(self, owner):
        """``remove_pair(id)`` -> fresh menu + ``pair_removed`` alert."""
        await set_pairs([*PAIRS, SECOND_PAIR])
        first, _second = await stored_pairs()
        bot = make_bot(chats=TITLE_CHATS)
        callback = make_callback(f"st:del:{first.id}", bot=bot)

        consumed = await dispatch_callback(callback)

        assert consumed is not None, "st:del:{id} must be consumed by the settings router"
        assert await stored_count() == 1, "the pair must be gone from the table"
        assert chats_mod().chats.target_for(SOURCE_ID) is None, (
            "remove_pair must refresh the registry too"
        )
        assert edited_text(callback) == menu_text(1, "Gamma → Delta"), (
            "the prompt message must be edited into the FRESH menu"
        )
        assert keyboard_pairs(edited_markup(callback)) == {
            (ADD_BUTTON, "st:add"),
            (DELETE_BUTTON, "st:del"),
        }
        assert answer_text(callback) == PAIR_REMOVED

    async def test_deleting_the_same_pair_twice_answers_no_pairs(self, owner):
        """Race: the row is already gone -> fresh menu + ``no_pairs`` alert."""
        await set_pairs(PAIRS)
        (first,) = await stored_pairs()
        bot = make_bot(chats=TITLE_CHATS)
        await dispatch_callback(make_callback(f"st:del:{first.id}", bot=bot))
        raced = make_callback(f"st:del:{first.id}", bot=bot)

        await dispatch_callback(raced)

        assert edited_text(raced) == menu_text(0, NO_PAIRS), (
            "the raced click must still edit into the FRESH menu"
        )
        assert answer_text(raced) == NO_PAIRS
        assert await stored_count() == 0

    async def test_back_returns_to_the_fresh_menu(self, owner):
        """``st:back`` edits the message into the current menu."""
        await set_pairs(PAIRS)
        bot = make_bot(chats=TITLE_CHATS)
        callback = make_callback("st:back", bot=bot)

        consumed = await dispatch_callback(callback)

        assert consumed is not None, "st:back must be consumed by the settings router"
        assert edited_text(callback) == menu_text(1, "Alpha → Beta")
        assert keyboard_pairs(edited_markup(callback)) == {
            (ADD_BUTTON, "st:add"),
            (DELETE_BUTTON, "st:del"),
        }


# --- tests: the inline chat pickers -----------------------------------------


class TestPickKeyboard:
    """Both waiting prompts carry the chat buttons + Cancel (spec pin B1)."""

    async def test_the_source_step_lists_every_known_chat_and_cancel(self, owner):
        """``st:add`` edits into ``send_source`` + the picker (registry order)."""
        await set_pairs([PAIRS[0], {"source": TARGET_REF, "target": SECOND_TARGET_REF}])
        bot = make_bot(chats=TITLE_CHATS)
        callback = make_callback("st:add", bot=bot)

        consumed = await dispatch_callback(callback)

        assert consumed is not None, "st:add must still open the flow"
        assert edited_text(callback) == SEND_SOURCE, "the prompt text itself must not change"
        assert keyboard_buttons(edited_markup(callback)) == [
            ("Alpha", "st:pick:-100111"),
            ("Beta", "st:pick:@forumgroup"),
            ("Delta", "st:pick:@othergroup"),
            ("Cancel", "st:cancel"),
        ], (
            "union of the sources and the targets of every pair in registry "
            "order (@forumgroup collapsed at its first place), labels via "
            "get_chat, Cancel last"
        )

    async def test_the_target_step_carries_the_same_picker(self, owner):
        """The ``send_target`` answer carries the very same buttons."""
        await set_pairs(PAIRS)
        bot = make_bot(chats=TITLE_CHATS)
        await start_add(bot=bot)
        message = dm_message(str(SOURCE_ID), bot=bot)

        consumed = await dispatch(message)

        assert consumed is not None, "the waiting chat must consume the answer"
        assert reply_text(message) == SEND_TARGET, "the prompt text itself must not change"
        assert keyboard_buttons(reply_markup(message)) == [
            ("Alpha", "st:pick:-100111"),
            ("Beta", "st:pick:@forumgroup"),
            ("Cancel", "st:cancel"),
        ]

    async def test_an_empty_registry_keeps_the_target_prompt_keyboardless(self, owner):
        """No chats to offer → no ``reply_markup`` (the status quo, GREEN in RED)."""
        await start_add()
        message = dm_message(str(SOURCE_ID))

        consumed = await dispatch(message)

        assert consumed is not None, "the waiting chat must consume the answer"
        assert reply_text(message) == SEND_TARGET
        assert reply_markup(message) is None, (
            "an empty registry must not build a keyboard for the prompt"
        )


class TestPickCallback:
    """``st:pick:<ref>`` walks the same path as a typed answer (spec pin B2)."""

    async def test_a_pick_button_reaches_the_confirmation_like_a_typed_answer(self, owner):
        """wait_source → wait_target → confirm, entirely through the buttons."""
        await set_pairs(PAIRS)
        bot = make_bot(admins=bot_admins(SOURCE_ID, TARGET_REF), chats=TITLE_CHATS)
        await start_add(bot=bot)
        source_click = make_callback(f"st:pick:{SOURCE_ID}", bot=bot)

        consumed = await dispatch_callback(source_click)

        assert consumed is not None, "st:pick:<ref> must be registered on the settings router"
        assert reply_text(source_click.message) == SEND_TARGET
        state = settings_mod().states.get(DM_CHAT_ID)
        assert state is not None and state.step == "wait_target"
        assert state.source == SOURCE_ID, "the picked ref lands in state.source"
        assert_spinner_cleared(source_click)

        target_click = make_callback(f"st:pick:{TARGET_REF}", bot=bot)

        consumed = await dispatch_callback(target_click)

        assert consumed is not None
        assert reply_text(target_click.message) == "Add pair: Alpha → Beta?"
        assert keyboard_pairs(reply_markup(target_click.message)) == {
            (CONFIRM_BUTTON, "st:yes"),
            (CANCEL_BUTTON, "st:no"),
        }
        state = settings_mod().states.get(DM_CHAT_ID)
        assert state.step == "confirm" and state.target == TARGET_REF
        assert await stored_count() == 1, (
            "only the seeded pair — ours must wait for the Confirm button"
        )
        assert_spinner_cleared(target_click)

    async def test_a_garbage_ref_in_a_pick_button_is_invalid_input(self, owner):
        """A payload that parses to no chat → the typed-answer refusal, step stays."""
        await start_add()
        bot = make_bot()
        click = make_callback("st:pick:not a chat", bot=bot)

        consumed = await dispatch_callback(click)

        assert consumed is not None, "st:pick:<ref> must be consumed by the settings router"
        assert reply_text(click.message) == INVALID_INPUT, (
            "the visible refusal of a typed miss"
        )
        state = settings_mod().states.get(DM_CHAT_ID)
        assert state is not None and state.step == "wait_source"
        assert state.source is None, "an invalid pick must not move the step"
        assert_spinner_cleared(click)

    async def test_a_pick_button_during_the_confirmation_changes_nothing(self, owner):
        """The confirm step waits for Confirm/Cancel — a picker click is a no-op."""
        await set_pairs(PAIRS)
        bot = make_bot(admins=bot_admins(SOURCE_ID, TARGET_REF), chats=TITLE_CHATS)
        await start_add(bot=bot)
        await dispatch(dm_message(str(SOURCE_ID), bot=bot))
        await dispatch(dm_message(TARGET_REF, bot=bot))
        click = make_callback(f"st:pick:{SECOND_SOURCE_ID}", bot=bot)

        consumed = await dispatch_callback(click)

        assert consumed is not None, "the click is consumed — and then ignored"
        state = settings_mod().states.get(DM_CHAT_ID)
        assert state.step == "confirm", "the pick must not move the step"
        assert state.source == SOURCE_ID and state.target == TARGET_REF, (
            "nor overwrite the confirmed pair"
        )
        assert await stored_count() == 1, (
            "only the seeded pair — a pick never stores anything by itself"
        )
        assert click.message.answer.await_count == 0, (
            "the confirmation stays the only reply of the prompt"
        )
        assert_spinner_cleared(click)

    async def test_a_pick_button_without_any_state_is_a_noop(self, owner):
        """Like ``st:yes`` outside the flow: consumed, but nothing happens."""
        click = make_callback(f"st:pick:{SOURCE_ID}")

        consumed = await dispatch_callback(click)

        assert consumed is not None, "st:pick:<ref> is registered for every click"
        assert click.message.answer.await_count == 0
        assert settings_mod().states.get(DM_CHAT_ID) is None, (
            "a stray click must not open a step"
        )
        assert await stored_count() == 0
        assert_spinner_cleared(click)

    async def test_a_stranger_cannot_press_a_pick_button(self, owner):
        """ACL as ``st:add``: the refusal alerts and changes nothing."""
        await start_add()
        bot = make_bot()
        click = make_callback(f"st:pick:{SOURCE_ID}", bot=bot, user_id=STRANGER_ID)

        consumed = await dispatch_callback(click)

        assert consumed is not None, "the click is consumed — to refuse it"
        assert answer_text(click) == FORBIDDEN
        assert click.message.answer.await_count == 0, "a refusal must not advance the step"
        state = settings_mod().states.get(DM_CHAT_ID)
        assert state is not None and state.step == "wait_source"
        assert state.source is None, "a refused pick must not fill the step"


class TestSettingsCancelCallback:
    """``st:cancel`` mirrors ``/cancel`` (spec pin B3)."""

    async def test_the_cancel_button_drops_the_source_step(self, owner):
        """Any active step: cleared + ``watcher.cancelled`` visible in the chat."""
        await start_add()
        click = make_callback("st:cancel")

        consumed = await dispatch_callback(click)

        assert consumed is not None, "st:cancel must be registered on the settings router"
        assert reply_text(click.message) == CANCELLED, "the visible text of /cancel"
        assert settings_mod().states.get(DM_CHAT_ID) is None, "the step is cleared"
        late = dm_message(str(SOURCE_ID))
        assert await dispatch(late) is None, "nothing waits for an answer anymore"
        assert_spinner_cleared(click)

    async def test_the_cancel_button_also_drops_the_confirmation(self, owner):
        """The confirmation is an active state too — Cancel must free it."""
        await set_pairs(PAIRS)
        bot = make_bot(admins=bot_admins(SOURCE_ID, TARGET_REF), chats=TITLE_CHATS)
        await start_add(bot=bot)
        await dispatch(dm_message(str(SOURCE_ID), bot=bot))
        await dispatch(dm_message(TARGET_REF, bot=bot))
        click = make_callback("st:cancel", bot=bot)

        consumed = await dispatch_callback(click)

        assert consumed is not None, "st:cancel must be consumed by the settings router"
        assert reply_text(click.message) == CANCELLED, (
            "any active step cancels the same way"
        )
        assert settings_mod().states.get(DM_CHAT_ID) is None, "the confirmation is dropped"
        assert await stored_count() == 1, (
            "only the seeded pair — Cancel must never add one"
        )
        assert_spinner_cleared(click)

    async def test_the_cancel_button_without_a_state_is_a_noop(self, owner):
        """No active step → consumed, but nothing happens."""
        click = make_callback("st:cancel")

        consumed = await dispatch_callback(click)

        assert consumed is not None, "st:cancel is registered for every click"
        assert click.message.answer.await_count == 0
        assert settings_mod().states.get(DM_CHAT_ID) is None
        assert_spinner_cleared(click)

    async def test_a_stranger_cannot_cancel_with_the_button(self, owner):
        """ACL as every ``st:*`` callback: refusal alert, the step survives."""
        await start_add()
        click = make_callback("st:cancel", user_id=STRANGER_ID)

        consumed = await dispatch_callback(click)

        assert consumed is not None, "the click is consumed — to refuse it"
        assert answer_text(click) == FORBIDDEN
        assert click.message.answer.await_count == 0
        assert settings_mod().states.get(DM_CHAT_ID) is not None, (
            "a refused cancel must keep the step"
        )


# --- tests: the security-fix pins (fix package, RED phase) -------------------


#: The group chat a forwarded menu lands in — L-1 forbids every ``st:*``
#: handler from touching anything there.
GROUP_CHAT_ID = -100555

#: The short names the ``st:*`` payload pins below are parametrised by.
MENU_KINDS = ("add", "del-list", "del-id", "back", "yes", "no", "pick", "cancel")


def group_callback(data, *, bot=None, user_id=USER_ID):
    """A click whose menu message lives in a GROUP chat (not a private one)."""
    bot = bot if bot is not None else make_bot()
    message = SimpleNamespace(
        message_id=11,
        chat=SimpleNamespace(id=GROUP_CHAT_ID, type="group", username=None),
        from_user=SimpleNamespace(id=user_id),
        text="",
        bot=bot,
        answer=AsyncMock(name="answer"),
        edit_text=AsyncMock(name="edit_text"),
    )
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id),
        bot=bot,
        message=message,
        answer=AsyncMock(name="answer"),
    )


def messageless_callback(data, *, bot=None, user_id=USER_ID):
    """A click Telegram delivers WITHOUT its message (``message is None``)."""
    bot = bot if bot is not None else make_bot()
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id),
        bot=bot,
        message=None,
        answer=AsyncMock(name="answer"),
    )


def inaccessible_callback(data, *, bot=None, user_id=USER_ID):
    """An InaccessibleMessage-shaped menu message: ``chat`` yes, no ``edit_text``."""
    bot = bot if bot is not None else make_bot()
    message = SimpleNamespace(
        message_id=12,
        chat=SimpleNamespace(id=DM_CHAT_ID, type="private", username=None),
        from_user=SimpleNamespace(id=user_id),
        text="",
        bot=bot,
        answer=AsyncMock(name="answer"),
    )
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id),
        bot=bot,
        message=message,
        answer=AsyncMock(name="answer"),
    )


async def menu_data(kind: str) -> str:
    """The exact ``callback_data`` the short ``kind`` names."""
    if kind == "del-id":
        (row,) = await stored_pairs()
        return f"st:del:{row.id}"
    return {
        "add": "st:add",
        "del-list": "st:del",
        "back": "st:back",
        "yes": "st:yes",
        "no": "st:no",
        "pick": "st:pick:-100111",
        "cancel": "st:cancel",
    }[kind]


async def walk_to_confirm(bot, source=SECOND_SOURCE_ID, target=SECOND_TARGET_REF):
    """Drive the typed add flow up to the confirmation prompt for a pair.

    The registry is the caller's business: the confirmation must hold a
    pair the table does NOT know yet, so a successful ``st:yes`` really
    stores one and a refused one really must not.
    """
    await start_add(bot=bot)
    await dispatch(dm_message(str(source), bot=bot))
    await dispatch(dm_message(str(target), bot=bot))
    state = settings_mod().states.get(DM_CHAT_ID)
    assert state is not None and state.step == "confirm", (
        "sanity: these fakes must let the flow reach the confirm step"
    )
    return state


async def prepare_menu_click(kind: str):
    """Registry, flow state and ``(bot, callback_data)`` a ``st:*`` kind needs."""
    if kind in {"yes", "no"}:
        await set_pairs(PAIRS)
        bot = make_bot(
            admins=bot_admins(SECOND_SOURCE_ID, SECOND_TARGET_REF), chats=TITLE_CHATS
        )
        await walk_to_confirm(bot)
        return bot, "st:yes" if kind == "yes" else "st:no"
    await set_pairs(PAIRS)
    return make_bot(chats=TITLE_CHATS), await menu_data(kind)


async def seed_group_state(kind: str):
    """Reach the step ``kind`` needs in the DM, then file it under the group chat."""
    if kind in {"yes", "no"}:
        bot = make_bot(
            admins=bot_admins(SECOND_SOURCE_ID, SECOND_TARGET_REF), chats=TITLE_CHATS
        )
        await walk_to_confirm(bot)
    else:
        await start_add()
    settings = settings_mod()
    state = settings.states.pop(DM_CHAT_ID)
    settings.states[GROUP_CHAT_ID] = state
    return state


def admin_calls_by_ref(bot) -> dict:
    """How often each chat ref hit ``get_chat_administrators`` (by ``str(ref)``)."""
    counts: dict = {}
    for call_obj in bot.get_chat_administrators.await_args_list:
        if call_obj.args:
            ref = call_obj.args[0]
        elif "chat_id" in call_obj.kwargs:
            ref = call_obj.kwargs["chat_id"]
        else:
            ref = call_obj.kwargs.get("chat")
        key = str(ref)
        counts[key] = counts.get(key, 0) + 1
    return counts


class TestConfirmAuthorization:
    """H-1 + M-1: ``st:yes`` re-checks the caller INSIDE the confirmed pair."""

    async def test_a_caller_admin_of_only_one_chat_of_the_pair_is_refused(self):
        """Not admin of BOTH chats of the pair being added -> forbidden, no add."""
        await set_pairs(PAIRS)
        bot = make_bot(
            admins={
                str(SOURCE_ID): [member(USER_ID, "administrator")],
                str(SECOND_SOURCE_ID): [
                    member(USER_ID, "administrator"),
                    member(BOT_ID, "administrator", True),
                ],
                str(SECOND_TARGET_REF): [member(BOT_ID, "administrator", True)],
            },
            chats=TITLE_CHATS,
        )
        state = await walk_to_confirm(bot)
        callback = make_callback("st:yes", bot=bot)

        consumed = await dispatch_callback(callback)

        assert consumed is not None, "st:yes must still be consumed — to refuse it"
        assert answer_text(callback) == FORBIDDEN, "the refusal alert is the refusal"
        assert await stored_count() == 1, "the pair must NOT be added"
        assert settings_mod().states.get(DM_CHAT_ID) is state, "the confirmation stays"
        assert state.step == "confirm", "and so does its step"
        assert callback.message.edit_text.await_count == 0, "the menu is not redrawn"

    async def test_a_caller_admin_of_both_chats_of_the_pair_confirms(self):
        """Admin of source AND target of the confirmed pair -> the pair lands."""
        await set_pairs(PAIRS)
        bot = make_bot(
            admins={
                str(SOURCE_ID): [member(USER_ID, "administrator")],
                str(SECOND_SOURCE_ID): [
                    member(USER_ID, "administrator"),
                    member(BOT_ID, "administrator", True),
                ],
                str(SECOND_TARGET_REF): [
                    member(USER_ID, "administrator"),
                    member(BOT_ID, "administrator", True),
                ],
            },
            chats=TITLE_CHATS,
        )
        await walk_to_confirm(bot)
        callback = make_callback("st:yes", bot=bot)

        await dispatch_callback(callback)

        assert await stored_count() == 2, "the authorized pair reaches the table"
        assert settings_mod().states.get(DM_CHAT_ID) is None, "the confirmation is spent"
        assert edited_text(callback) == menu_text(2, "Alpha → Beta", "Gamma → Delta")
        assert keyboard_pairs(edited_markup(callback)) == {
            (ADD_BUTTON, "st:add"),
            (DELETE_BUTTON, "st:del"),
        }
        assert answer_text(callback) == "Pair added: Gamma → Delta."

    @pytest.mark.parametrize(
        "mode",
        [
            pytest.param("empty", id="empty-admin-answers"),
            pytest.param("raising", id="raising-admin-lookups"),
        ],
    )
    async def test_the_owner_confirms_without_any_admin_answers(self, owner, mode):
        """The owner is exempt from BOTH rechecks — the bootstrap guarantee."""
        await set_pairs(PAIRS)
        bot = make_bot(
            admins=bot_admins(SECOND_SOURCE_ID, SECOND_TARGET_REF), chats=TITLE_CHATS
        )
        await walk_to_confirm(bot)
        if mode == "empty":
            bot.admins.clear()
        else:
            bot.admin_errors.update(
                {str(SOURCE_ID), TARGET_REF, str(SECOND_SOURCE_ID), SECOND_TARGET_REF}
            )
        callback = make_callback("st:yes", bot=bot)

        await dispatch_callback(callback)

        assert await stored_count() == 2, "the owner must not be blocked by dead lookups"
        assert settings_mod().states.get(DM_CHAT_ID) is None
        assert answer_text(callback) == "Pair added: Gamma → Delta."

    async def test_bot_rights_revoked_before_the_confirmation_are_caught(self):
        """The bot-rights recheck is FRESH: rights dropped after wait_target lose."""
        await set_pairs(PAIRS)
        bot = make_bot(
            admins={
                str(SOURCE_ID): [member(USER_ID, "administrator")],
                str(SECOND_SOURCE_ID): [
                    member(USER_ID, "administrator"),
                    member(BOT_ID, "administrator", True),
                ],
                str(SECOND_TARGET_REF): [
                    member(USER_ID, "administrator"),
                    member(BOT_ID, "administrator", True),
                ],
            },
            chats=TITLE_CHATS,
        )
        state = await walk_to_confirm(bot)
        for ref in (str(SECOND_SOURCE_ID), SECOND_TARGET_REF):
            bot.admins[ref] = [member(USER_ID, "administrator")]
        callback = make_callback("st:yes", bot=bot)

        consumed = await dispatch_callback(callback)

        assert consumed is not None, "st:yes must still be consumed — to refuse it"
        assert answer_text(callback) == BOT_NOT_ADMIN, "the bot-rights refusal"
        assert await stored_count() == 1, "the pair must NOT be added"
        assert settings_mod().states.get(DM_CHAT_ID) is state, "the confirmation stays"
        assert state.step == "confirm", "and so does its step"
        assert callback.message.edit_text.await_count == 0, "the menu is not redrawn"

    async def test_a_raising_admin_lookup_at_the_confirmation_is_refused(self):
        """Fail-safe: a raising lookup gives the pair no rights, never escapes."""
        await set_pairs(PAIRS)
        bot = make_bot(
            admins={
                str(SOURCE_ID): [member(USER_ID, "administrator")],
                str(SECOND_SOURCE_ID): [member(BOT_ID, "administrator", True)],
                str(SECOND_TARGET_REF): [member(BOT_ID, "administrator", True)],
            },
            chats=TITLE_CHATS,
        )
        state = await walk_to_confirm(bot)
        bot.admin_errors.update({str(SECOND_SOURCE_ID), SECOND_TARGET_REF})
        callback = make_callback("st:yes", bot=bot)

        consumed = await dispatch_callback(callback)

        assert consumed is not None, "st:yes must still be consumed — to refuse it"
        assert answer_text(callback) == FORBIDDEN, "a raising pair reads as no rights"
        assert await stored_count() == 1, "the pair must NOT be added"
        assert settings_mod().states.get(DM_CHAT_ID) is state, "the confirmation stays"
        assert state.step == "confirm", "and so does its step"
        assert callback.message.edit_text.await_count == 0, "the menu is not redrawn"


class TestDeleteAuthorization:
    """H-1: ``st:del:{id}`` re-checks the caller inside the REMOVED pair."""

    async def test_a_caller_of_another_pair_cannot_remove_a_pair(self):
        """Admin of a NEIGHBOUR pair only -> forbidden alert, the row survives."""
        await set_pairs([*PAIRS, SECOND_PAIR])
        first, _second = await stored_pairs()
        bot = make_bot(
            admins={str(SECOND_SOURCE_ID): [member(USER_ID, "administrator")]},
            chats=TITLE_CHATS,
        )
        callback = make_callback(f"st:del:{first.id}", bot=bot)

        consumed = await dispatch_callback(callback)

        assert consumed is not None, "st:del:{id} must still be consumed — to refuse it"
        assert answer_text(callback) == FORBIDDEN, "the refusal alert is the refusal"
        assert await stored_count() == 2, "the pair must stay in the table"
        assert callback.message.edit_text.await_count == 0, "and no menu is rendered"

    async def test_a_caller_admin_of_both_chats_of_the_pair_removes_it(self):
        """Admin of both chats of THAT pair -> removal, exactly as before."""
        await set_pairs([*PAIRS, SECOND_PAIR])
        first, _second = await stored_pairs()
        bot = make_bot(
            admins={
                str(SOURCE_ID): [member(USER_ID, "administrator")],
                TARGET_REF: [member(USER_ID, "administrator")],
            },
            chats=TITLE_CHATS,
        )
        callback = make_callback(f"st:del:{first.id}", bot=bot)

        await dispatch_callback(callback)

        assert await stored_count() == 1, "an admin of both chats of the pair removes it"
        assert edited_text(callback) == menu_text(1, "Gamma → Delta")
        assert answer_text(callback) == PAIR_REMOVED


class TestCanManageUsesTheSharedAdminCache:
    """H-2 + M-4: ``can_manage`` serves its lookups from the shared TTL cache."""

    async def test_two_consecutive_calls_look_each_ref_up_exactly_once(self):
        """Both calls together ask every ref of the registry pair ONE time."""
        await set_pairs(PAIRS)
        bot = make_bot(admins={TARGET_REF: [member(USER_ID, "administrator")]})

        assert await settings_mod().can_manage(USER_ID, bot) is True, "sanity: allowed"
        assert await settings_mod().can_manage(USER_ID, bot) is True, "sanity: still allowed"

        counts = admin_calls_by_ref(bot)
        assert counts.get(str(SOURCE_ID), 0) == 1, "the source ref is looked up once"
        assert counts.get(TARGET_REF, 0) == 1, "the target ref is looked up once"

    async def test_a_failing_lookup_is_cached_as_a_negative_answer(self):
        """A raising API call refuses once — the retry never reaches the API."""
        await set_pairs(PAIRS)
        bot = make_bot()

        async def raise_retry_after(*args, **kwargs):
            raise TelegramRetryAfter(method=None, message="Too Many Requests", retry_after=42)

        bot.get_chat_administrators.side_effect = raise_retry_after

        assert await settings_mod().can_manage(USER_ID, bot) is False, (
            "the failure must not escape can_manage"
        )
        assert bot.get_chat_administrators.await_count == 1, (
            "the raising ref already answered the lookup"
        )

        assert await settings_mod().can_manage(USER_ID, bot) is False, "and still refuses"
        assert bot.get_chat_administrators.await_count == 1, (
            "the negative answer is cached — no second API call in the TTL window"
        )


class TestCallbacksOnlyRunInPrivateChats:
    """L-1: a ``st:*`` click from a foreign chat changes NOTHING at all."""

    @pytest.mark.parametrize("kind", MENU_KINDS)
    async def test_a_menu_click_in_a_group_is_ignored(self, owner, kind):
        """Group state, group message and the pair table all stay untouched."""
        await set_pairs(PAIRS)
        seeded = await seed_group_state(kind)
        expected_step = "confirm" if kind in {"yes", "no"} else "wait_source"
        callback = group_callback(await menu_data(kind), bot=make_bot(chats=TITLE_CHATS))

        await dispatch_callback(callback)

        assert settings_mod().states.get(GROUP_CHAT_ID) is seeded, (
            "the group chat must keep the very state object it held"
        )
        assert seeded.step == expected_step, "and no step of it may move"
        assert callback.message.answer.await_count == 0, "no answer in a foreign chat"
        assert callback.message.edit_text.await_count == 0, "no edit in a foreign chat"
        if callback.answer.await_count:
            call = callback.answer.await_args
            assert not call.args and call.kwargs.get("text") is None, (
                f"an empty spinner reply is fine, an alert is not: {call!r}"
            )
        assert await stored_count() == 1, "the pair table must not move either"

    @pytest.mark.parametrize("kind", MENU_KINDS)
    async def test_a_menu_click_without_a_message_is_ignored(self, owner, kind):
        """No message to act on: no state is created, answered or edited."""
        await set_pairs(PAIRS)
        callback = messageless_callback(
            await menu_data(kind), bot=make_bot(chats=TITLE_CHATS)
        )

        await dispatch_callback(callback)

        assert settings_mod().states == {}, "without a chat there is no state to touch"
        if callback.answer.await_count:
            call = callback.answer.await_args
            assert not call.args and call.kwargs.get("text") is None, (
                f"an empty spinner reply is fine, an alert is not: {call!r}"
            )
        assert await stored_count() == 1, "the pair table must not move either"


class TestMenuEditsNeverPropagateTelegramErrors:
    """L-2: a failing (or missing) ``edit_text`` never escapes a ``st:*`` handler."""

    @pytest.mark.parametrize("kind", ("add", "del-list", "del-id", "back", "yes", "no"))
    async def test_a_not_modified_edit_is_swallowed_and_the_spinner_clears(
        self, owner, kind
    ):
        """TelegramBadRequest("Message is not modified") stays inside the handler."""
        bot, data = await prepare_menu_click(kind)
        callback = make_callback(data, bot=bot)
        callback.message.edit_text.side_effect = TelegramBadRequest(
            method=None, message="Message is not modified"
        )

        consumed = await dispatch_callback(callback)

        assert consumed is not None, f"{kind} must still consume the click"
        assert callback.answer.await_count >= 1, (
            "the loading spinner must be cleared even though the edit failed"
        )

    @pytest.mark.parametrize("kind", ("add", "back"))
    async def test_a_menu_message_without_edit_text_never_crashes(self, owner, kind):
        """An InaccessibleMessage-shaped chat (no ``edit_text``) is no AttributeError."""
        bot, data = await prepare_menu_click(kind)
        callback = inaccessible_callback(data, bot=bot)

        consumed = await dispatch_callback(callback)

        assert consumed is not None, f"{kind} must still consume the click"
        assert callback.answer.await_count >= 1, "the spinner must be cleared"
        if kind == "add":
            state = settings_mod().states.get(DM_CHAT_ID)
            assert state is not None, "st:add still opens the flow"
            assert state.step == "wait_source", "and lands in wait_source"


class TestPickerSkipsOversizedCallbackData:
    """L-5: a ref whose ``st:pick:`` payload would exceed 64 bytes never renders."""

    async def test_the_oversized_ref_is_dropped_and_the_short_ones_stay(self, owner):
        """One button per SHORT ref (registry order) plus Cancel — no exception."""
        long_ref = "@" + "a" * 60
        assert len(("st:pick:" + long_ref).encode("utf-8")) > 64, (
            "sanity: the pin ref really is oversized"
        )
        await set_pairs([{"source": long_ref, "target": TARGET_REF}, PAIRS[0]])
        bot = make_bot(chats=TITLE_CHATS)
        callback = make_callback("st:add", bot=bot)

        consumed = await dispatch_callback(callback)

        assert consumed is not None, "st:add must still open the flow"
        assert keyboard_buttons(edited_markup(callback)) == [
            ("Beta", "st:pick:@forumgroup"),
            ("Alpha", "st:pick:-100111"),
            ("Cancel", "st:cancel"),
        ], "the oversized button is skipped, every short ref stays, Cancel closes"


class TestSettingsCommandResetsTheFlowState:
    """L-7: ``/settings`` drops an open step — no ghost state eats the next text."""

    async def test_the_command_drops_an_active_step_and_shows_the_menu(self, owner):
        """An open wait_source step + ``/settings`` -> state gone, menu shown."""
        await start_add()
        assert settings_mod().states.get(DM_CHAT_ID) is not None, "sanity: a step is open"
        message = dm_message("/settings")

        consumed = await dispatch(message)

        assert consumed is not None, "/settings must be consumed by the menu handler"
        assert settings_mod().states.get(DM_CHAT_ID) is None, (
            "the command must reset the chat's flow state"
        )
        assert reply_text(message) == menu_text(0, NO_PAIRS)
        assert keyboard_pairs(reply_markup(message)) == {(ADD_BUTTON, "st:add")}

    async def test_the_text_after_the_command_is_not_intercepted(self, owner):
        """With the step dropped the next plain text passes through untouched."""
        await start_add()
        await dispatch(dm_message("/settings"))
        plain = dm_message("hello there")

        consumed = await dispatch(plain)

        assert consumed is None, "the reset step must not swallow the next text"
        assert plain.answer.await_count == 0, "and nothing may answer it"


# --- tests: the short-lived result cache of the confirm rechecks (N1b) ----------


async def walk_to_confirm_one_sided():
    """Registry, bot and confirm state where the CALLER admins ONE chat only.

    ``USER_ID`` opens the menu as an admin of the registry pair (ACL
    branch (b)) and confirms the SECOND pair, of which they admin the
    source alone — so ``st:yes`` must refuse them with
    ``settings.forbidden``. The bot itself holds delete rights in both
    chats of the second pair, so the flow really reaches ``confirm``
    and only the caller recheck stands between the click and the
    insert. Returns ``(bot, state)``; the same bot object carries every
    ``get_chat_administrators`` counter of the test.
    """
    await set_pairs(PAIRS)
    bot = make_bot(
        admins={
            str(SOURCE_ID): [member(USER_ID, "administrator")],
            str(SECOND_SOURCE_ID): [
                member(USER_ID, "administrator"),
                member(BOT_ID, "administrator", True),
            ],
            str(SECOND_TARGET_REF): [member(BOT_ID, "administrator", True)],
        },
        chats=TITLE_CHATS,
    )
    state = await walk_to_confirm(bot)
    return bot, state


class TestConfirmRechecksAreCachedForAShortWindow:
    """N1b: a repeated ``st:yes`` is answered from ``bot.admin_cache``.

    A REFUSED confirm leaves the ``confirm`` state open, so a user
    hammering the button used to hammer the API with it (two fresh
    ``get_chat_administrators`` for the caller check, plus the bot-rights
    recheck, per click). The contract: the verdict of the PAIR of
    rechecks — «the caller admins BOTH chats of the confirmed pair» and
    «the bot may still move» — lives in ``bot.admin_cache`` under the
    key ``(caller_id, str(source), str(target))`` for ``FRESH_TTL``
    seconds (the constant is pinned at 3.0 and patchable), so the very
    same click inside that window never reaches the API again. The
    wait_target check (``_bot_may_move`` on the way INTO the
    confirmation) stays FRESH — it runs first and is not part of this
    cache.
    """

    def test_the_fresh_ttl_constant_is_the_short_three_second_window(self):
        """``bot.admin_cache.FRESH_TTL`` is the patchable 3.0-second window.

        Long enough to swallow a hammered button, short enough that a
        rights change is noticed seconds later — and it must live in
        ``bot.admin_cache`` (the module ``clear()`` empties), not in
        the router.
        """
        fresh_ttl = getattr(admin_cache_mod(), "FRESH_TTL", None)

        assert fresh_ttl == 3.0, (
            "bot.admin_cache must expose FRESH_TTL = 3.0 (seconds, patchable) "
            f"— got {fresh_ttl!r}"
        )

    async def test_the_second_click_of_the_same_caller_adds_no_api_call(self):
        """A refused click then the VERY same click: the second one asks nothing."""
        bot, state = await walk_to_confirm_one_sided()
        before_first = bot.get_chat_administrators.await_count

        first = make_callback("st:yes", bot=bot)
        consumed_first = await dispatch_callback(first)
        after_first = bot.get_chat_administrators.await_count
        counts_first = admin_calls_by_ref(bot)

        assert consumed_first is not None, "st:yes must still be consumed — to refuse it"
        assert after_first > before_first, "the FIRST click runs its rechecks FRESH"
        assert answer_text(first) == FORBIDDEN, "the caller admins one chat of the pair only"

        second = make_callback("st:yes", bot=bot)
        consumed_second = await dispatch_callback(second)

        assert consumed_second is not None, "the repeated click is consumed too"
        assert answer_text(second) == FORBIDDEN, "and is refused exactly the same way"
        assert bot.get_chat_administrators.await_count == after_first, (
            "the second click inside the FRESH_TTL window must add ZERO "
            "get_chat_administrators calls"
        )
        assert admin_calls_by_ref(bot) == counts_first, "— not for a single ref"
        assert await stored_count() == 1, "no pair may be added by the hammering"
        assert settings_mod().states.get(DM_CHAT_ID) is state, "the confirmation survives"
        assert state.step == "confirm", "and so does its step"
        assert first.message.edit_text.await_count == 0, "a refusal redraws no menu"
        assert second.message.edit_text.await_count == 0, "and neither does the repeat"

    async def test_patching_fresh_ttl_to_zero_makes_the_next_click_fresh_again(
        self, monkeypatch
    ):
        """The window is the ``FRESH_TTL`` constant of ``bot.admin_cache``."""
        bot, _state = await walk_to_confirm_one_sided()
        monkeypatch.setattr(admin_cache_mod(), "FRESH_TTL", 0)

        first = make_callback("st:yes", bot=bot)
        await dispatch_callback(first)
        after_first = bot.get_chat_administrators.await_count
        assert answer_text(first) == FORBIDDEN, "sanity: refused once"
        assert after_first > 0, "sanity: the first click ran the rechecks"

        second = make_callback("st:yes", bot=bot)
        await dispatch_callback(second)

        assert answer_text(second) == FORBIDDEN, "the refusal itself never changes"
        assert bot.get_chat_administrators.await_count > after_first, (
            "FRESH_TTL = 0 expires the verdict at once — the next click must "
            "go to the API again"
        )

    async def test_clearing_the_shared_cache_makes_the_next_click_fresh(self):
        """``admin_cache.clear()`` (the per-test reset) drops the verdict too.

        Without it the tests would «pack»: one test's refusal would
        silently answer the next test's click.
        """
        bot, _state = await walk_to_confirm_one_sided()

        first = make_callback("st:yes", bot=bot)
        await dispatch_callback(first)
        counts_first = admin_calls_by_ref(bot)
        assert answer_text(first) == FORBIDDEN, "sanity: refused once"

        admin_cache_mod().clear()
        second = make_callback("st:yes", bot=bot)
        await dispatch_callback(second)
        counts_second = admin_calls_by_ref(bot)

        assert answer_text(second) == FORBIDDEN, "refused again"
        for ref in (str(SECOND_SOURCE_ID), SECOND_TARGET_REF):
            assert counts_second.get(ref, 0) > counts_first.get(ref, 0), (
                f"after admin_cache.clear() the recheck of {ref} must run fresh"
            )
        assert await stored_count() == 1, "still no pair added"

    async def test_another_caller_is_never_served_the_cached_verdict(self):
        """The verdict belongs to ``(caller, source, target)`` — not to the pair.

        Serving another caller the cached verdict of this pair would let
        an admin of some OTHER pair ride a colleague's confirmation, so
        the caller id is part of the key.
        """
        await set_pairs(PAIRS)
        bot = make_bot(
            admins={
                str(SOURCE_ID): [
                    member(USER_ID, "administrator"),
                    member(STRANGER_ID, "administrator"),
                ],
                str(SECOND_SOURCE_ID): [
                    member(USER_ID, "administrator"),
                    member(BOT_ID, "administrator", True),
                ],
                str(SECOND_TARGET_REF): [member(BOT_ID, "administrator", True)],
            },
            chats=TITLE_CHATS,
        )
        await walk_to_confirm(bot)

        mine = make_callback("st:yes", bot=bot, user_id=USER_ID)
        await dispatch_callback(mine)
        assert answer_text(mine) == FORBIDDEN, "sanity: the first caller is refused"
        counts_mine = admin_calls_by_ref(bot)

        theirs = make_callback("st:yes", bot=bot, user_id=STRANGER_ID)
        consumed = await dispatch_callback(theirs)

        assert consumed is not None, "the stranger's click is consumed too"
        assert answer_text(theirs) == FORBIDDEN, "they admin no chat of this pair"
        counts_theirs = admin_calls_by_ref(bot)
        assert counts_theirs.get(str(SECOND_SOURCE_ID), 0) > counts_mine.get(
            str(SECOND_SOURCE_ID), 0
        ), (
            "another caller must run ITS OWN recheck — a verdict cached for a "
            "different caller_id may never answer for them"
        )


# --- tests: the oversized pair_added alert of st:yes (N9) ----------------------


class TestThePairAddedAlertFitsTheSpinner:
    """N9: the success alert of ``st:yes`` must fit Telegram and never crash."""

    async def test_two_128_char_titles_still_produce_a_short_pair_added_alert(self, owner):
        """Two 128-char titles → the alert is ≤200 chars and keeps its prefix.

        ``settings.pair_added`` renders both titles, so the naive text
        is 12+128+3+128+1 = 272 chars → TelegramBadRequest AFTER the
        state was dropped and the menu rendered (the spinner hangs).
        Where the text gets cut is the dev's business — the alert must
        still be an alert, still start with the pinned prefix and fit.
        """
        await set_pairs(PAIRS)
        bot = make_bot(
            admins=bot_admins(SECOND_SOURCE_ID, SECOND_TARGET_REF),
            chats={
                str(SECOND_SOURCE_ID): SimpleNamespace(title="S" * 128, username=None),
                SECOND_TARGET_REF: SimpleNamespace(title="T" * 128, username=None),
            },
        )
        await walk_to_confirm(bot)
        callback = make_callback("st:yes", bot=bot)

        consumed = await dispatch_callback(callback)

        assert consumed is not None, "st:yes must still be consumed"
        assert callback.answer.await_count == 1, "the spinner must be answered exactly once"
        text = answer_text(callback)
        assert text.startswith("Pair added"), (
            "the alert must keep its pinned prefix, got "
            f"{text[:40]!r}{'…' if len(text) > 40 else ''}"
        )
        assert len(text) <= 200, (
            f"the alert must fit Telegram's 200-char limit, got {len(text)} chars"
        )
        assert await stored_count() == 2, "sanity: the pair still lands"
        assert settings_mod().states.get(DM_CHAT_ID) is None, "sanity: the flow is spent"
        assert callback.message.edit_text.await_count == 1, "sanity: the fresh menu is rendered"

    async def test_a_raising_final_alert_never_escapes_the_handler(self, owner):
        """A ``TelegramBadRequest`` out of the LAST ``callback.answer`` is swallowed.

        The spinner reply runs AFTER the state pop and the menu render,
        so its failure must not tear the dispatch down — the pair stays
        stored, the menu stays rendered, nothing propagates.
        """
        await set_pairs(PAIRS)
        bot = make_bot(
            admins=bot_admins(SECOND_SOURCE_ID, SECOND_TARGET_REF), chats=TITLE_CHATS
        )
        await walk_to_confirm(bot)
        callback = make_callback("st:yes", bot=bot)
        callback.answer.side_effect = TelegramBadRequest(
            method=None, message="BUTTON_DATA_INVALID"
        )

        consumed = await dispatch_callback(callback)

        assert consumed is not None, (
            "a failing alert must not abort the dispatch — it is the last "
            "thing the handler does"
        )
        assert settings_mod().states.get(DM_CHAT_ID) is None, "the confirmation is already spent"
        assert callback.message.edit_text.await_count == 1, "and the fresh menu is rendered"
        assert await stored_count() == 2, "the pair must stay stored"

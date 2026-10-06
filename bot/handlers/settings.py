"""Settings menu router: the ``/settings`` command and its pair flow (cycle B).

The router owns private chats and registers FIRST (``settings`` →
``watcher`` → ``buffering``), so a waiting chat never leaks its answers
to the other routers:

- ``/settings`` opens the menu for a user who passes ``can_manage``:
  the configured owner (``OWNER_ID``, read from ``bot.config.settings``
  at call time) or a creator/administrator of at least one chat of ANY
  registered pair — ``get_chat_administrators`` of the source AND the
  target of every pair through the SHARED TTL cache
  (``bot.admin_cache``), a raising pair contributing nothing (fail-safe)
  while the scan continues. A refusal answers ``settings.forbidden``
  and nothing else happens; the command also DROPS any open flow step
  of the chat first (L-7), so no ghost state swallows the next text;
- menus are re-rendered through ``callback.message.edit_text``, alerts
  go through ``callback.answer``; every callback runs a PRIVATE-CHAT
  guard first (L-1: a group click or a click without its message is
  ignored outright — no state read or write, no answer, no edit, just
  an empty spinner reply) and then the same ACL gate (a refusal never
  opens a step and never edits the message); every menu/prompt edit
  goes through ``_edit_or_ignore`` (L-2: no ``edit_text`` attribute →
  skipped, a ``TelegramBadRequest`` → logged at DEBUG, never raised);
- the flow itself lives in the module-level ``states`` dict keyed by
  the DM chat id (``MenuState``: ``wait_source`` → ``wait_target`` →
  ``confirm``). While a chat holds a state its incoming texts/forwards
  are consumed here: a forward resolves through
  ``chats.origin_chat_ref``, plain text through ``chats.parse_chat_ref``
  (a miss answers ``settings.invalid_input`` and keeps the step). At
  ``wait_target`` the checks run in THIS order: (1) self pair via
  ``chats.is_same_chat`` → ``settings.self_pair``, the step stays;
  (2) the bot must admin BOTH chats with delete rights → otherwise
  ``settings.bot_not_admin``, the step stays; (3) all good → the
  confirmation prompt with its two buttons;
- BOTH waiting prompts — the ``settings.send_source`` edit of ``st:add``
  and the ``settings.send_target`` answer — carry an inline picker: one
  button per chat of the registry (the UNION of the sources and the
  targets of every pair, duplicates by ``str(ref)`` dropped at their
  FIRST occurrence, registry order, labels resolved through
  ``bot.keyboards.chat_title``), ``callback_data = "st:pick:" +
  str(ref)`` — a ref whose payload would exceed Telegram's 64-byte
  limit is skipped (L-5) — plus a trailing ``settings.cancel_button``
  button (``st:cancel``); an EMPTY registry keeps the prompts
  keyboardless. ``st:pick:<ref>`` runs the same ACL gate and then the
  very ``_advance_with_ref`` path a typed answer takes (at ``confirm`` —
  and outside any state — the click is a silent no-op); ``st:cancel``
  mirrors ``/cancel``: an active step is dropped and
  ``watcher.cancelled`` answers visibly, no state means no-op. Every
  non-ACL branch ends in an EMPTY ``callback.answer()``;
- ``st:yes`` inserts via ``chats.add_pair`` (a ``False`` answer means
  the pair is already there — the confirmation SURVIVES it), ``st:no``
  and the ``/cancel`` command drop the state, ``st:del``/``st:del:{id}``
  drive removal through ``chats.remove_pair`` (the buttons carry the
  real row ids of ``chats.all_pairs()``). Both destructive clicks
  re-check a NON-owner INSIDE the pair they touch (H-1 + M-1): the
  caller must fresh-admin BOTH of its chats — ``st:yes`` refuses with
  ``settings.forbidden`` before the FRESH bot-rights re-check
  (``settings.bot_not_admin``) and before ``add_pair``, ``st:del:{id}``
  refuses with ``settings.forbidden`` before ``remove_pair``; the
  owner skips both rechecks (bootstrap), and no refusal ever reaches
  the pair table or the menu. The ``st:yes`` recheck verdict itself is
  shared for ``FRESH_TTL`` seconds per ``(caller, source, target)``
  through ``bot.admin_cache`` (N1b: a hammered confirm button costs no
  API calls, the ``wait_target`` re-check stays fresh, the owner stays
  out of the cache), and its success alert is cut to Telegram's
  200-char answer limit keeping its beginning, a ``TelegramBadRequest``
  out of that FINAL ``callback.answer`` never escaping the handler
  (N9);
- messages outside any state that are not ``/settings`` are never
  intercepted — the watcher and the buffering routers keep their events.

Every reply is a locale-pack key rendered by ``t()`` at the moment it
is sent — the texts themselves live in ``bot/locales`` only.
"""

import logging
from dataclasses import dataclass
from typing import Any

from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import admin_cache
from bot.chats import chats, is_same_chat, origin_chat_ref, parse_chat_ref
from bot.config import settings
from bot.i18n import t
from bot.keyboards import chat_title, picker_keyboard

#: Module logger — swallowed menu edits are logged at DEBUG, never raised.
logger = logging.getLogger(__name__)

#: Chat member statuses that manage a pair chat (flow check 2, ACL branch b).
ADMIN_STATUSES = ("creator", "administrator")

#: Flow steps of the pair-adding state machine.
STEP_WAIT_SOURCE = "wait_source"
STEP_WAIT_TARGET = "wait_target"
STEP_CONFIRM = "confirm"

#: The ``st:del:{id}`` callback prefix (the exact ``st:del`` is its own handler).
DELETE_PREFIX = "st:del:"

#: The ``st:pick:{ref}`` callback prefix (the picker's chat buttons).
PICK_PREFIX = "st:pick:"

#: The picker's trailing button callback (the ``/cancel`` mirror below).
CANCEL_PICK = "st:cancel"

#: Telegram's limit for a callback-answer alert; longer texts are cut HERE (N9).
ALERT_TEXT_LIMIT = 200


@dataclass
class MenuState:
    """Where one private chat stands in the pair-adding flow."""

    step: str = STEP_WAIT_SOURCE
    source: Any = None
    target: Any = None


#: Per-chat flow state, keyed by the private chat id (the tests clear
#: this dict around every test — its shape is this module's own).
states: dict[int, MenuState] = {}


async def can_manage(user_id: Any, bot: Bot) -> bool:
    """Whether ``user_id`` may open and drive the settings menu.

    Branch (a): the configured owner — ``bot.config.settings.owner_id``
    is read at CALL time (the tests repoint the instance) and must be a
    non-``None`` exact match. Branch (b): creator/administrator of at
    least one chat of ANY pair — ``get_chat_administrators`` is asked
    for the source AND the target of every pair through the SHARED TTL
    cache (``bot.admin_cache``): one lookup per ref per window, an
    EMPTY list a real «no rights» answer and a failing call cached as
    ``None`` (fail-safe: the exception never escapes) while the
    remaining pairs still count. No pairs and no owner → nobody
    (bootstrap: the first pair needs ``OWNER_ID`` in ``.env``).
    """
    if _is_owner(user_id):
        return True
    for pair in chats.all_pairs():
        members: list[Any] = []
        for ref in (pair["source"], pair["target"]):
            fetched = await admin_cache.get_chat_administrators(bot, ref)
            if fetched is None:
                # A failed lookup gives this pair no rights (the old
                # per-pair `except … continue`) — ``None`` only.
                members = []
                break
            members.extend(fetched)
        for member in members:
            user = getattr(member, "user", None)
            if (
                user is not None
                and getattr(user, "id", None) == user_id
                and getattr(member, "status", None) in ADMIN_STATUSES
            ):
                return True
    return False


def _is_owner(user_id: Any) -> bool:
    """Whether ``user_id`` is the configured owner (read at CALL time)."""
    owner = settings.owner_id
    return owner is not None and owner == user_id


async def _admins_confirm_pair(bot: Bot, source: Any, target: Any, user_id: Any) -> bool:
    """Whether ``user_id`` freshly admins BOTH chats of a pair (fail-closed).

    The confirmation (``st:yes``) and the removal (``st:del:{id}``) are
    the two destructive clicks, so each of them re-checks the caller
    INSIDE the pair it is about to touch — the menu-level ACL can only
    prove rights on SOME pair. The lookups are deliberately NOT served
    from the shared caches of ``bot.admin_cache``: a refusal must
    reflect the rights at the moment of the click. What the confirm
    click DOES share is the SHORT ``FRESH_TTL`` result cache of
    ``_confirm_verdict`` (N1b) wrapping this function together with
    ``_bot_may_move``; the removal re-check stays fully fresh. Any
    RAISING lookup, a non-admin in either chat and a missing sender all
    read as ``False`` — nothing escapes to the caller.
    """
    if _is_owner(user_id):
        return True
    for ref in (source, target):
        try:
            members = await bot.get_chat_administrators(ref)
        except Exception:
            return False
        if not any(
            getattr(getattr(member, "user", None), "id", None) == user_id
            and getattr(member, "status", None) in ADMIN_STATUSES
            for member in (members or ())
        ):
            return False
    return True


async def _confirm_verdict(bot: Bot, state: MenuState, caller: Any) -> tuple[bool, str]:
    """Verdict of the ``st:yes`` rechecks for a NON-owner caller (N1b).

    Returns ``(ok, refusal_key)``: ``ok`` is the FINAL bool of the pair
    of rechecks — «the caller admins BOTH chats of the confirmed pair»
    AND «the bot may still move» — the FIRST denying step making it
    ``False``, and ``refusal_key`` names that step's alert (``""``
    while ``ok``).

    The verdict of ``(caller_id, str(source), str(target))`` lives in
    ``bot.admin_cache`` for ``FRESH_TTL`` seconds, so a hammered
    confirm button never hammers the API: a HIT answers straight from
    the cache, a MISS runs both rechecks FRESH in their pinned order
    (``_admins_confirm_pair`` first, ``_bot_may_move`` only when the
    caller passed) and remembers the result. The owner never comes
    here, and the ``wait_target`` re-check stays out of this cache —
    it observes the bot's rights on the way INTO the confirmation.
    """
    hit, verdict = admin_cache.recall_confirm_verdict(caller, state.source, state.target)
    if hit:
        return verdict
    refusal = ""
    ok = bool(await _admins_confirm_pair(bot, state.source, state.target, caller))
    if not ok:
        refusal = "settings.forbidden"
    elif not await _bot_may_move(bot, state.source, state.target):
        ok = False
        refusal = "settings.bot_not_admin"
    verdict = (ok, refusal)
    admin_cache.remember_confirm_verdict(caller, state.source, state.target, verdict)
    return verdict


# --- message filters -------------------------------------------------------


def _command_name(text: Any) -> str | None:
    """First word of ``text`` without its ``@mention``, or ``None``.

    ``/settings`` and ``/settings@AnyBotName`` are the same command;
    ``/settingsish`` and a slash-less ``settings`` are plain texts.
    """
    if not isinstance(text, str):
        return None
    words = text.split(maxsplit=1)
    if not words:
        return None
    return words[0].split("@", 1)[0]


def is_private(message: Message) -> bool:
    """Whether the message arrives in a private chat (the menu's home)."""
    chat = getattr(message, "chat", None)
    return getattr(chat, "type", None) == "private"


def is_settings_command(message: Message) -> bool:
    """The ``/settings`` command of a private chat (a custom filter —
    aiogram's ``Command`` requires a real ``Message`` instance)."""
    return is_private(message) and _command_name(getattr(message, "text", None)) == "/settings"


def is_cancel_command(message: Message) -> bool:
    """Whether ``text`` is the ``/cancel`` command (a bot mention allowed)."""
    return _command_name(getattr(message, "text", None)) == "/cancel"


def has_active_state(message: Message) -> bool:
    """Whether the message is a private-chat text with an open menu step.

    Only such events are ever intercepted — an idle chat's messages
    pass through to the watcher and the buffering router untouched.
    """
    if not is_private(message):
        return False
    chat = getattr(message, "chat", None)
    return chat is not None and chat.id in states


# --- callback filters -------------------------------------------------------


def _callback_data_is(data: str) -> Any:
    """A filter matching the callback ``data`` exactly."""

    def matches(callback: CallbackQuery) -> bool:
        return getattr(callback, "data", None) == data

    return matches


def _callback_data_starts_with(prefix: str) -> Any:
    """A filter matching callbacks whose ``data`` starts with ``prefix``."""

    def matches(callback: CallbackQuery) -> bool:
        return isinstance(getattr(callback, "data", None), str) and callback.data.startswith(
            prefix
        )

    return matches


# --- shared rendering helpers ------------------------------------------------


async def _picker_keyboard(bot: Bot) -> InlineKeyboardMarkup | None:
    """The chat picker of the waiting prompts, or ``None`` at an empty registry.

    The union of the sources and the targets of every pair (registry
    order, duplicates by ``str(ref)`` collapsed at their FIRST
    occurrence) plus the trailing dismissal button; an EMPTY registry
    keeps the prompt keyboardless.
    """
    refs: list[Any] = []
    for pair in chats.all_pairs():
        refs.append(pair["source"])
        refs.append(pair["target"])
    return await picker_keyboard(bot, refs, prefix=PICK_PREFIX, cancel_data=CANCEL_PICK)


def _menu_keyboard(pair_count: int) -> InlineKeyboardMarkup:
    """The always-present creation button plus the removal one at pairs > 0."""
    rows = [[InlineKeyboardButton(text=t("settings.add_button"), callback_data="st:add")]]
    if pair_count:
        rows.append(
            [InlineKeyboardButton(text=t("settings.delete_button"), callback_data="st:del")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _confirm_keyboard() -> InlineKeyboardMarkup:
    """The two buttons of the confirmation prompt."""
    rows = [
        [InlineKeyboardButton(text=t("settings.confirm_button"), callback_data="st:yes")],
        [InlineKeyboardButton(text=t("settings.cancel_button"), callback_data="st:no")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_menu(bot: Bot) -> tuple[str, InlineKeyboardMarkup]:
    """The menu text plus its keyboard, rendered FRESH on every call."""
    pairs = chats.all_pairs()
    lines = [t("settings.menu_title", n=len(pairs))]
    if pairs:
        for pair in pairs:
            source_title = await chat_title(bot, pair["source"])
            target_title = await chat_title(bot, pair["target"])
            lines.append(f"{source_title} → {target_title}")
    else:
        lines.append(t("settings.no_pairs"))
    return "\n".join(lines), _menu_keyboard(len(pairs))


async def _incoming_ref(message: Message) -> int | str | None:
    """The chat ref the waiting answer carries, or ``None``.

    A forward resolves through ``chats.origin_chat_ref`` (a user origin
    shows no chat → ``None``), everything else through the strict
    ``chats.parse_chat_ref``.
    """
    origin = getattr(message, "forward_origin", None)
    if origin is not None:
        return origin_chat_ref(origin)
    return await parse_chat_ref(getattr(message, "text", None))


async def _bot_may_move(bot: Bot, source: Any, target: Any) -> bool:
    """Flow check 2: the bot admins BOTH chats WITH delete rights.

    ``bot.id`` must appear in ``get_chat_administrators`` of the source
    and of the target with status ``creator``/``administrator`` and
    ``can_delete_messages=True``; a raising call or a missing entry
    means «no rights» — never an exception out of the flow.

    Deliberately bypasses the shared caches of ``bot.admin_cache``: the
    confirmation reruns this check right before the insert, and it has
    to see the rights the bot holds at THAT moment (rights revoked
    after the target step must lose immediately, not after the 60-second
    TTL window). The ``wait_target`` step always runs it fresh; only at
    the confirm step its answer joins the SHORT ``FRESH_TTL`` verdict
    cache of ``_confirm_verdict`` (N1b).
    """
    try:
        for ref in (source, target):
            members = await bot.get_chat_administrators(ref)
            if not any(_deletes_messages(bot.id, member) for member in members):
                return False
    except Exception:
        return False
    return True


def _deletes_messages(bot_id: Any, member: Any) -> bool:
    """Whether ``member`` is the bot itself with status and delete rights."""
    user = getattr(member, "user", None)
    return (
        user is not None
        and getattr(user, "id", None) == bot_id
        and getattr(member, "status", None) in ADMIN_STATUSES
        and getattr(member, "can_delete_messages", None) is True
    )


def _menu_chat_id(callback: CallbackQuery) -> Any:
    """Private chat id the menu message lives in (the ``states`` key)."""
    message = getattr(callback, "message", None)
    chat = getattr(message, "chat", None)
    return getattr(chat, "id", None)


def _state_of(callback: CallbackQuery) -> MenuState | None:
    """The menu state of the callback's chat, or ``None``."""
    chat_id = _menu_chat_id(callback)
    return states.get(chat_id) if chat_id is not None else None


async def _refuse_when_forbidden(callback: CallbackQuery) -> bool:
    """ACL gate of every callback: alert ``settings.forbidden`` and report it."""
    sender = getattr(callback, "from_user", None)
    if await can_manage(getattr(sender, "id", None), callback.bot):
        return False
    await callback.answer(t("settings.forbidden"))
    return True


async def _ignore_unless_private(callback: CallbackQuery) -> bool:
    """L-1: ``st:*`` clicks belong to the private menu ONLY.

    A forwarded menu message in a group (or a click Telegram delivers
    without its message at all) must change NOTHING: no state read, no
    state write, no ``message.answer`` / ``edit_text``. Returns ``True``
    when the click has to be ignored; the spinner still gets its EMPTY
    ``callback.answer()`` so the button stops loading.
    """
    message = getattr(callback, "message", None)
    chat = getattr(message, "chat", None)
    if getattr(chat, "type", None) == "private":
        return False
    await callback.answer()
    return True


async def _edit_or_ignore(message: Any, text: str, reply_markup: Any = None) -> None:
    """Edit ``message`` into ``text``, swallowing every benign Telegram failure.

    L-2: an InaccessibleMessage-shaped message carries no ``edit_text``
    at all (skip it — no AttributeError out of the handler), and a
    ``TelegramBadRequest`` — «Message is not modified» first of all —
    is logged at DEBUG instead of propagating: the spinner answer (or
    the alert) that follows must still run, and the flow state must
    stay exactly where the handler left it.
    """
    edit = getattr(message, "edit_text", None)
    if edit is None:
        return
    try:
        await edit(text, reply_markup=reply_markup)
    except TelegramBadRequest as error:
        logger.debug("menu edit skipped: %s", error)


def _pair_id_of(data: Any) -> int | None:
    """The row id carried by a ``st:del:{id}`` callback, or ``None``."""
    try:
        return int(str(data).rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None


# --- message handlers --------------------------------------------------------


async def on_settings(message: Message) -> None:
    """Open the pair menu: the ACL refusal or the menu, exactly ONE answer.

    L-7: the command DROPS any open flow step of the chat before the
    render — a half-finished add must never swallow the next plain
    text after the admin reopened the menu.
    """
    states.pop(message.chat.id, None)
    sender = getattr(message, "from_user", None)
    if not await can_manage(getattr(sender, "id", None), message.bot):
        await message.answer(t("settings.forbidden"))
        return
    text, markup = await _render_menu(message.bot)
    await message.answer(text, reply_markup=markup)


async def on_cancel(message: Message) -> None:
    """``/cancel`` during an open step: drop the state, confirm it."""
    states.pop(message.chat.id, None)
    await message.answer(t("watcher.cancelled"))


async def on_pending(message: Message) -> None:
    """Serve the waiting answer: advance the step or refuse it (stays)."""
    state = states.get(message.chat.id)
    if state is None:
        return
    await _advance_with_ref(message, state, await _incoming_ref(message))


async def _advance_with_ref(message: Message, state: MenuState, ref: int | str | None) -> None:
    """Advance ``state`` by ``ref`` — the shared typed-answer / picked-button path.

    A ref that names no chat (a miss, a garbage payload) answers
    ``settings.invalid_input`` and leaves the step untouched; a valid
    ref walks ``wait_source`` → ``wait_target`` → ``confirm`` with the
    target checks in their pinned order (self pair → bot rights → the
    confirmation), every refusal keeping the state. Both entry points —
    the waiting text of ``on_pending`` and the ``st:pick:`` click — run
    through HERE, so the two answers can never drift apart.
    """
    if ref is None:
        await message.answer(t("settings.invalid_input"))
        return
    if state.step == STEP_WAIT_SOURCE:
        state.source = ref
        state.step = STEP_WAIT_TARGET
        await message.answer(
            t("settings.send_target"),
            reply_markup=await _picker_keyboard(message.bot),
        )
        return
    if state.step != STEP_WAIT_TARGET:
        # The confirmation waits for its buttons, not for free text.
        await message.answer(t("settings.invalid_input"))
        return
    if is_same_chat(state.source, ref):
        await message.answer(t("settings.self_pair"))
        return
    if not await _bot_may_move(message.bot, state.source, ref):
        await message.answer(t("settings.bot_not_admin"))
        return
    state.target = ref
    state.step = STEP_CONFIRM
    source_title = await chat_title(message.bot, state.source)
    target_title = await chat_title(message.bot, ref)
    await message.answer(
        t("settings.confirm_prompt", source=source_title, target=target_title),
        reply_markup=_confirm_keyboard(),
    )


# --- callback handlers -------------------------------------------------------


async def on_add(callback: CallbackQuery) -> None:
    """``st:add``: open the source step, the menu becomes the picker prompt."""
    if await _ignore_unless_private(callback):
        return
    if await _refuse_when_forbidden(callback):
        return
    chat_id = _menu_chat_id(callback)
    if chat_id is None:
        return
    states[chat_id] = MenuState(step=STEP_WAIT_SOURCE)
    await _edit_or_ignore(
        callback.message,
        t("settings.send_source"),
        reply_markup=await _picker_keyboard(callback.bot),
    )
    await callback.answer()


async def on_delete(callback: CallbackQuery) -> None:
    """``st:del``: list the pairs — or just alert when there is nothing to pick."""
    if await _ignore_unless_private(callback):
        return
    if await _refuse_when_forbidden(callback):
        return
    pairs = chats.all_pairs()
    if not pairs:
        await callback.answer(t("settings.no_pairs"))
        return
    rows = []
    for pair in pairs:
        source_title = await chat_title(callback.bot, pair["source"])
        target_title = await chat_title(callback.bot, pair["target"])
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{source_title} → {target_title}",
                    callback_data=f"{DELETE_PREFIX}{pair['id']}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text=t("settings.back_button"), callback_data="st:back")])
    await _edit_or_ignore(
        callback.message,
        t("settings.delete_prompt"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


async def on_delete_chosen(callback: CallbackQuery) -> None:
    """``st:del:{id}``: remove the pair (or notice the race), fresh menu either way.

    H-1: a caller who is not the owner may only remove a pair whose
    BOTH chats they admin (the check runs against THAT pair, fresh);
    a refusal alerts ``settings.forbidden`` with the row intact and the
    menu untouched. A pair id the registry no longer knows skips the
    re-check and keeps the old race behaviour (``no_pairs``).
    """
    if await _ignore_unless_private(callback):
        return
    if await _refuse_when_forbidden(callback):
        return
    pair_id = _pair_id_of(callback.data)
    sender = getattr(callback, "from_user", None)
    caller = getattr(sender, "id", None)
    if not _is_owner(caller):
        pair = next((row for row in chats.all_pairs() if row["id"] == pair_id), None)
        if pair is not None and not await _admins_confirm_pair(
            callback.bot, pair["source"], pair["target"], caller
        ):
            await callback.answer(t("settings.forbidden"))
            return
    removed = pair_id is not None and await chats.remove_pair(pair_id)
    text, markup = await _render_menu(callback.bot)
    await _edit_or_ignore(callback.message, text, reply_markup=markup)
    await callback.answer(t("settings.pair_removed") if removed else t("settings.no_pairs"))


async def on_back(callback: CallbackQuery) -> None:
    """``st:back``: edit the prompt message into the current menu."""
    if await _ignore_unless_private(callback):
        return
    if await _refuse_when_forbidden(callback):
        return
    text, markup = await _render_menu(callback.bot)
    await _edit_or_ignore(callback.message, text, reply_markup=markup)
    await callback.answer()


async def on_confirm(callback: CallbackQuery) -> None:
    """``st:yes`` in ``confirm``: insert, or keep the confirmation on a repeat.

    Order of the pre-insert checks (H-1 + M-1): the menu ACL first,
    then the flow state, then — for a caller who is NOT the owner —
    the rechecks of ``_confirm_verdict`` (N1b: answered from the SHORT
    result cache of ``bot.admin_cache`` when this very caller already
    got a verdict for this pair, fresh otherwise) — (a) the caller
    admins BOTH chats of the CONFIRMED pair (a raising lookup reads as
    no rights → ``settings.forbidden``, the state and the menu
    untouched) and only then (b) the FRESH bot-rights check
    (``settings.bot_not_admin``, same guarantees). The owner skips both
    rechecks (bootstrap guarantee), and neither refusal ever reaches
    ``chats.add_pair``.

    The success alert (N9) carries both titles, so it may overflow
    Telegram's 200-char answer limit: it is cut to fit while KEEPING
    its beginning (the pinned ``Pair added`` prefix), and a
    ``TelegramBadRequest`` out of that FINAL ``callback.answer`` is
    swallowed — the state is already dropped and the menu rendered by
    then, so a failing spinner reply must not tear the dispatch down.
    """
    if await _ignore_unless_private(callback):
        return
    if await _refuse_when_forbidden(callback):
        return
    state = _state_of(callback)
    if state is None or state.step != STEP_CONFIRM:
        return
    sender = getattr(callback, "from_user", None)
    caller = getattr(sender, "id", None)
    if not _is_owner(caller):
        ok, refusal = await _confirm_verdict(callback.bot, state, caller)
        if not ok:
            await callback.answer(t(refusal))
            return
    if not await chats.add_pair(state.source, state.target):
        # The pair is already there — the confirmation stays open so the
        # user can still back out of it.
        await callback.answer(t("settings.duplicate_pair"))
        return
    states.pop(_menu_chat_id(callback), None)
    text, markup = await _render_menu(callback.bot)
    await _edit_or_ignore(callback.message, text, reply_markup=markup)
    source_title = await chat_title(callback.bot, state.source)
    target_title = await chat_title(callback.bot, state.target)
    alert = t("settings.pair_added", source=source_title, target=target_title)
    if len(alert) > ALERT_TEXT_LIMIT:
        # N9: two 128-char titles render 272 chars — the beginning (the
        # pinned prefix included) is what has to survive the cut.
        alert = alert[:ALERT_TEXT_LIMIT]
    try:
        await callback.answer(alert)
    except TelegramBadRequest as error:
        # The state is already dropped and the menu rendered: the last
        # spinner reply failing must never tear the dispatch down (N9).
        logger.debug("pair_added alert skipped: %s", error)


async def on_decline(callback: CallbackQuery) -> None:
    """``st:no`` in ``confirm``: drop the state, the message loses its keyboard."""
    if await _ignore_unless_private(callback):
        return
    if await _refuse_when_forbidden(callback):
        return
    state = _state_of(callback)
    if state is None or state.step != STEP_CONFIRM:
        return
    states.pop(_menu_chat_id(callback), None)
    await _edit_or_ignore(callback.message, t("watcher.cancelled"))
    await callback.answer()


async def on_pick(callback: CallbackQuery) -> None:
    """``st:pick:<ref>``: the picked ref walks the typed-answer path.

    The same ACL gate as ``st:add`` (a refusal alerts and changes
    NOTHING), then the current step decides: at ``confirm`` — and
    outside any state — the click is a silent no-op; otherwise the
    payload goes through the SAME ``_advance_with_ref`` the waiting
    text uses (a payload naming no chat → ``settings.invalid_input``,
    the step stays). Every non-ACL branch clears the spinner with an
    EMPTY ``callback.answer()``.
    """
    if await _ignore_unless_private(callback):
        return
    if await _refuse_when_forbidden(callback):
        return
    state = _state_of(callback)
    if state is None or state.step == STEP_CONFIRM:
        await callback.answer()
        return
    ref = await parse_chat_ref(callback.data[len(PICK_PREFIX) :])
    await _advance_with_ref(callback.message, state, ref)
    await callback.answer()


async def on_pick_cancel(callback: CallbackQuery) -> None:
    """``st:cancel``: the ``/cancel`` mirror — an active step is dropped and
    ``watcher.cancelled`` answers visibly; without a state it no-ops.
    ACL refusal alerts, every other branch ends in an empty spinner reply."""
    if await _ignore_unless_private(callback):
        return
    if await _refuse_when_forbidden(callback):
        return
    state = _state_of(callback)
    if state is None:
        await callback.answer()
        return
    states.pop(_menu_chat_id(callback), None)
    await callback.message.answer(t("watcher.cancelled"))
    await callback.answer()


def create_router() -> Router:
    """Build a fresh settings router (one router attaches to one parent).

    Registration order IS the conflict resolution: ``/settings`` first,
    then the per-chat abandon command, then the flow handler — the flow
    consumes an event only while its chat holds a state, so idle chats
    pass through. Callbacks match their exact data (the row-id variant
    by its prefix, the picker's chat buttons by theirs) and each runs
    the ACL gate inside its handler, so a refused click is consumed
    without touching the state or the menu.
    """
    settings_router = Router()
    settings_router.message.register(on_settings, is_settings_command)
    settings_router.message.register(on_cancel, has_active_state, is_cancel_command)
    settings_router.message.register(on_pending, has_active_state)
    settings_router.callback_query.register(on_add, _callback_data_is("st:add"))
    settings_router.callback_query.register(on_delete, _callback_data_is("st:del"))
    settings_router.callback_query.register(
        on_delete_chosen, _callback_data_starts_with(DELETE_PREFIX)
    )
    settings_router.callback_query.register(on_back, _callback_data_is("st:back"))
    settings_router.callback_query.register(on_confirm, _callback_data_is("st:yes"))
    settings_router.callback_query.register(on_decline, _callback_data_is("st:no"))
    settings_router.callback_query.register(on_pick, _callback_data_starts_with(PICK_PREFIX))
    settings_router.callback_query.register(on_pick_cancel, _callback_data_is(CANCEL_PICK))
    return settings_router


#: Module-level router of this module (the one the tests dispatch through).
router = create_router()

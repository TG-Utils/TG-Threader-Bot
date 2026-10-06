"""Watcher router: turns a forwarded batch into a thread (BRIEF v3).

The router owns every configured threaded (target) chat:

- ``on_forward`` collects the admin's forwards into one pending batch
  per chat (session) and asks the question state machine as a reply to
  the FIRST message of the batch — «Which chat did you forward from?»
  when the forward origin does not identify a configured pair of THIS
  chat, else straight to «Thread title?». A batch carries exactly ONE
  source pair: a forward whose origin identifies a DIFFERENT configured
  pair re-opens the source question (F1), and the 100-message cap is
  enforced while ACCUMULATING (F5 — the refused forward never joins);
- ``on_text`` serves the admin's answers (``/cancel`` with an optional
  ``@botname`` mention, the source ref, the title — text messages only,
  F8) and executes the move: guards (batch cap → escaped invalid-target
  refusal → source-pair re-validation, F4/F7) → header → link edit →
  batch placement → forwarded copies deleted best-effort (F10) → source
  cleanup → question messages deleted → one two-line answer → session
  reset (also when the answer itself crashes, F3).

The source question of BOTH call sites in ``on_forward`` carries an
inline picker: one button per registry source whose pair targets THIS
chat (registry order, duplicates by ``str(ref)`` dropped at their FIRST
occurrence, labels resolved through ``bot.keyboards.chat_title``,
``callback_data = "w:src:" + str(ref)``) plus a trailing
``settings.cancel_button`` button (``w:cancel``); the title question
stays a plain prompt, and a registry naming no source of this chat asks
WITHOUT a ``reply_markup``. The router also registers their callbacks:
``w:src:<ref>`` re-runs the typed source answer behind the SAME admin
gate and the ``asking_source`` stage guard (a miss → the terminal
«не настроено» refusal, a hit → the pair is fixed and the title
question asked), ``w:cancel`` mirrors ``/cancel`` at any stage — both
read and write the session under the chat's lock (F6) and end every
branch in an EMPTY ``callback.answer()``.

Every reply is a key of the locale pack (``bot.i18n``), rendered by
``t()`` at the moment it is sent — the texts themselves live in
``bot/locales`` only.

The message handlers serialize on a per-chat ``asyncio.Lock`` (F6)
taken BEFORE the session state is read, so concurrent admins can never
run the same batch twice; the picker callbacks take the very same lock.
The message handlers are registered in exactly that order: a forward
carries text too, and it must be consumed as batch material — never
read as a title. Every reply goes out with ``parse_mode="HTML"``.
"""

import asyncio
import html
from datetime import datetime, timezone
from typing import Any

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, ReplyParameters

from bot import admin_cache
from bot.buffer import buffer
from bot.chats import (
    chats,
    is_configured_target,
    pair_for_origin,
    pair_for_source_ref,
    pair_is_registered,
    pair_targets_chat,
)
from bot.i18n import t
from bot.keyboards import picker_keyboard
from bot.services.threads import build_thread_url
from bot.sessions import STAGE_ASKING_SOURCE, STAGE_ASKING_TITLE, Session, sessions

#: Chat member statuses allowed to run the flow (BRIEF step 1).
ADMIN_STATUSES = ("creator", "administrator")

#: ``callback_data`` prefix of the source picker's chat buttons.
SOURCE_PICK_PREFIX = "w:src:"

#: ``callback_data`` of the source picker's trailing button.
CANCEL_PICK = "w:cancel"

# Every reply of this module is a locale-pack key (``bot/locales``),
# rendered by ``t()`` at the MOMENT of the answer: no reply text lives
# in the sources, and a locale switched mid-flight applies to the next
# reply. Dynamic fragments (sizes, the thread link, the title, the
# offending target) are HTML-escaped HERE, before ``t()`` embeds them —
# the escaping semantics of the former renderer, kept byte for byte.

#: Guard: batches over the limit are refused with the REAL size.
BATCH_LIMIT = 100

#: Maximum topic title length allowed by Telegram.
TITLE_MAX_LENGTH = 128

#: ``sendMediaGroup`` accepts at most 10 items — long albums are chunked.
MEDIA_GROUP_CHUNK = 10

#: Message media attributes in ``file_id`` extraction priority (F9).
MEDIA_KIND_PRIORITY = ("photo", "video", "audio", "document", "animation", "voice")

#: Media types ``sendMediaGroup`` accepts; one element outside this set
#: (animation/voice/sticker/media without a ``file_id``) sends the WHOLE
#: album as per-element copies instead (F9).
GROUPABLE_MEDIA_TYPES = frozenset({"photo", "video", "audio", "document"})

#: One ``asyncio.Lock`` per chat, kept for the bot's lifetime (F6).
_chat_locks: dict[Any, asyncio.Lock] = {}


def _lock_for(chat_id: Any) -> asyncio.Lock:
    """The handler lock of ``chat_id``, created on first use.

    Chats are few and bounded, so the dict may keep its locks; the lock
    is held across the WHOLE critical section of a handler — from the
    session read to the state write — so concurrent handlers of one
    chat never interleave (F6).
    """
    lock = _chat_locks.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        _chat_locks[chat_id] = lock
    return lock


async def _sender_may_run(bot: Bot, chat_id: Any, sender_id: Any) -> bool:
    """Whether ``sender_id`` admins ``chat_id`` — from the shared TTL cache.

    H-2 + M-4: every user check of this router (forwards, texts and the
    ``w:*`` clicks) goes through ``bot.admin_cache.get_chat_member``, so
    one ``(chat, user)`` lookup answers the whole batch inside the TTL
    window. A RAISING lookup is cached as ``None`` too (fail-closed):
    it reads as «not an admin» — the forward/text is silently ignored
    exactly like a member's message — and the error never escapes to
    flood the API. ``None``/an unknown status both mean «ignored».
    """
    if sender_id is None:
        return False
    member = await admin_cache.get_chat_member(bot, chat_id, sender_id)
    if member is None:
        return False
    return getattr(member, "status", None) in ADMIN_STATUSES


def is_watched_target(message: Message) -> bool:
    """Whether the message arrives in a configured threaded chat (§2)."""
    return is_configured_target(message.chat)


def has_pending_session(message: Message) -> bool:
    """Whether a batch is pending in the message's chat."""
    return sessions.get(message.chat.id) is not None


def has_text(message: Message) -> bool:
    """Whether the message carries a TEXT payload (F8).

    A sticker or a photo without a caption has ``text is None``: it must
    never reach the state machine — a missing text is NOT an answer (no
    fake «не настроено» СТОП, no title made of nothing).
    """
    return getattr(message, "text", None) is not None


def _is_cancel(text: str) -> bool:
    """Whether ``text`` is the ``/cancel`` command, a bot mention allowed (F8).

    Only the FIRST word of the text counts and everything after ``@``
    is dropped: ``/cancel`` and ``/cancel@AnyBotName`` cancel, while
    ``/cancelish`` and a slash-less ``cancel`` are ordinary texts.
    """
    words = text.split(maxsplit=1)
    if not words:
        return False
    return words[0].split("@", 1)[0] == "/cancel"


def _origin_sender_id(origin: Any) -> Any:
    """Author of a forward origin: ``sender_user.id`` → ``sender_chat.id`` → ``None``.

    The buffer lookup pins the ORIGINAL by its author (F2); an origin
    hiding the author (a channel forward) passes ``None`` and the
    sender condition is skipped.
    """
    sender_user = getattr(origin, "sender_user", None)
    if sender_user is not None:
        return getattr(sender_user, "id", None)
    sender_chat = getattr(origin, "sender_chat", None)
    if sender_chat is not None:
        return getattr(sender_chat, "id", None)
    return None


def _media_attachment(message: Message) -> Any:
    """The media payload carrying ``file_id``/``file_unique_id`` (F9).

    Priority: photo > video > audio > document > animation > voice —
    the FIRST attribute the message actually carries wins, so a video,
    a voice note or an animation stays findable in the step-5 cleanup
    and travels in albums by its original file id.
    """
    photo = getattr(message, "photo", None)
    if photo:
        return photo[-1]
    for kind in MEDIA_KIND_PRIORITY[1:]:
        attachment = getattr(message, kind, None)
        if attachment is not None:
            return attachment
    return None


def _message_ref(message: Message) -> dict:
    """Batch ref of a forwarded message: origin data plus payload."""
    origin = message.forward_origin
    origin_chat = getattr(origin, "chat", None) or getattr(origin, "sender_chat", None)
    attachment = _media_attachment(message)
    return {
        "message_id": message.message_id,
        "origin": {
            "type": getattr(origin, "type", None),
            "chat_id": getattr(origin_chat, "id", None),
            "username": getattr(origin_chat, "username", None),
            "message_id": getattr(origin, "message_id", None),
            "date": origin.date,
            "sender_id": _origin_sender_id(origin),
        },
        "text": message.text,
        "caption": message.caption,
        "file_unique_id": getattr(attachment, "file_unique_id", None),
        "file_id": getattr(attachment, "file_id", None),
        "media_group_id": getattr(message, "media_group_id", None),
        "caption_entities": getattr(message, "caption_entities", None),
        "media_type": getattr(message, "content_type", None),
    }


async def _ask(
    bot: Bot,
    chat_id: int,
    session: Session,
    question: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Send one question as a reply to the FIRST message of the batch."""
    sent = await bot.send_message(
        chat_id=chat_id,
        text=question,
        parse_mode="HTML",
        reply_to_message_id=session.messages[0]["message_id"],
        reply_markup=reply_markup,
    )
    session.add_prompt_id(sent.message_id)


async def _ask_or_reset(
    bot: Bot,
    chat_id: int,
    session: Session,
    question: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Send one question; a failed send must leave NO orphan session (F3).

    The reset runs even when the exception is re-raised (and a caller
    may swallow it): an ``asking_source``/``asking_title`` session whose
    question never reached the screen would make the next text run
    blindly against a question the admin never saw.
    """
    try:
        await _ask(bot, chat_id, session, question, reply_markup=reply_markup)
    except Exception:
        sessions.reset(chat_id)
        raise


async def _source_picker(bot: Bot, chat: Any) -> InlineKeyboardMarkup | None:
    """Source buttons of ``chat`` plus the trailing button, or ``None``.

    One button per registry source whose pair targets ``chat`` (the same
    ``pair_targets_chat`` filter the flow itself uses), registry order,
    duplicates collapsed at their FIRST occurrence; a registry naming no
    source of this chat asks its question WITHOUT a ``reply_markup``.

    The candidates are re-read from the registry's database first (the
    source of truth since cycle A): the offered buttons must reflect the
    CURRENT configuration, and a registry rewrite that skipped the cache
    refresh must not leave buttons naming pairs that no longer exist.
    A failing re-read keeps the cached view — the picker is best effort.
    """
    try:
        await chats.refresh()
    except Exception:
        pass
    refs = [pair["source"] for pair in chats.all_pairs() if pair_targets_chat(pair, chat)]
    return await picker_keyboard(bot, refs, prefix=SOURCE_PICK_PREFIX, cancel_data=CANCEL_PICK)


async def _delete_prompts(bot: Bot, chat_id: int, session: Session) -> None:
    """Remove the bot's own question messages (best effort)."""
    for prompt_id in session.prompt_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=prompt_id)
        except Exception:
            # Losing a question message must never break the flow.
            pass


async def _refuse(message: Message, session: Session, reply: str) -> None:
    """Answer one fixed refusal line, drop the questions, reset the state.

    The shared tail of every terminal answer (СТОП, batch cap, invalid
    target, ``Move failed…``): the batch never builds, so only the
    bot's own question messages may be deleted before the session goes.
    """
    await message.answer(reply, parse_mode="HTML")
    await _delete_prompts(message.bot, message.chat.id, session)
    sessions.reset(message.chat.id)


def _media_item(ref: dict) -> dict:
    """One ``sendMediaGroup`` item built from the ORIGINAL message.

    The media travels by its original ``file_id`` (no re-upload) and
    the caption with its entities is preserved verbatim.
    """
    return {
        "type": ref.get("media_type") or "photo",
        "media": ref.get("file_id"),
        "caption": ref.get("caption"),
        "caption_entities": ref.get("caption_entities"),
    }


def _groupable(ref: dict) -> bool:
    """Whether ``ref`` may travel inside a ``sendMediaGroup`` (F9)."""
    return ref.get("media_type") in GROUPABLE_MEDIA_TYPES and bool(ref.get("file_id"))


#: Placeholder standing in for a missing ``origin.date`` during the
#: placement sort. It is compared only with OTHER placeholders (the
#: leading ``date is not None`` flag keeps it away from the real —
#: possibly timezone-aware — datetimes, whose comparison with a naive
#: value would raise ``TypeError``).
_MISSING_DATE = datetime.min.replace(tzinfo=timezone.utc)


def _placement_key(ref: dict) -> tuple:
    """Sort key placing the batch in FORWARD-ORIGIN order (BRIEF step 4).

    aiogram processes one batch's updates concurrently, so
    ``session.messages`` reflects ARRIVAL order — which is not the
    order the originals were sent in. The placement runs
    ``origin.date`` ascending, ties broken by ``origin.message_id``
    ascending; a missing ``date`` reads as EARLIER than every dated
    ref, a missing ``message_id`` sorts among itself without ever
    being compared with an int (no ``TypeError``). Python's stable
    sort keeps refs equal on every part in their arrival order.
    """
    origin = ref.get("origin") or {}
    date = origin.get("date")
    message_id = origin.get("message_id")
    return (
        date is not None,
        date if date is not None else _MISSING_DATE,
        message_id is not None,
        message_id if message_id is not None else 0,
    )


async def _place_batch(bot: Bot, target_chat: Any, refs: list[dict], header_id: int) -> None:
    """Place the batch under the header, keeping shared albums glued.

    Consecutive elements sharing one ``media_group_id`` go out as a
    single ``sendMediaGroup`` (chunks of at most 10, replying to the
    header) — but only when EVERY element of the run is groupable
    (photo/video/audio/document with a ``file_id``): one animation,
    voice note, sticker or id-less element makes the WHOLE run fall
    back to per-element copies (F9). A rejected album falls back the
    same way and the flow continues — only the gluing is lost
    (BRIEF step 4).
    """
    index = 0
    while index < len(refs):
        ref = refs[index]
        group_id = ref.get("media_group_id")
        if group_id is None:
            await bot.copy_message(
                chat_id=target_chat,
                from_chat_id=target_chat,
                message_id=ref["message_id"],
                reply_to_message_id=header_id,
            )
            index += 1
            continue
        group: list[dict] = []
        while index < len(refs) and refs[index].get("media_group_id") == group_id:
            group.append(refs[index])
            index += 1
        if not all(_groupable(item) for item in group):
            for item in group:
                await bot.copy_message(
                    chat_id=target_chat,
                    from_chat_id=target_chat,
                    message_id=item["message_id"],
                    reply_to_message_id=header_id,
                )
            continue
        for start in range(0, len(group), MEDIA_GROUP_CHUNK):
            chunk = group[start : start + MEDIA_GROUP_CHUNK]
            try:
                await bot.send_media_group(
                    chat_id=target_chat,
                    media=[_media_item(item) for item in chunk],
                    reply_parameters=ReplyParameters(message_id=header_id),
                )
            except Exception:
                for item in chunk:
                    await bot.copy_message(
                        chat_id=target_chat,
                        from_chat_id=target_chat,
                        message_id=item["message_id"],
                        reply_to_message_id=header_id,
                    )


async def _cleanup_source(bot: Bot, session: Session, chat_ref: Any, refs: list[dict]) -> int:
    """Delete the found originals of the source chat (step 5, F1/F2).

    The direct channel path — ``delete_message(source,
    forward_origin.message_id)`` — is safe ONLY when the origin
    resolves to the pair FIXED ON THE SESSION: the id lives in the
    origin's chat, so a foreign pair's id must never be deleted out of
    this session's source chat. Any other origin (unconfigured,
    foreign pair) is looked up in the buffer by forward-origin date,
    the origin's sender (``sender_user.id``/``sender_chat.id``, unknown
    → ``None``) and the payload. A missing original is skipped, and a
    failing ``delete_message`` counts as «not found» — it never breaks
    the flow.
    """
    source = session.source["source"]
    deleted = 0
    for ref in refs:
        origin = ref.get("origin") or {}
        pair = pair_for_origin(origin, chat_ref)
        if origin.get("message_id") is not None and pair is not None and pair == session.source:
            found_id = origin["message_id"]
        else:
            found_id = await buffer.find_and_take(
                source,
                origin.get("date"),
                ref.get("text"),
                ref.get("caption"),
                ref.get("file_unique_id"),
                origin.get("sender_id"),
            )
        if found_id is None:
            continue
        try:
            await bot.delete_message(chat_id=source, message_id=found_id)
        except Exception:
            # The thread is already built: a failing delete of an
            # original is reported as «not found», nothing more.
            continue
        deleted += 1
    return deleted


async def _execute(message: Message, session: Session, title: str) -> None:
    """Build the thread, clean up the source and answer (steps 4–5).

    Guard order is pinned by the spec: batch cap → configured-target
    link (the offending target HTML-escaped, F7) → source-pair
    re-validation (the pair must still be registered AND target this
    chat, F4) → everything else inside one failure block (any error is
    answered with ``Move failed…`` and skips the source cleanup).
    Before placement the batch is sorted by forward-origin date
    (``_placement_key``): the store keeps ARRIVAL order, the thread
    must follow the ORIGINAL send order — same-date album members stay
    adjacent, so ``_place_batch`` still glues them into one group.
    Deleting the forwarded copies sits OUTSIDE that failure block (F10):
    the thread is built by then, so each delete is best-effort and a
    raising one only skips itself. Every API call goes to the chat the
    flow runs in — ``message.chat.id``, the very chat the pair is
    configured for.
    """
    bot = message.bot
    chat_id = message.chat.id
    refs = session.messages
    target_ref = session.source["target"] if session.source is not None else None

    size = len(refs)
    if size > BATCH_LIMIT:
        await _refuse(message, session, t("watcher.batch_too_large", n=size))
        return

    try:
        build_thread_url(target_ref, refs[0]["message_id"])
    except Exception:
        await _refuse(
            message,
            session,
            t("watcher.invalid_target", target=html.escape(str(target_ref))),
        )
        return

    if (
        session.source is None
        or not pair_targets_chat(session.source, message.chat)
        or not pair_is_registered(session.source)
    ):
        # F4: the pair went stale (deleted from the config or bound to
        # another chat) → «не настроено»: no send/edit/copy/delete of
        # any chat, only the bot's own questions go away.
        await _refuse(message, session, t("watcher.not_configured"))
        return

    # Step 4: aiogram hands the batch over in ARRIVAL order, which is
    # not the order the originals were sent in — place (and clean up)
    # by forward-origin date instead, ties by origin message id, a
    # missing date reading first. The store itself keeps the arrival
    # order: ``sorted`` builds a NEW list.
    ordered = sorted(refs, key=_placement_key)

    try:
        escaped_title = html.escape(title[:TITLE_MAX_LENGTH], quote=True)
        header_text = t("thread.header_template", title=escaped_title)
        header = await bot.send_message(chat_id=chat_id, text=header_text, parse_mode="HTML")
        thread_url = build_thread_url(target_ref, header.message_id)
        link_line = t("thread.link_line", thread_url=html.escape(thread_url, quote=True))
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=header.message_id,
            text=f"{header_text}\n{link_line}",
            parse_mode="HTML",
        )
        await _place_batch(bot, chat_id, ordered, header.message_id)
    except Exception as error:
        # The type name, not str(error): the fixed line must stay stable.
        await _refuse(message, session, t("watcher.move_failed", error=type(error).__name__))
        return

    # F10: the forwarded copies are deleted best-effort — every delete
    # in its OWN try, outside the failure block: the thread exists, so
    # a raising delete may skip itself but never fails the move.
    for ref in ordered:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=ref["message_id"])
        except Exception:
            continue

    deleted = await _cleanup_source(bot, session, message.chat, ordered)
    await _delete_prompts(bot, chat_id, session)
    reply = "\n".join(
        (
            t("watcher.created_line", n=size, thread_url=html.escape(thread_url, quote=True)),
            t("watcher.deleted_line", x=deleted, y=size),
        )
    )
    try:
        await message.answer(reply, parse_mode="HTML")
    finally:
        # F3: the move already executed — a kept session would run it twice.
        sessions.reset(chat_id)


async def on_forward(message: Message) -> None:
    """Collect a forward into the chat's pending batch (BRIEF step 1).

    The admin gate runs BEFORE any session exists: a senderless or a
    mere member's forward exits silently and leaves no state behind.
    The rest runs under the chat's lock (F6), reading and writing the
    session state only inside it: a pending batch absorbs the forward —
    unless the 100-message cap would be exceeded (F5: the refusal is
    exact, the forward does NOT join, the session resets) or the origin
    identifies a DIFFERENT configured pair (F1: one pair per batch →
    the fixed pair drops, the stage returns to ``asking_source`` and ONE
    new question is asked).
    """
    sender = message.from_user
    if sender is None:
        return
    if not await _sender_may_run(message.bot, message.chat.id, sender.id):
        return
    chat_id = message.chat.id
    async with _lock_for(chat_id):
        ref = _message_ref(message)
        session = sessions.get(chat_id)
        if session is not None:
            size = len(session.messages) + 1
            if size > BATCH_LIMIT:
                # F5: refuse BEFORE appending — an unbounded batch is a
                # memory-amplification vector; the cap state stays intact
                # for the report and then the session resets.
                await _refuse(message, session, t("watcher.batch_too_large", n=size))
                return
            if session.source is not None:
                pair = pair_for_origin(ref["origin"], message.chat)
                if pair is not None and pair != session.source:
                    # F1: one pending batch has exactly ONE source pair —
                    # a foreign configured origin re-asks the question.
                    session.source = None
                    session.stage = STAGE_ASKING_SOURCE
                    session.add(ref)
                    await _ask_or_reset(
                        message.bot,
                        chat_id,
                        session,
                        t("watcher.source_question"),
                        reply_markup=await _source_picker(message.bot, message.chat),
                    )
                    return
            # Further forwards of the same pair join the batch quietly.
            session.add(ref)
            return
        session = sessions.start(chat_id, ref)
        pair = pair_for_origin(ref["origin"], message.chat)
        if pair is not None:
            # The origin identifies a pair of THIS chat — fix it, skip question 1.
            session.source = pair
            session.stage = STAGE_ASKING_TITLE
            await _ask_or_reset(message.bot, chat_id, session, t("watcher.title_question"))
        else:
            # No pair of this chat in the origin (F4: a pair of ANOTHER
            # chat does not count) → ask where the batch came from.
            session.source = None
            session.stage = STAGE_ASKING_SOURCE
            await _ask_or_reset(
                message.bot,
                chat_id,
                session,
                t("watcher.source_question"),
                reply_markup=await _source_picker(message.bot, message.chat),
            )


async def on_text(message: Message) -> None:
    """Serve the admin's answers: state machine plus execution (steps 2–5).

    Text-only (the filter refuses everything else, F8), admin-only, and
    serialized per chat on the handler lock (F6): the stage is read and
    the state written only inside the lock.
    """
    sender = message.from_user
    if sender is None:
        return
    if not await _sender_may_run(message.bot, message.chat.id, sender.id):
        return
    chat_id = message.chat.id
    async with _lock_for(chat_id):
        session = sessions.get(chat_id)
        if session is None:
            return
        text = message.text or ""

        if _is_cancel(text):
            await _delete_prompts(message.bot, chat_id, session)
            await message.answer(t("watcher.cancelled"), parse_mode="HTML")
            sessions.reset(chat_id)
            return

        if session.stage == STAGE_ASKING_SOURCE:
            pair = pair_for_source_ref(text)
            if pair is None or not pair_targets_chat(pair, message.chat):
                # «не настроено → СТОП» (F4: a pair of ANOTHER chat reads
                # as «не настроено» HERE): no thread, no cleanup, reset.
                await _refuse(message, session, t("watcher.not_configured"))
                return
            session.source = pair
            session.stage = STAGE_ASKING_TITLE
            await _ask_or_reset(message.bot, chat_id, session, t("watcher.title_question"))
            return

        title = text.strip()
        if not title:
            # Keep waiting for a real title: the batch and prompts stay.
            await message.answer(t("watcher.empty_title"), parse_mode="HTML")
            return
        await _execute(message, session, title)


def _is_source_pick(callback: CallbackQuery) -> bool:
    """Whether the click is a source button of the question's picker."""
    return isinstance(getattr(callback, "data", None), str) and callback.data.startswith(
        SOURCE_PICK_PREFIX
    )


def _is_cancel_pick(callback: CallbackQuery) -> bool:
    """Whether the click is the picker's trailing button."""
    return getattr(callback, "data", None) == CANCEL_PICK


async def _clicker_is_admin(callback: CallbackQuery) -> bool:
    """Whether the click carries a sender that admins the button's chat.

    A senderless click looks up NOTHING (there is no user id to ask
    about); a click without its message likewise — there is no chat to
    ask about. Any other click runs its OWN gate on the chat the button
    lives in — through the shared TTL cache, exactly like ``on_text``
    and ``on_forward`` (H-2 + M-4), so one lookup answers every click
    of one admin inside the TTL window, a failing one is cached as
    ``None`` and reads as «not an admin» (fail-closed, no exception out
    of the callback).
    """
    sender = getattr(callback, "from_user", None)
    if sender is None:
        return False
    message = getattr(callback, "message", None)
    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", None)
    if chat_id is None:
        return False
    return await _sender_may_run(callback.bot, chat_id, getattr(sender, "id", None))


async def on_source_pick(callback: CallbackQuery) -> None:
    """``w:src:<ref>``: the click re-runs the typed source answer (step 2 → 3).

    Admin-gated and stage-guarded: outside ``asking_source`` — the title
    stage included — a BUTTON changes NOTHING (a TEXT there would become
    the title). A ref resolving a pair of THIS chat fixes it on the
    session and asks the title question (announced by the next question,
    never by an extra answer); a ref naming no source of this chat runs
    the TERMINAL «не настроено» refusal. The chat state is read and
    written under the chat's lock (F6), and every branch ends in an
    EMPTY ``callback.answer()`` so the spinner always stops. A click
    Telegram delivers WITHOUT its message (L-2) is ignored before
    anything else: the batch stays untouched, only the spinner dies.
    """
    if getattr(callback, "message", None) is None:
        await callback.answer()
        return
    if not await _clicker_is_admin(callback):
        await callback.answer()
        return
    message = callback.message
    chat_id = message.chat.id
    async with _lock_for(chat_id):
        session = sessions.get(chat_id)
        if session is not None and session.stage == STAGE_ASKING_SOURCE:
            ref = callback.data[len(SOURCE_PICK_PREFIX) :]
            pair = pair_for_source_ref(ref)
            if pair is None or not pair_targets_chat(pair, message.chat):
                # «не настроено → СТОП» (F4): the refusal is visible in
                # the chat, the prompts go away, the batch resets.
                await _refuse(message, session, t("watcher.not_configured"))
            else:
                session.source = pair
                session.stage = STAGE_ASKING_TITLE
                await _ask_or_reset(message.bot, chat_id, session, t("watcher.title_question"))
    await callback.answer()


async def on_source_cancel(callback: CallbackQuery) -> None:
    """``w:cancel`` mirrors ``/cancel`` at ANY stage: prompts deleted, the same
    fixed line, session reset — admin-gated, and an empty spinner reply
    in every branch. A messageless click (L-2) changes nothing but the
    spinner."""
    if getattr(callback, "message", None) is None:
        await callback.answer()
        return
    if not await _clicker_is_admin(callback):
        await callback.answer()
        return
    message = callback.message
    chat_id = message.chat.id
    async with _lock_for(chat_id):
        session = sessions.get(chat_id)
        if session is not None:
            await _delete_prompts(message.bot, chat_id, session)
            await message.answer(t("watcher.cancelled"), parse_mode="HTML")
            sessions.reset(chat_id)
    await callback.answer()


def create_router() -> Router:
    """Build a fresh watcher router (one router attaches to one parent).

    The registration order IS the conflict resolution of the spec: a
    forward must be consumed by ``on_forward`` before ``on_text`` could
    ever read its text as a title. The text handler additionally
    requires a TEXT payload (F8): a sticker or a caption-less media
    message never reaches the state machine. The picker callbacks match
    their exact ``callback_data`` shapes (``w:src:<ref>`` by its prefix).
    """
    watcher_router = Router()
    watcher_router.message.register(on_forward, F.forward_origin, is_watched_target)
    watcher_router.message.register(on_text, has_pending_session, has_text)
    watcher_router.callback_query.register(on_source_pick, _is_source_pick)
    watcher_router.callback_query.register(on_source_cancel, _is_cancel_pick)
    return watcher_router


#: Module-level router of this module (the one the tests dispatch through).
router = create_router()

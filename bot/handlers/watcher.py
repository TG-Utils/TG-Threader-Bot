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

Both handlers serialize on a per-chat ``asyncio.Lock`` (F6) taken
BEFORE the session state is read, so concurrent admins can never run
the same batch twice. The handlers are registered in exactly that
order: a forward carries text too, and it must be consumed as batch
material — never read as a title. Every reply goes out with
``parse_mode="HTML"``.
"""

import asyncio
import html
from datetime import datetime, timezone
from typing import Any

from aiogram import Bot, F, Router
from aiogram.types import Message, ReplyParameters

from bot.buffer import buffer
from bot.chats import (
    is_configured_target,
    pair_for_origin,
    pair_for_source_ref,
    pair_is_registered,
    pair_targets_chat,
)
from bot.services.templates import render
from bot.services.threads import build_thread_url
from bot.sessions import STAGE_ASKING_SOURCE, STAGE_ASKING_TITLE, Session, sessions

#: Chat member statuses allowed to run the flow (BRIEF step 1).
ADMIN_STATUSES = ("creator", "administrator")

#: The question of step 2: the origin hides its chat.
QUESTION_SOURCE = "Which chat did you forward from? Reply with @username or its id."
#: The question of step 3: always asked before anything is built.
QUESTION_TITLE = "Thread title? Send the title as a plain message."
#: ``/cancel`` from an admin: the batch stays untouched, state resets.
CANCELLED_REPLY = "Cancelled."
#: The admin's source answer is not in the registry — СТОП (step 2).
NOT_CONFIGURED_REPLY = "This chat is not configured as a source chat."
#: A whitespace-only title never creates an empty topic (step 3).
EMPTY_TITLE_REPLY = "Title is empty — send the thread title."

#: Guard: batches over the limit are refused with the REAL size.
BATCH_LIMIT = 100
BATCH_TOO_LARGE_TEMPLATE = "Batch too large ({size} messages, limit {limit}). Nothing was moved."
#: Guard: the configured target cannot form a thread link.
INVALID_TARGET_TEMPLATE = "Invalid target chat in configuration: {target}. Nothing was moved."
#: Any failure during the move is answered, never raised (type name!).
MOVE_FAILED_TEMPLATE = "Move failed: {error}. Check the target chat manually."

#: Header posted into the target chat: the topic line, title HTML-escaped.
TOPIC_TEMPLATE = "Topic: <b>{title}</b>"
#: Line the header edit adds with the link into the new thread.
THREAD_LINK_TEMPLATE = "Please use [a]this link[/a] to respond to this thread."
#: Success answer: both embedded values go through the escaping renderer (L2).
CREATED_REPLY_TEMPLATE = (
    "Thread created: {{count}} message(s). {{threadUrl}}\n"
    "Deleted {{deleted}} of {{total}} original messages."
)

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


async def _ask(bot: Bot, chat_id: int, session: Session, question: str) -> None:
    """Send one question as a reply to the FIRST message of the batch."""
    sent = await bot.send_message(
        chat_id=chat_id,
        text=question,
        parse_mode="HTML",
        reply_to_message_id=session.messages[0]["message_id"],
    )
    session.add_prompt_id(sent.message_id)


async def _ask_or_reset(bot: Bot, chat_id: int, session: Session, question: str) -> None:
    """Send one question; a failed send must leave NO orphan session (F3).

    The reset runs even when the exception is re-raised (and a caller
    may swallow it): an ``asking_source``/``asking_title`` session whose
    question never reached the screen would make the next text run
    blindly against a question the admin never saw.
    """
    try:
        await _ask(bot, chat_id, session, question)
    except Exception:
        sessions.reset(chat_id)
        raise


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
            found_id = buffer.find_and_take(
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
        await _refuse(
            message,
            session,
            BATCH_TOO_LARGE_TEMPLATE.format(size=size, limit=BATCH_LIMIT),
        )
        return

    try:
        build_thread_url(target_ref, refs[0]["message_id"])
    except Exception:
        await _refuse(
            message,
            session,
            INVALID_TARGET_TEMPLATE.format(target=html.escape(str(target_ref))),
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
        await _refuse(message, session, NOT_CONFIGURED_REPLY)
        return

    # Step 4: aiogram hands the batch over in ARRIVAL order, which is
    # not the order the originals were sent in — place (and clean up)
    # by forward-origin date instead, ties by origin message id, a
    # missing date reading first. The store itself keeps the arrival
    # order: ``sorted`` builds a NEW list.
    ordered = sorted(refs, key=_placement_key)

    try:
        escaped_title = html.escape(title[:TITLE_MAX_LENGTH], quote=True)
        header_text = TOPIC_TEMPLATE.format(title=escaped_title)
        header = await bot.send_message(chat_id=chat_id, text=header_text, parse_mode="HTML")
        thread_url = build_thread_url(target_ref, header.message_id)
        link_line = render(THREAD_LINK_TEMPLATE, {}, link_url=thread_url)
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=header.message_id,
            text=f"{header_text}\n{link_line}",
            parse_mode="HTML",
        )
        await _place_batch(bot, chat_id, ordered, header.message_id)
    except Exception as error:
        # The type name, not str(error): the fixed line must stay stable.
        await _refuse(message, session, MOVE_FAILED_TEMPLATE.format(error=type(error).__name__))
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
    reply = render(
        CREATED_REPLY_TEMPLATE,
        {
            "count": str(size),
            "threadUrl": thread_url,
            "deleted": str(deleted),
            "total": str(size),
        },
        link_url=thread_url,
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
    member = await message.bot.get_chat_member(chat_id=message.chat.id, user_id=sender.id)
    if member.status not in ADMIN_STATUSES:
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
                await _refuse(
                    message,
                    session,
                    BATCH_TOO_LARGE_TEMPLATE.format(size=size, limit=BATCH_LIMIT),
                )
                return
            if session.source is not None:
                pair = pair_for_origin(ref["origin"], message.chat)
                if pair is not None and pair != session.source:
                    # F1: one pending batch has exactly ONE source pair —
                    # a foreign configured origin re-asks the question.
                    session.source = None
                    session.stage = STAGE_ASKING_SOURCE
                    session.add(ref)
                    await _ask_or_reset(message.bot, chat_id, session, QUESTION_SOURCE)
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
            await _ask_or_reset(message.bot, chat_id, session, QUESTION_TITLE)
        else:
            # No pair of this chat in the origin (F4: a pair of ANOTHER
            # chat does not count) → ask where the batch came from.
            session.source = None
            session.stage = STAGE_ASKING_SOURCE
            await _ask_or_reset(message.bot, chat_id, session, QUESTION_SOURCE)


async def on_text(message: Message) -> None:
    """Serve the admin's answers: state machine plus execution (steps 2–5).

    Text-only (the filter refuses everything else, F8), admin-only, and
    serialized per chat on the handler lock (F6): the stage is read and
    the state written only inside the lock.
    """
    sender = message.from_user
    if sender is None:
        return
    member = await message.bot.get_chat_member(chat_id=message.chat.id, user_id=sender.id)
    if member.status not in ADMIN_STATUSES:
        return
    chat_id = message.chat.id
    async with _lock_for(chat_id):
        session = sessions.get(chat_id)
        if session is None:
            return
        text = message.text or ""

        if _is_cancel(text):
            await _delete_prompts(message.bot, chat_id, session)
            await message.answer(CANCELLED_REPLY, parse_mode="HTML")
            sessions.reset(chat_id)
            return

        if session.stage == STAGE_ASKING_SOURCE:
            pair = pair_for_source_ref(text)
            if pair is None or not pair_targets_chat(pair, message.chat):
                # «не настроено → СТОП» (F4: a pair of ANOTHER chat reads
                # as «не настроено» HERE): no thread, no cleanup, reset.
                await _refuse(message, session, NOT_CONFIGURED_REPLY)
                return
            session.source = pair
            session.stage = STAGE_ASKING_TITLE
            await _ask_or_reset(message.bot, chat_id, session, QUESTION_TITLE)
            return

        title = text.strip()
        if not title:
            # Keep waiting for a real title: the batch and prompts stay.
            await message.answer(EMPTY_TITLE_REPLY, parse_mode="HTML")
            return
        await _execute(message, session, title)


def create_router() -> Router:
    """Build a fresh watcher router (one router attaches to one parent).

    The registration order IS the conflict resolution of the spec: a
    forward must be consumed by ``on_forward`` before ``on_text`` could
    ever read its text as a title. The text handler additionally
    requires a TEXT payload (F8): a sticker or a caption-less media
    message never reaches the state machine.
    """
    watcher_router = Router()
    watcher_router.message.register(on_forward, F.forward_origin, is_watched_target)
    watcher_router.message.register(on_text, has_pending_session, has_text)
    return watcher_router


#: Module-level router of this module (the one the tests dispatch through).
router = create_router()

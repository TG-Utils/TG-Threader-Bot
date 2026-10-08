"""Inline-keyboard helpers shared by the routers: chat labels and pickers.

A picker button names a chat by its DISPLAY TITLE, resolved through
``bot.admin_cache.get_chat`` — the TTL-cached wrapper around
``bot.get_chat`` (N1a), so a menu rendered twice inside the window
costs ONE lookup per ref — with the fallback chain ``.title`` →
``.username`` rendered as ``@name`` → the ref itself, a RAISING
``get_chat`` reading as the ref too (an unavailable chat still gets a
readable button; the failure is cached as well, so it never floods the
API). The label is always a real ``str``: aiogram's
``InlineKeyboardButton.text`` is a validated string field, so nothing
but the resolved label may reach it.

``picker_keyboard`` renders the shared picker layout: one button per ref
(duplicates by ``str(ref)`` collapsed at their FIRST occurrence, in the
order the caller collected them) followed by the trailing dismissal
button (the ``settings.cancel_button`` label). An empty candidate list
yields ``None`` — the caller then sends its prompt WITHOUT a
``reply_markup``.
"""

from collections.abc import Iterable
from typing import Any

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot import admin_cache
from bot.i18n import translate as _

#: Telegram's hard limit for one ``callback_data`` payload, in bytes.
CALLBACK_DATA_LIMIT = 64


async def chat_title(bot: Bot, chat_ref: Any) -> str:
    """Display title of ``chat_ref``: ``.title`` → ``@username`` → the ref.

    Args:
        bot: Bot whose ``get_chat`` resolves the ref (through the
            shared TTL cache of ``bot.admin_cache`` — N1a).
        chat_ref: Registry ref of the chat (int id or ``@username``).

    Returns:
        The chat's title, its username rendered as ``@name``, or the ref
        itself — the ref also covers a ``get_chat`` that raises (the
        failure is negatively cached, so the repeat inside the window
        reads the same without another API call).
    """
    try:
        chat = await admin_cache.get_chat(bot, chat_ref)
    except Exception:
        return str(chat_ref)
    title = getattr(chat, "title", None)
    if title:
        return str(title)
    username = getattr(chat, "username", None)
    if username:
        return f"@{username}"
    return str(chat_ref)


def unique_refs(refs: Iterable[Any]) -> list[Any]:
    """``refs`` with duplicates by ``str(ref)`` dropped at their first occurrence.

    The registry may name the same chat from several pairs: the picker
    offers ONE button per chat, at the place of its FIRST appearance.
    """
    seen: set[str] = set()
    result: list[Any] = []
    for ref in refs:
        key = str(ref)
        if key in seen:
            continue
        seen.add(key)
        result.append(ref)
    return result


async def picker_keyboard(
    bot: Bot,
    refs: Iterable[Any],
    *,
    prefix: str,
    cancel_data: str,
) -> InlineKeyboardMarkup | None:
    """The picker of ``refs``: one labelled button each, the trailing button last.

    Args:
        bot: Bot whose ``get_chat`` labels every ref.
        refs: Candidate chats, in the order their buttons must appear.
        prefix: ``callback_data`` prefix of the pick buttons (the ref
            is appended as ``str(ref)``).
        cancel_data: ``callback_data`` of the trailing button rendered
            with the ``settings.cancel_button`` label.

    Returns:
        The markup, or ``None`` when no candidate remains — an empty
        picker must not be attached to the prompt at all.

    A ref whose full ``callback_data`` would exceed Telegram's 64-BYTE
    limit (L-5) is SKIPPED: one oversized button would make Telegram
    reject the whole message, so the short refs and the trailing button
    must survive on their own.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for ref in unique_refs(refs):
        data = f"{prefix}{ref}"
        if len(data.encode("utf-8")) > CALLBACK_DATA_LIMIT:
            continue
        rows.append(
            [
                InlineKeyboardButton(
                    text=await chat_title(bot, ref),
                    callback_data=data,
                )
            ]
        )
    if not rows:
        return None
    rows.append([InlineKeyboardButton(text=_("Cancel"), callback_data=cancel_data)])
    return InlineKeyboardMarkup(inline_keyboard=rows)

"""Shared TTL caches of Telegram lookups (security review H-2 + M-4 + N1a + N1b).

Every answer below lives in ONE process-wide ``_entries`` dict, so the
single ``clear()`` of the per-test reset drops all of them at once:

- ``get_chat_administrators`` (H-2 + M-4): a SUCCESSFUL call caches the
  returned LIST — an empty list is a real answer («nobody rights
  there») and is never confused with a failure; a RAISING call is
  cached as ``None`` — the NEGATIVE answer — so an erroring or
  flood-walled API cannot be hammered from every handler; the exception
  never escapes this module (fail-closed: ``None`` reads as «no rights»
  to every caller, which is what the gates did with an exception
  before);
- ``get_chat_member``: the ``ChatMember`` (whatever its ``status``) on
  success, ``None`` when the call raises — the same negative-caching
  rule;
- ``get_chat`` (N1a): the cached wrapper around ``bot.get_chat`` every
  menu/picker label resolves through (``bot.keyboards.chat_title``),
  window ``CHAT_TTL`` seconds. A RAISING lookup is cached too — the
  cached exception is RE-RAISED, so one failed call answers every
  repeat of the window without touching the API again (the label then
  falls back to the ref itself);
- ``remember_confirm_verdict`` / ``recall_confirm_verdict`` (N1b): the
  SHORT-lived result cache of the ``st:yes`` rechecks of the settings
  confirmation, key ``(caller_id, str(source), str(target))``, window
  ``FRESH_TTL`` = 3.0 s — a hammered confirm button may not hammer the
  API, while seconds later a rights change is noticed again;
- ``clear()`` empties every cache — the test suite resets it around
  every test, so no test ever inherits another one's answers.

Deliberately NOT served from here: the bot-rights re-check on the way
INTO the confirmation (``_bot_may_move`` of the ``wait_target`` step)
— that check must observe the CURRENT rights of the bot (a pair whose
rights were revoked must be refused at once), so it always talks to
the API fresh; only its result INSIDE the short ``FRESH_TTL`` window
of one confirmed pair is remembered (N1b).
"""

import time
from typing import Any

#: Seconds a cached answer stays valid (the tests patch this constant).
TTL = 60.0

#: Seconds a cached chat label (``get_chat``) stays valid (patched by the tests).
CHAT_TTL = 60.0

#: Seconds a cached ``st:yes`` verdict stays valid (N1b; the tests patch it).
FRESH_TTL = 3.0

#: ``cache key -> (monotonic expiry moment, value)``; ``None`` = cached failure.
_entries: dict[Any, tuple[float, Any]] = {}


def clear() -> None:
    """Drop every cached answer (the per-test reset of the suite)."""
    _entries.clear()


def _remember(key: Any, value: Any, ttl: float | None = None) -> None:
    """Store ``value`` (``None`` included) under ``key`` for one TTL window.

    ``ttl`` defaults to the module ``TTL`` — read at CALL time, so the
    tests can patch it — while the chat labels and the confirm verdicts
    pass their own windows (``CHAT_TTL`` / ``FRESH_TTL``).
    """
    window = TTL if ttl is None else ttl
    _entries[key] = (time.monotonic() + window, value)


def _recall(key: Any) -> tuple[bool, Any]:
    """``(True, value)`` of a live entry, else ``(False, None)``.

    An expired entry is dropped on sight, so a stale answer can never
    outlive its TTL window even when nobody else touches the cache.
    """
    entry = _entries.get(key)
    if entry is None:
        return False, None
    expires_at, value = entry
    if time.monotonic() >= expires_at:
        _entries.pop(key, None)
        return False, None
    return True, value


async def get_chat_administrators(bot: Any, chat_ref: Any) -> list[Any] | None:
    """Administrators of ``chat_ref``, cached — or ``None`` when the API fails.

    Args:
        bot: Bot whose ``get_chat_administrators`` is the API source.
        chat_ref: Chat to ask about (int id or ``@username``).

    Returns:
        The cached/administrator list (an EMPTY list is a success and
        is served as-is), or ``None`` when the call raised — the
        failure is cached too, so a retry inside the TTL window never
        reaches the API again. The exception itself never escapes.
    """
    key = ("admins", str(chat_ref))
    hit, value = _recall(key)
    if hit:
        return value
    try:
        value = list(await bot.get_chat_administrators(chat_ref))
    except Exception:
        value = None
    _remember(key, value)
    return value


async def get_chat_member(bot: Any, chat_id: Any, user_id: Any) -> Any | None:
    """Chat member ``user_id`` of ``chat_id``, cached — ``None`` on failure.

    Args:
        bot: Bot whose ``get_chat_member`` is the API source.
        chat_id: Chat the user was seen in.
        user_id: Telegram user to look up.

    Returns:
        The cached member object (its ``status`` is the caller's
        business), or ``None`` when the call raised — the failure is
        negatively cached as well, so one dead lookup answers every
        repeat of the TTL window without touching the API again.
    """
    key = ("member", chat_id, user_id)
    hit, value = _recall(key)
    if hit:
        return value
    try:
        value = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
    except Exception:
        value = None
    _remember(key, value)
    return value


async def get_chat(bot: Any, chat_ref: Any) -> Any:
    """Chat ``chat_ref``, cached — the wrapper every display label uses (N1a).

    Args:
        bot: Bot whose ``get_chat`` is the API source.
        chat_ref: Chat to ask about (int id or ``@username``).

    Returns:
        The chat object served from the cache or freshly fetched.

    Raises:
        Exception: whatever ``bot.get_chat`` raised — the FAILURE is
            cached as well (the negative answer), so the retry inside
            the ``CHAT_TTL`` window re-raises it without touching the
            API again; ``bot.keyboards.chat_title`` catches it and
            falls back to ``str(chat_ref)``, exactly like on a fresh
            call.
    """
    key = ("chat", str(chat_ref))
    hit, value = _recall(key)
    if hit:
        if isinstance(value, BaseException):
            raise value
        return value
    try:
        value = await bot.get_chat(chat_ref)
    except Exception as error:
        _remember(key, error, CHAT_TTL)
        raise
    _remember(key, value, CHAT_TTL)
    return value


def remember_confirm_verdict(caller_id: Any, source: Any, target: Any, verdict: Any) -> None:
    """Cache the ``st:yes`` verdict of this caller and pair (N1b).

    Args:
        caller_id: The user who pressed the confirm button — part of
            the key so no other caller may ever ride their verdict.
        source: Source ref of the confirmed pair.
        target: Target ref of the confirmed pair.
        verdict: The final answer of the recheck pair — ``(ok,
            refusal_key)`` where ``ok`` is the combined bool (the FIRST
            denying step makes it ``False``) and ``refusal_key`` names
            its alert (``""`` while ``ok``).

    The entry expires after ``FRESH_TTL`` seconds and is dropped by
    ``clear()`` — both are this module's contract (the tests pin them).
    """
    _remember((caller_id, str(source), str(target)), verdict, FRESH_TTL)


def recall_confirm_verdict(caller_id: Any, source: Any, target: Any) -> tuple[bool, Any]:
    """The cached ``st:yes`` verdict of this caller and pair, or a miss (N1b).

    Args:
        caller_id: The user who pressed the confirm button.
        source: Source ref of the confirmed pair.
        target: Target ref of the confirmed pair.

    Returns:
        ``(True, verdict)`` while the entry is live, ``(False, None)``
        on a miss — an expired verdict is dropped on sight, so
        ``FRESH_TTL = 0`` makes the very next click fresh again.
    """
    return _recall((caller_id, str(source), str(target)))

"""Source→target chat registry backed by the ``pairs`` table (cycle A).

The pairs used to live in a ``chats.json`` file re-read on every
lookup. Since cycle A the DATABASE is the source of truth
(``bot.models.Pair``) and this module keeps the pairs IN MEMORY as a
cache, so the read path stays SYNCHRONOUS — the watcher and the
buffering router ask it without awaiting — while the write path is
async:

- ``await chats.refresh()`` re-SELECTs every pair of the table into
  the cache; until it returns the cache stays exactly as it was;
- ``await chats.add_pair(source, target)`` INSERTs a pair and
  refreshes the cache on success (``True``). A repeated pair is
  refused with ``False`` by the ``UNIQUE (source_ref, target_ref)``
  constraint (``IntegrityError`` → rollback, exactly one row stays),
  and a self-referential pair is refused with ``False`` BEFORE any
  insert by the form-independent comparison ``is_same_chat`` (``@MyChat``
  ≡ ``@mychat``, ``-100666`` ≡ ``"-100666"`` — security review M4 +
  F4), so the table never holds it and no lookup can ever see it;
- ``await chats.remove_pair(pair_id)`` DELETEs an existing pair and
  refreshes (``True``); an id the table never held returns ``False``;
- ``await parse_chat_ref(text)`` strictly parses the admin's answer to
  the source-chat question of BRIEF step 2 — the extracted interior
  of the old ``pair_for_source_ref`` (cycle B);
- cycle B pins two public helpers beside them: ``is_same_chat(a, b)``
  (the form-independent comparison, public) and ``origin_chat_ref(origin)``
  (the chat ref a forward origin shows, ``None`` when it shows no chat).

Refs are stored in the TEXT columns in their recorded form and
canonicalised when the cache is reloaded: a numeric ref comes back as
an ``int``, a ``@username`` verbatim (case kept), so
``target_for(-100111) == -100222`` holds exactly like it did with the
JSON file. Every lookup compares through ``_normalize_ref`` — the
form-independent machinery the registry was already built on.
"""

import asyncio
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from bot.database import session
from bot.models import Pair

#: A public chat reference: ``@`` plus the Telegram username format.
USERNAME_REF_RE = re.compile(r"@[A-Za-z0-9_]{1,64}\Z")

#: A numeric chat id reference: optional minus and digits only (``-100…``).
ID_REF_RE = re.compile(r"-?[0-9]+\Z")


def _normalize_ref(chat_ref: Any) -> Any:
    """Comparable registry key of ``chat_ref``, independent of its FORM.

    ``-100666`` and ``"-100666"`` name the same chat, and Telegram
    usernames are case-insensitive (``@MyChat`` ≡ ``@mychat``); every
    other value keeps its raw stringified form (F4: a raw ``==`` is not
    enough to recognise the same chat written in two forms).
    """
    if isinstance(chat_ref, bool):
        return ("raw", repr(chat_ref))
    if isinstance(chat_ref, int):
        return ("id", chat_ref)
    if isinstance(chat_ref, str):
        ref = chat_ref.strip()
        if ref.startswith("@"):
            return ("username", ref[1:].casefold())
        if ID_REF_RE.fullmatch(ref) is not None:
            return ("id", int(ref))
        return ("raw", ref)
    return ("raw", repr(chat_ref))


def is_same_chat(left: Any, right: Any) -> bool:
    """Whether two chat references name the same chat in any recorded form.

    The public form-independent comparison the registry, the settings
    menu's self-pair guard and the load-time self-pair drop share:
    ``@MyChat`` ≡ ``@mychat``, ``-100666`` ≡ ``"-100666"`` (F4).
    """
    return _normalize_ref(left) == _normalize_ref(right)


def _canonical_ref(stored: str) -> Any:
    """Python form of a ref read back from a TEXT column.

    ``"-100222"`` was stored from the int ``-100222`` and must read
    back as that int (the registry keys numeric chats by ``int``);
    every other ref — ``@usernames`` included — stays verbatim.
    """
    ref = stored.strip()
    if ID_REF_RE.fullmatch(ref) is not None:
        return int(ref)
    return ref


def _stored_ref(chat_ref: Any) -> str:
    """TEXT-column form of ``chat_ref`` (ints become their digits, strings are stripped)."""
    return str(chat_ref).strip()


def _chat_refs(chat_ref: Any) -> tuple[Any, ...]:
    """Comparable registry keys of ``chat_ref``.

    A plain ref (``@username`` or an integer id) is its own key; a
    Chat-shaped object contributes BOTH of its forms — the integer id
    and the ``@username`` — so a pair keyed either way matches the same
    chat («любое совпадение id/@username»).
    """
    if isinstance(chat_ref, (str, int)):
        return (chat_ref,)
    refs: list[Any] = []
    chat_id = getattr(chat_ref, "id", None)
    if chat_id is not None:
        refs.append(chat_id)
    username = getattr(chat_ref, "username", None)
    if username:
        refs.append(f"@{username}")
    return tuple(refs)


def _chat_keys(chat_ref: Any) -> set[Any]:
    """Every comparable key of ``chat_ref`` (its integer id and/or its @username)."""
    return {_normalize_ref(ref) for ref in _chat_refs(chat_ref)}


def _pair_view(pair: dict[str, Any]) -> dict[str, Any]:
    """Caller-facing view of a cached pair: ``source`` and ``target`` only.

    The cache also holds the row ``id`` (cycle B menu buttons); every
    pair handed OUT to the watcher/session machinery stays a plain
    two-key dict, so pair equality keeps comparing the refs alone.
    """
    return {"source": pair["source"], "target": pair["target"]}


def _ref_candidates(chat_id: Any, username: Any) -> list[Any]:
    """Chat refs an origin announces: the integer id first, then ``@username``.

    The shared interior of the origin machinery: ``pair_for_origin``
    matches a forward origin against the registry with it, and
    ``origin_chat_ref`` hands the FIRST candidate to the settings menu.
    """
    candidates: list[Any] = []
    if chat_id is not None:
        candidates.append(chat_id)
    if username:
        candidates.append(f"@{username}")
    return candidates


def origin_chat_ref(origin: Any) -> int | str | None:
    """The chat reference a forward origin shows, or ``None``.

    A forward origin carries its chat as ``.chat`` (channel/user/group
    origins) or, when that is absent, as ``.sender_chat`` (a group
    re-share): the integer id wins, the ``@username`` is the fallback.
    An origin with no visible chat — the typical forward-from-a-user
    case — resolves to ``None``, which the settings menu reads as
    «not a chat reference» (cycle B).
    """
    chat = getattr(origin, "chat", None)
    if chat is None:
        chat = getattr(origin, "sender_chat", None)
    if chat is None:
        return None
    candidates = _ref_candidates(getattr(chat, "id", None), getattr(chat, "username", None))
    return candidates[0] if candidates else None


def _parse_ref(text: Any) -> str | int | None:
    """Canonical ref of the admin's answer, or ``None`` when it names no chat.

    A stripped ``@username`` comes back verbatim (case kept), a
    numeric id string (``-100…`` included) as an ``int``; free text,
    an empty/blank answer and a non-string answer all mean «not a
    chat ref» → ``None``. N3: a digit run beyond CPython's
    int-conversion limit (> 4300 digits) makes ``int()`` raise
    ``ValueError`` — that reads as «not a chat ref» too, never as an
    exception out of the waiting flow.
    """
    if not isinstance(text, str):
        return None
    ref = text.strip()
    if not ref:
        return None
    if ref.startswith("@"):
        if USERNAME_REF_RE.fullmatch(ref) is None:
            return None
        return ref
    if ID_REF_RE.fullmatch(ref) is None:
        return None
    try:
        return int(ref)
    except ValueError:
        # N3: an absurdly long digit string is simply «no chat ref» —
        # no handler of the flow may crash on an unconvertible answer.
        return None


async def parse_chat_ref(text: Any) -> str | int | None:
    """The strict parser of the «Which chat did you forward from?» answer.

    BRIEF step 2 asks for ``@username`` or the numeric id of the
    source chat; this is the extracted interior of the old
    ``pair_for_source_ref`` the settings menu of cycle B reuses: a
    valid answer becomes the ref the registry is keyed by, anything
    else resolves to ``None`` («не настроено → СТОП») — N3: a digit
    string past CPython's int-conversion limit does so too instead of
    raising ``ValueError`` out of the parser.
    """
    return _parse_ref(text)


class ChatPairs:
    """Registry of ``source → target`` pairs cached IN MEMORY over the process.

    The pairs live in the ``pairs`` table; the synchronous lookups of
    this class only ever touch the cache, so the watcher and the
    buffering router keep their sync call sites. ``refresh()`` is the
    single bridge from the database into the cache — called at startup
    (``bot.__main__``), by every CRUD helper and by the test fixtures.
    """

    def __init__(self) -> None:
        """Start with an empty cache — ``refresh()`` loads the table."""
        self._pairs: list[dict[str, Any]] = []
        #: Writer locks by event loop (M-2: writes serialize, reads don't).
        self._writers: dict[Any, asyncio.Lock] = {}

    def _write_lock(self) -> asyncio.Lock:
        """The writer lock of the CURRENT event loop, created on first use.

        M-2: ``refresh()``/``add_pair()``/``remove_pair()`` run entirely
        under this lock, so a ``refresh()`` that read the table BEFORE a
        removal can no longer assign its stale rows after the removal
        refreshed (the lost-update window that resurrected a deleted
        pair). Reads never take the lock — the cache is a plain list
        swap.

        The lock is kept PER EVENT LOOP: an ``asyncio`` primitive binds
        to the loop that first waits on it, and this singleton outlives
        the many loops the test suite drives it through — a lock bound
        to a dead loop would raise instead of locking.
        """
        loop = asyncio.get_running_loop()
        lock = self._writers.get(loop)
        if lock is None:
            self._writers.clear()  # one loop runs at a time: drop the dead lock
            lock = asyncio.Lock()
            self._writers[loop] = lock
        return lock

    async def refresh(self) -> None:
        """Re-SELECT every pair of the table into the cache (under the write lock).

        A row written behind the registry's back stays invisible until
        this runs; from then on the cache reflects the table (self-
        pairs are dropped at load too — the same form-independent
        guard as at insert, security review M4). Each cached entry
        carries the row ``id`` (the settings menu keys its delete
        buttons by it) beside the canonical ``source``/``target`` refs.
        """
        async with self._write_lock():
            await self._reload()

    async def _reload(self) -> None:
        """The table → cache copy itself (the caller holds the write lock)."""
        async with session() as db:
            rows = (await db.execute(select(Pair).order_by(Pair.id))).scalars()
            pairs = [
                {
                    "id": row.id,
                    "source": _canonical_ref(row.source_ref),
                    "target": _canonical_ref(row.target_ref),
                }
                for row in rows
                if not is_same_chat(row.source_ref, row.target_ref)
            ]
        self._pairs = pairs

    async def add_pair(self, source: Any, target: Any) -> bool:
        """INSERT ``source → target`` and refresh the cache (``True``).

        Returns:
            ``False`` for a self-referential pair (refused BEFORE any
            insert, in any recorded form) and for a pair the table
            already holds — the ``UNIQUE (source_ref, target_ref)``
            constraint refuses the repeat, the ``IntegrityError`` is
            rolled back and never escapes.
        """
        if is_same_chat(source, target):
            return False
        async with self._write_lock():
            try:
                async with session() as db:
                    db.add(Pair(source_ref=_stored_ref(source), target_ref=_stored_ref(target)))
                    await db.flush()
            except IntegrityError:
                return False
            await self._reload()
            return True

    async def remove_pair(self, pair_id: int) -> bool:
        """DELETE the pair with ``pair_id`` and refresh the cache (``True``).

        Returns:
            ``False`` when the table holds no such id — never inserted
            or already removed — which is not an error.
        """
        async with self._write_lock():
            async with session() as db:
                row = await db.get(Pair, pair_id)
                if row is None:
                    return False
                await db.delete(row)
            await self._reload()
            return True

    def _pair_for_source(self, source: Any) -> dict[str, Any] | None:
        """Cached pair whose source names ``source`` in any form, or ``None``.

        The caller-facing view carries ONLY ``source``/``target`` — the
        row ``id`` stays an internal cache detail (the pair dicts handed
        to the session/watcher compare equal to plain two-key pairs).
        """
        key = _normalize_ref(source)
        for pair in self._pairs:
            if _normalize_ref(pair["source"]) == key:
                return _pair_view(pair)
        return None

    def all_pairs(self) -> list[dict[str, Any]]:
        """Snapshot of the cached pairs — ``id``, ``source``, ``target``.

        Table order (the ``refresh()`` SELECT order), copied so a caller
        can never mutate the cache itself; the synchronous read path the
        settings menu (cycle B) renders its lines, its delete buttons
        and its ACL scan from.
        """
        return [dict(pair) for pair in self._pairs]

    def target_for(self, source: Any) -> Any:
        """Configured target of ``source``, or ``None`` when not configured.

        ``source`` is matched form-independently, so integer chat ids
        and ``@usernames`` both work and the canonical Python form of
        the stored ref comes back (``-100111`` → ``-100222``).
        """
        pair = self._pair_for_source(source)
        return pair["target"] if pair is not None else None


#: Shared singleton the handlers ask.
chats = ChatPairs()


def pair_targets_chat(pair: dict[str, Any], chat_ref: Any) -> bool:
    """Whether ``pair['target']`` names ``chat_ref``, in any form (F4).

    The registry may hold several pairs; a pair the bot resolves (from
    a forward origin or from the admin's «which chat?» answer) only
    counts for the threaded chat the flow actually runs in — a pair
    targeting another chat is «не настроено» here.
    """
    return _normalize_ref(pair.get("target")) in _chat_keys(chat_ref)


def pair_is_registered(pair: dict[str, Any]) -> bool:
    """Whether the exact ``pair`` still exists in the registry (cached state, F4).

    ``session.source`` may go stale while a batch is pending: the pair
    can be removed in the meantime. Both halves are compared
    form-independently, like the load-time self-pair drop.
    """
    source = _normalize_ref(pair.get("source"))
    target = _normalize_ref(pair.get("target"))
    return any(
        _normalize_ref(loaded["source"]) == source
        and _normalize_ref(loaded["target"]) == target
        for loaded in chats._pairs
    )


def pair_for_source_ref(text: Any) -> dict[str, Any] | None:
    """The pair the admin's «which chat did you forward from?» names.

    ``text`` must be a stripped ``@username`` or a numeric chat id
    string (including ``-100…``) naming a CONFIGURED source; anything
    else — free text, an unconfigured ref — resolves to ``None``
    (BRIEF step 2: «не настроено → СТОП»).
    """
    value = _parse_ref(text)
    if value is None:
        return None
    return chats._pair_for_source(value)


def is_configured_target(chat_ref: Any) -> bool:
    """Whether ``chat_ref`` is the ``target`` of any configured pair.

    This is the watcher forward filter (BRIEF §2: the bot works only in
    configured threaded chats); source-only chats and strangers are
    refused. The lookup consults the in-memory cache like every other
    lookup of this module.
    """
    keys = _chat_keys(chat_ref)
    return any(_normalize_ref(pair["target"]) in keys for pair in chats._pairs)


def is_source(chat_ref: Any) -> bool:
    """Whether ``chat_ref`` is the ``source`` of any configured pair.

    This is the buffering filter: only the source (main) chat is
    recorded — it is the base for finding originals in step 5.
    Threaded-only chats and strangers are never buffered.
    """
    keys = _chat_keys(chat_ref)
    return any(_normalize_ref(pair["source"]) in keys for pair in chats._pairs)


def pair_for_origin(origin: dict[str, Any], chat_ref: Any = None) -> dict[str, Any] | None:
    """The pair a forward origin identifies, or ``None`` (BRIEF step 2).

    The origin carries the origin chat as ``chat_id`` and/or
    ``username``; ANY of them matching a pair's source identifies the
    pair («forward_origin содержит чат и пара настроена → молча идём
    дальше»). An origin without a chat (a user forward) resolves to
    ``None`` — the bot asks the «which chat?» question instead.

    When ``chat_ref`` is given — the threaded chat the batch arrived
    in — a pair counts only when its ``target`` IS that chat (F4,
    form-independent): a pair targeting another threaded chat does not
    identify THIS chat's source pair, so the lookup keeps scanning and
    resolves to ``None`` when nothing else matches.
    """
    candidates = _ref_candidates(origin.get("chat_id"), origin.get("username"))
    if not candidates:
        return None
    keys = {_normalize_ref(candidate) for candidate in candidates}
    for pair in chats._pairs:
        if _normalize_ref(pair["source"]) not in keys:
            continue
        if chat_ref is not None and not pair_targets_chat(pair, chat_ref):
            continue
        return _pair_view(pair)
    return None

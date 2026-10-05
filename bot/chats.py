"""Source→target chat registry persisted as a JSON file.

File format (BRIEF section 5.5)::

    {"pairs": [{"source": "@src", "target": "@tgt"},
               {"source": -100111, "target": -100222}]}

Both public ``@usernames`` and private integer chat ids are allowed as
keys. A missing, broken or malformed file is NOT an error: it reads as
an empty registry. Every lookup reads the file anew, so a config
written after bot startup is picked up without a restart.

Besides ``target_for(source)`` the module exposes the lookups the v3
flow needs: ``pair_for_source_ref`` parses the admin's answer to
«Which chat did you forward from?», ``is_configured_target`` /
``is_source`` filter the watcher and the buffering routers, and
``pair_for_origin`` matches a forward origin against the pairs
(BRIEF step 2: an origin whose chat is configured lets the bot skip
the question — optionally bound to the threaded chat the batch
arrived in, see F4). ``pair_targets_chat`` / ``pair_is_registered``
back the F4 re-validation of the pair fixed on a pending session.
"""

import json
import re
from pathlib import Path
from typing import Any

#: Default registry file: ``chats.json`` in the current working directory.
DEFAULT_PATH = "chats.json"

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


def _same_ref(left: Any, right: Any) -> bool:
    """Whether two chat references name the same chat in any recorded form."""
    return _normalize_ref(left) == _normalize_ref(right)


class ChatPairs:
    """Registry of ``source → target`` pairs read fresh from ``path``."""

    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        """Read pairs from ``path`` (relative paths follow the cwd)."""
        self.path = Path(path)

    def _read_pairs(self) -> list[dict[str, Any]]:
        """Read the file anew; anything unreadable/malformed reads as no pairs."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(data, dict):
            return []
        pairs = data.get("pairs")
        if not isinstance(pairs, list):
            return []
        return [
            pair
            for pair in pairs
            if isinstance(pair, dict)
            and "source" in pair
            and "target" in pair
            # A self-referential pair (source == target, in either recorded
            # form: int id vs its numeric string, username letter-case) would
            # make a source chat its own target: the bot would build a thread
            # inside the very chat it moves messages out of, and both filters
            # would watch the chat on both ends. Dropped at load by a
            # form-independent comparison (security review M4 + F4), so no
            # lookup sees it, while valid pairs beside it keep working.
            and not _same_ref(pair["source"], pair["target"])
        ]

    def target_for(self, source: Any) -> Any:
        """Configured target of ``source``, or ``None`` when not configured.

        ``source`` is matched verbatim, so integer chat ids and
        ``@usernames`` both work; malformed entries next to a valid pair
        do not hide it.
        """
        for pair in self._read_pairs():
            if pair["source"] == source:
                return pair["target"]
        return None


#: Shared singleton the handlers ask.
chats = ChatPairs()


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


def pair_targets_chat(pair: dict[str, Any], chat_ref: Any) -> bool:
    """Whether ``pair['target']`` names ``chat_ref``, in any form (F4).

    The registry may hold several pairs; a pair the bot resolves (from
    a forward origin or from the admin's «which chat?» answer) only
    counts for the threaded chat the flow actually runs in — a pair
    targeting another chat is «не настроено» here.
    """
    return _normalize_ref(pair.get("target")) in _chat_keys(chat_ref)


def pair_is_registered(pair: dict[str, Any]) -> bool:
    """Whether the exact ``pair`` still exists in the registry (fresh read, F4).

    ``session.source`` may go stale while a batch is pending: the config
    can be rewritten and the pair dropped. Both halves are compared
    form-independently, like the load-time self-pair drop.
    """
    source = _normalize_ref(pair.get("source"))
    target = _normalize_ref(pair.get("target"))
    return any(
        _normalize_ref(loaded["source"]) == source
        and _normalize_ref(loaded["target"]) == target
        for loaded in chats._read_pairs()
    )


def pair_for_source_ref(text: Any) -> dict[str, Any] | None:
    """The pair the admin's «which chat did you forward from?» names.

    ``text`` must be a stripped ``@username`` or a numeric chat id
    string (including ``-100…``); anything else — free text, an
    unconfigured ref, a broken config — resolves to ``None`` (BRIEF
    step 2: «не настроено → СТОП»).
    """
    if not isinstance(text, str):
        return None
    ref = text.strip()
    if not ref:
        return None
    if ref.startswith("@"):
        if USERNAME_REF_RE.fullmatch(ref) is None:
            return None
        value: Any = ref
    else:
        if ID_REF_RE.fullmatch(ref) is None:
            return None
        value = int(ref)
    for pair in chats._read_pairs():
        if pair["source"] == value:
            return pair
    return None


def is_configured_target(chat_ref: Any) -> bool:
    """Whether ``chat_ref`` is the ``target`` of any configured pair.

    This is the watcher forward filter (BRIEF §2: the bot works only in
    configured threaded chats); source-only chats and strangers are
    refused. The lookup reads the registry anew, like every other
    lookup of this module.
    """
    refs = _chat_refs(chat_ref)
    return any(pair["target"] in refs for pair in chats._read_pairs())


def is_source(chat_ref: Any) -> bool:
    """Whether ``chat_ref`` is the ``source`` of any configured pair.

    This is the buffering filter: only the source (main) chat is
    recorded — it is the base for finding originals in step 5.
    Threaded-only chats and strangers are never buffered.
    """
    refs = _chat_refs(chat_ref)
    return any(pair["source"] in refs for pair in chats._read_pairs())


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
    candidates: list[Any] = []
    chat_id = origin.get("chat_id")
    if chat_id is not None:
        candidates.append(chat_id)
    username = origin.get("username")
    if username:
        candidates.append(f"@{username}")
    if not candidates:
        return None
    for pair in chats._read_pairs():
        if pair["source"] not in candidates:
            continue
        if chat_ref is not None and not pair_targets_chat(pair, chat_ref):
            continue
        return pair
    return None

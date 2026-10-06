"""Locale packs for the bot's replies: ``t`` and ``set_locale`` (backlog 5).

Every reply string lives in a JSON pack ``{locale}.json`` under
``bot/locales`` — the code only ASKS for a value by its key, so shipping
a new locale means adding a file, not editing handlers. The lookup order
of ``t(key)``: the CURRENT pack, then the ``en`` fallback pack of the
same directory, then ``KeyError(key)`` — a missing reply is an error,
never a blank.

The current locale lives in module state (the style of the ``buffer`` /
``chats`` singletons): ``set_locale`` switches it, and the first ``t()``
without an explicit switch lazily loads the default pack ``en``. With
``directory=None`` the pack directory resolves RELATIVE TO THIS PACKAGE
(``bot/locales``) — never to the current working directory, so a bot
started from anywhere reads the same packs.
"""

import json
import re
from pathlib import Path

#: Pack directory of ``set_locale(locale, directory=None)``: next to THIS module.
DEFAULT_DIRECTORY = Path(__file__).resolve().parent / "locales"

#: Locale served until the first ``set_locale`` call.
DEFAULT_LOCALE = "en"

#: Locale every key falls back to before ``KeyError`` is raised.
FALLBACK_LOCALE = "en"

#: A locale NAME that may safely become a file name (I-1): letters,
#: digits, ``_`` and ``-`` only — anything else (``..``, ``/``, spaces,
#: an empty string) is a traversal/blank attempt and is refused BEFORE
#: the filesystem is touched.
LOCALE_NAME_RE = re.compile(r"[A-Za-z0-9_-]+\Z")

#: Directory of the current pack (package-relative until ``set_locale`` says otherwise).
_directory: Path = DEFAULT_DIRECTORY

#: Name of the current locale pack.
_locale: str = DEFAULT_LOCALE

#: Current pack, ``None`` until the first ``t()`` or ``set_locale()`` reads it.
_pack: dict[str, str] | None = None

#: ``en`` fallback of ``_directory``, ``None`` until a key miss needs it.
_fallback: dict[str, str] | None = None


def _read_pack(directory: Path, locale: str) -> dict[str, str]:
    """Read ``{locale}.json`` from ``directory`` afresh (UTF-8).

    Raises:
        FileNotFoundError: The locale file does not exist; the message
            names the missing path.
    """
    path = directory / f"{locale}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def set_locale(locale: str, directory: str | Path | None = None) -> None:
    """Switch the current locale, re-reading its pack on EVERY call.

    Args:
        locale: Pack to serve — the file ``{locale}.json`` of ``directory``.
        directory: Pack directory. ``None`` resolves the package-relative
            ``bot/locales``; the current working directory never matters.

    Raises:
        ValueError: The NAME is not a plain pack name (I-1) — anything
            outside ``[A-Za-z0-9_-]+``, an empty string included. The
            message names the refused value, and the check runs BEFORE
            the filesystem, so ``../evil`` never becomes a path.
        FileNotFoundError: A well-formed name whose ``{locale}.json`` is
            absent — raised BEFORE any state changes, so a failed switch
            keeps the previous locale.
    """
    global _directory, _locale, _pack, _fallback
    if not isinstance(locale, str) or LOCALE_NAME_RE.fullmatch(locale) is None:
        raise ValueError(f"invalid locale name: {locale!r}")
    target = DEFAULT_DIRECTORY if directory is None else Path(directory)
    pack = _read_pack(target, locale)
    _directory, _locale, _pack, _fallback = target, locale, pack, None


def _current_pack() -> dict[str, str]:
    """The current pack, loaded lazily (the default ``en`` on first use)."""
    global _pack
    if _pack is None:
        _pack = _read_pack(_directory, _locale)
    return _pack


def _fallback_pack() -> dict[str, str]:
    """The ``en`` pack of the current directory; a missing file reads as empty."""
    global _fallback
    if _fallback is None:
        if _locale == FALLBACK_LOCALE:
            _fallback = _current_pack()
        else:
            try:
                _fallback = _read_pack(_directory, FALLBACK_LOCALE)
            except FileNotFoundError:
                # The fallback is best effort: without an ``en`` pack the
                # chain simply ends in ``KeyError`` below.
                _fallback = {}
    return _fallback


def t(key: str, **fmt: object) -> str:
    """Render the reply ``key`` of the current locale pack via ``str.format``.

    The pack is consulted at CALL time, so a locale switched mid-flight
    is what the next answer is rendered from — no reply is precomputed.

    Args:
        key: Pack key, e.g. ``watcher.cancelled``.
        **fmt: Placeholder values handed to ``str.format``.

    Returns:
        The formatted value from the current pack, else from the ``en``
        fallback pack.

    Raises:
        KeyError: The key is missing from BOTH packs — or ``fmt`` lacks a
            placeholder the value needs (``str.format``'s own ``KeyError``).
    """
    value = _current_pack().get(key)
    if value is None:
        value = _fallback_pack().get(key)
        if value is None:
            raise KeyError(key)
    return value.format(**fmt)

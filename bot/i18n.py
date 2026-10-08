"""gettext-based replies: ``translate`` (used as ``_``) and ``set_locale``.

The reply strings are NATURAL English literals in the code — the
standard gettext idiom the PR review asked for (keys per reply are a
burden to manage, JSON packs are inconvenient for translators):

    from bot.i18n import translate as _
    ...
    _("Thread title? Send the title as a plain message.")

Translated copies live in gettext catalogs under ``bot/locales``:

    bot/locales/messages.pot                      # template (pybabel extract)
    bot/locales/<locale>/LC_MESSAGES/messages.po  # translator's file
    bot/locales/<locale>/LC_MESSAGES/messages.mo  # compiled, loaded at runtime

``set_locale`` loads the catalog through stdlib
``gettext.translation`` with ``fallback=True``: a locale without a
catalog (an untranslated language) serves the msgid itself — English —
never an exception. The locale NAME is still validated BEFORE the
filesystem (I-1): traversal / blank names raise ``ValueError``.

The current translation lives in module state (the style of the
``buffer`` / ``chats`` singletons): ``set_locale`` switches it, and the
first ``translate()`` without an explicit switch lazily adopts the
default locale ``en`` (source language — no catalog needed). With
``directory=None`` the catalog root resolves RELATIVE TO THIS PACKAGE
(``bot/locales``) — never to the current working directory, so a bot
started from anywhere reads the same catalogs.

Tooling (Babel is a dev dependency): ``pybabel extract -F babel.cfg
-o bot/locales/messages.pot .`` refreshes the template, ``pybabel
update``/``init`` maintain the per-locale ``.po`` files, and
``pybabel compile -d bot/locales`` writes the ``.mo`` files the
runtime loads. ``tests/test_i18n.py`` pins both drift directions and
the ``.po`` → ``.mo`` compile chain.
"""

import re
from gettext import NullTranslations
from gettext import translation as _load_translation
from pathlib import Path

#: Catalog root of ``set_locale(locale, directory=None)``: next to THIS module.
DEFAULT_LOCALEDIR = Path(__file__).resolve().parent / "locales"

#: gettext domain — the ``messages`` of ``<locale>/LC_MESSAGES/messages.*``.
DOMAIN = "messages"

#: Locale served until the first ``set_locale`` call (the source language).
DEFAULT_LOCALE = "en"

#: A locale NAME that may safely become a directory name (I-1): letters,
#: digits, ``_`` and ``-`` only — anything else (``..``, ``/``, spaces,
#: an empty string) is a traversal/blank attempt and is refused BEFORE
#: the filesystem is touched.
LOCALE_NAME_RE = re.compile(r"[A-Za-z0-9_-]+\Z")

#: Loaded translation of the current locale, ``None`` until first use.
_translation: NullTranslations | None = None

#: Name the current translation was loaded for (informational).
_locale: str = DEFAULT_LOCALE


def set_locale(locale: str, directory: str | Path | None = None) -> None:
    """Switch the current locale, resolving its catalog on EVERY call.

    Stdlib note: ``gettext.translation`` caches parsed ``.mo`` catalogs
    per path for the process lifetime — switching a DIFFERENT locale or
    directory always loads afresh, while editing a ``.mo`` on disk under
    a live locale is not observed (the bot switches once, at startup).

    Args:
        locale: Catalog to serve — ``<directory>/<locale>/LC_MESSAGES/messages.mo``.
            A well-formed name WITHOUT a catalog is fine: ``fallback=True``
            switches to the English msgid passthrough (gettext standard).
        directory: Catalog root. ``None`` resolves the package-relative
            ``bot/locales``; the current working directory never matters.

    Raises:
        ValueError: The NAME is not a plain locale name (I-1) — anything
            outside ``[A-Za-z0-9_-]+``, an empty string included. The
            message names the refused value, and the check runs BEFORE
            the filesystem, so ``../evil`` never becomes a path.
    """
    global _translation, _locale
    if not isinstance(locale, str) or LOCALE_NAME_RE.fullmatch(locale) is None:
        raise ValueError(f"invalid locale name: {locale!r}")
    localedir = DEFAULT_LOCALEDIR if directory is None else Path(directory)
    _translation = _load_translation(
        DOMAIN,
        localedir=str(localedir),
        languages=[locale],
        fallback=True,
    )
    _locale = locale


def translate(message: str) -> str:
    """Look the message up in the current translation — gettext semantics.

    Call sites import it under the conventional name ``_`` (the review's
    example): ``from bot.i18n import translate as _``, then format at the
    call site — ``_("... {n} ...").format(n=size)``.

    The catalog is consulted at CALL time, so a locale switched
    mid-flight is what the next answer is rendered from — no reply is
    precomputed. A message the catalog does not translate comes back as
    itself (the English msgid), which is also the ``en`` default: the
    source language needs no catalog at all.
    """
    global _translation
    if _translation is None:
        # Lazy default: locale `en` of the package-relative root. With no
        # `en` catalog on disk `fallback=True` yields the identity
        # translation — the msgid (English) passes through.
        _translation = _load_translation(
            DOMAIN,
            localedir=str(DEFAULT_LOCALEDIR),
            languages=[DEFAULT_LOCALE],
            fallback=True,
        )
    return _translation.gettext(message)

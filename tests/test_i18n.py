"""i18n tests: gettext catalogs and the ``bot.i18n`` module (PR #2 review).

RED phase: ``bot/i18n.py`` still renders the JSON key pack ``en.json``
and ``bot/locales/messages.pot`` does not exist yet — so this file
fails on several fronts: ``FileNotFoundError`` for the template,
``ImportError`` for the gettext API (``translate as _``), asserts for
the surviving key-style ``t("...")`` calls and for the JSON pack that
must be gone. The spec pins the rework requested by the PR review
(comment on ``bot/locales/en.json``: keys are a burden, JSON is bad
for translators — use the standard gettext ecosystem, Babel for the
tooling):

- ``bot/locales/messages.pot`` carries EXACTLY the 30 reply strings of
  the former pack as byte-exact msgids — the template IS the
  specification — and stays in BOTH-way sync with the code: every
  ``_("literal")`` of ``bot/**/*.py`` is extracted into it, and no
  stale msgid survives in it (the drift check of the old key packs);
- replies are natural strings in the code (``from bot.i18n import
  translate as _`` + ``_("...")``): NO dotted-key ``t("...")`` call
  and NO JSON pack under ``bot/locales`` survives the migration;
- ``set_locale(locale, directory=None)`` loads
  ``<directory>/<locale>/LC_MESSAGES/messages.mo`` through stdlib
  ``gettext.translation`` with ``fallback=True``: a well-formed but
  untranslated locale serves the msgid (English) — silent fallback is
  the gettext standard, never an exception; the NAME is still
  validated FIRST (I-1): traversal / blank → ``ValueError`` naming
  the value, BEFORE the filesystem;
- ``directory=None`` resolves ``bot/locales`` RELATIVE TO THE PACKAGE
  (never the CWD), and the current translation is consulted at CALL
  time — a switch mid-flight is what the next ``_()`` renders from;
- formatting happens AT THE CALL SITE via ``str.format`` (the review
  example): a missing placeholder argument stays the natural
  ``KeyError``;
- the toolchain compiles end to end: a ``.po`` seeded into a tmp
  localedir and compiled to ``messages.mo`` with Babel is what
  ``set_locale`` serves — the machinery the first real translation
  will use.

``bot.i18n`` is imported INSIDE the test functions on purpose: a
module-level import would kill the whole file at collection and hide
the per-test failure modes.
"""

import ast
from pathlib import Path

import pytest

#: Repository root, the scanned package and the catalog area (tests/ sits next to bot/).
ROOT = Path(__file__).resolve().parent.parent
BOT_DIR = ROOT / "bot"
LOCALES_DIR = BOT_DIR / "locales"
TEMPLATE_PATH = LOCALES_DIR / "messages.pot"

#: The replies exactly as pinned by the spec: 30 byte-exact msgids.
PACK_SPEC = {
    "watcher.source_question": (
        "Which chat did you forward from? Reply with @username or its id."
    ),
    "watcher.title_question": "Thread title? Send the title as a plain message.",
    "watcher.not_configured": "This chat is not configured as a source chat.",
    "watcher.empty_title": "Title is empty — send the thread title.",
    "watcher.cancelled": "Cancelled.",
    "watcher.batch_too_large": (
        "Batch too large ({n} messages, limit 100). Nothing was moved."
    ),
    "watcher.invalid_target": (
        "Invalid target chat in configuration: {target}. Nothing was moved."
    ),
    "watcher.move_failed": "Move failed: {error}. Check the target chat manually.",
    "watcher.created_line": "Thread created: {n} message(s). {thread_url}",
    "watcher.deleted_line": "Deleted {x} of {y} original messages.",
    "thread.header_template": "Topic: <b>{title}</b>",
    "thread.link_line": (
        'Please use <a href="{thread_url}">this link</a> to respond to this thread.'
    ),
    "settings.menu_title": "Pair settings ({n}):",
    "settings.no_pairs": "No pairs configured yet.",
    "settings.forbidden": "You are not allowed to manage settings.",
    "settings.add_button": "Add pair",
    "settings.delete_button": "Delete pair",
    "settings.back_button": "Back",
    "settings.cancel_button": "Cancel",
    "settings.confirm_button": "Confirm",
    "settings.send_source": (
        "Send a message forwarded from the source chat, or its @username or id."
    ),
    "settings.send_target": (
        "Now do the same for the target chat: forward a message or send @username or id."
    ),
    "settings.invalid_input": (
        "Not a chat reference. Send a forwarded message, @username or numeric id."
    ),
    "settings.self_pair": "Source and target are the same chat.",
    "settings.bot_not_admin": (
        "I must be an admin with the Delete Messages permission in both chats of a pair."
    ),
    "settings.duplicate_pair": "This pair already exists.",
    "settings.pair_added": "Pair added: {source} → {target}.",
    "settings.pair_removed": "Pair removed.",
    "settings.confirm_prompt": "Add pair: {source} → {target}?",
    "settings.delete_prompt": "Select a pair to remove:",
}


def _template_msgids() -> set[str]:
    """Msgids of ``bot/locales/messages.pot`` via Babel — FileNotFoundError while RED.

    The header entry (``msgid ""``) is not a reply and is dropped.
    """
    from babel.messages.pofile import read_po

    with open(TEMPLATE_PATH, encoding="utf-8") as file:
        catalog = read_po(file)
    return {message.id for message in catalog if message.id}


def _seed_catalog(directory: Path, locale: str, mapping: dict[str, str]) -> Path:
    """Write ``<locale>/LC_MESSAGES/messages.{po,mo}`` into ``directory`` (Babel)."""
    from babel.messages.catalog import Catalog
    from babel.messages.mofile import write_mo
    from babel.messages.pofile import write_po

    lc_dir = directory / locale / "LC_MESSAGES"
    lc_dir.mkdir(parents=True, exist_ok=True)
    catalog = Catalog(locale=locale)
    for msgid, msgstr in mapping.items():
        catalog.add(msgid, string=msgstr)
    with open(lc_dir / "messages.po", "wb") as file:
        write_po(file, catalog)
    with open(lc_dir / "messages.mo", "wb") as file:
        write_mo(file, catalog)
    return lc_dir


def _bot_python_files() -> list[Path]:
    """Every ``.py`` file of the ``bot`` package (the catalogs live elsewhere)."""
    return sorted(path for path in BOT_DIR.rglob("*.py") if LOCALES_DIR not in path.parents)


def _gettext_literal_strings(files: list[Path]) -> list[str]:
    """First-argument literals of every ``_("...")`` / ``translate("...")`` call."""
    msgids: list[str] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name: str | None = None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name not in {"_", "translate"}:
                continue
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                msgids.append(first.value)
    return msgids


def _key_style_calls(files: list[Path]) -> list[str]:
    """Keys of every ``t("literal")`` / ``i18n.t("literal")`` call — must vanish."""
    keys: list[str] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id == "t":
                pass
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "t":
                pass
            else:
                continue
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                keys.append(first.value)
    return keys


class TestCatalogSpecification:
    """The file ``bot/locales/messages.pot`` IS the specification of the replies."""

    def test_template_exists_and_pins_exactly_the_30_reply_strings(self):
        """30 msgids, byte-exact: the very strings the bot sends (old pack values)."""
        msgids = _template_msgids()

        assert len(PACK_SPEC) == 30, "spec sanity: the spec pins exactly 30 replies"
        assert msgids == set(PACK_SPEC.values()), (
            "messages.pot must carry exactly the 30 pinned reply strings as msgids "
            "(run `pybabel extract -F babel.cfg -o bot/locales/messages.pot .`)"
        )

    def test_no_json_pack_survives_under_bot_locales(self):
        """JSON key packs are gone — gettext catalogs are the only format here."""
        leftovers = sorted(path.name for path in LOCALES_DIR.glob("*.json"))

        assert not leftovers, f"JSON packs must not survive the gettext migration: {leftovers}"


class TestExtractionDrift:
    """Both directions of the extraction, read out of the AST (pin)."""

    def test_every_gettext_literal_in_the_code_is_extracted_into_the_template(self):
        """(a) every ``_("literal")`` of ``bot/**/*.py`` is a msgid of the template."""
        msgids = _template_msgids()
        used = _gettext_literal_strings(_bot_python_files())
        missing = sorted(set(used) - msgids)

        assert not missing, (
            f"reply strings used in the code but absent from messages.pot — "
            f"re-run pybabel extract: {missing}"
        )

    def test_no_stale_msgid_survives_in_the_template(self):
        """(b) every template msgid still exists in the code — no stale entries."""
        msgids = _template_msgids()
        used = _gettext_literal_strings(_bot_python_files())
        stale = sorted(msgids - set(used))

        assert not stale, (
            f"msgids in messages.pot that no call site uses — re-run pybabel extract: {stale}"
        )

    def test_no_key_style_calls_survive_in_the_bot_sources(self):
        """Natural strings only: the dotted-key ``t("settings.x")`` style must vanish."""
        keys = _key_style_calls(_bot_python_files())

        assert not keys, (
            f"key-style t(...) calls must be migrated to natural strings: {keys}"
        )


class TestTranslation:
    """The gettext runtime: natural strings, call-site formatting, fallback."""

    def test_default_locale_serves_the_natural_string_without_explicit_set_locale(self):
        """The first ``_()`` runs WITHOUT ``set_locale``: English msgid passthrough."""
        from bot.i18n import translate as _

        assert _("Cancelled.") == "Cancelled."

    def test_formatting_happens_at_the_call_site(self):
        """The batch-cap refusal embeds the REAL size via ``str.format`` (review example)."""
        from bot.i18n import translate as _

        expected = "Batch too large (101 messages, limit 100). Nothing was moved."
        assert _("Batch too large ({n} messages, limit 100). Nothing was moved.").format(
            n=101
        ) == expected

    def test_missing_placeholder_argument_raises_key_error(self):
        """No ``n`` passed → the natural ``KeyError`` of ``str.format``."""
        from bot.i18n import translate as _

        with pytest.raises(KeyError):
            _("Batch too large ({n} messages, limit 100). Nothing was moved.").format()

    def test_well_formed_unknown_locale_serves_english_silently(self, tmp_path):
        """A well-formed name without a catalog: ``fallback=True`` — English, no exception.

        The gettext standard (the review's example): the NAME is still
        validated first (I-1), but a missing CATALOG never aborts a switch.
        """
        from bot.i18n import set_locale
        from bot.i18n import translate as _

        set_locale("no-such-locale", directory=tmp_path)

        assert _("Cancelled.") == "Cancelled."

    def test_a_seeded_catalog_is_served_after_set_locale(self, tmp_path):
        """A compiled ``xx`` catalog replaces the served strings wholesale."""
        from bot.i18n import set_locale
        from bot.i18n import translate as _

        _seed_catalog(
            tmp_path,
            "xx",
            {
                "Cancelled.": "XX cancelled.",
                "Pair settings ({n}):": "XX settings ({n}):",
            },
        )
        set_locale("xx", directory=tmp_path)

        assert _("Cancelled.") == "XX cancelled."
        assert _("Pair settings ({n}):").format(n=2) == "XX settings (2):"

    def test_msgids_missing_from_the_catalog_fall_back_to_the_msgid(self, tmp_path):
        """A PARTIAL catalog: translated msgid wins, the rest stays English (msgid)."""
        from bot.i18n import set_locale
        from bot.i18n import translate as _

        _seed_catalog(tmp_path, "xx", {"Cancelled.": "XX cancelled."})
        set_locale("xx", directory=tmp_path)

        assert _("Cancelled.") == "XX cancelled."
        assert _("Title is empty — send the thread title.") == (
            "Title is empty — send the thread title."
        )

    def test_locale_switch_is_read_at_call_time(self, tmp_path):
        """The current translation is consulted per call — no reply is precomputed."""
        from bot.i18n import set_locale
        from bot.i18n import translate as _

        _seed_catalog(tmp_path, "xx", {"Cancelled.": "XX cancelled."})
        set_locale("xx", directory=tmp_path)
        assert _("Cancelled.") == "XX cancelled."

        set_locale("en", directory=tmp_path)
        assert _("Cancelled.") == "Cancelled."

    def test_default_localedir_is_package_relative_not_cwd(self, monkeypatch, tmp_path):
        """``directory=None`` resolves ``bot/locales`` NEXT TO THE PACKAGE.

        The empty ``tmp_path`` is the working directory: a cwd-relative
        lookup would not find the repository's catalog area either way,
        but the point is the resolution base — package-relative, always.
        """
        from bot.i18n import set_locale
        from bot.i18n import translate as _

        monkeypatch.chdir(tmp_path)
        set_locale("en")

        assert _("Cancelled.") == "Cancelled."


class TestLocaleNameValidation:
    """I-1: ``set_locale`` validates the name BEFORE the filesystem is touched."""

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("../evil", id="path-traversal"),
            pytest.param("en/../../x", id="nested-traversal"),
            pytest.param("", id="empty-name"),
            pytest.param("a b", id="name-with-space"),
        ],
    )
    def test_an_invalid_locale_name_raises_value_error(self, tmp_path, value):
        """A traversal / blank name → ``ValueError`` naming the value, not OSError."""
        from bot.i18n import set_locale

        with pytest.raises(ValueError) as exc_info:
            set_locale(value, directory=tmp_path / "packs")

        assert value in str(exc_info.value), (
            f"the ValueError must name the refused locale {value!r}"
        )

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("en_US", id="underscore-locale"),
            pytest.param("pt-BR", id="hyphen-locale"),
            pytest.param("no-such-locale", id="missing-catalog"),
        ],
    )
    def test_a_valid_locale_name_is_accepted_even_without_a_catalog(self, tmp_path, value):
        """A well-formed name switches to the silent msgid fallback (no file error)."""
        from bot.i18n import set_locale
        from bot.i18n import translate as _

        set_locale(value, directory=tmp_path / "packs")

        assert _("Cancelled.") == "Cancelled.", "the switch must land on English, not fail"

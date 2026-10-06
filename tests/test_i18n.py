"""i18n tests: the locale pack and the ``bot.i18n`` module (backlog item 5).

RED phase: neither ``bot/locales/en.json`` nor ``bot/i18n.py`` exists yet,
so every test here fails — ``FileNotFoundError`` for the pack itself
(specification test and the key-side drift check), ``ModuleNotFoundError``
for the ``bot.i18n`` calls, and the value-side drift check fails its
ASSERT while the reply literals still live in ``bot/handlers/watcher.py``.
The tests pin the migration:

- ``bot/locales/en.json`` carries EXACTLY the 30 keys below with
  byte-exact values — the very strings the bot sends (the 12 watcher
  strings moved out of ``bot/handlers/watcher.py`` plus the 18
  settings-menu strings of cycle B; the pack test IS the
  specification);
- ``t(key, **fmt)`` renders through ``str.format`` (a missing placeholder
  argument stays the natural ``KeyError``) and works WITHOUT an explicit
  ``set_locale``: the first call lazily loads the default pack ``en``;
- ``set_locale(locale, directory=None)`` re-reads ``{locale}.json`` from
  ``directory``; ``None`` resolves ``bot/locales`` RELATIVE TO THE
  PACKAGE (never the CWD), and a missing locale file raises
  ``FileNotFoundError`` naming the path;
- a key absent from the current pack falls back to ``en``, a key absent
  there too raises ``KeyError(key)``;
- key drift is caught from BOTH sides over the AST of ``bot/**/*.py``:
  every ``t("literal")`` key exists in the pack, and no pack value
  survives in any source file (``bot/locales/`` excluded) — after the
  migration the reply strings live in the JSON pack ONLY.

``bot.i18n`` is imported INSIDE the test functions on purpose: a
module-level import would kill the whole file at collection and hide the
per-test failure modes (the pack tests must fail on their own).
"""

import ast
import json
from pathlib import Path

import pytest

#: Repository root, the scanned package and the pack area (tests/ sits next to bot/).
ROOT = Path(__file__).resolve().parent.parent
BOT_DIR = ROOT / "bot"
LOCALES_DIR = BOT_DIR / "locales"
EN_PACK_PATH = LOCALES_DIR / "en.json"

#: The locale pack exactly as pinned by the spec: 30 keys, byte-exact values.
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


def _read_en_pack() -> dict:
    """Load ``bot/locales/en.json`` (UTF-8) — ``FileNotFoundError`` while RED."""
    return json.loads(EN_PACK_PATH.read_text(encoding="utf-8"))


def _seed_pack(directory: Path, locale: str, pack: dict) -> Path:
    """Write ``{locale}.json`` into ``directory`` (UTF-8, pretty-printed)."""
    path = directory / f"{locale}.json"
    path.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _bot_python_files() -> list[Path]:
    """Every ``.py`` file of the ``bot`` package (the packs live in JSON)."""
    return sorted(path for path in BOT_DIR.rglob("*.py") if LOCALES_DIR not in path.parents)


def _string_literals(files: list[Path]) -> list[str]:
    """Every string constant of ``files`` via AST: docstrings and f-parts included."""
    literals: list[str] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                literals.append(node.value)
    return literals


def _is_t_call(node: ast.Call) -> bool:
    """Whether ``node`` calls ``t`` as a bare name or as an attribute."""
    if isinstance(node.func, ast.Name):
        return node.func.id == "t"
    return isinstance(node.func, ast.Attribute) and node.func.attr == "t"


def _t_literal_keys(files: list[Path]) -> list[str]:
    """Keys of every ``t("literal", ...)`` / ``i18n.t("literal")`` call."""
    keys: list[str] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not _is_t_call(node):
                continue
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                keys.append(first.value)
    return keys


class TestPackSpecification:
    """The file ``bot/locales/en.json`` IS the specification of the replies."""

    def test_en_pack_has_exactly_the_pinned_keys_and_values(self):
        """30 keys, byte-exact values: the 12 watcher strings + 18 settings ones."""
        pack = _read_en_pack()

        assert len(PACK_SPEC) == 30, "spec sanity: the pack pins exactly 30 keys"
        assert set(pack) == set(PACK_SPEC), "the pack must carry exactly the 30 pinned keys"
        assert pack == PACK_SPEC, "every value must match the specification byte for byte"


class TestFormatting:
    """``t(key, **fmt)`` renders through ``str.format`` on the lazy default pack."""

    def test_batch_too_large_formats_n(self):
        """The batch-cap refusal embeds the REAL size; the limit stays pinned."""
        from bot.i18n import t

        expected = "Batch too large (101 messages, limit 100). Nothing was moved."
        assert t("watcher.batch_too_large", n=101) == expected

    def test_created_line_formats_n_and_thread_url(self):
        """Success line 1: the count plus the ready thread link."""
        from bot.i18n import t

        expected = "Thread created: 2 message(s). x"
        assert t("watcher.created_line", n=2, thread_url="x") == expected

    def test_deleted_line_formats_x_and_y(self):
        """Success line 2: how many originals the cleanup actually found."""
        from bot.i18n import t

        expected = "Deleted 1 of 2 original messages."
        assert t("watcher.deleted_line", x=1, y=2) == expected

    def test_missing_placeholder_argument_raises_key_error(self):
        """No ``n`` passed → the natural ``KeyError`` of ``str.format``."""
        from bot.i18n import t

        with pytest.raises(KeyError):
            t("watcher.batch_too_large")


class TestLocaleFallbackAndErrors:
    """Foreign packs, the ``en`` fallback chain and the two error types (pin)."""

    def test_full_foreign_pack_serves_its_own_values(self, tmp_path):
        """A complete ``xx`` pack replaces every value of the current locale."""
        from bot.i18n import set_locale, t

        _seed_pack(tmp_path, "en", PACK_SPEC)
        _seed_pack(tmp_path, "xx", {key: f"XX {key}" for key in PACK_SPEC})
        set_locale("xx", directory=tmp_path)

        assert t("watcher.cancelled") == "XX watcher.cancelled"
        assert t("thread.header_template") == "XX thread.header_template"

    def test_keys_missing_from_the_pack_fall_back_to_en(self, tmp_path):
        """A one-key ``xx`` pack: its own key wins, the rest come from ``en``."""
        from bot.i18n import set_locale, t

        _seed_pack(tmp_path, "en", PACK_SPEC)
        _seed_pack(tmp_path, "xx", {"watcher.cancelled": "XX cancelled."})
        set_locale("xx", directory=tmp_path)

        assert t("watcher.cancelled") == "XX cancelled."
        assert t("watcher.empty_title") == PACK_SPEC["watcher.empty_title"]

    def test_unknown_key_raises_key_error(self):
        """No ``en`` value either → ``KeyError(key)``, never ``None`` or a blank."""
        from bot.i18n import t

        with pytest.raises(KeyError) as exc_info:
            t("there.is.no.such.key")

        assert "there.is.no.such.key" in str(exc_info.value)

    def test_unknown_key_raises_key_error_in_a_foreign_pack_too(self, tmp_path):
        """The fallback chain ENDS in ``KeyError`` even for a complete pack."""
        from bot.i18n import set_locale, t

        _seed_pack(tmp_path, "en", PACK_SPEC)
        _seed_pack(tmp_path, "xx", {key: f"XX {key}" for key in PACK_SPEC})
        set_locale("xx", directory=tmp_path)

        with pytest.raises(KeyError):
            t("there.is.no.such.key")

    def test_missing_locale_file_raises_file_not_found(self, tmp_path):
        """``{locale}.json`` absent → ``FileNotFoundError`` naming that path."""
        from bot.i18n import set_locale

        _seed_pack(tmp_path, "en", PACK_SPEC)

        with pytest.raises(FileNotFoundError) as exc_info:
            set_locale("no-such-locale", directory=tmp_path)

        assert "no-such-locale" in str(exc_info.value)

    def test_default_pack_directory_is_package_relative_not_cwd(self, monkeypatch, tmp_path):
        """``directory=None`` resolves ``bot/locales`` NEXT TO THE PACKAGE.

        The empty ``tmp_path`` is the working directory: a cwd-relative
        lookup would raise ``FileNotFoundError`` here instead of loading
        the real pack of the repository.
        """
        from bot.i18n import set_locale, t

        monkeypatch.chdir(tmp_path)
        set_locale("en")

        assert t("watcher.cancelled") == "Cancelled."


class TestKeyDrift:
    """Both directions of the migration, read out of the AST (pin)."""

    def test_every_t_key_used_in_the_code_exists_in_the_pack(self):
        """(a) every ``t("literal")`` of ``bot/**/*.py`` is defined in ``en.json``."""
        pack = _read_en_pack()
        keys = _t_literal_keys(_bot_python_files())
        missing = sorted(set(keys) - set(pack))

        assert not missing, f"keys used in the code but absent from en.json: {missing}"

    def test_no_pack_value_survives_in_the_bot_sources(self):
        """(b) every pack value is GONE from the sources — the strings live in JSON.

        This is the heart of the migration: while the reply literals still
        sit in ``bot/handlers/watcher.py``, their values are found in the
        source constants and the test fails naming the leaking keys.

        The values are taken from ``PACK_SPEC`` rather than from the file:
        ``test_en_pack_has_exactly_the_pinned_keys_and_values`` pins
        ``en.json == PACK_SPEC``, so the scan is equivalent to reading the
        pack — but it already RUNS in the RED phase (where the pack file
        does not exist yet) and fails on the leaking watcher literals.
        """
        literals = _string_literals(_bot_python_files())
        leaked = sorted(
            key for key, value in PACK_SPEC.items() if any(value in chunk for chunk in literals)
        )

        assert not leaked, (
            f"values of these keys still live in bot/**/*.py — move them to the pack: {leaked}"
        )


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
            pytest.param("no-such-locale", id="missing-pack-file"),
        ],
    )
    def test_a_valid_locale_name_reaches_the_filesystem(self, tmp_path, value):
        """A well-formed name is accepted — the missing FILE is what is reported."""
        from bot.i18n import set_locale

        with pytest.raises(FileNotFoundError) as exc_info:
            set_locale(value, directory=tmp_path / "packs")

        assert value in str(exc_info.value), "the FileNotFoundError names the path"

    def test_the_valid_default_english_pack_still_serves(self, tmp_path):
        """A well-formed name loads its pack exactly as before the validation."""
        from bot.i18n import set_locale, t

        _seed_pack(tmp_path, "en", PACK_SPEC)
        set_locale("en", directory=tmp_path)

        assert t("watcher.cancelled") == "Cancelled."

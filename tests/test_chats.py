"""Source→target pair registry tests: module ``bot.chats`` (BRIEF v3, §2).

The v3 registry keeps the JSON pair format of v2 and adds the three
lookups the watcher flow needs: the admin's «which chat did you forward
from?» answer (``pair_for_source_ref``), the filters for the watcher
(``is_configured_target``) and the buffering router (``is_source``),
plus the forward-origin matching of step 2 (``pair_for_origin``).

Specification (BRIEF.md v3, sections «Конфиг», «Шаг 2», «Шаг 5»):
- the config holds pairs ``source → target`` in a JSON file of the
  shape::

      {"pairs": [{"source": "@src", "target": "@tgt"},
                 {"source": -100111, "target": -100222}]}

  — public ``@usernames`` and private integer chat ids both allowed;
- ``target_for(source)`` returns the configured target for the source
  chat, or ``None`` when the source is not configured; matching works
  both by integer id and by ``@username``;
- a missing, broken or malformed config file is NOT an error: the
  registry behaves as an empty one — every lookup returns ``None`` and
  nothing raises (fixed here by these tests);
- a self-referential pair ``source == target`` is dropped at load:
  such an entry is neither a usable source nor a watchable target
  (security review M4), while complete pairs next to it keep working;
  the drop must recognise the SAME chat in different forms too (int id
  vs its numeric string, username letter-case — F4), a raw value
  comparison is not enough;
- ``pair_for_source_ref(text)`` parses the admin's answer to the
  «which chat did you forward from?» question — a ``@username`` or a
  numeric id string (including ``-100…``) — and returns the matching
  pair or ``None``: this is the config check of BRIEF step 2 («не
  настроено → СТОП»);
- ``is_configured_target(chat_ref)`` reports whether the chat is the
  target of ANY pair (the watcher forward filter);
- ``is_source(chat_ref)`` reports whether the chat is the source of ANY
  pair (the buffering filter);
- ``pair_for_origin(origin)`` matches a forward origin against the
  pairs: the origin carries ``chat_id`` and/or ``username`` and ANY of
  them matching a pair's source identifies the pair (step 2: an origin
  whose chat is configured lets the bot skip the question); an origin
  without a chat (a user forward) is ``None``;
- the module exposes a singleton whose default path is ``chats.json``
  in the current working directory;
- every lookup reads the file anew, so a config written after startup
  is picked up (no stale cache);
- every test works on its own tmp files and never touches a project
  config.

RED phase (security review F4): ``TestSelfPairRecognisedAcrossForms``
pins the normalized self-pair drop (raw ``source != target`` misses the
same chat written in two forms) — its tests fail until the load-time
comparison normalises; the kept v2/v3 tests stay green.
"""

import json
from pathlib import Path

import pytest

#: The config fixture of this module: one username pair, one int-id pair.
PAIRS = [
    {"source": "@src", "target": "@tgt"},
    {"source": -100111, "target": -100222},
]


def chats_mod():
    """The ``bot.chats`` module, imported at test time.

    The import lives inside this function on purpose: at top level a
    missing module would abort the collection of the whole pytest run
    and would be classified as third-party by ruff, flipping ``I001``
    once the file exists.
    """
    import bot.chats as chats_module

    return chats_module


def write_pairs(path: Path, pairs) -> Path:
    """Write a registry file of the documented JSON shape to ``path``."""
    path.write_text(json.dumps({"pairs": pairs}), encoding="utf-8")
    return path


def registry(tmp_path: Path, pairs=PAIRS):
    """A registry on its own tmp file (never the project config)."""
    return chats_mod().ChatPairs(write_pairs(tmp_path / "chats.json", pairs))


class TestTargetFor:
    """``target_for(source)``: lookup by @username and by integer id."""

    def test_username_source_matches_username_pair(self, tmp_path: Path):
        """``@src`` is configured → its target comes back verbatim (as a string)."""
        pairs = registry(tmp_path)

        assert pairs.target_for("@src") == "@tgt"

    def test_integer_source_matches_integer_pair(self, tmp_path: Path):
        """A private chat id is configured → its target comes back verbatim (as an int)."""
        pairs = registry(tmp_path)

        assert pairs.target_for(-100111) == -100222

    @pytest.mark.parametrize(
        "unknown_source",
        [pytest.param("@unknown", id="unknown-username"), pytest.param(-100999, id="unknown-id")],
    )
    def test_unconfigured_source_returns_none(self, tmp_path: Path, unknown_source):
        """A chat without a pair → ``None``: the caller learns it is not a source chat."""
        pairs = registry(tmp_path)

        assert pairs.target_for(unknown_source) is None

    def test_empty_registry_returns_none(self, tmp_path: Path):
        """An empty pair list is a valid config: every lookup misses."""
        pairs = registry(tmp_path, [])

        assert pairs.target_for("@src") is None
        assert pairs.target_for(-100111) is None


class TestBrokenRegistryIsEmpty:
    """Broken config → empty registry (``None``), never an exception."""

    def test_missing_file_is_not_an_error(self, tmp_path: Path):
        """No config file at all → lookups miss instead of crashing the bot."""
        pairs = chats_mod().ChatPairs(tmp_path / "does-not-exist.json")

        assert pairs.target_for("@src") is None

    def test_invalid_json_is_not_an_error(self, tmp_path: Path):
        """A hand-edited/truncated file → empty registry, no exception."""
        path = tmp_path / "chats.json"
        path.write_text("{oops, not json", encoding="utf-8")
        pairs = chats_mod().ChatPairs(path)

        assert pairs.target_for("@src") is None, (
            "a syntactically broken config must read as \"no pairs\", not raise"
        )

    @pytest.mark.parametrize(
        "content",
        [
            pytest.param("[1, 2, 3]", id="json-list-instead-of-object"),
            pytest.param(json.dumps({"pairs": "nope"}), id="pairs-not-a-list"),
            pytest.param(json.dumps({"nonsense": []}), id="pairs-key-missing"),
            pytest.param(json.dumps({"pairs": [{"target": "@tgt"}]}), id="source-key-missing"),
            pytest.param(json.dumps({"pairs": [{"source": "@src"}]}), id="target-key-missing"),
        ],
    )
    def test_malformed_content_reads_as_empty(self, tmp_path: Path, content: str):
        """Anything that is not a list of complete pairs → empty registry."""
        path = tmp_path / "chats.json"
        path.write_text(content, encoding="utf-8")
        pairs = chats_mod().ChatPairs(path)

        assert pairs.target_for("@src") is None

    def test_malformed_entries_do_not_hide_the_valid_ones(self, tmp_path: Path):
        """Garbage entries are skipped; a complete pair next to them still works."""
        pairs = registry(
            tmp_path,
            [42, {"target": "@dangling"}, {"source": "@src"}, {"source": "@ok", "target": "@good"}],
        )

        assert pairs.target_for("@src") is None, "an entry without a target is not a pair"
        assert pairs.target_for("@ok") == "@good", "a complete pair must survive bad neighbours"


class TestLookupReadsTheConfigFresh:
    """No stale cache: pairs written after construction are visible."""

    def test_pairs_written_after_construction_are_seen(self, tmp_path: Path):
        """The config may be created after the registry (even after bot startup)."""
        path = tmp_path / "chats.json"
        pairs = chats_mod().ChatPairs(path)

        assert pairs.target_for("@src") is None, "sanity: no file yet"

        write_pairs(path, PAIRS)

        assert pairs.target_for("@src") == "@tgt", (
            "target_for must read the config anew — a file written after startup "
            "has to be picked up without restarting the process"
        )


class TestModuleSingleton:
    """The shared registry instance of ``bot.chats``."""

    def test_singleton_is_a_chat_pairs_registry(self):
        """``bot.chats.chats`` is a ``ChatPairs`` — the instance handlers ask."""
        mod = chats_mod()

        assert isinstance(mod.chats, mod.ChatPairs), (
            "bot.chats must expose the singleton chats = ChatPairs()"
        )

    def test_default_path_is_chats_json_in_cwd(self, tmp_path: Path, monkeypatch):
        """The singleton's default path is ``chats.json`` in the current directory."""
        monkeypatch.chdir(tmp_path)
        write_pairs(Path.cwd() / "chats.json", PAIRS)

        assert chats_mod().chats.target_for("@src") == "@tgt", (
            "the singleton must resolve chats.json against the current working directory"
        )


@pytest.fixture()
def registry_in_cwd(tmp_path: Path, monkeypatch):
    """Run the test against a cwd ``chats.json`` holding ``PAIRS``."""
    monkeypatch.chdir(tmp_path)
    write_pairs(Path.cwd() / "chats.json", PAIRS)


class TestPairForSourceRef:
    """``pair_for_source_ref(text)``: the admin's «which chat?» answer (step 2).

    The bot asks ``Which chat did you forward from? Reply with
    @username or its id.`` — the answer must be matched against the
    configured pairs: found → the pair is fixed and the flow continues;
    not found → ``None`` and the bot answers
    ``This chat is not configured as a source chat.`` and stops.
    """

    @pytest.mark.parametrize(
        "answer,expected",
        [
            pytest.param(
                "@src", {"source": "@src", "target": "@tgt"}, id="username-answer"
            ),
            pytest.param(
                "-100111",
                {"source": -100111, "target": -100222},
                id="negative-internal-id-answer",
            ),
            pytest.param(
                "  -100111  ",
                {"source": -100111, "target": -100222},
                id="answer-with-surrounding-spaces",
            ),
        ],
    )
    def test_configured_answers_resolve_to_the_pair(self, registry_in_cwd, answer, expected):
        """A configured ``@username``/id answer comes back as its pair dict."""
        mod = chats_mod()

        assert mod.pair_for_source_ref(answer) == expected, (
            f"the answer {answer!r} must resolve to the configured pair {expected!r}"
        )

    @pytest.mark.parametrize(
        "answer",
        [
            pytest.param("@unknown", id="unconfigured-username"),
            pytest.param("-100999", id="unconfigured-id"),
            pytest.param("", id="empty-answer"),
            pytest.param("just a chat name", id="free-text"),
            pytest.param("src", id="username-without-at"),
            pytest.param("12.5", id="not-an-integer"),
        ],
    )
    def test_unconfigured_answers_resolve_to_none(self, registry_in_cwd, answer):
        """Anything that is not a configured pair → ``None`` («не настроено → СТОП»)."""
        mod = chats_mod()

        assert mod.pair_for_source_ref(answer) is None, (
            f"the answer {answer!r} must not resolve to any pair"
        )

    def test_broken_config_resolves_to_none(self, tmp_path: Path, monkeypatch):
        """A broken registry answers every question with ``None``."""
        monkeypatch.chdir(tmp_path)
        Path.cwd().joinpath("chats.json").write_text("{oops", encoding="utf-8")

        assert chats_mod().pair_for_source_ref("@src") is None


class TestIsConfiguredTarget:
    """``is_configured_target(chat_ref)``: the watcher forward filter (step 1).

    The bot works ONLY in configured threaded chats (BRIEF §2) — the
    forward handler of the watcher admits a chat that is the ``target``
    of any pair; source-only chats and strangers are refused.
    """

    def test_username_target_is_configured(self, registry_in_cwd):
        """``@tgt`` is the target of a pair → the watcher admits it."""
        assert chats_mod().is_configured_target("@tgt") is True

    def test_integer_target_is_configured(self, registry_in_cwd):
        """A private id used as a target works exactly like a username."""
        assert chats_mod().is_configured_target(-100222) is True

    @pytest.mark.parametrize(
        "ref",
        [
            pytest.param("@src", id="source-only-username"),
            pytest.param(-100111, id="source-only-id"),
            pytest.param("@unknown", id="stranger-username"),
            pytest.param(-100999, id="stranger-id"),
        ],
    )
    def test_non_targets_are_not_configured(self, registry_in_cwd, ref):
        """Source-only chats and strangers must not pass the watcher filter."""
        assert chats_mod().is_configured_target(ref) is False

    def test_broken_config_configures_nothing(self, tmp_path: Path, monkeypatch):
        """A broken registry must not let any chat through."""
        monkeypatch.chdir(tmp_path)
        Path.cwd().joinpath("chats.json").write_text("{oops", encoding="utf-8")

        assert chats_mod().is_configured_target("@tgt") is False


class TestIsSource:
    """``is_source(chat_ref)``: the buffering filter (section 5).

    Only the source (main) chat of a pair is buffered — it is the base
    for finding originals in step 5. Threaded chats and strangers must
    never be recorded.
    """

    def test_username_source_is_a_source(self, registry_in_cwd):
        """``@src`` is the source of a pair → buffered."""
        assert chats_mod().is_source("@src") is True

    def test_integer_source_is_a_source(self, registry_in_cwd):
        """A private id used as a source works exactly like a username."""
        assert chats_mod().is_source(-100111) is True

    @pytest.mark.parametrize(
        "ref",
        [
            pytest.param("@tgt", id="target-only-username"),
            pytest.param(-100222, id="target-only-id"),
            pytest.param("@unknown", id="stranger-username"),
            pytest.param(-100999, id="stranger-id"),
        ],
    )
    def test_non_sources_are_not_sources(self, registry_in_cwd, ref):
        """Threaded-only chats and strangers must not be buffered."""
        assert chats_mod().is_source(ref) is False

    def test_broken_config_has_no_sources(self, tmp_path: Path, monkeypatch):
        """A broken registry must not make any chat a source."""
        monkeypatch.chdir(tmp_path)
        Path.cwd().joinpath("chats.json").write_text("{oops", encoding="utf-8")

        assert chats_mod().is_source("@src") is False


class TestPairForOrigin:
    """``pair_for_origin(origin)``: forward-origin matching (BRIEF step 2).

    A forward origin carries the origin chat as ``chat_id`` and/or
    ``username`` (channels show both, a public group shows a username,
    a private group only its id). ANY match against a pair's source
    identifies the pair — «forward_origin содержит чат и пара настроена
    → молча идём дальше». An origin without a chat (a user forward)
    resolves to ``None`` — the bot asks the «which chat?» question.
    """

    def test_origin_matching_by_integer_id(self, registry_in_cwd):
        """A public origin (@name) of the group configured by ``-100…`` id → its pair.

        The pin of the spec: the pair is stored as the private id of
        the same group the public origin announces — the id match must
        recognise it.
        """
        mod = chats_mod()
        origin = {"chat_id": -100111, "username": "srcgroup"}

        assert mod.pair_for_origin(origin) == {"source": -100111, "target": -100222}, (
            "the origin chat id matches the pair keyed by that id"
        )

    def test_origin_matching_by_username(self, registry_in_cwd):
        """An origin with ``chat_id=None`` but a username → the username pair."""
        mod = chats_mod()

        assert mod.pair_for_origin({"chat_id": None, "username": "src"}) == {
            "source": "@src",
            "target": "@tgt",
        }

    def test_origin_matching_by_username_with_id_absent(self, registry_in_cwd):
        """A partial origin (the ``chat_id`` key is missing entirely) still matches."""
        mod = chats_mod()

        assert mod.pair_for_origin({"username": "src"}) is not None

    @pytest.mark.parametrize(
        "origin",
        [
            pytest.param({"chat_id": -100999, "username": "other"}, id="stranger-group"),
            pytest.param({"chat_id": None, "username": None}, id="no-chat-user-origin"),
            pytest.param({}, id="empty-origin"),
        ],
    )
    def test_origin_without_a_configured_chat_is_none(self, registry_in_cwd, origin):
        """A foreign or chatless origin → ``None``: the bot must ask the question."""
        assert chats_mod().pair_for_origin(origin) is None

    def test_pair_for_origin_reads_the_config_fresh(self, tmp_path: Path, monkeypatch):
        """Pairs added after startup are matched too (no stale cache)."""
        monkeypatch.chdir(tmp_path)
        assert chats_mod().pair_for_origin({"username": "src"}) is None, "sanity: no file"

        write_pairs(Path.cwd() / "chats.json", PAIRS)

        assert chats_mod().pair_for_origin({"username": "src"}) == {
            "source": "@src",
            "target": "@tgt",
        }


class TestSelfReferentialPairIsDropped:
    """A pair whose source equals its target never loads (security review M4).

    ``{"source": "@x", "target": "@x"}`` would make a chat its own
    target: the bot would try to build a thread inside the very chat it
    moves messages out of, and the filters would watch it on both ends.
    Such an entry is dropped at load: ``target_for`` misses for it, and
    neither ``is_configured_target`` nor ``is_source`` reports it, while
    every complete pair next to it keeps working.
    """

    @pytest.mark.parametrize(
        "ref",
        [
            pytest.param("@loop", id="username-loop"),
            pytest.param(-100111, id="integer-loop"),
        ],
    )
    def test_self_pair_is_dropped_for_every_lookup(self, tmp_path: Path, monkeypatch, ref):
        """source == target → not a target, not a source, no resolvable target."""
        monkeypatch.chdir(tmp_path)
        write_pairs(Path.cwd() / "chats.json", [{"source": ref, "target": ref}])
        mod = chats_mod()

        assert mod.chats.target_for(ref) is None, (
            f"a self-referential pair must not resolve as {ref!r} — it would "
            "let the source chat move messages into itself"
        )
        assert mod.is_configured_target(ref) is False, (
            "a self-referential pair must not make the watcher watch the source chat"
        )
        assert mod.is_source(ref) is False, (
            "a self-referential pair must not make the buffering watch the target chat"
        )

    def test_dropped_self_pair_does_not_hide_the_valid_ones(
        self, tmp_path: Path, monkeypatch
    ):
        """Garbage is skipped selectively: a complete pair beside it still works."""
        monkeypatch.chdir(tmp_path)
        write_pairs(
            Path.cwd() / "chats.json",
            [
                {"source": "@loop", "target": "@loop"},
                {"source": "@ok", "target": "@good"},
            ],
        )
        mod = chats_mod()

        assert mod.chats.target_for("@loop") is None, "the self-pair must be dropped"
        assert mod.chats.target_for("@ok") == "@good", (
            "a complete pair must survive the dropped self-referential neighbour"
        )
        assert mod.is_configured_target("@good") is True
        assert mod.is_configured_target("@loop") is False
        assert mod.is_source("@ok") is True
        assert mod.is_source("@loop") is False


class TestSelfPairRecognisedAcrossForms:
    """The self-pair drop must not compare RAW values (security review F4).

    ``{"source": -100111, "target": "-100111"}`` (int id vs its numeric
    string) and ``{"source": "@MyChat", "target": "@mychat"}`` (Telegram
    usernames are case-insensitive) are the SAME chat in two forms — a
    raw ``source != target`` keeps such a pair and makes a chat its own
    target. Every lookup must refuse it exactly like the canonical
    ``source == target`` pair of ``TestSelfReferentialPairIsDropped``.
    """

    @pytest.mark.parametrize(
        "pair,own_ref,other_ref",
        [
            pytest.param(
                {"source": -100666, "target": "-100666"},
                -100666,
                "-100666",
                id="int-id-vs-numeric-string",
            ),
            pytest.param(
                {"source": "@MyChat", "target": "@mychat"},
                "@MyChat",
                "@mychat",
                id="username-letter-case",
            ),
        ],
    )
    def test_a_self_pair_in_another_form_is_dropped(
        self, tmp_path: Path, monkeypatch, pair, own_ref, other_ref
    ):
        """The pair loads as no pair at all: no target, not a source, not a target."""
        monkeypatch.chdir(tmp_path)
        write_pairs(Path.cwd() / "chats.json", [pair])
        mod = chats_mod()

        assert mod.chats.target_for(own_ref) is None, (
            f"the self-referential pair {pair!r} must not resolve as {own_ref!r} — "
            "it would let the chat move messages into itself"
        )
        assert mod.is_source(own_ref) is False, (
            "a self-pair must not make the buffering watch the target chat"
        )
        assert mod.is_configured_target(other_ref) is False, (
            "a self-pair must not make the watcher watch the source chat"
        )

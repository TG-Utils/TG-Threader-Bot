"""Source→target pair registry tests: module ``bot.chats`` (BRIEF v3, §2).

Cycle A moved the registry from the JSON file into the ``pairs``
table: the module keeps its pairs IN MEMORY as a cache, the database
is the source of truth (``refresh()`` reloads the cache, ``add_pair``
/ ``remove_pair`` maintain it), and the JSON file is NOT read anymore.
The synchronous read API stays exactly as it was — the watcher and the
buffering router ask it without awaiting:

Specification (BRIEF.md v3, sections "Config", "Step 2", "Step 5"):
- the registry holds pairs ``source → target`` — public
  ``@usernames`` and private integer chat ids both allowed (the
  ``pairs`` table stores them as TEXT and reads them back in their
  canonical Python form: numeric refs come back as ``int``);
- ``target_for(source)`` returns the configured target for the source
  chat, or ``None`` when the source is not configured; matching works
  both by integer id and by ``@username``;
- a self-referential pair ``source == target`` can never ENTER the
  registry: ``add_pair`` refuses it at insert (the same form-independent
  drop as the old load time — security review M4 + F4), while complete
  pairs beside it keep working;
- ``pair_for_source_ref(text)`` parses the admin's answer to the
  «Which chat did you forward from?» question — a ``@username`` or a
  numeric id string (including ``-100…``) — and returns the matching
  pair or ``None``: this is the config check of BRIEF step 2 («not
  configured → STOP»);
- ``is_configured_target(chat_ref)`` reports whether the chat is the
  target of ANY pair (the watcher forward filter);
- ``is_source(chat_ref)`` reports whether the chat is the source of ANY
  pair (the buffering filter);
- ``pair_for_origin(origin)`` matches a forward origin against the
  pairs: the origin carries ``chat_id`` and/or ``username`` and ANY of
  them matching a pair's source identifies the pair (step 2: an origin
  whose chat is configured lets the bot skip the question); an origin
  without a chat (a user forward) is ``None``;
- the module exposes a singleton (``bot.chats.chats``) whose pairs
  live in the database — a pair added after startup is picked up by
  ``add_pair`` (which refreshes), and the suite reseeds it per test;
- every test seeds through ``add_pair`` (no file anywhere) and never
  touches a project config.

RED phase: ``add_pair`` / ``refresh`` do not exist yet — every seeded
test fails inside its fixture or body (``AttributeError``), never at
collection time: the ``bot.chats`` import stays lazy in the
``chats_mod()`` helper below. The two tests that need no seeding (an
empty registry, the singleton type) stay green.
"""

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


async def seed_pairs(pairs, registry=None) -> None:
    """Insert ``pairs`` into the database-backed registry (the only seeding).

    Every test starts from an empty table (the suite-wide
    ``fresh_database`` fixture of ``tests/conftest.py`` reloads the
    pair cache too), so seeding is a plain insert through
    ``add_pair`` — which refreshes the cache on success.
    """
    target = chats_mod().chats if registry is None else registry
    for pair in pairs:
        added = await target.add_pair(pair["source"], pair["target"])
        assert added is True, f"seeding the pair {pair!r} must be accepted, got {added!r}"


@pytest.fixture()
async def seeded_registry(fresh_database):
    """The singleton registry seeded with ``PAIRS`` (fresh database + cache)."""
    await seed_pairs(PAIRS)

    return chats_mod().chats


class TestTargetFor:
    """``target_for(source)``: lookup by @username and by integer id."""

    def test_username_source_matches_username_pair(self, seeded_registry):
        """``@src`` is configured → its target comes back verbatim (as a string)."""
        assert seeded_registry.target_for("@src") == "@tgt"

    def test_integer_source_matches_integer_pair(self, seeded_registry):
        """A private chat id is configured → its target comes back verbatim (as an int)."""
        assert seeded_registry.target_for(-100111) == -100222

    @pytest.mark.parametrize(
        "unknown_source",
        [pytest.param("@unknown", id="unknown-username"), pytest.param(-100999, id="unknown-id")],
    )
    def test_unconfigured_source_returns_none(self, seeded_registry, unknown_source):
        """A chat without a pair → ``None``: the caller learns it is not a source chat."""
        assert seeded_registry.target_for(unknown_source) is None

    def test_empty_registry_returns_none(self):
        """An empty pair list is a valid registry: every lookup misses."""
        pairs = chats_mod().chats

        assert pairs.target_for("@src") is None
        assert pairs.target_for(-100111) is None


class TestModuleSingleton:
    """The shared registry instance of ``bot.chats``."""

    def test_singleton_is_a_chat_pairs_registry(self):
        """``bot.chats.chats`` is a ``ChatPairs`` — the instance handlers ask."""
        mod = chats_mod()

        assert isinstance(mod.chats, mod.ChatPairs), (
            "bot.chats must expose the singleton chats = ChatPairs()"
        )


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
    def test_configured_answers_resolve_to_the_pair(self, seeded_registry, answer, expected):
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
    def test_unconfigured_answers_resolve_to_none(self, seeded_registry, answer):
        """Anything that is not a configured pair → ``None`` («not configured → STOP»)."""
        mod = chats_mod()

        assert mod.pair_for_source_ref(answer) is None, (
            f"the answer {answer!r} must not resolve to any pair"
        )


class TestIsConfiguredTarget:
    """``is_configured_target(chat_ref)``: the watcher forward filter (step 1).

    The bot works ONLY in configured threaded chats (BRIEF §2) — the
    forward handler of the watcher admits a chat that is the ``target``
    of any pair; source-only chats and strangers are refused.
    """

    def test_username_target_is_configured(self, seeded_registry):
        """``@tgt`` is the target of a pair → the watcher admits it."""
        assert chats_mod().is_configured_target("@tgt") is True

    def test_integer_target_is_configured(self, seeded_registry):
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
    def test_non_targets_are_not_configured(self, seeded_registry, ref):
        """Source-only chats and strangers must not pass the watcher filter."""
        assert chats_mod().is_configured_target(ref) is False


class TestIsSource:
    """``is_source(chat_ref)``: the buffering filter (section 5).

    Only the source (main) chat of a pair is buffered — it is the base
    for finding originals in step 5. Threaded chats and strangers must
    never be recorded.
    """

    def test_username_source_is_a_source(self, seeded_registry):
        """``@src`` is the source of a pair → buffered."""
        assert chats_mod().is_source("@src") is True

    def test_integer_source_is_a_source(self, seeded_registry):
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
    def test_non_sources_are_not_sources(self, seeded_registry, ref):
        """Threaded-only chats and strangers must not be buffered."""
        assert chats_mod().is_source(ref) is False


class TestPairForOrigin:
    """``pair_for_origin(origin)``: forward-origin matching (BRIEF step 2).

    A forward origin carries the origin chat as ``chat_id`` and/or
    ``username`` (channels show both, a public group shows a username,
    a private group only its id). ANY match against a pair's source
    identifies the pair («forward_origin carries a chat and a pair is
    configured → proceed silently»). An origin without a chat (a user
    forward) resolves to ``None`` — the bot asks the «which chat?»
    question.
    """

    def test_origin_matching_by_integer_id(self, seeded_registry):
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

    def test_origin_matching_by_username(self, seeded_registry):
        """An origin with ``chat_id=None`` but a username → the username pair."""
        mod = chats_mod()

        assert mod.pair_for_origin({"chat_id": None, "username": "src"}) == {
            "source": "@src",
            "target": "@tgt",
        }

    def test_origin_matching_by_username_with_id_absent(self, seeded_registry):
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
    def test_origin_without_a_configured_chat_is_none(self, seeded_registry, origin):
        """A foreign or chatless origin → ``None``: the bot must ask the question."""
        assert chats_mod().pair_for_origin(origin) is None

    async def test_pair_for_origin_sees_pairs_added_after_startup(self):
        """A pair added later is matched too — ``add_pair`` refreshes the cache."""
        mod = chats_mod()
        assert mod.pair_for_origin({"username": "src"}) is None, "sanity: empty registry"

        await seed_pairs(PAIRS)

        assert mod.pair_for_origin({"username": "src"}) == {
            "source": "@src",
            "target": "@tgt",
        }


class TestSelfReferentialPairIsDropped:
    """A pair whose source equals its target never enters the registry (M4).

    ``{"source": "@x", "target": "@x"}`` would make a chat its own
    target: the bot would try to build a thread inside the very chat it
    moves messages out of, and the filters would watch it on both ends.
    ``add_pair`` refuses such a pair at INSERT (cycle A: there is no
    file load anymore), so ``target_for`` misses for it and neither
    ``is_configured_target`` nor ``is_source`` reports it, while every
    complete pair beside it keeps working.
    """

    @pytest.mark.parametrize(
        "ref",
        [
            pytest.param("@loop", id="username-loop"),
            pytest.param(-100111, id="integer-loop"),
        ],
    )
    async def test_self_pair_is_dropped_for_every_lookup(self, ref):
        """source == target → refused, not a target, not a source, no target."""
        mod = chats_mod()

        assert await mod.chats.add_pair(ref, ref) is False, (
            f"a self-referential pair must not be accepted as {ref!r} — it would "
            "let the source chat move messages into itself"
        )
        assert mod.chats.target_for(ref) is None, "a refused self-pair must not resolve"
        assert mod.is_configured_target(ref) is False, (
            "a self-referential pair must not make the watcher watch the source chat"
        )
        assert mod.is_source(ref) is False, (
            "a self-referential pair must not make the buffering watch the target chat"
        )

    async def test_dropped_self_pair_does_not_hide_the_valid_ones(self):
        """Refusal is selective: a complete pair beside the refused one still works."""
        mod = chats_mod()

        assert await mod.chats.add_pair("@loop", "@loop") is False, "the self-pair is refused"
        assert await mod.chats.add_pair("@ok", "@good") is True, "the valid pair is accepted"

        assert mod.chats.target_for("@loop") is None, "the self-pair must be dropped"
        assert mod.chats.target_for("@ok") == "@good", (
            "a complete pair must survive the refused self-referential neighbour"
        )
        assert mod.is_configured_target("@good") is True
        assert mod.is_configured_target("@loop") is False
        assert mod.is_source("@ok") is True
        assert mod.is_source("@loop") is False


class TestSelfPairRecognisedAcrossForms:
    """The self-pair refusal must not compare RAW values (security review F4).

    ``{"source": -100111, "target": "-100111"}`` (int id vs its numeric
    string) and ``{"source": "@MyChat", "target": "@mychat"}`` (Telegram
    usernames are case-insensitive) are the SAME chat in two forms — a
    raw ``source != target`` would accept such a pair and make a chat
    its own target. ``add_pair`` must refuse it exactly like the
    canonical ``source == target`` pair of
    ``TestSelfReferentialPairIsDropped``.
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
    async def test_a_self_pair_in_another_form_is_dropped(self, pair, own_ref, other_ref):
        """The pair never enters the registry: no target, not a source, not a target."""
        mod = chats_mod()

        assert await mod.chats.add_pair(pair["source"], pair["target"]) is False, (
            f"the self-referential pair {pair!r} must be refused — "
            "it would let the chat move messages into itself"
        )
        assert mod.chats.target_for(own_ref) is None, (
            f"the self-referential pair {pair!r} must not resolve as {own_ref!r}"
        )
        assert mod.is_source(own_ref) is False, (
            "a self-pair must not make the buffering watch the target chat"
        )
        assert mod.is_configured_target(other_ref) is False, (
            "a self-pair must not make the watcher watch the source chat"
        )

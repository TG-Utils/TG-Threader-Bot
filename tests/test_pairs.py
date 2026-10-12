"""Pair-registry data layer (cycle A): model, DB-backed CRUD, ref parsing.

The ``source → target`` pairs move out of ``chats.json`` into the
``pairs`` table: ``bot.models.Pair`` is the row, ``bot.chats`` keeps
the pairs IN MEMORY as a cache (the synchronous read path the watcher
and the buffering router keep using), and the database is the source
of truth reloaded through ``refresh()``. ``add_pair`` / ``remove_pair``
are the CRUD primitives the settings menu of cycle B will drive.

Specification (cycle A pins):
- ``await chats.add_pair(source, target) -> bool`` INSERTs the pair
  and refreshes the cache (``True``); the columns are TEXT, so a
  numeric ref is stored as its text form and must read back as the
  same number (``target_for(-100111) == -100222``);
- a repeated pair is refused with ``False`` and exactly ONE row stays
  (the ``UNIQUE (source_ref, target_ref)`` constraint of the table);
- a self-referential pair is NEVER stored: ``add_pair`` refuses it
  with ``False`` — the comparison is form-independent (the existing
  ``_same_ref``/``_chat_refs`` machinery: ``@MyChat`` ≡ ``@mychat``,
  ``-100666`` ≡ ``"-100666"``), so no lookup ever sees it;
- ``await refresh()`` re-SELECTs every pair into the cache: a row
  written behind the registry's back is invisible BEFORE ``refresh()``
  and visible after it (until then the cache stays as it was);
- ``await remove_pair(pair_id) -> bool`` DELETEs an existing pair and
  refreshes (``True``); an id without a row — never inserted or
  already removed — returns ``False``;
- ``await parse_chat_ref(text) -> str | int | None`` is the strict
  parser of the admin's answer to ``Which chat did you forward from?
  Reply with @username or its id.`` — the interior of the old
  ``pair_for_source_ref`` extracted for cycle B: a ``@username`` comes
  back verbatim (case kept, surrounding spaces stripped), a numeric id
  (``-100…`` included) comes back as an ``int``, everything else —
  free text, an empty/blank answer, a non-string — is ``None``.
  N3: a digit string longer than CPython's int-conversion limit
  (>4300 digits) must ALSO answer ``None`` — ``int()`` raising a
  ValueError there may never escape the parser.

RED phase: ``bot.models.Pair`` and the ``bot.chats`` DML helpers
(``add_pair`` / ``remove_pair`` / ``refresh`` / ``parse_chat_ref``)
do not exist yet, so every test below fails INSIDE its body
(``ImportError`` / ``AttributeError``) — never at collection time:
the ``bot.*`` imports are lazy, exactly like the ``chats_mod()``
helper of ``tests/test_chats.py``.
"""

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select


def chats_mod():
    """The ``bot.chats`` module, imported at test time.

    The import lives inside this function on purpose: at top level a
    missing module would abort the collection of the whole pytest run
    and would be classified as third-party by ruff, flipping ``I001``
    once the file exists.
    """
    import bot.chats as chats_module

    return chats_module


def registry():
    """The shared registry singleton the handlers ask."""
    return chats_mod().chats


async def stored_pairs() -> list:
    """Every ``Pair`` row currently in the database (order by insertion)."""
    from bot.database import session
    from bot.models import Pair

    async with session() as db:
        return list((await db.execute(select(Pair).order_by(Pair.id))).scalars())


async def stored_count() -> int:
    """How many pairs the table holds right now."""
    return len(await stored_pairs())


class TestAddPair:
    """``add_pair(source, target)``: INSERT + cache refresh, ref round-trip."""

    async def test_added_pairs_are_visible_through_the_cache(self):
        """Both ref forms land in the table and read back through the cache.

        ``add_pair`` returns ``True`` and refreshes on success, so the
        pair is immediately visible to the synchronous lookups — the
        numeric TEXT columns included (``-100111`` reads back as the
        int ``-100111``, not as a string).
        """
        reg = registry()

        assert await reg.add_pair("@src", "@tgt") is True, "a new username pair is accepted"
        assert await reg.add_pair(-100111, -100222) is True, "a new id pair is accepted"
        assert reg.target_for("@src") == "@tgt", (
            "add_pair must refresh the cache — the pair has to be readable right away"
        )
        assert reg.target_for(-100111) == -100222, (
            "a numeric ref stored in a TEXT column must read back as the same int"
        )

    async def test_repeated_pair_is_refused_and_stored_once(self):
        """The same pair twice → ``False`` and exactly one row (DB uniqueness)."""
        reg = registry()
        assert await reg.add_pair(-100111, -100222) is True, "sanity: the first insert lands"

        assert await reg.add_pair(-100111, -100222) is False, (
            "the UNIQUE (source_ref, target_ref) constraint must refuse the repeat "
            "instead of raising"
        )
        assert await stored_count() == 1, "the refused repeat must leave a single row"
        assert reg.target_for(-100111) == -100222, "the stored pair must stay visible"

    async def test_database_change_is_visible_only_after_refresh(self):
        """The cache freezes until ``refresh()``; the database does not."""
        from bot.database import session
        from bot.models import Pair

        reg = registry()
        async with session() as db:
            db.add(Pair(source_ref="@direct", target_ref="@direct-target"))

        assert reg.target_for("@direct") is None, (
            "a row inserted behind the registry's back must NOT show up before "
            "refresh() — until then the cache stays as it was"
        )
        await reg.refresh()
        assert reg.target_for("@direct") == "@direct-target", (
            "refresh() must re-SELECT the pairs into the cache"
        )


class TestSelfPairRefused:
    """A self-referential pair is never stored, in ANY recorded form.

    ``source == target`` (in either recorded form: int id vs its
    numeric string, username letter-case) would make a source chat its
    own target — the bot would build a thread inside the very chat it
    moves messages out of. ``add_pair`` refuses such a pair with
    ``False`` using the form-independent comparison of ``bot.chats``
    (``_same_ref``/``_chat_refs``), so the table never holds it and no
    lookup can ever see it (security review M4 + F4).
    """

    @pytest.mark.parametrize(
        "source,target",
        [
            pytest.param("@loop", "@loop", id="username-verbatim"),
            pytest.param(-100666, -100666, id="integer-id"),
            pytest.param(-100666, "-100666", id="int-id-vs-numeric-string"),
            pytest.param("@MyChat", "@mychat", id="username-letter-case"),
        ],
    )
    async def test_self_pair_is_refused_and_never_stored(self, source, target):
        """``False``, zero rows, and every lookup keeps missing for it."""
        reg = registry()

        assert await reg.add_pair(source, target) is False, (
            f"a self-referential pair {source!r} → {target!r} must be refused, not stored"
        )
        assert await stored_count() == 0, "a refused self-pair must not reach the table"
        assert reg.target_for(source) is None, "a self-pair must never resolve a target"
        assert chats_mod().is_source(source) is False, (
            "a self-pair must not make the buffering watch it"
        )
        assert chats_mod().is_configured_target(target) is False, (
            "a self-pair must not make the watcher watch it"
        )


class TestRemovePair:
    """``remove_pair(pair_id)``: DELETE + refresh, ``True``/``False`` reporting."""

    async def test_removing_an_existing_pair_clears_row_and_cache(self):
        """``True`` for a live id: the row is gone and the cache forgets it."""
        reg = registry()
        assert await reg.add_pair("@src", "@tgt") is True, "sanity: seeded"
        (row,) = await stored_pairs()

        assert await reg.remove_pair(row.id) is True, "an existing id must remove as True"
        assert await stored_count() == 0, "remove_pair must DELETE the row"
        assert reg.target_for("@src") is None, "remove_pair must refresh the cache too"

    async def test_removing_a_missing_pair_returns_false(self):
        """An id without a row — never inserted or already removed — is ``False``."""
        reg = registry()

        assert await reg.remove_pair(999_999) is False, (
            "an id the table never held must return False, not raise"
        )
        assert await reg.add_pair("@src", "@tgt") is True, "sanity: seeded"
        (row,) = await stored_pairs()
        assert await reg.remove_pair(row.id) is True, "sanity: first removal works"
        assert await reg.remove_pair(row.id) is False, (
            "removing the same id again must return False — the row is gone"
        )
        assert await stored_count() == 0, "the table stays empty"
        assert reg.target_for("@src") is None, "the cache stays empty"


class TestParseChatRef:
    """``parse_chat_ref(text)``: the strict answer parser (BRIEF step 2, cycle B).

    The bot asks ``Which chat did you forward from? Reply with
    @username or its id.`` — this parser is the extracted interior of
    the old ``pair_for_source_ref``: it turns an answer into the ref
    the registry is keyed by, or into ``None`` when the answer is not
    a ``@username`` nor a numeric id at all.
    """

    @pytest.mark.parametrize(
        "text,expected",
        [
            pytest.param("@user", "@user", id="username"),
            pytest.param("  @user  ", "@user", id="username-with-surrounding-spaces"),
            pytest.param("@Name", "@Name", id="username-keeps-its-registry-form"),
            pytest.param("-100123", -100123, id="negative-internal-id"),
            pytest.param("123", 123, id="plain-integer-id"),
            pytest.param("  -100111  ", -100111, id="id-with-surrounding-spaces"),
        ],
    )
    async def test_valid_answers_parse_to_the_canonical_ref(self, text, expected):
        """A ``@username`` stays a string, a numeric id becomes an ``int``."""
        assert await chats_mod().parse_chat_ref(text) == expected, (
            f"the answer {text!r} must parse to {expected!r}"
        )

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("", id="empty-answer"),
            pytest.param("   ", id="blank-answer"),
            pytest.param("just a chat name", id="free-text"),
            pytest.param("src", id="username-without-at"),
            pytest.param("@", id="bare-at"),
            pytest.param("@us er", id="username-with-internal-space"),
            pytest.param("12.5", id="not-an-integer"),
            pytest.param("- 100123", id="id-with-internal-space"),
            pytest.param(42, id="non-string-answer"),
        ],
    )
    async def test_invalid_answers_parse_to_none(self, text):
        """Anything that is neither a ``@username`` nor digits → ``None``."""
        assert await chats_mod().parse_chat_ref(text) is None, (
            f"the answer {text!r} must not parse to any ref"
        )

    async def test_an_absurdly_long_digit_answer_parses_to_none(self):
        """N3: more digits than CPython converts → ``None``, never a ValueError.

        ``ID_REF_RE`` happily matches a 5000-digit string, but ``int()``
        refuses it («Exceeds the limit (4300 digits) for integer string
        conversion») — that ValueError must not escape the parser the
        waiting flow (``on_text`` / ``on_pending``) calls: an
        unconvertible answer is simply «not a chat ref», so it answers
        ``settings.invalid_input`` and keeps the step instead of
        crashing the handler.
        """
        assert await chats_mod().parse_chat_ref("5" * 5000) is None, (
            "a digit string beyond the int-conversion limit must read as "
            "«no chat ref», not raise out of parse_chat_ref"
        )


class TestConcurrentRegistryWrites:
    """M-2: a refresh() racing remove_pair() may never resurrect the row."""

    async def test_a_refresh_reading_before_the_delete_never_wins_the_cache(self, monkeypatch):
        """The refresh reads the row, the removal lands, the cache must follow.

        The slow reader is parked BETWEEN its committed read and its
        cache assignment (the exact lost-update window), so the removal
        runs inside it: whatever the fix does with the ordering, the
        final cache has to mirror the table.
        """
        reg = registry()
        assert await reg.add_pair("@src", "@tgt") is True, "sanity: seeded"
        (row,) = await stored_pairs()

        started = asyncio.Event()
        gate = asyncio.Event()
        parked: list[bool] = []
        real_session = chats_mod().session

        @asynccontextmanager
        async def parking_session():
            """Park the FIRST session after its commit, before its caller resumes."""
            async with real_session() as db:
                yield db
            if not parked:
                parked.append(True)
                started.set()
                await gate.wait()

        monkeypatch.setattr(chats_mod(), "session", parking_session)

        slow_refresh = asyncio.create_task(reg.refresh())
        await asyncio.wait_for(started.wait(), 5)

        removal = asyncio.create_task(reg.remove_pair(row.id))
        try:
            await asyncio.wait_for(asyncio.shield(removal), 2)
        except asyncio.TimeoutError:
            # The fix serializes the writers: the removal waits for the refresh.
            # asyncio.TimeoutError (not the builtin): until 3.11 wait_for raises
            # a class of its own — on 3.10 they are different exceptions.
            pass
        gate.set()
        await asyncio.wait_for(asyncio.gather(slow_refresh, removal), 5)

        table_ids = sorted(p.id for p in await stored_pairs())
        cache_ids = sorted(pair["id"] for pair in reg.all_pairs())
        assert cache_ids == table_ids, (
            "the cache must mirror the table after a refresh raced a removal"
        )

    async def test_concurrent_writes_never_raise_and_settle_on_the_table(self):
        """A gather of the three write paths is silent — and settles on the table."""
        reg = registry()

        results = await asyncio.wait_for(
            asyncio.gather(
                reg.refresh(),
                reg.add_pair("@src", "@tgt"),
                reg.refresh(),
                return_exceptions=True,
            ),
            10,
        )
        failures = [result for result in results if isinstance(result, BaseException)]
        assert not failures, f"concurrent registry writes must not raise: {failures!r}"

        await reg.refresh()

        table_ids = sorted(p.id for p in await stored_pairs())
        cache_ids = sorted(pair["id"] for pair in reg.all_pairs())
        assert cache_ids == table_ids, (
            "a settling refresh() must hand out a cache that mirrors the table"
        )

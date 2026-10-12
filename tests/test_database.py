"""Database module tests: module ``bot.database`` (item 1, SQLAlchemy).

RED phase: neither ``bot/database.py`` nor ``bot/models.py`` exists
yet — the helpers below import them INSIDE the test bodies on purpose:
a top-level import of a missing module would abort pytest collection
for the whole suite. Every test of this file therefore fails with
``ModuleNotFoundError`` while the collection stays clean.

Specification (BRIEF item 1 — SQLAlchemy + PostgreSQL + Alembic):
- ``configure(url)`` builds the async engine plus an
  ``async_sessionmaker(expire_on_commit=False)``; a
  ``sqlite+aiosqlite:///:memory:`` engine uses ``StaticPool`` — ONE
  shared connection, otherwise every pooled connection would be its
  own EMPTY memory database and one session's writes would be
  invisible to the next; a repeated ``configure()`` DISPOSES the
  previous engine so tests are isolated from each other;
- ``session()`` is an async context manager: commit on success,
  rollback + re-raise on error, close either way;
- ``create_all()`` / ``drop_all()`` run the metadata through
  ``run_sync(Base.metadata.create_all/drop_all)`` and are idempotent
  (test tooling);
- ``check()`` probes the connection (``SELECT 1``) and RAISES when the
  database is unreachable — the startup fail-fast of ``bot.__main__``;
- error hygiene: no exception text produced by the module may embed
  the URL — it carries the password of the production DSN (pinned by
  ``test_check_failure_never_leaks_the_dsn_password`` against
  ``SECRETPW``);
- the model behind it all: ``bot.models.Base`` (``DeclarativeBase``)
  and ``BufferMessage`` of the ``buffer_messages`` table — exercised
  through real rows here, column by column in ``tests/test_buffer.py``
  and structurally in ``tests/test_alembic.py``.

The helpers drive each ``bot.database`` call by awaiting its result
only when there is one to await: the specification pins what every
helper DOES, and only ``session()``'s async-context-manager flavour is
part of the contract (pinned by ``async with`` below).
"""

import inspect
from datetime import datetime

import pytest
from sqlalchemy import BigInteger, Text, select

#: The engine URL of these tests: a fresh shared in-memory database.
TEST_URL = "sqlite+aiosqlite:///:memory:"

#: An unreachable DSN whose password must never surface in an error.
BAD_URL = "postgresql+asyncpg://user:SECRETPW@localhost:1/x"

#: A syntactically broken DSN with an explicit password (configure path).
PIN_URL = "postgresql+asyncpg://user:SECRETPW123@bad-host:xx/db"

#: A parseable DSN with the same password whose first port refuses (check path).
PIN_CHECK_URL = "postgresql+asyncpg://user:SECRETPW123@localhost:1/db"

#: A stored timestamp (the suite convention: naive UTC).
STAMP = datetime(2026, 10, 5, 12, 0, 0)


def database_mod():
    """The ``bot.database`` module, imported at test time (RED-safe)."""
    import bot.database as database_module

    return database_module


def models_mod():
    """The ``bot.models`` module, imported at test time (RED-safe)."""
    import bot.models as models_module

    return models_module


async def drive(value):
    """Await ``value`` only when the call produced an awaitable."""
    if inspect.isawaitable(value):
        return await value
    return value


async def all_rows(models):
    """Every ``BufferMessage`` of the currently configured database."""
    database = database_mod()
    async with database.session() as session:
        result = await session.execute(select(models.BufferMessage))
        return list(result.scalars())


class TestConfigureAndSession:
    """`configure()` + `session()`: engine setup, commit, isolation."""

    async def test_configure_and_session_roundtrip(self):
        """Insert through one session, read it back through another.

        Two DIFFERENT sessions on a ``sqlite+aiosqlite:///:memory:``
        engine only see each other's writes when the engine shares ONE
        connection (StaticPool) — with a normal pool every connection
        would be its own empty memory database and the second session
        would read nothing.
        """
        database = database_mod()
        models = models_mod()
        await drive(database.configure(TEST_URL))
        await drive(database.create_all())

        row = models.BufferMessage(chat_id=1, message_id=2, user_id=3, sent_at=STAMP, text="hello")
        async with database.session() as session:
            session.add(row)
        # The commit must NOT expire the object: expire_on_commit=False.
        assert row.text == "hello", "attributes must stay usable after the commit"

        rows = await all_rows(models)
        assert len(rows) == 1, "the committed row must be visible to the NEXT session"
        stored = rows[0]
        assert stored.chat_id == 1
        assert stored.message_id == 2
        assert stored.user_id == 3
        assert stored.sent_at == STAMP
        assert stored.text == "hello"
        assert stored.created_at is not None, "created_at carries a Python default"

    async def test_repeated_configure_isolates_the_previous_engine(self):
        """A second ``configure()`` starts from an EMPTY database.

        The previous engine is disposed (spec: a repeated configure
        disposes the previous engine) and the new one — even over the
        same ``:memory:`` URL — knows nothing of the old rows: that is
        the isolation every test of the suite relies on.
        """
        database = database_mod()
        models = models_mod()
        await drive(database.configure(TEST_URL))
        await drive(database.create_all())
        async with database.session() as session:
            session.add(models.BufferMessage(chat_id=1, message_id=1, sent_at=STAMP, text="old"))

        await drive(database.configure(TEST_URL))
        await drive(database.create_all())

        assert await all_rows(models) == [], (
            "a repeated configure() must dispose the old engine and hand out "
            "an isolated database"
        )

    async def test_session_rolls_back_on_error_and_propagates_it(self):
        """An exception inside ``session()`` → rollback AND the error escapes."""
        database = database_mod()
        models = models_mod()
        await drive(database.configure(TEST_URL))
        await drive(database.create_all())

        with pytest.raises(ValueError, match="boom"):
            async with database.session() as session:
                session.add(models.BufferMessage(chat_id=1, message_id=1, sent_at=STAMP))
                await session.flush()  # the INSERT is really on the wire
                raise ValueError("boom")

        assert await all_rows(models) == [], "the failed transaction must be rolled back"


class TestCheck:
    """`check()`: the startup probe of the database connection."""

    async def test_check_passes_on_a_reachable_database(self):
        """``SELECT 1`` on a live engine → no exception."""
        database = database_mod()
        await drive(database.configure(TEST_URL))
        await drive(database.create_all())

        await drive(database.check())  # must not raise

    async def test_check_failure_never_leaks_the_dsn_password(self):
        """An unreachable DSN → an exception whose text hides the URL.

        Port 1 on localhost refuses the connection instantly, and
        neither ``str`` nor ``repr`` of the raised error may contain
        the password of the DSN (nor the URL it comes from) — a
        credential must never end up in a log line.
        """
        database = database_mod()
        await drive(database.configure(BAD_URL))
        error: Exception | None = None
        try:
            await drive(database.check())
        except Exception as caught:  # the failure itself is the point
            error = caught
        finally:
            # The shared conftest teardown drives drop_all()/dispose()
            # over the CURRENT engine: put a healthy in-memory one back
            # so the teardown can never hang on the unreachable DSN.
            await drive(database.configure(TEST_URL))

        assert error is not None, "check() must raise when the database is unreachable"
        assert "SECRETPW" not in str(error), f"the password leaked into str(): {error!s}"
        assert "SECRETPW" not in repr(error), f"the password leaked into repr(): {error!r}"


class TestSchemaHelpers:
    """`create_all()` / `drop_all()`: the test tooling behind the fixtures."""

    async def test_create_all_and_drop_all_are_idempotent(self):
        """Creating an existing schema and dropping an absent one are no-ops."""
        database = database_mod()
        models = models_mod()
        await drive(database.configure(TEST_URL))

        await drive(database.create_all())
        await drive(database.create_all())  # already there → a plain no-op
        await drive(database.drop_all())
        await drive(database.drop_all())  # already gone → a plain no-op
        await drive(database.create_all())  # and the schema comes back

        async with database.session() as session:
            session.add(models.BufferMessage(chat_id=5, message_id=6, sent_at=STAMP))
        assert len(await all_rows(models)) == 1, "the recreated schema accepts rows again"


class TestTelegramIdsAre64Bit:
    """Telegram ids exceed int32 — the columns MUST be BIGINT.

    sqlite accepts oversized integers into INTEGER columns, so the
    live PostgreSQL overflow (``value out of int32 range`` on every
    ``record()`` with a ``-100…`` chat id) is only catchable
    structurally: migration ``0003_buffer_id_bigints`` widened
    ``chat_id``/``message_id``/``user_id``, and this pins the models.
    """

    async def test_buffer_message_id_columns_are_bigint(self):
        """chat_id, message_id and user_id are BigInteger columns."""
        models = models_mod()
        columns = models.BufferMessage.__table__.columns

        for name in ("chat_id", "message_id", "user_id"):
            assert isinstance(columns[name].type, BigInteger), (
                f"{name} must be BIGINT (Telegram ids like -1004327410333 "
                f"overflow PostgreSQL integer): {columns[name].type!r}"
            )

    async def test_pair_refs_stay_text(self):
        """``-100…`` chat ids and @usernames are stored as TEXT, not ints."""
        models = models_mod()
        columns = models.Pair.__table__.columns

        for name in ("source_ref", "target_ref"):
            assert isinstance(columns[name].type, Text), (
                f"{name} must be TEXT — it holds either an id or an @username"
            )


def error_chain_texts(error: Exception) -> list[str]:
    """``str()`` of ``error`` and of every ``__cause__`` / ``__context__`` node.

    DSN hygiene has to hold for the WHOLE chain a caller may print
    (``raise ... from e`` keeps the original reachable), so the walker
    follows both links and guards against reference cycles.
    """
    texts: list[str] = []
    seen: set[int] = set()
    queue: list[Exception | None] = [error]
    while queue:
        node = queue.pop()
        if node is None or id(node) in seen:
            continue
        seen.add(id(node))
        texts.append(str(node))
        queue.append(node.__cause__)
        queue.append(node.__context__)
    return texts


class TestConfigureErrorHygiene:
    """L-4: no exception escaping the engine paths may carry the DSN."""

    async def test_a_broken_dsn_never_leaks_its_password(self):
        """The configure path raises — and nothing in the chain names the DSN."""
        database = database_mod()
        error: Exception | None = None
        try:
            await drive(database.configure(PIN_URL))
        except Exception as caught:  # the failure itself is the point
            error = caught
        finally:
            # The shared conftest teardown drives drop_all()/dispose() over
            # the CURRENT engine: put a healthy in-memory one back.
            await drive(database.configure(TEST_URL))

        assert error is not None, "an unusable DSN must surface as an exception"
        for text in error_chain_texts(error):
            assert "SECRETPW" not in text, f"the password leaked: {text!r}"
            assert "user:" not in text, f"the DSN credentials leaked: {text!r}"

    async def test_a_failed_check_never_leaks_the_chain(self):
        """The probe fails — its own text AND the attached cause stay clean."""
        database = database_mod()
        error: Exception | None = None
        try:
            await drive(database.configure(PIN_CHECK_URL))
            await drive(database.check())
        except Exception as caught:  # the failure itself is the point
            error = caught
        finally:
            await drive(database.configure(TEST_URL))

        assert error is not None, "check() must raise on an unreachable database"
        texts = error_chain_texts(error)
        assert len(texts) > 1, "the original error stays attached as __cause__"
        for text in texts:
            assert "SECRETPW" not in text, f"the password leaked: {text!r}"
            assert "user:" not in text, f"the DSN credentials leaked: {text!r}"

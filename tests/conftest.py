"""Shared fixtures of the test suite.

Two pieces of per-test state must not leak between tests:

1. The i18n module (backlog item 5) keeps the CURRENT locale in module
   state, so a test that switches the locale would poison every test
   running after it. The autouse fixture below returns the state to the
   default ``en`` pack after EVERY test.

2. The database-backed source buffer (item 3, ``bot.database`` /
   ``bot.models``) talks to whatever engine ``configure()`` installed.
   The autouse fixture below configures a FRESH
   ``sqlite+aiosqlite:///:memory:`` database (StaticPool — one shared
   connection, otherwise every pooled connection would be its own
   empty memory database) and creates the schema for EVERY test, then
   drops the schema and disposes the engine. Tests therefore never see
   each other's rows, and a test that calls ``configure()`` itself
   (engine-isolation checks) starts from a clean slate either way.

   Since cycle A the pair registry caches its pairs IN MEMORY over the
   process, so right after the fresh database is installed the cache
   is reloaded too (``chats.refresh()``) — pairs seeded by one test
   can never leak into the next.

3. The shared admin cache (item 2, ``bot.admin_cache``) is a process-wide
   TTL cache of chat-administrator lookups. A test that warms it up must
   not hand its cached answers to the next test, so the autouse fixture
   below clears it before and after EVERY test.

All fixtures tolerate the RED phase: while ``bot/i18n.py``,
``bot/database.py`` or ``bot.admin_cache`` do not exist, there is
nothing to set up or to restore, and a test failing because of a
missing module must not fail a second time in teardown.
"""

import inspect

import pytest

#: The per-test database: a fresh in-memory file for every single test.
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


async def drive(value):
    """Return ``await value`` when there is something to await.

    The specification pins what each ``bot.database`` helper DOES
    (engine + StaticPool, schema creation, ``SELECT 1`` probe, …) but
    not the sync/async flavour of every helper except ``session()``
    (an async context manager) — so the fixtures drive a call by
    awaiting its result only when the call returned an awaitable.
    """
    if inspect.isawaitable(value):
        return await value
    return value


async def reload_pair_cache() -> None:
    """Reload the pair-registry cache from the fresh (empty) database.

    ``bot.chats`` keeps the pairs in memory over the process (cycle A:
    the database is the source of truth, ``refresh()`` re-SELECTs into
    the cache), so every test starts with the cache pointed at ITS OWN
    fresh database.

    Tolerates the RED phase: while ``bot.chats`` has no ``refresh()``
    yet, there is no cache to reload — the current code has no state
    at all — so there is nothing to do here.
    """
    try:
        import bot.chats as chats_module
    except ModuleNotFoundError:
        return
    refresh = getattr(chats_module.chats, "refresh", None)
    if refresh is None:
        return
    await drive(refresh())


@pytest.fixture(autouse=True)
async def fresh_database():
    """Configure a brand-new in-memory database around EVERY test."""
    try:
        import bot.database as database_module
    except ModuleNotFoundError:
        # RED phase: bot/database.py (or bot/models.py) does not exist
        # yet — the DB tests fail on their own lazy imports; the
        # teardown must not turn that into a second, collateral error.
        yield
        return
    await drive(database_module.configure(TEST_DATABASE_URL))
    await drive(database_module.create_all())
    await drive(reload_pair_cache())
    yield
    await drive(database_module.drop_all())
    await drive(database_module.dispose())


@pytest.fixture(autouse=True)
def restore_default_locale():
    """Return the ``bot.i18n`` state to the default ``en`` pack after each test."""
    yield
    try:
        import bot.i18n as i18n_module
    except ModuleNotFoundError:
        return  # RED phase: bot/i18n.py does not exist yet — nothing to restore
    try:
        i18n_module.set_locale("en")
    except FileNotFoundError:
        # A missing en.json is reported by the pack tests themselves; the
        # teardown must not turn their failure into collateral damage for
        # the OTHER tests of the suite.
        pass


@pytest.fixture(autouse=True)
def reset_admin_cache():
    """Start (and end) every test with an EMPTY shared admin cache.

    Item 2 moves the chat-administrator lookups into a process-wide TTL
    cache in ``bot.admin_cache``; without a per-test reset one test's
    cached answer would silently satisfy (or fail) another test's
    ``can_manage`` / bot-rights assertions.

    Tolerates the RED phase: while ``bot/admin_cache.py`` does not
    exist yet there is nothing to clear — the cache tests fail on their
    own imports instead.
    """
    reset = _admin_cache_reset()
    if reset is None:
        yield
        return
    reset()
    yield
    reset()


def _admin_cache_reset():
    """Return a callable clearing the admin cache, or ``None`` if absent."""
    try:
        import bot.admin_cache as admin_cache
    except ModuleNotFoundError:
        return None  # RED phase: bot/admin_cache.py does not exist yet
    for name in ("clear", "reset"):
        reset = getattr(admin_cache, name, None)
        if callable(reset):
            return reset
    return None

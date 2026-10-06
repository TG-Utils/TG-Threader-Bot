"""Application entry point tests: module ``bot.__main__``.

RED phase: module ``bot.__main__`` does not exist yet, so every test
here must fail with ``ModuleNotFoundError``.

Specification (wiki, General-idea; settings are read from the
environment/.env):
- without ``BOT_TOKEN`` set, ``asyncio.run(run())`` terminates with
  ``SystemExit`` and the message names the ``BOT_TOKEN`` variable — the
  bot must not start with an empty token;
- ``run()`` is an async coroutine (otherwise ``asyncio.run`` is
  impossible);
- ``main()`` is a synchronous wrapper that runs ``run()`` precisely via
  ``asyncio.run``;
- at startup ``set_locale(settings.locale)`` runs BEFORE the dispatcher
  (i18n, backlog item 5) — see ``test_startup_sets_locale_before_the_dispatcher``;
- the database initialisation (item 1) runs after the locale and
  BEFORE ``Bot()``/the dispatcher: ``configure(settings.database_url)``
  + ``await check()``; an empty ``DATABASE_URL`` aborts with a
  ``RuntimeError`` naming the variable — see
  ``test_run_without_database_url_fails_fast_naming_the_variable``;
- right after a SUCCESSFUL ``check()`` (and only then) the pair
  registry reloads its cache from the database — ``await
  chats.refresh()`` — so the running bot starts with the pairs of the
  database, never with a stale cache (cycle A):
  ``locale → database → chats → dispatcher``;
- logging (N2): ``_configure_logging`` puts the level on the ROOT
  logger AND leaves it with at least one handler — a root logger
  without handlers swallows every record below WARNING (``lastResort``)
  — and it re-applies the level on EVERY call, so a later call is not
  a ``basicConfig`` no-op (``test_configure_logging_installs_a_handler_and_reapplies_the_level``).
"""

import asyncio
import inspect
import logging
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.__main__ import main, run
from bot.config import settings


def test_run_is_coroutine_function():
    """``run()`` is an async function: it is launched by ``asyncio.run``."""
    assert inspect.iscoroutinefunction(run), "run() must be an async function"


def test_run_without_bot_token_exits_naming_bot_token(monkeypatch: pytest.MonkeyPatch):
    """Without ``BOT_TOKEN`` → ``SystemExit`` naming the variable in the message."""
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.setattr(settings, "bot_token", "")

    with pytest.raises(SystemExit) as exc_info:
        asyncio.run(run())

    assert "BOT_TOKEN" in str(
        exc_info.value
    ), "the missing-token message must name the BOT_TOKEN variable"


def test_main_wraps_run_in_asyncio_run(monkeypatch: pytest.MonkeyPatch):
    """``main()`` calls ``run()`` and wraps it in ``asyncio.run``."""
    fake_run = AsyncMock(name="run")
    monkeypatch.setattr("bot.__main__.run", fake_run)

    captured: dict = {}

    def fake_asyncio_run(coro):
        captured["coro"] = coro
        coro.close()  # nobody awaits the coroutine — silence the RuntimeWarning
        return None

    monkeypatch.setattr(asyncio, "run", fake_asyncio_run)

    main()

    assert fake_run.call_count == 1, "main() must call run() exactly once"
    assert "coro" in captured, "main() must launch run() through asyncio.run()"
    assert asyncio.iscoroutine(
        captured["coro"]
    ), "asyncio.run must receive the coroutine returned by run()"


def test_startup_sets_locale_before_the_dispatcher(monkeypatch: pytest.MonkeyPatch):
    """``set_locale`` → database init → registry refresh → dispatcher, in THAT order.

    i18n (backlog item 5): the locale pack must be applied before any
    dispatcher work. Item 1 (DB): the database is configured and
    probed (``configure(settings.database_url)`` + ``check()``) after
    the locale and BEFORE ``Bot()``/the dispatcher — a broken DSN must
    fail the startup before anything Telegram-facing is built. Cycle A
    (pairs): right after a SUCCESSFUL ``check()`` the pair registry
    reloads its cache (``await chats.refresh()``) — the expected event
    list pins the full order ``[locale → database → chats →
    dispatcher]``, the ``chats`` step sitting strictly after the
    database step.

    The spies cover BOTH possible bindings of every collaboration — the
    module attribute and a name imported straight into
    ``bot.__main__`` — so the assertion pins the startup ORDER, not the
    import style: for the registry that means the singleton method
    (``bot.chats.chats.refresh``), a module-level ``bot.chats.refresh``
    and a ``refresh``/``chats`` name bound inside ``bot.__main__`` all
    lead to the SAME spy. ``run()`` itself executes for real, with
    every collaboration it needs (Bot, dispatcher, database) mocked out.

    RED phase (cycle A): neither ``bot.chats.chats.refresh`` nor a
    module-level ``bot.chats.refresh`` exists yet, and ``run()`` never
    calls one — the spies are installed with ``raising=False`` and the
    test fails on the event list (``AssertionError`` naming the
    missing ``chats`` step), never at collection.
    """
    import bot.chats
    import bot.i18n

    events: list[str] = []

    def spy_set_locale(locale, directory=None):
        events.append(f"locale:{locale}")

    configured_urls: list[str] = []

    def spy_configure(url):
        events.append("database")
        configured_urls.append(url)

    async def spy_refresh():
        events.append("chats")

    fake_check = AsyncMock(name="check")
    fake_session = SimpleNamespace(close=AsyncMock(name="close"))

    def fake_bot(*args, **kwargs):
        return SimpleNamespace(session=fake_session)

    fake_dispatcher = SimpleNamespace(start_polling=AsyncMock(name="start_polling"))

    def fake_create_dispatcher(*args, **kwargs):
        events.append("dispatcher")
        return fake_dispatcher

    # One import statement only (phase-stable for isort); everything else
    # is patched through string targets. The spy covers BOTH bindings of
    # every startup collaboration: the module attribute
    # (``i18n.set_locale`` / ``database.configure`` / ``database.check``)
    # and a name imported straight into ``bot.__main__`` via
    # ``from bot.… import``. The registry refresh is pinned on all three
    # plausible shapes: the singleton method, a module-level function
    # and a name bound inside ``bot.__main__``.
    monkeypatch.setattr(bot.i18n, "set_locale", spy_set_locale)
    monkeypatch.setattr("bot.__main__.set_locale", spy_set_locale, raising=False)
    monkeypatch.setattr("bot.database.configure", spy_configure)
    monkeypatch.setattr("bot.__main__.configure", spy_configure, raising=False)
    monkeypatch.setattr("bot.database.check", fake_check)
    monkeypatch.setattr("bot.__main__.check", fake_check, raising=False)
    monkeypatch.setattr(bot.chats.chats, "refresh", spy_refresh, raising=False)
    monkeypatch.setattr(bot.chats, "refresh", spy_refresh, raising=False)
    monkeypatch.setattr("bot.__main__.refresh", spy_refresh, raising=False)
    monkeypatch.setattr("bot.__main__.chats", bot.chats.chats, raising=False)
    monkeypatch.setattr("bot.dispatcher.create_dispatcher", fake_create_dispatcher)
    monkeypatch.setattr("bot.__main__.create_dispatcher", fake_create_dispatcher, raising=False)
    monkeypatch.setattr("bot.__main__.Bot", fake_bot)
    monkeypatch.setattr(settings, "bot_token", "42:TESTTOKEN")
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://bot@localhost/tg")

    main()

    assert events == [f"locale:{settings.locale}", "database", "chats", "dispatcher"], (
        "the startup must run set_locale(settings.locale), then the database "
        f"(configure + check), then chats.refresh(), then create_dispatcher — got {events!r}"
    )
    assert configured_urls == [settings.database_url], (
        "configure() must receive exactly settings.database_url, "
        f"got {configured_urls!r}"
    )
    assert fake_check.await_count == 1, "check() must be AWAITED exactly once at startup"


def test_run_without_database_url_fails_fast_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch,
):
    """An empty ``DATABASE_URL`` → ``RuntimeError`` BEFORE ``Bot()``/dispatcher.

    Item 1 (fail-fast): the database initialisation runs right after
    ``set_locale(settings.locale)``; with nothing configured it must
    abort with a RuntimeError naming ``DATABASE_URL`` (EN text, no URL
    value inside the message), and neither ``Bot()`` nor the
    dispatcher may be constructed on the way down.

    RED phase: ``run()`` has no database step yet — it proceeds to
    ``Bot()``/polling and the test fails with ``DID NOT RAISE``.
    """
    started: list[str] = []

    def fake_bot(*args, **kwargs):
        started.append("Bot")
        return SimpleNamespace(session=SimpleNamespace(close=AsyncMock(name="close")))

    def fake_create_dispatcher(*args, **kwargs):
        started.append("dispatcher")
        return SimpleNamespace(start_polling=AsyncMock(name="start_polling"))

    monkeypatch.setattr("bot.__main__.Bot", fake_bot)
    monkeypatch.setattr("bot.__main__.create_dispatcher", fake_create_dispatcher, raising=False)
    monkeypatch.setattr(settings, "bot_token", "42:TESTTOKEN")
    monkeypatch.setattr(settings, "database_url", "")

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(run())

    assert "DATABASE_URL is not configured." in str(exc_info.value), (
        "the message must name the DATABASE_URL variable and say it is not "
        f"configured, got {str(exc_info.value)!r}"
    )
    assert started == [], (
        "the database check must fail fast BEFORE Bot() and the dispatcher "
        f"are built, got {started!r}"
    )


def test_configure_logging_applies_the_configured_level():
    """I-2: ``_configure_logging`` puts the settings level on the ROOT logger.

    The helper is imported lazily so a missing one fails inside this
    body (RED phase) instead of breaking the collection of the module,
    and the root logger level is restored afterwards — the suite owns it.
    """
    from bot.__main__ import _configure_logging

    root = logging.getLogger()
    previous = root.level
    try:
        _configure_logging(SimpleNamespace(log_level="DEBUG"))
        assert root.level == logging.DEBUG, "DEBUG must reach the root logger"
        _configure_logging(SimpleNamespace(log_level="INFO"))
        assert root.level == logging.INFO, "and INFO must reach it too"
    finally:
        root.setLevel(previous)


def test_configure_logging_installs_a_handler_and_reapplies_the_level():
    """I-2 follow-up (N2): below-WARNING records must not be swallowed.

    ``_configure_logging`` used to call ``setLevel`` alone: a root
    logger without a handler of its own has nothing to emit to, so
    INFO/DEBUG disappear (``lastResort`` only shows WARNING). Two pins:

    - starting from a root logger with NO handler (a fresh process),
      one call installs at least one handler beside the level;
    - the level is applied on EVERY call — the second call with INFO
      must land even though handlers exist by then, i.e. the fix is
      not a bare ``logging.basicConfig()`` (a no-op while the root
      logger already carries handlers).

    The root logger state is restored afterwards — the suite owns it.
    """
    from bot.__main__ import _configure_logging

    root = logging.getLogger()
    previous_level = root.level
    previous_handlers = root.handlers[:]
    root.handlers.clear()
    try:
        _configure_logging(SimpleNamespace(log_level="DEBUG"))
        assert root.level == logging.DEBUG, "DEBUG must reach the root logger"
        assert root.handlers, (
            "without a handler of its own the root logger drops every record "
            "below WARNING on the floor"
        )

        _configure_logging(SimpleNamespace(log_level="INFO"))
        assert root.level == logging.INFO, (
            "the level must be applied on EVERY call, handler or no handler"
        )
        assert root.handlers, "and the second call must not cost the handler either"
    finally:
        root.handlers.extend(previous_handlers)
        root.setLevel(previous_level)


def test_python_m_bot_actually_runs_the_application():
    """``python -m bot`` reaches ``main()`` — the module entry guard.

    ``bot/__main__.py`` documents itself as the ``python -m bot`` entry
    point: running the module must EXECUTE the application, not merely
    define ``main()`` and exit silently. The subprocess proves it by
    behaviour: an empty ``BOT_TOKEN`` (env wins over ``.env``) aborts
    inside ``run()`` with the token message and a non-zero exit code —
    a module without the guard would exit 0 with an empty stderr.
    """
    env = {**os.environ, "BOT_TOKEN": ""}
    proc = subprocess.run(
        [sys.executable, "-m", "bot"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert proc.returncode != 0, (
        "python -m bot must invoke main(), not just import the module"
    )
    assert "BOT_TOKEN is not set" in proc.stderr, (
        f"expected the token refusal on stderr, got: {proc.stderr[-400:]!r}"
    )

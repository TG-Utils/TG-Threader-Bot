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
  ``asyncio.run``.
"""

import asyncio
import inspect
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

"""Application entry point (``python -m bot``).

``main()`` is the synchronous wrapper for scripts and console launches;
``run()`` is the async coroutine that validates configuration, applies
the configured locale pack, fails fast on the database (item 1),
reloads the pair registry cache from the database (cycle A) and
starts Telegram long polling.
"""

import asyncio
import inspect
import logging

from aiogram import Bot

from bot.chats import chats
from bot.config import settings
from bot.database import check, configure
from bot.dispatcher import create_dispatcher
from bot.i18n import set_locale


def _configure_logging(config: object) -> None:
    """Put ``config.log_level`` on the ROOT logger and give it a handler (I-2 + N2).

    Runs before ``Bot()`` exists, so even the startup messages of the
    very first steps honour the configured level. The level name is
    already normalised by ``Settings``; a name ``logging`` does not
    know is a configuration error, never a silent fallback.

    ``logging.basicConfig`` installs a handler only while the root
    logger has none (a fresh process), so the level is ALSO re-applied
    explicitly on every call — otherwise a second call would be a
    no-op behind the handlers of the first one — and the root logger
    is guaranteed to carry at least one handler afterwards: without it
    every record below WARNING would hit ``lastResort`` and vanish.
    """
    level_name = getattr(config, "log_level", "INFO")
    level: object = None
    if isinstance(level_name, int):
        level = level_name
    elif isinstance(level_name, str):
        level = getattr(logging, level_name, None)
    if not isinstance(level, int):
        raise ValueError(f"unknown log level: {level_name!r}")
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=level,
    )
    logging.getLogger().setLevel(level)


async def run() -> None:
    """Start the bot with Telegram long polling until interrupted."""
    token = settings.bot_token
    if not token:
        raise SystemExit(
            "BOT_TOKEN is not set: export the BOT_TOKEN environment variable "
            "before starting the bot."
        )

    # The locale pack must be in place BEFORE any dispatcher work, so
    # every reply of the running bot is rendered from the configured one.
    set_locale(settings.locale)

    # Fail fast (item 1): the database must be configured and reachable
    # BEFORE Bot()/the dispatcher are built — an empty DSN aborts with a
    # RuntimeError naming the variable, and a broken one aborts right
    # after, while nothing Telegram-facing exists yet. The error text
    # never carries the DSN itself (it embeds the database password).
    if not settings.database_url:
        raise RuntimeError(
            "DATABASE_URL is not configured. Set the DATABASE_URL environment "
            "variable (or add it to .env) before starting the bot."
        )
    configured = configure(settings.database_url)
    if inspect.isawaitable(configured):
        # ``configure`` may be bound to a plain function; only an
        # awaitable result needs awaiting before the probe below.
        await configured
    await check()

    # Cycle A: the pairs live in the database, so the registry cache
    # reloads right after a SUCCESSFUL probe — only then, and before
    # anything Telegram-facing exists — the startup order is
    # locale → database → chats → dispatcher.
    await chats.refresh()

    bot = Bot(token=token)
    dispatcher = create_dispatcher()
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()


def main() -> None:
    """Configure logging from the settings, then run ``run()``."""
    _configure_logging(settings)
    asyncio.run(run())


if __name__ == "__main__":
    # ``python -m bot`` executes this module as ``__main__``: without
    # this guard the run would define ``main()`` and exit silently.
    main()

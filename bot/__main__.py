"""Application entry point (``python -m bot``).

``main()`` is the synchronous wrapper for scripts and console launches;
``run()`` is the async coroutine that validates configuration and starts
Telegram long polling.
"""

import asyncio

from aiogram import Bot

from bot.config import settings
from bot.dispatcher import create_dispatcher


async def run() -> None:
    """Start the bot with Telegram long polling until interrupted."""
    token = settings.bot_token
    if not token:
        raise SystemExit(
            "BOT_TOKEN is not set: export the BOT_TOKEN environment variable "
            "before starting the bot."
        )

    bot = Bot(token=token)
    dispatcher = create_dispatcher()
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()


def main() -> None:
    """Run the ``run()`` coroutine from synchronous code."""
    asyncio.run(run())

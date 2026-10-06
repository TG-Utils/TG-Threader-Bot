"""Dispatcher factory: assembles the aiogram dispatcher from routers.

``create_dispatcher()`` is the assembly point of the bot from which
polling starts. Routers are created inside the factory because a
``Router`` instance can be attached to only one parent dispatcher.

Router order: settings → watcher → buffering. The settings router owns
the private chats (the ``/settings`` menu and its per-chat flow) and
consumes those events first, then the watcher consumes everything of
the configured threaded chats (forwards and pending-state texts) before
the buffering router could ever see those events; the buffering router
records the source chat's messages — the base for the step-5 cleanup.
Neither the build nor the router tree touches the network or needs
``BOT_TOKEN``.
"""

from aiogram import Dispatcher

from bot.handlers.buffering import create_router as create_buffering_router
from bot.handlers.settings import create_router as create_settings_router
from bot.handlers.watcher import create_router as create_watcher_router


def create_dispatcher() -> Dispatcher:
    """Assemble a new dispatcher with the three routers attached.

    The build is self-contained: neither ``BOT_TOKEN`` nor network access
    is required — the token is consumed later, when ``Bot`` is created
    for polling.
    """
    dispatcher = Dispatcher()
    dispatcher.include_router(create_settings_router())
    dispatcher.include_router(create_watcher_router())
    dispatcher.include_router(create_buffering_router())
    return dispatcher

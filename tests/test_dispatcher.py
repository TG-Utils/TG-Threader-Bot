"""aiogram dispatcher tests: module ``bot.dispatcher`` (BRIEF v3, §3/§5).

v3 removes every command/live-mode router: the dispatcher is exactly
``watcher → buffering``.

Specification (BRIEF.md v3):
- ``create_dispatcher()`` returns an ``aiogram.Dispatcher`` — the
  assembly point from which polling starts;
- assembling needs neither ``BOT_TOKEN`` nor network access;
- the router tree carries exactly the two v3 routers, watcher FIRST:
  the watcher's text-with-session handler and its forwards in the
  threaded chat must consume those events before the buffering router
  could ever see them (the chats do not overlap, so the order plus the
  filters decide);
- no command routers are wired anywhere: ``/thread``/``/target`` and
  the v2 live-mode handlers (``bot.handlers.commands`` /
  ``tracking`` / ``cleanup``) are gone with the v2 flow — no
  ``Command`` filter survives in the tree;
- behaviour: an admin forwarding a batch into a configured threaded
  chat creates a pending session while the message runs through the
  real router pipeline (first handler whose filters pass consumes it),
  and that forward must NOT be buffered.

RED phase (iteration C): ``bot.handlers.watcher`` does not exist — the
behaviour test fails with ``ModuleNotFoundError`` on its lazy import;
the structural tests fail on their assertions while the v2 routers are
still wired in. The two assembly tests of ``create_dispatcher()`` stay
green in every phase.
"""

import json
import socket
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Dispatcher
from aiogram.filters import Command

from bot.config import settings
from bot.dispatcher import create_dispatcher

#: The two routers of the v3 dispatcher, in order.
WATCHER_MODULE = "bot.handlers.watcher"
BUFFERING_MODULE = "bot.handlers.buffering"

#: Modules of the removed v2 flows — none of them may appear in the tree.
DEAD_MODULES = (
    "bot.handlers.commands",
    "bot.handlers.tracking",
    "bot.handlers.cleanup",
)

#: The registry the behaviour tests run against: one source→target pair.
PAIRS = [{"source": -100111, "target": "@forumgroup"}]
TARGET_CHAT_ID = -100666
TARGET_USERNAME = "forumgroup"
SOURCE_USERNAME = "srcgroup"
ADMIN_ID = 501

#: The forward's origin: the configured source chat of the pair.
ORIGIN_DATE = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def configured_cwd(tmp_path: Path, monkeypatch):
    """Run every test in a cwd whose ``chats.json`` holds ``PAIRS``.

    Pure file operations on purpose: while ``bot.sessions`` /
    ``bot.handlers.watcher`` do not exist (RED), this fixture must not
    fail — only the tests themselves do. The shared pending-session
    store is reset as well (it persists across tests by design).
    """
    monkeypatch.chdir(tmp_path)
    Path.cwd().joinpath("chats.json").write_text(
        json.dumps({"pairs": PAIRS}), encoding="utf-8"
    )
    try:
        import bot.sessions as sessions_module
    except ModuleNotFoundError:
        sessions_module = None
    if sessions_module is not None:
        sessions_module.sessions.reset(TARGET_CHAT_ID)


def iter_routers(root):
    """Walk the router tree: root plus every sub-router (``sub_routers``)."""
    yield root
    for sub_router in root.sub_routers:
        yield from iter_routers(sub_router)


def router_index_for_module(root, module_name: str) -> int | None:
    """Index in ``iter_routers`` order of the first router carrying a handler
    whose callback is defined in ``module_name`` (``None`` when absent)."""
    for index, router in enumerate(iter_routers(root)):
        if any(
            getattr(handler.callback, "__module__", None) == module_name
            for handler in router.message.handlers
        ):
            return index
    return None


def find_module_handler(root, module_name: str):
    """The first handler of the tree defined in ``module_name``.

    Raises a named ``AssertionError`` when the router is not attached —
    the RED failure of these tests is about the missing v3 wiring, never
    about a missing Python module.
    """
    for router in iter_routers(root):
        for handler in router.message.handlers:
            if getattr(handler.callback, "__module__", None) == module_name:
                return handler
    raise AssertionError(
        f"a handler defined in {module_name} must be attached to the dispatcher"
    )


def all_handlers(root):
    """Every message handler of the tree, in registration order."""
    for router in iter_routers(root):
        yield from router.message.handlers


def handler_has_command_filter(handler) -> bool:
    """Does the handler carry an aiogram ``Command`` filter?"""
    for filter_object in handler.filters:
        actual = getattr(filter_object, "callback", filter_object)
        if isinstance(actual, Command):
            return True
    return False


class TestCreateDispatcher:
    """The ``create_dispatcher()`` factory."""

    def test_create_dispatcher_returns_dispatcher_instance(self):
        """``create_dispatcher()`` returns exactly an ``aiogram.Dispatcher``."""
        dispatcher = create_dispatcher()

        assert isinstance(
            dispatcher, Dispatcher
        ), "create_dispatcher() must return an aiogram.Dispatcher instance"

    def test_create_dispatcher_needs_no_bot_token_and_no_network(self, monkeypatch):
        """Assembling the dispatcher needs no token and touches no network.

        Specification: settings are read from the environment, but the
        dispatcher itself is a plain aiogram structure; the token is only
        needed for ``Bot``/polling.
        """
        monkeypatch.delenv("BOT_TOKEN", raising=False)
        monkeypatch.setattr(settings, "bot_token", "")

        def _blocked(*args, **kwargs):
            raise AssertionError("building the dispatcher must not touch the network")

        monkeypatch.setattr(socket, "getaddrinfo", _blocked)
        monkeypatch.setattr(socket, "create_connection", _blocked)

        dispatcher = create_dispatcher()

        assert isinstance(
            dispatcher, Dispatcher
        ), "the dispatcher must build both without BOT_TOKEN and without network"


class TestRouterSetOrder:
    """The tree is exactly ``watcher → buffering``, in that order."""

    def test_watcher_router_precedes_the_buffering_router(self):
        """Both v3 routers are attached, the watcher FIRST.

        The watcher owns the threaded chats (forwards, pending-state
        texts); buffering owns the source chat. The order pins the
        intent «the watcher consumes first» even where the chats do not
        overlap, and a missing router is reported as such (RED: named
        AssertionError, not an import error).
        """
        dispatcher = create_dispatcher()
        watcher_index = router_index_for_module(dispatcher, WATCHER_MODULE)
        buffering_index = router_index_for_module(dispatcher, BUFFERING_MODULE)

        assert buffering_index is not None, (
            "the buffering router must stay attached to the dispatcher"
        )
        assert watcher_index is not None, (
            f"the watcher router ({WATCHER_MODULE}) must be attached to the dispatcher"
        )
        assert watcher_index < buffering_index, (
            "routers must be attached in the order watcher → buffering, "
            f"got watcher at {watcher_index}, buffering at {buffering_index}"
        )

    def test_no_command_routers_are_wired(self):
        """``/thread``, ``/target`` and the v2 live-mode routers are gone.

        BRIEF v3 section 5: the command handlers, the step-8 tracker and
        the step-9 cleanup were deleted with the v2 flow — a leftover
        router would keep dead handlers (and their ``Command`` filters)
        reachable.
        """
        dispatcher = create_dispatcher()
        handlers = list(all_handlers(dispatcher))

        command_filters = [
            handler for handler in handlers if handler_has_command_filter(handler)
        ]
        assert not command_filters, (
            "no Command filter may survive in the v3 dispatcher tree: "
            f"{[handler.callback for handler in command_filters]!r}"
        )

        dead = [
            handler
            for handler in handlers
            if getattr(handler.callback, "__module__", None) in DEAD_MODULES
        ]
        assert not dead, (
            "handlers of the removed v2 modules must not be wired: "
            f"{[handler.callback for handler in dead]!r}"
        )


async def run_through_dispatcher(dispatcher, message):
    """Run ``message`` through the tree like aiogram would.

    Handlers are checked in registration order (routers first, then
    their handlers) and the first whose filters pass consumes the event
    — returning it so the test can name the consumer.
    """
    for router in iter_routers(dispatcher):
        for handler in router.message.handlers:
            passed, _ = await handler.check(message)
            if passed:
                await handler.callback(message)
                return handler
    return None


def admin_forward(bot):
    """An admin's forward of the configured source chat into the threaded chat."""
    origin = SimpleNamespace(
        type="channel",
        date=ORIGIN_DATE,
        chat=SimpleNamespace(id=-100111, username=SOURCE_USERNAME, type="channel"),
        message_id=501,
        sender_chat=None,
        sender_user=None,
    )
    return SimpleNamespace(
        message_id=11,
        chat=SimpleNamespace(id=TARGET_CHAT_ID, username=TARGET_USERNAME, type="supergroup"),
        from_user=SimpleNamespace(id=ADMIN_ID),
        forward_origin=origin,
        text="one",
        caption=None,
        date=ORIGIN_DATE,
        reply_to_message=None,
        bot=bot,
        answer=AsyncMock(name="answer"),
    )


class TestBehaviour:
    """End-to-end through the real router pipeline."""

    async def test_admin_forward_in_the_threaded_chat_starts_a_session(self):
        """The forward is consumed by the watcher and opens a pending batch.

        This pins both the wiring and the filters: the FIRST passing
        handler of the pipeline must be the watcher's forward handler
        (never the cleanup/commands leftovers of v2, never buffering).
        """
        import bot.handlers.watcher as watcher_module  # RED: ModuleNotFoundError

        try:
            from bot.sessions import sessions
        except ModuleNotFoundError:  # pragma: no cover - reported by watcher import above
            sessions = None
        if sessions is not None:
            sessions.reset(TARGET_CHAT_ID)

        bot = AsyncMock(name="bot")
        bot.get_chat_member.return_value = SimpleNamespace(status="administrator")
        bot.send_message.side_effect = lambda *a, **k: SimpleNamespace(message_id=9001)
        message = admin_forward(bot)
        dispatcher = create_dispatcher()

        consumed = await run_through_dispatcher(dispatcher, message)

        assert consumed is not None, "the forward must be consumed by some router"
        assert consumed.callback is watcher_module.on_forward, (
            "the watcher's forward handler must consume an admin forward in the "
            f"threaded chat first, got {consumed.callback!r}"
        )
        if sessions is not None:
            session = sessions.get(TARGET_CHAT_ID)
            assert session is not None, "the forward must start the pending batch"
            assert [m["message_id"] for m in session.messages] == [11]

    async def test_the_threaded_forward_is_never_buffered(self):
        """The buffering filter must refuse the threaded chat outright.

        Consumption means the FILTER refuses it: the pipeline test above
        can pass on order alone, while this one pins the buffering side
        of the contract (threaded chats are not a source of originals).
        """
        import bot.handlers.watcher as watcher_module  # RED: ModuleNotFoundError

        bot = AsyncMock(name="bot")
        bot.get_chat_member.return_value = SimpleNamespace(status="administrator")
        bot.send_message.side_effect = lambda *a, **k: SimpleNamespace(message_id=9001)
        message = admin_forward(bot)
        dispatcher = create_dispatcher()
        buffering = find_module_handler(dispatcher, BUFFERING_MODULE)
        watcher = find_module_handler(dispatcher, WATCHER_MODULE)

        assert watcher.callback is watcher_module.on_forward, (
            "the tree's watcher handler must be the module's on_forward"
        )
        watcher_passed, _ = await watcher.check(message)
        buffering_passed, _ = await buffering.check(message)

        assert watcher_passed is True, "sanity: the watcher admits the forward"
        assert buffering_passed is False, (
            "the forward of a threaded chat must not pass the buffering filter"
        )

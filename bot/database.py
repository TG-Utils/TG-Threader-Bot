"""Async engine and session management for the bot (item 1: SQLAlchemy).

The module is a singleton in the style of ``bot.buffer`` / ``bot.chats``:
``configure(url)`` installs ONE async engine plus an
``async_sessionmaker(expire_on_commit=False)`` for the whole process,
and every collaborator of the package talks to the database through
``session()``. A shared ``sqlite+aiosqlite:///:memory:`` URL is pooled
through a ``StaticPool`` — one shared connection — because every other
pooled connection of a memory database would be its OWN empty database
and one session's writes would be invisible to the next. A repeated
``configure()`` disposes the previous engine first, so every caller
starts from an isolated database.

Error hygiene (L-4): the production DSN carries the database password,
so no error text produced here may embed the URL. ``configure()`` and
``check()`` therefore surface their failures as a FRESH ``RuntimeError``
whose text names nothing but a generic context and the TYPE of the
underlying error; the original error is attached through
``raise_sanitized`` (``raise ... from``), which walks the WHOLE
``__cause__``/``__context__`` chain first and — when any node would
name the DSN credentials — replaces it with a single redacted
stand-in. ``redact_dsn`` is the shared sanitizer ``migrations/env.py``
imports for its own exceptions.
"""

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import NoReturn

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from bot.models import Base

#: URL shape that must share ONE connection: the suite's in-memory database.
_MEMORY_MARKER = ":memory:"

#: ``scheme://userinfo@host`` — the credentials of a DSN (L-4).
_USERINFO_RE = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*://)[^/\s@]+@")

#: ``password=…`` (query-string form) of a DSN.
_PASSWORD_RE = re.compile(r"(?i)\b(password|passwd|pwd)=[^&\s]+")

#: The engine every helper of this module runs on; ``None`` until ``configure()``.
_engine: AsyncEngine | None = None

#: The session factory of the current engine (``expire_on_commit=False``).
_session_factory: async_sessionmaker[AsyncSession] | None = None


def redact_dsn(text_value: str) -> str:
    """``text_value`` with every DSN credential stripped (L-4).

    ``scheme://user:secret@host`` becomes ``scheme://***@host`` and a
    ``password=…`` query parameter is masked — no error text leaving
    this module (or ``migrations/env.py``, which imports this helper)
    may name the password or the userinfo of the configured DSN.
    """
    without_userinfo = _USERINFO_RE.sub(r"\1***@", text_value)
    return _PASSWORD_RE.sub(r"\1=***", without_userinfo)


def _chain_nodes(error: BaseException) -> list[BaseException]:
    """``error`` plus every ``__cause__``/``__context__`` node (cycle-safe)."""
    nodes: list[BaseException] = []
    queue: list[BaseException | None] = [error]
    seen: set[int] = set()
    while queue:
        node = queue.pop()
        if node is None or id(node) in seen:
            continue
        seen.add(id(node))
        nodes.append(node)
        queue.append(node.__cause__)
        queue.append(node.__context__)
    return nodes


def _safe_cause(error: BaseException) -> BaseException:
    """``error`` when its WHOLE chain carries no DSN credentials, else a stand-in.

    The exception a caller may print has to be credential-free across
    ``__cause__``/``__context__`` alike, so a dirty chain is replaced
    by one redacted ``RuntimeError`` summarising every node (the stand-in
    starts with NO links of its own — otherwise the implicit context of
    its construction would re-attach the original).
    """
    nodes = _chain_nodes(error)
    if all(redact_dsn(str(node)) == str(node) for node in nodes):
        return error
    summary = "; ".join(
        f"{type(node).__name__}: {redact_dsn(str(node))}"
        for node in nodes
    )
    stand_in = RuntimeError(summary)
    stand_in.__cause__ = None
    stand_in.__context__ = None
    return stand_in


def raise_sanitized(message: str, error: BaseException) -> NoReturn:
    """Raise ``RuntimeError(message)`` over a credential-free ``error`` chain.

    The fresh wrapper is pinned to the SANITIZED cause as both its
    ``__cause__`` and its ``__context__`` before it is raised, so the
    implicit exception chaining can never re-attach the raw error
    behind the caller's back.
    """
    cause = _safe_cause(error)
    wrapper = RuntimeError(message)
    wrapper.__context__ = cause
    raise wrapper from cause


def _require_engine() -> AsyncEngine:
    """The configured engine — a clear error when ``configure()`` never ran."""
    if _engine is None:
        raise RuntimeError("database is not configured: call configure() first.")
    return _engine


async def configure(url: str) -> None:
    """Install the async engine for ``url`` as THE engine of the process.

    A repeated call DISPOSES the previous engine before installing the
    new one, so the old database (and its pooled connections) can never
    bleed into the next configuration. An in-memory sqlite URL gets a
    ``StaticPool``: one shared connection, visible to every session.

    Raises:
        RuntimeError: The DSN cannot be turned into an engine (L-4:
            the message carries only the failure TYPE, and the whole
            ``__cause__``/``__context__`` chain is redacted before it
            is attached — never the password/userinfo of the URL).
    """
    global _engine, _session_factory
    previous, _engine = _engine, None
    _session_factory = None
    if previous is not None:
        await previous.dispose()
    pool_options = {}
    if _MEMORY_MARKER in url:
        pool_options["poolclass"] = StaticPool
    try:
        engine = create_async_engine(url, **pool_options)
    except Exception as error:
        raise_sanitized(f"database configuration failed ({type(error).__name__})", error)
    _engine = engine
    _session_factory = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session() -> AsyncIterator[AsyncSession]:
    """One session: commit on success, rollback + re-raise on error, close either way.

    Yields:
        The live ``AsyncSession`` of the currently configured engine.

    Raises:
        RuntimeError: No engine has been configured yet.
    """
    factory = _session_factory
    if factory is None:
        raise RuntimeError("database is not configured: call configure() first.")
    async with factory() as db_session:
        try:
            yield db_session
        except BaseException:
            await db_session.rollback()
            raise
        await db_session.commit()


async def create_all() -> None:
    """Create every table of ``Base.metadata`` — idempotent (test tooling)."""
    engine = _require_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def drop_all() -> None:
    """Drop every table of ``Base.metadata`` — idempotent (test tooling)."""
    engine = _require_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)


async def check() -> None:
    """Probe the connection with ``SELECT 1`` (the startup fail-fast).

    Raises:
        RuntimeError: The database is unreachable. The fresh message
            carries only a generic context and the TYPE of the original
            error — never the DSN (its password must not leak into any
            ``str()``/``repr()``); the original error stays reachable
            through the exception ``__cause__`` for debugging, with the
            whole chain redacted when it would name credentials (L-4).
    """
    engine = _require_engine()
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as error:
        raise_sanitized(
            f"database check failed: unreachable database ({type(error).__name__})",
            error,
        )


async def dispose() -> None:
    """Dispose the current engine and reset the module state."""
    global _engine, _session_factory
    engine, _engine = _engine, None
    _session_factory = None
    if engine is not None:
        await engine.dispose()

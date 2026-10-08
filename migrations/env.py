"""Async Alembic environment of the bot (item 1).

The DSN is NEVER hardcoded: ``migrations/env.py`` reads ``DATABASE_URL``
from the environment (the very hook the tests patch), falling back to
the ``.env`` file of ``bot.config`` — the same sources as the running
bot. The migration runs on an async engine driven through
``asyncio.run()`` so the whole file stays callable from synchronous
alembic commands (``alembic upgrade head`` and the functional tests).
"""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from bot.config import Settings
from bot.database import raise_sanitized
from bot.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

#: The metadata every migration is compared against (autogenerate target).
target_metadata = Base.metadata


def _database_url() -> str:
    """The migration DSN: ``DATABASE_URL`` from the environment, else ``.env``.

    Raises:
        RuntimeError: Neither source carries a URL — the same fail-fast
            text as the application startup.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        url = Settings().database_url
    if not url:
        raise RuntimeError("DATABASE_URL is not configured.")
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode: emit SQL, no DB connection.

    The DSN read stays OUTSIDE the guarded block (its fail-fast message
    is credential-free by construction); everything alembic raises after
    it is sanitized like the online path (L-4).
    """
    url = _database_url()
    try:
        context.configure(
            url=url,
            target_metadata=target_metadata,
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()
    except Exception as error:
        raise_sanitized(f"offline migration failed ({type(error).__name__})", error)


def do_run_migrations(connection: Connection) -> None:
    """Run the migration history against a live connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Connect through the async engine, migrate, dispose.

    Error hygiene (L-4): every exception leaving this file goes through
    ``bot.database.raise_sanitized`` — the message names only the
    failure TYPE and the whole ``__cause__``/``__context__`` chain is
    redacted before it is attached, so a migration failure can never
    print the password/userinfo of ``DATABASE_URL``.
    """
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()
    try:
        connectable = async_engine_from_config(
            configuration,
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
    except Exception as error:
        raise_sanitized(f"migration engine setup failed ({type(error).__name__})", error)

    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    except Exception as error:
        raise_sanitized(f"migration failed ({type(error).__name__})", error)
    finally:
        try:
            await connectable.dispose()
        except Exception as error:
            raise_sanitized(f"migration cleanup failed ({type(error).__name__})", error)


def run_migrations_online() -> None:
    """Run migrations against the live database (``asyncio.run`` — sync callers)."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

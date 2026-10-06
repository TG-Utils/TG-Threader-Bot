"""Alembic migration tests (item 1 — PostgreSQL + Alembic).

RED phase: ``alembic.ini`` and ``migrations/`` do not exist yet — every
test below fails on its first filesystem assertion (``AssertionError``),
never at collection time: nothing here imports ``bot.database`` /
``bot.models`` at module level (the model is imported lazily inside
the insert helper).

Specification (item 1 + cycle A):
- ``alembic.ini`` drives the history from ``migrations``
  (``script_location = migrations``) with ``prepend_sys_path = .``;
- ``migrations/env.py`` (async template: URL from the ``DATABASE_URL``
  environment variable, ``target_metadata = Base.metadata``,
  ``render_as_batch=True``), ``migrations/script.py.mako`` and at
  least one version script under ``migrations/versions/``;
- the history has EXACTLY ONE head (it must not branch);
- ``alembic.command.upgrade(cfg, "head")`` builds ``buffer_messages``
  of the CURRENT models: every column, the ``UNIQUE (chat_id,
  message_id)`` constraint and the ``chat_id`` index — a row inserted
  through ``bot.models.BufferMessage`` must land in the migrated file
  (schema == models);
- the chained ``0002_add_pairs_table`` adds the ``pairs`` table of the
  pair registry (cycle A): columns ``id, source_ref, target_ref,
  created_at`` (id = primary key, everything NOT NULL) and
  ``UNIQUE (source_ref, target_ref)`` — a ``Pair`` row inserted
  through ``bot.models.Pair`` lands without DDL help and gets its
  ``created_at`` from the PYTHON default (naive UTC);
- ``alembic.command.downgrade(cfg, "base")`` removes BOTH tables
  again (the full chain rolls back).

The functional tests are SYNC on purpose: ``migrations/env.py`` runs
the async migration through ``asyncio.run()``, which is impossible
from inside a running event loop (an async pytest test body would be
one). The migrated database is a tmp-file sqlite, patched in through
the ``DATABASE_URL`` environment variable — the very hook
``env.py`` reads.

RED phase (cycle A): the ``0002_add_pairs_table`` version does not
exist yet — the pairs tests fail on their first table assertion
(``AssertionError``), never at collection time: nothing here imports
``bot.database`` / ``bot.models`` at module level (the models are
imported lazily inside the insert helpers).
"""

import asyncio
import configparser
import contextlib
import inspect
import sqlite3
from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import select

#: The repository root: alembic.ini and migrations/ live there.
REPO_ROOT = Path(__file__).resolve().parents[1]

#: Exactly the columns of ``buffer_messages`` (the BufferMessage model).
BUFFER_COLUMNS = {
    "id",
    "chat_id",
    "message_id",
    "user_id",
    "sent_at",
    "text",
    "caption",
    "file_unique_id",
    "created_at",
}

#: Exactly the columns of ``pairs`` (the Pair model), in declaration order.
PAIR_COLUMN_ORDER = ["id", "source_ref", "target_ref", "created_at"]

#: The same columns as a set, for the shape assertion.
PAIR_COLUMNS = set(PAIR_COLUMN_ORDER)


def alembic_config(monkeypatch) -> Config:
    """A Config over the repo's ``alembic.ini``, resolved from the repo root.

    ``script_location = migrations`` is a relative path, so alembic
    resolves it against the current working directory — the tests
    therefore run from the repository root (the module of ``prepend_
    sys_path = .`` as well).
    """
    ini = REPO_ROOT / "alembic.ini"
    assert ini.is_file(), f"alembic.ini must exist, got no file at {ini}"
    monkeypatch.chdir(REPO_ROOT)
    return Config(str(ini))


def table_names(db_file: Path) -> set[str]:
    """Every table of the sqlite file (stdlib inspector)."""
    with contextlib.closing(sqlite3.connect(db_file)) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {name for (name,) in rows}


def column_names(db_file: Path, table: str) -> list[str]:
    """Column names of ``table``, in declaration order."""
    with contextlib.closing(sqlite3.connect(db_file)) as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [row[1] for row in rows]  # pragma table_info: cid, name, type, notnull, …


def unique_index_columns(db_file: Path, table: str) -> list[set[str]]:
    """Column sets of every UNIQUE index/constraint of ``table``."""
    with contextlib.closing(sqlite3.connect(db_file)) as conn:
        indexes = conn.execute(f"PRAGMA index_list({table})").fetchall()
        # pragma index_list: seq, name, unique, origin, partial
        unique_names = [row[1] for row in indexes if row[2]]
        return [
            {row[2] for row in conn.execute(f"PRAGMA index_info({name})").fetchall()}
            for name in unique_names
        ]


def plain_index_columns(db_file: Path, table: str) -> list[list[str]]:
    """Column lists of every NON-unique index of ``table``."""
    with contextlib.closing(sqlite3.connect(db_file)) as conn:
        indexes = conn.execute(f"PRAGMA index_list({table})").fetchall()
        plain_names = [row[1] for row in indexes if not row[2]]
        return [
            [row[2] for row in conn.execute(f"PRAGMA index_info({name})").fetchall()]
            for name in plain_names
        ]


def primary_key_columns(db_file: Path, table: str) -> list[str]:
    """PRIMARY KEY column names of ``table`` (``pk`` flag of PRAGMA table_info)."""
    with contextlib.closing(sqlite3.connect(db_file)) as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [row[1] for row in rows if row[5]]  # table_info: …, notnull, dflt, pk


def required_columns(db_file: Path, table: str) -> set[str]:
    """Column names declared NOT NULL of ``table`` (``notnull`` flag)."""
    with contextlib.closing(sqlite3.connect(db_file)) as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows if row[3]}


async def insert_one_row(db_file: Path) -> None:
    """Insert a model row into the migrated file through ``bot.database``.

    The schema produced by the migration must EQUAL the models: an
    insert built from ``BufferMessage`` lands without any DDL help.
    Lazily imported (RED: ``bot.database``/``bot.models`` do not exist
    yet — this helper fails inside the test body, not at collection).
    """
    import bot.database as database_module
    from bot.models import BufferMessage

    url = f"sqlite+aiosqlite:///{db_file}"
    configured = database_module.configure(url)
    if inspect.isawaitable(configured):
        await configured
    async with database_module.session() as session:
        session.add(
            BufferMessage(
                chat_id=7,
                message_id=8,
                user_id=9,
                sent_at=datetime(2026, 10, 5, 10, 0, 0),
                text="migrated",
            )
        )
    async with database_module.session() as session:
        rows = list((await session.execute(select(BufferMessage))).scalars())
    assert len(rows) == 1, f"the migrated file must accept a model row, got {rows!r}"
    assert rows[0].text == "migrated"


async def insert_pair_row(db_file: Path) -> None:
    """Insert a ``Pair`` row into the migrated file through ``bot.database``.

    The ``pairs`` migration must EQUAL the ``Pair`` model: an insert
    built from the model class lands without any DDL help.
    ``created_at`` is NOT passed on purpose — the row must get it from
    the PYTHON default as NAIVE UTC, exactly like ``BufferMessage``.
    Lazily imported (RED: ``bot.models.Pair`` does not exist yet —
    this helper fails inside the test body, not at collection).
    """
    import bot.database as database_module
    from bot.models import Pair

    url = f"sqlite+aiosqlite:///{db_file}"
    configured = database_module.configure(url)
    if inspect.isawaitable(configured):
        await configured
    async with database_module.session() as session:
        session.add(Pair(source_ref="@src", target_ref="@tgt"))
    async with database_module.session() as session:
        rows = list((await session.execute(select(Pair))).scalars())
    assert len(rows) == 1, f"the migrated file must accept a Pair row, got {rows!r}"
    assert rows[0].source_ref == "@src" and rows[0].target_ref == "@tgt"
    assert rows[0].created_at is not None, "created_at must be filled by the Python default"
    assert rows[0].created_at.tzinfo is None, (
        "created_at must be NAIVE UTC, like every other timestamp of the suite"
    )


class TestAlembicFiles:
    """The migration skeleton on disk."""

    def test_the_alembic_files_exist(self):
        """``alembic.ini`` + ``migrations/{env.py,script.py.mako,versions/}``."""
        assert (REPO_ROOT / "alembic.ini").is_file(), "alembic.ini must exist"
        assert (REPO_ROOT / "migrations" / "env.py").is_file(), (
            "migrations/env.py must exist"
        )
        assert (REPO_ROOT / "migrations" / "script.py.mako").is_file(), (
            "migrations/script.py.mako must exist"
        )
        versions = sorted(
            path.name
            for path in (REPO_ROOT / "migrations" / "versions").glob("*.py")
            if path.name != "__init__.py"
        )
        assert versions, "the history must carry at least one version script"

    def test_the_ini_pins_the_expected_options(self):
        """``script_location = migrations`` and ``prepend_sys_path = .``."""
        parser = configparser.RawConfigParser()
        parser.read(REPO_ROOT / "alembic.ini")

        assert parser.has_section("alembic"), "alembic.ini must carry the [alembic] section"
        assert parser.get("alembic", "script_location") == "migrations"
        assert parser.get("alembic", "prepend_sys_path") == "."

    def test_the_history_has_exactly_one_head(self, monkeypatch):
        """One head: the versions must form a single chain, not a branch."""
        cfg = alembic_config(monkeypatch)
        script = ScriptDirectory.from_config(cfg)

        heads = script.get_heads()
        assert len(heads) == 1, f"the migration history must have one head, got {heads!r}"


class TestUpgradeAndDowngrade:
    """The functional lifecycle: upgrade → schema == models → downgrade."""

    def test_upgrade_builds_the_buffer_messages_schema(self, tmp_path, monkeypatch):
        """``upgrade(head)`` creates ``buffer_messages`` exactly like the models.

        Columns (all nine of ``BufferMessage``), the
        ``UNIQUE (chat_id, message_id)`` constraint and the ``chat_id``
        index — plus a row inserted THROUGH the model class lands in
        the migrated file (schema == models).
        """
        db_file = tmp_path / "migrated.db"
        monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file}")
        cfg = alembic_config(monkeypatch)

        command.upgrade(cfg, "head")

        assert "buffer_messages" in table_names(db_file), (
            "upgrade(head) must create the buffer_messages table"
        )
        assert set(column_names(db_file, "buffer_messages")) == BUFFER_COLUMNS, (
            "the migrated columns must equal the BufferMessage columns"
        )
        assert {"chat_id", "message_id"} in unique_index_columns(db_file, "buffer_messages"), (
            "the UNIQUE (chat_id, message_id) constraint must be migrated"
        )
        assert ["chat_id"] in plain_index_columns(db_file, "buffer_messages"), (
            "the chat_id index must be migrated"
        )
        asyncio.run(insert_one_row(db_file))

    def test_upgrade_builds_the_pairs_schema(self, tmp_path, monkeypatch):
        """``upgrade(head)`` creates BOTH tables; ``pairs`` matches the Pair model.

        The chained ``0002_add_pairs_table`` migration adds the pair
        registry of cycle A: exactly the columns ``id, source_ref,
        target_ref, created_at`` in declaration order (``id`` the sole
        primary key, every column NOT NULL), the
        ``UNIQUE (source_ref, target_ref)`` constraint — plus a row
        inserted THROUGH ``bot.models.Pair`` lands in the migrated
        file and takes its ``created_at`` from the PYTHON default
        (naive UTC). ``buffer_messages`` of ``0001`` must survive the
        chain beside it.
        """
        db_file = tmp_path / "pairs.db"
        monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file}")
        cfg = alembic_config(monkeypatch)

        command.upgrade(cfg, "head")

        names = table_names(db_file)
        assert "buffer_messages" in names, (
            "the chain must keep the 0001 buffer_messages table"
        )
        assert "pairs" in names, "0002_add_pairs_table must create the pairs table"
        assert column_names(db_file, "pairs") == PAIR_COLUMN_ORDER, (
            "the migrated columns must equal the Pair columns in declaration order"
        )
        assert set(column_names(db_file, "pairs")) == PAIR_COLUMNS, (
            "the migrated columns must equal the Pair columns"
        )
        assert primary_key_columns(db_file, "pairs") == ["id"], (
            "id must be the primary key of the pairs table"
        )
        assert required_columns(db_file, "pairs") == PAIR_COLUMNS, (
            "every column of pairs must be declared NOT NULL"
        )
        assert {"source_ref", "target_ref"} in unique_index_columns(db_file, "pairs"), (
            "the UNIQUE (source_ref, target_ref) constraint must be migrated"
        )
        asyncio.run(insert_pair_row(db_file))

    def test_downgrade_to_base_drops_both_tables(self, tmp_path, monkeypatch):
        """``downgrade(base)`` rolls the WHOLE chain back: no buffer, no pairs."""
        db_file = tmp_path / "downgraded.db"
        monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file}")
        cfg = alembic_config(monkeypatch)

        command.upgrade(cfg, "head")
        assert "buffer_messages" in table_names(db_file), "sanity: upgrade created the table"
        assert "pairs" in table_names(db_file), "sanity: upgrade created the pairs table"

        command.downgrade(cfg, "base")

        names = table_names(db_file)
        assert "buffer_messages" not in names, (
            "downgrade(base) must drop the buffer_messages table"
        )
        assert "pairs" not in names, "downgrade(base) must drop the pairs table too"

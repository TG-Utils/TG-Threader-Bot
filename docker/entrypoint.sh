#!/bin/sh
# Start the bot. Schema migrations run first so a fresh database is usable.
# Set RUN_MIGRATIONS=0 to start polling without applying migrations.
set -eu

if [ "${RUN_MIGRATIONS:-1}" != "0" ]; then
  python -m alembic upgrade head
fi

exec python -m bot

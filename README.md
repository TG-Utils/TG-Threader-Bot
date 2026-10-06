# TG-Threader-Bot

Telegram bot that turns a manually forwarded batch of messages into a
reply-chain thread: forward the flood from the source chat into the
configured thread chat, and the bot asks for the source (when the forward
origin does not say it) and a title, builds the thread (header + flat
replies, albums glued with one `sendMediaGroup`), removes the forwarded
copies, cleans the originals in the source chat, and reports
`Thread created … / Deleted x of y` (partial success is normal — the Bot
API has no history access).

> Specification: [`BRIEF.md`](BRIEF.md) (forward-batch, agreed) and the
> project wiki `General-idea` for templates/background. Agent workflow:
> [`AGENTS.md`](AGENTS.md).

## Stack

Python 3, aiogram 3, pydantic-settings (`.env`). No web layer, no AI.

## Configuration

- `.env` — copy from `.env.example`, set `BOT_TOKEN`, `DATABASE_URL`
  and `OWNER_ID` (never commit `.env`); `LOCALE` selects the message
  pack from `bot/locales/`.
- `DATABASE_URL` — async SQLAlchemy URL, e.g.
  `postgresql+asyncpg://user@localhost:5432/tg_threader`; the bot fails
  fast at startup if it is missing or unreachable. Apply migrations with
  `.venv/bin/alembic upgrade head`.
- **Pairs live in the database** and are managed with `/settings` in
  the bot's DM: the list shows chat titles (`getChat`), a pair is added
  by forwarding a message from the chat or typing `@username`/id, and
  removed with an inline button. `/settings` is open to `OWNER_ID` and
  to admins of any pair's chats (bootstrap: set `OWNER_ID` before the
  first pair). The bot must be an admin with `can_delete_messages` in
  **both** chats of a pair.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/alembic upgrade head   # create the schema
.venv/bin/pytest -q              # 511 tests
.venv/bin/ruff check .
```

CI runs ruff and pytest (Python 3.10 and 3.14) on every push and pull
request to `main`/`develop` — see `.github/workflows/ci.yml`.

## Run

```bash
.venv/bin/python -m bot        # long polling until Ctrl+C
```

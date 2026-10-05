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

- `.env` — copy from `.env.example`, set `BOT_TOKEN` (never commit `.env`).
- `chats.json` — pairs `{"source": …, "target": …}` (see
  `chats.example.json`); the bot must be an admin with
  `can_delete_messages` in **both** chats of a pair.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q        # 314 tests
.venv/bin/ruff check .
```

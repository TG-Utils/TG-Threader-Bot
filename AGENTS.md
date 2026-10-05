# AGENTS.md

Instructions for AI coding agents working on TG-Threader-Bot.

## About the project

Telegram bot that turns a manually forwarded batch into a reply-chain
thread: a moderator of the source chat selects messages and forwards them
as one batch into a configured thread chat; the bot captures the batch in
a pending state, asks "Which chat did you forward from?" only when the
forward origin does not resolve to a configured pair, then always asks
"Thread title?" (any admin answers; `/cancel` aborts); it posts a
template header, edits in the thread link, places the batch as flat
replies to it (albums glued via one `sendMediaGroup` per `media_group_id`,
copy fallback on failure), deletes the forwarded copies, cleans the
originals in the source chat (channel origin → by `forward_origin`
message id when it matches the session pair, otherwise matched against
the live buffer: date + sender + text/caption/`file_unique_id`), and
replies `Thread created: {n} message(s). {url}` +
`Deleted {x} of {y} original messages.` (partial success is normal —
Bot API has no history read).

Specification: `BRIEF.md` in the repo root (v3 forward-batch, agreed)
plus the project wiki page `General-idea` for templates and background.

- **Project language: English** — code, identifiers, comments, docstrings,
  tests, and bot replies.
- Stack: Python 3, aiogram 3, pydantic-settings (`.env`). Only the aiogram
  part: no HTTP/web layer, no AI features.
- Secrets live only in `.env` (gitignored). Never commit tokens.

## Commands

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q        # run tests
.venv/bin/ruff check .     # lint, must be clean
```

## TDD workflow

**Tests are the specification.** Code is written only to make failing tests pass.

Roles are OpenCode subagents defined in `.opencode/agents/`:

| Agent | Phase | Responsibility |
|---|---|---|
| `tester` | RED | writes failing tests before any implementation exists |
| `developer` | GREEN | implements the minimal code for the tests; never edits tests |
| `security` | review | read-only audit by severity (Critical/High/Medium/Low), file:line |

### Cycle

1. **RED** — launch `tester` with: scope (modules to cover), spec bullets from
   the wiki, the constraint "tests/ only", and the expected failure mode
   (`ModuleNotFoundError` for new modules, `AssertionError` for behaviour changes).
2. **GREEN** — launch `developer` with: the fix scope, "tests are the spec",
   the target test count, clean ruff, and a mandatory report.
3. **Verify personally**: `pytest -q` + `ruff check .` — do not trust reports blindly.
4. **After milestones** — launch `security` on the changed files; its findings
   become the scope of the next RED cycle.

### Prompt templates

**RED (new module):**

> Phase RED, cycle N. Project: <checkout path>. Write FAILING tests for the
> not-yet-existing module `bot/<name>`: <behaviour list from the spec>.
> Write only to `tests/`, never touch `bot/`. Run `.venv/bin/pytest -q`;
> failures must be `ModuleNotFoundError` for the new module, existing tests
> stay green. Report: list of tests + run output (red). Empty report unacceptable.

**GREEN (implementation):**

> Phase GREEN, cycle N. Make the failing tests pass by editing ONLY files in
> `bot/`: <fix scope>. The tests are the specification — never edit them; if a
> test looks buggy, stop and explain instead of fitting code to it.
> Run `.venv/bin/pytest -q` → target `N passed`, then
> `.venv/bin/ruff check bot/ tests/` → clean. No commits.
> Mandatory report: changed files, pytest and ruff outputs, deviations.

**Security review:**

> Read-only review of <files/changes>. Check: secrets, injection (HTML/URL
> schemes), input validation, bot permissions and abuse, DoS/rate limits,
> unsafe defaults. Format: severity → file:line → issue → recommendation.
> Change nothing.

## Current state and backlog

- The test suite is green (314 tests), ruff clean (`ruff check --no-cache`).
  Implemented per `BRIEF.md` (v3): config (`.env` + `chats.json` pairs,
  fresh-read, normalized-ref self-pair rejection, pair↔target binding),
  template renderer (`{{vars}}` + `[a]label[/a]`, strict URL validation,
  HTML escaping), thread/message URL builders, source-chat live buffer
  (`bot/buffer.py`: `record` + `find_and_take` with date + sender +
  text/caption/`file_unique_id`, consumed matches), chat-pairs registry
  (`bot/chats.py`: `target_for`, `pair_for_source_ref`,
  `pair_for_origin`, `is_source`, `is_configured_target`,
  `pair_targets_chat`), pending sessions (`bot/sessions.py`), the
  forward-batch state machine + execution
  (`bot/handlers/watcher.py`: source/title questions, `/cancel`,
  accumulation cap 100, per-chat `asyncio.Lock`, header → edit →
  media-group/copy placement → best-effort forward deletion → source
  cleanup → success report), source buffering
  (`bot/handlers/buffering.py`), dispatcher (watcher → buffering).
- Security reviews are closed through iterations C/D: H1 mixed-origin
  deletion scoping + origin-conflict re-ask, M1 sender/caption matching,
  M2 question/final-answer failure resilience, M3 pair must match the
  current chat, M5 accumulation cap, M6 per-chat lock, L1 target
  escaping, L2 non-text inputs ignored + `/cancel@bot`, L4 multi-type
  media extraction, L10 forward deletion best-effort, I2 normalized
  self-pair. Notes carried into `BRIEF.md` §7: shared-administration
  invariant, reply-correlation option.
- Backlog: rate limiting / admin cooldown, `TelegramRetryAfter`
  handling, mtime-cache for `chats.json` reads, admin-status TTL cache,
  pruning of long-lived stores, `SecretStr` for BOT_TOKEN, UTF-16-aware
  title truncation, anonymous-admin handling, dependency lockfile,
  structured logging, reply-correlation option (accept title/source
  answers only as replies to the bot's question), `chats.json` privacy
  scrub before any publish, wiki sync (Tech-stack, forward-flow
  deviations), persistence of pending sessions/buffer across restarts.

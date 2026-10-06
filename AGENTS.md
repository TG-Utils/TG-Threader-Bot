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

- The test suite is green (511 tests), ruff clean
  (`ruff check --no-cache`); CI (`.github/workflows/ci.yml`) runs ruff +
  pytest on push/PR to main/develop (Python 3.10 and 3.14).
- Implemented per `BRIEF.md` (v3 + post-v1 additions): config (`.env` —
  `BOT_TOKEN`, `LOCALE`, `DATABASE_URL`, `OWNER_ID`; language packs),
  i18n message packs (`bot/i18n.py` + `bot/locales/en.json`, 30 keys,
  `set_locale`/`t`, en-fallback, key-drift tests), database layer
  (`bot/database.py`: async engine/session/check with URL-free error
  messages; `bot/models.py`: `buffer_messages` + `pairs` tables, unique
  constraints, naive-UTC timestamps; Alembic in `migrations/` — heads
  0001/0002; fail-fast `DATABASE_URL` in `bot/__main__.py`), async
  DB-backed source buffer (`bot/buffer.py`: `record` with dedup +
  per-chat maxlen prune, `find_and_take` consuming matches by
  instant/date + sender + text/caption/`file_unique_id`), settings menu
  (`bot/handlers/settings.py`: `/settings` in DM, ACL = `OWNER_ID` or
  admin of any pair via `get_chat_administrators` fail-safe, pair list
  with `getChat` titles, add flow — forward origin or `@username`/id
  input with self-pair/bot-rights/duplicate checks → inline confirm,
  delete flow with per-pair inline buttons, per-DM `states`;
  chat pickers on both add-flow prompts — buttons of every known chat
  (`st:pick:<ref>`, `st:cancel`), shared `bot/keyboards.py`), pairs
  registry over DB (`bot/chats.py`: in-memory cache + `refresh`/
  `add_pair`/`remove_pair`/`parse_chat_ref`/`is_same_chat`/
  `origin_chat_ref`; `chats.json` is no longer read), template renderer
  and URL builders, pending sessions (`bot/sessions.py`), the
  forward-batch state machine + execution (`bot/handlers/watcher.py`:
  source/title questions, `/cancel`, an inline source picker on the
  source question (`w:src:<ref>` / `w:cancel` — buttons of the sources
  targeting the current chat), accumulation cap 100, per-chat
  `asyncio.Lock`, chronological placement, header → edit →
  media-group/copy placement → best-effort forward deletion → source
  cleanup → success report), source buffering
  (`bot/handlers/buffering.py`), dispatcher (settings → watcher →
  buffering).
- Security reviews: iterations C/D closed (H1 mixed-origin deletion
  scoping + origin-conflict re-ask, M1 sender/caption matching, M2
  question/final-answer failure resilience, M3 pair must match the
  current chat, M5 accumulation cap, M6 per-chat lock, L1 target
  escaping, L2 non-text inputs ignored + `/cancel@bot`, L4 multi-type
  media extraction, L10 forward deletion best-effort, I2 normalized
  self-pair). The second review (DB/menu/pickers) and the third
  (verification of its fixes) are closed as well: confirm-time pair
  authority (H-1), TTL + negative admin/`getChat` caches with a 3 s
  confirm-verdict window (H-2/N1), registry write lock (M-2), cached
  sender checks (M-4), private-only `st:*` (L-1), safe edits (L-2),
  DSN hygiene (L-4), 64-byte payloads (L-5), `/settings` state reset
  (L-7), `LOCALE`/`LOG_LEVEL` validation + real logging (I-1/I-2),
  the long-digit guard (N3), the ≤200-char alert (N9) and CI
  `permissions`/`timeout-minutes` (L-6). Full list: BRIEF.md §7.
- Backlog: rate limiting / admin cooldown, buffer write
  flood-protection, pruning of long-lived stores, `SecretStr` for
  BOT_TOKEN, UTF-16-aware title truncation, wrapping every
  `callback.answer` against `TelegramBadRequest`, fresh-exception
  re-raise in the admin cache (traceback growth), anonymous-admin
  handling, dependency lockfile + action SHA-pinning, structured
  logging, reply-correlation option (accept title/source answers only
  as replies to the bot's question), wiki sync (Tech-stack,
  forward-flow deviations), persistence of pending sessions across
  restarts, per-chat language selection (packs are global per-process
  today), menu scoping to the caller's pairs, per-batch registry
  refresh in the picker, `st:del` recheck window for cache-unknown
  rows, admin-cache eviction/bot-id keying.

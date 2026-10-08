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

CI (`.github/workflows/ci.yml`) runs both on push/PR to
`main`/`develop` (Python 3.10 and 3.14).

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


## State and backlog

The living state, security-review status and backlog live in
`PLAN.md` — this file stays base-only (per the PR #2 review).

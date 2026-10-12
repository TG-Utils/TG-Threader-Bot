# PLAN — state and backlog

Living status of TG-Threader-Bot, kept out of `AGENTS.md` (PR #2
review: agent instructions carry base information only, and interim
test-suite reports do not belong in repo docs).

## Implemented

Per `BRIEF.md` (v3 + post-v1 additions):

- config (`.env` — `BOT_TOKEN`, `LOCALE`, `DATABASE_URL`, `OWNER_ID`);
- i18n over the standard gettext ecosystem (`bot/i18n.py`:
  `translate` imported at call sites as `_`, `set_locale` over stdlib
  `gettext.translation(fallback=True)`; template
  `bot/locales/messages.pot` extracted with `pybabel extract -F
  babel.cfg`; Babel in `[dev]`; both drift directions and the
  `.po` → `.mo` compile chain pinned by `tests/test_i18n.py`);
- database layer (`bot/database.py`: async engine/session/check with
  URL-free error messages; `bot/models.py`: `buffer_messages` +
  `pairs` tables, unique constraints, naive-UTC timestamps; Alembic in
  `migrations/` — heads 0001/0002; fail-fast `DATABASE_URL` in
  `bot/__main__.py`);
- async DB-backed source buffer (`bot/buffer.py`: `record` with dedup
  + per-chat maxlen prune, `find_and_take` consuming matches by
  instant/date + sender + text/caption/`file_unique_id`);
- settings menu (`bot/handlers/settings.py`: `/settings` in DM, ACL =
  `OWNER_ID` or admin of any pair via `get_chat_administrators`
  fail-safe, pair list with `getChat` titles, add flow — forward origin
  or `@username`/id input with self-pair/bot-rights/duplicate checks →
  inline confirm, delete flow with per-pair inline buttons, per-DM
  `states`; chat pickers on both add-flow prompts — buttons of every
  known chat (`st:pick:<ref>`, `st:cancel`), shared `bot/keyboards.py`);
- pairs registry over DB (`bot/chats.py`: in-memory cache +
  `refresh`/`add_pair`/`remove_pair`/`parse_chat_ref`/`is_same_chat`/
  `origin_chat_ref`; `chats.json` is no longer read);
- template renderer and URL builders, pending sessions
  (`bot/sessions.py`);
- the forward-batch state machine + execution
  (`bot/handlers/watcher.py`: source/title questions, `/cancel`, an
  inline source picker on the source question (`w:src:<ref>` /
  `w:cancel` — buttons of the sources targeting the current chat),
  accumulation cap 100, per-chat `asyncio.Lock`, chronological
  placement, header → edit → media-group/copy placement → best-effort
  forward deletion → source cleanup → success report);
- source buffering (`bot/handlers/buffering.py`);
- dispatcher (settings → watcher → buffering);
- GitHub Actions CI (ruff + pytest, Python 3.10/3.14).

## Security reviews

Iterations C/D closed (H1 mixed-origin deletion scoping +
origin-conflict re-ask, M1 sender/caption matching, M2
question/final-answer failure resilience, M3 pair must match the
current chat, M5 accumulation cap, M6 per-chat lock, L1 target
escaping, L2 non-text inputs ignored + `/cancel@bot`, L4 multi-type
media extraction, L10 forward deletion best-effort, I2 normalized
self-pair). The second review (DB/menu/pickers) and the third
(verification of its fixes) are closed as well: confirm-time pair
authority (H-1), TTL + negative admin/`getChat` caches with a 3 s
confirm-verdict window (H-2/N1), registry write lock (M-2), cached
sender checks (M-4), private-only `st:*` (L-1), safe edits (L-2), DSN
hygiene (L-4), 64-byte payloads (L-5), `/settings` state reset (L-7),
`LOCALE`/`LOG_LEVEL` validation + real logging (I-1/I-2), the
long-digit guard (N3), the ≤200-char alert (N9) and CI
`permissions`/`timeout-minutes` (L-6). Full list: BRIEF.md §7.

## Backlog

- rate limiting / admin cooldown
- buffer write flood-protection
- pruning of long-lived stores
- `SecretStr` for BOT_TOKEN
- UTF-16-aware title truncation
- wrapping every `callback.answer` against `TelegramBadRequest`
- fresh-exception re-raise in the admin cache (traceback growth)
- anonymous-admin handling
- dependency lockfile + action SHA-pinning
- structured logging
- reply-correlation option (accept title/source answers only as
  replies to the bot's question)
- wiki sync (Tech-stack, forward-flow deviations)
- persistence of pending sessions across restarts
- per-chat language selection (catalogs are global per-process today)
- menu scoping to the caller's pairs
- per-batch registry refresh in the picker
- `st:del` recheck window for cache-unknown rows
- admin-cache eviction/bot-id keying

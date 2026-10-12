# TG-Threader-Bot — Brief v3 (forward batch)

Status: **agreed** (user + TL, 05.10). Replaces brief v2 entirely.

## 1. Core idea

A moderator of the **source** chat selects messages manually and forwards
them as a **batch into the thread chat**. The bot turns the batch into a
thread (template header + reply chain), then **cleans the originals in
the source chat**.

The v2 live-capture flow (buffer trigger, `/thread` as a reply to the
anchor, steps 8–9) is **cancelled**. The only thing that survives from
the buffer is live observation of the source chat (see §5).

## 2. Configuration

- **"source → thread" pairs live in the `pairs` database table**
  (PostgreSQL via `DATABASE_URL`, schema through Alembic migrations).
  `chats.json` is no longer read. Pairs are managed with the
  **`/settings` menu** in the bot's DM (open to `OWNER_ID` and to
  admins of any pair's chats): the list shows chat titles from
  `getChat`, a pair is added by forwarding a message from the chat or
  by typing `@username`/id (with self-pair, bot-rights and duplicate
  checks → inline confirmation) and removed with an inline button.
  Both add-flow prompts also carry a **chat picker**: buttons of every
  chat already known from the pairs (titles from `getChat`,
  `st:pick:<ref>`) plus `Cancel` (`st:cancel`).
- **Bot UI messages come from JSON language packs**
  (`bot/locales/*.json`, selected by `LOCALE`) — a custom pack can
  replace every literal without touching the code.
- **The bot is an admin in BOTH chats of a pair**, with
  `can_delete_messages` in both (mandatory in the source chat: it deletes
  other people's messages). The menu verifies this at pair-creation
  time.
- The bot operates **only** in configured thread chats; all other chats
  are ignored completely.

## 3. Flow

### Step 1. The batch

A source-chat admin selects messages → forwards them as one operation
into the thread chat. The bot (in a configured thread chat, from an
admin) accumulates the sequence of forwards into a **pending state**
(one per chat).

### Step 2. The source

- `forward_origin` carries a chat (channel/group) **and a pair is
  configured** → **proceed silently**;
- `forward_origin` carries a chat, **no pair** → ask;
- user origin (no chat visible — the typical forward-from-a-user case)
  → ask.

The question (as a reply to the first message of the batch):
`Which chat did you forward from? Reply with @username or its id.`

The question message also carries an **inline picker**: one button per
configured source whose pair targets the current chat (labels are chat
titles from `getChat`, `callback_data="w:src:<ref>"`) plus a `Cancel`
button (`w:cancel` = `/cancel`). A tap runs exactly the typed-answer
path (config check, terminal `not_configured`, stage advance); the
typed answer keeps working as before.

The admin's answer is checked against the config:

- **not configured → STOP**: no thread, no cleanup;
  reply `This chat is not configured as a source chat.`, state resets;
- configured → the source pair is pinned at step 5.

### Step 3. Thread title

Always (after "where from?" or without it) — as a reply to the first
message of the batch:
`Thread title? Send the title as a plain message.`

- **Any thread-chat admin** may answer (others' text is ignored);
- empty title → `Title is empty — send the thread title.` (keep waiting);
- longer than 128 characters → truncation (existing logic);
- `/cancel` from an admin → the bot deletes its own question messages,
  replies `Cancelled.`, the batch stays untouched (state reset).

### Step 4. Thread assembly (thread chat)

1. `send_message` — header: `Topic: <b>{title}</b>` (title escaped);
2. `edit_message_text` of the same message — adds
   `Please use <a href="{thread_url}">this link</a> to respond to this thread.`;
3. placing the batch **as replies to the header** (flat chain, in
   original send order):
   - elements sharing a `media_group_id` → **one `sendMediaGroup` call**
     (chunks ≤10) with `reply_parameters` to the header; captions and
     `caption_entities` from the originals, media by the originals'
     `file_id` (no re-upload) → **albums stay glued**;
   - single messages → `copy_message(..., reply_to_message_id=header)`;
   - types unsupported by `sendMediaGroup` or a call error →
     **fallback**: item-by-item copies (thread still completes, unglued);
4. `delete_message` of every **original forward** in the thread chat;
5. deleting its own question messages;
6. the reply to the moderator (one message, via render/escape):
   - `Thread created: {n} message(s). {thread_url}`
   - `Deleted {x} of {y} original messages.` (step 5, see below)

### Step 5. Source-chat cleanup

Only **after successful assembly** (order: thread first, cleanup second —
if assembly failed, the originals are not touched).

Finding each original:

- **channel origin** → directly by `forward_origin.message_id`
  (chat id matched the pair);
- otherwise → by the pair **(date from `forward_origin`,
  text/caption)** among source-chat messages **the bot saw live**
  (buffer since startup); media without text → **(date +
  `file_unique_id`)**;
- found → `delete_message`; not found → skip;
- `delete_message` failure → counted as "not found", the flow does not
  break.

The `Deleted {x} of {y}.` total — **partial success is normal**
(confirmed).

**Bot API limitation:** there is no history (no `getHistory`) — messages
sent **before the bot started** cannot be found → `Deleted 0 of Y`.
Key point to relay to TL: cleanup works only against live-seen messages
+ channel ids.

## 4. Failures and limits

- batch >100 → `Batch too large (101 messages, limit 100). Nothing was moved.`
  (real n; state reset, forwards stay). The limit is enforced **during
  accumulation**: the 101st forward is rejected immediately, without
  being added to the batch;
- an exception while sending the header, editing, or placing →
  `Move failed: {Type}. Check the target chat manually.`
  (type name, not `str()`); **no cleanup**, state reset,
  retry = forward the batch again;
- deleting the original forwards after assembly is **best-effort**: a
  `delete_message` failure on one of them does not produce
  `Move failed` (the thread is already built); the flow proceeds to
  cleanup and to success;
- a failure in step 5 → the final reply is still `Thread created…`
  with honest `{x} of {y}` (the thread is already built);
- `from_user is None` → not an admin (no crashes);
- one pending per chat; conflicting origins in a batch → ask
  "where from?" (one pair per batch).

## 5. What returns / is removed from v2

| Returns | Removed |
|---|---|
| Source-chat buffer — ONLY as the search base for originals in step 5 (records: message_id, date, text/caption, file_unique_id, sender id; matching: date + sender [when visible on both sides] + text, else caption, else file_unique_id) | Trigger buffer, `/thread`-on-anchor, window/anchor logic |
| Question state router ("where from?" → "title?") | Steps 8–9 (reply tracking, thread-chat auto-cleanup) |
| | `/target`, DM intake, redirect notices |

## 6. v1 scope

**In scope:** pair config, question state machine (with `/cancel`),
thread assembly (incl. **sendMediaGroup albums**, user decision),
cleanup with a partial report, all guards (limits, escaping, admin
checks), templates and URL builders.

**Out of scope:** AI, FastAPI/web, persistence of *pending sessions*
(restart = pending lost; the source buffer now persists in the
database), rate-limit / cooldown (backlog), rollback of a partial
assembly (backlog). The settings menu is no longer "TL side" — it is
implemented in this repo (see §2).

## 7. Technical requirements and TL notes

1. **Forward-as-reply is impossible** (`forwardMessage` has no
   `reply_to_message_id`; `message_thread_id` — forum topics only).
   The thread is built via `copyMessage` / `sendMediaGroup` — the
   "Forwarded from" attribution is lost. Deviation from the wiki
   ("forwards") — the thread takes priority.
2. **Bot API history is unavailable** — "I go in and find by
   timestamp+text" works only against live-seen messages (+ channel
   ids). Key clarification for TL.
3. `initialMessageUrl` and the redirect notice were removed from the v2
   templates (the original "main" message is not visible inside a
   forward) — deviation from the wiki.
4. `sendMediaGroup` supports `reply_parameters` (Bot API 7+); the copy
   fallback guarantees delivery even on errors.
5. Albums: gluing via `sendMediaGroup` — **accepted for v1**
   (instead of item-by-item copies).
6. The wiki "Bot commands" section is outdated: the commands are
   `/cancel` and `/settings` (the thread trigger is not a command but
   the forward itself).
7. Project language is English: all literals, bot replies — EN.
8. **Pair invariant:** pairs are configured only for chats with
   **shared administration** — a thread-chat admin drives cleanup in the
   source chat (the bot's rights in the pair constrain but do not
   replace that).
9. Options to discuss with TL: (a) answer correlation — accept
   "where from?"/"title?" only as replies to the bot's question (today
   any admin text in a pending chat may become the title);
   (b) late sessions — TTL (currently they live until `/cancel`);
   (c) known fallback risk: a `sendMediaGroup` timeout after the actual
   send → duplicated elements in the copy fallback.
10. **Second security review** (after DB/menu/pickers — all closed in
    code; verified by a follow-up re-review): pair authority checked at
    confirm time (caller must admin BOTH chats — item 8 now enforced;
    owner is exempt from BOTH the caller and the bot-rights rechecks,
    by design for bootstrap) with a 3 s verdict cache
    (`FRESH_TTL`) against confirm-spam, TTL + negative caches for admin
    lookups and `getChat` titles (`bot/admin_cache.py`, fail-closed,
    protects the API budget), pair-registry write lock, `st:*`
    private-only, safe edits, DSN hygiene in every raised chain,
    64-byte callback-data guard, `/settings` resets the step, `LOCALE`
    and `LOG_LEVEL` validation (INFO/DEBUG actually logged via
    `_configure_logging`), the >4300-digit answer guard, the ≤200-char
    `pair_added` alert, and CI hardening (`permissions: contents: read`
    + `timeout-minutes`; SHA-pinning/lockfile remain backlog).
    Still open (backlog/TL): buffer message retention policy
    (privacy), form-independent pair dedupe (`@Name` ≡ id ≡ `@name`),
    `SecretStr` for the token, menu scoping (only pairs relevant to
    the caller), the per-batch registry refresh in the source picker,
    an `st:del` recheck window for rows unknown to the cache, and
    admin-cache eviction/bot-keying — plus 9(a)/9(b), UTF-16-aware
    alert truncation (a 200-codepoint alert with heavy emoji may still
    exceed Telegram's UTF-16 limit — swallowed, never crashes),
    wrapping the remaining `callback.answer` calls against
    `TelegramBadRequest`, fresh-exception re-raise in the admin cache
    (a cached exception accumulates tracebacks per hit), and buffer
    write flood-protection (per-message INSERT+DELETE under spam).

## 8. Repo requirements

- Lint and tests stay green on every push: `ruff check --no-cache .`
  and `pytest -q`, enforced by CI (`.github/workflows/ci.yml`) on
  Python 3.10 and 3.14 for `main`/`develop`.
- The v3 forward-batch flow works end to end: source question, thread
  title, header → edit → flat-reply placement, albums glued per
  `media_group_id`, best-effort forward deletion, source cleanup and
  the success report with the deleted counter.
- The async database layer (SQLAlchemy + Alembic: `buffer_messages`,
  `pairs`) survives restarts, and migrations apply cleanly on a
  fresh database.
- `/settings` manages pairs with the pinned ACL and flows (list with
  `getChat` titles, forward-origin or typed add, inline delete,
  chat pickers).
- Every user-facing reply is a natural string looked up through
  gettext (`bot/i18n.py`, catalogs in `bot/locales/`) — never a
  per-locale branch inside the handlers.
- Secrets live only in `.env` (gitignored); errors and logs must not
  leak DSNs or tokens.

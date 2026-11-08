# Article Chat — Design

Date: 2026-11-07
Status: draft, awaiting approval

## Intent

Let the reader ask questions about the open article, chatbot-style, with
multi-turn follow-ups — on desktop web and in the iOS home-screen PWA. It is
the reader's second LLM feature, next to Summarize, and runs on the same
`claude` CLI against the owner's Claude subscription (never the API).

Why this shape: the owner wants Claude Code's own conversation handling
(history, system-prompt snapshot, auto-compact, prompt caching) instead of a
hand-rolled one, and the app runs on a 1GB VM that already swaps, so nothing
may stay resident between turns.

## Goals

- Ask a question about the open article and get an answer grounded in it.
- Follow-up questions continue the same conversation.
- Reopening an article shows its earlier conversation.
- Works well on desktop web and on iPhone (installed PWA).
- One conversation per (user, article): accounts don't see each other's.

## Non-goals

- Streaming answers token-by-token (decided: all-at-once for now; may revisit).
- Web search / tool use. Chat has no tools.
- Chatting across multiple articles or the whole feed.
- Regenerating or editing a past answer.

## Anti-goals (failures even if it "works")

- Anything that bills the Anthropic API instead of the subscription.
- A `claude` process that outlives a request, or two running at once on the VM.
- Answers that blend outside knowledge into article claims without marking it.
- A chat UI that fights the reader on iPhone (zooming inputs, swipe-nav
  firing while you scroll the thread, input hidden behind the keyboard).

## Constraints

- **VM:** 1GB RAM, swaps. `openreader.service` has `MemoryMax=700M`; one
  `claude -p` peaks around 300MB RSS (docs/WORKLOG.md, 2026-08-14). So at most
  one `claude` process may run at a time across the whole app.
- **Subscription auth** (same rules as `backend/app/summarize.py`, must not be
  relaxed): never pass `--bare`; scrub `ANTHROPIC_API_KEY` from the child env.
- **Determinism/footprint flags** carried over from Summarize:
  `--tools ""`, `--strict-mcp-config`, `--setting-sources ""`,
  `--disable-slash-commands`, `--model sonnet`.
- Chat **does not** pass `--no-session-persistence` — persistence is the point.
  Summarize keeps it.
- The CLI files sessions under `~/.claude/projects/<encoded cwd>/`, so the
  subprocess must always run with the **same fixed cwd** or `--resume` won't
  find the session.

## Decisions made with the owner

1. **Option B — CLI keeps the conversation.** First turn starts a session with
   `--session-id <uuid>`; later turns use `--resume <uuid>` and send only the
   new question on stdin. (Owner understands the CLI still sends the full
   context to the model each turn; the win is not owning history code.)
2. **Display copy in SQLite.** The UI reads history from our DB, never from the
   CLI's JSONL (internal format). The CLI's session file is the model context;
   the SQLite copy is for display and recovery.
3. **Reopen shows earlier conversation.**
4. **Layout: side panel on desktop, bottom sheet on iPhone** (mockup option A).
5. **Grounding:** answer from the article first; when it must go beyond the
   article, answer from general knowledge and clearly flag that part. If the
   article and general knowledge disagree, say so.
6. **Answers arrive all at once** (no streaming), like Summarize.
7. **Open to every signed-in account, no usage cap.** Every account spends the
   owner's subscription; the owner accepted that. No per-user limit, no 429.
8. **No article length cap** — same as Summarize (`limit=None`; WORKLOG ~line
   1353 records why). Largest article in the local DB is ~227KB of HTML, well
   under where compaction would kick in.

## Calls made during design (owner to check)

- **SQLite is the source of truth** when it and the CLI session disagree. The
  CLI may record a turn in its JSONL even when our request fails (timeout
  after the reply, bad envelope, client backgrounded). So after any failed
  or uncertain turn, mark the thread for reseed (e.g. a `needs_reseed` flag on
  `chat_sessions`, or delete the row): the next turn starts a **new** session
  UUID seeded from `chat_messages`. A first-turn retry always mints a new UUID
  (reusing a `--session-id` whose file exists would be refused).
- **Client on error re-fetches the thread (GET) before showing Retry**, so a
  turn the server finished (phone backgrounded mid-request) isn't sent twice.
- **One deadline per request: 300s total, including lock wait.** Subtract the
  time spent waiting for the lock from the subprocess timeout. If two requests
  are already waiting, a new one fails fast with 503 "busy, try again".
- **Apache proxy timeout must be ≥ 300s.** Summarize already depends on this
  but the repo doesn't record it; verify `ProxyTimeout`/`Timeout` on the VM
  before shipping and note it in WORKLOG.
- **Subscription-limit errors** get their own message ("Claude usage limit
  reached — try later") if the CLI's error text makes them distinguishable;
  otherwise the generic 503.
- Chat shares the `llm.enabled` kill switch; Sonnet; short answers; `c` hotkey;
  starter questions; dot on 💬 for threads
  with history; silent recovery from missing session files.

## Backend

### Data model (new tables, in `db.py` SCHEMA, created with `IF NOT EXISTS`)

```sql
CREATE TABLE IF NOT EXISTS chat_sessions (
    user_id    INTEGER NOT NULL REFERENCES users(id)    ON DELETE CASCADE,
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    session_id TEXT    NOT NULL,          -- CLI session UUID
    created_at TEXT    NOT NULL,
    PRIMARY KEY (user_id, article_id)
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id)    ON DELETE CASCADE,
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    role       TEXT    NOT NULL CHECK (role IN ('user','assistant')),
    body       TEXT    NOT NULL,          -- plain text for user, sanitized HTML for assistant
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_thread
    ON chat_messages(user_id, article_id, id);
```

Store functions in `store.py`: `get_chat(user_id, article_id)`,
`append_chat_turn(user_id, article_id, question, answer_html)` (inserts both
rows in one transaction), `get_or_none_chat_session`, `save_chat_session`,
`clear_chat(user_id, article_id)` (deletes session row + messages).

The article list's `has_summary`-style flag: add `has_chat` to the single-
article payload (cheap `EXISTS`) so the 💬 button can show a dot. Not needed in
the list endpoint.

### CLI wrapper (`backend/app/chat.py`, sibling of `summarize.py`)

Share the hardening with Summarize rather than duplicating: extract the common
base command + runner + `ClaudeError` + envelope parsing into a small shared
helper (builder's call on exact shape), keeping both modules' public functions.

- `ask(session_id, is_first_turn, article_text, question, runner=...) -> str`
  - First turn: `claude -p --session-id <uuid> --system-prompt <CHAT_PROMPT> ...`;
    stdin = article text (word count + text) followed by the question.
  - Later turns: `claude -p --resume <uuid> ...`; stdin = question only. The
    CLI's system-prompt snapshot (default on in CLI 2.1.303) reuses the
    first turn's prompt; still pass the same `--system-prompt` each time so it
    is correct if the snapshot is ever off.
  - `--output-format json --json-schema {"answer_html": string}`, parse
    `structured_output.answer_html`, run through `sanitize_html`. Same error
    handling as `summarize_text` (non-zero exit, timeout, bad JSON,
    `is_error`). Timeout: 300s like Summarize.
  - `cwd` = fixed chat working dir (below). Env scrubbed of `ANTHROPIC_API_KEY`.
- **Reseed path** (used both when `--resume` fails because the session file
  is missing — builder confirms the exact signal — and when the thread is
  marked for reseed after a failed turn): start a new session with a new UUID whose first stdin contains the article,
  then the prior Q&A from `chat_messages` rendered as a plain transcript, then
  the new question. Update `chat_sessions.session_id`. Silent to the user.

### System prompt (draft — builder may tighten wording)

> You are answering questions about one article for a personal RSS reader.
> The article text is given in the first message. Answer from the article
> first. When a question needs information the article doesn't contain, you
> may answer from general knowledge, but put that part in a separate
> paragraph starting with `<p class="beyond"><b>Beyond the article:</b>` so
> the reader can see it isn't from the text. If general knowledge contradicts
> the article, say so explicitly. Never invent quotes or figures and attribute
> them to the article. Keep answers short and conversational — a few
> sentences or a short list — unless the question asks for depth. Output HTML
> using only: p, br, b, strong, i, em, ul, ol, li, blockquote. Output must
> match the provided JSON schema.

`sanitize_html` must keep `class="beyond"` on `<p>` (check its allowlist;
extend narrowly if needed) so the frontend can style it muted.

### Concurrency: one `claude` at a time

A single process-wide lock (e.g. `threading.Lock` held inside the off-thread
call) around **every** `claude` invocation — chat and Summarize both. Summarize
today has no such guard; adding it is part of this work because two concurrent
calls would exceed `MemoryMax=700M`. A request waits for the lock within its
single 300s deadline (lock wait counts against it); if two requests are
already waiting, a new one gets 503 "busy, try again" immediately. This also guarantees two turns never
write the same session file at once.

### Working directory

`<data dir>/claude-chat/` (sibling of the SQLite file), created on startup if
missing. Builder picks how the path is derived from existing config.

### API

All require a signed-in user (401 otherwise) and `llm.enabled` (404 otherwise),
mirroring `summarize`. Chat shares the `llm.enabled` kill switch.

- `GET  /api/articles/{id}/chat` → `{"messages": [{id, role, body, created_at}, ...]}`
- `POST /api/articles/{id}/chat` body `{"question": str}` (trimmed, 1–2000
  chars) → `{"messages": [<user msg>, <assistant msg>]}`. On `ClaudeError`:
  503 `{"error": ...}` and **nothing is saved** (no orphaned question).
- `DELETE /api/articles/{id}/chat` → 204. Clears session row + messages. The
  CLI's JSONL is left for the CLI's own cleanup.

Article text for the first turn: `plain_text_excerpt(content_html, limit=None)`,
same as Summarize. The article is snapshotted at the session's first turn; a
later hydration doesn't update an existing session (acceptable — the reader
auto-hydrates on open, before a question is typically asked).

## Frontend

### Entry point

- New 💬 icon button in `ArticleReader` toolbar, between ✨ and ★. Hidden when
  `llmEnabled` is false or the user is anonymous (same gating as ✨).
- Small dot on 💬 when the open article has an existing thread (`has_chat`).
- Keyboard: `c` toggles the panel on desktop (unused today; existing keys are
  j/k/m/o/r). Ignore when focus is in an input.

### Desktop web (≥ the app's existing tablet/desktop breakpoint)

- Right-docked panel, 340–440px depending on window width; the article column
  narrows (no overlay). Same background as the reader. Focuses its input on
  open. (Revised 2026-11-08.)
- Header: "Ask about this article" · "New chat" (confirm) · close ✕.
- The panel belongs to the article it was opened on: moving to prev/next (or
  closing the article) closes it. Opening 💬 on another article shows that
  article's own thread. (Owner decision.)

### iPhone / narrow viewports

- Revised 2026-11-08 (owner): the half-height bottom sheet was replaced by a
  full-screen overlay. The sheet jumped to full height when the keyboard
  came up, and its drag handle and ✕ ended up under the camera island.
- Header below the safe-area inset: **Done** (left) · article title ·
  New chat.
- The overlay is sized to `visualViewport` (top and height), so the input sits
  directly on the keyboard; `env(safe-area-inset-bottom)` padding when the
  keyboard is down.
- Keyboard comes up on open for a new conversation only; an existing one opens
  with the keyboard down so the last answer is readable. iOS raises the
  keyboard only for a focus made during the tap, so the tap focuses a
  throwaway input and the panel takes focus over on mount
  (`frontend/src/chatLayout.ts`).
- Same layout in a phone browser outside the PWA (owner decision).
- Input font-size ≥ 16px (prevents iOS focus-zoom).
- `useSwipeNav` disabled while the chat is open.

### Thread

- On open: `GET` history, scroll to bottom.
- Text size matches the article: answers use the reading font at the
  article's size (19px desktop, 17px phone); questions the same size in the
  UI font. (2026-11-08.)
- User messages: right-aligned amber-wash bubbles (plain text, escaped).
  Assistant messages: left, rendered HTML styled like the Summary view;
  `.beyond` paragraphs in a muted color with the bold label.
- Sending: button → spinner, a "Thinking…" placeholder in the thread; the
  input stays editable but send is disabled until the answer lands. The
  question bubble appears optimistically and is rolled back on error.
- Error: re-fetch the thread first; if the turn actually landed, just show it.
  Otherwise show an inline error row with Retry and restore the question text
  to the input.
- Empty state: 2–3 tappable starters ("What's the main argument?", "What
  evidence does it give?", "What's left unsaid?") that send immediately.
- React Query: `['chat', articleId]` query; POST/DELETE mutations update it.

## Testing

- `test_chat.py` with a fake runner (same pattern as `test_summarize.py`):
  first-turn vs resume command shape; required flags present, `--bare` and
  `--no-session-persistence` absent; `ANTHROPIC_API_KEY` scrubbed; fixed cwd;
  envelope errors → `ClaudeError`; resume-missing → reseeded new session;
  failed turn → next turn reseeds with a new UUID; first-turn retry mints a
  new UUID.
- Lock: queue of two waiting → third request gets 503 busy; lock wait counts
  against the 300s deadline.
- API tests: auth/kill-switch gating, per-user isolation, nothing saved on
  failure, DELETE clears both tables.
- Lock test: two concurrent calls never overlap the runner.
- Manual: desktop panel and iPhone PWA full-screen chat with keyboard (real device).

## Deployment

No new VM setup: the `openreader` user already has `claude` on PATH and
logged in. The chat working dir is created by the app. Confirm on the VM that
`~/.claude/projects/` is writable by `openreader` and note the deploy in
docs/WORKLOG.md.

## Left to the builder

- Exact shared-helper refactor between `summarize.py` and `chat.py`.
- How the CLI signals "session not found" on `--resume`, and matching it.
- Panel/sheet component structure and the drag-sheet implementation
  (hand-rolled vs. small lib — prefer hand-rolled, no new heavy deps).
- Exact breakpoint (reuse the app's existing mobile breakpoint).

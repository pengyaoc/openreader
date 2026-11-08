# Article Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-article, per-user multi-turn chat in the reader, answered by the `claude` CLI on the owner's subscription, with the CLI keeping the conversation and SQLite keeping a display copy.

**Architecture:** A shared `claude_cli.py` holds the subscription-safe base command, env scrubbing, JSON-envelope parsing and a process-wide one-at-a-time slot used by both Summarize and chat. `chat.py` drives a turn (first / resume / reseed) against `store.py`'s two new tables. Three routes on `/api/articles/{id}/chat`. Frontend adds a `ChatPanel` (right panel ≥761px, bottom sheet ≤760px) opened from a 💬 toolbar button.

**Tech Stack:** Python 3 / Starlette / sqlite3 / nh3 / pytest (run via `uv run pytest` in `backend/`); React + TypeScript + TanStack Query + Vite (`npm run build`, `npm run lint` in `frontend/`).

**Spec:** `docs/superpowers/specs/2026-11-07-article-chat-design.md`

## Global Constraints

- Never pass `--bare`; always scrub `ANTHROPIC_API_KEY` from the child env.
- Every `claude` call carries `--tools ""`, `--strict-mcp-config`, `--setting-sources ""`, `--disable-slash-commands`, `--model sonnet`, `--output-format json`.
- Chat calls do NOT pass `--no-session-persistence`; Summarize keeps it.
- Chat subprocess cwd is always `Path(db_path).parent / "claude-chat"`.
- At most one `claude` process at a time (chat + Summarize share one slot); max 2 requests waiting, a third gets 503 `"busy, try again"`; one 300s deadline per request including lock wait.
- Missing session signal: exit code 1 and stderr containing `No conversation found with session ID` (verified against CLI 2.1.303).
- Chat open to every signed-in account; gated by the existing `llm.enabled` (404 when off), 401 when anonymous. No usage cap.
- Article text: `plain_text_excerpt(content_html, limit=None)` — no length cap.
- Question: trimmed, 1–2000 chars, else 400.
- SQLite is the source of truth: on a failed turn nothing is saved; an existing session is marked `needs_reseed`.
- Mobile breakpoint is the app's existing `max-width: 760px`. Chat input font-size ≥ 16px.
- Panel closes when the open article changes. `c` toggles it (not when typing in an input/textarea).

## Review Focus

1. Phone backgrounded mid-request: server saves the turn, client sees an error → client must re-fetch before showing Retry and not double-send. (Task 5 manual check.)
2. Session file deleted on the VM (CLI cleanup/rebuild) → next question silently reseeds from history and succeeds. (Task 3 test `test_resume_missing_reseeds_with_history`.)
3. Model emits `<p class="beyond">` plus other classes/attrs/script → only `class="beyond"` on `<p>` survives. (Task 3 test `test_sanitize_chat_html_keeps_only_beyond_class`.)
4. Summarize and chat requested at the same moment → never two `claude` processes. (Task 1 test `test_slot_serializes_and_rejects_third_waiter`.)
5. User B opens an article user A chatted on → B sees an empty thread; A's DELETE never touches B's. (Task 4 test `test_chat_is_per_user`.)

---

### Task 1: Shared CLI helper and the one-at-a-time slot

**Files:**
- Create: `backend/app/claude_cli.py`
- Modify: `backend/app/summarize.py` (use the helper; public API unchanged)
- Modify: `backend/app/api/articles.py:149-153` (`_run_summarize` acquires the slot)
- Test: `backend/tests/test_claude_cli.py`; existing `backend/tests/test_summarize.py` must still pass unchanged

**Interfaces:**
- Produces:
  - `class ClaudeError(Exception)` (moved here; `summarize.py` re-exports it so `from app.summarize import ClaudeError` keeps working)
  - `class ClaudeBusyError(ClaudeError)`
  - `class SessionMissingError(ClaudeError)`
  - `BASE_FLAGS: list[str]` — `["--tools", "", "--strict-mcp-config", "--setting-sources", "", "--disable-slash-commands", "--output-format", "json"]`
  - `Runner = Callable[[list[str], str, float, str | None], subprocess.CompletedProcess]` (cmd, stdin, timeout, cwd)
  - `default_runner(cmd, stdin_text, timeout, cwd=None)` — scrubs `ANTHROPIC_API_KEY`
  - `run_structured(cmd, stdin_text, timeout, cwd, runner, key: str) -> str` — runs, maps TimeoutExpired/non-zero/bad JSON/`is_error`/missing `structured_output[key]` to `ClaudeError`; non-zero exit whose stderr contains `No conversation found with session ID` → `SessionMissingError`
  - `class ClaudeSlot(max_waiting: int = 2)` with `@contextmanager acquire(deadline_s: float) -> Iterator[float]` yielding remaining seconds; raises `ClaudeBusyError("busy, try again")` if `max_waiting` already waiting, `ClaudeError` on lock-wait timeout
  - `CLAUDE_SLOT = ClaudeSlot()` module singleton
  - `DEADLINE_S = 300.0`

- [ ] **Step 1: Write failing tests** in `test_claude_cli.py`:
  - `test_default_runner_scrubs_api_key` — monkeypatch `subprocess.run`, set env `ANTHROPIC_API_KEY=x`, assert captured `env` lacks it and `cwd` is passed through.
  - `test_run_structured_maps_missing_session` — fake runner returns `returncode=1, stderr="No conversation found with session ID: abc"` → `pytest.raises(SessionMissingError)`.
  - `test_run_structured_other_failures_raise_claude_error` — parametrize: exit 2; stdout `"not json"`; `{"is_error": true, "result": "x"}`; `{"structured_output": {}}`; runner raising `TimeoutExpired` → each `ClaudeError` and not `SessionMissingError`.
  - `test_run_structured_returns_key` — stdout `{"structured_output": {"answer_html": "<p>a</p>"}}`, key `answer_html` → `"<p>a</p>"`.
  - `test_slot_serializes_and_rejects_third_waiter` — `ClaudeSlot(max_waiting=2)`; thread A holds `acquire(5)` until an Event; threads B and C call `acquire(5)` and block; D calls `acquire(5)` → `ClaudeBusyError`; release A; B and C each enter, never concurrently (track a counter, assert max 1).
  - `test_slot_yields_remaining_deadline` — hold the slot ~0.2s in another thread, then `acquire(1.0)` yields a value `< 1.0` and `> 0.5`.
  - `test_slot_wait_timeout` — held slot, `acquire(0.1)` → `ClaudeError`, not `ClaudeBusyError`.
- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_claude_cli.py -q` — expect ImportError/FAIL.
- [ ] **Step 3: Implement `claude_cli.py`.** `ClaudeSlot` = `threading.Lock` + a `threading.Lock`-guarded waiting counter; `acquire` uses `lock.acquire(timeout=deadline_s)` and yields `deadline_s - elapsed`.
- [ ] **Step 4: Refactor `summarize.py`** to build `["claude", "-p", "--model", model, "--system-prompt", ..., *BASE_FLAGS, "--no-session-persistence", "--json-schema", ...]` and call `run_structured(..., key="summary_html")` then `sanitize_html`. Keep `build_summarize_command`, `build_user_prompt`, `summarize_text` signatures; `summarize_text`'s `runner` default becomes a 3-arg adapter over `default_runner` so existing tests' 3-arg fake runners still work.
- [ ] **Step 5: Wrap `_run_summarize`** in `with CLAUDE_SLOT.acquire(DEADLINE_S) as remaining:` and pass `timeout=remaining` to `summarize_text`. `ClaudeBusyError` is a `ClaudeError`, so the existing 503 path covers it.
- [ ] **Step 6: Run** `uv run pytest -q` — all pass (328 existing + new).
- [ ] **Step 7: Commit** `git commit -m "Share claude CLI hardening and a one-at-a-time slot with Summarize"`

### Task 2: Chat tables and store functions

**Files:**
- Modify: `backend/app/db.py` (SCHEMA: add tables + index from the spec, plus `needs_reseed INTEGER NOT NULL DEFAULT 0` on `chat_sessions`)
- Modify: `backend/app/store.py`
- Test: `backend/tests/test_store.py` (append)

**Interfaces:**
- Produces (all take `conn` first, `user_id: int`, `article_id: int`):
  - `get_chat_messages(conn, user_id, article_id) -> list[dict]` — `{id, role, body, created_at}` ordered by `id`
  - `get_chat_session(conn, user_id, article_id) -> dict | None` — `{session_id: str, needs_reseed: bool}`
  - `save_chat_turn(conn, user_id, article_id, session_id: str, question: str, answer_html: str) -> list[dict]` — one transaction: upsert session row (`needs_reseed=0`, keep `created_at` on update), insert user then assistant rows; returns the two new message dicts
  - `mark_chat_needs_reseed(conn, user_id, article_id) -> None` (no-op if no row)
  - `clear_chat(conn, user_id, article_id) -> None` — deletes session row and messages

- [ ] **Step 1: Write failing tests:** `test_save_chat_turn_roundtrip` (two turns → 4 messages in order, roles user/assistant/user/assistant, session_id updated to second call's value, `needs_reseed` False); `test_mark_needs_reseed_then_save_clears_it`; `test_clear_chat_removes_both`; `test_chat_rows_cascade_on_article_delete` (delete article → no rows); `test_chat_scoped_per_user` (two users via `seed_user`, A's turn invisible to B).
- [ ] **Step 2: Run** `uv run pytest tests/test_store.py -q -k chat` — FAIL.
- [ ] **Step 3: Implement** schema + functions. Timestamps `datetime.now(UTC).isoformat()` like `save_summary`.
- [ ] **Step 4: Run** `uv run pytest -q` — all pass (also confirms `init_schema` is idempotent on existing test DBs).
- [ ] **Step 5: Commit** `git commit -m "Add chat session and message tables"`

### Task 3: Chat turn driver (`chat.py`)

**Files:**
- Create: `backend/app/chat.py`
- Test: `backend/tests/test_chat.py`

**Interfaces:**
- Consumes: Task 1 (`BASE_FLAGS`, `run_structured`, `default_runner`, `Runner`, `SessionMissingError`, `ClaudeError`), Task 2 store functions.
- Produces:
  - `CHAT_SYSTEM_PROMPT: str` — the spec's draft prompt verbatim (section "System prompt").
  - `CHAT_JSON_SCHEMA = {"type": "object", "properties": {"answer_html": {"type": "string"}}, "required": ["answer_html"]}`
  - `build_chat_command(session_id: str, resume: bool, model: str = "sonnet") -> list[str]` — `["claude", "-p", ("--resume" if resume else "--session-id"), session_id, "--model", model, "--system-prompt", CHAT_SYSTEM_PROMPT, *BASE_FLAGS, "--json-schema", json.dumps(CHAT_JSON_SCHEMA)]`
  - `build_first_prompt(article_text: str, question: str) -> str` — `f"Article text ({n} words):\n\n{article_text}\n\nQuestion: {question}"`
  - `build_reseed_prompt(article_text: str, history: list[dict], question: str) -> str` — article block as above, then `"Earlier conversation:\n"` with `User: …` / `Assistant: …` lines (assistant bodies via `plain_text_excerpt(body, limit=None)`), then `f"\nNew question: {question}"`
  - `sanitize_chat_html(html: str) -> str` — `nh3.clean` with tags `{p, br, b, strong, i, em, ul, ol, li, blockquote}`, no attributes, `allowed_classes={"p": {"beyond"}}`
  - `chat_turn(conn, user_id, article_id, content_html, question, cwd: str, timeout: float, runner: Runner = default_runner, new_id: Callable[[], str] = lambda: str(uuid.uuid4())) -> list[dict]` — returns the two saved messages

`chat_turn` algorithm (not determined by the signature):
```
session = get_chat_session(...); history = get_chat_messages(...)
text = plain_text_excerpt(content_html, limit=None)
if session and not session["needs_reseed"]:
    try: answer = ask(resume=True, session["session_id"], stdin=question)
    except SessionMissingError: answer, sid = reseed()
    except ClaudeError: mark_chat_needs_reseed(...); raise
    else: sid = session["session_id"]
elif history:  sid = new_id(); answer = ask(resume=False, sid, build_reseed_prompt(text, history, question))
else:          sid = new_id(); answer = ask(resume=False, sid, build_first_prompt(text, question))
return save_chat_turn(conn, user_id, article_id, sid, question, sanitize_chat_html(answer))
```
`reseed()` = the `elif history` branch. A failure in a new-session branch saves nothing (no session row written), so the next attempt mints a fresh UUID.

- [ ] **Step 1: Write failing tests** (fake runner records `(cmd, stdin, timeout, cwd)`; returns envelope `{"structured_output": {"answer_html": "<p>A</p>"}}` unless scripted otherwise; `new_id` yields `"s1"`, `"s2"`, …; DB via `connect`+`init_schema` in `tmp_path` with one source/article/user):
  - `test_build_chat_command_flags` — first and resume variants: `--bare` and `--no-session-persistence` absent; `--tools ""`, `--setting-sources ""`, `--strict-mcp-config`, `--model sonnet` present; `--session-id s1` vs `--resume s1`.
  - `test_first_turn_sends_article_and_question` — stdin contains article text and `Question: Why?`; cwd passed; returns 2 messages; session `s1` saved.
  - `test_second_turn_resumes_with_question_only` — stdin == `"And then?"`; cmd has `--resume s1`.
  - `test_resume_missing_reseeds_with_history` — second runner call raises via stderr `No conversation found with session ID: s1` (exit 1), third call succeeds: third cmd has `--session-id s2`, stdin contains `Earlier conversation:` and the first Q; stored session is `s2`; 4 messages total.
  - `test_failed_resume_marks_reseed_and_saves_nothing` — resume returns exit 2 → `ClaudeError`; messages unchanged; `needs_reseed` True; next call uses `--session-id s2` with reseed prompt.
  - `test_failed_first_turn_saves_nothing_and_retry_uses_new_id` — first call fails; no session row, no messages; retry uses `s2`.
  - `test_sanitize_chat_html_keeps_only_beyond_class` — input `<p class="beyond x"><b>Beyond the article:</b> y</p><p class="evil" onclick="z">q</p><script>1</script><a href="u">l</a>` → output keeps `class="beyond"` on the first `<p>`, no `x`, no `evil`, no `onclick`, no `<script>`, no `<a`.
- [ ] **Step 2: Run** `uv run pytest tests/test_chat.py -q` — FAIL.
- [ ] **Step 3: Implement `chat.py`.**
- [ ] **Step 4: Run** `uv run pytest -q` — all pass.
- [ ] **Step 5: Commit** `git commit -m "Add chat turn driver over CLI-kept sessions"`

### Task 4: Chat API routes

**Files:**
- Modify: `backend/app/api/articles.py` (three handlers), `backend/app/main.py` (routes; `app.state.chat_cwd`, `mkdir(parents=True, exist_ok=True)` at startup)
- Test: `backend/tests/test_api.py` (append; reuse `_client_with_llm_enabled`)

**Interfaces:**
- Consumes: `chat_turn` (Task 3), `CLAUDE_SLOT`, `DEADLINE_S`, `ClaudeError`, `ClaudeBusyError` (Task 1), store functions (Task 2).
- Produces (HTTP):
  - `GET /api/articles/{id}/chat` → 200 `{"messages": [...]}`
  - `POST /api/articles/{id}/chat` `{"question": str}` → 200 `{"messages": [user_msg, assistant_msg]}`; 400 bad question; 503 `{"error": str}` on `ClaudeError` (busy included)
  - `DELETE /api/articles/{id}/chat` → 204
  - All: 401 anonymous, 404 when `llm.enabled` false or article missing.
  - 503 message: if the `ClaudeError` text matches `/usage limit|rate limit/i`, return `"Claude usage limit reached — try later"`; otherwise the error text. (Spec: best-effort; test `test_chat_post_usage_limit_message`.)

- [ ] **Step 1: Write failing tests** (monkeypatch `articles.chat_turn`):
  - `test_chat_404_when_llm_disabled` (GET/POST/DELETE with default `client`).
  - `test_chat_401_when_anonymous` (follow how existing summarize/star anonymous tests build an anonymous client).
  - `test_chat_post_validates_question` — `""`, `"   "`, 2001 chars, missing key → 400; `chat_turn` not called.
  - `test_chat_post_then_get_roundtrip` — fake `chat_turn` calls real `store.save_chat_turn`; POST returns 2 messages; GET returns same.
  - `test_chat_post_503_on_claude_error` — fake raises `ClaudeError("x")` → 503 and GET still empty.
  - `test_chat_delete_clears` — after a turn, DELETE → 204, GET → `[]`.
  - `test_chat_is_per_user` — use the `two_users` fixture pattern; A's turn invisible to B; B's DELETE leaves A's thread.
  - `test_chat_post_passes_cwd_and_deadline` — fake captures kwargs: `cwd` ends with `claude-chat`, `timeout <= 300`.
- [ ] **Step 2: Run** `uv run pytest tests/test_api.py -q -k chat` — FAIL.
- [ ] **Step 3: Implement.** POST runs, off-thread, a helper that does `with CLAUDE_SLOT.acquire(DEADLINE_S) as remaining: return chat_turn(conn, ..., cwd=str(app.state.chat_cwd), timeout=remaining)`. Register the three methods on one `Route("/api/articles/{article_id}/chat", ..., methods=["GET","POST","DELETE"])` dispatching by method, or three routes — match `main.py` style.
- [ ] **Step 4: Run** `uv run pytest -q` — all pass.
- [ ] **Step 5: Live smoke test (real CLI, local):** start the dev server via `.claude/launch.json` (create an entry for the backend if missing), enable `llm.enabled` in local config, POST two questions to one article with curl; confirm second answer references the first; delete the session file under `~/.claude/projects/*claude-chat*/` and POST again → 200 (reseed). Note the observed latency.
- [ ] **Step 6: Commit** `git commit -m "Add article chat API"`

### Task 5: Frontend chat panel

**Files:**
- Modify: `frontend/src/api.ts` (`ChatMessage` type; `getChat`, `sendChat`, `clearChat`)
- Create: `frontend/src/components/ChatPanel.tsx`
- Modify: `frontend/src/components/ArticleReader.tsx` (💬 button, dot, `c` key, panel mount, close on `article.id` change, pass `enabled: !chatOpen` to swipe)
- Modify: `frontend/src/useSwipeNav.ts` (`enabled?: boolean` option, default `true`; touch handlers no-op when false)
- Modify: `frontend/src/App.tsx` (pass `isAnonymous`-aware `chatEnabled = llmEnabled && !isAnonymous` to the reader)
- Modify: `frontend/src/index.css` (panel/sheet styles)

**Interfaces:**
- Consumes: Task 4 HTTP API.
- Produces:
  - `interface ChatMessage { id: number; role: 'user' | 'assistant'; body: string; created_at: string }`
  - `api.getChat(id: number): Promise<{ messages: ChatMessage[] }>`; `api.sendChat(id: number, question: string): Promise<{ messages: ChatMessage[] }>`; `api.clearChat(id: number): Promise<void>`
  - `<ChatPanel articleId={number} onClose={() => void} />`
  - React Query key `['chat', articleId]`

Behavior (all from the spec; listed so nothing is missed):
- 💬 between ✨ and ★, only when `chatEnabled`. Prefetch `['chat', id]` on article open when `chatEnabled`; dot when it has ≥1 message (replaces the spec's server `has_chat` flag — one cached GET serves both dot and panel).
- `c` toggles (ignore when `e.target` is input/textarea/contenteditable or modifier held). Panel closes when `article.id` changes.
- ≥761px: right-docked ~340px column, article column narrows. ≤760px: bottom sheet at 50% height, handle drags to full / down past threshold closes; on input focus expand to full; bottom offset follows `window.visualViewport` (height + offsetTop) so the composer sits above the keyboard; `padding-bottom: env(safe-area-inset-bottom)`.
- Header: "Ask about this article", "New chat" (`window.confirm`, then `clearChat`), ✕.
- Thread: scroll to bottom on load/new message. User bubbles right, amber wash, text escaped (render as text). Assistant via `dangerouslySetInnerHTML` (server-sanitized) using the Summary view's body class; `.beyond` muted color.
- Empty state starters: "What's the main argument?", "What evidence does it give?", "What's left unsaid?" — tap sends.
- Send: textarea (font-size 16px, Enter sends on desktop, Shift+Enter newline; on touch Enter inserts newline), optimistic user bubble + "Thinking…" row, send disabled while pending (typing allowed). On error: `refetch` the thread first; if the last stored user message equals the question, just show it; else roll back the optimistic bubble, show inline error with Retry, restore the question into the input.

- [ ] **Step 1: Implement** api.ts additions and `useSwipeNav` `enabled`.
- [ ] **Step 2: Implement** `ChatPanel.tsx` and CSS.
- [ ] **Step 3: Wire** into `ArticleReader`/`App`.
- [ ] **Step 4: Run** `cd frontend && npm run lint && npm run build` — no errors.
- [ ] **Step 5: Browser check (desktop):** preview the app, open an article, `c` opens panel, starter question gets an answer, follow-up works, prev/next closes the panel, reopening shows history and dot, New chat clears. Screenshot.
- [ ] **Step 6: Browser check (mobile):** `resize_window` preset mobile; sheet opens at half, drag to full, swipe-nav disabled while open, input not zooming (computed font-size ≥16px), composer visible. Screenshot. (Real-device keyboard behavior is a manual owner check after deploy.)
- [ ] **Step 7: Commit** `git commit -m "Add article chat panel (side panel / bottom sheet)"`

### Task 6: Docs

**Files:**
- Modify: `docs/WORKLOG.md` (dated 2026-11-07 entry: design summary, shared slot now also gates Summarize, CLI missing-session signal, chat cwd), `README.md` (one Features bullet for Article chat, matching the Summarize bullet's tone), `backend/app/config.py:64` comment ("kill switch for Summarize and Chat")

- [ ] **Step 1: Write** the entries.
- [ ] **Step 2: Run** `cd backend && uv run pytest -q` and `cd frontend && npm run build` once more — green.
- [ ] **Step 3: Commit** `git commit -m "Document article chat"`

Deploy (`scripts/deploy.sh`) is not part of this plan: it needs the owner's go, plus a check on the VM that Apache's `ProxyTimeout`/`Timeout` ≥ 300s.

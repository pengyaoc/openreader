# OpenReader — Technical Design & ERD

Status: reflects the app as built (2026-08-13). Companion to `docs/PRD.md`.

## 1. Architecture overview

```
┌─────────────────────────┐        ┌──────────────────────────────────┐
│  Frontend (React/Vite)   │  HTTP  │  Backend (Starlette, Python)      │
│  3-pane UI, dark/light,  │◄──────►│  Sync request handlers only —     │
│  responsive drawer        │  /api  │  no request ever does network     │
└─────────────────────────┘        │  I/O except /api/refresh, /api/img │
                                    │  and /api/topics/:key/generate     │
                                    └──────────────┬─────────────────────┘
                                                    │
                        ┌───────────────────────────┼───────────────────────────┐
                        ▼                           ▼                           ▼
              ┌──────────────────┐       ┌──────────────────┐        ┌──────────────────────┐
              │ RSS connector     │       │ IMAP connector    │        │ Generation worker      │
              │ httpx + defusedxml│       │ imaplib, app       │        │ out-of-process,        │
              │ conditional GET   │       │ password auth      │        │ spawned via subprocess │
              └──────────────────┘       └──────────────────┘        │ runs `claude` CLI       │
                        │                           │                └──────────────────────┘
                        └───────────────┬───────────┘                           │
                                        ▼                                        ▼
                              ┌────────────────────┐                  ┌──────────────────┐
                              │  Ingest pipeline     │                  │  jobs table        │
                              │  rules → dedup →     │                  │  (status tracking) │
                              │  persist              │                  └──────────────────┘
                              └────────────────────┘
                                        │
                                        ▼
                              ┌────────────────────┐
                              │  SQLite (WAL)        │
                              │  sources / articles / │
                              │  jobs                 │
                              └────────────────────┘
```

Key architectural rule: **the request path never blocks on external I/O**
except the three endpoints that are inherently about triggering external
work (`/api/refresh`, `/api/img`, `/api/topics/:key/generate`). Everything
else — article list, sources, config, job status — is a local SQLite read.

## 2. Backend module map

```
backend/app/
├── main.py            # Starlette app factory, route table
├── asgi.py             # production entrypoint (uvicorn app.asgi:app)
├── settings.py         # env-driven paths (DB, config, media, IMAP
│                        # host/user/password, readonly-config flag,
│                        # auth password hash/session secret)
├── config.py            # msgspec Config/Source/Rule/Topic structs,
│                        # YAML parse+validate+serialize (to_yaml),
│                        # credential-key guard (see §5)
├── auth.py               # app-layer login: bcrypt password check,
│                         # HMAC-signed session cookie, AuthMiddleware
│                         # (see §5)
├── db.py                # schema (see §3), WAL connection factory
├── store.py             # read/write helpers used by the API layer
├── connectors/
│   ├── base.py           # NormalizedEntry — the shape every connector emits
│   ├── rss.py             # Atom/RSS2.0/RDF parser (defusedxml)
│   ├── dates.py           # RFC822 + ISO8601 -> canonical UTC ISO string
│   ├── http_fetch.py       # conditional-GET wrapper (ETag/Last-Modified)
│   └── imap.py               # IMAP client + MIME parser, app-password
│                              # auth — type=imap, the only newsletter
│                              # connector (see §5 for why)
├── ingest/
│   ├── rules.py             # regex include/exclude engine (pure)
│   ├── dedup.py              # URL canonicalization + content hash (pure)
│   ├── textutil.py            # excerpt extraction, sanitize, image-proxy
│   │                          # rewrite, newsletter-HTML whitespace tightening
│   ├── extract.py             # readability heuristic + relative->absolute URLs
│   ├── hydrate.py              # lazy per-article full-text fetch (once, ever)
│   └── refresh.py               # orchestrates one sync refresh pass;
│                                 # also reconcile_read_state() (§5)
├── generate/
│   ├── prompt.py                 # system prompt + JSON schema for the model
│   ├── client.py                  # `claude` CLI subprocess wrapper
│   ├── jobs.py                     # SQLite job status transitions
│   └── worker.py                    # out-of-process job runner (__main__)
└── api/
    ├── sources.py, articles.py, refresh_api.py, config_api.py,
    │ images.py, generate_api.py, auth_api.py   # thin Starlette handlers
    │                                            # over the above
```

Design rule followed throughout: **pure logic is separated from I/O** and
every I/O boundary (HTTP fetch, IMAP socket, subprocess call) is injectable
in tests via a `Fetcher`/`Runner`/`GenerateFn`-style callable parameter, so
the entire pipeline is unit-tested without a real network call or
subprocess spawn. 192 backend tests, zero of which touch the network.

## 3. Data model (ERD)

```mermaid
erDiagram
    sources ||--o{ articles : "has many"
    jobs ||--o{ articles : "produced (llm origin only)"

    sources {
        int id PK
        text key UK "stable identifier from config, or topic.key for llm sources"
        text type "rss | imap | llm"
        text title
        text folder
        text url "null for imap/llm"
        text etag "RSS conditional-GET cache"
        text last_modified
        text last_fetched_at
        text last_error
        text last_error_at
    }

    articles {
        int id PK
        int source_id FK
        text guid "unique per source; feed guid / email Message-Id / job-N-index"
        text url "empty string for llm-generated articles"
        text canonical_url
        text title
        text author
        text published_at "canonical UTC ISO-8601, see connectors/dates.py"
        text fetched_at
        text excerpt "boilerplate-filtered, ~900 chars"
        text content_html "sanitized, images rewritten through /api/img"
        text content_hash "sha256(canonical_url + normalized title)"
        text matched_rule "which include/exclude rule let this through"
        text origin "feed | email | llm"
        int job_id FK "set only when origin=llm"
        text citations_json "sources[] for llm articles"
        text hydrated_at "lazy full-text fetch completed"
        text hydrate_failed_at "lazy full-text fetch failed (suppresses retry)"
        bool is_read
        text read_at
        bool is_starred
    }

    jobs {
        int id PK
        text topic_key "matches config Topic.key, not a FK (config-defined)"
        text status "queued | running | done | error"
        text brief_snapshot "the brief at job creation time, for auditability"
        text model
        text started_at
        text finished_at
        text error
        int articles_created
    }
```

Notes on choices that aren't obvious from the columns alone:

- **`UNIQUE(source_id, guid)`** on `articles` is the entire dedup
  mechanism for re-ingestion: a refresh that sees the same guid again is a
  no-op insert, which is also what makes refresh idempotent and cheap to
  run repeatedly.
- **LLM-generated articles get a real row in `sources`** (`type='llm'`,
  keyed by the topic's `key`), created on first generation. This means
  generated content reuses the exact same list/folder/unread-count
  machinery as RSS/IMAP sources for free — no parallel data path in the
  API or frontend for "generated" content.
- **`content_hash`** is *not* currently used for cross-source duplicate
  detection (e.g. the same story from two outlets) — it's indexed for a
  future dedup pass but the only active dedup key today is `(source_id,
  guid)`.
- **`published_at` is always normalized to UTC ISO-8601** at ingest time
  (`connectors/dates.py`), specifically because RSS 2.0's RFC822 dates
  (`Tue, 11 Aug 2026 05:30:24 +0000`) and Atom's ISO-8601 dates sort
  incorrectly against each other as raw strings — this was a real bug
  found and fixed mid-build (see WORKLOG).

## 4. API surface

Every route below except `/api/login`/`/api/logout` is gated by
`AuthMiddleware` (§5) whenever login is configured — permissive (no
cookie required) otherwise, matching this app's local/LAN default.

| Method & path | Purpose | Blocking I/O? |
|---|---|---|
| `POST /api/login` | Verify password against `READER_AUTH_PASSWORD_HASH`, set the session cookie (§5) — unauthenticated (has to be, nothing to authenticate with yet) | bcrypt check off the event loop (`asyncio.to_thread`) — deliberately ~100-300ms |
| `POST /api/logout` | Clear the session cookie — always 200, a no-op if there was nothing to clear; unauthenticated | No |
| `GET /api/sources` | List sources with unread counts, filtered to keys present in the live config — a source removed from `feeds.yaml` stops appearing here immediately, even though its DB row and articles aren't deleted (§5) | No |
| `POST /api/sources` | Structured add-source, any type (rss/imap) (validates, writes YAML, creates DB row) | No |
| `GET /api/sources/:id` | Full detail for one source (url/query/mailbox_folder/fetch_full_text/rules) — `list_sources` deliberately omits these; backs the edit form's pre-fill | No |
| `PUT /api/sources/:id` | Edit a source's fields in place; `key`/`type` locked to the existing entry regardless of what's sent (§5) | No |
| `DELETE /api/sources/:id` | Remove from config only — DB rows/articles untouched, same behavior as a raw-YAML removal (§5) | No |
| `POST /api/sources/:id/mark-all-read` | Bulk-mark every unread article on one source | No |
| `GET /api/articles` | List articles (`view=all\|unread\|starred`, `source_id`, `folder`, `limit`=50 default, `offset`) | No |
| `GET /api/articles/:id` | Article detail; triggers lazy full-text hydration if applicable | One-time per article, but off the event loop (`asyncio.to_thread`, §5) — doesn't block other requests |
| `POST /api/articles/mark-all-read` | Bulk-mark every unread article across every source (registered ahead of `:id` in the route table, or it'd be swallowed by that pattern) | No |
| `POST /api/articles/:id/read` | Mark read (idempotent, one-directional) | No |
| `POST /api/articles/:id/toggle-read` | Manual read/unread toggle | No |
| `POST /api/articles/:id/star` | Toggle starred | No |
| `POST /api/refresh` | Refresh (`?source=key` for one); RSS fetches concurrently (bounded pool), IMAP sequentially over one shared connection (§5) | Runs off the event loop (`asyncio.to_thread`, §5) — doesn't block other requests, but is still the slowest single call in the app (~2-5s typical) |
| `GET/PUT /api/config` | Read/write `feeds.yaml` (validates on write); `PUT` also runs `reconcile_read_state` (§5) before responding | No |
| `GET /api/img` | Image proxy (strips Referer, sidesteps hotlink protection) | SSRF-check DNS lookup runs off the event loop (`asyncio.to_thread`, §5); the actual fetch is async `httpx` |
| `GET /api/topics` | List configured topics + `llm.enabled` flag | No |
| `POST /api/topics/:key/generate` | Create a job, spawn worker subprocess, return immediately | No (spawn is fire-and-forget) |
| `GET /api/jobs/:id` | Poll job status | No |

## 5. Key technical decisions (and why)

**Synchronous, manual refresh — no scheduler.** Simpler to reason about
and test; the per-source report (`fetched/new/filtered/error`) is the
thing that actually matters when tuning regex rules, and you only get that
naturally from a synchronous, on-demand call.

**RSS sources fetch concurrently (bounded thread pool); IMAP sources
fetch sequentially over one shared connection; the DB write path stays
single-threaded regardless.** Measured live, 2026-08-13: 9 sequential RSS
fetches took 7.7s with no single dominant outlier — just ordinary
per-host TLS+network latency, paid one at a time. `refresh_source`
(RSS)'s logic is split into a fetch half (`_rss_fetch_only`, pure network
I/O, safe on a pool worker) and a persist half (`_persist_rss_result`,
all DB writes, always on the calling thread) because `sqlite3` connections
default to `check_same_thread=True` — persistence can never move to a
worker thread, only the fetch can. `refresh_all()`'s whole call also runs
via `asyncio.to_thread` from the API layer (own SQLite connection, same
pattern as the SSRF-guard and hydration fixes above) — proven live to
matter, not assumed: a request fired 0.3s into a refresh, before that fix,
sat blocked for the remaining 8.4s before uvicorn's single event loop
could serve anything else.

IMAP briefly went through the same per-source-concurrent shape RSS uses
(`_refresh_imap_batch`, one connection per source via a `connect_fn`
factory, opened concurrently) but was reverted the same day: IMAP is a
stateful protocol, so parallelizing meant every refresh opened as many
fresh TLS+LOGIN sessions as there were IMAP sources, from one datacenter
IP — indistinguishable from abuse to Gmail's IMAP servers, which respond
by silently stalling the connection (accept the handshake, never answer
LOGIN) rather than erroring, hanging the request forever since
`imaplib.IMAP4_SSL` also had no socket timeout at the time. Two fixes
landed together: `connect()` now passes an explicit `timeout` so a stall
fails instead of hanging, and `_refresh_imap_sequential` opens one
connection via `connect_fn` and reuses it across every IMAP source in the
refresh, sequentially, via the single-source `refresh_imap_source` —
fewer logins, indistinguishable from a normal mail client. See
docs/WORKLOG.md, 2026-08-13.

**Lazy, per-article full-text extraction, not a background hydration
pass.** Bandwidth and CPU are spent only on the ~10% of articles actually
opened. `hydrated_at`/`hydrate_failed_at` make it a strict once-ever
operation with a hard 5s timeout that falls back to the feed's own
summary rather than erroring.

**Image proxy (`/api/img`) is mandatory, not optional.** Several real
feeds (dapenti.com/xilei, discovered mid-build) hotlink-protect their
images on `Referer` — a direct `<img src>` from our own origin gets
silently blocked. Routing every image through the server sidesteps that
and also avoids leaking the reader's IP/referrer to third-party hosts on
every article view.

**`claude` CLI subprocess, not the Anthropic API.** Generation runs
against the user's Claude subscription via OAuth (keychain), not
`ANTHROPIC_API_KEY`. Two invariants protect that: never pass `--bare`
(its own docs: under `--bare`, OAuth/keychain are never read — only
`ANTHROPIC_API_KEY`), and the API key is explicitly scrubbed from the
child environment regardless.

**`--permission-mode bypassPermissions`, not `dontAsk`.** Found live,
mid-build: `dontAsk` silently *denies* every WebSearch/WebFetch call in
headless mode — the run completes successfully with zero real research
done, which is a dangerous silent-failure shape (you'd get a job marked
"done" with 0 articles and no obvious reason why). `bypassPermissions`
actually lets the two tools `--tools` already restricts the model to
run. Locked in with a regression test
(`test_build_command_uses_bypass_permissions_not_dont_ask`).

**Generated articles carry hard provenance.** `origin='llm'`, a visible
"Generated by Claude" badge, a mandatory Sources block with real citation
URLs, and the exact brief snapshot stored on the job row — because
synthesized text sitting next to real reporting is easy to misread later,
and the stored brief is what lets you debug a topic producing bad output.

**IMAP refresh is scoped incrementally, not a fixed re-query.** A source's
configured `query` (e.g. `from:x@example.com newer_than:30d`) only bounds
the *first* refresh's window. Every refresh after that computes SEARCH
SINCE from the source's `last_fetched_at`, minus a one-day overlap (IMAP
SEARCH SINCE is date-only, no time-of-day, so the overlap has to be a
whole day rather than a few minutes) — a routine refresh SEARCHes only
messages since it last ran instead of re-walking the source's entire
configured window every single time. Re-seeing a message id in that
overlap is a no-op, not a duplicate, since persistence already dedupes on
the parsed message's Message-Id.

**Article list is paginated at the API, not truncated.** `GET
/api/articles` defaults to `limit=50` with an `offset` param; the frontend
uses `useInfiniteQuery` and a "Load more" affordance rather than fetching
everything at once or (the bug this replaced) silently showing only the
first 50 with no way to reach anything past it.

**Reader state persists through two independent layers, not one.** Which
article/view is open lives only in frontend React state, so any reload —
including one the app never gets a chance to react to, like iOS reclaiming
a backgrounded tab's memory — would otherwise drop it. State is mirrored
into both the URL (`history.replaceState`, `?sel=...&article=...`) and
`sessionStorage` on every change. Neither is redundant: the URL layer
gives a shareable/bookmarkable link, but on iOS, third-party browsers
(Chrome, Firefox, Edge — all required by Apple to run on WKWebView) don't
reliably sync `replaceState` calls back to their own native
tab-restoration bookkeeping, so a reload can revert to an older URL
snapshot than the page's actual last state. `sessionStorage` has no
dependency on the navigation stack at all, so it survives that gap; the
URL stays as the primary/shareable source when both are available (`App.tsx`,
`parseSelectionFromQuery` / `readStoredViewState`).

**Nested `<table>` layout in email content gets contained, not stripped.**
HTML newsletters nest tables several levels deep for both padding
(a real, common authoring pattern) and pure spacer chains (empty, no real
purpose). Rather than extracting content out of that structure (e.g. a
readability-style density-scored rewrite — considered, but risks silently
dropping real content that doesn't score as dense prose, and this app's
existing `ingest/extract.py` readability heuristic isn't wired up for
table-laid-out input today), the reader keeps the original DOM and patches
specific failure shapes as they're found live: tables become independently
scrollable blocks capped at 100% width (mobile overflow), only the
outermost table in a nest keeps a visual-break margin (nested padding
tables would otherwise compound into large blank gaps), and fully empty
table chains are pruned the same way empty `<p>` spacers are
(`ingest/textutil.tighten_newsletter_whitespace`).

**Image proxy re-validates every redirect hop, not just the original
URL.** Found while evaluating internet-facing deployment: `/api/img` took
any URL from an unauthenticated request and fetched it server-side with
`follow_redirects=True` — a direct forwarder into the host's own
localhost or a cloud provider's internal metadata network if pointed at
one. `_assert_safe_url` resolves the hostname and rejects loopback,
private, link-local, multicast, and reserved addresses; `follow_redirects`
is replaced with a manual loop (capped at 3 hops) that re-runs the same
check on every `Location` header, since a redirect from an otherwise-public
host is exactly how the direct check gets bypassed. Known accepted gap:
this isn't atomic with the actual connection, so a DNS record that changes
between the check and httpx's own connect (rebinding) isn't covered —
closing that needs a custom transport pinning the resolved IP, out of
scope for what's actually been seen against open image proxies in the
wild.

**`feeds.yaml` fields are checked against a credential-shaped-key
denylist at parse time.** `GET /api/config` serves the file's raw
contents unauthenticated by design (the structured "Add source" UI and
the raw-YAML editor both round-trip through it). msgspec's `Config`
struct silently *drops* unknown fields on convert rather than rejecting
them — verified directly, not assumed — so a `password:`/`token:`/etc.
key smuggled into a source would previously have been accepted, silently
stripped from the parsed `Config`, but still sitting in the file on disk
and re-served by the next `GET`. `_check_no_credentials` runs against the
raw parsed YAML dict, before struct conversion would hide the problem,
and `parse_config` raises `ConfigError` rather than allowing the write.

**`READER_READONLY_CONFIG` is a deployment-time flag, not a permission
system.** By default v1 has no auth requirement — `PUT /api/config` is
reachable by anyone who can reach the port. That's an acceptable LAN-only
default, but not once the app is reachable from the internet: the same
endpoint can rewrite the entire source list *and*, since it also assigns
`app.state.config`, flip `llm.enabled` back on at runtime regardless of
what's on disk. Two ways to close that gap, both legitimate depending on
what's in front of the app: set the flag (hard-403, edit config via SSH
instead — what a deployment with no auth layer at all should do), or put
real auth in front of the whole app and leave the flag unset, since the
endpoint is no longer anonymously reachable. The VM deployment moved from
the former to the latter on 2026-08-13 (initially Apache Basic Auth, then
the app-layer login described below, same day).

**App-layer login (`app/auth.py`) replaced Apache Basic Auth the same
day it was added.** Basic Auth's `WWW-Authenticate` popup turned out to
be invisible to Chrome's password-manager save UI on any platform (that
only hooks real `<form>` submissions), and its credential cache is an
in-memory, browser-session/tab-lifetime thing mobile Chrome discards
aggressively on backgrounding — no server-side knob extends it, so the
login prompt reappeared very frequently on mobile. `AuthMiddleware` gates
every `/api/*` route except `/api/login`/`/api/logout` with a
`SameSite=Lax`/`HttpOnly`/`Secure` session cookie, stateless and
HMAC-signed (`READER_SESSION_SECRET`) — no session table, no
garbage-collection, verifying is just recomputing one HMAC and checking
an expiry timestamp. The password itself is a bcrypt hash
(`READER_AUTH_PASSWORD_HASH`), generated locally the same way the old
`.htpasswd-reader` was (`htpasswd -nbB`) — the plaintext never touches
the server, only ever arriving transiently in a login request body before
`bcrypt.checkpw` (run via `asyncio.to_thread`, since it's deliberately
~100-300ms of blocking CPU — same off-event-loop pattern as this app's
other blocking work) discards it. Both env vars unset (the default) means
no login is required at all, matching every other optional credential in
this app — only exactly one set fails closed, since that looks like a
deploy mistake rather than an intentional choice. Apache's role shrank
back to a pure reverse proxy (`ProxyPass` only, no `AuthType Basic`
block) once this was live.

**`type: imap` is the only newsletter connector — a `type: gmail` OAuth
connector existed early on and was fully removed 2026-08-13.** Discovered
when planning an unattended deployment: any app in "Testing" publishing
status — which personal/hobby OAuth clients stay in indefinitely — has
every refresh token revoked after 7 days, and escaping that for a
*restricted* scope like `gmail.readonly` requires full Google verification
plus a paid CASA security assessment. Re-running a one-time OAuth consent
script by hand every week is viable on a laptop, not on a box meant to run
unattended, so `type: gmail` never had a realistic path to working on the
VM. `connectors/imap.py` authenticates with an app password over plain
`IMAP4_SSL` instead — no token, no expiry, no Cloud project — and its
`parse_query` understands the same `from:`/`subject:`/`newer_than:Nd`
tokens Gmail's own search box uses, for familiarity, even though nothing
Gmail-specific remains in the codebase. Deliberately paired with a
dedicated, throwaway mailbox rather than the user's real inbox: an app
password isn't scoped per-protocol the way OAuth is — it authorizes SMTP
too, not just IMAP read access — so the blast radius of the credential
living on a deployed box is bounded by what's *in* that mailbox, not by
the credential's own scope.

**A source removed from `feeds.yaml` is filtered out, never deleted.**
`sources` DB rows are create-only — written once by `get_or_create_source`
the first time a source is ever refreshed — so nothing was removing a
row just because the source later disappeared from config, and the
sidebar kept showing it forever (found live, 2026-08-13: *"Uber
engineering still show in the left side bar"*). `list_sources()` now
takes an optional `valid_keys` set and filters to it; the API handler
passes the live config's keys. Its already-fetched articles are neither
deleted nor hidden from `All items`/`Starred` — only newly out-of-scope
*unread* ones get swept into `is_read=1` by `reconcile_read_state()`
(next entry), same as any other read article, fully reversible by
toggling read state back by hand.

**`reconcile_read_state()` runs on every `PUT /api/config`, marking
`is_read=1` — not hiding, not deleting.** When a source is removed or its
rules change, its previously-ingested articles don't retroactively
disappear on their own; without this they'd sit in `Unread` forever, no
longer relevant but never resolved. Considered and rejected: a new
`hidden` column (first implementation — real code, reverted) that would
have made these invisible in every view including `All items`/`Starred`,
requiring the app's first schema migration (`ALTER TABLE ... ADD COLUMN`,
non-trivial against DBs with real data on both the local machine and the
live VM). Live user feedback settled it: *"Why do you need migration? You
can just mark them as READ, right? That's existing feature."* — correct,
and simpler. The real tradeoff accepted by using plain `is_read` instead
of a dedicated flag: an article hidden this way is indistinguishable from
one the user genuinely read themselves, so there's no way to tell "was
filtered out, might be worth resurfacing if the rule loosens again" apart
from "actually read." Judged not worth a schema change to solve. Only
`is_read=0` rows are touched — already-read articles (by either path)
are left alone, so a genuinely-user-read article's `read_at` is never
overwritten.

**Editing a source locks `key`/`type`; deleting only ever touches
config, never the DB.** `PUT /api/sources/:id` accepts a request body
that could technically include `key`/`type`, and silently overwrites
whatever it sends for those two fields with the existing entry's actual
values — not a validation error, a deliberate no-op, because both are
identity: `_persist_entry`'s dedup is `(source_id, guid)` keyed off a
stable `key`, and changing it (or `type`, which determines which refresh
path a source even goes through) is really "delete this one, create a
different one," not an edit. `DELETE /api/sources/:id` reuses exactly the
behavior a raw-YAML removal already had before any UI existed for it —
`list_sources`' `valid_keys` filter hides it, `reconcile_read_state`
sweeps its still-unread articles to read — so the new button isn't a
second, more destructive way to remove a source than hand-editing the
file always was.

**`get_or_create_source()` only ever inserts — building the edit endpoint
surfaced a real, pre-existing gap.** Every refresh path calls it, but it
has no update branch: an existing row's `title`/`folder`/`url` columns
never change again once written, even when `feeds.yaml` does. Not new
behavior — refresh has always worked this way — but it meant a source
edited through the UI would validate, write to disk, and then not visibly
change in the sidebar, since nothing was ever going to refresh those
columns from config on its own. `update_source` (`sources.py`) writes
`title`/`folder`/`url` directly instead of assuming a future refresh will
reconcile them — it won't.

**Newsletter sources get a query *builder*, not a raw text field.** The
whole point of this tool is not asking the user to hand-write
`from:x subject:"y" newer_than:Nd` any more than they'd hand-edit YAML —
`SourceModal.tsx`'s `decomposeQuery()`/`composeQuery()` mirror
`connectors/imap.py`'s `parse_query()` regexes closely enough that an
existing source's query round-trips through the friendly from/subject/
window fields on edit, not just compose cleanly on create.

**Dependency minimalism.** Deliberately avoided FastAPI/pydantic
(Starlette + msgspec instead) and feedparser (stdlib
`defusedxml.ElementTree` + a ~150-line normalizer instead) — each swap
was chosen to keep the idle resident footprint small for a service meant
to run indefinitely on a personal machine. IMAP uses stdlib `imaplib`
directly, so newsletters need no client library at all, unlike the
now-removed Gmail OAuth path (`google-auth-oauthlib`).

## 6. Testing strategy

- **Unit, no network/subprocess**: every connector, the rules engine,
  dedup, date normalization, the readability extractor, the image proxy
  rewriter, and the `claude` CLI wrapper are tested via injected
  fetch/runner functions against fixtures. 192 tests as of this writing —
  see `docs/WORKLOG.md` for what each new round added.
- **Live verification for the pieces that can't be meaningfully faked**:
  real RSS feeds (including a CJK-language, hotlink-protected, non-UTF8
  one), real Chrome browser sessions (desktop + mobile viewport), and one
  real `claude` CLI generation run — which is what surfaced the
  `bypassPermissions` bug no unit test could have caught, since it's a
  property of the real CLI's headless permission handling.
- **No automated frontend test suite yet** (see PRD open questions) —
  frontend changes were verified via `tsc --noEmit`, `vite build`, and
  live browser testing per change.

## 7. Deployment / local operation

- Backend: `uv run uvicorn app.asgi:app --host 0.0.0.0 --port 8787` (LAN-
  reachable by default; `READER_CONFIG`/`READER_DB` env vars override
  paths).
- Frontend dev: `npm run dev` (Vite, also bound to `0.0.0.0`, proxies
  `/api` to `127.0.0.1:8787`). Production build (`npm run build`) is
  served directly by the backend via `StaticFiles` when `frontend/dist`
  exists.
- Newsletters: set `READER_IMAP_HOST`/`_USER`/`_PASSWORD` (an app
  password against a dedicated mailbox) — no setup script, no consent
  flow, nothing to re-run (see §5's `type: imap` entry).

### 7.1 Co-hosted VM deployment (`pengyaochen.com/reader/`)

Runs alongside an existing WordPress site on a single e2-micro (1 GB RAM).
Full history of the tradeoffs and what was found along the way is in
`docs/WORKLOG.md`, 2026-08-12; this is the resulting shape.

- **Path-prefixed, not subdomain.** Reuses the existing vhost/cert instead
  of provisioning a new one. `vite.config.ts`'s `base` is
  `VITE_BASE=/reader/` at build time; `frontend/src/api.ts` reads it back
  via `import.meta.env.BASE_URL` into an `API_BASE` constant prefixed onto
  every `fetch` call. Apache's `ProxyPass`/`ProxyPassReverse` strips the
  prefix before the request reaches uvicorn, so **the backend has zero
  `/reader/` awareness** — `content_html`'s baked-in `/api/img?url=...`
  (written once at ingest by `textutil.proxy_image_urls`) is rewritten to
  the prefixed path client-side, at render time, in
  `ArticleReader.tsx`'s `withApiBase` — the one `dangerouslySetInnerHTML`
  site — rather than server-side or via a DB migration.
- **Build artifacts pushed, not pulled.** `scripts/deploy.sh` runs
  entirely on the developer's machine: typecheck, `pytest`, `VITE_BASE=/reader/
  npm run build`, then the backend source + `frontend/dist` go up via
  `gcloud compute scp` and land via `cp` (not `rsync` — the VM's apt broke
  when Debian 10/buster was archived at EOL, so nothing gets installed to
  fix that). Node/Vite never run on the VM. `git pull` isn't used either —
  deliberately: it wouldn't help with the build problem, and a laptop-side
  gate (tests fail → nothing ships) beats a VM that can end up mid-broken-
  state from a bad pull.
  `uv sync` on the VM resolves prebuilt `manylinux` wheels for
  `nh3`/`selectolax`/`msgspec`/`uvloop` — no Rust toolchain needed, which
  matters on a box this constrained.
- **`openreader` runs as a systemd `--user` unit**, not a system service —
  no root involvement in routine deploys/restarts. Needs
  `loginctl enable-linger openreader` once so the unit survives an SSH
  logout, and the sandboxing directives (`ProtectSystem=strict`,
  `MemoryMax=300M`, etc.) that would normally need root to install are
  applied the same way a system unit would, at the cost of the
  credential file being owned by that user rather than root (a user
  manager can only read files it owns) — see §5's IMAP entry for why the
  credential in that file is scoped to a throwaway mailbox specifically
  because of this.
- **`llm.enabled: false`** on this deployment — the `claude` CLI subprocess
  (Node, 300–600 MB, up to 10 minutes) is the one thing that would
  reliably OOM a 1 GB box also running Apache/MySQL/PHP-FPM.
- **App-layer login** (`READER_AUTH_PASSWORD_HASH`/`READER_SESSION_SECRET`
  set in `/opt/openreader/openreader.env`, see §5) stands in front of the
  whole app — `READER_READONLY_CONFIG` is intentionally *not* set here as
  a result (see §5's entry on that flag): the config-write endpoint no
  longer needs its own lock once nothing unauthenticated can reach it at
  all. Briefly Apache Basic Auth instead (2026-08-13, same day) — dropped
  once its `WWW-Authenticate` popup turned out to be invisible to Chrome's
  password-manager save UI and its credential cache got evicted by mobile
  Chrome constantly enough to reprompt very frequently; Apache is back to
  a pure reverse proxy now.
- Verified live end-to-end post-deploy: real RSS refresh (conditional-GET
  304s and dedup both confirmed against real feed servers, not just
  fixtures), the SSRF guard rejecting a loopback/metadata `/api/img` URL,
  and all four `type: imap` sources authenticating against a dedicated
  mailbox over real TLS.

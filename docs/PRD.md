# OpenReader — Product Requirements Document

Status: reflects the app as built (2026-08-14). Update this doc when product
behavior changes — it should never drift further than a day or two behind
the running app.

## 1. Problem

Feed readers force a choice: control (self-hosted RSS, no smarts) or
convenience (AI-curated feeds, no transparency about *why* you're seeing
something) — usually behind a monthly subscription either way. The user
wants full control over information intake — programmatic filtering they
can inspect and edit, running on their own infrastructure, with AI
assistance (when they want it) billed through their own subscription
rather than a SaaS markup.

## 2. Users

Single-user, self-hosted, local-network app. Built for one person (an
engineering manager) running it on their own machine and reading from
other devices (phone, tablet) on the same LAN.

## 3. Product principles

- **Transparency over magic.** Every article that appears can be traced to
  a rule that let it through and the feed it came from. No black-box
  ranking.
- **Deterministic by default, LLM by exception.** RSS and newsletter
  ingestion never call a model. The one LLM feature (summarization) is
  opt-in, per-article, and only ever runs when you press the button.
- **Manual trigger over automation.** No background scheduler. You press
  Refresh. You press Summarize. Nothing runs on a timer.
- **Small footprint.** Runs on a personal machine — or the smallest VM
  your cloud provider sells — indefinitely. It should idle near-zero, not
  run an always-on daemon farm, so self-hosting it never costs more than
  the subscription it replaces.

## 4. Core features

### 4.1 RSS/Atom feeds with regex filtering

- Add any RSS/Atom feed by URL, title, and folder.
- Per-source regex rules: `include` / `exclude`, matched against
  `title | summary | content | author | url | any`.
- Semantics: any matching `exclude` drops the item. If the source has ≥1
  `include` rule, an item must match one to pass; with none, everything
  not excluded passes. Every kept article stores *which* rule let it
  through (`matched_rule`), so filtering is auditable, not just applied.
- Refresh is **synchronous and manual** — press Refresh, get a per-source
  report (`fetched` / `new` / `filtered` / errored), see immediately what
  your rules did. No polling, no scheduler.
- A broken source (bad feed, network error, 4xx/5xx) is isolated — it
  never blocks other sources from refreshing, and its error is visible in
  the sidebar.

### 4.2 IMAP newsletters

- Read-only IMAP integration (SEARCH/FETCH only, mailbox opened with
  `readonly=True` — never STORE/EXPUNGE/DELETE), authenticated with an app
  password rather than OAuth — no consent screen, no token to refresh,
  nothing that expires and needs re-auth (see §7 for why OAuth was dropped).
- An `imap`-type source is a saved search query using Gmail-search-style
  syntax (`from:`/`subject:`/`newer_than:`); each matching message becomes
  an article, same filtering rules apply.
- **Scoped, incremental fetching.** After the first refresh, each source's
  SEARCH is automatically narrowed to messages since that source's last
  successful refresh (with a one-day overlap window, since IMAP SEARCH
  SINCE is date-only) — a routine refresh lists only what's new rather
  than re-walking the source's entire configured window (e.g.
  `newer_than:30d`) every time.
- All IMAP sources in one refresh share a single connection, opened once
  and reused sequentially — a fresh login per source, opened concurrently,
  is indistinguishable from abuse to some mail providers and gets silently
  throttled (found live, 2026-08-13, see docs/WORKLOG.md).
- **One SEARCH/FETCH pass per mailbox folder, not one per source
  (2026-08-14).** Sources sharing a `mailbox_folder` are batched into a
  single SEARCH; each returned message is fetched once and matched
  locally against every source sharing that folder (its own `from:`/
  `subject:` tokens, then the regex rules), instead of each source
  running its own server-side-filtered SEARCH. A newly-added source
  backfills at most 7 days on its first sync regardless of its own
  `newer_than:` setting, so it can't force a much wider re-scan of a
  folder its already-synced siblings share.
- Newsletter HTML is cleaned up for readability: empty spacer paragraphs,
  stacked `<br>` runs, and long `&nbsp;` runs (both artifacts of HTML email
  templates, not article content) are collapsed at ingest time.

### 4.3 On-demand article summarization

- A **Summarize** button in the fullscreen reader, next to Star — for
  adhoc, personal use (not a bulk/scheduled feature), it turns the
  already-open article's own text into a summary via the user's own
  Claude subscription (not pay-per-token API billing), gated by a single
  config flag (`llm.enabled`) that hides the UI, 404s the API, and
  guarantees `claude` is never invoked while it's off. Off by default.
- **Faithful, not creative:** grounded purely in the article's own text —
  no outside knowledge, no speculation, no invented facts/quotes. Always
  the `sonnet` model.
- **Proportional length:** at least `max(100 words, 20% of the original
  article's word count)` — a one-paragraph note gets a short summary, a
  long-form piece gets a substantive one, not a fixed-size blurb either
  way.
- **Formatted for readability:** bullet points and bold highlighting for
  key terms/figures where it helps scanning, rendered with the same
  styling as a regular article body — not a wall of plain text.
- **Generated once, kept forever:** the summary is stored and shown again
  on a later visit to the same article, with no re-generation path (no
  "refresh" button) since nothing in this feature calls for one. A
  Full/Summary toggle lets you swap views without losing either one.
- A failed or timed-out call leaves the article's stored summary
  untouched — never a corrupted/partial one persisted.

### 4.4 Reading experience

- Three-pane layout: sources/folders sidebar, article list (title +
  subtitle + thumbnail), fullscreen reader.
- Opening an article in fullscreen is what marks it read (not hovering,
  not scrolling past it in the list).
- Prev/next navigation inside the fullscreen reader (labeled buttons at
  the end of the article body, reachable by scrolling to it, + arrow
  keys), without ever flashing back to the list between articles.
- Keyboard shortcuts: `j`/`k` move, `o`/`Enter` open, `Esc` close, `m`
  toggle read, `r` refresh.
- Full-text extraction is **per-article, once ever**: the list shows the
  feed's own summary until the full page is fetched and extracted, then
  cached forever. As of 2026-08-14, most of that happens automatically at
  the end of each refresh (bounded batch, eligible sources only) rather
  than purely on open — opening an article usually hits an already-cached
  row; the on-open fetch is still there as a fallback for anything the
  batch hasn't reached yet.
- A **Pull full article** button in the reader (left of Summarize) lets you
  force that same fetch-and-extract on demand, regardless of the source's
  `fetch_full_text` setting — for sources that leave it off, or whenever
  the auto-hydrated text came back thin. Same one-shot semantics as the
  passive path: it disappears once an attempt has been made (success or
  failure) for that article, and the pulled full text replaces the partial
  one in place — a later Summarize click naturally summarizes the full
  article, not the truncated excerpt, since it reads whatever `content_html`
  is stored at that point.
- Images always render through a same-origin proxy, so hotlink-protected
  images (a real issue with several real-world feeds) still load, and the
  reader's IP/referrer is never leaked to third-party image hosts.
- Dark and light themes; responsive down to phone width with a collapsible
  sidebar drawer. Newsletter HTML (deeply nested layout tables in
  particular) is contained within its own scrollable region on narrow
  viewports rather than blowing out the page's width.
- Article list is paginated (50 at a time, "Load more" at the bottom)
  rather than silently capping at the first page with no way to see older
  items once a folder/view passes that count.
- Subtitle/excerpt text filters out feed-boilerplate (e.g. auto-generated
  "Article URL: ... / Comments URL: ..." lines some feeds emit) so it
  actually helps decide whether to read something.

### 4.5 Configuration

- `config/feeds.yaml` is the single source of truth for sources, rules,
  and the summarization kill switch (`llm.enabled`).
- Editable two ways inside one Settings drawer (⚙): a **Feeds** tab —
  search-and-pick list backed by a structured add/edit form with a visual
  rule builder for the common case — or an **Advanced** tab, the raw YAML
  editor (validates before writing — a bad regex or malformed YAML never
  reaches the file) for config keys the structured form doesn't cover.

## 5. Non-goals (v1)

- X/Twitter integration — no viable free API path as of 2026; deferred.
- Google News as a source type — deferred.
- LLM-based relevance scoring/ranking, or LLM-generated new articles for
  topics with no feed — removed 2026-08-14 (see docs/WORKLOG.md): the one
  LLM feature this app keeps is on-demand summarization of an article you
  already have, not sourcing new content. Filtering stays regex-only.
- Any form of scheduling or background polling.
- Multi-user auth, accounts, or sharing.
- OPML import/export.
- Mobile app (the responsive web UI is the mobile story).

## 6. Success criteria

- You can add a feed with a filter rule and verify, from the refresh
  report, that it filtered what you expected — without reading code.
- The app idles at negligible CPU/memory on a personal machine and never
  makes an unattended network call.
- Read/unread state survives restarts and refreshes exactly the articles
  you've actually opened — no more, no less.

## 7. Open questions / known gaps

- The compact readability extractor (not a full Mozilla-Readability port)
  occasionally pulls page chrome (e.g. a GitHub repo's file-listing table)
  instead of the real article on unusual page layouts.
- A Gmail OAuth connector (`type: gmail`) existed early on but was removed
  2026-08-13: Google expires refresh tokens after 7 days for any app in
  "Testing" publishing status, and escaping that for a restricted scope
  like `gmail.readonly` needs full app verification + a CASA security
  audit — not viable for a personal project, and a bad fit for a
  deployment meant to run unattended (e.g. a VM, vs. re-authenticating by
  hand from a laptop every week). `type: imap` (`connectors/imap.py`)
  against a dedicated newsletter-only mailbox with an app password is now
  the only newsletter path: no token expiry, no Cloud project, no consent
  screen. See docs/WORKLOG.md, 2026-08-12 and 2026-08-13.
- No automated frontend test suite yet — frontend correctness has been
  verified through manual/live browser testing per change, not CI.

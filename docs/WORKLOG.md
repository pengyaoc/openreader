# OpenReader — Worklog

Append-only. Newest at the bottom. Each entry: what changed, why, and the
feedback that triggered it (if any). This is the "why did we do it this
way" record — `docs/PRD.md` and `docs/ERD.md` are the current-state
snapshots; this is the history.

---

## 2026-08-10 — Initial build (steps 1–5)

Brainstormed and planned via a structured design pass, then built
test-first (TDD) in order: config/YAML loading + validation, the regex
rules engine, RSS/Atom/RDF parsing, the synchronous refresh pipeline, and
the Starlette API + React 3-pane shell. 47 backend tests passing at this
point; first point the app was actually usable end-to-end.

**Bugs found via live browser testing (not caught by unit tests):**
- Excerpt field showed raw, unstripped HTML in the list view. → added
  `plain_text_excerpt` (selectolax-based tag stripping).
- Unread rows without a thumbnail were visually broken — CSS grid
  auto-placement put the unread dot in column 1 and squeezed the title
  into the `auto` column, sized to content instead of filling the row.
  → switched `.article-row` from grid to flexbox.
- Sidebar footer (Refresh/Configure buttons) was invisible — a flex child
  without `min-height: 0` grew to its content's height instead of the
  container's, and got clipped by `overflow: hidden`. Same bug existed
  one level up on `.shell` (a CSS Grid version of the same issue) — fixed
  both with `min-height: 0` / `minmax(0, 1fr)`.

## 2026-08-10 — Lazy full-text extraction (step 6)

Feedback: *"full text extraction should be done on demand for an article
rather than doing all ahead of time"* — this removed a whole background
"hydrate" pipeline stage from the original plan in favor of per-article,
on-open extraction with a 5s timeout and summary fallback.

## 2026-08-11 — Gmail connector + LLM generation mode (steps 7–8), requested together

Feedback: *"do the rest of the tasks including task 7"* — built the
Add-Source UI (task 7, backend endpoint had shipped earlier but no
frontend), the Gmail connector, and LLM generation mode in one pass.

Along the way, real bugs surfaced through live testing rather than unit
tests (which is expected — these are exactly the class of bug unit tests
with injected fetchers can't catch):

- **Test fixture clobbered the real `config/feeds.yaml`.** The API test
  client fixture didn't override `config_path`, so it defaulted to the
  production config file — a `PUT /api/config`-exercising test silently
  overwrote the real file with test data. Fixed the fixture to always use
  a tmp path; restored the real config from known-good content.
- **`--permission-mode dontAsk` silently denies tool calls in headless
  mode.** A live generation run completed "successfully" with 0 articles;
  inspecting the raw response showed `permission_denials` for every
  WebSearch/WebFetch call. Switched to `bypassPermissions` (safe here
  because `--tools` already restricts the model to those two read-only
  tools) and added a regression test.
- **RSS date sorting was broken across mixed feed formats.** RSS 2.0's
  RFC822 dates and Atom's ISO-8601 dates were stored as raw strings;
  `ORDER BY published_at` did a lexicographic string comparison, which
  only sorts correctly within one format. Added `connectors/dates.py` to
  normalize every date to UTC ISO-8601 at ingest time.
- **Hotlink-protected images.** A real feed (dapenti.com/xilei) blocks
  direct image loads based on `Referer`. Built `/api/img`, a same-origin
  proxy that fetches server-side with no `Referer`, and rewrote every
  `<img src>` at persist time to go through it.
- **Feed content silently dropped when `content:encoded` was absent.**
  Some feeds (same dapenti.com/xilei case) put the full HTML body —
  images included — directly in `<description>` with no `content:encoded`
  at all; the persist logic only ever read `content:encoded` into
  `content_html`, so the real body was discarded and only a truncated
  plain-text excerpt ever showed. Added a fallback.
- **Excerpt boilerplate.** hnrss.org-style feeds emit a `description`
  that's pure "Article URL: ... / Comments URL: ... / Points: N" link
  echo, not a summary — useless for deciding whether to read something.
  Added paragraph-level filtering to drop known boilerplate lines before
  truncating to the excerpt.

Feedback along the way, applied directly:
- *"Also worklog file..."* → this file.
- *"Handle feed refresh gracefully... only ingest newer items"* → already
  true (dedup on `(source_id, guid)` + conditional GET), confirmed rather
  than rebuilt.
- *"The feed should still be coherent after two pulls and order by
  timestamp"* → the date-normalization fix above.
- *"Seems the previous read articles become unread now... is it related
  to feed refresh?"* → **not** a refresh bug. Several `rm -f
  data/reader.db*` commands during this session's dev iteration wiped the
  whole DB, including read state set while testing in between fixes.
  Confirmed refresh never touches `is_read` for an existing row
  (idempotency test), and stopped wiping the DB mid-session going forward.

## 2026-08-11 — UX pass: mobile, theming, navigation

Feedback, applied in sequence:
- *"I want a light theme version... full screen view should render images
  well"* → added a light palette (`:root[data-theme="light"]`), toggle
  button, persisted to `localStorage`.
- *"Feed item width should have a max-width"* → capped list rows and the
  reader body at readable line lengths; later *"Full screen article's
  max-width can be a bit wider"* → widened the reader from 660px to 760px.
- *"Make sure both Chinese and English renders well... larger font"* →
  added Noto Sans SC as a CJK-native fallback in every font stack; bumped
  base/title/body sizes across list and reader.
- *"Use Sans Serif instead of Serif fonts"* → swapped Newsreader (serif)
  for Hanken Grotesk (sans) as the reading font.
- *"Full screen should have left/right button... to go to prev/next
  article"* → added, then found and fixed a real bug: navigating flashed
  the list page before showing the next article, because React Query
  resets `data` to `undefined` while a new `article_id` query loads and
  the reader was conditionally rendered on that data existing. Fixed by
  falling back to the already-cached list article as an instant
  placeholder, so the reader never unmounts mid-navigation.
- *"Left, right button are covering text on small devices... make them
  less visually intrusive and transparent"* → ghosted the nav buttons on
  mobile (no fill, no border, low opacity) instead of repositioning them.
- *"Left panel should be collapsible on mobile"* → off-canvas drawer with
  backdrop, hamburger trigger, closes on selection.
- *"Restart the server to allow local network access... iOS UX... rename
  to OpenReader"* → bound both dev servers to `0.0.0.0`; `100dvh` +
  `env(safe-area-inset-*)` for iOS Safari's collapsing address bar and
  notch; full responsive breakpoint at 760px; renamed throughout
  (package.json ×2, page title, sidebar wordmark).
- *"unread count is not correct. It can't be same as All items"* → the
  "All items" badge was computed with the same formula as "Unread" by
  mistake. Then: *"Actually remove the number for All items. It is not
  useful"* → removed the badge entirely rather than fixing the count.
- *"icon and text are not aligned"* (nav row) → the icon glyph was sized
  but not flex-centered within its box; also removed the default blue
  browser focus outline in favor of a themed one.

## 2026-08-11 — Docs

Feedback: *"What docs did you keep for future refresh about product and
technical design?"* → answer was "none, beyond a stale pre-implementation
plan file outside the repo." Followed by: *"Put product design (PRD) and
technical design (ERD) into 2 docs under docs folder"* and *"Also worklog
file that contains the iterations of changes + my feedback"* → this file
plus `docs/PRD.md` and `docs/ERD.md`.

## 2026-08-11/12 — More sources added, and three real proxy/link bugs found live

Added via the structured `POST /api/sources` API (dogfooding it rather
than hand-editing YAML): 爱范儿 (ifanr, include-filtered on "早报"),
阮一峰的网络日志 (ruanyifeng, unfiltered), 少数派 (sspai, include-filtered
on "派早报", later given `fetch_full_text: true` via a `PUT /api/config`
edit since the feed only carries a teaser), 每日环球视野 (idaily,
unfiltered, single-digest-item feed), 知乎日报 (zhihudaily, added then
**removed** — see below).

**rsshub.app is unusable as a source.** *"add https://rsshub.app/zhihu/daily"*
→ blocked by a Cloudflare Managed Challenge (`cf-mitigated: challenge`,
a JS proof-of-work no server-side HTTP client can solve — confirmed via
direct curl, with and without browser-shaped headers). Not a bug in this
app: rsshub.app's own plaintext error response says they deliberately
gate the public demo against feed-reader traffic "due to cost
considerations" and recommend self-hosting. Works in a real browser only
because a browser has a JS engine to solve the challenge and a matching
TLS fingerprint. Tried `https://feedx.net/rss/zhihudaily.xml` as an
alternative per user request — it parsed fine but turned out to be a
**dead mirror**: *"latest content is very very old"* → confirmed via the
origin's own `Last-Modified: Tue, 16 Sep 2025` header, ~11 months stale,
not a caching or date-parsing issue on our end. User chose to drop 知乎日报
for now; source removed from `feeds.yaml` (its already-fetched articles
were left in the DB rather than deleted, since only config removal was
asked for).

**Bug: article-body links navigated away in the same tab.** Reader-chrome
links ("original source," citations) already used `target="_blank"`, but
links *inside* rendered article content (feed/Gmail/LLM body HTML) had no
target set at all — a real gap, not covered by any existing test. Fixed
at the sanitize layer: `nh3.clean(..., set_tag_attribute_values={"a":
{"target": "_blank"}})`, which forces it on every `<a>` regardless of
what the source HTML requested, and applies uniformly to every content
origin in one place. Backfilled all 44 existing articles with links in
the live DB so the fix applied retroactively, not just to new content.

**Bug: sspai.com images 404'd through the proxy — contradictory Referer
policies.** The proxy sent no Referer at all (the original dapenti.com
fix), which dapenti needs (blocks a *foreign* Referer) but sspai's CDN
rejects (requires *a* same-site Referer to be present, full 403
otherwise). A fixed policy can't satisfy both. Fixed by deriving Referer
from the image URL's own origin — same-site by construction, verified
live against both sites, plus a pure `referer_for()` unit test.

**Bug: ~90% of 爱范儿's images silently failed — proxy trusted a lying
Content-Type header.** User reported *"some of the images are not showing
up"*. Traced every image URL across the 4 ingested 早报 articles through
the proxy directly: 80 of 89 were rejected with the proxy's own 415, not
a network error. Root cause: s3.ifanr.com (Qiniu-backed storage, used for
Lark/Feishu-pasted screenshots) serves genuine PNGs as
`Content-Type: application/octet-stream`. The proxy trusted that header
exclusively and rejected real images outright. Fixed with
`sniff_image_type()` — checks actual magic bytes (PNG/JPEG/GIF/WebP) as a
fallback whenever Content-Type doesn't say `image/*`, only overriding
when the header is actually wrong. Verified live: all 89 images in the
affected articles now load; visually confirmed a previously-broken
screenshot renders in the reader.

147 backend tests passing at this point — up from 136 (the count when
`docs/ERD.md` was first written): +2 for the forced-new-tab sanitize
behavior, +3 for Referer derivation, +6 for magic-byte sniffing.

## 2026-08-12 — Four Gmail newsletters added, and four more real bugs found live

Feedback: *"there is no email pulled into the reader... I want 'WSJ What's
News', Snacks from hello@snacks.robinhood.com, The Batch @
DeepLearning.AI from thebatch@deeplearning.ai"*, then *"Also BiggerPockets
from info@m.biggerpockets.com"*.

Looked up each newsletter's actual sender address via the connected Gmail
account before writing config (rather than guessing): `access@interactive
.wsj.com` for WSJ What's News (subject varies day to day, so filtered on
sender + an exclude rule for the "do you still want to receive" retention
email, not on subject), `hello@snacks.robinhood.com`, `thebatch@deeplearning.ai`,
`info@m.biggerpockets.com`. Added all four as `type: gmail` sources in a
new `Newsletters` folder, `newer_than:30d` as the initial bound.

Gmail OAuth hadn't been set up on this machine at all. Walked through it
live: Cloud project → enable Gmail API → OAuth consent screen → **hit a
real gotcha**: consent failed with `Error 403: access_denied` /
"has not completed the Google verification process" until the account was
added under **Test users** on the consent screen (undocumented in the
original Gmail setup doc — added to `README.md` afterward so the next
setup doesn't hit the same wall blind).

**Bug (checked for, not found): `cid:` inline-image attachments.** Before
assuming the existing image pipeline would "just work" for newsletters,
fetched real messages from all four senders via the Gmail connector and
grepped for `cid:` references — none of the four sources use inline MIME
attachments for images (all hosted HTTPS, via cmail20.com, HubSpot,
Sailthru, etc.), so the existing `/api/img` proxy handled them with zero
code changes. Verified by actually fetching a sample image URL from each
sender through the proxy logic and confirming 200s.

**Bug: mobile horizontal scroll on email-sourced articles.** *"Email
source doesn't respect max width on mobile device causing page to be able
to scroll left & right."* Root cause: WSJ/BiggerPockets newsletters build
their layout from deeply nested `<table>` markup (up to 4 levels deep,
sometimes 45+ tables in one message) meant for a fixed-width email client,
not a responsive one. Verified concretely rather than guessing: rendered
all 74 ingested Gmail articles' `content_html` in a sandboxed 343px-wide
div with and without a candidate fix — confirmed 0/74 overflowed with
`.reader-body table { display: block; max-width: 100%; overflow-x: auto }`,
and 19/74 (all BiggerPockets) overflowed to 522–568px without it.

**Bug: excessive blank space in newsletter articles.** *"For email
sources, automatically reduce excessive newlines"* → found empty spacer
`<p>` tags (email builders' `<p>&nbsp;</p>` vertical-padding trick, meant
to be handled by CSS margins that get stripped along with every other
inline style) and runs of consecutive `<br>` — added
`tighten_newsletter_whitespace()` (empty-`<p>` removal + `<br>` run
collapse), scoped to `origin == "gmail"` only since RSS content doesn't
share this markup pattern. Backfilled the 74 already-ingested articles
(27 changed). Follow-up: *"I still see excessive spaces between text
content"* — a second, distinct cause: newsletters pad paragraphs with
dozens of consecutive `&nbsp;` to control inbox preview-snippet length,
normally invisible because it sits inside a hidden (`display:none`)
element — but since every inline style is stripped for sanitization, the
padding surfaced as a literal wall of spaces mid-paragraph. Extended the
same function to collapse 3+ consecutive `&nbsp;` to a single space;
backfilled the 2 affected articles.

**Bug: Gmail refresh re-listed the same messages every time.** *"Make
sure it's as scoped as possible — only fetch for time range that hasn't
fetched before... record the timestamp and future refresh would go from
there."* `refresh_gmail_source` was already recording `last_fetched_at`
but never reading it back — every refresh reused the source's full
configured query (`newer_than:30d`) rather than narrowing to what's new.
Fixed by appending `after:<epoch of last_fetched_at, minus a 5-minute
overlap>` to the query on every refresh after the first. Verified live:
first refresh of `wsj-whats-news` listed 25 messages; the very next
refresh, scoped, listed 0.

**Bug: article list silently capped at 50 with no way to see more.**
*"All items shows '50 items' when unread is 90. Did we max it at 50?"*
Yes — `GET /api/articles` always had a `limit=50` default, but the
frontend never paginated past it (no `offset` ever sent, no load-more
affordance), so any view exceeding 50 items just... stopped. This had
been latent since the RSS-only version rarely crossed 50 unread; adding
four active newsletters pushed a real user over that line for the first
time. Fixed with `useInfiniteQuery` + a "Load more" button in the list
(the natural place to add the trigger, since `.article-list` is the
actual scroll container, not `.main`), reusing the existing `offset` param
`store.list_articles` already supported server-side but the client never
used.

148 backend tests passing at this point (+1 for Gmail query scoping).
Frontend changes verified via `tsc --noEmit` plus live Chrome sessions —
including sandboxed-iframe overflow testing across all 74 ingested Gmail
articles at real mobile width, both with and without each candidate fix,
rather than trusting a single visual spot-check.

## 2026-08-12 — Four more live-found bugs: stale build, two table-CSS
## regressions, and cross-browser reload state loss

**Bug: `:8787`/LAN was silently serving a stale frontend build.**
*"biggerpockets's '23,000 Jobs Are Gone' email still scrolls left to
right"* — re-tested the exact table-overflow fix from the previous entry
against that specific article and found **zero** overflow, contradicting
the report. Root cause wasn't the fix — it was `frontend/dist`'s mtime
predating `index.css`'s last edit: `npm run build` had been run once,
then more CSS changes landed after it, and the backend (`:8787`, what the
LAN/phone URL hits) has no way to know its static build is stale — it
just keeps serving whatever was last built, forever, with no error.
Rebuilt, verified the fix byte in the served CSS matched. **Follow-up
fix**: added `scripts/serve.sh` (build, then start the backend) and
pointed the README's production-style-run section at it, so this specific
trap — "I edited frontend/src, restarted nothing, and `:8787` looks
unchanged" — doesn't recur silently.

**Bug: the table-overflow fix itself caused giant blank gaps.**
*"Email content nested tables... still large gap for email"* (BiggerPockets
"23,000 Jobs" between "0.6% on the day" and "FOOD FIGHT"). Root cause was
the earlier `.reader-body table { display: block; margin: 0 0 22px }` fix:
email templates nest tables 4-5 levels deep purely for layout padding
(a real, common pattern, not malformed markup), and `display: block` turns
each nested level into its own block box — so every level independently
added the 22px margin, compounding into 100px+ of blank space with nothing
structurally wrong to point to. Two-part fix: (1) `.reader-body table
table { margin: 0 }` so only the outermost table in a nest gets the
visual-break margin; (2) extended `tighten_newsletter_whitespace()` to
recursively prune genuinely empty `<table>` chains (no text, no `<img>`,
bottom-up to a fixed point) the same way it already pruned empty `<p>`
spacers, since those are pure layout artifacts contributing nothing.
Backfilled all 74 Gmail articles (46 changed). Verified by measuring the
actual pixel gap between the two text nodes in a sandboxed reader-body div
before/after: ~380px → ~36px (normal paragraph spacing), then confirmed
visually via screenshot.

**Bug: a photo credit rendered as a giant heading.** *"Why is the 'Marcin
Golba/Getty Images' part that big? It looks small on email client"* — the
Snacks template wraps that caption in a real `<h1>` tag, purely to reuse
its predefined small-caption CSS class, with no semantic weight intended.
Sanitization strips the class/inline-style that made it small in the
original template, so it fell back to the browser's unset UA default for
`h1` (~2em) — a caption showing at ~38px. Underlying bug: `.reader-body
h1/h2/h3` never had an explicit `font-size`, relying entirely on that
default; usually invisible because most content doesn't misuse heading
tags this way. Fixed with explicit, modest sizes (1.5em/1.3em/1.15em) —
applied to all origins, not just gmail, since RSS/blog headings inheriting
the same oversized default was the identical latent bug, just less
visible there.

**Bug: reader state (open article, current view) lost on any page
reload**, including the one iOS forces on a tab that's sat idle/backgrounded
for a while — the app has no hook into that, so it can't be caught or
prevented, only survived. *"The app keeps refreshing in the browser if
unused for a while. Post refresh, I would lose the article I was on."*
First fix: mirror `selection`/`openArticleId` into the URL via
`history.replaceState`, restore from `URLSearchParams` on mount. Verified
live (open article → URL updates → hard-reload that exact URL → article
reopens) and looked solid. **Then**: *"Back to previous issue — it
refreshed and still back to feed view."* Asked for the exact address-bar
contents post-reload: `?sel=view%3Aunread` — the *first* replaceState
call from mount, missing the `article=` param that should have been
written once an article was opened. Diagnosis: the user was on **Chrome**,
not Safari — and Chrome on iOS runs on WKWebView (Apple requires every
third-party iOS browser to), which doesn't reliably sync
`history.replaceState` calls back to its own native tab-restoration
bookkeeping. When the OS reclaims a backgrounded tab's memory, the browser
can reload it from an older URL snapshot than the page's actual last
state — a known class of gap for WKWebView-based (i.e. every non-Safari)
iOS browser, not something fixable by calling replaceState more
carefully. Fixed by adding `sessionStorage` as a second, fully independent
persistence layer — a plain per-tab key/value write with no dependency on
the navigation stack at all. Verified by reproducing the exact reported
shape: loaded a URL deliberately missing the `article` param (mirroring
what was observed) and confirmed the article still restored correctly
from `sessionStorage`, which also re-synced the URL afterward.

No backend changes in this entry — all four fixes are `frontend/src`
(CSS + `App.tsx`) plus one backend HTML-cleanup extension
(`textutil.tighten_newsletter_whitespace`) and one operational script
(`scripts/serve.sh`). 148 backend tests still passing throughout (the
table/heading/state fixes don't touch backend logic covered by tests,
apart from the whitespace-pruning extension, which reused existing
coverage). Every fix in this entry was caught by testing the *specific*
article/browser the user reported, not a generic pass — the stale-build
bug in particular would have been invisible without re-testing the exact
one that was reported broken instead of trusting the previous general
fix.

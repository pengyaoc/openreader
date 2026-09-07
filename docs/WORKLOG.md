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

## 2026-08-12 — Co-hosting on the WordPress VM: hardening, IMAP connector, live deploy

Request: *"Evaluate how my app can co-live on my e2-micro vm with my
wordpress site."* Not a feature request — a deployment feasibility
question that turned into real app changes once the answer was "yes, but
not safely as either side currently stands."

**VM audit first.** `wordpress-1-vm` (e2-micro, 1 GB RAM) had 985 MB
total, 246 MB available, and swap 70% consumed — before OpenReader added
anything. Root cause wasn't WordPress: `google_osconfig_agent` was
holding 316 MB `RssAnon`, accumulated over 1389 days of uptime with no
reboot. Apache's `MaxRequestWorkers 150` (~7.5 MB each) was also sized for
a box with far more headroom than this one has.

**Exposure review, before writing any deploy config.** Asked *"How can I
keep the site safe though? It can pull my gmail. We need some auth
gating."* Two real problems surfaced from reading the actual code, not
assumption:
- `PUT /api/config` (`config_api.py`) is an unauthenticated arbitrary
  write to `feeds.yaml`. Chained with a Gmail source's OAuth token
  (`gmail.readonly`, whole-mailbox), three unauthenticated requests
  (write a source with an empty query → refresh → read articles) would
  expose the entire inbox to anyone who found the URL.
- `/api/img` validated only the URL *scheme*, with
  `follow_redirects=True` — an open SSRF forwarder into the box's own
  localhost or a cloud metadata endpoint.

Explored WordPress-login reuse for the `/reader/` path first (`mod_authn_dbd`
can't verify WordPress's phpass hashes, PHP-FPM's `mod_authnz_fcgi` only
implements the FastCGI *Responder* role, not *Authorizer* — genuinely not
available on stock Apache 2.4 without adding an OIDC plugin to an
otherwise-stock WP install). Then the actual pivot, from the user: *"we
can use a new clean email address to sign up for newsletters without any
private information... the site doesn't have to be deeply secured."*
Fixes the problem structurally (no whole-mailbox credential on an
internet-facing box) rather than by layering auth in front of one.

**That surfaced a second, unrelated blocker:** Google expires OAuth
refresh tokens after 7 days for any app in "Testing" publishing status —
true for personal OAuth clients indefinitely, since escaping it for a
restricted scope (`gmail.readonly`) needs full verification + a paid CASA
audit. Not viable, and re-running `scripts/gmail_auth.py` by hand weekly
defeats the point of an unattended deployment. Landed on IMAP + a Google
app password against the dedicated mailbox instead — no expiry, no Cloud
project, no `token.json`.

**Built, test-first:**
- `connectors/imap.py` — `IMAP4_SSL`, `parse_message`/`parse_query` pure
  functions mirroring `gmail.py`'s contract exactly (same
  `NormalizedEntry` shape, same multipart-walk logic, same
  `from:`/`subject:`/`newer_than:Nd` query tokens) so an existing
  `type: gmail` source becomes `type: imap` by changing one field.
  `refresh_imap_source` in `refresh.py`, dispatched from `refresh_all`.
  IMAP debug stays at 0 always — `imaplib`'s debug output echoes `LOGIN`,
  password included.
- `_check_no_credentials` in `config.py` — found while implementing the
  "credentials never in feeds.yaml" rule: msgspec's `Config.convert`
  silently *drops* unknown fields rather than rejecting them (verified,
  not assumed), so a `password:` key smuggled into a source would
  previously have parsed clean and been re-served by the next unauthenticated
  `GET /api/config`. Now `parse_config` rejects any credential-shaped key
  before struct conversion could hide it.
- SSRF guard on `/api/img` (`images.py`) — resolves the hostname, rejects
  loopback/private/link-local/multicast/reserved addresses, and — since a
  redirect from an otherwise-public host is exactly how a same-request
  check gets bypassed — replaces `follow_redirects=True` with a manual,
  re-validated hop loop (max 3).
- `READER_READONLY_CONFIG` — `PUT /api/config` 403s when set. Not just the
  write itself: the endpoint also assigns `app.state.config`, so it could
  otherwise flip `llm.enabled` back on at runtime regardless of what's on
  disk.
- Path-prefix support for serving under `/reader/` instead of a
  subdomain: `vite.config.ts`'s `base`, an `API_BASE` constant in
  `api.ts` read from `import.meta.env.BASE_URL`, and a `withApiBase`
  rewrite in `ArticleReader.tsx` for the `/api/img?...` URLs already
  baked into stored `content_html` at ingest time. The backend stays
  completely unaware of the prefix — Apache strips it before uvicorn ever
  sees the request.

179 backend tests passing throughout (147 pre-existing + 32 new: IMAP
parsing/refresh, the credential guard, the SSRF guard, the readonly-config
lock). Verified locally both as root (`scripts/serve.sh`-style) and built
with `VITE_BASE=/reader/`, including grepping the minified bundle to
confirm `API_BASE` actually resolved to `/reader/` rather than trusting
the build not to silently fall back to `/`.

**Deploy mechanics, decided explicitly rather than defaulted into.**
First instinct was "VM pulls from GitHub and runs a cmd" — reasonable
until walking through it: `frontend/dist/` is gitignored on purpose (Vite/
tsc spike memory, and there's no Node on the VM), so a bare `git pull`
would never update the frontend, and installing Node to build there
reopens the exact memory risk the plan was written to avoid. Landed on
push instead: `scripts/deploy.sh` runs entirely on the laptop (typecheck,
`pytest`, build), then ships finished artifacts up via `gcloud compute
scp` — no git, no Node, no build tooling on the VM at all, and a broken
build never reaches it. Real cost, noted rather than ignored: no
`git log`-style audit trail of what's live on the box, since rsync/cp just
overwrite files. Mitigated the cheap way (a `VERSION` stamp), not by
adopting pull.

Hit the environment mid-implementation: the VM's `rsync` isn't installed,
and `apt` has been broken since Debian 10/buster was archived at EOL
(confirmed: `E: The repository ... no longer has a Release file`). Fixing
apt was explicitly out of scope from the earlier VM-hygiene decision
("fix memory + Apache only"), so `deploy.sh`'s remote install step uses
`cp`/`rm` instead — already on the box, no package install needed either
way.

**Live provisioning**, snapshotted first (`wordpress-1-vm-pre-openreader-
20260812-1011`) since this VM hadn't rebooted in 1389 days:
- Disabled `google-osconfig-agent` (reclaimed the 316 MB), capped Apache
  to `MaxRequestWorkers 16`, grew swap 512 MB → 2 GB, rebooted — first
  reboot on this box in four years, came back clean, WordPress verified
  200 the whole way through each step.
- `openreader` system user, `uv` installed for that user, a systemd
  `--user` unit (`loginctl enable-linger openreader` so it survives
  logout), Apache `proxy_http` + a `ProxyPass /reader/ →
  127.0.0.1:8787/` block added to the existing `wordpress-https.conf`.
- First `scripts/deploy.sh` run: `uv sync` resolved 25 packages entirely
  from prebuilt wheels (no `nh3`/`selectolax` source compile needed — the
  preflight concern from planning didn't materialize). Initial 503 was
  Apache reaching the backend before `uv sync` had finished on a cold
  start, not a real failure — resolved on retry.
- End-to-end verification against the *live* deployment, not just tests:
  triggered a real refresh (60 articles landed from 9 working RSS sources;
  `uber-eng` 406s — Uber's own blog blocking bare requests, pre-existing
  and unrelated), confirmed conditional-GET/dedup idempotency by
  accidentally triggering the same refresh twice (a parsing bug in my own
  verification script masked that the first POST had already succeeded —
  the second call correctly reported `not_modified`/`new: 0` for
  everything the first had just fetched, which is exactly the intended
  behavior, live, against real feed servers, not just fixtures), and
  confirmed the SSRF guard, the readonly-config 403, and the localhost
  bind (port 8787 unreachable from the VM's external IP) all held on the
  real deployment.

**IMAP mailbox, live.** User created a dedicated Gmail mailbox, enabled
2SV, generated an app password, and — after confirming they understood
the tradeoff of pasting it into chat rather than typing it directly on
the VM — handed it over to write directly. Written to
`/opt/openreader/openreader.env` (`chmod 600`, owned by the `openreader`
user — see ERD §7.1 for why a user-manager unit means user-owned, not
root-owned). Four `type: imap` sources added to the VM's `feeds.yaml`
mirroring the real config's Gmail queries. All four authenticate
successfully (`status: ok`) against real Gmail IMAP over TLS; `fetched: 0`
across the board since the newsletters aren't subscribed to the new
address yet — not an error, just no mail there yet.

**Newsletter resubscription — partially done, stopped deliberately.**
Attempted via `claude-in-chrome` browser automation. Robinhood Snacks has
been folded into "Sherwood News" (still Robinhood-owned) — a bundled
three-newsletter signup page with Snacks/Entrypoint/Scoreboard all
pre-checked by default; unchecked the two not wanted, but flagged before
finishing that the sender may now be a `sherwood.news` address rather
than the `hello@snacks.robinhood.com` the existing filter matches on,
which would need a query update regardless of how signup goes. Hit a
real, repeated tool failure next (`Cannot access a chrome-extension://
URL of different extension` — on screenshots, then on a basic JS
`document.querySelector` read, on two unrelated tabs — consistent with
another Chrome extension's popup stealing focus after a field
interaction, not anything page-specific). Stopped rather than push
through blind per the browser-automation guidance (retry once, then stop
and report) — WSJ What's News and Sherwood/Snacks were left with email
filled in but not submitted; The Batch and BiggerPockets weren't started.
Left both tabs in a known, reported state for the user to finish by hand
rather than guessing at unverifiable form state.

No backend logic changed by the browser-automation portion — this
paragraph is process, not code. Everything through "IMAP mailbox, live"
above is real, verified-live app and infrastructure change; the docs/
`.gitignore` update in this same pass added `.env`/`*.env` preemptively
(no such file exists in the repo — the real credential only ever touched
the VM directly — but the new `READER_IMAP_*` env vars make it likely
someone creates a local one for testing later).

## 2026-08-13 — Bug: article scroll position persisted across prev/next

Feedback: *"when go left and right on article view, the article will
persist previous page's scroll state. It shouldn't. On moving left and
right, article should start from the beginning."*

`ArticleReader` stays mounted while paging through articles — `App.tsx`
swaps only the `article` prop, `onPrev`/`onNext` never unmount the
component — so `.reader-scroll`'s `scrollTop` carried over unchanged from
whatever the previous article was left at. Fixed with a `ref` on the
scroll container and a `useEffect` keyed on `article.id` that resets
`scrollTop` to 0 on every article change, in both directions.

Verified live in a real browser against real local data (not just
typecheck): opened an article, scrolled deep into it, clicked next — new
article opened at the top; clicked prev back — also opened at the top.
Deployed to the VM (`scripts/deploy.sh`); same cold-start 503 as the first
deploy (Apache reaching the backend before `uv sync` + restart finished),
settled within a few seconds as before — now a recognized, not alarming,
shape for this box rather than something to re-diagnose each time.

## 2026-08-13 — Mark as Read, event-loop blocking fix, basic auth, and a live config-corruption incident

Four threads in one session, landed roughly in this order:

**Mark as Read.** Per-article checkmark in `ArticleList` (reveal-on-hover
via `@media (hover: hover)`/`(hover: none)`, so touch devices — which have
no hover state to reveal it with — get it always-visible instead) plus a
new `POST /api/sources/{id}/mark-all-read` bulk action in the sidebar.
Reused the existing `toggleReadMutation` for the per-row button rather
than building a new one-directional endpoint — already fully wired,
tested, and reversible on a second click.

**Event-loop blocking (found from a live report: *"read for long articles
take a few seconds to load"*).** Two synchronous calls were running
directly on uvicorn's single event loop:
- `images.py`'s SSRF guard did a blocking `socket.getaddrinfo()` inline in
  the async `proxy_image` handler — once per image. Verified against a
  real 35-image article from the local DB: concurrent fetches dropped
  from a 28.4s serialized sum to 2.3s wall-clock once moved to
  `asyncio.to_thread`.
- `articles.py`'s `hydrate_article()` call did a synchronous `httpx.get`
  (up to a 5s timeout) inline in the async `get_article` handler on first
  open of any `fetch_full_text` source's article — blocking every other
  in-flight request for that duration. Also moved to `asyncio.to_thread`,
  with its own SQLite connection (the request's connection can't cross
  threads — `sqlite3` defaults to `check_same_thread=True`).

**Blank byline (found from a screenshot: a "早报" roundup article showing
just a date, no name, before it).** `get_article()` never joined `sources`
the way `list_articles()` does, so `source_title` was always `None` on the
single-article detail endpoint the reader actually uses — the frontend's
new author-or-source-name fallback had nothing to fall back to. Fixed the
JOIN in `store.py` alongside the frontend fallback.

**HTTP basic auth on `/reader/`, and dropping `READER_READONLY_CONFIG`.**
Request: *"config/feeds.yaml is not editable on remote deployment... can
we make it writable."* The flag existed specifically because
`PUT /api/config` was unauthenticated and internet-reachable; making it
writable again without addressing that would have reopened the exact
thing it closed. Chose Apache basic auth over the alternatives discussed
back when the VM was first provisioned (WP-login reuse isn't cleanly
available on stock Apache — see the earlier 2026-08-12 entry) now that
there's a real reason to want the config UI to work remotely. Bcrypt hash
generated locally (`htpasswd -nbB`) so the plaintext password never
touches the VM's disk or shell history — only `/etc/apache2/.htpasswd-
reader` (root:www-data, 640) does. `auth_basic`/`authn_file` were already
enabled. Verified: no credentials → 401, wrong password → 401, correct →
200, WordPress unaffected throughout.

**Incident: a verification script corrupted the live config.** While
confirming `PUT /api/config` worked post-auth, a round-trip test wrapped
the *entire GET response* (`{"yaml": "..."}`) as the value of a second
`{"yaml": ...}` object instead of extracting just the inner field —
double-JSON-encoded the file's contents and pushed it back. `parse_config`
didn't reject it: the garbled text was technically valid YAML (a flow
mapping with one key, `"yaml"`, whose double-quoted value YAML itself
unescaped back into real newlines), so it parsed to `{"yaml": "<original
text>"}\` — a dict with no field `Config` recognizes, which msgspec's
unknown-field-drop behavior (the same behavior `_check_no_credentials`
exists to guard against, from the 2026-08-12 entry) silently accepted as
an all-defaults, zero-source `Config`. Caught immediately by validating
the exact same PUT body through `parse_config` locally before touching
the live server again — 14 sources, correct `llm.enabled` — then pushing
that verified-correct body and restarting the service to force a clean
reload. Confirmed recovery with a scoped `POST /api/refresh?source=xkcd`
succeeding (proving `app.state.config.sources` was genuinely restored, not
just the file on disk — `GET /api/sources` alone wouldn't have proven
that, since it reads the DB's `sources` table, populated by past refreshes
independent of the current in-memory config). No data was lost — this
corrupted the source *list*, not `data/reader.db`.

Password rotated once more after this, purely at the user's request (not
related to the incident, which never exposed the credential itself) — same
local-hash-generation process, old password confirmed rejected and new
one confirmed accepted immediately after.

**Duplicate articles (found from a live report, same session, right after
the incident above): *"I am seeing duplicate feed items for Simon
Willison feed items, but not others."*** `_persist_entry` deduped only on
`(source_id, guid)`, despite a `content_hash` column (canonical URL +
normalized title) already being computed and stored on every insert, with
its own index — never queried. `simonwillison.net`'s Atom feed started
appending a `#atom-everything` fragment to entry `<id>` values it
previously served bare; `canonicalize_url()` already strips fragments, so
`content_hash` stayed stable across that drift while `guid` didn't,
minting a "new" guid for every already-ingested post on the next refresh.
30 articles duplicated in production as a result. Fixed by checking
`content_hash` alongside `guid` in the dedup query — the column and index
existed for exactly this, just never wired up. Regression test reproduces
the exact scenario (same Atom entry fetched twice with a fragment added to
its `<id>` between fetches; asserts one row, not two). Cleaned up the 28
existing duplicate groups on the live VM directly (source list only,
`data/reader.db`) — kept whichever row of each pair was already read or
starred, preserving reading state, tie-broken by lowest id; verified zero
duplicate groups remained afterward.

185 backend tests passing (182 → 183 → 184 → 185 across this entry's four
fixes). All four deployed via `scripts/deploy.sh` and verified live
against the real VM, not just locally.

## 2026-08-13 (cont.) — Removed sources still showing in the sidebar, and the read-state reconciliation that followed

Feedback: *"I just removed Uber engineering from my remote server's yaml
file... Uber engineering still show in the left side bar. We shouldn't
display the section if it's gone from yaml."*

Root cause: `store.list_sources()` reads only the `sources` DB table,
which is create-only — `get_or_create_source` writes a row the first time
a source is ever refreshed, and nothing ever removes it. Deleting a
source from `feeds.yaml` was silently a no-op as far as the sidebar was
concerned. Fixed by having `list_sources()` accept an optional
`valid_keys` set and filtering to it; the API handler passes the live
config's source keys.

**What happens to a removed source's already-fetched articles was a real
design question, not obvious from the bug report alone** — asked, offered
three shapes (hide sidebar only / hide everywhere but keep the DB rows /
delete outright). User's answer was actually a fourth, more precise one:
*"any feed items that don't meet the criteria should be marked as read
and hidden from UI. They can still stay in the DB."* First pass added a
new `hidden` column (plus the app's first schema migration — `ALTER
TABLE ... ADD COLUMN`, since `CREATE TABLE IF NOT EXISTS` is a no-op
against tables that already have real data on both the local DB and the
live VM) to distinguish "hidden because it no longer matches" from
"read because the user actually read it." Pushback: *"Why do you need
migration? You can just mark them as READ, right? That's existing
feature."* Correct that `is_read` alone is cheaper and already exists —
the tradeoff is that it wouldn't hide these articles from All items or
Starred (only from Unread), and conflates "read because filtered out"
with "read because you opened it," so a rule loosened back later has no
way to tell which read articles might be worth surfacing again. User
confirmed that gap was fine. Reverted the `hidden` column and migration
entirely, kept the `valid_keys` sidebar filter, and reimplemented
reconciliation as a plain `is_read=1` UPDATE — no schema change needed
after all.

`reconcile_read_state()` (`refresh.py`) runs on every `PUT /api/config`:
for each DB source row, if its key isn't in the new config, mark all its
unread articles read; if it's still in config, re-run `evaluate_rules`
against every unread article's stored fields against the *current* rules
and mark the ones that no longer pass. Only touches `is_read=0` rows, so
already-read articles (by either path) are never re-touched — `read_at`
for a genuinely-user-read article is preserved exactly.

Also removed the per-article-row "Mark as Read" checkmark added earlier
this session — feedback: *"the mark as read at the feed item level
interrupts UI too much. especially on mobile."* Discussed lower-profile
alternatives (bare-glyph styling, swipe-to-reveal) before the actual ask
landed: drop the per-row action entirely, keep the per-source bulk action
(sidebar, next to each source's unread count), and add the same bulk
pattern to the "Unread" saved view itself — one click marks every unread
article across every source read, not just one source's. New
`POST /api/articles/mark-all-read` (registered ahead of
`/api/articles/{article_id}` in the route table so the literal path
wins the match — Starlette matches routes in registration order) backs
it; `store.mark_all_read_global()` is the un-scoped counterpart to the
existing per-source `mark_all_read()`. Frontend: `Sidebar`'s "Unread"
`NavRow` gets the same `.source-row`/`.source-row__mark-read` wrapper
already built for per-source rows (no new CSS needed), shown only when
`totalUnread > 0`.

Verified live end-to-end against real local data (not just typecheck):
confirmed a genuinely orphaned `uber-eng` source row (removed from
`config/feeds.yaml` earlier when pulling the VM's edited config down to
align local) disappeared from the sidebar immediately on server restart;
clicked the new "Unread" checkmark and confirmed every source's count and
the total zeroed together, with buttons correctly disappearing once
nothing was left to mark.

192 backend tests passing (185 → 192: reconcile_read_state's four
scenarios — source removed, rules tightened, already-read articles
untouched, idempotent — the global mark-all-read endpoint including a
route-collision regression test, and the end-to-end config-removal
flow through the real API).

## 2026-08-13 (cont.) — Favicon didn't match the app's own visual identity

Feedback: *"what icon did we use for OpenReader? It's not consistent
between icon in chrome tab vs icon on the web page."* Investigated rather
than assumed: the tab favicon (`frontend/public/favicon.svg`) was a
complex, colorful abstract illustration (purple/blue blob shapes) with no
relationship to the app's actual palette — looked like a leftover
scaffolding placeholder, never designed to match anything. The in-page
mark next to "OpenReader" in the sidebar was a separate, unrelated 9px
CSS-drawn amber diamond (`.sidebar__brand-mark`, a rotated `background:
var(--amber)` square) — that one, at least, used the app's real color.

Asked what direction to take it; user asked for *"a minimalistic version
of the google reader icon."* The classic broadcast-wave "feed icon" glyph
(dot + concentric quarter-arcs) is an open, industry-standard symbol —
released by the RSS Advisory Board specifically for free reuse in 2005,
not Google-proprietary artwork; Feedly/Inoreader/NetNewsWire all use their
own variants of the same silhouette. Built a fresh minimal version from
scratch (own arc geometry, not traced from any existing asset) in the
app's actual amber (`#e08a3e`) and cream (`#faf6ee`, the light theme's
`--bg`) rather than Google's original orange, so it's consistent with
*this* app's palette specifically.

First pass only replaced `favicon.svg` — verified in a real browser tab,
looked right there. But the report was about a *mismatch*, and swapping
one side of a mismatch without touching the other doesn't fix it: *"on
the page, it still looks like this"* (screenshot of the old diamond,
unchanged). Replaced `.sidebar__brand-mark` too — an inline SVG using the
same glyph, but with `fill="var(--amber)"` / `stroke="var(--bg)"` instead
of the favicon file's fixed hex colors, so unlike the static favicon (SVG
files referenced via `<link rel="icon">` can't reactively read the host
page's CSS custom properties) the in-page mark actually re-colors itself
correctly across the dark/light theme toggle. Verified live in both
themes rather than assuming the CSS variables would resolve as expected.

No backend changes, no new tests (a decorative asset, not logic) — both
changes deployed via `scripts/deploy.sh` and confirmed live by grepping
the deployed JS bundle for the new class name, not just trusting the
deploy script's own success output.

## 2026-08-13 (cont.) — Refresh took ~9s and froze the whole app while it ran

Feedback: *"Refresh takes ~9s. Any inefficiency there? How we can scope
the fetch as much as possible?"*

Measured before touching anything (9 real RSS sources, sequential,
against the actual configured feeds): 7.74s total, no single dominant
outlier — `xilei`/dapenti.com was slowest at 2.6s, but the rest still
summed to ~5.2s. Ordinary per-host TLS+network latency, paid out one
source at a time. IMAP (4 more sources, each its own SEARCH round-trip)
accounted for the rest of the reported ~9s.

Separately, and worse: `POST /api/refresh` called `refresh_all()`
directly — synchronously — from an `async def` handler, with no
`asyncio.to_thread`. Claimed live that this "doesn't freeze the app even
today"; rather than argue from theory, tested it directly — fired a
refresh, then a concurrent `GET /api/sources` 0.3s later. That request
sat blocked for **8.36s**, only returning once the refresh finished. With
one uvicorn worker, a synchronous blocking call inside an async handler
blocks the entire event loop for its duration — not just that request,
every request, for the whole process. (Likely why it didn't *feel*
broken day to day: articles already loaded render from the frontend's
local cache with no round-trip needed, so browsing what's already on
screen keeps working; only a *new* server request would visibly hang.)

Two fixes, agreed to do together after the concurrent-block was proven
live rather than assumed:

- **`_refresh_rss_batch()`** (`refresh.py`): RSS sources are fetched
  concurrently now, via a bounded `ThreadPoolExecutor` (6 workers) —
  network I/O only, no DB access in the pool. `refresh_source` was split
  into `_rss_fetch_only` (safe off-thread) and `_persist_rss_result`
  (DB writes, always back on the calling thread — `sqlite3` connections
  default to `check_same_thread=True`, so persistence can't cross into a
  worker thread the way the fetch can). `get_or_create_source` for every
  source runs upfront, sequentially, before the pool starts (cheap local
  reads, and each source's etag/last_modified has to be known before its
  fetch can even be sent). Report order is preserved by keying results on
  source key and reassembling in the caller's original order — batching
  by type internally shouldn't reorder output relative to a config where
  rss/gmail/imap sources are interleaved. Gmail/IMAP stay sequential:
  both page through one shared, stateful connection/token per refresh
  (one IMAP connection, one Gmail access token) rather than independent
  per-source connections, so there's nothing to parallelize there without
  provisioning per-source connections instead — judged not worth it given
  RSS was the dominant cost (7.7 of ~9s) and IMAP/Gmail servers can rate-
  limit concurrent logins from one account.
- **`_refresh_off_thread()`** (`refresh_api.py`): the whole refresh call
  now runs via `asyncio.to_thread`, with its own SQLite connection (same
  `_hydrate_off_thread` pattern as the earlier event-loop-blocking fixes
  this session) — the request handler's own connection can't be reused
  from a different thread.

Verified live, not just re-run through the test suite: fired the same
concurrent-request-during-refresh test again — the second request now
returns in **0.004s** instead of 8.36s. Total refresh wall time dropped
from 8.68s to **3.80s** (RSS batch now bounded by its slowest single
source instead of their sum; local config has no IMAP configured, so
this run's remaining time is close to pure RSS-batch cost).

194 backend tests passing (192 → 194: a timing-based test proving 5
sources with an artificial per-fetch delay finish in roughly one delay's
worth rather than five — the actual concurrency claim, not just "the
code still returns the right data" — and a source-order-preservation test
across interleaved rss/imap/gmail types).

## 2026-08-13 (cont.) — Extended concurrency to IMAP after live data showed it mattered more than expected

Deploying the RSS-concurrency fix revealed the real cost split on the VM
wasn't what local measurements alone had suggested: total refresh stayed
at 9.76s post-fix, because the VM's 4 real IMAP sources (not configured
locally, so absent from every earlier measurement) turned out to cost
more than the smaller share initially estimated — RSS dropped correctly
to ~4.4s (bounded by `xilei`, slower from the VM's network path than from
a laptop — 4.36s vs. 2.6s measured locally for the same source), but ~5.3s
of sequential IMAP was left untouched by design. Asked whether to extend
concurrency there too; answered *"extend concurrency"*.

Unlike RSS's independent HTTP requests, IMAP is a stateful protocol — one
connection can't run concurrent commands from multiple threads, so the
previous design (`refresh_api.py` opens one shared client, hands it to
every IMAP source in turn) couldn't parallelize without restructuring
connection ownership itself. Each source now gets its own connection:

- `refresh.py`: `_imap_fetch_only()` (network-bound: opens its own
  connection via an injected `connect_fn`, SEARCH+FETCH+parse every
  message, always logs out — no DB access, safe on a worker thread) and
  `_persist_imap_result()` (DB writes, always on the calling thread — same
  `check_same_thread=True` constraint as the RSS split). `_refresh_imap_batch()`
  orchestrates: `get_or_create_source`/scoping computed upfront sequentially
  (cheap, and each source's `since` window has to be known before its
  fetch can be sent), fetches run through a bounded pool (`_IMAP_FETCH_CONCURRENCY
  = 6` — Gmail allows up to 15 concurrent connections per account, so this
  is headroom, not a real ceiling for the handful of sources that
  realistically exist), persistence sequential after.
- `refresh_all()`'s `imap_client` parameter (a single pre-connected
  object) became `imap_connect` (a zero-arg factory) — RSS and IMAP are
  now each batched, results reassembled by source key into the caller's
  original order, same mechanism as the RSS-only version from earlier
  today.
- `refresh_imap_source` (the single-source, pre-connected-client
  function) is untouched — still the direct entry point its existing
  tests use; only `refresh_all`'s dispatch changed to route IMAP sources
  through the new batch instead of calling it in a loop.
- `refresh_api.py`: no longer eagerly opens one IMAP connection and holds
  it for the request's duration; builds an `imap_connect` closure instead
  and lets each source's own connection attempt report its own real
  error on failure — an accuracy improvement over the old behavior, which
  collapsed any connect failure into a blanket "not configured" message
  regardless of the actual cause.

196 backend tests passing (194 → 196: a timing proof for the IMAP batch —
same shape as the RSS one, 5 sources with an artificial per-connect delay
finishing in roughly one delay's worth — and an end-to-end correctness
test through `_refresh_imap_batch` directly, confirming distinct
connections, per-source search results, and message counts all land
correctly). One bug caught in my own test before it shipped: a fake
`connect_fn` shared across the thread pool read-then-appended to a list
to identify which source was connecting — not atomic, a real race between
threads (not a hypothetical one) that a lock fixed.

## 2026-08-13 (cont.) — Comprehensive source management: add/edit/delete for RSS and newsletters

Feedback: *"'Add source' feature doesn't seem to work properly on remote.
Fix that. Also I want to create a comprehensive tooling to add/remove/edit
source for both RSS and newsletters without going through the raw config
yaml file."*

**Diagnosed the "doesn't work" report first**, empirically rather than
guessing: `POST /api/sources` against the live VM via raw `curl` returned
`201` immediately — the backend endpoint was never broken. The actual gap
was in the frontend: `NewSource`'s TypeScript type hardcoded
`type: 'rss'` literally, and the Add Source modal had no type selector at
all — there was no way to create a newsletter/IMAP source through the UI,
full stop. Trying to use "Add source" for a newsletter either did nothing
(URL field required, submit stayed disabled) or created a nonsensical RSS
source pointed at a non-feed address. This is exactly what the requested
comprehensive tooling needed to fix anyway, so no separate patch — the
rebuild resolves both asks at once.

**Backend, three new endpoints in `sources.py`:**
- `PUT /api/sources/:id` — edits an existing source's fields in place.
  `key`/`type` are locked to the existing entry's values regardless of
  what the request body sends, even if it tries to change them — changing
  either is really "delete this, add a different one," since dedup
  ((source_id, guid)) and the sidebar/history both key off a stable
  `key`. Re-runs `reconcile_read_state` afterward, same as a raw-YAML
  config save — tightening a rule via the edit form sweeps articles that
  no longer pass, exactly like editing feeds.yaml by hand always did.
- `DELETE /api/sources/:id` — removes a source from config only. Shares
  the exact same "hide from sidebar, keep the DB rows, mark unread
  articles read" behavior a raw YAML removal already had (from the
  earlier `list_sources`/`reconcile_read_state` work this session) —
  deleting via the new UI isn't a different, more destructive path than
  hand-editing the file was.
- `GET /api/sources/:id` — full detail (url/query/mailbox_folder/
  fetch_full_text/rules) merged from the live config by key. Needed
  because `GET /api/sources` is deliberately lean (sidebar-only fields) —
  the edit form needs the real feed URL or IMAP query to pre-fill from,
  which the list endpoint never carried.

**Found and fixed a real, pre-existing bug while building the edit
path:** `get_or_create_source()` only ever inserts a source's DB row once
— it has no update branch, so an existing row's `title`/`folder`/`url`
columns silently never change again, even when config does. This wasn't
new (refresh has always worked this way), but it meant an edit made
through the new modal would validate, write to `feeds.yaml`, and then
*not show up in the sidebar at all* until — never, since nothing else
updates that row either. `update_source` now writes those three columns
directly instead of waiting on a refresh path that was never going to do
it.

**Also closed a gap between the two config-write endpoints:**
`config_api.put_config` (raw YAML) has checked `READER_READONLY_CONFIG`
since it was introduced; `sources.add_source` never did, an inconsistency
nobody had reason to notice until there were three more write endpoints
sitting right next to it. All five source-mutating handlers now share one
`_readonly_response()` check.

**Frontend:** `AddSourceModal.tsx` → `SourceModal.tsx`, generalized to
add *and* edit, with a type toggle (RSS feed / Newsletter) shown only
when adding — editing shows the existing type as a locked label instead,
since it can't change. Newsletter sources get a structured query builder
(From address / Subject contains / first-refresh window in days) that
composes into the same `from:x subject:"y" newer_than:Nd` string
`type: gmail` sources have always used, rather than asking for raw query
syntax — `decomposeQuery()`/`composeQuery()` mirror
`connectors/imap.py`'s `parse_query()` regexes so an existing source's
query round-trips through the friendly fields correctly on edit, not just
on create. Delete is a two-step "click again to confirm" button in the
drawer footer rather than a native `confirm()` dialog, for visual
consistency with the rest of the app. Sidebar gets a small ✎ edit icon
next to every source row, alongside the existing mark-all-read checkmark.

Also fixed in passing: `Source.type` in `api.ts` was typed
`'rss' | 'gmail' | 'llm'` — `'llm'` isn't a source type at all (that's an
*article* origin), and `'imap'` was missing entirely. Would have made the
new type-aware edit-form logic silently wrong for every IMAP source.

**Verification was messier than usual.** Backend: 207 tests (12 new,
195 → 207), including exact-value round-trips through `get_source`/
`update_source` (not just status codes) and readonly-mode enforcement
across all five write endpoints. Frontend: clean typecheck and build.
Live browser verification hit the same transient extension conflict from
earlier in this session (screenshots/clicks/JS-eval all failing
intermittently, not page-specific, survived one fresh tab then recurred
on a second) — stopped rather than keep fighting it, per how this was
handled last time it came up, and asked how to proceed. Landed on
"good enough, ship it, verify manually" — followed immediately by a real
false alarm: *"can't see any config or feed items for localhost:5173"*,
which turned out to be that I'd stopped the dev servers as part of
cleanup a few messages earlier and only the frontend had been
restarted — the backend was simply not running. Confirmed and fixed via
`curl` directly (backend port, frontend port, and the frontend's `/api`
proxy target all individually), not by re-touching the flaky browser
tool.

## 2026-08-13 (cont.) — Fixed "refresh feed" hanging forever on the VM

Feedback: *"refreshing is stuck"* / *"spinning for multiple minutes"*.
Diagnosed live on `wordpress-1-vm` rather than guessing: the openreader
service was healthy and idle (0% CPU), but an `ESTABLISHED` TCP
connection to `imap.gmail.com:993` had been sitting open with no
completing request afterward — a refresh was blocked on a socket read
that was never going to return.

**Root cause, two layers:**
- `connectors/imap.py`'s `connect()` called `imaplib.IMAP4_SSL(host, port,
  ssl_context=context)` with no `timeout`. imaplib defaults to a fully
  blocking socket, so if the server stalls mid-command instead of closing
  cleanly, the calling thread waits forever — no exception, no recovery,
  the request (and the frontend's spinner tied to it) just hangs.
- Why Gmail was stalling in the first place: the four `type: imap`
  sources (`wsj-whats-news`, `robinhood-snacks`, `the-batch`,
  `biggerpockets` — a dedicated app-password mailbox, distinct from the
  OAuth `type: gmail` sources) had each been opening their own IMAP
  connection since the earlier same-day concurrency change
  (`baf53c0`). Every refresh meant up to 4 fresh TLS+LOGIN sessions
  against `imap.gmail.com`, from one GCP datacenter IP, in quick
  succession — roughly 60 raw IMAP logins over the session's ~15
  refreshes. Gmail's abuse heuristics answer that pattern by silently
  stalling the connection (accept the handshake, never respond to LOGIN)
  rather than erroring — a defense on their end, not a client bug that
  could be fixed by retrying harder.

**Fix, both parts:**
- `connectors/imap.py`: `connect()` now passes `timeout=30` (new
  `DEFAULT_TIMEOUT_SECONDS`) into `IMAP4_SSL`. Since the timeout applies
  to the socket for the connection's whole lifetime, it bounds
  LOGIN/SEARCH/FETCH too, not just the initial connect — a stall now
  fails in ~30s instead of hanging indefinitely, surfacing as a normal
  per-source error.
- `ingest/refresh.py`: replaced `_refresh_imap_batch` (concurrent,
  one connection per source) with `_refresh_imap_sequential` — calls
  `imap_connect` once, reuses that single connection across every IMAP
  source via the existing `refresh_imap_source`, logs out once at the
  end. This is a partial revert of `baf53c0`'s per-source-connection
  design: RSS stays concurrent (independent HTTP requests, no shared
  state, no abuse-detection downside observed), but IMAP goes back to one
  login per refresh — cheaper and indistinguishable from a normal mail
  client, which is what avoids triggering Gmail's throttling instead of
  just failing faster once it happens.

Tests updated to match: the old timing proof
(`test_imap_batch_fetches_concurrently_not_sequentially`) is replaced by
`test_imap_refresh_connects_once_and_reuses_it_across_sources`, asserting
`connect_fn` is called exactly once across N sources; the persistence
test now drives a single shared `FakeClient` instead of one per source.
207 backend tests passing. Deployed via `scripts/deploy.sh`; service
restarted clean, `/reader/` verified.

## 2026-08-13 (cont.) — Removed the Gmail OAuth connector; IMAP is now the only newsletter path

Feedback: *"Remove the code path for gmail fetch that will expire in 7
days. keep imap solution that doesn't require re-auth."* — `type: gmail`
never had a realistic path to running unattended (docs/PRD.md §7 already
flagged this: Google revokes refresh tokens after 7 days for any
"Testing"-status OAuth app, and escaping that for `gmail.readonly` needs
full verification + a paid CASA audit), and `type: imap` against a
dedicated app-password mailbox had already replaced it as the VM's actual
newsletter source months earlier — this removes the now-dead code path
rather than leaving two ways to do the same thing.

**Deleted outright:** `connectors/gmail.py` (the Gmail REST client + MIME
parser), `scripts/gmail_auth.py` (the one-time OAuth consent script),
`tests/test_gmail.py`, and the `gmail-auth` optional dependency group
(`google-auth-oauthlib`) from `pyproject.toml`.

**`config.py`:** `SourceType` narrowed from `"rss" | "gmail" | "imap"` to
`"rss" | "imap"`; dropped the `type=gmail requires query` validation
branch.

**`ingest/refresh.py`:** removed `refresh_gmail_source`,
`_scoped_gmail_query`, `_GMAIL_OVERLAP_SECONDS`, `GmailListFn`/`GmailGetFn`,
and the `elif s.type == "gmail"` branch in `refresh_all` — RSS and IMAP
are now the only two batches. Also fixed a real mislabel this surfaced:
`refresh_imap_source`/`_refresh_imap_sequential` were persisting IMAP
entries with `origin="gmail"` (piggybacking on the Gmail-specific
whitespace-tightening flag since it was the only "this is an email"
signal available) — now `origin="email"`, checked by `_persist_entry`
instead. `db.py`'s schema comment and the `articles.origin` column's
documented value set updated to match (`feed | email | llm`).

**`api/refresh_api.py`:** dropped `gmail_access_token` — no more reading
`settings.TOKEN_PATH` (also removed from `settings.py`; `data/token.json`
is now unread by any code path, though the file itself, if present,
stays gitignored rather than deleted).

**Frontend:** `SourceType`/`ArticleOrigin` in `api.ts` lost `'gmail'`
(`ArticleOrigin`'s `'gmail'` value became `'email'`, matching the backend
rename). `SourceModal.tsx`'s `lockedType` state existed only to
distinguish an editing source's real type between `'imap'` and `'gmail'`
(both rendered as the same newsletter-shaped form) — with only `imap`
left, that distinction is gone, so the state was removed in favor of
reading `sourceType` directly.

**Docs:** `README.md`'s "Gmail (optional)" section replaced with an IMAP
setup walkthrough (app password, not a Google Cloud OAuth client);
`docs/PRD.md` §4.2 rewritten from "Gmail newsletters" to "IMAP
newsletters", and its §7 known-gap entry about OAuth token expiry updated
from "here's the workaround" to "this is why the workaround is now the
only path"; `docs/ERD.md`'s architecture diagram, module map, data model,
API table, and the §5 concurrency/scoping paragraphs (still describing a
per-source-concurrent IMAP shape and a Gmail-token-based scoping model,
both already superseded by the two prior sessions today) all brought
current.

**Config:** local `config/feeds.yaml` (gitignored, not this repo's
concern normally, but already had 4 `type: gmail` entries left over from
before the VM's own config was migrated) converted to `type: imap` to
match what's actually running. `config/feeds.example.yaml`'s commented
Gmail example replaced with an IMAP one.

**One safety fix caught along the way:** deleting `gmail_client_secret.json`'s
dedicated `.gitignore` line (reasoning: "the code path is gone, so is the
need to ignore it") would have un-ignored a real credential file that's
still sitting in `config/` — confirmed via `git status` showing it turn
untracked (`??`) the moment the rule was removed. Restored the ignore
rule; the file itself wasn't touched.

192 backend tests passing (207 → 192: the 15 Gmail-specific tests
removed with `test_gmail.py` and the gmail refresh-source tests in
`test_refresh.py`). Frontend typecheck and build clean.

## 2026-08-13 (cont.) — Replaced Apache Basic Auth with an app-layer login, same day it was added

Feedback: *"The sign in for remote site seems to show very frequently.
Why? Also doesn't seem to trigger my mobile chrome password manager to
remember the password either."* Both symptoms turned out to be structural
to HTTP Basic Auth, not a misconfiguration — confirmed directly against
the live VM's Apache config (`AuthType Basic` / `AuthUserFile` /
`Require valid-user` inside `<Location /reader/>`, hand-edited on the box
earlier the same day, never tracked in this repo): Chrome's
password-manager save UI (desktop and mobile alike) hooks real `<form>`
POST submissions or the Credential Management API, never the browser's
native `WWW-Authenticate: Basic` popup — so it was never going to offer
to save this credential, on any platform. And Basic Auth's credential
cache is an in-memory, browser-session/tab-lifetime thing with no
server-side knob to extend it; mobile Chrome discards it aggressively
whenever it reclaims a backgrounded tab's renderer process, which is
routine on a phone. Investigated and confirmed reusing WordPress's own
login was still a dead end (per the earlier same-day entry:
`mod_authn_dbd` can't verify phpass hashes, `mod_authnz_fcgi` only
implements the FastCGI Responder role) — this plan doesn't touch
WordPress.

Decided with the user: app-layer login instead — a real login screen (so
Chrome recognizes and offers to save it) and a 90-day session cookie.
Single shared password, no accounts, matching this app's existing
no-multi-user-auth stance.

**`app/auth.py` (new).** `verify_password` — bcrypt check against
`READER_AUTH_PASSWORD_HASH` (a hash generated locally the same way
`.htpasswd-reader` was, `htpasswd -nbB`; the plaintext never touches the
server, arriving only transiently in a login request body). Session
token is stateless — `{expires_at}.{hmac_sha256(secret, expires_at)}`,
signed with `READER_SESSION_SECRET` — no session table, nothing to
garbage-collect; verifying is one `hmac.compare_digest` and an expiry
check. `AuthMiddleware` (pure ASGI middleware) gates every `/api/*` route
except `/api/login`/`/api/logout`, returning a plain 401 with no
`WWW-Authenticate` header — that header is specifically what makes a
browser pop its native dialog, and omitting it is what lets the SPA's own
fetch code handle a 401 instead. `app/api/auth_api.py`: `POST /api/login`
(bcrypt check via `asyncio.to_thread` — deliberately ~100-300ms of CPU,
must not run inline on the event loop, same pattern as this app's other
blocking work) and `POST /api/logout` (clears the cookie, always 200).

**Caught during implementation, not in the original plan: fail-closed-by-
default would have broken local/LAN use.** First pass made
`AuthMiddleware` reject every request whenever either env var was unset —
reasoning was "this is what stands in for Basic Auth, it must not have a
forgot-to-configure bypass." Live-tested against a local run with no env
vars set and found every route 401ing, which silently makes the app
unusable for exactly the local/LAN scenario the README's Quickstart has
always promised zero-auth for (Basic Auth was only ever added at the
Apache layer, only on the internet-facing VM — local dev never had it).
Fixed: both env vars unset is now permissive (matches every other
optional credential in this app — `IMAP_HOST`, `READER_READONLY_CONFIG`);
*exactly one* set fails closed, since that looks like a deploy mistake
rather than an intentional "no auth" choice.

**Frontend.** `api.ts` gained an `apiFetch()` wrapper so every call
(previously 15 independent `fetch()` sites, none setting `credentials`)
gets `credentials: 'include'` in one place, plus `login()`/`logout()` and
an `UnauthorizedError` the shared `json<T>` helper throws on 401 instead
of a generic `Error`, so callers can tell "not logged in" apart from a
normal failure. `main.tsx`'s `QueryClient` gets a `retry` override that
doesn't retry `UnauthorizedError` — otherwise every query would retry 3
times against a 401 before settling, delaying the login screen for no
reason. `LoginPage.tsx` (new): a real `<form onSubmit>` with a single
password field (`autoComplete="current-password"`, no username — this
app has exactly one credential) — that realness is what makes Chrome's
save-password heuristic fire at all. `App.tsx` renders it instead of the
3-pane shell whenever `sourcesQuery.error instanceof UnauthorizedError`;
`Sidebar.tsx` gets a "Log out" button in its footer, next to Configure.

**Verification.** `bcrypt` added as a real dependency (`pyproject.toml`)
— the one class of thing not worth hand-rolling, and small next to what
the Gmail OAuth removal just took out. 202 backend tests passing (192 →
202: new `test_auth.py` — login success/failure, protected-route
gating, expired/tampered tokens, logout, and both the permissive-when-
unconfigured and fail-closed-when-partial cases found above). Frontend
typecheck and build clean. Full login/logout flow smoke-tested live
against a local server via curl before touching the VM: 401 unauthenticated,
401 wrong password, 200 + `Secure`/`HttpOnly`/`SameSite=Lax` cookie
(`Max-Age=7776000`) on success, 200 on the protected route with the
cookie, 200 logout, 401 after — confirmed no `WWW-Authenticate` header
anywhere the way Basic Auth always sent one.

VM cutover (provisioning the new env vars, removing Apache's
`AuthType Basic` block, decommissioning `.htpasswd-reader`) is a separate
manual step, not yet done as of this entry — same "secrets never touch
this repo or `deploy.sh`" pattern the IMAP credentials already use.

**VM cutover, done.** `READER_AUTH_PASSWORD_HASH`/`READER_SESSION_SECRET`
added to `/opt/openreader/openreader.env`, deployed via `scripts/deploy.sh`.
Verified the app-layer login directly against `127.0.0.1:8787` first
(bypassing Apache) before touching its config — hit one testing-method
false alarm there: the VM's `curl` is an ancient libcurl (7.64.0, Debian
buster) that correctly refuses to store/send a `Secure`-flagged cookie
over plain `http://127.0.0.1`, same as a real browser would; not a bug,
just the wrong protocol to test a `Secure` cookie against. Backed up
`wordpress-https.conf`, removed the `AuthType Basic`/`AuthUserFile`/
`Require valid-user` lines (kept `ProxyPass`), `apachectl configtest` +
reload, then verified the full flow through the real
`https://pengyaochen.com/reader/` domain: 401 with no cookie, 401 wrong
password, 200 + correctly-flagged `Secure` cookie on success, 200 on a
protected route with that cookie, `index.html` still reachable with no
cookie at all (so the login screen itself can load). Decommissioned
`.htpasswd-reader` and the config backup once confirmed working.

## 2026-08-13 (cont.) — Fixed excessive blank lines in WSJ newsletter articles

Feedback: *"Read into the WSJ What's News latest article. There are a
lot of new lines when I read it... look into the email parsing code."*
Pulled the live article's `content_html` from the VM DB (id 178) and
counted tags: 45 `<table>`, 179 `<tr>`, only 25 `<p>` — a classic
HTML-email layout table, one (or a few) big tables used purely for
positioning, not tabular data.

`tighten_newsletter_whitespace` (`ingest/textutil.py`) already stripped
fully-empty `<p>` spacers and fully-empty `<table>` chains, but never
looked at individual `<tr>` rows — and WSJ's markup interleaves empty
`<tr><td> </td></tr>` spacer rows *between* real content rows in the
*same* table, so the whole-table-emptiness check never caught them (the
table has real content elsewhere). Counted directly: 81 of 179 `<tr>`
elements (45%) were pure spacers. Each `<tr>` renders as its own block
box once the reader's CSS makes tables scrollable — that's the actual
source of the "lots of new lines," not the already-handled fully-empty
nested spacer tables.

Fix: added the same bottom-up empty-node sweep already used for
`<table>` to `<tr>` too, in the same fixed-point loop (removing a
row can leave its table empty, and removing a table can leave an outer
row — in a parent layout table — empty in turn, so both need sweeping
together until a pass removes nothing). No change to what counts as
"visually empty" (`_is_visually_empty`, unchanged) or to the `<br>`/
`&nbsp;` regex collapsing — only the sweep target list grew.

Ran the actual WSJ article through the fixed function to confirm before
touching anything live: 179 `<tr>` → 98 (removed exactly the 81 counted
spacers), `<table>` count unchanged (45 → 45 — none of *this* article's
tables happened to become fully empty as a side effect), real content
(`"Luigi Mangione"` etc.) confirmed still present, ~1.5KB of pure spacer
markup removed.

`tighten_newsletter_whitespace` had zero direct unit tests before this —
added seven to `test_textutil.py`: empty input, empty-paragraph removal,
`<br>` collapsing, `&nbsp;` collapsing, fully-empty nested spacer tables
(the pre-existing case), and two new ones for the actual bug — empty
`<tr>` rows removed from within an otherwise-real table, and a table with
only real rows left fully intact. 209 backend tests passing (202 → 209).

Deployed via `scripts/deploy.sh`, then backfilled already-stored
`origin='email'` articles on the VM by re-running the fixed function over
their already-sanitized `content_html` and writing the result back in
place — a refresh alone wouldn't have touched them, since dedup on
`(source_id, guid)` means an already-ingested message is never
re-fetched/re-processed.

Backfill also had to cover `origin='gmail'` rows, not just `origin='email'`
— found live: only 2 of 6 stored email-type articles had the new `'email'`
label; the other 4 predated this session's origin rename (`'gmail'` →
`'email'`, when the Gmail OAuth connector was removed) and were still
sitting on the old value. 4 of 6 articles actually changed (2 needed no
change — already had no spacer rows). Verified article 178 (the one
actually reported) directly through the live authenticated API afterward:
`content_html` now 23,794 bytes with 98 `<tr>` — exactly matching the
local dry-run's prediction (179 → 98) before anything was touched live.

## 2026-08-14 — On-demand article summarization via `claude -p`, and the VM provisioning it needed

Feature request: a "Summarize" button in the reader, next to Star, using
the *subscription* (not API-key billing) via `claude -p`, for personal
adhoc use (~1 query/5min). Faithful summaries only — sonnet, no
hallucination, grounded purely in the article text — proportional length
(`max(100 words, 20% of the original)`), formatted for readability with
bullets and bold highlighting, and persisted so a summary is still there
on the next visit.

Before writing any app code, spent the first part of this session
provisioning the VM to run `claude -p` at all, and measuring it — apt is
unusable on this buster VM (archived at EOL, same constraint
`scripts/deploy.sh`'s header already documents), so Node v24.19.0 LTS was
installed from the official prebuilt tarball (checksum-verified) under
`/opt/node`, and `@anthropic-ai/claude-code` installed for the
`openreader` user via a user-local npm prefix (`~/.npm-global`) —
no `sudo npm`, no touching apt. One-time interactive `claude` login as
the `openreader` user seeded `~/.claude/.credentials.json` against the
Pro/Max subscription's OAuth, not an API key.

Measured live, twice: a single `claude -p` call peaks around 300MB RSS
(301MB and 308MB in two separate measurements), fully released after the
process exits — confirmed via `free -h` before/during/after and by
polling `ps` RSS across the process and its children while a real call
ran. Against the VM's ~985MB total (MySQL + Apache + the OpenReader
backend's own steady state already use ~350-400MB), that's tight but
workable for sparse, adhoc use with the 2GB swap already in place as a
cushion — not something to run per-request at any real frequency, but
fine for "click a button occasionally."

**Design**: reused `app/generate/client.py`'s subprocess pattern
(subscription OAuth, `ANTHROPIC_API_KEY` scrubbed from the child env,
`--strict-mcp-config`/`--setting-sources ""` for isolation, never
`--bare`) in a new `app/generate/summarize.py` — a distinct capability
from `app/generate/`'s topic research (WebSearch/WebFetch, multi-minute
jobs polled via a jobs table): summarization hands the model text already
in hand, `--tools ""` (disables all tools — confirmed via `claude --help`,
nothing to research), and runs synchronously through the existing
`run_off_thread` helper (same one `articles.get_article` already uses for
`hydrate_article`) rather than reintroducing the jobs/poll machinery,
since a call takes ~5-20s, not minutes. Model is hardcoded to `"sonnet"`
in `summarize.py`, decoupled from whatever `config.llm.model` is set to
for topic generation. The model's returned `summary_html` is run through
the same `textutil.sanitize_html` allowlist as every other piece of HTML
this app renders via `dangerouslySetInnerHTML` — never trusted just
because it came back from our own prompt. `plain_text_excerpt` gained an
optional `limit=None` (skip truncation) so summarization sees the full
article, not the 300/900-char excerpts used elsewhere in the app — needed
both for an accurate word count and because sonnet's 1M-token context has
no practical reason to truncate a personal RSS article.

New `articles.summary_html`/`llm_summary_at` columns, persisted forever
once generated (no regenerate path — nothing in the ask called for one).
`app/db.py` has no migration framework (`CREATE TABLE IF NOT EXISTS`
only) — added an idempotent `ALTER TABLE ... ADD COLUMN` guarded by
`try/except sqlite3.OperationalError` (ignoring "duplicate column name")
in `init_schema()`, so the already-deployed VM database picks the columns
up on next restart with no manual migration step.

Frontend: a ✨ button next to Star (visible only when `llm.enabled`, reusing
the `topicsQuery` the sidebar's existing Topics feature already fetches —
no new query); once a summary exists it becomes a Full/Summary segmented
toggle, auto-switching to Summary the moment a fresh one lands via the
mutation's query-cache patch (not on every render — tracked via a ref so
opening an article that already had a cached summary doesn't force-switch
away from Full). 20 new backend tests (injected-runner unit tests for the
CLI wrapper, mirroring `test_generate_client.py`'s conventions, plus API
tests for the cache/kill-switch/error paths) — 228 passing total. `tsc
--noEmit` clean.

**Deploying it surfaced two VM-side bugs neither existed before this
session, because `claude` never ran on this VM until today** — so
topic-generation (the pre-existing feature) had silently never actually
worked here either:
1. `openreader.service`'s `PATH` had neither `/opt/node/bin` nor
   `~/.npm-global/bin` — `claude` wasn't found. Fixed by adding an
   explicit `Environment=PATH=...` line to the unit.
2. `MemoryMax=300M` — sized only for the FastAPI process itself — is
   smaller than a single `claude -p` call's own ~300MB peak RSS *alone*,
   before adding the service's own ~100MB baseline. The very first
   summarize/generate call would have been OOM-killed by the cgroup.
   Raised to `700M` (VM total ~985MB, non-OpenReader baseline ~350-400MB,
   2GB swap as further cushion).
3. (Related, caught while editing the unit) `ProtectHome=read-only` would
   have blocked `claude`'s OAuth token-refresh writes to
   `~/.claude/.credentials.json` — added `/home/openreader/.claude` to
   `ReadWritePaths` alongside the existing data/config paths.

Verified the memory fix properly, not just by assertion: `claude` doesn't
report live `MemoryCurrent` under this VM's systemd 241 hybrid cgroup v1/v2
setup (a pre-existing, already-documented gap in the unit file's own
comments), so ad hoc SSH-spawned test calls don't actually run inside the
service's cgroup and can't validate the cap that way. Instead used
`systemd-run --user --scope -p MemoryMax=700M` to run the real
`summarize_text` call inside a transient scope with the identical limit
the live service uses — `Result=success`, no OOM-kill, real summary
persisted to two real production articles (ids 180, 182) — before trusting
the number.

Both VM-unit changes and the Node/CLI provisioning steps are one-time,
by-hand setup — not scripted by `scripts/deploy.sh` (the OAuth login step
is inherently interactive), but documented in its header comment,
alongside the existing `openreader` service-user prerequisite, so they're
at least discoverable rather than tribal knowledge from this session.

Deployed via `scripts/deploy.sh` (bumped its post-restart verification
`sleep` from 1s to 3s after watching the existing value race a real
restart and report a false 503). `config/feeds.yaml`'s `llm.enabled` was
still `false` on the VM (config edits are SSH-only by this app's design,
never touched by `deploy.sh`) — flipped to `true` by hand; `topics: []`
was already empty, so this only activated Summarize, not the unrelated
topic-generation feature.

## 2026-08-14 (cont.) — Summarize button: spinner instead of a static "…"

Feedback: the Summarize button just swapped to a static "…" while a call
was in flight — no visual cue that it was actually working versus stuck.
Replaced it with a small CSS-only spinning ring (`.spinner`, `@keyframes
spin`, 0.7s linear) in the button's violet accent, and gave the button
itself a `.icon-btn--busy` highlight (violet border/wash) while
`summarizing` is true, so the whole control reads as "actively working,"
not just disabled. No new dependency — pure CSS animation.

## 2026-08-14 (cont.) — Summary styling wasn't from the model, and the button UX was wrong

Two pieces of feedback on the same session:

1. *"Why are the summarized posts always have a line on the left side and
   purple highlights? Is it coming from the summarized HTML or is it
   hardwired?"* — hardwired, not the model's output: `.reader-body--summary`
   (added alongside the feature itself) set a violet `border-left` and
   recolored `b`/`strong` violet. Removed both rules entirely — bold text
   in a summary now renders exactly like bold text in a full article (the
   article's default ink color, effectively black in light mode), no side
   border. `summarize.py`'s system prompt and `sanitize_html`'s allowlist
   were never involved in this — worth confirming explicitly since the
   question could as easily have been about either of those.
2. The Full/Summary segmented toggle that replaced the ✨ button once a
   summary existed was the wrong shape — asked for a single button that
   never changes shape: click once (no summary yet) to generate, and once
   generated the *same* button toggles Full↔Summary in place on each
   subsequent click, showing a selected state (reusing the existing
   `.icon-btn.active` amber highlight, same visual language as Star)
   when on the Summary view. Removed `.view-toggle`/`.view-toggle__btn`
   entirely — `viewMode` local state and the auto-switch-to-summary effect
   on first generation are unchanged, only the button markup and its click
   handler changed (branches on whether `article.llm_summary_html` is set:
   generate vs. toggle).

## 2026-08-14 (cont.) — Consolidated the VM's env vars into one file

Feedback, after walking through every non-git config on the VM field by
field (no values shown, just names, to build confidence config wasn't
scattered): `openreader.service` had `READER_CONFIG`/`READER_DB`/
`READER_MEDIA`/`PATH` as inline `Environment=` lines, split from the
actual secrets (`READER_IMAP_*`, `READER_AUTH_PASSWORD_HASH`,
`READER_SESSION_SECRET`) sitting in `/opt/openreader/openreader.env` —
two files for the same category of thing (service env vars), for no real
reason.

Moved the four non-secret lines into `openreader.env` too, and stripped
them from the unit file, which now carries a single `EnvironmentFile=`
line and nothing else env-related. Verified via `/proc/<pid>/environ` on
the restarted service that `PATH`/`READER_CONFIG`/`READER_DB`/
`READER_MEDIA` all still resolved correctly (systemd's own
`systemctl show -p Environment` only reflects inline `Environment=`
lines, not `EnvironmentFile=` contents, so that alone wouldn't have
proven anything), then confirmed `https://pengyaochen.com/reader/` still
200'd.

This didn't reduce the total number of separate config locations on the
VM (still four: this env file, `feeds.yaml`, and the `claude` CLI's own
`~/.claude/.credentials.json` + `~/.claude.json`) — those serve genuinely
different purposes and shouldn't be merged (in particular,
`.credentials.json` is written/refreshed by the `claude` CLI itself, not
something this app should own). It just removed a pointless split within
one of those four.

Documented as a reproducible recipe in README.md's new "Production
deployment: one env file, not two" section — the full `openreader.env`
template and unit file, not just the narrative of what changed, so
someone standing up their own systemd deployment from the GitHub repo
doesn't have to reconstruct this from WORKLOG archaeology.

## 2026-08-14 (cont.) — IMAP refresh: one SEARCH/FETCH pass per folder, not one per source

Feedback: "instead of doing one separate call for each of the newsletters,
we can do one call to fetch all the emails from the last sync time and
then apply the filter." Fair — `_refresh_imap_sequential` already shared
one login across sources (2026-08-13), but each source still ran its own
`SEARCH` scoped by its own `from:`/`subject:` query tokens, applied
server-side.

Talked through the tradeoff before building: per-source SEARCH's FROM/
SUBJECT filtering means the server never hands back a message that won't
match — one shared SEARCH ALL/SINCE trades that for fewer round-trips, at
the cost of FETCHing (the actually expensive part — full RFC822 bodies)
messages that might not match any configured source. Reasonable for this
app specifically, since the README already recommends a dedicated
newsletter-only mailbox — close to 100% of messages there should match
something.

Landed as `refresh_imap_sources` (`ingest/refresh.py`): groups sources by
`mailbox_folder`, one `SEARCH` per distinct folder, one `FETCH` per
message id regardless of how many sources it matches (dispatch happens
after the fetch, not before — confirmed no source's presence causes a
duplicate FETCH, per explicit feedback to check that). `from:`/`subject:`
matching that used to happen server-side moved to a new
`imap_connector.matches_query()`, applied locally per source after fetch —
had to add `parse_message_with_from_header()` alongside the existing
`parse_message()` because IMAP's own FROM SEARCH matches the *whole*
header (display name + address), and `NormalizedEntry.author` only keeps
the display name (`parseaddr()`'d already, inside `parse_message`) — a
`from:someone@example.com` query would never have matched against that.
`refresh_imap_source` (singular) is now a thin wrapper calling
`refresh_imap_sources` with a group of one, so every existing single-
source test kept passing unchanged.

Three rounds of live back-and-forth on the shared SEARCH's `since` bound,
each one changing the actual answer:
1. First cut: `max` of the group's per-source `since` values (scope to
   the most-recently-synced source). Caught before shipping: a source's
   own `since` check after the fetch can only *narrow* what it accepts,
   never pull in a message the SEARCH never returned — so `max` would
   silently and **permanently** skip a newly-added source's intended
   backfill (its `last_fetched_at` still advances to `now` regardless of
   what it actually saw), not just delay it to next refresh.
2. Corrected to `min` instead — identical to `max` in the steady state
   (every source in a group already shares one `last_fetched_at` after a
   joint refresh), only diverges for a source that's genuinely behind, and
   in that case `min` is what actually gives it a real chance to backfill
   instead of quietly losing history.
3. Feedback: "min - but limit to last 7 days." `min` alone means one
   brand-new source (30-day default window) sharing a folder with
   already-synced siblings forces the *whole group* through a wide
   30-day re-SEARCH+FETCH every refresh until that source catches up —
   added `_IMAP_SEARCH_LOOKBACK_CAP_DAYS = 7` as a floor clamped onto the
   `min` result (`max(min(...), now - 7d)`), applied uniformly (including
   the single-source path, since `refresh_imap_source` now shares the
   same code). A brand-new source's first sync backfills at most 7 days,
   not its full configured/default window — bounded cost over full
   backfill, matching this app's "no scheduler, adhoc use" posture
   elsewhere. An explicit `newer_than:Nd` with N > 7 is capped the same
   way now too, worth knowing if anyone configures one.

Caught one real bug before it shipped: `_refresh_imap_sequential` (the
actual multi-source entry point `refresh_all` calls) was still looping
`refresh_imap_source(...)` once per source — meaning it kept calling the
new grouped function with a group of *one* every time, batching nothing
in practice. Fixed to call `refresh_imap_sources(conn, sources, ...)`
once for the whole batch, and added a dedicated regression test
(`test_refresh_imap_sequential_issues_one_search_across_all_sources_in_one_folder`)
asserting exactly one `SEARCH` call across multiple sources through that
actual entry point — the earlier "one search per folder" test only
exercised `refresh_imap_sources` directly and wouldn't have caught this.

10 new backend tests: `matches_query`/`parse_message_with_from_header`
unit tests in `test_imap.py`, plus grouped-refresh tests in
`test_refresh.py` covering one-search-per-folder (both at the
`refresh_imap_sources` level and through the real `_refresh_imap_sequential`
entry point), no-duplicate-fetch-across-matching-sources, local from:
routing, the 7-day cap, and the min-not-max new-source-backfill case
specifically. 239 backend tests passing (228 → 239).

## 2026-08-14 (cont.) — Removed LLM-generated topic tracking; Summarize stays

Feedback: *"Remove the LLM generate feed article feature. I don't [think]
that's useful. Remove from readme as well. Keep the LLM summary feature
though."* Confirmed on the VM first — production DB had zero rows in
`jobs`, zero `origin='llm'` articles, zero `type='llm'` sources (the
`topics: []` block had been empty the entire time llm.enabled was ever
on) — so this was a clean removal, not a lossy one, and safe to drop the
DB schema for it too, not just leave it inert.

**Backend**: deleted `app/generate/` entirely (`client.py`, `prompt.py`,
`jobs.py`, `worker.py`) and `app/api/generate_api.py`. `summarize.py` was
the only real consumer of `client.py`'s shared pieces (`ClaudeError`,
`Runner`, `_default_runner`) — rather than leave a nearly-empty `client.py`
around for three symbols, flattened the whole `app/generate/` package down
to a single `app/summarize.py` at the top level, now self-contained. This
is also the actual answer to "look for opportunities to simplify" asked
alongside this removal: one file, one responsibility, no package nesting
left over from a feature that's gone.

`config.py`: removed the `Topic` struct, `Config.topics`, and
`Config.topic()`; `LLMSettings` shrank to just `enabled` (`model`/
`timeout_minutes` were only ever read by the removed worker/generate_api).
Also dropped `interest_profile` — it existed solely as free-text context
for topic briefs, unused by anything else, so it's dead weight now too
(msgspec silently drops unknown YAML keys, so this doesn't break loading
an existing `feeds.yaml` that still has old `topics:`/`interest_profile:`
content — confirmed against both `feeds.example.yaml` and the real local
`feeds.yaml`).

**Removing `/api/topics` broke something non-obvious**: the frontend's
`llmEnabled` gate on the Summarize button was reading `/api/topics`'s
`enabled` field, not anything summarize-specific. Added a small
replacement, `GET /api/llm-status` → `{"enabled": bool}`, rather than
leave Summarize with no way to know if it should show itself.

**DB schema**: dropped the `jobs` table and `articles.job_id`/
`citations_json` columns for real, not just stopped creating them for new
databases — `init_schema()` gained `DROP TABLE IF EXISTS jobs` (naturally
idempotent) and a `_drop_column_if_present` helper mirroring the existing
`_add_column_if_missing` one (catches "no such column" the way its sibling
catches "duplicate column name"). Confirmed `ALTER TABLE ... DROP COLUMN`
support first — SQLite 3.53.1 bundled with Python's stdlib `sqlite3`,
checked on both this machine and the VM, well past the 3.35 minimum.
New test simulates a pre-removal database (full old schema, `jobs` table
included) and asserts `init_schema()` cleans it up on next startup,
idempotently on a second call too — not just that a fresh DB never creates
these anymore.

**Frontend**: removed the Topics sidebar section (`.gen-topic`/`.gen-btn`/
`.gen-spinner` and their now-dead CSS, including a duplicate `@keyframes
spin` left over once the old generation spinner was deleted — the
Summarize button already had its own), the `generateMutation`/job-polling
`useEffect` in `App.tsx`, the "Generated by Claude" badge and citations/
Sources block in `ArticleReader.tsx` (`citations_json` doesn't exist
anymore either), and the `origin === 'llm'` badge in `ArticleList.tsx`.
`ArticleOrigin` narrowed from `'feed' | 'email' | 'llm'` to `'feed' |
'email'`. `topicsQuery` replaced by `llmStatusQuery` reading the new
endpoint.

**Tests**: deleted `test_generate_client.py`/`test_generate_jobs.py`/
`test_generate_worker.py` outright (tested code that no longer exists).
Stripped the topic/job tests from `test_api.py`, replaced with two for
`/api/llm-status`. `test_summarize.py` and the summarize-specific tests in
`test_api.py` just needed their import path updated
(`app.generate.summarize`/`app.generate.client` → `app.summarize`) — the
feature itself and its test coverage were untouched, exactly as asked.
Added a `test_db.py` case for the schema-drop migration. 212 backend tests
passing (239 → 212 — a large net removal, as expected for deleting a
feature rather than adding one).

**Docs**: rewrote `README.md`'s intro as a positioning pitch per explicit
request — "open-source, self-hosted alternative to Feedly/Inoreader,
runs on a sub-1GB VM" — backed by a real fact already true of this app
(the co-hosted VM is a 1GB e2-micro also running WordPress), not an
invented claim. Restructured the rest of the README into a numbered
"Self-hosting" walkthrough (local run → sources → IMAP → login →
summarization → VM deploy) rather than a flat list of independent
sections. `docs/PRD.md` §4.3 (topic tracking) removed outright and
sections renumbered; §4.4 (now the on-demand-summarization section)
reworded to stop referencing the removed feature ("the same subscription
topic generation uses" → just states its own facts). `docs/ERD.md`'s
architecture diagram, module map, data model, API table, and "key
technical decisions" prose all updated — including removing the
`--permission-mode bypassPermissions` decision entry entirely, since that
was specific to the removed feature's WebSearch/WebFetch tool use and
doesn't apply to `summarize.py` (`--tools ""`, no research, no permission
question to answer).

Not yet deployed to the VM — this removal touches the production DB
schema (dropping `jobs`/`job_id`/`citations_json`) and flips a behavior
change (the Generate feature disappearing from the sidebar), so it should
go out deliberately via `scripts/deploy.sh`, not bundled silently into
some other change.

## 2026-08-13 — On-demand "Pull full article" button

Feedback: the reader already hydrates full text lazily on open, but only
when the source's `fetch_full_text` config is on, and only once — there
was no way to force it for a specific article on demand. Added a button
in the reader bar, left of Summarize, that does exactly that.

Deliberately reused `hydrate_article()` unchanged rather than adding a
force-refetch flag or new DB column: the new `POST
/api/articles/:id/hydrate` endpoint just calls it with
`fetch_full_text=True` hardcoded, ignoring the source's own config. Its
existing one-shot short-circuit (`hydrated_at`/`hydrate_failed_at`) does
double duty as the button's own idempotency guard — no new state to
track. The frontend hides the button once either field is set, so it
disappears after one attempt whether it succeeded or failed (kept it
simple rather than adding a retry-on-failure path, since nothing in the
request asked for one).

Because the pulled text is written to `content_html` synchronously and
patched into the query cache immediately, a later Summarize click reads
the already-replaced full article for free — no special-casing needed to
make summarization prefer full text over the truncated excerpt.

## 2026-08-14 (cont.) — Settings drawer consolidation, list payload trim, batch hydration, reader footer nav

**Settings drawer**: merged the sidebar's separate "+ Add source" button,
per-row ✎ edit button, and "⚙ Configure" (raw YAML) panel into one entry
point — a single "⚙ Settings" button opening `SettingsDrawer.tsx`, the
only overlay/drawer in the app now. Two independent overlays used to
compound their backdrops and slide-in animations when a source modal
opened on top of the config panel, which read as a glitch (a second bar
sliding in over the first, background going darker again). `SourceModal.tsx`
renamed to `SourceForm.tsx` (`onClose` → `onCancel`) and `ConfigEditor.tsx`
renamed to `YamlConfigPanel.tsx`, both stripped of their own overlay chrome
to render as panes inside `SettingsDrawer`'s two tabs: **Feeds** (search-
and-pick list, backed by `SourceForm` for add/edit) and **Advanced** (the
raw `feeds.yaml` editor, kept as an escape hatch for config keys — `llm`
settings, `defaults` — the structured form doesn't cover). `Sidebar.tsx`
lost `onAddSource`/`onEditSource` entirely; `App.tsx`'s `sourceModal`/
`configOpen` state collapsed into one `settings: 'list' | 'add' | null`
(editing a specific source is `SettingsDrawer`'s own internal pane state,
nothing to track above it).

**List payload trim**: `GET /api/articles` was shipping every column
(including `content_html`/`llm_summary_html`, tens of KB each) on every
row, even though the list view only ever renders title/excerpt/meta — a
50-item page was ~570KB of JSON the client immediately discarded.
`store.py` gained `_LIST_COLUMNS` (all of `_ARTICLE_COLUMNS` minus those
two) for `list_articles`, plus a computed `has_summary` boolean so the
list can still show a summary indicator without the body.
`GET /api/articles/:id` (`get_article`) is untouched — still the full
row. Frontend: new `ArticleListItem` type (`api.ts`) drives `ArticleList`/
`App.tsx`; `App.tsx`'s cache-patch helper (`patchArticleCaches`) gained
`toListPatch` to translate an `Article` patch (e.g. a freshly generated
`llm_summary_html`) into the list cache's `has_summary` flag instead of
writing the (now absent) field. The reader's placeholder-while-loading
data (built from the list item on prev/next nav) renders blank body
fields for a beat now — covered by the existing loading skeleton via
`openArticleQuery.isLoading`, not a regression in practice since the real
query result swaps in immediately after. `openArticleQuery` also gained
`staleTime: Infinity`/`gcTime: 30min` — an article body never changes
after hydration (mutations patch the cache directly), so there was never
a reason to refetch it on window refocus or evict it quickly.

**Sidebar unread counts**: `list_sources`/`get_source` computed
`unread_count` via `LEFT JOIN articles ... COUNT(...) FILTER (...) GROUP
BY s.id` — a full scan of every article for every source, on every
sidebar load. Replaced with a pre-aggregated subquery
(`GROUP BY source_id` once, joined back to `sources`), which the new
`idx_articles_src_unread (source_id, is_read)` index turns into an
index-only scan.

**Query param hardening**: `list_articles`' `?limit=`/`?offset=` did a
bare `int(...)` — `?limit=abc` 500'd, and `?limit=999999` was honored
outright, letting a client force an unbounded scan. New `_parse_int`
helper (`articles.py`) falls back to the default on anything unparseable
and clamps into `[minimum, maximum]` (`limit` capped at 200).

**Batch full-text hydration**: previously, full-text extraction only ever
happened lazily on `GET /api/articles/:id` — the first open of any article
paid a live ~5s fetch inline on the read path. `hydrate.py` gained
`hydrate_pending`, called at the end of `refresh_all` (`refresh.py`), which
fetches+extracts up to 100 eligible not-yet-hydrated articles per refresh
across a bounded 6-way thread pool (`fetch_and_extract`, the pure
network+parse half of `hydrate_article`, split out so it's safe to run off
the DB connection). `hydrate_article` now just calls the same
`fetch_and_extract`, so the on-open path and the batch path share one
implementation. Scoped to `to_refresh` (not every configured source) so a
single-source refresh (`?source=key`) doesn't also hydrate backlog on
every other source; sources not opted into `fetch_full_text` are skipped
entirely. Most articles are now already hydrated by the time a user opens
them — `GET /api/articles/:id` short-circuits on the indexed
`hydrated_at` read instead of fetching live.

**DB pragmas + indexes**: `connect()` now sets `foreign_keys=ON` (was
never enforced before), `busy_timeout=5000` (retry internally instead of
raising "database is locked" when a background hydrate/refresh thread
briefly holds the write lock), `synchronous=NORMAL` (safe under WAL,
cheaper than FULL), and `temp_store=MEMORY`/`cache_size=-16000` (keeps
ORDER BY temp b-trees and a larger page cache in memory for this small,
frequently-read DB). New indexes: `idx_articles_unread` gained a trailing
`, id DESC` to match `list_articles`' tiebreaker (ties on `published_at`
aren't rare across feeds/newsletters sharing a timestamp) so SQLite can
satisfy the sort from the index instead of a temp b-tree; `idx_articles_pub`
backs the unfiltered "all" view; `idx_articles_starred` backs the
previously-unindexed starred view; `idx_sources_folder` backs the folder
filter. Turning `foreign_keys` on surfaced a real ordering bug in
`init_schema()`: it dropped the `jobs` table before dropping
`articles.job_id` (the column that references it), which now fails with
`IntegrityError: FOREIGN KEY constraint failed` instead of silently
succeeding as it did with enforcement off. Fixed by dropping the
referencing column first.

**Reader footer nav**: replaced the always-visible floating `‹`/`›` side
buttons in the fullscreen reader with a labeled "‹ Previous article" /
"Next article ›" pair inline at the end of the article body — feedback
was that the side arrows were a distraction while reading and only need
to be seen once you've actually finished the article. Pure DOM
placement (`ArticleReader.tsx`), no scroll-position JS: the buttons are
only reachable by scrolling to the end, same as any other footer content.
Keyboard ←/→ navigation is unchanged.

**iOS scroll indicator missing**: `.reader-scroll`, `.article-list`, and
`.feed-picker__list` were each a flex child (`flex: 1`) with
`overflow-y: auto` but no `min-height: 0`. Desktop Chrome (Blink) is
lenient and lets the item shrink to scroll anyway; iOS Safari/Chrome
(WebKit) is spec-strict and won't constrain the item without it —
scrolling itself still worked, but WebKit didn't draw its native overlay
scroll indicator on the (incorrectly-sized) element either. This exact
bug, and the same fix, already existed in this file for `.settings-pane`
(see 2026-08-10's entry above) and was missed in these three spots.
`.article-row__excerpt` also bumped from 13.5px to 15.5px for readability.

221 backend tests passing (up from 212 — new coverage for
`hydrate_pending`, the query-param clamping, and the `init_schema` drop
ordering).

## 2026-08-14 (cont. 2) — Reader footer nav restyle, Load more width fix

Feedback on the labeled footer-nav buttons from the entry above: *"the
buttons look really bad. Just go with a low profile left and right arrow
similar to style as the go-back button on top"* — the flex layout was
also making one button read as centered and the other right-aligned
instead of pinned to opposite edges. Replaced the `reader-footer-nav__btn`
pair with plain `.icon-btn` chevrons (same class as the reader bar's ←
back button), pinned left/right via `justify-content: space-between` on
`.reader-footer-nav`, with a `<span />` spacer standing in for a hidden
prev button so the next button doesn't drift to center.

Separately: *"load more should be as wide as the feed tiles, not edge to
edge"* — `.load-more-btn` used `width: calc(100% - 56px)` with fixed
28px side margins, so on viewports wider than `.article-row`'s
`max-width: 760px` centered box, the button stretched edge-to-edge past
the tiles above it. Added the same `max-width: 760px` and switched to
`margin: 8px auto 24px` so it centers and lines up with the tiles at any
width, same as before on narrow viewports.

## 2026-08-16 — Horizontal-pan/scrollbar bug fix, PWA installability

**Bug report** (screenshot from iOS Chrome): one specific article allowed
left-right dragging that interrupted top-to-bottom reading flow, and
separately the scroll indicator was invisible on iOS Chrome. Root-caused
both: `.reader-scroll` set `overflow-y: auto` but left `overflow-x`
unset — per spec, an element with one overflow axis non-`visible` and the
other unset gets that axis computed to `auto`, not `visible`. So any
article with content a hair wider than the pane (an oversized embed
image, a long unbroken URL used as visible link text) silently made the
*whole reading pane* horizontally pannable instead of just clipping it.
Fixed with an explicit `overflow-x: hidden` on `.reader-scroll`, paired
with `overflow-wrap: anywhere` on `.reader-body` so an unbreakable long
string wraps instead of getting silently clipped by the new
`overflow-x: hidden`. Tables/`pre` already carry their own scoped
`overflow-x: auto` and are unaffected. Separately, iOS Safari/Chrome
(both WebKit) ignore `::-webkit-scrollbar` theming for their overlay
scrollbar and fall back to a system default that read as invisible
against the dark background — added the standard `scrollbar-color`
property (which recent iOS WebKit honors) alongside the existing
`::-webkit-scrollbar` rules for desktop.

**PWA installability** (feature request, brainstormed → spec → plan →
executed via `superpowers` skills; spec at
`docs/superpowers/specs/2026-08-16-pwa-install-design.md`, plan at
`docs/superpowers/plans/2026-08-16-pwa-install.md`). Scoped to
installability only, no offline/service worker — the user's actual use
case is iOS "Add to Home Screen", which reads the manifest directly and
needs no service worker (unlike Chrome's desktop/Android omnibox install
button, explicitly deferred). Icons reuse the existing `favicon.svg`
brand mark rendered once to static PNGs (192/512/apple-touch-icon),
committed rather than added as an ongoing build dependency. Manifest
(`frontend/public/manifest.webmanifest`) uses relative `start_url`/
`scope`/icon `src` values so it resolves correctly under both local dev
(`/`) and the production `VITE_BASE=/reader/` path prefix with no
build-time templating — verified by building with `VITE_BASE=/reader/`
and confirming every path in `dist/index.html` picked up the `/reader/`
prefix via Vite's existing `base` rewriting (same mechanism the prior
`favicon.svg` link already relied on). Added the iOS-specific meta tags
(`apple-mobile-web-app-capable`, `-status-bar-style`, `-title`) and a
`theme-color` meta that now also updates live when the in-app dark/light
toggle fires, via the existing theme `useEffect` in `App.tsx`.

## 2026-08-17/18 — Scroll-reset bugs, and a multi-round chase on the iOS PWA bottom-edge issue

**Scroll position not resetting**: two real bugs, reported together —
switching between "All items"/"Unread"/"Starred"/a source or folder kept
whatever scroll position the previous list was at, and toggling
Full/Summary within the same open article did the same. Root cause in
both cases: the scrolling DOM element (`.article-list`, `.reader-scroll`)
stays mounted across these transitions — only its *content* changes — so
nothing ever told the browser to move it back to the top.
`ArticleReader.tsx` already had a per-article scroll-reset `useEffect`
keyed on `article.id`; added `viewMode` to its dependency array so
toggling Full/Summary resets scroll the same way switching articles
already did. `ArticleList.tsx` had no equivalent at all — added a
`listKey` prop (`App.tsx`'s existing `selectionToQueryValue(selection)`,
already unique per view/source/folder) plus a ref + effect mirroring the
reader's pattern. Verified live with the local dev server + real DB data
(175 articles) via browser automation: scrolling the "All items" list,
switching to "Unread", confirming `scrollTop` snapped back to 0.

**Then a detour that turned out to be based on a misread report.** A
screenshot of a DOOMBERG article in summary mode showed the title
partially hidden under the reader bar at the top and text running into a
darker strip at the bottom. Read as "scroll isn't resetting on the
Full/Summary toggle, even after the fix above" — user later corrected
this: *"I am not talking about the reset scroll state. That was working
fine."* Applied a double-`requestAnimationFrame` defer to both scroll-reset
effects anyway, reasoning from a real (but here misdiagnosed) class of
iOS WebKit bug — in-flight momentum-scroll or uncommitted layout
overriding a synchronous `scrollTo()`. Once corrected, reverted that
commit outright (`git revert`, not a rewrite — both the double-rAF commit
and this revert had already been pushed and deployed). **Lesson: when a
report says "still happening," confirm which specific behavior before
re-diagnosing** — the double-rAF change was harmless but solved a problem
that didn't exist while the actual one went unaddressed for another round
trip.

**The actual bottom-of-screen issue**, once correctly scoped: in
standalone (Add to Home Screen) mode on iOS specifically — not a regular
Safari tab — content can render under the home indicator, which iOS
draws its own translucent scrim over. Went through three iterations
before landing on what the user wanted:
1. First pass (this file's entry above, 2026-08-16): added
   `env(safe-area-inset-bottom)` padding to `.reader-article`,
   `.article-list`, `.settings-pane`, `.feed-picker__list`, and
   `.config-drawer__footer`, reasoning that text should never sit under
   the scrim.
2. Feedback after seeing it live: *"it will cut off texts instead of
   letting it go to the bottom edge"* — the buffer was too generous; it
   stopped content well short of the true edge, which read as content
   being truncated rather than reaching it. Reduced the four reading-
   content spots back to their pre-safe-area-fix padding, keeping only
   `.config-drawer__footer`'s (a distinct concern — Cancel/Save tap-target
   safety under Apple HIG, not reading flow).
3. Immediate feedback: *"This is worse. revert the change"* — reverted
   step 2 (`git revert`), landing back on step 1's padding. Confirmed as
   the right call afterward: *"the change is actually fine."*

Net effect on `.reader-article`/`.article-list`/`.settings-pane`/
`.feed-picker__list`: the `env(safe-area-inset-bottom)` padding from the
2026-08-16 entry stands as originally shipped. Every step in this thread
was pushed and deployed live via `scripts/deploy.sh` rather than staged
first — each `git revert` (never a history rewrite, since origin/main
had already moved) undid a deployed commit with its own deploy
immediately after, so production tracked the same back-and-forth as this
log.

## 2026-08-18 — Standalone PWA: same bottom-edge saga, but from the top this time

Continuation of the entry above, but this round was actually diagnosed
before touching CSS (`superpowers:systematic-debugging`'s "3+ failed
fixes means question the architecture, not the parameters" — five prior
attempts had all tweaked `env(safe-area-inset-bottom)` padding and none
stuck). Sent four fresh iPhone screenshots (installed PWA vs. Chrome
iOS, list view and article view) and measured pixel colors down each one
instead of eyeballing.

**Root cause, this time confirmed by measurement, not guessing:** in
standalone mode on a 393×853pt iPhone, real content reaches all the way
to y=797.5pt — a **55.5pt band at the physical bottom edge is `body`'s
background color**, painted by iOS entirely outside the layout viewport.
`.sidebar` and `.reader-overlay` are both `position: fixed; inset: 0`,
but neither can put a pixel in that band — it isn't part of the DOM's
paintable area at all. That's why every previous `env(safe-area-inset-
bottom)` padding attempt either did nothing (band still visible above
the padding) or ate into content for no visual benefit (the "cuts off
text" complaint) — the band was never reachable by any padding value.
Corroborated by WebKit bug
[#301994](https://bugs.webkit.org/show_bug.cgi?id=301994) (system-
painted bands no DOM element can reach in standalone web apps),
reopened 2026-08-04 against iOS 26.5.2/27 beta.

**The fix:** stop trying to paint the band and make it invisible by
color-matching instead. `html`/`body` now carry an explicit `background`
(previously only `body` did — a transparent `html` is what produces
stray white/black bands) that tracks which full-screen surface is
currently open via a `document.documentElement.dataset.surface` write in
`App.tsx` (`'reader'` when `openArticleId` is set, else `'list'`),
switching between `--bg` and `--bg-overlay`. Also dropped the
`env(safe-area-inset-bottom)` padding on `.reader-article`, `.article-
list`, `.settings-pane`, and `.feed-picker__list` per the same
reasoning — kept it only on `.config-drawer__footer` and `.toast`, which
guard interactive tap targets rather than reading flow. `.login-page`
picked up the same `100vh`/`100dvh` fallback pair `.shell` already had.

**First pass also pushed the sidebar drawer and reader bar down by
`env(safe-area-inset-top)` in a new `display-mode: standalone` media
query**, reasoning from the original ask ("settings overlay color
shouldn't bleed into the top bar"). Verified live via `scripts/serve.sh`
+ deploy, confirmed working. Immediate follow-up reversed that specific
part: *"let's revert back to original state... when the drawer comes
from the left, the background color of the drawer should blend onto the
top status bar too."* Removed the new media query and the mobile
`.sidebar` background override that had flattened the drawer's own
`--bg-raised` down to page `--bg` — both were needed only to support the
top-inset behavior that got reversed. Net: the bottom-edge fix stands
as-is; the top status bar blends with whichever full-screen surface is
open, same as before this round started.

Both rounds deployed live via `scripts/deploy.sh` (typecheck + 221
backend tests + build, gate before every push).

## 2026-08-19 — Standalone PWA bottom gap after resume: measured, six JS fixes failed, root cause NOT confirmed

New symptom, easy to mistake for a repeat of the two entries above: kill-and-relaunch
doesn't reproduce it, but after the installed PWA sits backgrounded for a while and you
return to it, content stops short of the bottom edge by a blank strip — in *both* the feed
list and the article reader. Rotating to landscape and back fixes it until the next
occurrence.

**Measured live via Safari Web Inspector attached to the installed PWA** (iPhone, 440×956pt,
`__snap()` probe reading `window.innerHeight`, `visualViewport.height`, a
`position:fixed;inset:0` probe div, `.shell`/`.reader-overlay`'s own rendered height, and
`env(safe-area-inset-*)` exposed via a custom property so JS could read it):

```
buggy      innerH 894  vvH 894  fixedInsetH 894  surfaceH 893.98  screenH 956  safeTop 62px  safeBottom 34px
after-rot  innerH 956  vvH 956  fixedInsetH 956  surfaceH 956     screenH 956  safeTop 62px  safeBottom 34px
```

**The arithmetic is exact: 956 − 62 = 894.** The layout viewport loses precisely
`env(safe-area-inset-top)` — while still *reporting* that inset as 62px. That reads as a
fingerprint (the web view's native frame computed once as if inset below the status bar,
while its safe-area insets are kept as if it weren't), but **treat this as a hypothesis, not
a confirmed mechanism** — it rests on a single occurrence on a single device. "Origin stays at
y=0" in particular is an inference from screenshots, not something JS can read directly; if the
origin actually sits at y=62 instead, the geometry is "content pushed down from the top"
rather than "gap at the bottom," which is a different bug with a different fix shape. Not
ruled out.

Confirms this is a genuinely different bug from the one two entries up: that one (the
always-present ~55.5pt unreachable band) is unaffected by resume/rotation and was fixed by
color-matching. This one is resume-triggered and rotation-clearable — a stale **native**
value, not a paint-reach problem.

**Every position:fixed and every 100dvh surface failed together** — that's what rules out a
CSS-unit bug (swap `dvh` for `fixed`, etc.) before wasting a round on it. `.reader-overlay`
is already `position: fixed; inset: 0` and it was short too.

**`screen.width`/`screen.height` stayed correct (956) through every single buggy capture.**
Only `window.innerHeight` (and everything derived from it — `visualViewport`, `dvh`,
`position:fixed` boxes) went stale. That's a fair diagnostic for "native frame is wrong," as
opposed to "CSS unit is wrong" — screen dimensions come from a different, unaffected code
path. (They're not usable as a fix, though — content can't paint outside the real layout
viewport, so sizing `.shell` to 956px just gets the extra 62px silently clipped, invisible.
Confirmed this the hard way by nearly proposing it before working the constraint through.)

**Unresolved contradiction, flagged by a critical-review pass and not chased down live:** in
the post-rotation "fixed" snapshot, `innerHeight` read 956 but
`document.documentElement.clientHeight` stayed at **894** — never moved, in either state. Per
CSSOM-View, `clientHeight` on the root element *is* the layout viewport height, same quantity
`position:fixed;inset:0` sizes against. So the post-rotation snapshot has the layout viewport
reading both 894 (`clientHeight`) and 956 (`innerHeight`, and the `fixed` probe) at once —
which can't both be true. Two live possibilities, not distinguished:
1. The measurement methodology has a bug (the `__snap()` probe was re-injected mid-session
   over an existing page rather than defined from first load; a stale probe element or closure
   surviving a reload could explain a value that never updates), or
2. The layout viewport was 894 throughout and rotation only changed *painting*, not the
   viewport itself — which would mean the "native frame caching" mechanism above is wrong.

This was dismissed in the original draft of this entry as "a red herring — not what any of the
app's layout depends on." That dismissal was itself a mistake: the value's job was as a
consistency check on the stated model, and it failed the check. Recorded here so a future
session re-runs the probe from first page load (not mid-session re-injection) before trusting
this mechanism further.

**Six independent JS-side attempts to force WebKit to recompute the native frame, all
measured, all failed** (stayed at 894 after every one):

1. `<meta name="viewport">` content toggle + double rAF
2. `documentElement.style.zoom` nudge + double rAF — note `frontend/index.html`'s viewport tag
   sets `maximum-scale=1.0`, which may have blunted this attempt specifically; not re-tested
   without that constraint
3. Waiting 1s / 3s / 8s — never self-corrected in that window
4. Focus/blur a hidden `<input>` to trigger a keyboard show/hide cycle (different native
   inset code path than anything else tried)
5. Toggling `apple-mobile-web-app-status-bar-style` between `black-translucent` and `default`
   — the exact setting governing under-status-bar extension, i.e. the exact 62pt being lost
6. `display: none` → synchronous `offsetHeight` read → `display: ''` (forced sync reflow) on
   `.shell`/`.reader-overlay` directly — a technique that *does* fix a superficially similar
   bug (see below), but not this one

All six probe the same lever — forcing WebKit to re-run CSS layout — which then re-reads
whatever native size is cached. That's one experiment repeated six ways, not six independent
lines of evidence; treat "no JS fix found" as provisional, not proven. Real gaps in how these
were run, surfaced by a critical-review pass:
- Every attempt ran **in sequence, in one already-broken session**, with no re-run to check
  repeatability and no variation in order. Attempts #1 and #5 both mutate persistent
  `<meta>` state; #2–#6 ran against whatever state those left behind, not a clean baseline.
- **Safari Web Inspector was attached throughout.** Attaching devtools to a home-screen web
  app requires foregrounding it and can alter suspend/throttle behavior — and the bug's
  trigger is specifically backgrounding duration. The debugging tool sits on the causal path
  and this was never controlled for.

**Untried categories** (not just untried parameter values — genuinely different levers):
`pageshow` with `event.persisted`, `pagehide`, `document.wasDiscarded`, the Page Lifecycle
API's `freeze`/`resume`; logging `visualViewport`'s own `resize`/`scroll` events across a
*real* backgrounding cycle instead of synthetic pokes after the fact (this alone would answer
whether iOS ever fires a resize signal on resume — the single most diagnostic thing not yet
checked); running the detection probe from **first page load** rather than re-injecting it
mid-session (also fixes the stale-probe risk noted above); and two concrete candidate fixes
found during research but never tried: `html { min-height: calc(100% + env(safe-area-inset-top)) }`
(from an Apple Developer Forums thread, see below) and the dev.to heal function fired on
`visibilitychange → visible` instead of on blur.

**Only a physical device rotation cleared it** in what was tested. That's consistent with
"rotation invalidates a cached native frame" but doesn't prove it — see the `clientHeight`
contradiction above, which this same rotation test surfaced and left unresolved.

**Web research citations — corrected after a critical-review pass fact-checked each source
directly. The original version of this entry overstated the match on all but one:**

- **WebKit [#301994](https://bugs.webkit.org/show_bug.cgi?id=301994) does NOT describe this
  symptom** — checked directly against the actual bug report, not just the number carried
  forward from the `4e1438f` entry above. Its actual title is "REGRESSION (iOS 26.1): Status
  bar remains visible in fullscreen mode in Home Screen Web apps": the top status bar stays
  *visible* and pushes content *down*, with `env(safe-area-inset-top)` dropping to **0**. This
  session measured `safeTop: 62px` throughout — the opposite signature. No resume trigger and
  no rotation-clears-it behavior is documented in that bug. `4e1438f`'s WORKLOG entry glossed
  #301994 as "unreachable bands"; this entry originally glossed the same number as
  "backgrounding/rotation layout loss." Neither matches the tracker. Lesson: a bug number
  carried forward in repo context is not verification — re-read the actual report every time
  before citing it, especially when the symptom differs from the round that first cited it.
  (#301994 is real and reopened 2026-08-04 against iOS 27 beta — that date checks out — it's
  just the wrong bug for this symptom.)
- **The MacRumors thread** ("[iOS 26.1 PWA full screen broken](https://forums.macrumors.com/threads/ios-26-1-pwa-full-screen-broken.2470545/)")
  is real and matches #301994's top-bar symptom, not this one — landscape renders full screen
  correctly while portrait doesn't, the reverse shape from "rotation fixes it." Fixed in iOS
  26.2. The original entry's claim that "reporters found no effective JS/CSS workaround" is
  not supported by the thread content — that was an inference written as if it were a cited
  finding. Retracted.
- **The `openclaw/openclaw#76505` citation contained two factual errors, now retracted.** The
  issue is filed against **iOS 18.x**; "2026.5.2" in that issue is the *OpenClaw application's
  own version number*, not an iOS build — it was misread as matching `4e1438f`'s "iOS
  26.5.2" note, manufacturing a version overlap that doesn't exist. The issue itself is an
  unrelated, plain missing-`safe-area-inset-bottom` layout bug (toolbar behind the home
  indicator), not resume- or rotation-related. The claimed quote — "their landed conclusion
  was the same as ours: use Safari instead of the installed PWA until Apple ships one" — does
  not appear in that issue and should not have been attributed to it.
- **The dev.to writeup was accurately characterized.** Keyboard-driven, permanent shrink until
  force-quit, `display` toggle forcing a sync reflow genuinely fixes *that* bug. Correctly
  distinguished from this one (that mechanism is obscured-insets residue that reflow clears;
  ours didn't respond to the identical trick, attempt #6 above). Worth noting the article
  reports a 59pt drop on a Pro Max — also exactly that device's top inset — which is a second
  data point for the "shortfall == top inset" pattern that wasn't used as such.
- **A closer match, found only during the critical-review pass and missed in the original
  research:** [Apple Developer Forums #744327, "iOS17 PWA `position: fixed` element breaks
  after a while"](https://developer.apple.com/forums/thread/744327) — backgrounding-triggered
  (~1hr backgrounded plus active use), `position: fixed` elements misplaced as if an invisible
  bar is pushing the viewport, aggravated by repeatedly opening/closing overlays (this app
  opens/closes `.reader-overlay` constantly), unresolved as of Dec 2024, independently
  confirmed by a second developer. This is on **iOS 17** — two major versions before the
  iOS-26.1 regression cited above — which is the strongest reason to doubt "this is a fresh
  iOS 26 regression Apple is already fixing." If the same bug family predates iOS 26 this
  much, it's more likely a longstanding WKWebView standalone-mode issue than something with a
  point-release fix already in flight.

**Net effect of the correction:** the "some real native-frame-related iOS/WebKit bug" framing
is still plausible, but nothing found actually confirms which known bug this is, whether it's
version-specific, or whether it's currently being fixed by Apple. The original "confirmed
Apple bug, just wait for the update" framing was not earned by the evidence.

**Decision: no fix implemented.** A padding-reduction mitigation (shrink the reader's own
bottom padding when the bug fingerprint is detected, so the app's own spacing doesn't stack
on top of iOS's unavoidable dead strip) was built, typechecked, and built successfully, then
explicitly reverted at the user's request — the gap isn't severe enough to carry permanent
detection code and a CSS branch, independent of how the root cause shakes out.

**If this resurfaces, in priority order:**
1. **Don't re-cite #301994 for this symptom** — it's the wrong bug (see above). Check Apple
   Developer Forums #744327 for updates instead; it's the closer match and still unresolved as
   of this writing.
2. **Get a real fingerprint before trying anything else.** Ship a temporary, dev-only passive
   logger from **first page load** (not mid-session re-injection) that records to
   `localStorage` on `visibilitychange`, `pageshow` (with `event.persisted`), `resize`, and
   `visualViewport.resize` — capturing `innerHeight`, `visualViewport.height/offsetTop`,
   `documentElement.clientHeight`, `screen.height`, both safe-area insets, and a timestamp.
   One background/resume cycle from the user closes the `clientHeight` contradiction above,
   turns n=1 into a real sample, and answers whether *any* event fires on resume at all —
   without a Web Inspector attached to muddy the backgrounding behavior.
3. Only then try the two untried candidates: `html { min-height: calc(100% + env(safe-area-inset-top)) }`
   (from Apple Forums #744327) and the dev.to heal function fired on `visibilitychange →
   visible` rather than on blur.
4. The padding-mitigation approach (not implemented, reasoning preserved above) or a native
   wrapper (Capacitor/Swift `WKWebView` shell, to hook `viewSafeAreaInsetsDidChange`/
   `viewWillTransition` directly) remain fallback options if 1–3 don't produce a real fix —
   the wrapper is untested and not obviously better, since it still delegates web view sizing
   to the same WebKit code.

## 2026-08-31 — Multi-user: two accounts, per-account read/starred state

Request: *"allow multiple people to use the openreader without affecting each other. Each
person should be able to sign in with their own account and their own read state/star state
tracking... the feed config is still one version that's shared (only me and my wife will read
this). I should still keep existing account and progress. Create a separate account for my
wife with the same password as me (you can copy the hash)."*

So: two accounts (the primary account, the second account), separate read and starred state, everything else shared.
Existing production state — 209 articles, 48 unread, 0 starred — migrates onto the primary account;
the second account starts with the whole backlog unread.

**Schema.** `users` (id, username, bcrypt hash, timestamps) and `article_states`
(user_id, article_id, is_read, read_at, is_starred), the latter `WITHOUT ROWID` since the
composite PK is the entire table. `articles.is_read/read_at/is_starred` are gone.

The design decision everything else follows from: **a missing `article_states` row means
unread and unstarred.** That's what makes "add an account and they see the existing backlog
as unread" a zero-write operation rather than a 209-row backfill, and keeps the table
proportional to what people touch rather than accounts × articles. The cost, named honestly:
*unread is no longer indexable*, because it's partly the absence of a row. Three indexes
(`idx_articles_unread`, `idx_articles_starred`, `idx_articles_src_unread`) had nothing left
to index and were dropped; reads now drive from `articles` ordered by `idx_articles_pub` and
probe state by primary key per row. At 209 articles that's free and stays fine into the tens
of thousands. Escape hatch if that ever changes is documented in ERD §3 (denormalize
`source_id` into `article_states`). The starred view is the one exception and inner-joins,
driving off `idx_article_states_starred` over one account's handful of rows.

**The migration gotcha, worth remembering.** `ALTER TABLE ... DROP COLUMN` fails if *any*
index references the column — and it raises `error in index ... after drop column`, which is
**not** the `no such column` string that `_drop_column_if_present` swallows. Dropping the
three indexes first isn't tidiness; without it `init_schema` raises on startup against every
existing deployment. The backfill is an `ON CONFLICT DO NOTHING` upsert and detection is "do
the legacy columns still exist", so a crash anywhere in the middle just re-runs harmlessly on
the next boot. Verified against a copy of the live DB before deploying: the primary account's per-source
unread counts came back identical to the pre-migration baseline
(`[(1,26),(2,5),(4,1),(5,6),(6,3),(8,3),(12,1),(13,1),(14,1),(16,1)]`, 48 total), the second account got
209 unread / 0 starred.

**Auth.** The session cookie payload became `"<user_id>:<expiry>"` with the HMAC over both, so
a valid cookie can't be edited into another account's session. Old-format cookies
(`"<expiry>"`, no identity) are **rejected** rather than mapped to account 1 — honoring them
would make the only thing separating the accounts bypassable by holding a stale cookie. Cost
is one extra login each, once.

Passwords moved from `READER_AUTH_PASSWORD_HASH` into `users.password_hash`, which forced a
real decision: **that env var can no longer be part of deciding whether login is required.**
It's consumed once as a first-run seed and then dead, so gating on it would mean deleting the
now-useless line from `openreader.env` silently unlocks an internet-facing deployment. Auth
now keys off `READER_SESSION_SECRET` alone. That does delete the old "exactly one of the two
set fails closed" safety net — replaced with a loud stderr warning in `build_production_app()`
when a multi-account DB starts with no secret. Warn, not refuse: running a copy of the
production DB locally with no secret is routine.

Deliberately *not* built: an HTTP password-change endpoint (two accounts, SSH access, a
CSRF-sensitive write path for something done roughly never — `app/useradm.py` handles it), and
a `users.session_epoch` column to invalidate sessions on password change (puts a DB read on a
deliberately stateless verification path, to defend a threat model a household reader doesn't
have; rotating the session secret already nukes everything).

**Account bootstrap.** The first draft seeded both accounts from a `READER_BOOTSTRAP_USERS`
env var on first startup. Rejected before writing it: a forgotten env-file edit before the
restart would migrate the entire production read history onto an account named `reader`,
recoverable only by hand-editing the DB, and the failure is silent. Instead `init_schema`
guarantees exactly one account exists (`reader`, fresh-DB only) and the real accounts are
created explicitly with `python -m app.useradm`, where a typo is visible immediately.
`useradm` lives in `backend/app/` rather than `scripts/` for an unglamorous reason: `deploy.sh`
rsyncs only `backend/app`, so a `scripts/` file would be unrunnable exactly where accounts get
created.

**Frontend.** Username field on the login form (`autoComplete="username"` alongside
`current-password` is what makes Chrome store credentials *per account* rather than one blob
for the site), a `['me']` query whose 401 is now the single "show the login screen" signal, and
an identity row in the sidebar footer. The subtle part is `resetForIdentityChange`, fired on
both login and logout: `qc.clear()` rather than `invalidateQueries()`, because the cache holds
the previous account's unread counts and read/starred flags and would render them before
refetching — plus a reset of `selection`/`openArticleId`, which are plain React state that
survives the login-screen swap and would otherwise land the next person on the previous one's
open article. `sessionStorage['reader-view-state']` is wiped rather than namespaced per
account: reaching a second identity in one tab must go through that reset anyway, and
namespacing would force the restore-on-reload read (a `useState` initializer) to wait on an
async `['me']` round trip and visibly flash the default view first. Theme stays global — that's
a property of the device and the light you're reading in, not of who's signed in.

**`reconcile_read_state`** now sweeps for every account (being out of config scope is a
property of the config, not of a person) but still returns the count of distinct *articles*,
not `(user, article)` rows, so the `reconciled` number in API responses doesn't double with
the account count.

247 backend tests (was 212), including a new `test_store.py` covering isolation in both
directions, migration tests against a synthetic legacy DB (including the interrupted-migration
resume path), and two-account end-to-end API tests running the real login flow. Verified live
in a browser: signed in as the second account (185 unread, config-filtered), logged out, signed in as
the primary account (48 unread, per-source counts all different, no stale rows).

## 2026-09-05 — Contain VoiceOver to the reader overlay

Reported bug: opening an article in the installed iOS PWA and swiping with VoiceOver read
content that wasn't visible on screen. Cause: `ArticleReader` renders a
`position: fixed; inset: 0` overlay (`index.css`'s `.reader-overlay`) as a *sibling* of the
sidebar and article list inside `.shell`, not a replacement for them — both stayed fully
mounted and unmarked underneath it, so VO's swipe/rotor navigation walked straight past the
overlay into the list and sidebar behind it. The codebase had essentially no accessibility
attributes before this (three `aria-hidden`s, two `aria-label`s, zero `.focus()` calls).

Fix, scoped deliberately to just the reader (not the off-canvas mobile sidebar, article-row
semantics, icon-button labels, or live regions — separate work):

- `App.tsx` sets `inert` on `.main` and passes it into `Sidebar` whenever the reader is open
  (`readerOpen = openArticleId !== null`). React 19's boolean `inert` prop maps straight to
  the DOM property — no string-attribute workaround needed — and it's what actually pulls
  the background out of the accessibility tree (iOS Safari 15.5+).
- `ArticleReader`'s root gets `role="dialog"`, `aria-modal="true"`, `aria-labelledby`
  pointing at the article `<h1>` (via `useId`), and `tabIndex={-1}` so it's a legitimate
  focus target — `inert` hides the background, this is what makes VO treat the overlay as
  its own self-contained unit rather than just another chunk of page.
- Focus moves onto the overlay itself on open and on every prev/next article change (same
  effect that already reset scroll position on `article.id` change), and back onto the
  specific article row on close (`ArticleList` rows take `tabIndex={-1}` only for the
  currently-selected row, so there's a legitimate script-focusable target — not reachable by
  Tab, since rows otherwise stay plain non-interactive divs per the reader-only scope).
  Without this, both open and close left VO/keyboard focus stranded on a now-inert or
  now-unmounted element.

Verified: `tsc -b` + `vite build` clean, `oxlint` shows only the one pre-existing
`exhaustive-deps` warning (confirmed present before this change too, unrelated to it — the
llm-summary auto-switch effect). Checked in Chrome DevTools' full accessibility tree that the
sidebar/article-list subtrees disappear while the reader is open and reappear on close, and
that Tab-cycling can't reach a background control either.

**Follow-up same day:** live device testing showed VoiceOver still started on the toolbar
(Close, Pull full article, Summarize, Star, Open original) before the article — containing
the *background* wasn't the whole problem. Root cause: initial focus targeted the overlay
`<div>` itself, whose first descendant in the DOM is `.reader-bar`, so a screen reader's
forward navigation (which reads on from wherever focus currently sits) hit the toolbar
first. Fix: focus now lands on the article `<h1>` (`ref`+`tabIndex={-1}`, still the
`aria-labelledby` target) instead of the overlay container, so forward reads start at the
title and go straight into the meta line and body; the toolbar is still reachable by
swiping backward. Removed the now-unused ref/`tabIndex={-1}` from the overlay div itself.
Verified live against the real feed: `document.activeElement` after opening an article is
the `<h1 class="reader-article__title">`, not the overlay or toolbar.

**Second follow-up same day:** still reported reading feed-item titles with the reader open.
Root cause this time: `inert` isn't a reliable guarantee on iOS at all — this is a
documented Safari 26 regression
([discussions.apple.com/thread/256161078](https://discussions.apple.com/thread/256161078)):
VoiceOver's virtual cursor can keep navigating into `inert` content, and when focus is lost
it falls back to whatever's nearest by screen position to where it last was — an
inert-but-still-present article row is exactly that kind of candidate. `aria-hidden` has
the same class of long-standing WebKit bugs
([bugs.webkit.org/show_bug.cgi?id=201887](https://bugs.webkit.org/show_bug.cgi?id=201887)),
so it's not a safe substitute either. The only guarantee that doesn't depend on WebKit
computing an "ignored" AX flag correctly is not having the nodes in the DOM at all.

Fix: `Sidebar` and `ArticleList` now take the reader-open state and render their actual
content as nothing (`Sidebar`'s brand/nav-and-folders/footer sections; a new `hidden` prop
on `ArticleList`) instead of just marking it `inert` — `inert` is kept alongside as
defense-in-depth for browsers where it behaves correctly and to block pointer/keyboard
interaction, but the content itself is gone, so there's nothing left for any AX-tree bug to
expose. Verified: `document.querySelectorAll('[data-article-id]')` and `.nav-row` both
return zero while the reader is open, and `#article-list`/`#sidebar-scroll`'s own children
count is 0.

That surfaced a real regression of its own: emptying a scroll container clamps its own
`scrollTop` to 0 (there's nothing left to scroll to), and it does not restore on its own once
content comes back — confirmed live (scrolled the list to 400, opened and closed an article,
came back at 0). `App.tsx` now explicitly saves `#article-list` and `#sidebar-scroll`'s
`scrollTop` in `openArticle` (the one place every "open an article" path funnels through —
both a row click and the `j`/`k` + `o`/Enter keyboard shortcut) and writes it back in
`closeArticle`'s existing focus-restoring `requestAnimationFrame`. Verified live via both
paths: scrolled the list, opened via click, closed, back at the exact same `scrollTop`;
repeated via `j` then `o`, same result. Also confirmed via the Network panel that closing
never re-fetches `/api/articles` or `/api/sources` — the list is React Query cache in
`App.tsx`, untouched by `ArticleList` unmounting its rows, so there's no data reload on close
even before this scroll-position fix, only the (now-fixed) visual scroll reset.

## 2026-09-06 — Shared Apache OIDC gateway live; app code not yet deployed

The consolidated-login Apache gateway (see 2026-09-05 entry above, and pchauth's spec/plan
in the `pchauth` repo) went live on `wordpress-2-vm` today. `wordpress-https.conf`'s
`<Location ${READER_PREFIX}/>` block gained `AuthType openid-connect`, `Require all
granted`, and `RequestHeader unset`/`set X-Remote-Email` — this repo's inline block, not a
separate fragment (OpenReader's Apache config still isn't backported to its own repo; see
the still-open follow-up from 2026-08-13). Backed up first
(`wordpress-https.conf.bak-pre-oidc-20260906`).

**Important: this repo's `feat/consolidated-login` branch (the actual app code —
`pchauth`-backed `PchauthMiddleware`, `current_user_id`/`require_user_id` split,
signed-out browsing UI) has NOT been deployed to the VM yet.** The gateway change alone
is live; `/reader/` currently passes through Apache unauthenticated into the *old*,
still-running app code, which still enforces its own bcrypt/session-cookie login and
knows nothing about `X-Remote-Email`. A header-spoofing check run against
`/reader/api/me` today returned 401 for a forged header, but that's the *old* app's own
auth rejecting an unauthenticated request generally — it does not yet prove the new
scrubbing behavior (`RequestHeader unset` before `set`) actually works, since the
deployed code doesn't read that header at all. Re-verify once this branch is deployed.

Two real Apache-layer bugs were found and fixed during the gateway rollout — full
incident in `01-projects/personal-brand/wordpress-vm-pages-setup.md`'s "Consolidated
login" section:
1. `OIDCCacheShmMax` has an enforced minimum of 128, not the `20` the original design
   spec assumed — `apache2ctl configtest` caught it immediately.
2. `OIDCUnAuthAction pass`, set vhost-wide so `/reader/` and `/summrabook/` never block an
   anonymous request, was initially inherited by `/pages/` too, briefly serving it with no
   gate at all — fixed with a `/pages/`-local `OIDCUnAuthAction auth` override. Doesn't
   affect OpenReader directly, but is why the vhost now has that override present.

**Still needed before this branch deploys:** link the 2 real accounts to
`pychen007@gmail.com`/`cassyheng@gmail.com` via `useradm link` (need their current
usernames from the live DB first — not yet looked up), so the first Google login attaches
to the existing rows instead of creating new ones.

## 2026-09-06 (later same day) — App code deployed, full stack verified

Deployed via the usual `scripts/deploy.sh`. Set `READER_AUTH_MODE=optional` and
`READER_ALLOWED_EMAILS=pychen007@gmail.com,cassyheng@gmail.com` in `openreader.env`
alongside the deploy (backed up first, `openreader.env.bak-pre-oidc-20260906`) so there was
no window where the app ran with neither the old bcrypt auth nor the new trusted_header
auth active.

Linked both real accounts immediately after: `useradm link pengyao pychen007@gmail.com`,
`useradm link heng cassyheng@gmail.com`. `article_states` history confirmed intact via
`useradm list` before/after (437 read / 1 starred for pengyao, 66 read for heng — unchanged
except pengyao's own read count from verification traffic).

Verified live: anonymous `GET /api/sources` and `/api/me` succeed (200, `id: null`);
a forged `X-Remote-Email: attacker@evil.com` sent from outside still resolves to `id: null`
— proof the Apache `RequestHeader unset` scrub actually works now that this app reads the
header (the 2026-09-06 morning check only proved the *old* app's unrelated auth rejected a
forged header, not the new scrubbing behavior); `POST /api/refresh` 401s anonymously as
expected.

Deferred to a later pass, now that the deploy is confirmed working: deleting the
bcrypt/session-cookie rollback code in `app/auth.py` and `useradm`'s
`add`/`set-password`/`copy-password` subcommands (Task C4 Step 4), and merging this branch
to `main`.

## 2026-09-06 (later still) — Real root cause found: Require all granted silently skipped auth

The first real human login (via `/pages/`) exposed the actual bug behind "still shows
signed out": Apache's `<Location /reader/>` used `Require all granted`, which is a
documented Apache 2.4 core optimization — if authorization succeeds unconditionally
regardless of identity, Apache's core skips invoking any authentication module at all,
including `mod_auth_openidc`. So `X-Remote-Email` was never injected even for a
genuinely signed-in session; nothing in this app was ever broken. Two earlier real bugs
found along the way (the `/oidc/callback` 404, and the default shm session cache getting
wiped on every Apache reload) were also real and are documented in the vault note, but
neither was sufficient to explain the symptom on its own.

Fix (Apache-side only, this repo's code needed no change): `Require valid-user` instead
of `Require all granted`. `OIDCUnAuthAction pass` (vhost-wide) is what still lets an
anonymous, no-session request through untouched — confirmed this is a separate mechanism
from `Require`'s own evaluation.

**Fully verified live, in a real browser, real Google account:** signed in once via
`/pages/`, `/reader/api/me` correctly returned the real identity (`{"id":1,"username":
"pengyao",...}`), personalized state rendered (192 unread, pengyao's actual read
history) instead of the anonymous/default view, and — the actual point of this whole
project — landed already-signed-in on `/summrabook/` too with zero further login
prompts. Anonymous access, write-gating, and header-spoofing scrubbing all re-confirmed
unaffected by this fix.

Full incident write-up: `01-projects/personal-brand/wordpress-vm-pages-setup.md`,
"Consolidated login" sections (2026-09-06). This closes the item deferred in the previous
entry — the deploy is now genuinely, completely verified, not just anonymous-path-verified.

## 2026-09-06 (yet later) — Removed the dead "Sign in with Google" link; added a public-demo banner instead

Separately from the SSO-plumbing fixes above: a user report that `/reader`'s sidebar
"Sign in with Google" link just redirected to the pengyaochen.com homepage. Root cause:
`frontend/src/components/Sidebar.tsx` rendered `<a href="/" className="sidebar__signin-link">`
for the anonymous case — a plain anchor to ungated site root, not a link into any gated
pengyaochen.com path that would actually trigger Apache's OIDC redirect. There was never any
click handler or OAuth code behind it (confirmed nothing in the frontend bundle references
Google/OAuth/client_id) — this wasn't a regression, the link was never finished.

Product decision (not just a bug fix): OpenReader doesn't need its own sign-in UI at all.
`optional` auth mode already gives silent cross-app SSO for allowed emails via the shared
gateway (verified end-to-end in the entry above) when signed in at `/pages/`; there's no
"local" sign-in this app could offer beyond that. So the fix removes the link entirely rather
than pointing it at a working gated URL.

For the anonymous case specifically, OpenReader's public deployment is meant to be browsed
directly (e.g. by a recruiter looking at this project), so replaced the dead link with a
friendly banner: "👋 You're viewing a public demo of OpenReader — browse freely, sign-in isn't
required." (`.readonly-banner` in `index.css`, shown in `App.tsx` when `meQuery.data?.id ==
null`). Removed the now-unused `isAnonymous` prop from `Sidebar` since its only use was the
deleted link. 252 backend tests + frontend typecheck passed; deployed via `scripts/deploy.sh`.

See Summra's equivalent entry in its own `WORK_LOG.md` (same session, same root cause, same
decision) — Summra's fix has no banner (its anonymous UX was already fine as read-only), just
a plain in-modal explanation instead of a CTA.

## 2026-09-06 (still later) — Actually lock down the read-only demo, not just remove the dead button

Follow-up from the anonymous-banner work above: the banner said "browse freely" but nothing
had actually been done to stop an anonymous visitor from *trying* mutating actions — the
Star/Summarize/Pull-full-article icons, the sidebar's Refresh feeds and Settings buttons, and
the two "mark all as read" checkmarks were all still live in the UI, even though the backend's
`require_user_id` (see the multi-user work) already 401s every one of them for a signed-out
request. That's not a security hole — nothing was actually mutable — but it's a bad UX: a
recruiter clicking Star and having nothing visibly happen (or seeing a console error) reads as
a broken app, not a demo.

Fix, in `frontend/src/App.tsx`, `Sidebar.tsx`, and `index.css`:

- **Sidebar** gets a new `readOnly` prop (`isAnonymous` from `App.tsx`). When true: the
  "Refresh feeds" and "⚙ Settings" buttons, and the per-source/per-unread-count "✓ mark all
  read" checkmarks, don't render at all — removed, not disabled, matching the instruction that
  a non-working button is worse than no button.
- **ArticleReader**'s Star/Pull-full/Summarize handlers, when anonymous, no longer call their
  mutations at all (critically: `onSummarize` never reaches the LLM call in this path) —
  instead they call a new `showNotice(message)` that renders a small `.toast` (index.css) with
  a plain-language explanation ("Starring isn't available in this read-only demo — sign in to
  save articles.", etc.), auto-dismissing after ~3s. The "Open original" arrow is untouched —
  it's a plain external link, nothing to gate.
- Automatic side effects that aren't a deliberate click — auto-mark-read on opening an article,
  the `m`/`r` keyboard shortcuts — are silently skipped for an anonymous visitor rather than
  firing a request that would just 401. No toast for these: there's no visible control to
  explain, and a popup on every article open would be its own bad UX.
- Pull-to-refresh (touch gesture, `ArticleList`'s `onRefresh`) becomes a no-op rather than
  `undefined`, since the prop type is a required `() => void` — same reasoning as above, no
  toast (a gesture has no label to attach one to).
- Banner text rewritten to actually describe what's locked down, instead of the vaguer
  "sign-in isn't required": "...This is a read-only view, so marking articles read, starring,
  summarizing, and changing feeds aren't available."

Verified: 252 backend tests + `tsc -b && vite build` clean. Deployed via `scripts/deploy.sh`.

---

## 2026-09-07 — Real "Sign in with Google" link (PWA couldn't sign in at all)

Two related login bugs reported by the user:

1. **Sessions were expiring after a few hours, not the intended ~30 days.** Root cause:
   `mod_auth_openidc` has two independent expiry knobs — `OIDCSessionInactivityTimeout`
   (idle timeout, resets on activity) and `OIDCSessionMaxDuration` (an absolute hard cap
   regardless of activity). The vhost only set the former (`2592000`, 30 days); the latter
   was silently at the module's default of **28800s (8h)**. Fixed by adding
   `OIDCSessionMaxDuration 2592000` next to the inactivity line (vault note
   `01-projects/personal-brand/wordpress-vm-pages-setup.md`, applied live on
   `wordpress-2-vm`, `apache2ctl configtest` + graceful reload — verified `/reader/` and
   `/pages/` both still correct post-reload).

2. **The PWA installed to an iOS home screen had no way to sign back in at all**, forcing a
   delete-and-reinstall each time the session expired. Cause: login is 100% delegated to
   Apache now (see the 2026-09-05 entry below), so this app has no login page of its own.
   The signed-out UI (`needsLogin` in `App.tsx`) just printed plain text — "Visit any
   pengyaochen.com path to sign in with Google" — not even a clickable link. A PWA has no
   address bar, so there was genuinely nowhere to click. (Separately: reinstalling the PWA
   "worked" only because iOS seeds a new home-screen web app's isolated storage with a
   one-time snapshot of Safari's cookies at creation time — not a real fix, just a manual
   way to force a fresh snapshot.)

   First attempt was wrong: assumed `mod_auth_openidc` supports a `?oidc_action=login`
   query-string trigger to force auth on a `pass`-mode location. It doesn't — confirmed by
   grepping strings in the installed `mod_auth_openidc.so` (2.4.17) on the VM; no such
   string exists, and a live `curl` against it returned a plain 200 from the backend, not a
   302. Corrected to the actually-documented mechanism: a dedicated, more-specific
   `<Location ${READER_PREFIX}/login>` that overrides the vhost-wide `OIDCUnAuthAction pass`
   back to `auth` for that one path only — same pattern already used for `/pages/`. Apache
   matches the longest/most-specific `<Location>` regardless of file order, so this new
   block takes precedence over `/reader/` for that path without touching `/reader/`'s own
   anonymous-passthrough behavior. `ProxyPass` targets the same backend root (there's no
   `/login` app route) — after a successful Google login, `mod_auth_openidc` redirects the
   browser back to this same `/reader/login` URL, which now proxies through with
   `X-Remote-Email` set and just renders the normal app shell (safe because `frontend/dist`'s
   asset paths are absolute `/reader/...`, not relative to the current path).

   `App.tsx` changes: both signed-out surfaces — the `required`-mode full-page message and
   the `optional`-mode anonymous read-only banner — now render a real `<a href="/reader/login">
   Sign in with Google</a>` (`.signin-link`/`.signed-out-shell` added to `index.css`, themed
   off the existing `--violet` token). Must be a real anchor (full top-level navigation), not
   a fetch/JS redirect, specifically so it works from inside a PWA's own webview.

Verified live: `curl -I https://pengyaochen.com/reader/login` returns `302` to
`accounts.google.com/o/oauth2/v2/auth`; `/reader/` still `200` anonymous passthrough;
`/pages/` still `302` (unaffected). 252 backend tests + `tsc -b && vite build` clean.
Deployed via `scripts/deploy.sh`.

Follow-up same day, in response to: "if sign in with the wrong account, user should land
back on feed page with a notice rather than a blank error page." Investigated first —
Apache's `Require valid-user` on `/reader/` and `/reader/login` has no email restriction
(only `/pages/` gets `Require claim`), so *any* Google account completes login; the actual
allowlist check is app-level (`pchauth`'s `is_allowed`, backed by `READER_ALLOWED_EMAILS`).
A disallowed-but-authenticated identity already 403s on `/api/me`/`/api/sources`, and since
every render site already defaults with `?? []`, the app doesn't actually crash — it was
falling into the same `isAnonymous` demo view as a plain logged-out visitor, which is a
*silent*, unexplained failure (not literally blank, but indistinguishable from never having
signed in — same practical problem the user was flagging).

Changes:
- **`pchauth`** (`starlette_adapter.py`, `flask_adapter.py`, both `is_allowed` 403 paths):
  echo the caller's own email in the 403 body — `{"error": "not on the allowlist", "email":
  identity.email}`. Not a disclosure (it's always the caller's own address from their own
  request header). Tests updated in both adapter suites; synced into `reader` via
  `scripts/sync-auth.sh` (32 pchauth tests, 252 reader backend tests, both green). Not yet
  synced into Summra — same fix is available there whenever it's next resynced.
- **`frontend/src/api.ts`**: new `ForbiddenError` (mirrors `UnauthorizedError`, carries
  `email`) thrown on a 403.
- **`frontend/src/App.tsx`**: new `wrongAccountEmail` derived from `meQuery`/`sourcesQuery`
  errors. When set, the top banner switches from the neutral "public demo" message to an
  explicit amber one: "Signed in as {email}, which isn't authorized for this reader...".
  Per explicit instruction, no sign-in link in this banner — it's informational only, sign-in
  itself stays in the sidebar's already-quiet corner (2026-09-07 entry above).
- **Fixed a real bug this surfaced**: the sidebar's `sign in` link (and the `required`-mode
  full-page one) pointed straight at `/reader/login`, which only works the *first* time.
  Once a wrong-account session exists, `/reader/login` would just satisfy `Require
  valid-user` with the *existing* (wrong) Apache session and never re-prompt at all — a dead
  loop back to the same rejected account. Fixed with a new `SIGNIN_URL` in `api.ts`:
  `/reader/login?logout=/reader/login` (URL-encoded) — `logout=<url>` is a real
  mod_auth_openidc query param (confirmed: appears literally in `mod_auth_openidc.so`'s
  strings, unlike the earlier `oidc_action` false start) that clears the local Apache
  session before redirecting to the given target, so hitting `/reader/login` again is
  guaranteed to trigger a *fresh* authorization request instead of reusing the stale one.
- **Apache**: added `OIDCAuthRequestParams "prompt=select_account"` (vhost-scope only —
  confirmed live that it's rejected inside `<Location>`, `AH00526`) so that fresh
  authorization request also forces Google's own account picker, rather than Google
  silently re-authenticating its own still-active session for the same wrong account. Live
  on `wordpress-2-vm`, `apache2ctl configtest` clean, graceful reload; verified `/reader/login`'s
  redirect URL now includes `&prompt=select_account`, and `/reader/`/`/pages/` unaffected.

Not fully verified end-to-end: confirming the *complete* wrong-account round trip (sign in
wrong → see the amber notice → hit sidebar "sign in" → land on Google's account picker →
pick the right account → notice clears) needs an actual second Google account to sign in
with, which wasn't done live this session. Each individual mechanism was verified
independently (`logout=` doesn't error, `prompt=select_account` appears on the redirect,
the 403 body carries the email, the frontend renders the amber banner off that email) — the
full chain is standing on real building blocks, not another `oidc_action`-style guess, but
should still get one real live run before calling it fully closed.

Verified: 252 backend + 32 pchauth tests, `tsc -b && vite build` clean. Deployed via
`scripts/deploy.sh`.

Same-day follow-up: "make sure both apps give the right error message — need to distinguish
if Google login failed or email not on allow list." The wrong-account case above already
covers the second half. The first half — the Google OAuth flow itself failing (cancelled
consent, expired/mismatched state, blocked cookies) — happens entirely inside Apache/
`mod_auth_openidc`, before any request ever reaches this app, so nothing in this repo could
fix it. Verified live by grabbing a real pending state (hit `/reader/login`, kept the
`mod_auth_openidc_state_*` cookie and `state=` value it returned) and replaying it against
`/oidc/callback?error=access_denied&...` — got a bare, technical `400 Bad Request` back. This
version of `mod_auth_openidc` (2.4.17) has no error-template directive (checked the module's
own strings — `OIDCErrorTemplate` isn't real, learned that lesson from the earlier
`oidc_action` false start above). Fixed vhost-wide instead: `ErrorDocument 400
/login-error.html` plus a small static "Sign-in didn't go through — go back and try again"
page. Confirmed `ProxyErrorOverride` is unset on the vhost, so this only intercepts errors
Apache generates itself — replaying the same disallowed-email request this app returns as
JSON (`{"error": "not on the allowlist", ...}`) confirmed it still comes back untouched, not
swallowed by the new ErrorDocument. This fix lives in the shared vhost config (vault's
`wordpress-vm-pages-setup.md`), not this repo, since it's Apache-level and applies equally to
`/summrabook/` — see `summra/WORK_LOG.md`, 2026-09-07, for that side and for the same "sign
in" entry point added there.

## 2026-09-07 (cont.) — Yesterday's fix wasn't enough: the cookie itself had no expiry

User reported being asked to sign in again shortly after a fresh login — and, critically,
*also* on `/pages/`, and in both Safari and Chrome, ruling out Safari's ITP cross-site-bounce
cookie cap as the cause (that's client-side-only and Chrome doesn't have it). Checked the
actual mod_auth_openidc session cache on the VM: 9 separate session files created across one
day (07:40, 16:23, 18:31, 20:09, 23:59, 01:13, 02:10, 02:50, 03:19), each written once and
never reused — a fresh login roughly every 1-3 hours, nowhere close to either configured
30-day timeout.

Root cause, found in `/etc/apache2/conf-available/auth_openidc.conf`'s own bundled docs:
`OIDCSessionType` defaults to bare `server-cache` (no `:persistent` suffix) when unset — which
we'd left unset. That means the *browser-side* cookie has no `Expires`/`Max-Age` at all, a
plain session cookie tied to the lifetime of the browser process itself.
`OIDCSessionMaxDuration`/`OIDCSessionInactivityTimeout` (yesterday's fix) only ever governed
the *server-side* cache entry's validity — never whether the cookie itself survives the
browser (or, far more relevant on mobile, the OS backgrounding and killing the browser app)
restarting, which happens far more often than any timeout value on iOS. Yesterday's fix was
real and correct, just not sufficient — this was always going to keep happening regardless of
how high `OIDCSessionMaxDuration` was set.

Fix: added `OIDCSessionType server-cache:persistent` to the vhost. Per the module's own docs,
the `:persistent` suffix gives the cookie a real `Expires` value (driven by
`OIDCSessionInactivityTimeout`, so 30 days) instead of a bare session cookie. Live on
`wordpress-2-vm`, `apache2ctl configtest` clean, graceful reload; `/reader/`, `/summrabook/`,
`/pages/` all still respond correctly post-reload. This is vhost-level config (vault's
`wordpress-vm-pages-setup.md`), applies to all three cohosted services identically.

Not verified end-to-end yet — confirming the cookie now actually carries a multi-day Expires
value needs a real login (I can complete the OIDC redirect chain and grab the *state* cookie
via curl, but Google's authorization code exchange can't be faked, so I can't generate a real
*session* cookie myself). Next real login should be checked for this.

Also found and fixed a real UX bug while looking at a screenshot of the above: on mobile,
both `.readonly-banner` and `.main__header` independently added
`env(safe-area-inset-top)` padding — when the banner renders above the header (the demo/
wrong-account cases), that safe-area inset got applied twice, producing a large empty band
between the banner and the article list on an iOS PWA. Fixed with a
`.readonly-banner + .main__header` override that drops the header's redundant copy when a
banner precedes it. Deployed via `scripts/deploy.sh` (252 tests, `tsc -b && vite build`
clean).

Also found, not yet fixed: this vhost's general access logging has been effectively disabled
since the `/pages/` tracker's own `CustomLog` was added (2026-09-05) — Apache's
"log vhosts with no `CustomLog` of their own to `other_vhosts_access.log`" mechanism stops
applying to a vhost the moment it defines *any* `CustomLog`, even a conditional one scoped to
a `SetEnvIf`. `other_vhosts_access.log` has been sitting at 0 bytes since the day this
started. This is why the investigation above had to lean on `mod_auth_openidc`'s own session
cache files for evidence instead of the access log — there's no record of any real request to
`/reader/`, `/oidc/callback`, or `/summrabook/` since. Worth a real fix (an explicit
vhost-level `CustomLog` line) but out of scope for this session.

## 2026-09-06 — Light-first theme and low-cost anonymous browsing

- Changed the default theme to light while preserving an explicit saved dark preference. The
  first-paint `<meta name="theme-color">` and PWA manifest now use the light palette so a new
  browser or installed app does not flash dark before React initializes.
- Restyled the public sidebar's sign-in link as the same full-width action treatment as “Copy
  viewport log,” while retaining it as a real link required by the gateway's top-level redirect.
- Anonymous article detail requests now skip passive full-text hydration: a public reader never
  triggers an outbound fetch or a database write just by opening an article. Signed-in reads keep
  the existing fallback for articles missed by refresh-time hydration.
- Anonymous `/api/sources` requests bypass the per-source unread aggregate over `articles` and
  return zero unread counts, which hides source and total unread badges without changing the
  shared response shape. This makes public page opens independent of the retained article count.

Verified with the full backend suite: **253 passed** (one existing Starlette/httpx deprecation
warning). Frontend production build and lint also pass; lint retains one unrelated existing
`ArticleReader.tsx` hook-dependency warning.

## 2026-09-06 (late) — iOS Chrome cold-reload content painted behind the native toolbar

User reported a deterministic Chrome-on-iOS rendering failure: load `/reader/` normally,
clear Chrome's browsing history for the last 24 hours (including cached files), and let
Chrome reload the page. The public-demo banner and nearly all of the list header disappear
behind the browser's top toolbar. Navigating away/backgrounding Chrome and returning repairs
the display. Reproduced on an **iPhone 15 Pro Max**, **iOS 26.6.1**, **Chrome
152.0.7977.64**. It occurs in a normal Chrome tab, not the installed Home Screen PWA, and is
therefore distinct from the standalone viewport-height bugs documented on 2026-08-18 through
2026-08-21.

Two rounds of first-load instrumentation were captured before and after recovery. The first
measured viewport sizes and lifecycle events; the second added window/root/body scroll
positions, Visual Viewport page offsets, and `getBoundingClientRect()` values for the root,
body, a `position:fixed; inset:0` probe, `.shell`, `.readonly-banner`, `.main__header`, and
`.article-list`. The copy action was moved from authenticated Settings to the public sidebar
so the broken anonymous state could be captured without signing in (`50c6be4`); the expanded
position instrumentation landed in `f5eaeb9`.

**All web-visible geometry was correct and identical in the broken and repaired states:**

```
screen / outer                       440 × 956
layout + visual viewport             440 × 766
window scrollX/scrollY               0 / 0
root/body scrollTop                  0 / 0
visualViewport offsetTop/pageTop     0 / 0
root/body/fixed-probe/shell rect     top 0, bottom 766, height 766
banner rect                          top 0, bottom 73
header rect                          top 73, bottom 134
article-list rect                    top 134, bottom 766
safe-area top/bottom                 0 / 0 (expected for a browser tab)
```

The bad state persisted through the automatic `load` snapshots at double-rAF, +300ms, +1s,
and +3s. The navigation was a real cold navigation (`pageshow.persisted === false`), not a
BFCache restoration, and `document.wasDiscarded` stayed false. Chrome emitted `resize`
events both at cold load and after returning to the page, but every measured dimension and
position remained unchanged. A later transient `visualViewport.height === 526` occurred
immediately before another navigation/address-bar interaction and returned to 766; it is not
on the original failure path.

**The screenshots identify the missing coordinate that JavaScript cannot observe.** The
943-pixel screenshot represents the 440-point-wide screen at ~2.143 device pixels per CSS
point. Chrome's native top toolbar ends at screenshot y≈240, or **112 CSS points**. In the
bad screenshot the header's bottom border appears at y≈285; the DOM reports that border at
CSS y=134, and `134 × 2.143 ≈ 287`. Therefore Chrome is painting DOM y=0 at physical screen
y=0, underneath its own 112-point toolbar, instead of mapping DOM y=0 to physical y=112:

- the banner at DOM y=0–73 is completely covered;
- the header at DOM y=73–134 loses its first 39 points and only its last 22 remain visible;
- the article list begins immediately after that clipped remnant.

That pixel arithmetic matches the screenshot exactly, including only the bottom slice of the
36-point hamburger button remaining visible. In the correct screenshot the same DOM y=0
begins below the toolbar at physical y=112 and the full banner/header are visible.

**Conclusion:** this is not stale data, font loading, root scrolling, safe-area padding,
`100dvh`, or a layout/Visual Viewport calculation error. Chrome appears to create or place
its native `WKWebView`/rendering layer with the correct 766-point size but the wrong native Y
origin. Returning to the page triggers native view layout/compositing and repairs that origin
without changing any CSSOM-visible value. This is analogous to WebKit
[#312149](https://bugs.webkit.org/show_bug.cgi?id=312149), where painted pixels disagree
with CSSOM coordinates on iOS Chrome, but that report concerns fixed-bottom elements after
toolbar scrolling and is **not** claimed as the same bug. It also differs from WebKit
[#311821](https://bugs.webkit.org/show_bug.cgi?id=311821): that keyboard-triggered issue
exposes a negative `getBoundingClientRect().top`, while every rect here remains exactly zero.

**No app-side fix implemented.** The broken and correct states are
indistinguishable to page JavaScript—the native view's screen-space frame is not exposed.
Adding a fixed 112px compensation would repair this one state while creating an equally large
gap on every normal load, and forced DOM reflow/viewport-unit changes only operate below the
misplaced native layer. The actionable outcome is a Chrome iOS bug report with the exact
versions, reproduction steps, paired screenshots/logs, and 112-point calculation. Keep the
temporary logger and public copy button until that report is filed or a reliable browser/app
workaround is found.

## 2026-09-06 — Hydrated subtitles and public fallback for unallowed accounts

- Full-text hydration now also derives and stores the list-view subtitle from the extracted
  website content. This applies to both refresh-time batch hydration and an explicit “Pull full
  article” action. The pull response includes the new excerpt, so React patches the visible feed
  row immediately rather than requiring a reload.
- Kept the full-text subtitle limit in `textutil.py`, shared with feed ingestion, so direct and
  hydrated articles use the same 900-character list-preview policy.
- In optional auth mode, a Google account outside `READER_ALLOWED_EMAILS` now receives the same
  public read-only source and article data as an unsigned visitor. `/api/me` alone returns its
  email-specific 403, preserving the UI banner that explains why the public fallback is shown.
  Required mode remains a hard 403; write requests in optional mode remain a 401.
- Added the same 8px footer gap between the public “Copy viewport log” and “Sign in” controls as
  between the authenticated Refresh and Settings controls.

Regression coverage includes hydration persistence and API response excerpts, plus the optional
wrong-account fallback. Verified before deploy with the full backend suite and production frontend
build.

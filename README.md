# OpenReader

A self-hosted, single-user feed reader for people who want to control
their information intake programmatically instead of trusting an
algorithm.

- **RSS/Atom feeds with regex filtering** — `include`/`exclude` rules per
  source, matched against title/summary/content/author/url. Every kept
  article stores *which* rule let it through.
- **Gmail newsletters** — read-only (`gmail.readonly` only; never sends,
  labels, drafts, or deletes).
- **LLM-generated topic tracking** — for interests with no good feed,
  define a research brief and press Generate. Runs on your Claude
  subscription (not API billing), only on manual trigger, and every
  generated article carries hard provenance (a visible badge + a Sources
  block with real citations — never mistakable for a real feed item).
- **No scheduler, anywhere.** Refresh is a button. Generate is a button.
  Nothing runs on a timer.
- Dark/light themes, responsive down to phone width, lazy per-article
  full-text extraction, images proxied server-side (sidesteps hotlink
  protection and keeps your IP off third-party image hosts).

See [`docs/PRD.md`](docs/PRD.md) for the full product spec and
[`docs/ERD.md`](docs/ERD.md) for architecture, the database schema, and
the non-obvious technical decisions (and why). [`docs/WORKLOG.md`](docs/WORKLOG.md)
is a running log of what changed and why, including real bugs found along
the way.

## Quickstart

Requires Python 3.13+, [`uv`](https://docs.astral.sh/uv/), and Node 18+.

```bash
# Backend
cd backend
uv sync
cp ../config/feeds.example.yaml ../config/feeds.yaml   # edit to taste
uv run uvicorn app.asgi:app --host 0.0.0.0 --port 8787

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. The Vite dev server proxies `/api` to the
backend on port 8787. Both bind to `0.0.0.0` by default, so the app is
also reachable from other devices on your LAN (e.g. `http://<your-ip>:5173`
from a phone).

For a production-style run — one port, no separate Vite dev server, the
way you'd access it from your phone over LAN — build the frontend and let
the backend serve it directly:

```bash
./scripts/serve.sh
```

This always rebuilds `frontend/dist` before starting the backend. Run it
this way (not the two-terminal dev setup above) any time you want
`:8787`/your LAN URL to reflect the latest frontend changes — `frontend/dist`
is a static build snapshot, so the backend will silently keep serving
whatever was last built otherwise. (Equivalent to running `cd frontend &&
npm run build` then `cd ../backend && uv run uvicorn app.asgi:app --host
0.0.0.0 --port 8787` by hand, in order, every time.)

### Configuration

`config/feeds.yaml` is the source of truth for sources, filter rules, and
LLM topics — copy it from `config/feeds.example.yaml` (it's gitignored:
it's your personal reading list, not something to commit). Edit it by
hand, or from inside the app (the "Add source" form, or the raw YAML
editor — both validate before writing and never leave a broken file on
disk).

Environment variables (all optional, sensible defaults):

| Variable | Default |
|---|---|
| `READER_CONFIG` | `config/feeds.yaml` |
| `READER_DB` | `data/reader.db` |
| `READER_MEDIA` | `data/media` |
| `READER_GMAIL_TOKEN` | `data/token.json` |
| `READER_IMAP_HOST` / `_USER` / `_PASSWORD` | unset — required together for `type: imap` sources |
| `READER_READONLY_CONFIG` | unset — set to `1` to make `PUT /api/config` return 403 |

### Gmail (optional)

Pulls newsletters straight from your inbox as sources, read-only
(`gmail.readonly` scope only — the connector only ever calls
`messages.list`/`messages.get`/`history.list`; it never sends, labels,
drafts, or deletes).

1. **Create/select a Google Cloud project.**
   https://console.cloud.google.com/projectcreate (or pick an existing
   project from the switcher).
2. **Enable the Gmail API** for that project.
   https://console.cloud.google.com/apis/library/gmail.googleapis.com →
   **Enable**.
3. **Configure the OAuth consent screen** (once per project).
   https://console.cloud.google.com/apis/credentials/consent → **External**
   → fill in an app name and your email. You can leave it in **Testing**
   mode (no Google review needed) — but you MUST add your own Google
   account under **Test users**, or consent fails with `Error 403:
   access_denied` / "has not completed the Google verification process".
4. **Create OAuth credentials.**
   https://console.cloud.google.com/apis/credentials → **Create
   Credentials → OAuth client ID** → type **Desktop app** → **Create** →
   **Download JSON**. Save it as `config/gmail_client_secret.json`
   (gitignored).
5. **One-time consent, run locally:**
   ```bash
   cd backend
   uv run --extra gmail-auth python ../scripts/gmail_auth.py
   ```
   Opens a browser for the read-only consent screen, then writes
   `data/token.json` (gitignored). The running server only ever reads that
   file to mint short-lived access tokens — it never sees your Google
   password, and `google-auth-oauthlib` is never imported outside this one
   script. Re-run this script any time the token is revoked or expires.
6. **Add a `type: gmail` source** to `feeds.yaml`, with a Gmail search
   `query` (same syntax as the Gmail search box):
   ```yaml
   - key: some-newsletter
     type: gmail
     title: Some Newsletter
     folder: Newsletters
     query: "from:sender@example.com newer_than:30d"
   ```
   `newer_than:N` in the query keeps the first refresh from backfilling
   years of inbox history. Press **Refresh feeds** in the app (or `POST
   /api/refresh`) to pull — like everything else in v1, there's no
   scheduler, refresh is always a manual trigger.

### LLM generation (optional)

Off by default (`llm.enabled: false` — a hard kill switch: the UI and API
endpoints are hidden/404 and `claude` is never invoked while it's off).
To use it, you need the [`claude`](https://claude.com/product/claude-code)
CLI installed and logged into your Claude subscription. Flip
`llm.enabled: true` in `feeds.yaml`, add a `topics` entry, and press
**Generate** in the sidebar.

## Development

```bash
cd backend && uv run pytest -q      # 147+ tests, no network/subprocess
cd frontend && npx tsc --noEmit -p tsconfig.app.json && npm run build
```

Backend tests are unit tests against injected fetch/subprocess functions
— nothing touches the network or spawns a real process. Frontend changes
are verified via typecheck + build + live browser testing (no automated
frontend test suite yet).

## Project structure

```
backend/app/
├── connectors/    # RSS/Atom/RDF parser, Gmail REST client, date normalization
├── ingest/        # regex rules engine, dedup, lazy full-text extraction, sanitize
├── generate/      # claude CLI wrapper, job tracking, out-of-process worker
└── api/           # Starlette route handlers
frontend/src/
├── components/    # Sidebar, ArticleList, ArticleReader, ConfigEditor, ...
└── App.tsx         # 3-pane shell + state
scripts/
└── gmail_auth.py  # one-time OAuth consent (only place google-auth-oauthlib is used)
docs/
├── PRD.md          # product requirements
├── ERD.md           # architecture + entity-relationship diagram
└── WORKLOG.md        # chronological change log
```

## License

Personal project, no license file yet — treat as all-rights-reserved
unless/until one is added.

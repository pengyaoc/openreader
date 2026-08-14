# OpenReader

A self-hosted, single-user feed reader for people who want to control
their information intake programmatically instead of trusting an
algorithm.

- **RSS/Atom feeds with regex filtering** — `include`/`exclude` rules per
  source, matched against title/summary/content/author/url. Every kept
  article stores *which* rule let it through.
- **Newsletters via IMAP** — reads a mailbox with an app password (no
  OAuth, no re-auth or token-expiry maintenance), read-only throughout.
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
| `READER_IMAP_HOST` / `_USER` / `_PASSWORD` | unset — required together for `type: imap` sources |
| `READER_READONLY_CONFIG` | unset — set to `1` to make `PUT /api/config` return 403 |
| `READER_AUTH_PASSWORD_HASH` / `READER_SESSION_SECRET` | unset — no login required (see Login below); both must be set together |

### IMAP newsletters (optional)

Pulls newsletters straight from a mailbox as sources, read-only throughout
(SEARCH/FETCH only, opened with `readonly=True` — never STORE/EXPUNGE/DELETE).
Authenticates with an app password rather than OAuth: no consent screen, no
token to refresh, nothing that expires and needs re-auth.

1. **Get an app password** for the mailbox you want to read from (for a
   Gmail account: [Google Account → Security → App
   passwords](https://myaccount.google.com/apppasswords) — requires 2-Step
   Verification to be enabled). A dedicated newsletter-only mailbox is
   worth setting up separately from your primary inbox, so this app only
   ever sees what you've deliberately routed to it.
2. **Set the three IMAP environment variables** before starting the
   backend: `READER_IMAP_HOST` (e.g. `imap.gmail.com`), `READER_IMAP_USER`,
   `READER_IMAP_PASSWORD` (the app password, not your account password).
   All three are required together — a partial set is treated as "IMAP not
   configured".
3. **Add a `type: imap` source** to `feeds.yaml`, with a Gmail-search-style
   `query` (`from:`/`subject:`/`newer_than:` — IMAP SEARCH doesn't support
   Gmail's full operator set, so only those three tokens are understood):
   ```yaml
   - key: some-newsletter
     type: imap
     title: Some Newsletter
     folder: Newsletters
     query: "from:sender@example.com newer_than:30d"
   ```
   `newer_than:N` in the query keeps the first refresh from backfilling
   years of mailbox history. `mailbox_folder` (optional, defaults to
   `INBOX`) picks which folder to SEARCH if your mail client filters
   newsletters into their own folder. Press **Refresh feeds** in the app
   (or `POST /api/refresh`) to pull — like everything else in v1, there's
   no scheduler, refresh is always a manual trigger.

   All IMAP sources in one refresh share a single connection, opened once
   and reused sequentially rather than one connection per source — mail
   providers can silently throttle a datacenter IP that opens many fresh
   logins in quick succession.

### Login (optional)

Off by default — same as everything else on this page, local/LAN use has
never required a password. Set this up if the app is reachable from the
internet (see the co-hosted VM deployment in `docs/ERD.md` §7.1), which is
the only scenario it exists for.

The app has its own login screen and a 90-day session cookie — no
Apache/reverse-proxy config needed, and unlike Basic Auth (this app's
approach until 2026-08-13), Chrome recognizes the real `<form>` login and
offers to save the password, and the session doesn't get dropped every
time a mobile browser reclaims a backgrounded tab.

1. **Generate a bcrypt password hash, locally:**
   ```bash
   htpasswd -nbB reader 'your-password-here'
   ```
   Take just the hash portion after `reader:` (starts with `$2y$` or
   `$2b$`). The plaintext password is never stored anywhere — see the
   hashing step above and `app/auth.py`'s module docstring.
2. **Generate a session secret, locally:**
   ```bash
   python3 -c "import secrets; print(secrets.token_hex(32))"
   ```
3. **Set both env vars** before starting the backend:
   `READER_AUTH_PASSWORD_HASH` (the hash from step 1) and
   `READER_SESSION_SECRET` (from step 2). Both must be set together — one
   without the other is treated as broken config and locks everything out
   (401), not a silent fallback to no-auth.

### LLM generation (optional)

Off by default (`llm.enabled: false` — a hard kill switch: the UI and API
endpoints are hidden/404 and `claude` is never invoked while it's off).
To use it, you need the [`claude`](https://claude.com/product/claude-code)
CLI installed and logged into your Claude subscription. Flip
`llm.enabled: true` in `feeds.yaml`, add a `topics` entry, and press
**Generate** in the sidebar. The same subscription/CLI setup also backs
the **Summarize** button in the reader (per-article, on demand) — no
separate config for that, it shares `llm.enabled`.

### Production deployment: one env file, not two

If you're running this as a long-lived `systemd --user` service (as
opposed to the dev/LAN setup above) — see `docs/ERD.md` §7.1 for the full
co-hosted-VM writeup this is drawn from — put **every** environment
variable the service needs, secret and non-secret alike, in one file
loaded via systemd's `EnvironmentFile=` directive, rather than splitting
secrets into that file and non-secrets into inline `Environment=` lines in
the unit itself. One file is easier to audit, back up, and reason about
than config split across two.

`EnvironmentFile=` is a systemd directive that loads `KEY=value` lines
from a plain file at service start — distinct from `Environment=`, which
sets a var inline in the unit file. A leading `-` before the path (as
below) means "don't error if the file is missing."

1. **Create the env file** (owned by the service user, not world-readable
   — this holds real secrets):
   ```bash
   sudo -u <service-user> tee /opt/openreader/openreader.env > /dev/null << 'EOF'
   # Secrets
   READER_IMAP_HOST=imap.gmail.com
   READER_IMAP_USER=your-newsletter-inbox@example.com
   READER_IMAP_PASSWORD=your-app-password
   READER_AUTH_PASSWORD_HASH=$2b$...        # from `htpasswd -nbB`, see Login above
   READER_SESSION_SECRET=...                # from `secrets.token_hex(32)`, see Login above

   # Non-secret process wiring — kept in the same file so there's exactly
   # one place to look, not because it's sensitive
   READER_CONFIG=/opt/openreader/config/feeds.yaml
   READER_DB=/opt/openreader/data/reader.db
   READER_MEDIA=/opt/openreader/data/media
   PATH=/opt/node/bin:/home/<service-user>/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
   EOF
   chmod 600 /opt/openreader/openreader.env
   ```
   The `PATH` override is only needed if you're using LLM
   generation/summarization — it's what lets the service find the `claude`
   CLI (installed under `/opt/node` + a user-local npm prefix, since `apt`
   is often unusable on an old/frozen distro — see `docs/WORKLOG.md`,
   2026-08-14, for why and how) as a subprocess. Drop it if you're not
   using that feature.

2. **Point the systemd unit at just that one file** — no inline
   `Environment=` lines at all:
   ```ini
   [Unit]
   Description=OpenReader (self-hosted feed reader)
   After=network.target

   [Service]
   WorkingDirectory=/opt/openreader/backend
   ExecStart=%h/.local/bin/uv run uvicorn app.asgi:app --host 127.0.0.1 --port 8787
   Restart=on-failure
   RestartSec=5

   EnvironmentFile=-/opt/openreader/openreader.env

   # Sandboxing (optional but recommended)
   NoNewPrivileges=true
   PrivateTmp=true
   ProtectSystem=strict
   ProtectHome=read-only
   # Carve out exactly what needs to be writable: app data/config, plus
   # ~/.claude if you're using LLM generation/summarization (the `claude`
   # CLI needs to write there for its own OAuth token refresh).
   ReadWritePaths=/opt/openreader/data /opt/openreader/config /home/<service-user>/.claude
   # Size well past a single `claude -p` call's own peak RSS (~300MB,
   # measured live) if using LLM features, not just the FastAPI process's
   # own baseline — see docs/WORKLOG.md, 2026-08-14, for how this was
   # measured and why 300M OOM-killed the first real call.
   MemoryMax=700M

   [Install]
   WantedBy=default.target
   ```

3. **Reload and restart** after either file changes:
   ```bash
   systemctl --user daemon-reload
   systemctl --user restart openreader
   ```

`config/feeds.yaml` stays separate from this file on purpose — it's your
live source list and LLM topics, edited far more often than secrets, and
already covered under **Configuration** above.

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
├── connectors/    # RSS/Atom/RDF parser, IMAP client, date normalization
├── ingest/        # regex rules engine, dedup, lazy full-text extraction, sanitize
├── generate/      # claude CLI wrapper, job tracking, out-of-process worker
└── api/           # Starlette route handlers
frontend/src/
├── components/    # Sidebar, ArticleList, ArticleReader, ConfigEditor, ...
└── App.tsx         # 3-pane shell + state
docs/
├── PRD.md          # product requirements
├── ERD.md           # architecture + entity-relationship diagram
└── WORKLOG.md        # chronological change log
```

## License

Personal project, no license file yet — treat as all-rights-reserved
unless/until one is added.

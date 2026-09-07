// Typed fetch client for the reader backend. No abstraction beyond what's
// needed — TanStack Query owns caching, this owns the wire format.

// '/' locally and on LAN; '/reader/' when built for the VM's Apache
// ProxyPass mount (see vite.config.ts's `base`). import.meta.env.BASE_URL
// always has a trailing slash, so strip it once here rather than at every
// call site.
export const API_BASE = import.meta.env.BASE_URL.replace(/\/$/, '')

// Apache's dedicated login entry point (see docs/WORKLOG.md, 2026-09-07,
// and the vault's wordpress-vm-pages-setup.md for the <Location> that
// backs this). `logout=` first clears any existing Apache session before
// redirecting back to this same URL — necessary because without it, an
// existing session for the *wrong* Google account would just pass
// straight through again with no new authorization request at all,
// silently reusing the same wrong identity forever (mod_auth_openidc
// only re-prompts when there's no valid session to satisfy `Require
// valid-user` in the first place). The Apache <Location> also sets
// `OIDCAuthRequestParams "prompt=select_account"` vhost-wide, so once a
// fresh authorization request does fire, Google shows its account picker
// instead of silently re-using its own still-active session for the
// same wrong account.
export const SIGNIN_URL = `${API_BASE}/login?logout=${encodeURIComponent(`${API_BASE}/login`)}`

export type ArticleOrigin = 'feed' | 'email'

export interface Article {
  id: number
  source_id: number
  guid: string
  url: string
  canonical_url: string | null
  title: string
  author: string
  published_at: string | null
  fetched_at: string | null
  excerpt: string
  content_html: string
  top_image_path: string | null
  matched_rule: string | null
  origin: ArticleOrigin
  hydrated_at: string | null
  hydrate_failed_at: string | null
  is_read: boolean
  read_at: string | null
  is_starred: boolean
  llm_summary_html: string | null
  llm_summary_at: string | null
  source_title?: string
}

// Shape of a row from GET /api/articles — deliberately missing
// content_html/llm_summary_html (see app/store.py's _LIST_COLUMNS): the
// list view never renders either field, and shipping them made a 50-item
// page ~570KB of JSON the client immediately discarded. `has_summary`
// stands in for llm_summary_html so the list can still show a summary
// indicator without the body. GET /api/articles/:id (`api.article`) still
// returns the full Article.
export type ArticleListItem = Omit<Article, 'content_html' | 'llm_summary_html'> & {
  has_summary: boolean
}

export type SourceType = 'rss' | 'imap'

export interface Source {
  id: number
  key: string
  type: SourceType
  title: string
  folder: string
  last_fetched_at: string | null
  last_error: string | null
  unread_count: number
}

// Full detail for one source — GET /api/sources/:id. Adds the
// config-derived fields (url/query/etc.) the lean sidebar listing above
// doesn't carry; used to pre-fill the edit form.
export interface SourceDetail extends Source {
  url: string | null
  query: string | null
  mailbox_folder: string | null
  fetch_full_text: boolean
  rules: Rule[]
}

export interface RefreshSourceReport {
  key: string
  status: 'ok' | 'not_modified' | 'error'
  fetched?: number
  new?: number
  filtered?: number
  error?: string
}

export interface RefreshReport {
  elapsed_ms: number
  sources: RefreshSourceReport[]
}

export type RuleAction = 'include' | 'exclude'
export type RuleField = 'title' | 'summary' | 'content' | 'author' | 'url' | 'any'

export interface Rule {
  action: RuleAction
  field: RuleField
  pattern: string
}

// Two source shapes the add/edit form can submit — url only makes sense
// for rss, query/mailbox_folder only for imap. `key` is only present when
// creating (POST); the backend locks key/type on edit (PUT) regardless of
// what's sent, so update payloads omit it rather than imply it's editable.
interface SourceFieldsRss {
  type: 'rss'
  title: string
  folder: string
  url: string
  fetch_full_text?: boolean
  rules?: Rule[]
}

interface SourceFieldsImap {
  type: 'imap'
  title: string
  folder: string
  query?: string
  mailbox_folder?: string
  rules?: Rule[]
}

export type SourceFields = SourceFieldsRss | SourceFieldsImap
export type NewSource = SourceFields & { key: string }

// The signed-in account, or the anonymous state (id/username both null)
// under `optional` mode — see docs/WORKLOG.md, 2026-09-05. `auth_enabled`
// is false on a no-login deployment (`off` mode, local/LAN), where a
// non-null id is the default account rather than anyone who signed in.
export interface Me {
  id: number | null
  username: string | null
  auth_enabled: boolean
}

// Thrown instead of a generic Error on a 401, so callers (App.tsx's
// top-level auth check) can tell "not authenticated" apart from a normal
// request failure. Only reachable in `required` mode now — `optional`
// mode (this deployment's default) lets reads through anonymously; see
// docs/WORKLOG.md, 2026-09-05.
export class UnauthorizedError extends Error {}

// Thrown on a 403 — the caller completed Google sign-in (Apache accepts
// any Google account; it doesn't know about READER_ALLOWED_EMAILS), but
// the email isn't on this app's own allowlist. `email` is the backend's
// echo of the caller's own identity (pchauth's starlette_adapter,
// 2026-09-07), letting App.tsx show "signed in as X, not authorized"
// instead of silently looking identical to never having signed in at all.
export class ForbiddenError extends Error {
  email: string | null
  constructor(message: string, email: string | null) {
    super(message)
    this.email = email
  }
}

// Every call goes through here. `credentials: 'include'` is a no-op under
// trusted_header (there's no cookie of this app's own any more — identity
// comes from Apache's X-Remote-Email header), kept only in case a
// deployment ever sits behind something that does use cookies.
function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${API_BASE}${path}`, { ...init, credentials: 'include' })
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    if (res.status === 401) throw new UnauthorizedError('not authenticated')
    let detail = res.statusText
    let email: string | null = null
    try {
      const body = await res.json()
      detail = body.error ?? detail
      email = body.email ?? null
    } catch {
      /* ignore */
    }
    if (res.status === 403) throw new ForbiddenError(detail, email)
    throw new Error(detail)
  }
  return res.json()
}

export const api = {
  // There is no login/logout call any more — signing in means visiting
  // any gated pengyaochen.com path and letting Apache's Google redirect
  // run; signing out is the shared gateway's logout, not this app's.
  me: () => apiFetch('/api/me').then((r) => json<Me>(r)),

  sources: () => apiFetch('/api/sources').then((r) => json<Source[]>(r)),

  articles: (params: { view?: string; source_id?: number; folder?: string; offset?: number }) => {
    const qs = new URLSearchParams()
    if (params.view) qs.set('view', params.view)
    if (params.source_id) qs.set('source_id', String(params.source_id))
    if (params.folder) qs.set('folder', params.folder)
    if (params.offset) qs.set('offset', String(params.offset))
    return apiFetch(`/api/articles?${qs}`).then((r) => json<ArticleListItem[]>(r))
  },

  article: (id: number) => apiFetch(`/api/articles/${id}`).then((r) => json<Article>(r)),

  markRead: (id: number) =>
    apiFetch(`/api/articles/${id}/read`, { method: 'POST' }).then((r) => json<{ ok: boolean }>(r)),

  toggleStar: (id: number) =>
    apiFetch(`/api/articles/${id}/star`, { method: 'POST' }).then((r) =>
      json<{ is_starred: boolean }>(r),
    ),

  summarize: (id: number) =>
    apiFetch(`/api/articles/${id}/summarize`, { method: 'POST' }).then((r) =>
      json<{ summary_html: string; llm_summary_at: string }>(r),
    ),

  pullFullArticle: (id: number) =>
    apiFetch(`/api/articles/${id}/hydrate`, { method: 'POST' }).then((r) =>
      json<{
        content_html: string
        excerpt: string
        hydrated_at: string | null
        hydrate_failed_at: string | null
      }>(
        r,
      ),
    ),

  toggleRead: (id: number) =>
    apiFetch(`/api/articles/${id}/toggle-read`, { method: 'POST' }).then((r) =>
      json<{ is_read: boolean }>(r),
    ),

  refresh: (sourceKey?: string) =>
    apiFetch(
      `/api/refresh${sourceKey ? `?source=${encodeURIComponent(sourceKey)}` : ''}`,
      { method: 'POST' },
    ).then((r) => json<RefreshReport>(r)),

  addSource: (source: NewSource) =>
    apiFetch('/api/sources', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(source),
    }).then((r) => json<{ ok: boolean; key: string }>(r)),

  getSource: (id: number) => apiFetch(`/api/sources/${id}`).then((r) => json<SourceDetail>(r)),

  updateSource: (id: number, fields: SourceFields) =>
    apiFetch(`/api/sources/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(fields),
    }).then((r) => json<{ ok: boolean; key: string; reconciled: number }>(r)),

  removeSource: (id: number) =>
    apiFetch(`/api/sources/${id}`, { method: 'DELETE' }).then((r) =>
      json<{ ok: boolean; reconciled: number }>(r),
    ),

  markAllRead: (sourceId: number) =>
    apiFetch(`/api/sources/${sourceId}/mark-all-read`, { method: 'POST' }).then((r) =>
      json<{ ok: boolean; marked: number }>(r),
    ),

  markAllUnreadRead: () =>
    apiFetch('/api/articles/mark-all-read', { method: 'POST' }).then((r) =>
      json<{ ok: boolean; marked: number }>(r),
    ),

  llmStatus: () => apiFetch('/api/llm-status').then((r) => json<{ enabled: boolean }>(r)),

  getConfig: () => apiFetch('/api/config').then((r) => json<{ yaml: string }>(r)),

  putConfig: (yaml: string) =>
    apiFetch('/api/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ yaml }),
    }).then((r) => json<{ ok: boolean }>(r)),
}

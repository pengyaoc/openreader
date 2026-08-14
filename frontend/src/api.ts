// Typed fetch client for the reader backend. No abstraction beyond what's
// needed — TanStack Query owns caching, this owns the wire format.

// '/' locally and on LAN; '/reader/' when built for the VM's Apache
// ProxyPass mount (see vite.config.ts's `base`). import.meta.env.BASE_URL
// always has a trailing slash, so strip it once here rather than at every
// call site.
export const API_BASE = import.meta.env.BASE_URL.replace(/\/$/, '')

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

// Thrown instead of a generic Error on a 401, so callers (App.tsx's
// top-level auth check) can tell "not logged in" apart from a normal
// request failure and show the login screen instead of an error state.
export class UnauthorizedError extends Error {}

// Every call goes through here so `credentials: 'include'` (the session
// cookie set by POST /api/login) is never forgotten at a call site — the
// app-layer login replacing Apache Basic Auth (docs/WORKLOG.md,
// 2026-08-13 cont.) only works if the cookie actually round-trips.
function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${API_BASE}${path}`, { ...init, credentials: 'include' })
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    if (res.status === 401) throw new UnauthorizedError('not authenticated')
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.error ?? detail
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }
  return res.json()
}

export const api = {
  login: (password: string) =>
    apiFetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    }).then((r) => json<{ ok: boolean }>(r)),

  logout: () => apiFetch('/api/logout', { method: 'POST' }).then((r) => json<{ ok: boolean }>(r)),

  sources: () => apiFetch('/api/sources').then((r) => json<Source[]>(r)),

  articles: (params: { view?: string; source_id?: number; folder?: string; offset?: number }) => {
    const qs = new URLSearchParams()
    if (params.view) qs.set('view', params.view)
    if (params.source_id) qs.set('source_id', String(params.source_id))
    if (params.folder) qs.set('folder', params.folder)
    if (params.offset) qs.set('offset', String(params.offset))
    return apiFetch(`/api/articles?${qs}`).then((r) => json<Article[]>(r))
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
      json<{ content_html: string; hydrated_at: string | null; hydrate_failed_at: string | null }>(
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

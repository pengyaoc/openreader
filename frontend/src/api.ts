// Typed fetch client for the reader backend. No abstraction beyond what's
// needed — TanStack Query owns caching, this owns the wire format.

export type ArticleOrigin = 'feed' | 'gmail' | 'llm'

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
  job_id: number | null
  citations_json: string | null
  hydrated_at: string | null
  hydrate_failed_at: string | null
  is_read: boolean
  read_at: string | null
  is_starred: boolean
  source_title?: string
}

export interface Source {
  id: number
  key: string
  type: 'rss' | 'gmail' | 'llm'
  title: string
  folder: string
  last_fetched_at: string | null
  last_error: string | null
  unread_count: number
}

export interface Topic {
  key: string
  title: string
  folder: string
  brief: string
  lookback_days: number
  max_articles: number
}

export type JobStatus = 'queued' | 'running' | 'done' | 'error'

export interface Job {
  id: number
  topic_key: string
  status: JobStatus
  brief_snapshot: string | null
  model: string | null
  started_at: string | null
  finished_at: string | null
  error: string | null
  articles_created: number
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

export interface NewSource {
  key: string
  type: 'rss'
  title: string
  folder: string
  url: string
  fetch_full_text?: boolean
  rules?: Rule[]
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
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
  sources: () => fetch('/api/sources').then((r) => json<Source[]>(r)),

  articles: (params: { view?: string; source_id?: number; folder?: string }) => {
    const qs = new URLSearchParams()
    if (params.view) qs.set('view', params.view)
    if (params.source_id) qs.set('source_id', String(params.source_id))
    if (params.folder) qs.set('folder', params.folder)
    return fetch(`/api/articles?${qs}`).then((r) => json<Article[]>(r))
  },

  article: (id: number) => fetch(`/api/articles/${id}`).then((r) => json<Article>(r)),

  markRead: (id: number) =>
    fetch(`/api/articles/${id}/read`, { method: 'POST' }).then((r) => json<{ ok: boolean }>(r)),

  toggleStar: (id: number) =>
    fetch(`/api/articles/${id}/star`, { method: 'POST' }).then((r) =>
      json<{ is_starred: boolean }>(r),
    ),

  toggleRead: (id: number) =>
    fetch(`/api/articles/${id}/toggle-read`, { method: 'POST' }).then((r) =>
      json<{ is_read: boolean }>(r),
    ),

  refresh: (sourceKey?: string) =>
    fetch(`/api/refresh${sourceKey ? `?source=${encodeURIComponent(sourceKey)}` : ''}`, {
      method: 'POST',
    }).then((r) => json<RefreshReport>(r)),

  addSource: (source: NewSource) =>
    fetch('/api/sources', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(source),
    }).then((r) => json<{ ok: boolean; key: string }>(r)),

  topics: () => fetch('/api/topics').then((r) => json<{ enabled: boolean; topics: Topic[] }>(r)),

  generateTopic: (key: string) =>
    fetch(`/api/topics/${encodeURIComponent(key)}/generate`, { method: 'POST' }).then((r) =>
      json<{ job_id: number }>(r),
    ),

  getJob: (id: number) => fetch(`/api/jobs/${id}`).then((r) => json<Job>(r)),

  getConfig: () => fetch('/api/config').then((r) => json<{ yaml: string }>(r)),

  putConfig: (yaml: string) =>
    fetch('/api/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ yaml }),
    }).then((r) => json<{ ok: boolean }>(r)),
}

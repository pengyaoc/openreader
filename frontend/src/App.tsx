import { useCallback, useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { api, type Article, type Job, type RefreshReport, type Source } from './api'
import type { ViewSelection } from './types'
import { Sidebar } from './components/Sidebar'
import { ArticleList } from './components/ArticleList'
import { ArticleReader } from './components/ArticleReader'
import { ConfigEditor } from './components/ConfigEditor'
import { AddSourceModal } from './components/AddSourceModal'
import { RefreshToast } from './components/RefreshToast'

const VIEW_TITLES: Record<string, string> = {
  all: 'All items',
  unread: 'Unread',
  starred: 'Starred',
}

// Patch cached article data in place rather than invalidating — marking an
// article read should grey it out where it sits, not yank it out of the
// "Unread" list or refetch/reflow the whole page underneath the reader.
function patchArticleCaches(qc: QueryClient, id: number, patch: Partial<Article>) {
  qc.setQueriesData<Article[]>({ queryKey: ['articles'] }, (old) =>
    old?.map((a) => (a.id === id ? { ...a, ...patch } : a)),
  )
  qc.setQueryData<Article>(['article', id], (prev) => (prev ? { ...prev, ...patch } : prev))
}

function adjustSourceUnread(qc: QueryClient, sourceId: number, delta: number) {
  qc.setQueryData<Source[]>(['sources'], (old) =>
    old?.map((s) =>
      s.id === sourceId ? { ...s, unread_count: Math.max(0, s.unread_count + delta) } : s,
    ),
  )
}

export default function App() {
  const qc = useQueryClient()
  const [selection, setSelection] = useState<ViewSelection>({ kind: 'saved', view: 'unread' })
  const [openArticleId, setOpenArticleId] = useState<number | null>(null)
  const [cursorId, setCursorId] = useState<number | null>(null)
  const [configOpen, setConfigOpen] = useState(false)
  const [addSourceOpen, setAddSourceOpen] = useState(false)
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const [refreshReport, setRefreshReport] = useState<RefreshReport | null>(null)
  const [jobsByTopic, setJobsByTopic] = useState<Record<string, Job>>({})
  const [theme, setTheme] = useState<'dark' | 'light'>(
    () => (localStorage.getItem('reader-theme') as 'dark' | 'light' | null) ?? 'dark',
  )

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('reader-theme', theme)
  }, [theme])

  const sourcesQuery = useQuery({ queryKey: ['sources'], queryFn: api.sources })
  const topicsQuery = useQuery({ queryKey: ['topics'], queryFn: api.topics })

  const listParams = useMemo(() => {
    if (selection.kind === 'saved') return { view: selection.view }
    if (selection.kind === 'source') return { view: 'all', source_id: selection.sourceId }
    return { view: 'all', folder: selection.folder }
  }, [selection])

  const articlesQuery = useQuery({
    queryKey: ['articles', listParams],
    queryFn: () => api.articles(listParams),
  })

  const openArticleQuery = useQuery({
    queryKey: ['article', openArticleId],
    queryFn: () => api.article(openArticleId!),
    enabled: openArticleId !== null,
  })

  const refreshMutation = useMutation({
    mutationFn: () => api.refresh(),
    onSuccess: (report) => {
      setRefreshReport(report)
      qc.invalidateQueries({ queryKey: ['sources'] })
      qc.invalidateQueries({ queryKey: ['articles'] })
    },
  })

  const generateMutation = useMutation({
    mutationFn: (topicKey: string) => api.generateTopic(topicKey),
    onSuccess: (data, topicKey) => {
      setJobsByTopic((prev) => ({
        ...prev,
        [topicKey]: {
          id: data.job_id,
          topic_key: topicKey,
          status: 'queued',
          brief_snapshot: null,
          model: null,
          started_at: null,
          finished_at: null,
          error: null,
          articles_created: 0,
        },
      }))
    },
  })

  // Poll active generation jobs every 3s until each settles. Job state
  // lives in SQLite (not this component), so this survives navigating away
  // and back — it's just re-reading status, not driving the job itself.
  useEffect(() => {
    const active = Object.entries(jobsByTopic).filter(
      ([, j]) => j.status === 'queued' || j.status === 'running',
    )
    if (active.length === 0) return

    const interval = setInterval(() => {
      active.forEach(async ([topicKey, job]) => {
        try {
          const updated = await api.getJob(job.id)
          setJobsByTopic((prev) => ({ ...prev, [topicKey]: updated }))
          if (updated.status === 'done' || updated.status === 'error') {
            qc.invalidateQueries({ queryKey: ['sources'] })
            qc.invalidateQueries({ queryKey: ['articles'] })
          }
        } catch {
          // transient poll failure — try again next tick
        }
      })
    }, 3000)
    return () => clearInterval(interval)
  }, [jobsByTopic, qc])

  const markReadMutation = useMutation({
    mutationFn: (article: Article) => api.markRead(article.id),
    onSuccess: (_data, article) => {
      patchArticleCaches(qc, article.id, { is_read: true })
      adjustSourceUnread(qc, article.source_id, -1)
    },
  })

  const toggleReadMutation = useMutation({
    mutationFn: (article: Article) => api.toggleRead(article.id),
    onSuccess: (data, article) => {
      patchArticleCaches(qc, article.id, { is_read: data.is_read })
      adjustSourceUnread(qc, article.source_id, data.is_read ? -1 : 1)
    },
  })

  const toggleStarMutation = useMutation({
    mutationFn: (id: number) => api.toggleStar(id),
    onSuccess: (data, id) => {
      patchArticleCaches(qc, id, { is_starred: data.is_starred })
    },
  })

  const openArticle = useCallback(
    (article: Article) => {
      setOpenArticleId(article.id)
      setCursorId(article.id)
      if (!article.is_read) markReadMutation.mutate(article)
    },
    [markReadMutation],
  )

  const closeArticle = useCallback(() => setOpenArticleId(null), [])

  const articles = articlesQuery.data ?? []

  // openArticleQuery.data is undefined for a beat whenever openArticleId
  // changes (React Query resets it while the new query loads) — without a
  // fallback, ArticleReader would unmount and flash the list underneath on
  // every prev/next navigation. The list already has this article cached
  // (that's how we got here), so use it as an instant placeholder; the
  // query result (with lazily-hydrated full text) swaps in once it lands.
  const openArticleDisplayData =
    openArticleQuery.data ?? articles.find((a) => a.id === openArticleId)

  const openArticleIndex = articles.findIndex((a) => a.id === openArticleId)
  const hasPrev = openArticleIndex > 0
  const hasNext = openArticleIndex !== -1 && openArticleIndex < articles.length - 1

  const goToOffset = useCallback(
    (offset: number) => {
      if (openArticleIndex === -1) return
      const next = articles[openArticleIndex + offset]
      if (next) openArticle(next)
    },
    [articles, openArticleIndex, openArticle],
  )
  const goPrev = useCallback(() => goToOffset(-1), [goToOffset])
  const goNext = useCallback(() => goToOffset(1), [goToOffset])

  // keyboard: j/k move, o/Enter open, Esc close (handled in reader), m toggle, r refresh
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName
      if (tag === 'TEXTAREA' || tag === 'INPUT') return
      if (openArticleId !== null) return // reader owns Esc itself

      if (e.key === 'j' || e.key === 'k') {
        e.preventDefault()
        const idx = articles.findIndex((a) => a.id === cursorId)
        const nextIdx =
          e.key === 'j'
            ? Math.min(idx + 1, articles.length - 1)
            : Math.max(idx - 1, 0)
        const next = articles[nextIdx === -1 ? 0 : nextIdx]
        if (next) {
          setCursorId(next.id)
          document
            .querySelector(`[data-article-id="${next.id}"]`)
            ?.scrollIntoView({ block: 'nearest' })
        }
      } else if (e.key === 'o' || e.key === 'Enter') {
        const current = articles.find((a) => a.id === cursorId)
        if (current) openArticle(current)
      } else if (e.key === 'm') {
        const current = articles.find((a) => a.id === cursorId)
        if (current) toggleReadMutation.mutate(current)
      } else if (e.key === 'r') {
        refreshMutation.mutate()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [articles, cursorId, openArticleId, openArticle, toggleReadMutation, refreshMutation])

  const totalUnread = (sourcesQuery.data ?? []).reduce((n, s) => n + s.unread_count, 0)
  const totalStarred = articles.filter((a) => a.is_starred).length

  const headerTitle =
    selection.kind === 'saved'
      ? VIEW_TITLES[selection.view]
      : selection.kind === 'source'
        ? selection.title
        : selection.folder

  return (
    <div className="shell">
      <Sidebar
        sources={sourcesQuery.data ?? []}
        selection={selection}
        onSelect={setSelection}
        totalUnread={totalUnread}
        totalStarred={totalStarred}
        onRefresh={() => refreshMutation.mutate()}
        refreshing={refreshMutation.isPending}
        onOpenConfig={() => setConfigOpen(true)}
        theme={theme}
        onToggleTheme={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}
        onAddSource={() => setAddSourceOpen(true)}
        mobileOpen={mobileSidebarOpen}
        onCloseMobile={() => setMobileSidebarOpen(false)}
        llmEnabled={topicsQuery.data?.enabled ?? false}
        topics={topicsQuery.data?.topics ?? []}
        jobsByTopic={jobsByTopic}
        onGenerate={(key) => generateMutation.mutate(key)}
      />

      <div className="main">
        <div className="main__header">
          <div className="main__header-inner">
            <button
              className="mobile-menu-btn"
              onClick={() => setMobileSidebarOpen(true)}
              aria-label="Open menu"
            >
              ☰
            </button>
            <div>
              <span className="main__title">{headerTitle}</span>
              <span className="main__title-count">{articles.length} items</span>
            </div>
          </div>
        </div>
        <ArticleList articles={articles} selectedId={cursorId} onOpen={openArticle} />
      </div>

      {openArticleId !== null && openArticleDisplayData && (
        <ArticleReader
          article={openArticleDisplayData}
          loading={openArticleQuery.isLoading}
          onClose={closeArticle}
          onToggleStar={() => toggleStarMutation.mutate(openArticleId)}
          onPrev={goPrev}
          onNext={goNext}
          hasPrev={hasPrev}
          hasNext={hasNext}
        />
      )}

      {configOpen && (
        <ConfigEditor
          onClose={() => setConfigOpen(false)}
          onSaved={() => {
            qc.invalidateQueries({ queryKey: ['sources'] })
            qc.invalidateQueries({ queryKey: ['articles'] })
          }}
        />
      )}

      {addSourceOpen && (
        <AddSourceModal
          onClose={() => setAddSourceOpen(false)}
          onAdded={() => {
            qc.invalidateQueries({ queryKey: ['sources'] })
            qc.invalidateQueries({ queryKey: ['articles'] })
          }}
        />
      )}

      {refreshReport && (
        <RefreshToast report={refreshReport} onClose={() => setRefreshReport(null)} />
      )}
    </div>
  )
}

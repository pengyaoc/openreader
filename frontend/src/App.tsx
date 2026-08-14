import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query'
import { api, UnauthorizedError, type Article, type RefreshReport, type Source } from './api'
import type { ViewSelection } from './types'
import { Sidebar } from './components/Sidebar'
import { ArticleList } from './components/ArticleList'
import { ArticleReader } from './components/ArticleReader'
import { ConfigEditor } from './components/ConfigEditor'
import { SourceModal } from './components/SourceModal'
import { RefreshToast } from './components/RefreshToast'
import { LoginPage } from './components/LoginPage'

const VIEW_TITLES: Record<string, string> = {
  all: 'All items',
  unread: 'Unread',
  starred: 'Starred',
}

// Matches the backend's default page size (app/api/articles.py) — the
// article list is paginated, loaded a page at a time via "Load more"
// rather than the old behavior of silently capping at the server default
// with no way to see anything past it.
const ARTICLES_PAGE_SIZE = 50

type ArticlesPages = { pages: Article[][]; pageParams: number[] }

// Patch cached article data in place rather than invalidating — marking an
// article read should grey it out where it sits, not yank it out of the
// "Unread" list or refetch/reflow the whole page underneath the reader.
function patchArticleCaches(qc: QueryClient, id: number, patch: Partial<Article>) {
  qc.setQueriesData<ArticlesPages>({ queryKey: ['articles'] }, (old) =>
    old && { ...old, pages: old.pages.map((page) => page.map((a) => (a.id === id ? { ...a, ...patch } : a))) },
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

// The open article and current view live only in React state, so a page
// reload always started blank — including the reload iOS Safari forces on
// its own after a tab sits idle/backgrounded for a while, which has no
// app-level trigger to hook and can't be prevented. Mirroring both into the
// URL means that reload (from any cause) restores exactly where the reader
// was, and it doubles as a shareable/bookmarkable link to a specific
// article. `source` selection's title isn't known until sources load, so
// it round-trips as an id and gets its title filled in by an effect below.
function selectionToQueryValue(selection: ViewSelection): string {
  if (selection.kind === 'saved') return `view:${selection.view}`
  if (selection.kind === 'source') return `source:${selection.sourceId}`
  return `folder:${encodeURIComponent(selection.folder)}`
}

function parseSelectionFromQuery(value: string | null): ViewSelection | null {
  if (!value) return null
  const sep = value.indexOf(':')
  if (sep === -1) return null
  const kind = value.slice(0, sep)
  const raw = value.slice(sep + 1)
  if (kind === 'view' && (raw === 'all' || raw === 'unread' || raw === 'starred')) {
    return { kind: 'saved', view: raw }
  }
  if (kind === 'source') {
    const sourceId = Number(raw)
    if (!Number.isNaN(sourceId)) return { kind: 'source', sourceId, title: '' }
  }
  if (kind === 'folder' && raw) {
    return { kind: 'folder', folder: decodeURIComponent(raw) }
  }
  return null
}

// Second, independent persistence layer for the same state, alongside the
// URL. Needed because third-party iOS browsers (Chrome, Firefox, Edge — all
// required by Apple to run on WKWebView) don't reliably sync
// history.replaceState back to their own native tab-restoration
// bookkeeping: when the OS reclaims a backgrounded tab's memory and the
// browser later reloads it, it can revert to an older URL than the page's
// actual last state. sessionStorage is a plain per-tab write with no
// dependency on the navigation stack, so it survives that class of bug.
// Stores the whole selection object (title included), so restoring from it
// skips the URL path's title-backfill-after-sources-load step entirely.
const STORAGE_KEY = 'reader-view-state'

function readStoredViewState(): { selection: ViewSelection | null; articleId: number | null } {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return { selection: null, articleId: null }
    const parsed = JSON.parse(raw)
    return { selection: parsed.selection ?? null, articleId: parsed.articleId ?? null }
  } catch {
    return { selection: null, articleId: null }
  }
}

export default function App() {
  const qc = useQueryClient()
  const [selection, setSelection] = useState<ViewSelection>(() => {
    const fromUrl = parseSelectionFromQuery(new URLSearchParams(window.location.search).get('sel'))
    return fromUrl ?? readStoredViewState().selection ?? { kind: 'saved', view: 'unread' }
  })
  const [openArticleId, setOpenArticleId] = useState<number | null>(() => {
    const fromUrl = new URLSearchParams(window.location.search).get('article')
    if (fromUrl) return Number(fromUrl)
    return readStoredViewState().articleId
  })
  const [cursorId, setCursorId] = useState<number | null>(null)
  const [configOpen, setConfigOpen] = useState(false)
  // 'closed' | 'add' | <source id being edited>
  const [sourceModal, setSourceModal] = useState<'closed' | 'add' | number>('closed')
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const [refreshReport, setRefreshReport] = useState<RefreshReport | null>(null)
  const [theme, setTheme] = useState<'dark' | 'light'>(
    () => (localStorage.getItem('reader-theme') as 'dark' | 'light' | null) ?? 'dark',
  )

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('reader-theme', theme)
  }, [theme])

  const sourcesQuery = useQuery({ queryKey: ['sources'], queryFn: api.sources })
  const llmStatusQuery = useQuery({ queryKey: ['llm-status'], queryFn: api.llmStatus })

  const needsLogin = sourcesQuery.error instanceof UnauthorizedError

  // A source selection restored from the URL only has the id (see
  // parseSelectionFromQuery) — fill in its title once sources have loaded.
  useEffect(() => {
    if (selection.kind === 'source' && selection.title === '' && sourcesQuery.data) {
      const src = sourcesQuery.data.find((s) => s.id === selection.sourceId)
      if (src) setSelection({ kind: 'source', sourceId: src.id, title: src.title })
    }
  }, [selection, sourcesQuery.data])

  // Keep both persistence layers mirroring current view + open article, so
  // a reload (from any cause, including a backgrounded tab's memory being
  // reclaimed) restores it. Two independent layers because the URL one
  // alone isn't reliable on iOS third-party browsers — see
  // readStoredViewState's comment above for why.
  useEffect(() => {
    const params = new URLSearchParams()
    params.set('sel', selectionToQueryValue(selection))
    if (openArticleId !== null) params.set('article', String(openArticleId))
    const qs = params.toString()
    window.history.replaceState(null, '', qs ? `${window.location.pathname}?${qs}` : window.location.pathname)
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ selection, articleId: openArticleId }))
    } catch {
      /* storage unavailable (private browsing etc) — URL sync above still covers normal reloads */
    }
  }, [selection, openArticleId])

  const listParams = useMemo(() => {
    if (selection.kind === 'saved') return { view: selection.view }
    if (selection.kind === 'source') return { view: 'all', source_id: selection.sourceId }
    return { view: 'all', folder: selection.folder }
  }, [selection])

  const articlesQuery = useInfiniteQuery({
    queryKey: ['articles', listParams],
    queryFn: ({ pageParam }) => api.articles({ ...listParams, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) =>
      lastPage.length === ARTICLES_PAGE_SIZE ? allPages.length * ARTICLES_PAGE_SIZE : undefined,
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

  const summarizeMutation = useMutation({
    mutationFn: (id: number) => api.summarize(id),
    onSuccess: (data, id) => {
      patchArticleCaches(qc, id, {
        llm_summary_html: data.summary_html,
        llm_summary_at: data.llm_summary_at,
      })
    },
  })

  const pullFullMutation = useMutation({
    mutationFn: (id: number) => api.pullFullArticle(id),
    onSuccess: (data, id) => {
      patchArticleCaches(qc, id, {
        content_html: data.content_html,
        hydrated_at: data.hydrated_at,
        hydrate_failed_at: data.hydrate_failed_at,
      })
    },
  })

  const markAllReadMutation = useMutation({
    mutationFn: (sourceId: number) => api.markAllRead(sourceId),
    onSuccess: (_data, sourceId) => {
      // Same in-place-patch philosophy as patchArticleCaches/adjustSourceUnread
      // above: flip is_read without removing rows from an already-rendered
      // Unread list, so a bulk mark-read doesn't yank the list out from
      // under the user mid-scroll.
      qc.setQueriesData<ArticlesPages>({ queryKey: ['articles'] }, (old) =>
        old && {
          ...old,
          pages: old.pages.map((page) =>
            page.map((a) => (a.source_id === sourceId ? { ...a, is_read: true } : a)),
          ),
        },
      )
      qc.setQueriesData<Article>({ queryKey: ['article'] }, (old) =>
        old && old.source_id === sourceId ? { ...old, is_read: true } : old,
      )
      qc.setQueryData<Source[]>(['sources'], (old) =>
        old?.map((s) => (s.id === sourceId ? { ...s, unread_count: 0 } : s)),
      )
    },
  })

  const markAllUnreadReadMutation = useMutation({
    mutationFn: () => api.markAllUnreadRead(),
    onSuccess: () => {
      // Global version of markAllReadMutation above — every article, not
      // scoped to one source_id, so every source's unread_count zeroes too.
      qc.setQueriesData<ArticlesPages>({ queryKey: ['articles'] }, (old) =>
        old && {
          ...old,
          pages: old.pages.map((page) => page.map((a) => ({ ...a, is_read: true }))),
        },
      )
      qc.setQueriesData<Article>({ queryKey: ['article'] }, (old) =>
        old ? { ...old, is_read: true } : old,
      )
      qc.setQueryData<Source[]>(['sources'], (old) =>
        old?.map((s) => ({ ...s, unread_count: 0 })),
      )
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

  const articles = useMemo(() => articlesQuery.data?.pages.flat() ?? [], [articlesQuery.data])

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

  if (needsLogin) {
    return <LoginPage onLoggedIn={() => qc.invalidateQueries()} />
  }

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
        onAddSource={() => setSourceModal('add')}
        onEditSource={(id) => setSourceModal(id)}
        mobileOpen={mobileSidebarOpen}
        onCloseMobile={() => setMobileSidebarOpen(false)}
        onMarkAllRead={(sourceId) => markAllReadMutation.mutate(sourceId)}
        onMarkAllUnreadRead={() => markAllUnreadReadMutation.mutate()}
        onLogout={() => api.logout().finally(() => qc.invalidateQueries())}
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
              <span className="main__title-count">
                {articles.length}
                {articlesQuery.hasNextPage ? '+' : ''} items
              </span>
            </div>
          </div>
        </div>
        <ArticleList
          articles={articles}
          selectedId={cursorId}
          onOpen={openArticle}
          hasMore={articlesQuery.hasNextPage}
          loadingMore={articlesQuery.isFetchingNextPage}
          onLoadMore={() => articlesQuery.fetchNextPage()}
        />
      </div>

      {openArticleId !== null && openArticleDisplayData && (
        <ArticleReader
          article={openArticleDisplayData}
          loading={openArticleQuery.isLoading}
          onClose={closeArticle}
          onToggleStar={() => toggleStarMutation.mutate(openArticleId)}
          onPullFull={() => pullFullMutation.mutate(openArticleId)}
          pullingFull={pullFullMutation.isPending}
          onSummarize={() => summarizeMutation.mutate(openArticleId)}
          summarizing={summarizeMutation.isPending}
          llmEnabled={llmStatusQuery.data?.enabled ?? false}
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

      {sourceModal !== 'closed' && (
        <SourceModal
          onClose={() => setSourceModal('closed')}
          editingSourceId={typeof sourceModal === 'number' ? sourceModal : undefined}
          onSaved={() => {
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

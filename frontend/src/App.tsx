import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query'
import {
  api,
  ForbiddenError,
  SIGNIN_URL,
  UnauthorizedError,
  type Article,
  type ArticleListItem,
  type Source,
} from './api'
import type { ViewSelection } from './types'
import { Sidebar } from './components/Sidebar'
import { ArticleList } from './components/ArticleList'
import { ArticleReader } from './components/ArticleReader'
import { SettingsDrawer } from './components/SettingsDrawer'

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

type ArticlesPages = { pages: ArticleListItem[][]; pageParams: number[] }

// Article detail fields the list cache doesn't carry (see api.ts's
// ArticleListItem) — dropped from a list-cache patch, with llm_summary_html
// collapsed to the has_summary flag the list *does* carry.
function toListPatch(patch: Partial<Article>): Partial<ArticleListItem> {
  const { content_html: _content_html, llm_summary_html, ...rest } = patch
  const listPatch: Partial<ArticleListItem> = rest
  if (llm_summary_html !== undefined) listPatch.has_summary = llm_summary_html !== null
  return listPatch
}

// Patch cached article data in place rather than invalidating — marking an
// article read should grey it out where it sits, not yank it out of the
// "Unread" list or refetch/reflow the whole page underneath the reader.
function patchArticleCaches(qc: QueryClient, id: number, patch: Partial<Article>) {
  const listPatch = toListPatch(patch)
  qc.setQueriesData<ArticlesPages>({ queryKey: ['articles'] }, (old) =>
    old && {
      ...old,
      pages: old.pages.map((page) => page.map((a) => (a.id === id ? { ...a, ...listPatch } : a))),
    },
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
  // null = closed. 'list' | 'add' pick which pane the Settings drawer opens
  // on — editing a specific feed happens entirely inside the drawer itself
  // (SettingsDrawer's own pane state), so there's nothing to track here for
  // that case.
  const [settings, setSettings] = useState<'list' | 'add' | null>(null)
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  // Brief friendly message shown when an anonymous visitor clicks an action
  // that's turned off in the public read-only demo (Star/Summarize/Download
  // — see the .toast rule in index.css). Auto-dismisses.
  const [notice, setNotice] = useState<string | null>(null)
  const noticeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const showNotice = useCallback((message: string) => {
    if (noticeTimerRef.current) clearTimeout(noticeTimerRef.current)
    setNotice(message)
    noticeTimerRef.current = setTimeout(() => setNotice(null), 3200)
  }, [])
  // Not namespaced per account, deliberately: dark/light is a property of
  // the device and the light you're reading in, not of who's signed in.
  const [theme, setTheme] = useState<'dark' | 'light'>(
    () => (localStorage.getItem('reader-theme') === 'dark' ? 'dark' : 'light'),
  )

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('reader-theme', theme)
    document
      .querySelector('meta[name="theme-color"]')
      ?.setAttribute('content', theme === 'light' ? '#faf6ee' : '#16140f')
  }, [theme])

  // iOS standalone paints the unreachable strip below the viewport from
  // html/body's background (see index.css) — keep it in sync with whichever
  // full-screen surface is on top so that strip never shows a color seam.
  useEffect(() => {
    document.documentElement.dataset.surface = openArticleId ? 'reader' : 'list'
  }, [openArticleId])

  const sourcesQuery = useQuery({ queryKey: ['sources'], queryFn: api.sources })
  const llmStatusQuery = useQuery({ queryKey: ['llm-status'], queryFn: api.llmStatus })
  // retry:false so a 401 shows the login screen immediately instead of
  // after the default retry backoff; staleTime:Infinity because identity
  // only changes by logging out, which clears the whole cache below.
  const meQuery = useQuery({
    queryKey: ['me'],
    queryFn: api.me,
    retry: false,
    staleTime: Infinity,
  })

  // Only reachable in `required` mode (not this deployment's default) —
  // `optional` mode lets ['sources'] and ['me'] both resolve anonymously
  // (see docs/WORKLOG.md, 2026-09-05), so a 401 here means the deployment
  // truly requires signing in, not just that nobody has yet.
  const needsLogin =
    meQuery.error instanceof UnauthorizedError || sourcesQuery.error instanceof UnauthorizedError

  // ['me'] resolving with a null id means optional-mode anonymous
  // browsing — content renders, but a "Sign in with Google" affordance
  // shows in the sidebar instead of an identity.
  const isAnonymous = meQuery.data?.id == null

  // Completed Google sign-in with an account that isn't on
  // READER_ALLOWED_EMAILS (Apache accepts any Google account; only this
  // app's own allowlist rejects it — see pchauth's starlette_adapter).
  // Surfaced distinctly from plain `isAnonymous` so the feed page can say
  // *why* — "signed in as X, not authorized" — instead of looking
  // identical to never having signed in at all (added 2026-09-07, after
  // the "blank error page" report).
  const wrongAccountEmail =
    meQuery.error instanceof ForbiddenError
      ? meQuery.error.email
      : sourcesQuery.error instanceof ForbiddenError
        ? sourcesQuery.error.email
        : null

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
    // An article body never changes after hydration (read/star mutations
    // patch the cache in place — see patchArticleCaches), so once fetched
    // there's no reason to ever refetch or evict it just from time passing.
    staleTime: Infinity,
    gcTime: 30 * 60 * 1000,
  })

  const refreshMutation = useMutation({
    mutationFn: () => api.refresh(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['sources'] })
      qc.invalidateQueries({ queryKey: ['articles'] })
    },
  })

  const markReadMutation = useMutation({
    mutationFn: (article: ArticleListItem) => api.markRead(article.id),
    onSuccess: (_data, article) => {
      patchArticleCaches(qc, article.id, { is_read: true })
      adjustSourceUnread(qc, article.source_id, -1)
    },
  })

  const toggleReadMutation = useMutation({
    mutationFn: (article: ArticleListItem) => api.toggleRead(article.id),
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

  // Both `#article-list` and `#sidebar-scroll` empty their contents while
  // the reader is open (see the `hidden`/`inert` handling in ArticleList and
  // Sidebar) so VoiceOver has nothing behind the reader to fall back onto —
  // but an emptied scroll container clamps its own scrollTop to 0, and nothing
  // restores that automatically once content comes back. Captured here, at
  // the one place every "open an article" path funnels through (a row
  // click and the j/k+o/Enter keyboard shortcut below both call
  // `openArticle`), and written back in `closeArticle`.
  const savedListScrollRef = useRef(0)
  const savedSidebarScrollRef = useRef(0)

  const openArticle = useCallback(
    (article: ArticleListItem) => {
      savedListScrollRef.current = document.getElementById('article-list')?.scrollTop ?? 0
      savedSidebarScrollRef.current = document.getElementById('sidebar-scroll')?.scrollTop ?? 0
      setOpenArticleId(article.id)
      setCursorId(article.id)
      // Anonymous visitors browse read-only — read state can't persist for
      // them server-side (require_user_id rejects the write), so skip the
      // call entirely rather than firing a request that's just going to 401.
      if (!article.is_read && !isAnonymous) markReadMutation.mutate(article)
    },
    [markReadMutation, isAnonymous],
  )

  // Closing unmounts the reader (which held VO/keyboard focus), so without
  // hand-off the focus silently drops to the top of the document — send it
  // back to the row the article was opened from, matching where j/k leaves
  // the cursor (see the `data-article-id` lookup in the keydown handler
  // below), and restore both scroll containers to where openArticle found
  // them. rAF because none of this exists/has its content back — the row,
  // and the list/sidebar's real children — until this state update commits.
  const closeArticle = useCallback(() => {
    setOpenArticleId((id) => {
      if (id !== null) {
        requestAnimationFrame(() => {
          const row = document.querySelector<HTMLElement>(`[data-article-id="${id}"]`)
          row?.focus()
          const list = document.getElementById('article-list')
          if (list) list.scrollTop = savedListScrollRef.current
          const sidebarScroll = document.getElementById('sidebar-scroll')
          if (sidebarScroll) sidebarScroll.scrollTop = savedSidebarScrollRef.current
        })
      }
      return null
    })
  }, [])

  const articles = useMemo(() => articlesQuery.data?.pages.flat() ?? [], [articlesQuery.data])

  // openArticleQuery.data is undefined for a beat whenever openArticleId
  // changes (React Query resets it while the new query loads) — without a
  // fallback, ArticleReader would unmount and flash the list underneath on
  // every prev/next navigation. The list item (ArticleListItem) no longer
  // carries content_html/llm_summary_html (see api.ts), so the placeholder
  // built from it renders blank body fields; ArticleReader's `loading` prop
  // (driven by openArticleQuery.isLoading below) covers that gap with its
  // skeleton rather than flashing empty content. The query result swaps in
  // once it lands.
  const openArticleListItem = articles.find((a) => a.id === openArticleId)
  const openArticleDisplayData: Article | undefined =
    openArticleQuery.data ??
    (openArticleListItem && {
      ...openArticleListItem,
      content_html: '',
      llm_summary_html: null,
    })

  // Drives `inert` on the sidebar and article list while the reader overlay
  // sits on top of them — without it, VoiceOver's swipe/rotor navigation
  // walks straight past the fixed-position overlay into content that isn't
  // visible on screen (both panes stay mounted underneath; see App.tsx's
  // render below and index.css's `.reader-overlay`).
  const readerOpen = openArticleId !== null

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
      } else if (e.key === 'm' && !isAnonymous) {
        const current = articles.find((a) => a.id === cursorId)
        if (current) toggleReadMutation.mutate(current)
      } else if (e.key === 'r' && !isAnonymous) {
        refreshMutation.mutate()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [articles, cursorId, openArticleId, openArticle, toggleReadMutation, refreshMutation, isAnonymous])

  const totalUnread = (sourcesQuery.data ?? []).reduce((n, s) => n + s.unread_count, 0)
  const totalStarred = articles.filter((a) => a.is_starred).length

  const headerTitle =
    selection.kind === 'saved'
      ? VIEW_TITLES[selection.view]
      : selection.kind === 'source'
        ? selection.title
        : selection.folder

  if (needsLogin) {
    // `required` mode only — Apache owns login entirely in
    // trusted_header mode, so there's nothing for this app to render but
    // a link that triggers the gateway's redirect (see
    // docs/superpowers/specs/2026-09-05-consolidated-login-design.md in
    // the pchauth repo). /reader/login is a dedicated Apache <Location>
    // that overrides the vhost-wide OIDCUnAuthAction pass back to `auth`
    // (same pattern as /pages/) — mod_auth_openidc has no query-string
    // trigger to force a login on a `pass` location, so a real protected
    // sub-path is the only mechanism. Must be a real `<a href>` (full
    // top-level navigation), not a fetch/JS redirect: a PWA installed to
    // an iOS home screen has no address bar, so this link is the only
    // way to reach Google sign-in from inside it.
    return (
      <div className="signed-out-shell">
        <p>Sign-in required.</p>
        <a className="signin-link" href={SIGNIN_URL}>
          Sign in with Google
        </a>
      </div>
    )
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
        onOpenConfig={() => setSettings('list')}
        theme={theme}
        onToggleTheme={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}
        mobileOpen={mobileSidebarOpen}
        onCloseMobile={() => setMobileSidebarOpen(false)}
        onMarkAllRead={(sourceId) => markAllReadMutation.mutate(sourceId)}
        onMarkAllUnreadRead={() => markAllUnreadReadMutation.mutate()}
        username={meQuery.data?.username ?? undefined}
        inert={readerOpen}
        // Nothing in the sidebar mutates anything for an anonymous
        // visitor — feeds and read state can't be changed without an
        // account (see require_user_id server-side). Hiding these
        // controls entirely (rather than showing them disabled with an
        // error) is simpler and more honest than exposing buttons that
        // would just 401.
        readOnly={isAnonymous}
      />

      <div className="main" inert={readerOpen}>
        {wrongAccountEmail ? (
          <div className="readonly-banner readonly-banner--warn">
            ⚠️ Signed in as {wrongAccountEmail}, which isn't authorized for this reader. You're
            viewing the public read-only demo instead.
          </div>
        ) : (
          isAnonymous && (
            <div className="readonly-banner">
              👋 You're viewing a public demo of OpenReader — browse and read freely. This is a
              read-only view, so marking articles read, starring, summarizing, and changing feeds
              aren't available.
            </div>
          )
        )}
        {/* `inert` alone isn't a reliable guarantee on iOS: a documented
            Safari 26 VoiceOver regression lets the virtual cursor keep
            navigating into content that's still in the DOM even when it's
            `inert` (https://discussions.apple.com/thread/256161078) — when
            focus is lost, VoiceOver falls back to whatever's nearest by
            screen position to where it last was, and an inert-but-present
            article row is still a candidate (this is almost certainly why
            titles from the list kept getting read with the reader open).
            `inert` is kept for browsers where it behaves correctly (and for
            pointer/keyboard blocking); not rendering the header, and
            handing ArticleList `hidden` to empty out its rows, closes the
            gap for everyone else — there's nothing left for VO to fall back
            to. ArticleList itself stays mounted (see its `hidden` prop) so
            the list's own scroll position survives the reader opening and
            closing, rather than resetting to the top every time. */}
        {!readerOpen && (
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
        )}
        <ArticleList
          articles={articles}
          selectedId={cursorId}
          onOpen={openArticle}
          hasMore={articlesQuery.hasNextPage}
          loadingMore={articlesQuery.isFetchingNextPage}
          onLoadMore={() => articlesQuery.fetchNextPage()}
          listKey={selectionToQueryValue(selection)}
          onRefresh={() => refreshMutation.mutate()}
          refreshing={refreshMutation.isPending}
          refreshEnabled={!isAnonymous}
          loading={articlesQuery.isPending}
          hidden={readerOpen}
        />
      </div>

      {readerOpen && openArticleDisplayData && (
        <ArticleReader
          article={openArticleDisplayData}
          loading={openArticleQuery.isLoading}
          onClose={closeArticle}
          onToggleStar={() =>
            isAnonymous
              ? showNotice("Starring isn't available in this read-only demo — sign in to save articles.")
              : toggleStarMutation.mutate(openArticleId)
          }
          onPullFull={() =>
            isAnonymous
              ? showNotice("Pulling the full article isn't available in this read-only demo.")
              : pullFullMutation.mutate(openArticleId)
          }
          pullingFull={pullFullMutation.isPending}
          onSummarize={() =>
            isAnonymous
              ? showNotice('AI summaries are turned off in this read-only demo to avoid unexpected costs.')
              : summarizeMutation.mutate(openArticleId)
          }
          summarizing={summarizeMutation.isPending}
          llmEnabled={llmStatusQuery.data?.enabled ?? false}
          onPrev={goPrev}
          onNext={goNext}
          hasPrev={hasPrev}
          hasNext={hasNext}
        />
      )}

      {settings !== null && (
        <SettingsDrawer
          sources={sourcesQuery.data ?? []}
          onClose={() => setSettings(null)}
          initialPane={settings === 'add' ? { kind: 'add' } : { kind: 'list' }}
          onSaved={() => {
            qc.invalidateQueries({ queryKey: ['sources'] })
            qc.invalidateQueries({ queryKey: ['articles'] })
          }}
        />
      )}

      {notice && <div className="toast">{notice}</div>}
    </div>
  )
}

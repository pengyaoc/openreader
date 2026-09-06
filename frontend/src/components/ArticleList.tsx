import { useEffect, useRef, useState } from 'react'
import type { ArticleListItem } from '../api'

// Pull-to-refresh tuning — see docs/superpowers/specs/2026-08-21-pull-to-refresh-design.md.
const PULL_RESISTANCE = 0.5
const PULL_MAX = 96
const PULL_THRESHOLD = 64

function timeAgo(iso: string | null): string {
  if (!iso) return ''
  const then = new Date(iso).getTime()
  const diffMin = Math.round((Date.now() - then) / 60000)
  if (diffMin < 1) return 'now'
  if (diffMin < 60) return `${diffMin}m`
  const diffHr = Math.round(diffMin / 60)
  if (diffHr < 24) return `${diffHr}h`
  const diffDay = Math.round(diffHr / 24)
  if (diffDay < 30) return `${diffDay}d`
  return new Date(iso).toLocaleDateString()
}

// Touch-driven pull-to-refresh on a scroll container. Raw DOM listeners
// (not React's synthetic touch handlers) because touchmove needs
// { passive: false } to preventDefault() the native rubber-band bounce
// while a pull is in progress — otherwise the custom indicator visually
// fights the browser's own overscroll.
function usePullToRefresh(
  scrollRef: React.RefObject<HTMLDivElement | null>,
  onRefresh: () => void,
  refreshing: boolean,
  enabled: boolean,
) {
  const [pullDistance, setPullDistance] = useState(0)
  const startYRef = useRef<number | null>(null)
  const refreshingRef = useRef(refreshing)
  refreshingRef.current = refreshing

  useEffect(() => {
    if (!enabled) {
      startYRef.current = null
      setPullDistance(0)
      return
    }

    const el = scrollRef.current
    if (!el) return

    function onTouchStart(e: TouchEvent) {
      if (refreshingRef.current || el!.scrollTop !== 0) {
        startYRef.current = null
        return
      }
      startYRef.current = e.touches[0].clientY
    }

    function onTouchMove(e: TouchEvent) {
      if (startYRef.current === null) return
      const rawDelta = e.touches[0].clientY - startYRef.current
      if (rawDelta <= 0) {
        setPullDistance(0)
        return
      }
      e.preventDefault()
      setPullDistance(Math.min(rawDelta * PULL_RESISTANCE, PULL_MAX))
    }

    function onTouchEnd() {
      if (startYRef.current === null) return
      startYRef.current = null
      setPullDistance((distance) => {
        if (distance >= PULL_THRESHOLD) {
          onRefresh()
          // Held at the threshold (not the raw drag distance) so the
          // indicator doesn't jump/snap once the finger lifts — it settles
          // to the "refreshing" resting height instead.
          return PULL_THRESHOLD
        }
        return 0
      })
    }

    el.addEventListener('touchstart', onTouchStart, { passive: true })
    el.addEventListener('touchmove', onTouchMove, { passive: false })
    el.addEventListener('touchend', onTouchEnd, { passive: true })
    el.addEventListener('touchcancel', onTouchEnd, { passive: true })
    return () => {
      el.removeEventListener('touchstart', onTouchStart)
      el.removeEventListener('touchmove', onTouchMove)
      el.removeEventListener('touchend', onTouchEnd)
      el.removeEventListener('touchcancel', onTouchEnd)
    }
  }, [scrollRef, onRefresh, enabled])

  // Refresh finished — animate the indicator back to resting.
  useEffect(() => {
    if (!refreshing) setPullDistance(0)
  }, [refreshing])

  return pullDistance
}

function PullIndicator({ pullDistance, refreshing }: { pullDistance: number; refreshing: boolean }) {
  if (pullDistance === 0 && !refreshing) return null
  const ready = pullDistance >= PULL_THRESHOLD
  const label = refreshing ? 'Refreshing…' : ready ? 'Release to refresh' : 'Pull to refresh'
  const rotation = refreshing ? 0 : Math.min((pullDistance / PULL_THRESHOLD) * 180, 180)
  return (
    <div
      className="pull-indicator"
      style={{ height: refreshing ? PULL_THRESHOLD : pullDistance }}
    >
      <span
        className={`pull-indicator__icon ${refreshing ? 'spinning' : ''} ${ready ? 'ready' : ''}`}
        style={refreshing ? undefined : { transform: `rotate(${rotation}deg)` }}
      >
        ⟳
      </span>
      <span className={`pull-indicator__label ${ready || refreshing ? 'ready' : ''}`}>{label}</span>
    </div>
  )
}

function ArticleRowSkeleton() {
  return (
    <div className="article-row article-row--skeleton" aria-hidden="true">
      <span className="article-row__unread-dot is-read" />
      <div className="article-row__main">
        <div className="skeleton-line skeleton-line--meta" />
        <div className="skeleton-line skeleton-line--title" />
        <div className="skeleton-line skeleton-line--excerpt" />
      </div>
    </div>
  )
}

interface Props {
  articles: ArticleListItem[]
  selectedId: number | null
  onOpen: (article: ArticleListItem) => void
  hasMore?: boolean
  loadingMore?: boolean
  onLoadMore?: () => void
  // Identifies which view/source/folder is currently showing (e.g.
  // "view:unread", "source:12") — ArticleList stays mounted across
  // switches between them, so without this the scroll container keeps
  // whatever scrollTop the previous list was left at.
  listKey: string
  onRefresh: () => void
  refreshing: boolean
  // Anonymous read-only browsing cannot start a refresh. Disable the
  // gesture itself as well as its callback so a pull cannot leave the list
  // stuck at the "Release to refresh" resting height.
  refreshEnabled?: boolean
  // True while the initial fetch for the current view is still in flight —
  // distinct from `refreshing` (a user-triggered re-fetch of an already
  // loaded list). Without this the list rendered "Nothing here yet" for the
  // whole first fetch, which reads as broken rather than loading.
  loading?: boolean
  // True while the reader overlay is open on top of the list. Renders the
  // scroll container with no children — nothing left for VoiceOver to read
  // or fall back to (see App.tsx's `readerOpen` comment) — while keeping
  // the container's own DOM node (and its scrollTop) alive throughout, so
  // closing the reader doesn't scroll the list back to the top.
  hidden?: boolean
}

export function ArticleList({
  articles,
  selectedId,
  onOpen,
  hasMore,
  loadingMore,
  onLoadMore,
  listKey,
  onRefresh,
  refreshing,
  refreshEnabled = true,
  loading,
  hidden,
}: Props) {
  const scrollRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0 })
  }, [listKey])
  const pullDistance = usePullToRefresh(scrollRef, onRefresh, refreshing, refreshEnabled)

  // Checked first, ahead of the loading/empty states below — same
  // "article-list" id/ref every branch uses, so React keeps this as one
  // continuous DOM node (and scroll position) as it switches between them.
  if (hidden) {
    return <div className="article-list" id="article-list" ref={scrollRef} />
  }

  if (articles.length === 0 && loading) {
    return (
      <div className="article-list" id="article-list" ref={scrollRef}>
        <PullIndicator pullDistance={pullDistance} refreshing={refreshing} />
        {Array.from({ length: 8 }, (_, i) => (
          <ArticleRowSkeleton key={i} />
        ))}
      </div>
    )
  }

  if (articles.length === 0) {
    return (
      <div className="article-list" id="article-list" ref={scrollRef}>
        <PullIndicator pullDistance={pullDistance} refreshing={refreshing} />
        <div className="empty-state">
          <div className="empty-state__icon">◧</div>
          <div className="empty-state__title">Nothing here yet</div>
          <div className="empty-state__hint">
            Press <kbd>r</kbd> or hit Refresh to pull the latest items, or adjust your filters in
            Configure.
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="article-list" id="article-list" ref={scrollRef}>
      <PullIndicator pullDistance={pullDistance} refreshing={refreshing} />
      {articles.map((a) => (
        <div
          key={a.id}
          data-article-id={a.id}
          className={`article-row ${a.is_read ? 'read' : ''} ${a.id === selectedId ? 'selected' : ''}`}
          onClick={() => onOpen(a)}
          // Focusable only for the row the reader was last opened from, so
          // closing the reader has somewhere legitimate to hand focus back
          // to (App.tsx's closeArticle) — see the plan's "reader-only"
          // scope note: rows aren't made keyboard-activatable in general.
          tabIndex={a.id === selectedId ? -1 : undefined}
        >
          <span className={`article-row__unread-dot ${a.is_read ? 'is-read' : ''}`} />
          <div className="article-row__main">
            <div className="article-row__meta">
              <span className="article-row__source">{a.source_title}</span>
              <span>·</span>
              <span>{timeAgo(a.published_at)}</span>
            </div>
            <h3 className="article-row__title">{a.title}</h3>
            {a.excerpt && <p className="article-row__excerpt">{a.excerpt}</p>}
          </div>
          {a.top_image_path && (
            <img className="article-row__thumb" src={a.top_image_path} alt="" loading="lazy" />
          )}
          {a.is_starred && <span className="article-row__star">★</span>}
        </div>
      ))}
      {hasMore && (
        <button className="load-more-btn" onClick={onLoadMore} disabled={loadingMore}>
          {loadingMore ? 'Loading…' : 'Load more'}
        </button>
      )}
    </div>
  )
}

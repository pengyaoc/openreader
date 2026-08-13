import type { Article } from '../api'

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

interface Props {
  articles: Article[]
  selectedId: number | null
  onOpen: (article: Article) => void
  onToggleRead: (article: Article) => void
  hasMore?: boolean
  loadingMore?: boolean
  onLoadMore?: () => void
}

export function ArticleList({
  articles,
  selectedId,
  onOpen,
  onToggleRead,
  hasMore,
  loadingMore,
  onLoadMore,
}: Props) {
  if (articles.length === 0) {
    return (
      <div className="empty-state">
        <div className="empty-state__icon">◧</div>
        <div className="empty-state__title">Nothing here yet</div>
        <div className="empty-state__hint">
          Press <kbd>r</kbd> or hit Refresh to pull the latest items, or adjust your filters in
          Configure.
        </div>
      </div>
    )
  }

  return (
    <div className="article-list" id="article-list">
      {articles.map((a) => (
        <div
          key={a.id}
          data-article-id={a.id}
          className={`article-row ${a.is_read ? 'read' : ''} ${a.id === selectedId ? 'selected' : ''}`}
          onClick={() => onOpen(a)}
        >
          <span className={`article-row__unread-dot ${a.is_read ? 'is-read' : ''}`} />
          <div className="article-row__main">
            <div className="article-row__meta">
              <span className="article-row__source">{a.source_title}</span>
              <span>·</span>
              <span>{timeAgo(a.published_at)}</span>
              {a.origin === 'llm' && <span className="badge badge--generated">Generated</span>}
            </div>
            <h3 className="article-row__title">{a.title}</h3>
            {a.excerpt && <p className="article-row__excerpt">{a.excerpt}</p>}
          </div>
          {a.top_image_path && (
            <img className="article-row__thumb" src={a.top_image_path} alt="" loading="lazy" />
          )}
          <div className="article-row__actions">
            {a.is_starred && <span className="article-row__star">★</span>}
            <button
              className={`article-row__mark-read ${a.is_read ? 'active' : ''}`}
              onClick={(e) => {
                e.stopPropagation()
                onToggleRead(a)
              }}
              title={a.is_read ? 'Mark as unread' : 'Mark as read'}
            >
              ✓
            </button>
          </div>
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

import { useEffect, useId, useRef, useState, type RefObject } from 'react'
import { API_BASE, type Article, type ArticleListItem } from '../api'
import { useSwipeNav } from '../useSwipeNav'

type ViewMode = 'full' | 'summary'

interface Props {
  article: Article
  loading: boolean
  onClose: () => void
  onToggleStar: () => void
  onToggleRead: () => void
  onPullFull: () => void
  pullingFull: boolean
  onSummarize: () => void
  summarizing: boolean
  llmEnabled: boolean
  onPrev: () => void
  onNext: () => void
  hasPrev: boolean
  hasNext: boolean
  /** Neighbors for the swipe peek cards. Null with hasNext set means the
   *  next article is on a list page that hasn't loaded yet. */
  prevItem: ArticleListItem | null
  nextItem: ArticleListItem | null
}

function fullDate(iso: string | null): string {
  if (!iso) return ''
  return new Date(iso).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
}

// Article bodies are sanitized server-side at ingest time with every
// <img src> already rewritten to the root-absolute path "/api/img?url=..."
// (backend/app/ingest/textutil.py's proxy_image_urls) — that happened once,
// so existing rows in the DB carry the unprefixed path regardless of which
// base this build is served from. Prefixing it here at render time, rather
// than in the backend or via a migration, covers both existing and future
// rows with one change. A no-op when API_BASE is '' (root deploy/dev).
function withApiBase(html: string): string {
  if (!API_BASE) return html
  return html.replaceAll('"/api/img?', `"${API_BASE}/api/img?`)
}

export function ArticleReader({
  article,
  loading,
  onClose,
  onToggleStar,
  onToggleRead,
  onPullFull,
  pullingFull,
  onSummarize,
  summarizing,
  llmEnabled,
  onPrev,
  onNext,
  hasPrev,
  hasNext,
  prevItem,
  nextItem,
}: Props) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      else if (e.key === 'ArrowLeft' && hasPrev) onPrev()
      else if (e.key === 'ArrowRight' && hasNext) onNext()
      else if (e.key === 'm' && !e.metaKey && !e.ctrlKey && !e.altKey) onToggleRead()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose, onPrev, onNext, onToggleRead, hasPrev, hasNext])

  const scrollRef = useRef<HTMLDivElement>(null)
  const stageRef = useRef<HTMLDivElement>(null)
  const trackRef = useRef<HTMLDivElement>(null)
  useSwipeNav({
    trackRef,
    surfaceRef: stageRef,
    canPrev: hasPrev,
    canNext: hasNext,
    onPrev,
    onNext,
    articleKey: article.id,
  })
  const hint = usePageTurnHint(stageRef, hasPrev || hasNext)
  const titleRef = useRef<HTMLHeadingElement>(null)
  const titleId = useId()
  const [viewMode, setViewMode] = useState<ViewMode>('full')
  // ArticleReader stays mounted across prev/next, and Full/Summary toggles
  // within the same article, so the scroll container otherwise keeps
  // whatever scrollTop it was left at. Reset on either changing.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0 })
  }, [article.id, viewMode])
  // The background is made `inert` while this overlay is open (App.tsx), so
  // without an explicit focus move here VO/keyboard focus is left stranded
  // on the now-inert article row. Focus lands on the *title*, not the
  // overlay container or the toolbar above it — a screen reader's forward
  // navigation starts from wherever focus is and reads on from there, so
  // focusing the container (whose first descendant in the DOM is the
  // toolbar) meant Close/Pull-full/Summarize/Star were announced before the
  // article itself. The toolbar is still reachable by swiping backward.
  useEffect(() => {
    titleRef.current?.focus()
  }, [article.id])
  // Tracks whether the *currently open* article already had a summary the
  // last time we checked — lets the effect below tell "freshly generated
  // while this article is open" (auto-switch to Summary) apart from "this
  // article already had a cached summary when it was opened" (stay on
  // Full, matching the reset below, until the user toggles manually).
  const hadSummaryRef = useRef(false)

  // Always land on the full view when switching articles — a summary
  // toggled on for the previous article shouldn't carry over — and reset
  // the "had a summary" baseline to match the newly opened article.
  useEffect(() => {
    setViewMode('full')
    hadSummaryRef.current = Boolean(article.llm_summary_html)
  }, [article.id])

  // Auto-switch to the summary the moment it first appears for the article
  // currently open (freshly generated, arriving via the query-cache patch
  // in App.tsx) rather than requiring a second click.
  useEffect(() => {
    if (article.llm_summary_html && !hadSummaryRef.current) {
      setViewMode('summary')
    }
    hadSummaryRef.current = Boolean(article.llm_summary_html)
  }, [article.llm_summary_html])

  return (
    <div className="reader-overlay" role="dialog" aria-modal="true" aria-labelledby={titleId}>
      <div className="reader-bar">
        <div className="reader-bar__left">
          <button className="icon-btn" onClick={onClose} title="Close (Esc)">
            ←
          </button>
          <span className="reader-source">{article.source_title}</span>
        </div>
        <div className="reader-bar__right">
          {article.url && !article.hydrated_at && !article.hydrate_failed_at && (
            <button
              className={`icon-btn ${pullingFull ? 'icon-btn--busy' : ''}`}
              onClick={onPullFull}
              disabled={pullingFull}
              title={pullingFull ? 'Pulling full article…' : 'Pull full article'}
            >
              {pullingFull ? <span className="spinner" /> : '⤓'}
            </button>
          )}
          {llmEnabled && (
            <button
              className={`icon-btn ${summarizing ? 'icon-btn--busy' : ''} ${
                viewMode === 'summary' ? 'active' : ''
              }`}
              onClick={() => {
                if (!article.llm_summary_html) onSummarize()
                else setViewMode((m) => (m === 'summary' ? 'full' : 'summary'))
              }}
              disabled={summarizing}
              title={
                summarizing
                  ? 'Summarizing…'
                  : viewMode === 'summary'
                    ? 'Show full article'
                    : 'Summarize'
              }
            >
              {summarizing ? <span className="spinner" /> : '✨'}
            </button>
          )}
          <button
            className={`icon-btn ${article.is_starred ? 'active' : ''}`}
            onClick={onToggleStar}
            title="Star"
          >
            ★
          </button>
          {/* Opening an article marks it read; this undoes that (or redoes
              it) without leaving the reader. Moving on to prev/next doesn't
              re-mark it, so an article flagged here stays unread. */}
          <button
            className={`icon-btn ${article.is_read ? '' : 'active'}`}
            onClick={onToggleRead}
            title={article.is_read ? 'Mark as unread (m)' : 'Mark as read (m)'}
            aria-pressed={!article.is_read}
            aria-label="Unread"
          >
            ●
          </button>
          {article.url && (
            <a
              className="icon-btn"
              href={article.url}
              target="_blank"
              rel="noreferrer"
              title="Open original"
            >
              ↗
            </a>
          )}
        </div>
      </div>

      <div className="reader-stage" ref={stageRef}>
        {/* Margin strips for mouse readers (hidden on touch and on windows
            too narrow to fit them beside the column; see index.css). Kept
            out of the track so they don't slide with a swipe. */}
        {hasPrev && (
          <button
            className="reader-margin-nav reader-margin-nav--prev"
            onClick={onPrev}
            aria-label="Previous article"
            title="Previous article (←)"
          >
            ‹
          </button>
        )}
        {hasNext && (
          <button
            className="reader-margin-nav reader-margin-nav--next"
            onClick={onNext}
            aria-label="Next article"
            title="Next article (→)"
          >
            ›
          </button>
        )}
        <div className={`reader-track ${hint === 'touch' ? 'hint-nudge' : ''}`} ref={trackRef}>
          {hasPrev && <PeekCard side="prev" item={prevItem} />}
          {hasNext && <PeekCard side="next" item={nextItem} />}
          <div className="reader-scroll" ref={scrollRef}>
            <article className="reader-article">
              <h1 id={titleId} ref={titleRef} tabIndex={-1} className="reader-article__title">
                {article.title}
              </h1>
              <div className="reader-article__meta">
                {/* Not every source has a per-article author (feed-level bylines,
                    Twitter-sourced RSS, etc. often omit it) — falling back to the
                    source's own name keeps this line from just going blank,
                    which read as a rendering bug rather than "this item has no
                    byline". */}
                {(article.author || article.source_title) && (
                  <>{article.author || article.source_title} · </>
                )}
                {fullDate(article.published_at)}
                {article.url && (
                  <>
                    {' · '}
                    <a href={article.url} target="_blank" rel="noreferrer">
                      original source
                    </a>
                  </>
                )}
              </div>

              {loading ? (
                <div className="reader-skeleton">
                  <div className="reader-skeleton__line" style={{ width: '95%' }} />
                  <div className="reader-skeleton__line" style={{ width: '88%' }} />
                  <div className="reader-skeleton__line" style={{ width: '92%' }} />
                  <div className="reader-skeleton__line" style={{ width: '60%' }} />
                </div>
              ) : viewMode === 'summary' && article.llm_summary_html ? (
                <div
                  className="reader-body"
                  dangerouslySetInnerHTML={{ __html: article.llm_summary_html }}
                />
              ) : (
                <div
                  className="reader-body"
                  dangerouslySetInnerHTML={{
                    __html: withApiBase(article.content_html || `<p>${article.excerpt}</p>`),
                  }}
                />
              )}

              {(hasPrev || hasNext) && (
                <div className="reader-footer-nav">
                  {hasPrev ? (
                    <button className="icon-btn" onClick={onPrev} title="Previous article (←)">
                      ‹
                    </button>
                  ) : (
                    <span />
                  )}
                  {hasNext && (
                    <button className="icon-btn" onClick={onNext} title="Next article (→)">
                      ›
                    </button>
                  )}
                </div>
              )}
            </article>
          </div>
        </div>
      </div>
      {hint && (
        <div className="page-turn-hint" role="status">
          {hint === 'touch'
            ? 'Swipe to change articles'
            : hint === 'margins'
              ? 'Click the margins, swipe, or use ← → to change articles'
              : 'Swipe or use ← → to change articles'}
        </div>
      )}
    </div>
  )
}

// What the swipe reveals beside the article: just enough of the neighbor
// (source, title, date from the already-loaded list) to show where the
// swipe goes. Rendering the whole neighbor article would put its top beside
// the middle of the current one.
function PeekCard({ side, item }: { side: 'prev' | 'next'; item: ArticleListItem | null }) {
  return (
    <div className={`reader-peek reader-peek--${side}`} aria-hidden="true" inert>
      <div className="reader-peek__inner">
        <div className="reader-peek__label">{side === 'next' ? 'Next' : 'Previous'}</div>
        {item ? (
          <>
            <div className="reader-peek__source">{item.source_title}</div>
            <div className="reader-peek__title">{item.title}</div>
            <div className="reader-peek__date">{fullDate(item.published_at)}</div>
          </>
        ) : (
          <div className="reader-peek__title reader-peek__title--loading">Loading more…</div>
        )}
      </div>
    </div>
  )
}

type HintKind = 'touch' | 'margins' | 'keys'

// First time only, like Summra's showPageTurnHintOnce: tell a touch reader
// the article swipes (label plus a nudge), or a mouse reader about the
// margins and keys. Gone after 6s or the first interaction.
function usePageTurnHint(
  stageRef: RefObject<HTMLDivElement | null>,
  navigable: boolean,
): HintKind | null {
  const [hint, setHint] = useState<HintKind | null>(null)
  useEffect(() => {
    if (!navigable || hint !== null) return
    const mouse = window.matchMedia('(hover: hover) and (pointer: fine)').matches
    const key = mouse ? 'reader_nav_hint_desktop' : 'reader_nav_hint_touch'
    try {
      if (localStorage.getItem(key)) return
      localStorage.setItem(key, '1')
    } catch {
      return
    }
    const wide = (stageRef.current?.clientWidth ?? 0) >= 920
    setHint(mouse ? (wide ? 'margins' : 'keys') : 'touch')
  }, [navigable, hint, stageRef])
  useEffect(() => {
    if (!hint) return
    const events = ['touchstart', 'mousedown', 'wheel', 'keydown'] as const
    const dismiss = () => setHint(null)
    const timer = window.setTimeout(dismiss, 6000)
    events.forEach((type) => document.addEventListener(type, dismiss, { capture: true, passive: true }))
    return () => {
      window.clearTimeout(timer)
      events.forEach((type) => document.removeEventListener(type, dismiss, { capture: true }))
    }
  }, [hint])
  return hint
}

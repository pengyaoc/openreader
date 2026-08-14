import { useEffect, useRef, useState } from 'react'
import { API_BASE, type Article } from '../api'

interface Citation {
  title: string
  url: string
}

type ViewMode = 'full' | 'summary'

interface Props {
  article: Article
  loading: boolean
  onClose: () => void
  onToggleStar: () => void
  onSummarize: () => void
  summarizing: boolean
  llmEnabled: boolean
  onPrev: () => void
  onNext: () => void
  hasPrev: boolean
  hasNext: boolean
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
  onSummarize,
  summarizing,
  llmEnabled,
  onPrev,
  onNext,
  hasPrev,
  hasNext,
}: Props) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      else if (e.key === 'ArrowLeft' && hasPrev) onPrev()
      else if (e.key === 'ArrowRight' && hasNext) onNext()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose, onPrev, onNext, hasPrev, hasNext])

  const scrollRef = useRef<HTMLDivElement>(null)
  // ArticleReader stays mounted across prev/next — only `article` changes —
  // so the scroll container otherwise keeps whatever scrollTop the previous
  // article was left at. Reset on every article change, not just once on
  // mount.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0 })
  }, [article.id])

  const [viewMode, setViewMode] = useState<ViewMode>('full')
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

  const citations: Citation[] = article.citations_json ? JSON.parse(article.citations_json) : []

  return (
    <div className="reader-overlay">
      <div className="reader-bar">
        <div className="reader-bar__left">
          <button className="icon-btn" onClick={onClose} title="Close (Esc)">
            ←
          </button>
          <span className="reader-source">{article.source_title}</span>
        </div>
        <div className="reader-bar__right">
          {llmEnabled && article.llm_summary_html ? (
            <div className="view-toggle" role="group" aria-label="View mode">
              <button
                className={`view-toggle__btn ${viewMode === 'full' ? 'active' : ''}`}
                onClick={() => setViewMode('full')}
              >
                Full
              </button>
              <button
                className={`view-toggle__btn ${viewMode === 'summary' ? 'active' : ''}`}
                onClick={() => setViewMode('summary')}
              >
                Summary
              </button>
            </div>
          ) : (
            llmEnabled && (
              <button
                className={`icon-btn ${summarizing ? 'icon-btn--busy' : ''}`}
                onClick={onSummarize}
                disabled={summarizing}
                title={summarizing ? 'Summarizing…' : 'Summarize'}
              >
                {summarizing ? <span className="spinner" /> : '✨'}
              </button>
            )
          )}
          <button
            className={`icon-btn ${article.is_starred ? 'active' : ''}`}
            onClick={onToggleStar}
            title="Star"
          >
            ★
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

      {hasPrev && (
        <button
          className="reader-nav reader-nav--prev"
          onClick={onPrev}
          title="Previous article (←)"
        >
          ‹
        </button>
      )}
      {hasNext && (
        <button
          className="reader-nav reader-nav--next"
          onClick={onNext}
          title="Next article (→)"
        >
          ›
        </button>
      )}

      <div className="reader-scroll" ref={scrollRef}>
        <article className="reader-article">
          {article.origin === 'llm' && (
            <div className="reader-article__badges">
              <span className="badge badge--generated">Generated by Claude</span>
            </div>
          )}
          <h1 className="reader-article__title">{article.title}</h1>
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
              className="reader-body reader-body--summary"
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

          {citations.length > 0 && (
            <div className="reader-sources">
              <div className="reader-sources__label">Sources</div>
              {citations.map((c, i) => (
                <a key={i} className="reader-sources__item" href={c.url} target="_blank" rel="noreferrer">
                  {c.title}
                </a>
              ))}
            </div>
          )}
        </article>
      </div>
    </div>
  )
}

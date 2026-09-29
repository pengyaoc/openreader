import { useState } from 'react'
import { api, type WebFeedCandidate, type WebFeedPreview } from '../api'

export interface WebFeedPick {
  /** Post-redirect URL — saved as the source's url so refreshes (which
   * don't follow redirects) fetch the page directly. */
  url: string
  selector: string
  pageTitle: string
}

interface Props {
  initialUrl?: string
  initialSelector?: string
  /** Called whenever the chosen candidate changes; null = nothing usable. */
  onPick: (pick: WebFeedPick | null) => void
  /** The page advertises a real RSS/Atom feed — switch to adding that. */
  onUseFeed: (feedUrl: string) => void
}

const INTRO_DISMISSED_KEY = 'reader.webfeed.introDismissed'

function readIntroDismissed(): boolean {
  try {
    return localStorage.getItem(INTRO_DISMISSED_KEY) === '1'
  } catch {
    return false
  }
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

function formatDate(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? ''
    : d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

function candidateLabel(preview: WebFeedPreview, index: number): string {
  const candidate = preview.candidates[index]
  if (candidate.custom) return 'Your selector'
  const autoIndex = index - (preview.candidates[0]?.custom ? 1 : 0)
  return autoIndex === 0 ? 'Best match' : `Option ${autoIndex + 1}`
}

// The "Web page" flavour of Add source: paste a URL of a site with no RSS,
// the backend fetches it and proposes repeating link groups (see
// backend/app/connectors/webpage.py), the user previews and picks one — or
// types their own CSS selector — and SourceForm saves it as a type=web
// source. Nothing is saved from here; this only produces a WebFeedPick.
export function WebFeedBuilder({ initialUrl = '', initialSelector = '', onPick, onUseFeed }: Props) {
  const [url, setUrl] = useState(initialUrl)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [preview, setPreview] = useState<WebFeedPreview | null>(null)
  const [active, setActive] = useState(0)
  const [customSelector, setCustomSelector] = useState(initialSelector)
  const [introDismissed, setIntroDismissed] = useState(readIntroDismissed)

  const choose = (p: WebFeedPreview, index: number) => {
    setActive(index)
    const candidate = p.candidates[index]
    onPick(
      candidate && candidate.count > 0
        ? { url: p.final_url, selector: candidate.selector, pageTitle: p.page_title }
        : null,
    )
  }

  const load = async (selector?: string) => {
    if (!url.trim()) return
    setLoading(true)
    setError(null)
    try {
      const result = await api.previewWebFeed(url.trim(), selector)
      setPreview(result)
      setUrl(result.final_url)
      choose(result, 0)
    } catch (e) {
      setPreview(null)
      onPick(null)
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  const dismissIntro = () => {
    setIntroDismissed(true)
    try {
      localStorage.setItem(INTRO_DISMISSED_KEY, '1')
    } catch {
      /* private mode etc. — the panel just comes back next time */
    }
  }

  const candidate: WebFeedCandidate | undefined = preview?.candidates[active]

  return (
    <div className="webfeed">
      <form
        className="webfeed__load"
        onSubmit={(e) => {
          e.preventDefault()
          load(initialSelector && url === initialUrl ? initialSelector : undefined)
        }}
      >
        <input
          className="field__input webfeed__url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="Paste website URL"
          inputMode="url"
          autoFocus
        />
        <button className="btn btn--primary" type="submit" disabled={!url.trim() || loading}>
          {loading ? 'Loading…' : 'Load website'}
        </button>
      </form>

      {error && <div className="webfeed__notice webfeed__notice--error">{error}</div>}

      {!preview && !loading && !error && (
        <>
          <ol className="webfeed__steps">
            <li>
              <span className="webfeed__step-num">1</span>
              <span>Paste a website URL and click “Load website”.</span>
            </li>
            <li>
              <span className="webfeed__step-num">2</span>
              <span>Pick the group of links that are the articles — or type your own CSS selector.</span>
            </li>
            <li>
              <span className="webfeed__step-num">3</span>
              <span>Preview the items and follow your new feed.</span>
            </li>
          </ol>
          {!introDismissed && (
            <div className="webfeed__intro">
              <button className="icon-btn webfeed__intro-close" onClick={dismissIntro} aria-label="Dismiss">
                ✕
              </button>
              <strong>What is a web feed?</strong>
              <p>
                It follows websites that don’t offer RSS. Reader loads the page, finds the repeating
                list of article links and turns it into a feed. Each Refresh checks the page again,
                and new links show up as new items.
              </p>
              <p>
                It reads the page’s HTML as served. If a site builds its list with JavaScript, try a
                more specific listing page, like <code>/blog</code>, <code>/news</code> or an archive.
              </p>
            </div>
          )}
        </>
      )}

      {preview && preview.feed_links.length > 0 && (
        <div className="webfeed__notice">
          <span>
            {preview.candidates.length === 0
              ? 'This URL is already an RSS/Atom feed.'
              : 'This site already publishes a feed. That’s usually more reliable than scraping.'}
          </span>
          <button className="btn" type="button" onClick={() => onUseFeed(preview.feed_links[0])}>
            Add as RSS feed
          </button>
        </div>
      )}

      {preview && preview.candidates.length === 0 && preview.feed_links.length === 0 && (
        <div className="webfeed__notice">
          No repeating list of article links was found on this page. If the site loads its list
          with JavaScript, try a listing sub-page. Or enter a CSS selector below.
        </div>
      )}

      {preview && (
        <>
          {preview.candidates.length > 0 && (
            <div className="webfeed__candidates" role="tablist">
              {preview.candidates.map((c, i) => (
                <button
                  key={c.selector}
                  type="button"
                  role="tab"
                  aria-selected={i === active}
                  className={`webfeed__candidate ${i === active ? 'active' : ''}`}
                  onClick={() => choose(preview, i)}
                >
                  <span className="webfeed__candidate-label">{candidateLabel(preview, i)}</span>
                  <span className="webfeed__candidate-count">{c.count} items</span>
                </button>
              ))}
            </div>
          )}

          {candidate && (
            <>
              <code className="webfeed__selector" title="CSS selector for this group">
                {candidate.selector}
              </code>
              {candidate.count === 0 ? (
                <div className="webfeed__notice webfeed__notice--error">
                  This selector doesn’t match any links on the page.
                </div>
              ) : (
                <ul className="webfeed__items">
                  {candidate.items.map((item) => (
                    <li key={item.url} className="webfeed__item">
                      <a href={item.url} target="_blank" rel="noreferrer" className="webfeed__item-title">
                        {item.title}
                      </a>
                      <span className="webfeed__item-meta">
                        {hostOf(item.url)}
                        {item.published_at && ` · ${formatDate(item.published_at)}`}
                      </span>
                      {item.summary && <span className="webfeed__item-summary">{item.summary}</span>}
                    </li>
                  ))}
                  {candidate.count > candidate.items.length && (
                    <li className="webfeed__more">+ {candidate.count - candidate.items.length} more</li>
                  )}
                </ul>
              )}
            </>
          )}

          <form
            className="webfeed__custom"
            onSubmit={(e) => {
              e.preventDefault()
              if (customSelector.trim()) load(customSelector.trim())
            }}
          >
            <label className="field webfeed__custom-field">
              <span className="field__label">Custom CSS selector (optional)</span>
              <input
                className="field__input webfeed__custom-input"
                value={customSelector}
                onChange={(e) => setCustomSelector(e.target.value)}
                placeholder="e.g. article h2 a  or  li.post"
                spellCheck={false}
              />
            </label>
            <button className="btn" type="submit" disabled={!customSelector.trim() || loading}>
              Try it
            </button>
          </form>
        </>
      )}
    </div>
  )
}

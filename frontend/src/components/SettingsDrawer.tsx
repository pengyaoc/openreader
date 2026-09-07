import { useMemo, useState } from 'react'
import type { Source } from '../api'
import { SourceForm } from './SourceForm'
import { YamlConfigPanel } from './YamlConfigPanel'

interface Props {
  sources: Source[]
  onClose: () => void
  onSaved: () => void
  /** Opens the drawer straight into the add-source pane (from the sidebar's
   * own add affordance, if any) rather than the feed list. */
  initialPane?: Pane
}

type Tab = 'feeds' | 'advanced'
export type Pane = { kind: 'list' } | { kind: 'add' } | { kind: 'edit'; id: number }

// Settings drawer — the ⚙ button's target, and the *only* overlay/drawer
// in the app. Adding and editing a source are panes rendered inside this
// same drawer (SourceForm, stripped of its own overlay chrome) rather than
// a second stacked modal — two independent overlays used to compound their
// backdrops and slide-in animations on top of each other, which read as a
// confusing "second bar appearing over the first, background gone darker"
// glitch. Feeds tab is a friendly search-and-pick UX in front of that same
// structured form; Advanced tab is the raw feeds.yaml editor, kept as an
// escape hatch for config keys the per-source form doesn't cover.
export function SettingsDrawer({ sources, onClose, onSaved, initialPane }: Props) {
  const [tab, setTab] = useState<Tab>('feeds')
  const [query, setQuery] = useState('')
  const [pane, setPane] = useState<Pane>(initialPane ?? { kind: 'list' })

  const folders = useMemo(() => {
    const q = query.trim().toLowerCase()
    const filtered = q
      ? sources.filter((s) => s.title.toLowerCase().includes(q) || s.folder.toLowerCase().includes(q))
      : sources
    const map = new Map<string, Source[]>()
    for (const s of filtered) {
      if (!map.has(s.folder)) map.set(s.folder, [])
      map.get(s.folder)!.push(s)
    }
    return Array.from(map.entries())
  }, [sources, query])

  const backToList = () => setPane({ kind: 'list' })

  const title =
    pane.kind === 'list' ? 'Settings' : pane.kind === 'add' ? 'Add source' : 'Edit source'

  return (
    <div className="config-overlay" onClick={onClose}>
      <div className="config-drawer" onClick={(e) => e.stopPropagation()}>
        <div className="config-drawer__header">
          <div className="config-drawer__header-left">
            {pane.kind !== 'list' && (
              <button className="icon-btn" onClick={backToList} title="Back to feeds" aria-label="Back">
                ←
              </button>
            )}
            <span className="config-drawer__title">{title}</span>
          </div>
          <button className="icon-btn" onClick={onClose}>
            ✕
          </button>
        </div>

        {pane.kind === 'list' ? (
          <>
            <div className="settings-tabs">
              <button
                className={`settings-tabs__tab ${tab === 'feeds' ? 'active' : ''}`}
                onClick={() => setTab('feeds')}
              >
                Feeds
              </button>
              <button
                className={`settings-tabs__tab ${tab === 'advanced' ? 'active' : ''}`}
                onClick={() => setTab('advanced')}
              >
                Advanced
              </button>
            </div>

            {tab === 'feeds' ? (
              <div className="feed-picker">
                <div className="feed-picker__toolbar">
                  <input
                    className="field__input"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Search feeds…"
                    autoFocus
                  />
                  <button className="btn" onClick={() => setPane({ kind: 'add' })}>
                    + Add source
                  </button>
                </div>
                <div className="feed-picker__list">
                  {folders.length === 0 && (
                    <p className="field__hint" style={{ padding: '0 20px' }}>
                      No feeds match “{query}”.
                    </p>
                  )}
                  {folders.map(([folder, folderSources]) => (
                    <div key={folder}>
                      <div className="sidebar__section-label">{folder}</div>
                      {folderSources.map((s) => (
                        <button
                          key={s.id}
                          className="feed-picker__row"
                          onClick={() => setPane({ kind: 'edit', id: s.id })}
                        >
                          {s.last_error && (
                            <span className="nav-row__dot" title="Last refresh failed" />
                          )}
                          <span className="feed-picker__title">{s.title}</span>
                          <span className="feed-picker__badge">{s.type}</span>
                        </button>
                      ))}
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <YamlConfigPanel onSaved={onSaved} />
            )}
          </>
        ) : (
          <SourceForm
            key={pane.kind === 'edit' ? pane.id : 'add'}
            onCancel={backToList}
            onSaved={onSaved}
            editingSourceId={pane.kind === 'edit' ? pane.id : undefined}
          />
        )}
      </div>
    </div>
  )
}

import { useMemo } from 'react'
import type { Job, Source, Topic } from '../api'
import type { ViewSelection } from '../types'

interface Props {
  sources: Source[]
  selection: ViewSelection
  onSelect: (v: ViewSelection) => void
  totalUnread: number
  totalStarred: number
  onRefresh: () => void
  refreshing: boolean
  onOpenConfig: () => void
  theme: 'dark' | 'light'
  onToggleTheme: () => void
  onAddSource: () => void
  mobileOpen: boolean
  onCloseMobile: () => void
  llmEnabled: boolean
  topics: Topic[]
  jobsByTopic: Record<string, Job | undefined>
  onGenerate: (topicKey: string) => void
}

function isSame(a: ViewSelection, b: ViewSelection): boolean {
  if (a.kind !== b.kind) return false
  if (a.kind === 'saved' && b.kind === 'saved') return a.view === b.view
  if (a.kind === 'source' && b.kind === 'source') return a.sourceId === b.sourceId
  if (a.kind === 'folder' && b.kind === 'folder') return a.folder === b.folder
  return false
}

export function Sidebar({
  sources,
  selection,
  onSelect,
  totalUnread,
  totalStarred,
  onRefresh,
  refreshing,
  onOpenConfig,
  theme,
  onToggleTheme,
  onAddSource,
  mobileOpen,
  onCloseMobile,
  llmEnabled,
  topics,
  jobsByTopic,
  onGenerate,
}: Props) {
  const folders = useMemo(() => {
    const map = new Map<string, Source[]>()
    for (const s of sources) {
      if (!map.has(s.folder)) map.set(s.folder, [])
      map.get(s.folder)!.push(s)
    }
    return Array.from(map.entries())
  }, [sources])

  // On mobile the sidebar is an off-canvas drawer — picking anything closes
  // it. Harmless no-op on desktop, where it's always visible anyway.
  const select = (v: ViewSelection) => {
    onSelect(v)
    onCloseMobile()
  }

  return (
    <>
      {mobileOpen && <div className="sidebar-backdrop" onClick={onCloseMobile} />}
      <aside className={`sidebar ${mobileOpen ? 'sidebar--open' : ''}`}>
        <div className="sidebar__brand">
          <span className="sidebar__brand-mark" />
          <span className="sidebar__brand-name">OpenReader</span>
          <button
            className="theme-toggle"
            onClick={onToggleTheme}
            title={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
          >
            {theme === 'dark' ? '☀' : '☾'}
          </button>
        </div>

        <div className="sidebar__scroll">
          <div className="sidebar__section">
            <NavRow
              label="All items"
              icon="◧"
              active={isSame(selection, { kind: 'saved', view: 'all' })}
              onClick={() => select({ kind: 'saved', view: 'all' })}
            />
            <NavRow
              label="Unread"
              icon="●"
              count={totalUnread}
              active={isSame(selection, { kind: 'saved', view: 'unread' })}
              onClick={() => select({ kind: 'saved', view: 'unread' })}
            />
            <NavRow
              label="Starred"
              icon="★"
              count={totalStarred || undefined}
              active={isSame(selection, { kind: 'saved', view: 'starred' })}
              onClick={() => select({ kind: 'saved', view: 'starred' })}
            />
          </div>

          {llmEnabled && topics.length > 0 && (
            <div className="sidebar__section">
              <div className="sidebar__section-label">Generate</div>
              {topics.map((t) => {
                const job = jobsByTopic[t.key]
                const running = job?.status === 'queued' || job?.status === 'running'
                return (
                  <div className="gen-topic" key={t.key}>
                    <span className="gen-topic__label" title={job?.error ?? t.title}>
                      {t.title}
                    </span>
                    {job?.status === 'error' && (
                      <span className="nav-row__dot" title={job.error ?? 'Generation failed'} />
                    )}
                    {running ? (
                      <span className="gen-spinner" title="Generating…" />
                    ) : (
                      <button
                        className="gen-btn"
                        onClick={() => onGenerate(t.key)}
                        title={`Generate: ${t.brief}`}
                      >
                        Generate
                      </button>
                    )}
                  </div>
                )
              })}
            </div>
          )}

          {folders.map(([folder, folderSources]) => (
            <div className="sidebar__section" key={folder}>
              <div className="sidebar__section-label">{folder}</div>
              <div className="folder-group">
                {folderSources.map((s) => (
                  <NavRow
                    key={s.id}
                    label={s.title}
                    count={s.unread_count || undefined}
                    hasError={!!s.last_error}
                    active={isSame(selection, { kind: 'source', sourceId: s.id, title: s.title })}
                    onClick={() => select({ kind: 'source', sourceId: s.id, title: s.title })}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>

        <div className="sidebar__footer">
          <button className="refresh-btn" onClick={onRefresh} disabled={refreshing}>
            <span className={`refresh-icon ${refreshing ? 'spinning' : ''}`}>⟳</span>
            {refreshing ? 'Refreshing…' : 'Refresh feeds'}
          </button>
          <div style={{ height: 8 }} />
          <button className="refresh-btn" onClick={onAddSource}>
            + Add source
          </button>
          <div style={{ height: 8 }} />
          <button className="refresh-btn" onClick={onOpenConfig}>
            ⚙ Configure
          </button>
        </div>
      </aside>
    </>
  )
}

function NavRow({
  label,
  icon,
  count,
  active,
  hasError,
  onClick,
}: {
  label: string
  icon?: string
  count?: number
  active: boolean
  hasError?: boolean
  onClick: () => void
}) {
  return (
    <button className={`nav-row ${active ? 'active' : ''}`} onClick={onClick}>
      {icon && <span className="nav-row__icon">{icon}</span>}
      <span className="nav-row__label">{label}</span>
      {hasError && <span className="nav-row__dot" title="Last refresh failed" />}
      {count !== undefined && count > 0 && <span className="nav-row__count">{count}</span>}
    </button>
  )
}

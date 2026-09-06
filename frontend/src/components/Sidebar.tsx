import { useMemo } from 'react'
import type { Source } from '../api'
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
  mobileOpen: boolean
  onCloseMobile: () => void
  onMarkAllRead: (sourceId: number) => void
  onMarkAllUnreadRead: () => void
  onLogout: () => void
  username?: string
  authEnabled: boolean
  // True while the reader overlay is open on top of it — removes the whole
  // sidebar from the accessibility tree so VoiceOver's swipe/rotor
  // navigation can't reach it (see App.tsx's `readerOpen`).
  inert?: boolean
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
  mobileOpen,
  onCloseMobile,
  onMarkAllRead,
  onMarkAllUnreadRead,
  onLogout,
  username,
  authEnabled,
  inert,
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
      <aside className={`sidebar ${mobileOpen ? 'sidebar--open' : ''}`} inert={inert}>
        {/* `inert` alone isn't a reliable guarantee on iOS: a documented
            Safari 26 VoiceOver regression lets the virtual cursor keep
            navigating into content that's still in the DOM even when it's
            `inert` (https://discussions.apple.com/thread/256161078) — when
            focus is lost, VoiceOver falls back to whatever's nearest by
            screen position to where it last was, and an inert-but-present
            nav row is still a candidate. `inert` is kept for browsers where
            it behaves correctly (and for pointer/keyboard blocking); not
            rendering the content at all while the reader is open closes the
            gap for everyone else — there's nothing left for VO to fall back
            to. `.sidebar__scroll` itself stays mounted (only its contents
            are stripped) so a scrolled-down feed list doesn't jump back to
            the top every time the reader opens and closes. */}
        {!inert && (
          <div className="sidebar__brand">
            <svg className="sidebar__brand-mark" viewBox="0 0 32 32" aria-hidden="true">
              <rect width="32" height="32" rx="7" fill="var(--amber)" />
              <circle cx="9" cy="23" r="3.2" fill="var(--bg)" />
              <path
                d="M9 15 A8 8 0 0 1 17 23"
                fill="none"
                stroke="var(--bg)"
                strokeWidth="3.1"
                strokeLinecap="round"
              />
              <path
                d="M9 8 A15 15 0 0 1 24 23"
                fill="none"
                stroke="var(--bg)"
                strokeWidth="3.1"
                strokeLinecap="round"
              />
            </svg>
            <span className="sidebar__brand-name">OpenReader</span>
            <button
              className="theme-toggle"
              onClick={onToggleTheme}
              title={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
            >
              {theme === 'dark' ? '☀' : '☾'}
            </button>
          </div>
        )}

        <div className="sidebar__scroll" id="sidebar-scroll">
          {!inert && (
            <>
              <div className="sidebar__section">
                <NavRow
                  label="All items"
                  icon="◧"
                  active={isSame(selection, { kind: 'saved', view: 'all' })}
                  onClick={() => select({ kind: 'saved', view: 'all' })}
                />
                <div className="source-row">
                  <NavRow
                    label="Unread"
                    icon="●"
                    count={totalUnread}
                    active={isSame(selection, { kind: 'saved', view: 'unread' })}
                    onClick={() => select({ kind: 'saved', view: 'unread' })}
                  />
                  {totalUnread > 0 && (
                    <button
                      className="source-row__mark-read"
                      onClick={onMarkAllUnreadRead}
                      title={`Mark all ${totalUnread} as read`}
                    >
                      ✓
                    </button>
                  )}
                </div>
                <NavRow
                  label="Starred"
                  icon="★"
                  count={totalStarred || undefined}
                  active={isSame(selection, { kind: 'saved', view: 'starred' })}
                  onClick={() => select({ kind: 'saved', view: 'starred' })}
                />
              </div>

              {folders.map(([folder, folderSources]) => (
                <div className="sidebar__section" key={folder}>
                  <div className="sidebar__section-label">{folder}</div>
                  <div className="folder-group">
                    {folderSources.map((s) => (
                      <div className="source-row" key={s.id}>
                        <NavRow
                          label={s.title}
                          count={s.unread_count || undefined}
                          hasError={!!s.last_error}
                          active={isSame(selection, {
                            kind: 'source',
                            sourceId: s.id,
                            title: s.title,
                          })}
                          onClick={() => select({ kind: 'source', sourceId: s.id, title: s.title })}
                        />
                        {s.unread_count > 0 && (
                          <button
                            className="source-row__mark-read"
                            onClick={() => onMarkAllRead(s.id)}
                            title={`Mark all ${s.unread_count} as read`}
                          >
                            ✓
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </>
          )}
        </div>

        {!inert && (
          <div className="sidebar__footer">
            {username && (
              <div className="sidebar__identity">
                <span aria-hidden="true">👤</span>
                <span className="sidebar__identity-name">{username}</span>
              </div>
            )}
            <button className="refresh-btn" onClick={onRefresh} disabled={refreshing}>
              <span className={`refresh-icon ${refreshing ? 'spinning' : ''}`}>⟳</span>
              {refreshing ? 'Refreshing…' : 'Refresh feeds'}
            </button>
            <div style={{ height: 8 }} />
            <button className="refresh-btn" onClick={onOpenConfig}>
              ⚙ Settings
            </button>
            {/* No Log out on a deployment with no login configured — the
                button would clear a cookie that was never gating anything,
                and land you straight back on the same screen. */}
            {authEnabled && (
              <>
                <div style={{ height: 8 }} />
                <button className="refresh-btn" onClick={onLogout}>
                  ⏻ Log out
                </button>
              </>
            )}
          </div>
        )}
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

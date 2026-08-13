import { useEffect, useState } from 'react'
import { api, type Rule, type RuleAction, type RuleField, type SourceFields } from '../api'

interface Props {
  onClose: () => void
  onSaved: () => void
  /** undefined = add mode. A source id = edit mode; fields are fetched
   * and pre-filled from GET /api/sources/:id on mount. */
  editingSourceId?: number
}

function slugify(title: string): string {
  return title
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9一-鿿]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 40)
}

// Newsletter (imap/gmail) sources share one `query` field using the same
// from:/subject:/newer_than: syntax as type=gmail always used — mirrors
// connectors/imap.py's parse_query() regexes so an edited source's
// existing query round-trips through these friendly fields correctly
// instead of forcing raw query-string editing (the whole point of this
// tool over hand-editing feeds.yaml).
function decomposeQuery(query: string): { from: string; subject: string; newerThanDays: string } {
  const fromMatch = query.match(/from:(\S+)/)
  const subjectMatch = query.match(/subject:"([^"]+)"|subject:(\S+)/)
  const newerThanMatch = query.match(/newer_than:(\d+)d/)
  return {
    from: fromMatch?.[1] ?? '',
    subject: subjectMatch?.[1] ?? subjectMatch?.[2] ?? '',
    newerThanDays: newerThanMatch?.[1] ?? '30',
  }
}

function composeQuery(from: string, subject: string, newerThanDays: string): string {
  const parts: string[] = []
  if (from.trim()) parts.push(`from:${from.trim()}`)
  if (subject.trim()) parts.push(`subject:"${subject.trim()}"`)
  if (newerThanDays.trim()) parts.push(`newer_than:${newerThanDays.trim()}d`)
  return parts.join(' ')
}

const FIELD_OPTIONS: RuleField[] = ['title', 'summary', 'content', 'author', 'url', 'any']

export function SourceModal({ onClose, onSaved, editingSourceId }: Props) {
  const isEditing = editingSourceId !== undefined

  // 'imap' covers both type=imap and type=gmail on edit — they share the
  // same query-builder shape; gmail just isn't offered as a choice when
  // adding new (OAuth setup, not something this form can provision).
  const [sourceType, setSourceType] = useState<'rss' | 'imap'>('rss')
  const [lockedType, setLockedType] = useState<string | null>(null) // real type when editing gmail
  const [title, setTitle] = useState('')
  const [folder, setFolder] = useState('')
  const [url, setUrl] = useState('')
  const [mailboxFolder, setMailboxFolder] = useState('')
  const [queryFrom, setQueryFrom] = useState('')
  const [querySubject, setQuerySubject] = useState('')
  const [queryNewerThanDays, setQueryNewerThanDays] = useState('30')
  const [fetchFullText, setFetchFullText] = useState(false)
  const [rules, setRules] = useState<Rule[]>([])
  const [loading, setLoading] = useState(isEditing)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmingDelete, setConfirmingDelete] = useState(false)

  useEffect(() => {
    if (editingSourceId === undefined) return
    let cancelled = false
    api
      .getSource(editingSourceId)
      .then((detail) => {
        if (cancelled) return
        setTitle(detail.title)
        setFolder(detail.folder)
        setRules(detail.rules)
        if (detail.type === 'rss') {
          setSourceType('rss')
          setUrl(detail.url ?? '')
          setFetchFullText(detail.fetch_full_text)
        } else {
          setSourceType('imap')
          setLockedType(detail.type) // 'imap' or 'gmail'
          setMailboxFolder(detail.mailbox_folder ?? '')
          const { from, subject, newerThanDays } = decomposeQuery(detail.query ?? '')
          setQueryFrom(from)
          setQuerySubject(subject)
          setQueryNewerThanDays(newerThanDays)
        }
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [editingSourceId])

  const key = slugify(title)
  const addRule = () => setRules((r) => [...r, { action: 'include', field: 'title', pattern: '' }])
  const updateRule = (i: number, patch: Partial<Rule>) =>
    setRules((r) => r.map((rule, idx) => (idx === i ? { ...rule, ...patch } : rule)))
  const removeRule = (i: number) => setRules((r) => r.filter((_, idx) => idx !== i))

  const canSubmit =
    title.trim() &&
    folder.trim() &&
    (isEditing || key) &&
    (sourceType === 'rss' ? url.trim() : true)

  const buildFields = (): SourceFields => {
    const cleanRules = rules.filter((r) => r.pattern.trim())
    if (sourceType === 'rss') {
      return {
        type: 'rss',
        title: title.trim(),
        folder: folder.trim(),
        url: url.trim(),
        fetch_full_text: fetchFullText,
        rules: cleanRules,
      }
    }
    return {
      type: 'imap',
      title: title.trim(),
      folder: folder.trim(),
      query: composeQuery(queryFrom, querySubject, queryNewerThanDays),
      mailbox_folder: mailboxFolder.trim() || undefined,
      rules: cleanRules,
    }
  }

  const submit = async () => {
    setSaving(true)
    setError(null)
    try {
      if (isEditing) {
        await api.updateSource(editingSourceId, buildFields())
      } else {
        await api.addSource({ key, ...buildFields() })
      }
      onSaved()
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    if (!confirmingDelete) {
      setConfirmingDelete(true)
      return
    }
    setSaving(true)
    setError(null)
    try {
      await api.removeSource(editingSourceId!)
      onSaved()
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setSaving(false)
      setConfirmingDelete(false)
    }
  }

  return (
    <div className="config-overlay" onClick={onClose}>
      <div className="config-drawer add-source-drawer" onClick={(e) => e.stopPropagation()}>
        <div className="config-drawer__header">
          <span className="config-drawer__title">{isEditing ? 'Edit source' : 'Add source'}</span>
          <button className="icon-btn" onClick={onClose}>
            ✕
          </button>
        </div>

        {loading ? (
          <div className="add-source-form">
            <p className="field__hint">Loading…</p>
          </div>
        ) : (
          <div className="add-source-form">
            {!isEditing && (
              <div className="type-toggle">
                <button
                  type="button"
                  className={`type-toggle__option ${sourceType === 'rss' ? 'active' : ''}`}
                  onClick={() => setSourceType('rss')}
                >
                  RSS feed
                </button>
                <button
                  type="button"
                  className={`type-toggle__option ${sourceType === 'imap' ? 'active' : ''}`}
                  onClick={() => setSourceType('imap')}
                >
                  Newsletter
                </button>
              </div>
            )}
            {isEditing && lockedType && (
              <p className="field__hint">
                Type: <code>{lockedType}</code> (not editable — remove and re-add to change)
              </p>
            )}

            <label className="field">
              <span className="field__label">Title</span>
              <input
                className="field__input"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Simon Willison"
                autoFocus
              />
            </label>

            {sourceType === 'rss' ? (
              <label className="field">
                <span className="field__label">Feed URL</span>
                <input
                  className="field__input"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://example.com/feed.xml"
                />
              </label>
            ) : (
              <>
                <p className="field__hint">
                  Matches mail already arriving in the connected mailbox — this doesn't subscribe
                  you to anything. Leave a field blank to not filter on it.
                </p>
                <label className="field">
                  <span className="field__label">From address contains</span>
                  <input
                    className="field__input"
                    value={queryFrom}
                    onChange={(e) => setQueryFrom(e.target.value)}
                    placeholder="hello@newsletter.example.com"
                  />
                </label>
                <label className="field">
                  <span className="field__label">Subject contains (optional)</span>
                  <input
                    className="field__input"
                    value={querySubject}
                    onChange={(e) => setQuerySubject(e.target.value)}
                    placeholder="Weekly Digest"
                  />
                </label>
                <label className="field">
                  <span className="field__label">
                    First-refresh window (days) — bounds how far back the very first refresh
                    looks
                  </span>
                  <input
                    className="field__input"
                    type="number"
                    min={1}
                    value={queryNewerThanDays}
                    onChange={(e) => setQueryNewerThanDays(e.target.value)}
                  />
                </label>
                <label className="field">
                  <span className="field__label">Mailbox folder (optional, defaults to INBOX)</span>
                  <input
                    className="field__input"
                    value={mailboxFolder}
                    onChange={(e) => setMailboxFolder(e.target.value)}
                    placeholder="INBOX"
                  />
                </label>
              </>
            )}

            <label className="field">
              <span className="field__label">Sidebar folder</span>
              <input
                className="field__input"
                value={folder}
                onChange={(e) => setFolder(e.target.value)}
                placeholder="AI"
              />
              <span className="field__hint">
                Groups this source in the sidebar — unrelated to the mailbox folder above.
              </span>
            </label>

            {!isEditing && title && (
              <div className="field__hint">
                key: <code>{key || '(needs a title with letters/numbers)'}</code>
              </div>
            )}

            {sourceType === 'rss' && (
              <label className="field field--checkbox">
                <input
                  type="checkbox"
                  checked={fetchFullText}
                  onChange={(e) => setFetchFullText(e.target.checked)}
                />
                <span>Fetch full article text on open (for feeds with truncated content)</span>
              </label>
            )}

            <div className="rules-section">
              <div className="rules-section__header">
                <span className="field__label">Filter rules</span>
                <button className="btn" onClick={addRule} type="button">
                  + Add rule
                </button>
              </div>
              <p className="field__hint">
                With no <code>include</code> rules, everything not excluded passes. Any matching{' '}
                <code>exclude</code> rule always wins.
              </p>

              {rules.map((rule, i) => (
                <div className="rule-row" key={i}>
                  <select
                    value={rule.action}
                    onChange={(e) => updateRule(i, { action: e.target.value as RuleAction })}
                  >
                    <option value="include">include</option>
                    <option value="exclude">exclude</option>
                  </select>
                  <select
                    value={rule.field}
                    onChange={(e) => updateRule(i, { field: e.target.value as RuleField })}
                  >
                    {FIELD_OPTIONS.map((f) => (
                      <option key={f} value={f}>
                        {f}
                      </option>
                    ))}
                  </select>
                  <input
                    className="rule-row__pattern"
                    value={rule.pattern}
                    onChange={(e) => updateRule(i, { pattern: e.target.value })}
                    placeholder="(?i)regex pattern"
                  />
                  <button className="icon-btn" onClick={() => removeRule(i)} type="button">
                    ✕
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {error && <div className="config-drawer__error">{error}</div>}

        <div className="config-drawer__footer">
          {isEditing ? (
            <button
              className={`btn btn--danger ${confirmingDelete ? 'btn--danger-confirm' : ''}`}
              onClick={handleDelete}
              disabled={saving || loading}
            >
              {confirmingDelete ? 'Click again to delete' : 'Delete'}
            </button>
          ) : (
            <span />
          )}
          <div className="config-drawer__footer-right">
            <button className="btn" onClick={onClose}>
              Cancel
            </button>
            <button
              className="btn btn--primary"
              onClick={submit}
              disabled={!canSubmit || saving || loading}
            >
              {saving ? 'Saving…' : isEditing ? 'Save changes' : 'Add source'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

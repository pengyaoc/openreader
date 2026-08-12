import { useState } from 'react'
import { api, type Rule, type RuleAction, type RuleField } from '../api'

interface Props {
  onClose: () => void
  onAdded: () => void
}

function slugify(title: string): string {
  return title
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9一-鿿]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 40)
}

const FIELD_OPTIONS: RuleField[] = ['title', 'summary', 'content', 'author', 'url', 'any']

export function AddSourceModal({ onClose, onAdded }: Props) {
  const [title, setTitle] = useState('')
  const [url, setUrl] = useState('')
  const [folder, setFolder] = useState('')
  const [fetchFullText, setFetchFullText] = useState(false)
  const [rules, setRules] = useState<Rule[]>([])
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const key = slugify(title)

  const addRule = () => setRules((r) => [...r, { action: 'include', field: 'title', pattern: '' }])
  const updateRule = (i: number, patch: Partial<Rule>) =>
    setRules((r) => r.map((rule, idx) => (idx === i ? { ...rule, ...patch } : rule)))
  const removeRule = (i: number) => setRules((r) => r.filter((_, idx) => idx !== i))

  const canSubmit = title.trim() && url.trim() && folder.trim() && key

  const submit = async () => {
    setSaving(true)
    setError(null)
    try {
      await api.addSource({
        key,
        type: 'rss',
        title: title.trim(),
        folder: folder.trim(),
        url: url.trim(),
        fetch_full_text: fetchFullText,
        rules: rules.filter((r) => r.pattern.trim()),
      })
      onAdded()
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="config-overlay" onClick={onClose}>
      <div className="config-drawer add-source-drawer" onClick={(e) => e.stopPropagation()}>
        <div className="config-drawer__header">
          <span className="config-drawer__title">Add source</span>
          <button className="icon-btn" onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="add-source-form">
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

          <label className="field">
            <span className="field__label">Feed URL</span>
            <input
              className="field__input"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://example.com/feed.xml"
            />
          </label>

          <label className="field">
            <span className="field__label">Folder</span>
            <input
              className="field__input"
              value={folder}
              onChange={(e) => setFolder(e.target.value)}
              placeholder="AI"
            />
          </label>

          {title && (
            <div className="field__hint">
              key: <code>{key || '(needs a title with letters/numbers)'}</code>
            </div>
          )}

          <label className="field field--checkbox">
            <input
              type="checkbox"
              checked={fetchFullText}
              onChange={(e) => setFetchFullText(e.target.checked)}
            />
            <span>Fetch full article text on open (for feeds with truncated content)</span>
          </label>

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

        {error && <div className="config-drawer__error">{error}</div>}

        <div className="config-drawer__footer">
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn--primary" onClick={submit} disabled={!canSubmit || saving}>
            {saving ? 'Adding…' : 'Add source'}
          </button>
        </div>
      </div>
    </div>
  )
}

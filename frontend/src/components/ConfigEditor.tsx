import { useEffect, useState } from 'react'
import { api } from '../api'

interface Props {
  onClose: () => void
  onSaved: () => void
}

export function ConfigEditor({ onClose, onSaved }: Props) {
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    api.getConfig().then((r) => {
      setText(r.yaml)
      setLoaded(true)
    })
  }, [])

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      await api.putConfig(text)
      onSaved()
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="config-overlay" onClick={onClose}>
      <div className="config-drawer" onClick={(e) => e.stopPropagation()}>
        <div className="config-drawer__header">
          <span className="config-drawer__title">config/feeds.yaml</span>
          <button className="icon-btn" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="config-drawer__hint">
          Sources, per-source regex rules (<code>include</code>/<code>exclude</code> on{' '}
          <code>title</code>, <code>summary</code>, <code>content</code>, <code>author</code>,{' '}
          <code>url</code>, or <code>any</code>), and LLM generation topics all live here. Invalid
          regex or YAML is rejected on save — the file on disk is left untouched.
        </div>
        {error && <div className="config-drawer__error">{error}</div>}
        {loaded ? (
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            spellCheck={false}
          />
        ) : (
          <div style={{ padding: 20, color: 'var(--ink-faint)' }}>Loading…</div>
        )}
        <div className="config-drawer__footer">
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn--primary" onClick={save} disabled={saving || !loaded}>
            {saving ? 'Saving…' : 'Save & validate'}
          </button>
        </div>
      </div>
    </div>
  )
}

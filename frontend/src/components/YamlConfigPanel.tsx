import { useEffect, useState } from 'react'
import { api } from '../api'

interface Props {
  onSaved: () => void
}

// The raw config/feeds.yaml editor — kept as the Settings drawer's
// "Advanced" tab (see SettingsDrawer.tsx) for config keys the structured
// per-source form doesn't cover (llm settings, defaults, etc.). No overlay
// or drawer chrome of its own anymore; the parent drawer supplies that.
export function YamlConfigPanel({ onSaved }: Props) {
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
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <div className="config-drawer__hint">
        Sources and per-source regex rules (<code>include</code>/<code>exclude</code> on{' '}
        <code>title</code>, <code>summary</code>, <code>content</code>, <code>author</code>,{' '}
        <code>url</code>, or <code>any</code>) live here. Invalid regex or YAML is rejected on
        save — the file on disk is left untouched. Most feeds are easier to edit from the Feeds
        tab; this is for config keys that don't have a form yet.
      </div>
      {error && <div className="config-drawer__error">{error}</div>}
      {loaded ? (
        <textarea value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
      ) : (
        <div style={{ padding: 20, color: 'var(--ink-faint)' }}>Loading…</div>
      )}
      <div className="config-drawer__footer">
        <span />
        <div className="config-drawer__footer-right">
          <button className="btn btn--primary" onClick={save} disabled={saving || !loaded}>
            {saving ? 'Saving…' : 'Save & validate'}
          </button>
        </div>
      </div>
    </>
  )
}

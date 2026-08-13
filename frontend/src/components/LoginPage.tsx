import { useState } from 'react'
import { api } from '../api'

interface Props {
  onLoggedIn: () => void
}

// A real <form onSubmit> with a real password input, not a bare onClick
// handler — that's what makes Chrome's (including mobile Chrome's)
// password-manager save prompt fire at all. Basic Auth's native browser
// popup, which this replaces, is invisible to that heuristic (see
// docs/WORKLOG.md, 2026-08-13 cont.). No username field: this app has
// exactly one shared credential, not per-user accounts.
export function LoginPage({ onLoggedIn }: Props) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await api.login(password)
      onLoggedIn()
    } catch {
      setError('Incorrect password')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={submit}>
        <span className="login-card__title">OpenReader</span>
        <label className="field">
          <span className="field__label">Password</span>
          <input
            className="field__input"
            type="password"
            name="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoFocus
          />
        </label>
        {error && <div className="config-drawer__error">{error}</div>}
        <button className="btn btn--primary" type="submit" disabled={submitting || !password}>
          {submitting ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}

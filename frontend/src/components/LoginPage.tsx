import { useState } from 'react'
import { api } from '../api'

interface Props {
  onLoggedIn: () => void
}

// A real <form onSubmit> with real username and password inputs, not a
// bare onClick handler — that's what makes Chrome's (including mobile
// Chrome's) password-manager save prompt fire at all. Basic Auth's native
// browser popup, which this replaces, is invisible to that heuristic (see
// docs/WORKLOG.md, 2026-08-13 cont.). The autoComplete="username" +
// "current-password" pairing is what lets the browser store and offer
// credentials *per account* rather than one blob for the whole site.
export function LoginPage({ onLoggedIn }: Props) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await api.login(username, password)
      onLoggedIn()
    } catch {
      // Same message whichever half was wrong, matching the server — which
      // half it was is exactly what an attacker wants to learn.
      setError('Incorrect username or password')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={submit}>
        <span className="login-card__title">OpenReader</span>
        <label className="field">
          <span className="field__label">Username</span>
          <input
            className="field__input"
            type="text"
            name="username"
            autoComplete="username"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoFocus
          />
        </label>
        <label className="field">
          <span className="field__label">Password</span>
          <input
            className="field__input"
            type="password"
            name="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && <div className="config-drawer__error">{error}</div>}
        <button
          className="btn btn--primary"
          type="submit"
          disabled={submitting || !username || !password}
        >
          {submitting ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}

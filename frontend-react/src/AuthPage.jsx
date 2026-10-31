import { useEffect, useState } from 'react'
import './App.css'

const API_BASE = import.meta.env.VITE_API_BASE_URL
  || `http://${window.location.hostname || '127.0.0.1'}:8000`

const GOOGLE_ERRORS = {
  invalid_state: 'Google sign-in expired or could not be verified. Please try again.',
  cancelled: 'Google sign-in was cancelled.',
  server_configuration: 'Google sign-in is temporarily unavailable. Please contact the administrator.',
  account_conflict: 'This email is already linked to a different Google account.',
  unverified_email: 'Google did not provide a verified email address.',
  provider_error: 'Google sign-in could not be completed. Please try again.',
}

function AuthPage({ mode }) {
  const isRegistration = mode === 'register'
  const [notice, setNotice] = useState(() => {
    const code = new URLSearchParams(window.location.search).get('google_error')
    return Object.prototype.hasOwnProperty.call(GOOGLE_ERRORS, code)
      ? GOOGLE_ERRORS[code]
      : ''
  })
  const [submitting, setSubmitting] = useState(false)
  const [googleEnabled, setGoogleEnabled] = useState(false)

  useEffect(() => {
    let active = true
    fetch(`${API_BASE}/api/auth/providers`)
      .then((response) => {
        if (!response.ok) throw new Error(`Provider check failed with HTTP ${response.status}.`)
        return response.json()
      })
      .then(({ google }) => {
        if (active) setGoogleEnabled(google === true)
      })
      .catch((error) => console.error('Failed to load authentication providers:', error))
    return () => { active = false }
  }, [])

  const handleSubmit = async (event) => {
    event.preventDefault()
    setNotice('')
    const form = event.currentTarget
    if (
      isRegistration
      && form.elements.password.value !== form.elements.confirmPassword.value
    ) {
      setNotice('Passwords do not match. Please check both password fields.')
      return
    }

    setSubmitting(true)
    try {
      const payload = {
        email: form.elements.email.value,
        password: form.elements.password.value,
      }
      if (isRegistration) payload.name = form.elements.name.value
      const response = await fetch(
        `${API_BASE}/api/auth/${isRegistration ? 'register' : 'login'}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify(payload),
        },
      )
      const result = await response.json()
      if (!response.ok) {
        const detail = typeof result.detail === 'string'
          ? result.detail
          : 'Authentication could not be completed.'
        throw new Error(detail)
      }
      window.location.assign('/')
    } catch (error) {
      setNotice(error.message || 'Authentication could not be completed.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="auth-page">
      <header className="auth-header">
        <a className="auth-brand" href="/" aria-label="RegulaMind AI home">
          <img src="/rbi-logo.avif" alt="" />
          <span>
            <strong>RegulaMind AI</strong>
            <small>REGULATORY INTELLIGENCE</small>
          </span>
        </a>
      </header>

      <section className="auth-card" aria-labelledby="auth-title">
        <div className="auth-eyebrow">
          <span className="eyebrow-rule" />
          SECURE WORKSPACE
        </div>
        <h1 id="auth-title">{isRegistration ? 'Create your account' : 'Welcome back'}</h1>
        <p className="auth-intro">
          {isRegistration
            ? 'Set up an account to access your regulatory workspace.'
            : 'Sign in to continue to your regulatory workspace.'}
        </p>

        {googleEnabled ? (
          <a className="auth-google" href={`${API_BASE}/api/auth/google`}>
            <svg viewBox="0 0 48 48" aria-hidden="true">
              <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5Z" />
              <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.74 7.18l7.73 6C44.43 37.97 46.98 31.77 46.98 24.55Z" />
              <path fill="#FBBC05" d="M10.53 28.59A14.4 14.4 0 0 1 9.75 24c0-1.59.27-3.13.75-4.59l-7.98-6.2A23.9 23.9 0 0 0 0 24c0 3.87.93 7.55 2.56 10.79l7.97-6.2Z" />
              <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.91-5.8l-7.73-6c-2.15 1.45-4.9 2.3-8.18 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.2C6.51 42.62 14.62 48 24 48Z" />
            </svg>
            Continue with Google
          </a>
        ) : (
          <button className="auth-google" type="button" disabled>
            <svg viewBox="0 0 48 48" aria-hidden="true">
              <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5Z" />
              <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.74 7.18l7.73 6C44.43 37.97 46.98 31.77 46.98 24.55Z" />
              <path fill="#FBBC05" d="M10.53 28.59A14.4 14.4 0 0 1 9.75 24c0-1.59.27-3.13.75-4.59l-7.98-6.2A23.9 23.9 0 0 0 0 24c0 3.87.93 7.55 2.56 10.79l7.97-6.2Z" />
              <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.91-5.8l-7.73-6c-2.15 1.45-4.9 2.3-8.18 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.2C6.51 42.62 14.62 48 24 48Z" />
            </svg>
            Continue with Google
          </button>
        )}
        {!googleEnabled && (
          <p className="auth-provider-note">
            Google sign-in will be available after server credentials are added.
          </p>
        )}
        <div className="auth-divider"><span>or continue with email</span></div>

        <form className="auth-form" onSubmit={handleSubmit}>
          {isRegistration && (
            <label className="auth-field">
              <span>Full name</span>
              <input autoComplete="name" name="name" placeholder="Your name" required />
            </label>
          )}
          <label className="auth-field">
            <span>Email address</span>
            <input
              autoComplete="email"
              name="email"
              type="email"
              placeholder="you@example.com"
              required
            />
          </label>
          <label className="auth-field">
            <span>Password</span>
            <input
              autoComplete={isRegistration ? 'new-password' : 'current-password'}
              name="password"
              type="password"
              minLength="8"
              placeholder={isRegistration ? 'At least 8 characters' : 'Enter your password'}
              required
            />
          </label>
          {isRegistration && (
            <label className="auth-field">
              <span>Confirm password</span>
              <input
                autoComplete="new-password"
                name="confirmPassword"
                type="password"
                minLength="8"
                placeholder="Re-enter your password"
                required
              />
            </label>
          )}
          <button className="auth-submit" type="submit" disabled={submitting}>
            {submitting ? 'Please wait…' : isRegistration ? 'Create account' : 'Sign in'}
          </button>
          {notice && <p className="auth-submit-notice" role="status">{notice}</p>}
        </form>

        <p className="auth-switch">
          {isRegistration ? 'Already have an account?' : 'New to RegulaMind AI?'}{' '}
          <a href={isRegistration ? '/login' : '/register'}>
            {isRegistration ? 'Sign in' : 'Create an account'}
          </a>
        </p>
        <p className="auth-disclaimer">
          RBI regulatory assistance · Always verify requirements against official publications.
        </p>
      </section>
    </main>
  )
}

export default AuthPage

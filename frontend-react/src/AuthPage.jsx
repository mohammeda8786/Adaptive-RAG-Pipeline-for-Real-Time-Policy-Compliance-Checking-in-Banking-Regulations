import { useState } from 'react'
import './App.css'

const API_BASE = import.meta.env.VITE_API_BASE_URL
  || `http://${window.location.hostname || '127.0.0.1'}:8000`

function AuthPage({ mode }) {
  const isRegistration = mode === 'register'
  const [notice, setNotice] = useState('')
  const [submitting, setSubmitting] = useState(false)

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

import React, { useState } from 'react'
import { LogIn, Key, ShieldCheck, AlertCircle } from 'lucide-react'
import { api } from '../../api/client'

interface LoginDialogProps {
  open: boolean
  onSuccess: () => void
}

export const LoginDialog: React.FC<LoginDialogProps> = ({ open, onSuccess }) => {
  const [mode, setMode] = useState<'local_password' | 'api_key'>('local_password')
  const [email, setEmail] = useState('admin@example.com')
  const [password, setPassword] = useState('Password123!')
  const [apiKey, setApiKey] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  if (!open) return null

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)

    try {
      if (mode === 'local_password') {
        if (!email.trim() || !password) {
          setError('Email and password are required.')
          setLoading(false)
          return
        }
        await api.connectSession({
          mode: 'local_password',
          email: email.trim(),
          password,
        })
      } else {
        if (!apiKey.trim()) {
          setError('Open WebUI API key is required.')
          setLoading(false)
          return
        }
        await api.connectSession({
          mode: 'api_key',
          api_key: apiKey.trim(),
        })
      }
      onSuccess()
    } catch (err: any) {
      setError(
        err?.data?.detail?.error?.message ||
        err?.message ||
        'Authentication failed. Please check your credentials.'
      )
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0, 0, 0, 0.75)',
        backdropFilter: 'blur(4px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1100,
      }}
    >
      <div
        style={{
          background: 'var(--panel)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-lg)',
          width: '100%',
          maxWidth: 440,
          boxShadow: 'var(--shadow-lg)',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        {/* Header */}
        <div
          style={{
            padding: '20px 24px',
            borderBottom: '1px solid var(--border)',
            display: 'flex',
            alignItems: 'center',
            gap: 12,
          }}
        >
          <div
            style={{
              width: 36,
              height: 36,
              borderRadius: 'var(--radius-md)',
              background: 'var(--accent-subtle)',
              color: 'var(--accent)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <ShieldCheck size={20} />
          </div>
          <div>
            <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: 'var(--text)' }}>
              Connect to Agent Designer
            </h2>
            <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 2 }}>
              Authenticate via your Open WebUI account
            </div>
          </div>
        </div>

        {/* Mode Selector Tabs */}
        <div
          style={{
            display: 'flex',
            borderBottom: '1px solid var(--border)',
            background: 'var(--bg)',
          }}
        >
          <button
            type="button"
            onClick={() => {
              setMode('local_password')
              setError('')
            }}
            style={{
              flex: 1,
              padding: '10px 14px',
              background: mode === 'local_password' ? 'var(--panel)' : 'transparent',
              border: 'none',
              borderBottom: mode === 'local_password' ? '2px solid var(--accent)' : 'none',
              color: mode === 'local_password' ? 'var(--text)' : 'var(--muted)',
              fontSize: 12,
              fontWeight: 500,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 6,
            }}
          >
            <LogIn size={14} /> Password Sign-in
          </button>
          <button
            type="button"
            onClick={() => {
              setMode('api_key')
              setError('')
            }}
            style={{
              flex: 1,
              padding: '10px 14px',
              background: mode === 'api_key' ? 'var(--panel)' : 'transparent',
              border: 'none',
              borderBottom: mode === 'api_key' ? '2px solid var(--accent)' : 'none',
              color: mode === 'api_key' ? 'var(--text)' : 'var(--muted)',
              fontSize: 12,
              fontWeight: 500,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 6,
            }}
          >
            <Key size={14} /> Open WebUI API Key
          </button>
        </div>

        {/* Form Body */}
        <form onSubmit={handleSubmit} style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 16 }}>
          {error && (
            <div
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: 8,
                padding: '10px 12px',
                background: 'rgba(239, 68, 68, 0.1)',
                border: '1px solid var(--red)',
                borderRadius: 'var(--radius-md)',
                color: 'var(--red)',
                fontSize: 12,
              }}
            >
              <AlertCircle size={16} style={{ flexShrink: 0, marginTop: 1 }} />
              <div>{error}</div>
            </div>
          )}

          {mode === 'local_password' ? (
            <>
              <div>
                <label
                  htmlFor="designer-login-email"
                  style={{ display: 'block', fontSize: 12, fontWeight: 500, color: 'var(--text)', marginBottom: 6 }}
                >
                  Email Address
                </label>
                <input
                  id="designer-login-email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="admin@example.com"
                  required
                  style={{
                    width: '100%',
                    padding: '8px 12px',
                    borderRadius: 'var(--radius-md)',
                    border: '1px solid var(--border)',
                    background: 'var(--bg)',
                    color: 'var(--text)',
                    fontSize: 13,
                    outline: 'none',
                    boxSizing: 'border-box',
                  }}
                />
              </div>

              <div>
                <label
                  htmlFor="designer-login-password"
                  style={{ display: 'block', fontSize: 12, fontWeight: 500, color: 'var(--text)', marginBottom: 6 }}
                >
                  Password
                </label>
                <input
                  id="designer-login-password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  required
                  style={{
                    width: '100%',
                    padding: '8px 12px',
                    borderRadius: 'var(--radius-md)',
                    border: '1px solid var(--border)',
                    background: 'var(--bg)',
                    color: 'var(--text)',
                    fontSize: 13,
                    outline: 'none',
                    boxSizing: 'border-box',
                  }}
                />
              </div>
            </>
          ) : (
            <div>
              <label
                htmlFor="designer-login-api-key"
                style={{ display: 'block', fontSize: 12, fontWeight: 500, color: 'var(--text)', marginBottom: 6 }}
              >
                Open WebUI API Key
              </label>
              <input
                id="designer-login-api-key"
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="sk-..."
                required
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  borderRadius: 'var(--radius-md)',
                  border: '1px solid var(--border)',
                  background: 'var(--bg)',
                  color: 'var(--text)',
                  fontSize: 13,
                  outline: 'none',
                  boxSizing: 'border-box',
                }}
              />
            </div>
          )}

          <div
            style={{
              padding: '10px 12px',
              borderRadius: 'var(--radius-md)',
              background: 'var(--bg)',
              border: '1px solid var(--border)',
              fontSize: 11,
              color: 'var(--muted)',
              lineHeight: 1.4,
            }}
          >
            <strong>Local Dev Default:</strong> Log in with Open WebUI account <code>admin@example.com</code> / <code>Password123!</code>.
          </div>

          <button
            id="designer-login-submit"
            type="submit"
            disabled={loading}
            style={{
              marginTop: 4,
              padding: '10px 16px',
              borderRadius: 'var(--radius-md)',
              background: 'var(--text)',
              color: 'var(--bg)',
              border: 'none',
              fontWeight: 600,
              fontSize: 13,
              cursor: loading ? 'not-allowed' : 'pointer',
              opacity: loading ? 0.7 : 1,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 8,
            }}
          >
            {loading ? 'Authenticating...' : 'Sign In & Connect'}
          </button>
        </form>
      </div>
    </div>
  )
}

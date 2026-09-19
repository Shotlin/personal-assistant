import React, { useState } from 'react'
import { X } from 'lucide-react'

interface CreateAgentDialogProps {
  open: boolean
  onClose: () => void
  onCreate: (name: string, description: string, useVionTemplate: boolean) => Promise<void>
}

export const CreateAgentDialog: React.FC<CreateAgentDialogProps> = ({
  open,
  onClose,
  onCreate,
}) => {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [useVionTemplate, setUseVionTemplate] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  if (!open) return null

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) {
      setError('Agent name is required.')
      return
    }
    setLoading(true)
    setError('')
    try {
      await onCreate(name.trim(), description.trim(), useVionTemplate)
      onClose()
      setName('')
      setDescription('')
      setUseVionTemplate(false)
    } catch (err: any) {
      setError(err.message || 'Failed to create agent.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0, 0, 0, 0.6)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
      }}
    >
      <div
        style={{
          background: 'var(--panel)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-lg)',
          width: 440,
          maxWidth: '90vw',
          overflow: 'hidden',
          boxShadow: '0 20px 25px -5px rgba(0, 0, 0, 0.3)',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '16px 20px',
            borderBottom: '1px solid var(--border)',
          }}
        >
          <div style={{ fontWeight: 700, fontSize: 16 }}>Create New Agent</div>
          <button
            onClick={onClose}
            style={{ background: 'transparent', border: 'none', color: 'var(--muted)', display: 'flex' }}
          >
            <X size={18} />
          </button>
        </div>

        <form onSubmit={handleSubmit} style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: 16 }}>
          {error && (
            <div
              style={{
                padding: '8px 12px',
                background: 'rgba(248, 113, 113, 0.1)',
                border: '1px solid var(--red)',
                borderRadius: 'var(--radius-md)',
                color: 'var(--red)',
                fontSize: 12,
              }}
            >
              {error}
            </div>
          )}

          <div>
            <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted)', display: 'block', marginBottom: 6 }}>
              Agent Name *
            </label>
            <input
              type="text"
              placeholder="e.g. Research Assistant"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
              style={{
                width: '100%',
                padding: '8px 12px',
                background: 'var(--surface)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-md)',
                color: 'var(--text)',
                fontSize: 13,
                outline: 'none',
              }}
            />
          </div>

          <div>
            <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted)', display: 'block', marginBottom: 6 }}>
              Description
            </label>
            <textarea
              rows={3}
              placeholder="Describe the agent's purpose and capabilities..."
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              style={{
                width: '100%',
                padding: '8px 12px',
                background: 'var(--surface)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-md)',
                color: 'var(--text)',
                fontSize: 13,
                outline: 'none',
                resize: 'none',
              }}
            />
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <input
              type="checkbox"
              id="vion-template"
              checked={useVionTemplate}
              onChange={(e) => setUseVionTemplate(e.target.checked)}
              style={{ cursor: 'pointer' }}
            />
            <label htmlFor="vion-template" style={{ fontSize: 13, color: 'var(--text)', cursor: 'pointer' }}>
              Use Vion as template (pre-configures CUA desktop & Claude)
            </label>
          </div>

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 8 }}>
            <button
              type="button"
              onClick={onClose}
              style={{
                padding: '8px 14px',
                background: 'var(--surface)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-md)',
                color: 'var(--text)',
                fontSize: 13,
                fontWeight: 600,
              }}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading}
              style={{
                padding: '8px 18px',
                background: 'var(--text)',
                color: 'var(--bg)',
                border: 'none',
                borderRadius: 'var(--radius-md)',
                fontSize: 13,
                fontWeight: 700,
                opacity: loading ? 0.6 : 1,
              }}
            >
              {loading ? 'Creating...' : 'Create Draft'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

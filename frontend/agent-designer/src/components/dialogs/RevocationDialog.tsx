import React, { useState } from 'react'
import { AlertOctagon, X } from 'lucide-react'
import { useCanvasStore } from '../../state/canvasStore'

interface RevocationDialogProps {
  open: boolean
  onClose: () => void
  onConfirm: () => Promise<void>
}

export const RevocationDialog: React.FC<RevocationDialogProps> = ({
  open,
  onClose,
  onConfirm,
}) => {
  const agent = useCanvasStore((s) => s.agent)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  if (!open) return null

  const activeRevNum = agent?.active_revision_number

  const handleRevoke = async () => {
    setLoading(true)
    setError('')
    try {
      await onConfirm()
      onClose()
    } catch (err: any) {
      setError(err?.message || 'Revocation failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0, 0, 0, 0.7)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
        backdropFilter: 'blur(4px)',
      }}
    >
      <div
        style={{
          background: 'var(--panel)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-lg)',
          width: 480,
          maxWidth: '90vw',
          overflow: 'hidden',
          boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.5)',
        }}
      >
        {/* Header */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '16px 20px',
            borderBottom: '1px solid var(--border)',
            background: 'rgba(248, 113, 113, 0.08)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--red)' }}>
            <AlertOctagon size={20} />
            <span style={{ fontWeight: 700, fontSize: 16 }}>Revoke Active Revision</span>
          </div>
          <button
            onClick={onClose}
            style={{ background: 'transparent', border: 'none', color: 'var(--muted)', display: 'flex' }}
          >
            <X size={18} />
          </button>
        </div>

        {/* Content */}
        <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: 16 }}>
          {error && (
            <div
              style={{
                padding: '10px 14px',
                background: 'rgba(248, 113, 113, 0.1)',
                border: '1px solid var(--red)',
                borderRadius: 'var(--radius-md)',
                color: 'var(--red)',
                fontSize: 12,
              }}
            >
              <strong>Error:</strong> {error}
            </div>
          )}

          <div style={{ fontSize: 13, lineHeight: 1.5, color: 'var(--text)' }}>
            <p style={{ marginBottom: 8 }}>
              Revoke this capability for queued and active runs. The active revision pointer{' '}
              {activeRevNum ? <strong>(v{activeRevNum})</strong> : ''} will be immediately cleared to null.
            </p>
            <p style={{ color: 'var(--muted)', fontSize: 12 }}>
              Runtime instances will be drained and subsequent dispatches will be blocked until a new revision is activated.
            </p>
          </div>

          <div
            style={{
              padding: '10px 14px',
              background: 'rgba(248, 113, 113, 0.12)',
              border: '1px solid var(--red)',
              borderRadius: 'var(--radius-md)',
              color: 'var(--red)',
              fontSize: 12,
              fontWeight: 500,
            }}
          >
            ⚠️ Warning: Already performed external tool executions and published actions cannot be undone.
          </div>

          {/* Action buttons */}
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
              onClick={handleRevoke}
              disabled={loading}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                padding: '8px 18px',
                background: 'var(--red)',
                color: '#FFFFFF',
                border: 'none',
                borderRadius: 'var(--radius-md)',
                fontSize: 13,
                fontWeight: 700,
                opacity: loading ? 0.6 : 1,
                cursor: 'pointer',
              }}
            >
              <AlertOctagon size={14} />
              <span>{loading ? 'Revoking...' : 'Revoke Now'}</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

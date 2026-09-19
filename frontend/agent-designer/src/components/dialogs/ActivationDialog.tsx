import React, { useState } from 'react'
import { AlertTriangle, Check, X } from 'lucide-react'
import { useCanvasStore } from '../../state/canvasStore'

interface ActivationDialogProps {
  open: boolean
  onClose: () => void
  onConfirm: () => Promise<void>
}

export const ActivationDialog: React.FC<ActivationDialogProps> = ({
  open,
  onClose,
  onConfirm,
}) => {
  const agent = useCanvasStore((s) => s.agent)
  const isDirty = useCanvasStore((s) => s.isDirty)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  if (!open) return null

  const handleActivate = async () => {
    setLoading(true)
    setError('')
    try {
      await onConfirm()
      onClose()
    } catch (err: any) {
      setError(err.message || 'Activation failed.')
    } finally {
      setLoading(false)
    }
  }

  const activeRevNum = agent?.active_revision_number
  const candidateRevNum = agent?.draft?.revision_number || 1

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
          width: 460,
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
          <div style={{ fontWeight: 700, fontSize: 16 }}>Activate Agent Revision</div>
          <button
            onClick={onClose}
            style={{ background: 'transparent', border: 'none', color: 'var(--muted)', display: 'flex' }}
          >
            <X size={18} />
          </button>
        </div>

        <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: 16 }}>
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

          {isDirty ? (
            <div
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: 10,
                padding: '12px',
                background: 'rgba(251, 191, 36, 0.1)',
                border: '1px solid var(--amber)',
                borderRadius: 'var(--radius-md)',
                color: 'var(--amber)',
                fontSize: 13,
              }}
            >
              <AlertTriangle size={18} style={{ flexShrink: 0, marginTop: 2 }} />
              <div>
                <strong>Unsaved changes detected.</strong> Please save your draft revision before
                activating.
              </div>
            </div>
          ) : (
            <div>
              <p style={{ fontSize: 13, color: 'var(--text)', lineHeight: 1.5, marginBottom: 12 }}>
                Activating will swap the serving pointer to <strong>Revision v{candidateRevNum}</strong> via
                atomic CAS and immediately invalidate older runtime instances.
              </p>

              {activeRevNum ? (
                <div style={{ fontSize: 12, color: 'var(--muted)' }}>
                  Currently active: <strong>v{activeRevNum}</strong>.
                </div>
              ) : (
                <div style={{ fontSize: 12, color: 'var(--muted)' }}>
                  This agent has no active revision currently serving.
                </div>
              )}
            </div>
          )}

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
              onClick={handleActivate}
              disabled={loading || isDirty}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                padding: '8px 18px',
                background: 'var(--text)',
                color: 'var(--bg)',
                border: 'none',
                borderRadius: 'var(--radius-md)',
                fontSize: 13,
                fontWeight: 700,
                opacity: loading || isDirty ? 0.5 : 1,
              }}
            >
              <Check size={14} />
              <span>{loading ? 'Activating...' : 'Confirm Activation'}</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

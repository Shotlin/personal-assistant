import React from 'react'
import { AlertCircle, CheckCircle2 } from 'lucide-react'
import { useCanvasStore } from '../../state/canvasStore'

export const DiagnosticsStrip: React.FC = () => {
  const isDirty = useCanvasStore((s) => s.isDirty)
  const validationReport = useCanvasStore((s) => s.validationReport)
  const agent = useCanvasStore((s) => s.agent)

  const issueCount = validationReport?.issues?.length ?? 0
  const hasIssues = issueCount > 0

  return (
    <div
      style={{
        height: 'var(--diagnostics-height)',
        background: 'var(--panel)',
        borderTop: '1px solid var(--border)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0 16px',
        fontSize: 12,
        color: 'var(--muted)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          {hasIssues ? (
            <AlertCircle size={14} style={{ color: 'var(--red)' }} />
          ) : (
            <CheckCircle2 size={14} style={{ color: 'var(--green)' }} />
          )}
          <span style={{ color: hasIssues ? 'var(--red)' : 'var(--text)' }}>
            Diagnostics: {issueCount} {issueCount === 1 ? 'error' : 'errors'}
          </span>
        </div>

        {hasIssues && (
          <span style={{ color: 'var(--muted)' }}>
            ({validationReport?.issues[0]?.message})
          </span>
        )}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        <div>
          {isDirty ? (
            <span style={{ color: 'var(--amber)', fontWeight: 600 }}>● Unsaved changes</span>
          ) : (
            <span style={{ color: 'var(--muted)' }}>All changes saved</span>
          )}
        </div>

        <div style={{ width: 1, height: 14, background: 'var(--border)' }} />

        <div>
          {agent?.active_revision_id ? (
            <span>Active revision: v{agent.active_revision_number} (serving)</span>
          ) : (
            <span>Runtime unchanged (not active)</span>
          )}
        </div>
      </div>
    </div>
  )
}

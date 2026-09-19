import React from 'react'
import { CheckCircle, Play, Redo2, Save, Undo2 } from 'lucide-react'
import { useCanvasStore } from '../../state/canvasStore'

interface ModeBarProps {
  onSave: () => void
  onValidate: () => void
  onActivate: () => void
}

export const ModeBar: React.FC<ModeBarProps> = ({
  onSave,
  onValidate,
  onActivate,
}) => {
  const mode = useCanvasStore((s) => s.mode)
  const setMode = useCanvasStore((s) => s.setMode)
  const isDirty = useCanvasStore((s) => s.isDirty)
  const isSaving = useCanvasStore((s) => s.isSaving)
  const isValidating = useCanvasStore((s) => s.isValidating)
  const undo = useCanvasStore((s) => s.undo)
  const redo = useCanvasStore((s) => s.redo)
  const undoAvailable = useCanvasStore((s) => s.undoStack.length > 0)
  const redoAvailable = useCanvasStore((s) => s.redoStack.length > 0)

  return (
    <div
      style={{
        height: 'var(--modebar-height)',
        background: 'var(--surface)',
        borderBottom: '1px solid var(--border)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0 16px',
      }}
    >
      {/* Mode navigation tabs */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        {(['design', 'live', 'history'] as const).map((m) => {
          const active = mode === m
          return (
            <button
              key={m}
              onClick={() => setMode(m)}
              style={{
                background: active ? 'var(--panel)' : 'transparent',
                color: active ? 'var(--text)' : 'var(--muted)',
                border: active ? '1px solid var(--border)' : '1px solid transparent',
                borderRadius: 'var(--radius-md)',
                padding: '5px 14px',
                fontSize: 13,
                fontWeight: active ? 600 : 500,
                textTransform: 'capitalize',
                transition: 'all 0.15s ease',
              }}
            >
              {m}
            </button>
          )
        })}
      </div>

      {/* Primary actions */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {/* Undo / Redo */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 2, marginRight: 8 }}>
          <button
            onClick={undo}
            disabled={!undoAvailable}
            title="Undo (Ctrl+Z)"
            style={{
              background: 'transparent',
              color: undoAvailable ? 'var(--text)' : 'var(--muted)',
              border: 'none',
              padding: '6px',
              borderRadius: 'var(--radius-sm)',
              opacity: undoAvailable ? 1 : 0.4,
              display: 'flex',
            }}
          >
            <Undo2 size={16} />
          </button>
          <button
            onClick={redo}
            disabled={!redoAvailable}
            title="Redo (Ctrl+Y)"
            style={{
              background: 'transparent',
              color: redoAvailable ? 'var(--text)' : 'var(--muted)',
              border: 'none',
              padding: '6px',
              borderRadius: 'var(--radius-sm)',
              opacity: redoAvailable ? 1 : 0.4,
              display: 'flex',
            }}
          >
            <Redo2 size={16} />
          </button>
        </div>

        {/* Save draft */}
        <button
          onClick={onSave}
          disabled={!isDirty || isSaving}
          title="Save immutable draft revision"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            background: isDirty ? 'var(--panel)' : 'var(--surface)',
            color: 'var(--text)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-md)',
            padding: '6px 14px',
            fontSize: 13,
            fontWeight: 600,
            opacity: isDirty ? 1 : 0.6,
            cursor: isDirty && !isSaving ? 'pointer' : 'default',
          }}
        >
          <Save size={14} />
          <span>{isSaving ? 'Saving...' : 'Save draft'}</span>
        </button>

        {/* Validate */}
        <button
          onClick={onValidate}
          disabled={isValidating}
          title="Run non-destructive graph validation"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            background: 'var(--panel)',
            color: 'var(--text)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-md)',
            padding: '6px 14px',
            fontSize: 13,
            fontWeight: 600,
          }}
        >
          <CheckCircle size={14} style={{ color: 'var(--blue)' }} />
          <span>{isValidating ? 'Validating...' : 'Validate'}</span>
        </button>

        {/* Activate */}
        <button
          onClick={onActivate}
          title="Prepare candidate and CAS activate"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            background: 'var(--text)',
            color: 'var(--bg)',
            border: 'none',
            borderRadius: 'var(--radius-md)',
            padding: '6px 16px',
            fontSize: 13,
            fontWeight: 700,
          }}
        >
          <Play size={13} fill="currentColor" />
          <span>Activate</span>
        </button>
      </div>
    </div>
  )
}

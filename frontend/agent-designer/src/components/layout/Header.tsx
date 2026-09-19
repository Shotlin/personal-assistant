import React from 'react'
import { ArrowLeft, ChevronDown, Moon, Plus, Sun } from 'lucide-react'
import { useCanvasStore } from '../../state/canvasStore'
import type { AgentSummary } from '../../types/designer'

interface HeaderProps {
  agents: AgentSummary[]
  onSelectAgent: (agentId: string) => void
  onCreateAgent: () => void
}

export const Header: React.FC<HeaderProps> = ({
  agents,
  onSelectAgent,
  onCreateAgent,
}) => {
  const agent = useCanvasStore((s) => s.agent)
  const isDirty = useCanvasStore((s) => s.isDirty)
  const theme = useCanvasStore((s) => s.theme)
  const setTheme = useCanvasStore((s) => s.setTheme)

  const activeRev = agent?.active_revision_number
    ? `Active v${agent.active_revision_number}`
    : 'Not active'

  const draftRev = agent?.draft?.revision_number
    ? `Draft v${agent.draft.revision_number}`
    : 'Draft v1'

  const toggleTheme = () => {
    const next = theme === 'dark' ? 'light' : 'dark'
    setTheme(next)
  }

  return (
    <header
      style={{
        height: 'var(--header-height)',
        background: 'var(--panel)',
        borderBottom: '1px solid var(--border)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0 16px',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        <a
          href="/"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            color: 'var(--muted)',
            textDecoration: 'none',
            fontSize: 13,
            fontWeight: 500,
            transition: 'color 0.15s',
          }}
          onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--text)')}
          onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--muted)')}
        >
          <ArrowLeft size={16} />
          <span>Open WebUI</span>
        </a>

        <div style={{ width: 1, height: 20, background: 'var(--border)' }} />

        <div style={{ fontSize: 15, fontWeight: 700, letterSpacing: -0.2 }}>
          Agent Designer
        </div>

        {/* Agent dropdown */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ position: 'relative', display: 'inline-block' }}>
            <select
              value={agent?.agent_id || ''}
              onChange={(e) => onSelectAgent(e.target.value)}
              style={{
                background: 'var(--surface)',
                color: 'var(--text)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-md)',
                padding: '5px 28px 5px 10px',
                fontSize: 13,
                fontWeight: 600,
                appearance: 'none',
                cursor: 'pointer',
              }}
            >
              {agents.map((a) => (
                <option key={a.agent_id} value={a.agent_id}>
                  {a.name}
                </option>
              ))}
            </select>
            <ChevronDown
              size={14}
              style={{
                position: 'absolute',
                right: 8,
                top: '50%',
                transform: 'translateY(-50%)',
                pointerEvents: 'none',
                color: 'var(--muted)',
              }}
            />
          </div>

          <button
            onClick={onCreateAgent}
            title="Create new agent"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 4,
              background: 'var(--surface)',
              color: 'var(--text)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-md)',
              padding: '5px 10px',
              fontSize: 12,
              fontWeight: 500,
            }}
          >
            <Plus size={14} />
            <span>New</span>
          </button>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        {/* Revision indicators */}
        <div
          style={{
            fontSize: 12,
            padding: '3px 8px',
            borderRadius: 'var(--radius-sm)',
            background: agent?.active_revision_id ? 'rgba(74, 222, 128, 0.1)' : 'var(--surface)',
            border: `1px solid ${agent?.active_revision_id ? 'var(--green)' : 'var(--border)'}`,
            color: agent?.active_revision_id ? 'var(--green)' : 'var(--muted)',
            fontWeight: 500,
          }}
        >
          {activeRev}
        </div>

        <div
          style={{
            fontSize: 12,
            padding: '3px 8px',
            borderRadius: 'var(--radius-sm)',
            background: isDirty ? 'rgba(251, 191, 36, 0.1)' : 'var(--surface)',
            border: `1px solid ${isDirty ? 'var(--amber)' : 'var(--border)'}`,
            color: isDirty ? 'var(--amber)' : 'var(--text)',
            fontWeight: 500,
          }}
        >
          {draftRev} {isDirty && '• Unsaved'}
        </div>

        {/* Theme Toggle */}
        <button
          onClick={toggleTheme}
          title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
          style={{
            background: 'var(--surface)',
            color: 'var(--text)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-md)',
            padding: '6px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          {theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
        </button>
      </div>
    </header>
  )
}

import React from 'react'
import { useCanvasStore } from '../../../state/canvasStore'

interface NodeWrapperProps {
  id: string
  selected?: boolean
  title: string
  subtitle?: string
  icon?: React.ReactNode
  badge?: string
  statusColor?: string
  children?: React.ReactNode
}

export const NodeWrapper: React.FC<NodeWrapperProps> = ({
  id,
  selected,
  title,
  subtitle,
  icon,
  badge,
  statusColor,
  children,
}) => {
  const validationReport = useCanvasStore((s) => s.validationReport)
  const nodeIssue = validationReport?.issues?.find((i) => i.node_id === id)

  const isError = Boolean(nodeIssue)

  return (
    <div
      style={{
        background: 'var(--node-bg)',
        border: `1.5px solid ${
          isError
            ? 'var(--red)'
            : selected
            ? 'var(--node-selected)'
            : 'var(--node-border)'
        }`,
        borderRadius: 'var(--radius-md)',
        minWidth: 190,
        boxShadow: selected ? '0 0 0 2px rgba(96, 165, 250, 0.2)' : '0 1px 3px rgba(0,0,0,0.1)',
        overflow: 'hidden',
        fontSize: 13,
        color: 'var(--text)',
        transition: 'border-color 0.15s ease, box-shadow 0.15s ease',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '8px 10px',
          background: 'var(--surface)',
          borderBottom: '1px solid var(--border)',
        }}
      >
        {icon && <span style={{ color: statusColor || 'var(--muted)', display: 'flex' }}>{icon}</span>}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {title}
          </div>
          {subtitle && (
            <div style={{ fontSize: 11, color: 'var(--muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {subtitle}
            </div>
          )}
        </div>
        {badge && (
          <span
            style={{
              fontSize: 10,
              padding: '2px 5px',
              borderRadius: 'var(--radius-sm)',
              background: 'var(--panel)',
              border: '1px solid var(--border)',
              color: 'var(--muted)',
            }}
          >
            {badge}
          </span>
        )}
      </div>

      {children && <div style={{ padding: '8px 10px' }}>{children}</div>}

      {nodeIssue && (
        <div
          style={{
            padding: '4px 8px',
            background: 'rgba(248, 113, 113, 0.1)',
            borderTop: '1px solid var(--red)',
            fontSize: 11,
            color: 'var(--red)',
          }}
        >
          {nodeIssue.message}
        </div>
      )}
    </div>
  )
}

import React from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { Globe, Shield } from 'lucide-react'
import { NodeWrapper } from './NodeWrapper'

export const ConnectorNode: React.FC<NodeProps> = ({ id, selected, data }) => {
  const config = (data.config as Record<string, any>) || {}
  const kind = config.connector_type || (data.type === 'cua' ? 'CUA Desktop' : 'MCP Connector')
  const health = config.health || 'ONLINE'

  const healthColor =
    health === 'ONLINE'
      ? 'var(--green)'
      : health === 'QUARANTINED'
      ? 'var(--red)'
      : 'var(--amber)'

  return (
    <div style={{ position: 'relative' }}>
      <Handle
        type="source"
        position={Position.Left}
        id="connector-out"
        style={{ background: 'var(--green)' }}
      />

      <NodeWrapper
        id={id}
        selected={selected}
        title={(data.label as string) || 'Connector'}
        subtitle={kind}
        icon={data.type === 'cua' ? <Shield size={16} /> : <Globe size={16} />}
        badge="CONNECTION"
        statusColor={healthColor}
      >
        <div style={{ fontSize: 11, display: 'flex', alignItems: 'center', gap: 5 }}>
          <span
            style={{
              width: 7,
              height: 7,
              borderRadius: '50%',
              background: healthColor,
              display: 'inline-block',
            }}
          />
          <span style={{ color: 'var(--muted)' }}>Health: {health}</span>
        </div>
      </NodeWrapper>
    </div>
  )
}

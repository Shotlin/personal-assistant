import React from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { Brain } from 'lucide-react'
import { NodeWrapper } from './NodeWrapper'

export const MemoryNode: React.FC<NodeProps> = ({ id, selected, data }) => {
  const config = (data.config as Record<string, any>) || {}
  const kind = config.kind || 'thread'

  return (
    <div style={{ position: 'relative' }}>
      <NodeWrapper
        id={id}
        selected={selected}
        title={(data.label as string) || 'Memory'}
        subtitle={`Kind: ${kind}`}
        icon={<Brain size={16} />}
        badge="MEMORY"
        statusColor="var(--amber)"
      >
        <div style={{ fontSize: 11, color: 'var(--muted)' }}>
          Strategy: mem0 / PGStore
        </div>
      </NodeWrapper>

      <Handle
        type="source"
        position={Position.Right}
        id="memory-out"
        style={{ background: 'var(--amber)' }}
      />
    </div>
  )
}

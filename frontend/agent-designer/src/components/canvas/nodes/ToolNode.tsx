import React from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { Wrench } from 'lucide-react'
import { NodeWrapper } from './NodeWrapper'

export const ToolNode: React.FC<NodeProps> = ({ id, selected, data }) => {
  const config = (data.config as Record<string, any>) || {}
  const toolName = config.name || data.label || 'Tool'

  return (
    <div style={{ position: 'relative' }}>
      <Handle
        type="source"
        position={Position.Left}
        id="tool-out"
        style={{ background: 'var(--green)' }}
      />

      <NodeWrapper
        id={id}
        selected={selected}
        title={toolName}
        subtitle="Execution Tool"
        icon={<Wrench size={16} />}
        badge="TOOL"
        statusColor="var(--green)"
      >
        <div style={{ fontSize: 11, color: 'var(--muted)' }}>
          Mode: {config.mode || 'Automatic'}
        </div>
      </NodeWrapper>
    </div>
  )
}

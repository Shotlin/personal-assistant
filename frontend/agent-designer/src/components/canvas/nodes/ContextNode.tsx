import React from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { Sliders } from 'lucide-react'
import { NodeWrapper } from './NodeWrapper'

export const ContextNode: React.FC<NodeProps> = ({ id, selected, data }) => {
  const config = (data.config as Record<string, any>) || {}
  const turns = config.max_turns ?? 12
  const maxInputTokens = config.max_input_tokens ?? 32000

  return (
    <div style={{ position: 'relative' }}>
      <Handle
        type="source"
        position={Position.Top}
        id="context-out"
        style={{ background: 'var(--muted)' }}
      />

      <NodeWrapper
        id={id}
        selected={selected}
        title={(data.label as string) || 'Context Policy'}
        subtitle="Token & Budget Policy"
        icon={<Sliders size={16} />}
        badge="POLICY"
      >
        <div style={{ fontSize: 11, color: 'var(--muted)', display: 'flex', justifyContent: 'space-between' }}>
          <span>Turns: {turns}</span>
          <span>Max In: {maxInputTokens.toLocaleString()}</span>
        </div>
      </NodeWrapper>
    </div>
  )
}

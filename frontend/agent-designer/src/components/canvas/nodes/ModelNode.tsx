import React from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { Cpu } from 'lucide-react'
import { NodeWrapper } from './NodeWrapper'

export const ModelNode: React.FC<NodeProps> = ({ id, selected, data }) => {
  const config = (data.config as Record<string, any>) || {}
  const modelId = config.model_id || (config.provider ? `${config.provider} (configured)` : 'unset')

  return (
    <div style={{ position: 'relative' }}>
      <NodeWrapper
        id={id}
        selected={selected}
        title={(data.label as string) || 'Model'}
        subtitle={modelId}
        icon={<Cpu size={16} />}
        badge="MODEL"
        statusColor="var(--blue)"
      >
        <div style={{ fontSize: 11, color: 'var(--muted)', display: 'flex', justifyContent: 'space-between' }}>
          <span>Temp: {config.temperature ?? 0.0}</span>
          <span>Max: {config.max_tokens ?? 4096}</span>
        </div>
      </NodeWrapper>

      <Handle
        type="source"
        position={Position.Right}
        id="model-out"
        style={{ background: 'var(--blue)' }}
      />
    </div>
  )
}

import React from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { FileText } from 'lucide-react'
import { NodeWrapper } from './NodeWrapper'

export const PromptNode: React.FC<NodeProps> = ({ id, selected, data }) => {
  const config = (data.config as Record<string, any>) || {}
  const ref = config.resource_ref
    ? String(config.resource_ref)
    : config.source
      ? `${config.source}: inline`
      : 'inline'
  const snippet = config.system_prompt
    ? `${String(config.system_prompt).slice(0, 40)}...`
    : config.resource_ref
      ? 'loaded from resource'
      : 'No instructions set'

  return (
    <div style={{ position: 'relative' }}>
      <NodeWrapper
        id={id}
        selected={selected}
        title={(data.label as string) || 'System Prompt'}
        subtitle={ref}
        icon={<FileText size={16} />}
        badge="PROMPT"
      >
        <div style={{ fontSize: 11, color: 'var(--muted)', fontStyle: 'italic' }}>
          "{snippet}"
        </div>
      </NodeWrapper>

      <Handle
        type="source"
        position={Position.Right}
        id="prompt-out"
        style={{ background: 'var(--text)' }}
      />
    </div>
  )
}

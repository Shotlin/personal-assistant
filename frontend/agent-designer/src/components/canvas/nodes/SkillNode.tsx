import React from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { Sparkles } from 'lucide-react'
import { NodeWrapper } from './NodeWrapper'

export const SkillNode: React.FC<NodeProps> = ({ id, selected, data }) => {
  const config = (data.config as Record<string, any>) || {}
  const skillName = config.name || data.label || 'Skill'

  return (
    <div style={{ position: 'relative' }}>
      <Handle
        type="source"
        position={Position.Left}
        id="skill-out"
        style={{ background: 'var(--green)' }}
      />

      <NodeWrapper
        id={id}
        selected={selected}
        title={skillName}
        subtitle={config.source ? `${config.source}: ${config.id || skillName}` : 'Skill'}
        icon={<Sparkles size={16} />}
        badge="SKILL"
        statusColor="var(--green)"
      >
        <div style={{ fontSize: 11, color: 'var(--muted)' }}>
          Capability: {config.capability || 'EXECUTABLE'}
        </div>
      </NodeWrapper>
    </div>
  )
}

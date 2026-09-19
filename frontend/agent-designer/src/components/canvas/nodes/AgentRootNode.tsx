import React from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { Bot } from 'lucide-react'
import { NodeWrapper } from './NodeWrapper'

export const AgentRootNode: React.FC<NodeProps> = ({ id, selected, data }) => {
  return (
    <div style={{ position: 'relative' }}>
      <Handle
        type="target"
        position={Position.Left}
        id="model"
        style={{ top: '25%', background: 'var(--blue)' }}
      />
      <Handle
        type="target"
        position={Position.Left}
        id="prompt"
        style={{ top: '50%', background: 'var(--text)' }}
      />
      <Handle
        type="target"
        position={Position.Left}
        id="memory"
        style={{ top: '75%', background: 'var(--amber)' }}
      />
      <Handle
        type="target"
        position={Position.Right}
        id="tools"
        style={{ top: '50%', background: 'var(--green)' }}
      />
      <Handle
        type="target"
        position={Position.Bottom}
        id="context"
        style={{ left: '50%', background: 'var(--muted)' }}
      />

      <NodeWrapper
        id={id}
        selected={selected}
        title={(data.label as string) || 'Agent Core'}
        subtitle="Vion Architecture"
        icon={<Bot size={16} />}
        badge="ROOT"
        statusColor="var(--blue)"
      >
        <div style={{ fontSize: 11, color: 'var(--muted)', display: 'flex', flexDirection: 'column', gap: 3 }}>
          <div>Inputs: Model, Prompt, Memory</div>
          <div>Outputs: CUA, MCP Tools, Skills</div>
        </div>
      </NodeWrapper>
    </div>
  )
}

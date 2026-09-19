import React from 'react'
import { Trash2, X } from 'lucide-react'
import { useCanvasStore } from '../../state/canvasStore'

export const PropertiesPanel: React.FC = () => {
  const selectedNodeId = useCanvasStore((s) => s.selectedNodeId)
  const setSelectedNodeId = useCanvasStore((s) => s.setSelectedNodeId)
  const nodes = useCanvasStore((s) => s.nodes)
  const updateNodeConfig = useCanvasStore((s) => s.updateNodeConfig)
  const removeNode = useCanvasStore((s) => s.removeNode)
  const agent = useCanvasStore((s) => s.agent)

  const selectedNode = nodes.find((n) => n.id === selectedNodeId)

  if (!selectedNode) {
    return (
      <aside
        style={{
          width: 'var(--inspector-width)',
          height: 'calc(100vh - var(--header-height) - var(--modebar-height) - var(--diagnostics-height))',
          background: 'var(--panel)',
          borderLeft: '1px solid var(--border)',
          display: 'flex',
          flexDirection: 'column',
          padding: '16px',
          overflowY: 'auto',
          fontSize: 13,
          color: 'var(--muted)',
        }}
      >
        <div style={{ fontWeight: 700, color: 'var(--text)', marginBottom: 12 }}>
          Agent Properties
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div>
            <div style={{ fontSize: 11, fontWeight: 600 }}>Name</div>
            <div style={{ color: 'var(--text)', marginTop: 2 }}>{agent?.name || 'Agent'}</div>
          </div>
          <div>
            <div style={{ fontSize: 11, fontWeight: 600 }}>Slug / ID</div>
            <div style={{ color: 'var(--text)', fontFamily: 'var(--font-mono)', fontSize: 12, marginTop: 2 }}>
              {agent?.agent_id || 'None'}
            </div>
          </div>
          <div>
            <div style={{ fontSize: 11, fontWeight: 600 }}>Active Revision</div>
            <div style={{ color: 'var(--text)', marginTop: 2 }}>
              {agent?.active_revision_number ? `v${agent.active_revision_number}` : 'None'}
            </div>
          </div>
          <div style={{ marginTop: 20, fontStyle: 'italic', fontSize: 12 }}>
            Select any node on the canvas to inspect and configure its properties.
          </div>
        </div>
      </aside>
    )
  }

  const config = (selectedNode.data.config as Record<string, any>) || {}
  const type = selectedNode.type

  const handleConfigChange = (key: string, value: any) => {
    updateNodeConfig(selectedNode.id, { [key]: value })
  }

  return (
    <aside
      style={{
        width: 'var(--inspector-width)',
        height: 'calc(100vh - var(--header-height) - var(--modebar-height) - var(--diagnostics-height))',
        background: 'var(--panel)',
        borderLeft: '1px solid var(--border)',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        fontSize: 13,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '12px 16px',
          borderBottom: '1px solid var(--border)',
          background: 'var(--surface)',
        }}
      >
        <div>
          <div style={{ fontWeight: 700, color: 'var(--text)' }}>
            {(selectedNode.data.label as string) || selectedNode.id}
          </div>
          <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase' }}>
            {type} node
          </div>
        </div>

        <button
          onClick={() => setSelectedNodeId(null)}
          style={{ background: 'transparent', border: 'none', color: 'var(--muted)', display: 'flex' }}
        >
          <X size={16} />
        </button>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '16px', display: 'flex', flexDirection: 'column', gap: 16 }}>
        {/* Model Node Controls */}
        {type === 'model' && (
          <>
            <div>
              <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>
                Model Identifier
              </label>
              <select
                value={config.model_id || 'claude-3-5-sonnet'}
                onChange={(e) => handleConfigChange('model_id', e.target.value)}
                style={{
                  width: '100%',
                  padding: '6px 8px',
                  background: 'var(--surface)',
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--radius-md)',
                  color: 'var(--text)',
                }}
              >
                <option value="claude-3-5-sonnet">Claude 3.5 Sonnet</option>
                <option value="gpt-4o">GPT-4o</option>
                <option value="claude-3-haiku">Claude 3 Haiku</option>
                <option value="deepseek-r1">DeepSeek R1</option>
              </select>
            </div>

            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--muted)' }}>
                  Temperature
                </label>
                <span style={{ fontSize: 11, color: 'var(--text)' }}>{config.temperature ?? 0.0}</span>
              </div>
              <input
                type="range"
                min="0"
                max="1"
                step="0.05"
                value={config.temperature ?? 0.0}
                onChange={(e) => handleConfigChange('temperature', parseFloat(e.target.value))}
                style={{ width: '100%' }}
              />
            </div>

            <div>
              <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>
                Max Tokens
              </label>
              <input
                type="number"
                value={config.max_tokens ?? 4096}
                onChange={(e) => handleConfigChange('max_tokens', parseInt(e.target.value, 10))}
                style={{
                  width: '100%',
                  padding: '6px 8px',
                  background: 'var(--surface)',
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--radius-md)',
                  color: 'var(--text)',
                }}
              />
            </div>
          </>
        )}

        {/* Prompt Node Controls */}
        {type === 'prompt' && (
          <div>
            <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>
              System Prompt
            </label>
            <textarea
              rows={8}
              value={config.system_prompt || ''}
              onChange={(e) => handleConfigChange('system_prompt', e.target.value)}
              placeholder="Enter system instructions..."
              style={{
                width: '100%',
                padding: '8px',
                background: 'var(--surface)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-md)',
                color: 'var(--text)',
                fontSize: 12,
                fontFamily: 'var(--font-mono)',
                resize: 'vertical',
              }}
            />
          </div>
        )}

        {/* Memory Node Controls */}
        {type === 'memory' && (
          <div>
            <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>
              Memory Kind
            </label>
            <select
              value={config.kind || 'thread'}
              onChange={(e) => handleConfigChange('kind', e.target.value)}
              style={{
                width: '100%',
                padding: '6px 8px',
                background: 'var(--surface)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-md)',
                color: 'var(--text)',
              }}
            >
              <option value="thread">Thread (conversational)</option>
              <option value="user">User (profile & facts)</option>
              <option value="project">Project (workspace facts)</option>
            </select>
          </div>
        )}

        {/* Context Node Controls */}
        {type === 'context' && (
          <>
            <div>
              <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>
                Max Turns
              </label>
              <input
                type="number"
                value={config.max_turns ?? 12}
                onChange={(e) => handleConfigChange('max_turns', parseInt(e.target.value, 10))}
                style={{
                  width: '100%',
                  padding: '6px 8px',
                  background: 'var(--surface)',
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--radius-md)',
                  color: 'var(--text)',
                }}
              />
            </div>
            <div>
              <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>
                Max Input Tokens
              </label>
              <input
                type="number"
                value={config.max_input_tokens ?? 32000}
                onChange={(e) => handleConfigChange('max_input_tokens', parseInt(e.target.value, 10))}
                style={{
                  width: '100%',
                  padding: '6px 8px',
                  background: 'var(--surface)',
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--radius-md)',
                  color: 'var(--text)',
                }}
              />
            </div>
          </>
        )}

        {/* Common: Delete button */}
        {selectedNode.id !== 'agent_root' && (
          <div style={{ marginTop: 'auto', paddingTop: 16, borderTop: '1px solid var(--border)' }}>
            <button
              onClick={() => removeNode(selectedNode.id)}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 6,
                width: '100%',
                padding: '8px',
                background: 'rgba(248, 113, 113, 0.1)',
                border: '1px solid var(--red)',
                borderRadius: 'var(--radius-md)',
                color: 'var(--red)',
                fontSize: 12,
                fontWeight: 600,
              }}
            >
              <Trash2 size={14} />
              <span>Remove Node</span>
            </button>
          </div>
        )}
      </div>
    </aside>
  )
}

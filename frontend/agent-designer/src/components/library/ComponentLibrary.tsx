import React, { useState } from 'react'
import {
  Brain,
  Cpu,
  FileText,
  Globe,
  Plus,
  Search,
  Sliders,
  Sparkles,
} from 'lucide-react'
import { useCanvasStore } from '../../state/canvasStore'
import type { NodeType } from '../../types/designer'

interface PaletteItem {
  id: string
  type: NodeType
  name: string
  description: string
  badge?: string
  health?: string
  config: Record<string, any>
}

const DEFAULT_ITEMS: { category: string; icon: React.ReactNode; items: PaletteItem[] }[] = [
  {
    category: 'Models',
    icon: <Cpu size={15} />,
    items: [
      {
        id: 'model-claude',
        type: 'model',
        name: 'Claude 3.5 Sonnet',
        description: 'Anthropic flagship reasoning model',
        badge: 'EXECUTABLE',
        config: { model_id: 'claude-3-5-sonnet', provider: 'anthropic', temperature: 0.0, max_tokens: 4096 },
      },
      {
        id: 'model-gpt4o',
        type: 'model',
        name: 'GPT-4o',
        description: 'OpenAI multi-modal model',
        badge: 'EXECUTABLE',
        config: { model_id: 'gpt-4o', provider: 'openai', temperature: 0.0, max_tokens: 4096 },
      },
      {
        id: 'model-custom',
        type: 'model',
        name: 'Custom Provider Model',
        description: 'Provider-specific configuration',
        badge: 'CATALOG_ONLY',
        config: { model_id: 'custom-model', provider: 'custom' },
      },
    ],
  },
  {
    category: 'Prompts',
    icon: <FileText size={15} />,
    items: [
      {
        id: 'prompt-system',
        type: 'prompt',
        name: 'System Prompt',
        description: 'Persona, safety, and operational rules',
        config: { system_prompt: 'You are an intelligent assistant dedicated to helping the user.' },
      },
      {
        id: 'prompt-coder',
        type: 'prompt',
        name: 'Coding Persona',
        description: 'Specialized for writing clean, tested code',
        config: { system_prompt: 'You are an expert software engineer. Always verify with tests.' },
      },
    ],
  },
  {
    category: 'Skills (Open WebUI)',
    icon: <Sparkles size={15} />,
    items: [
      {
        id: 'skill-search',
        type: 'skill',
        name: 'Web Search',
        description: 'Search the web using search API',
        badge: 'EXECUTABLE',
        config: { skill_id: 'web-search', name: 'Web Search' },
      },
      {
        id: 'skill-artifacts',
        type: 'skill',
        name: 'Artifact Manager',
        description: 'Manage interactive artifacts',
        badge: 'EXECUTABLE',
        config: { skill_id: 'artifacts', name: 'Artifact Manager' },
      },
      {
        id: 'skill-knowledge',
        type: 'knowledge',
        name: 'Knowledge Base',
        description: 'RAG search over document corpus',
        badge: 'BLOCKED',
        config: { knowledge_id: 'kb-default', name: 'Knowledge Base' },
      },
    ],
  },
  {
    category: 'Memory',
    icon: <Brain size={15} />,
    items: [
      {
        id: 'memory-thread',
        type: 'memory',
        name: 'Thread Memory',
        description: 'Thread-scoped conversational context',
        config: { kind: 'thread' },
      },
      {
        id: 'memory-user',
        type: 'memory',
        name: 'User Memory',
        description: 'Persistent user preferences and facts',
        config: { kind: 'user' },
      },
    ],
  },
  {
    category: 'Context Policy',
    icon: <Sliders size={15} />,
    items: [
      {
        id: 'context-default',
        type: 'context',
        name: 'Default Policy',
        description: '12 turns, 32k input tokens, 15m timeout',
        config: { max_turns: 12, max_input_tokens: 32000, max_duration_seconds: 900 },
      },
      {
        id: 'context-large',
        type: 'context',
        name: 'Deep Research Policy',
        description: '24 turns, 64k input tokens',
        config: { max_turns: 24, max_input_tokens: 64000, max_duration_seconds: 1800 },
      },
    ],
  },
  {
    category: 'Tools & Connectors',
    icon: <Globe size={15} />,
    items: [
      {
        id: 'connector-cua',
        type: 'cua',
        name: 'CUA Desktop Driver',
        description: 'Full OS GUI & desktop automation',
        badge: 'EXECUTABLE',
        health: 'ONLINE',
        config: { connector_type: 'cua', health: 'ONLINE' },
      },
      {
        id: 'connector-mcp-fs',
        type: 'mcp',
        name: 'Filesystem MCP',
        description: 'Local filesystem tools via stdio MCP',
        badge: 'EXECUTABLE',
        health: 'ONLINE',
        config: { connector_type: 'mcp', mcp_id: 'filesystem', health: 'ONLINE' },
      },
      {
        id: 'tool-bash',
        type: 'tool',
        name: 'Terminal Command',
        description: 'Shell execution tool',
        badge: 'BLOCKED',
        config: { name: 'bash', mode: 'manual' },
      },
    ],
  },
]

export const ComponentLibrary: React.FC = () => {
  const [search, setSearch] = useState('')
  const addNode = useCanvasStore((s) => s.addNode)

  const onDragStart = (e: React.DragEvent, item: PaletteItem) => {
    e.dataTransfer.setData('application/reactflow-type', item.type)
    e.dataTransfer.setData('application/reactflow-label', item.name)
    e.dataTransfer.setData('application/reactflow-config', JSON.stringify(item.config))
    e.dataTransfer.effectAllowed = 'move'
  }

  const handleAddClick = (item: PaletteItem) => {
    addNode(item.type, item.name, item.config)
  }

  const filteredCategories = DEFAULT_ITEMS.map((cat) => ({
    ...cat,
    items: cat.items.filter(
      (item) =>
        item.name.toLowerCase().includes(search.toLowerCase()) ||
        item.description.toLowerCase().includes(search.toLowerCase())
    ),
  })).filter((cat) => cat.items.length > 0)

  return (
    <aside
      style={{
        width: 'var(--library-width)',
        height: 'calc(100vh - var(--header-height) - var(--modebar-height) - var(--diagnostics-height))',
        background: 'var(--panel)',
        borderRight: '1px solid var(--border)',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          padding: '12px',
          borderBottom: '1px solid var(--border)',
        }}
      >
        <div
          style={{
            position: 'relative',
            display: 'flex',
            alignItems: 'center',
          }}
        >
          <Search
            size={14}
            style={{
              position: 'absolute',
              left: 10,
              color: 'var(--muted)',
              pointerEvents: 'none',
            }}
          />
          <input
            type="text"
            placeholder="Search components..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{
              width: '100%',
              padding: '6px 10px 6px 30px',
              background: 'var(--surface)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-md)',
              color: 'var(--text)',
              fontSize: 12,
              outline: 'none',
            }}
          />
        </div>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '12px 8px' }}>
        {filteredCategories.map((cat) => (
          <div key={cat.category} style={{ marginBottom: 16 }}>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                padding: '4px 8px',
                fontSize: 11,
                fontWeight: 600,
                color: 'var(--muted)',
                textTransform: 'uppercase',
                letterSpacing: 0.5,
              }}
            >
              {cat.icon}
              <span>{cat.category}</span>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 4 }}>
              {cat.items.map((item) => (
                <div
                  key={item.id}
                  draggable
                  onDragStart={(e) => onDragStart(e, item)}
                  style={{
                    background: 'var(--surface)',
                    border: '1px solid var(--border)',
                    borderRadius: 'var(--radius-md)',
                    padding: '8px 10px',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 4,
                    cursor: 'grab',
                    transition: 'all 0.15s ease',
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--text)')}
                  onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
                >
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
                      {item.name}
                    </span>
                    <button
                      onClick={() => handleAddClick(item)}
                      title="Add to canvas"
                      style={{
                        background: 'transparent',
                        border: 'none',
                        color: 'var(--muted)',
                        padding: '2px',
                        display: 'flex',
                      }}
                      onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--text)')}
                      onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--muted)')}
                    >
                      <Plus size={14} />
                    </button>
                  </div>

                  <div style={{ fontSize: 11, color: 'var(--muted)', lineHeight: 1.3 }}>
                    {item.description}
                  </div>

                  {item.badge && (
                    <div style={{ display: 'flex', gap: 6, marginTop: 2 }}>
                      <span
                        style={{
                          fontSize: 9,
                          fontWeight: 700,
                          padding: '1px 5px',
                          borderRadius: 'var(--radius-sm)',
                          background:
                            item.badge === 'EXECUTABLE'
                              ? 'rgba(74, 222, 128, 0.1)'
                              : item.badge === 'BLOCKED'
                              ? 'rgba(248, 113, 113, 0.1)'
                              : 'rgba(161, 161, 170, 0.1)',
                          color:
                            item.badge === 'EXECUTABLE'
                              ? 'var(--green)'
                              : item.badge === 'BLOCKED'
                              ? 'var(--red)'
                              : 'var(--muted)',
                          border: `1px solid ${
                            item.badge === 'EXECUTABLE'
                              ? 'var(--green)'
                              : item.badge === 'BLOCKED'
                              ? 'var(--red)'
                              : 'var(--border)'
                          }`,
                        }}
                      >
                        {item.badge}
                      </span>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </aside>
  )
}

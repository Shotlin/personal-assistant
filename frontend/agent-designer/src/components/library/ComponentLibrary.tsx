import React, { useEffect, useMemo, useState } from 'react'
import {
  Brain,
  Cpu,
  Database,
  FileText,
  Globe,
  Monitor,
  Plus,
  Search,
  Sliders,
  Sparkles,
} from 'lucide-react'
import { useCanvasStore } from '../../state/canvasStore'
import { api } from '../../api/client'
import type { CatalogEntry, NodeType } from '../../types/designer'

interface PaletteItem {
  id: string
  type: NodeType
  name: string
  description: string
  badge: 'CONNECTED' | 'EXECUTABLE' | 'AVAILABLE' | 'CATALOG_ONLY' | 'BLOCKED' | 'OFFLINE'
  health: string
  config: Record<string, any>
}

interface CategoryDef {
  kind: string
  category: string
  icon: React.ReactNode
  /** Map a catalog entry + connected-flag to a canvas node. */
  toType: (entry: CatalogEntry) => NodeType
  toConfig: (entry: CatalogEntry) => Record<string, any>
}

const CATEGORIES: CategoryDef[] = [
  {
    kind: 'model',
    category: 'Models',
    icon: <Cpu size={15} />,
    toType: () => 'model',
    toConfig: (e) => ({
      provider: e.provenance?.provider || 'operator-env',
      model_id: e.provenance?.model_id || e.id,
      credential_ref: e.provenance?.credential_ref || 'operator-env',
    }),
  },
  {
    kind: 'prompt',
    category: 'Prompts (Open WebUI)',
    icon: <FileText size={15} />,
    toType: () => 'prompt',
    toConfig: (e) => ({ resource_ref: e.id, source: e.source }),
  },
  {
    kind: 'skill',
    category: 'Skills',
    icon: <Sparkles size={15} />,
    toType: () => 'skill',
    toConfig: (e) => ({
      source: e.source,
      id: e.id,
      name: e.name,
      revision_or_hash: e.provenance?.content_hash || '',
    }),
  },
  {
    kind: 'memory',
    category: 'Memory',
    icon: <Brain size={15} />,
    toType: () => 'memory',
    toConfig: (e) => ({ kind: e.provenance?.kind || 'user' }),
  },
  {
    kind: 'context',
    category: 'Context Policy',
    icon: <Sliders size={15} />,
    toType: () => 'context',
    toConfig: (e) => ({ ...(e.provenance?.policy || {}) }),
  },
  {
    kind: 'knowledge',
    category: 'Knowledge',
    icon: <Database size={15} />,
    toType: () => 'knowledge',
    toConfig: (e) => ({ knowledge_id: e.id }),
  },
  {
    kind: 'cua',
    category: 'Computer Use',
    icon: <Monitor size={15} />,
    toType: () => 'cua',
    toConfig: (e) => ({
      connector_id: e.provenance?.connector_id || 'cua-local',
      host_profile: 'bounded',
    }),
  },
  {
    kind: 'mcp',
    category: 'MCP Servers',
    icon: <Globe size={15} />,
    toType: () => 'mcp',
    toConfig: (e) => ({
      connector_id: e.provenance?.connector_id || e.id,
      selected_tool_ids: [],
    }),
  },
  {
    kind: 'tool',
    category: 'Tools',
    icon: <Globe size={15} />,
    toType: () => 'tool',
    toConfig: (e) => ({ name: e.name }),
  },
]

const BADGE_STYLES: Record<PaletteItem['badge'], { bg: string; fg: string; bd: string }> = {
  CONNECTED: { bg: 'rgba(96, 165, 250, 0.12)', fg: 'var(--blue)', bd: 'var(--blue)' },
  EXECUTABLE: { bg: 'rgba(74, 222, 128, 0.1)', fg: 'var(--green)', bd: 'var(--green)' },
  AVAILABLE: { bg: 'rgba(161, 161, 170, 0.1)', fg: 'var(--muted)', bd: 'var(--border)' },
  CATALOG_ONLY: { bg: 'rgba(251, 191, 36, 0.1)', fg: 'var(--amber)', bd: 'var(--amber)' },
  BLOCKED: { bg: 'rgba(248, 113, 113, 0.1)', fg: 'var(--red)', bd: 'var(--red)' },
  OFFLINE: { bg: 'rgba(161, 161, 170, 0.1)', fg: 'var(--muted)', bd: 'var(--border)' },
}

function classify(
  entry: CatalogEntry,
  connected: boolean
): PaletteItem['badge'] {
  if (connected) return 'CONNECTED'
  if (entry.health_status === 'OFFLINE') return 'OFFLINE'
  if (entry.capability_status === 'BLOCKED') return 'BLOCKED'
  if (entry.capability_status === 'CATALOG_ONLY') return 'CATALOG_ONLY'
  if (entry.capability_status === 'UNSUPPORTED') return 'BLOCKED'
  return 'AVAILABLE'
}

export const ComponentLibrary: React.FC = () => {
  const [search, setSearch] = useState('')
  const [catalog, setCatalog] = useState<Record<string, CatalogEntry[]>>({})
  const [failed, setFailed] = useState<Record<string, boolean>>({})
  const addNode = useCanvasStore((s) => s.addNode)
  const nodes = useCanvasStore((s) => s.nodes)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      const pairs: Array<[string, CatalogEntry[]]> = await Promise.all(
        CATEGORIES.map(async (cat) => {
          try {
            const res = await api.getCatalog(cat.kind)
            return [cat.kind, res.entries || []] as [string, CatalogEntry[]]
          } catch {
            // Honest degradation: an unavailable source shows as an empty
            // group with a note -- never fabricated demo items.
            return [cat.kind, [] as CatalogEntry[]]
          }
        })
      )
      if (cancelled) return
      const map: Record<string, CatalogEntry[]> = {}
      const fails: Record<string, boolean> = {}
      for (const [kind, entries] of pairs) {
        map[kind] = entries
        fails[kind] = entries.length === 0
      }
      setCatalog(map)
      setFailed(fails)
    }
    load()
    return () => {
      cancelled = true
    }
  }, [])

  // Resource ids actually attached to the CURRENT agent's graph.
  const connectedIds = useMemo(() => {
    const ids = new Set<string>()
    for (const node of nodes) {
      const cfg = (node.data as any)?.config || {}
      if (cfg.id) ids.add(String(cfg.id))
      if (cfg.connector_id) ids.add(String(cfg.connector_id))
      if (cfg.knowledge_id) ids.add(String(cfg.knowledge_id))
      if (cfg.kind) ids.add(`memory-${cfg.kind}`)
    }
    return ids
  }, [nodes])

  const isConnected = (cat: CategoryDef, entry: CatalogEntry): boolean => {
    if (cat.kind === 'model')
      return connectedIds.has(`operator-model:${entry.provenance?.provider}:${entry.provenance?.model_id}`)
        || [...connectedIds].some((id) => id === entry.provenance?.model_id)
    if (cat.kind === 'skill') return connectedIds.has(entry.id)
    if (cat.kind === 'prompt') return connectedIds.has(entry.id)
    if (cat.kind === 'memory') return connectedIds.has(`memory-${entry.provenance?.kind}`)
    if (cat.kind === 'context') return connectedIds.size >= 0 && nodes.some((n) => n.type === 'context')
    if (cat.kind === 'cua') return connectedIds.has(entry.provenance?.connector_id || 'cua-local')
    if (cat.kind === 'mcp') return connectedIds.has(entry.provenance?.connector_id || entry.id)
    return false
  }

  const onDragStart = (e: React.DragEvent, item: PaletteItem) => {
    e.dataTransfer.setData('application/reactflow-type', item.type)
    e.dataTransfer.setData('application/reactflow-label', item.name)
    e.dataTransfer.setData('application/reactflow-config', JSON.stringify(item.config))
    e.dataTransfer.effectAllowed = 'move'
  }

  const handleAddClick = (item: PaletteItem) => {
    addNode(item.type, item.name, item.config)
  }

  const filteredCategories = CATEGORIES.map((cat) => ({
    ...cat,
    items: (catalog[cat.kind] || [])
      .map((entry) => ({
        entry,
        item: {
          id: entry.id,
          type: cat.toType(entry),
          name: entry.name,
          description: entry.description || '',
          badge: classify(entry, isConnected(cat, entry)),
          health: entry.health_status,
          config: cat.toConfig(entry),
        } as PaletteItem,
      }))
      .filter(
        ({ item }) =>
          item.name.toLowerCase().includes(search.toLowerCase()) ||
          item.description.toLowerCase().includes(search.toLowerCase())
      ),
  })).filter((cat) => cat.items.length > 0 || !failed[cat.kind])

  return (
    <aside
      style={{
        width: 'var(--library-width)',
        height:
          'calc(100vh - var(--header-height) - var(--modebar-height) - var(--diagnostics-height))',
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
              {cat.items.length === 0 && (
                <div
                  style={{
                    fontSize: 11,
                    color: 'var(--muted)',
                    padding: '6px 8px',
                    fontStyle: 'italic',
                  }}
                >
                  No {cat.category.toLowerCase()} discovered (source unavailable
                  or none configured).
                </div>
              )}
              {cat.items.map(({ item }) => {
                const badgeStyle = BADGE_STYLES[item.badge]
                return (
                  <div
                    key={item.id}
                    draggable
                    onDragStart={(e) => onDragStart(e, item)}
                    style={{
                      background: 'var(--surface)',
                      border: `1px solid ${
                        item.badge === 'CONNECTED' ? 'var(--blue)' : 'var(--border)'
                      }`,
                      borderRadius: 'var(--radius-md)',
                      padding: '8px 10px',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: 4,
                      cursor: 'grab',
                      transition: 'all 0.15s ease',
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--text)')}
                    onMouseLeave={(e) =>
                      (e.currentTarget.style.borderColor =
                        item.badge === 'CONNECTED' ? 'var(--blue)' : 'var(--border)')
                    }
                  >
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                      }}
                    >
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

                    <div style={{ display: 'flex', gap: 6, marginTop: 2 }}>
                      <span
                        style={{
                          fontSize: 9,
                          fontWeight: 700,
                          padding: '1px 5px',
                          borderRadius: 'var(--radius-sm)',
                          background: badgeStyle.bg,
                          color: badgeStyle.fg,
                          border: `1px solid ${badgeStyle.bd}`,
                        }}
                      >
                        {item.badge === 'CONNECTED' ? 'CONNECTED TO AGENT' : item.badge}
                      </span>
                      <span
                        style={{
                          fontSize: 9,
                          fontWeight: 600,
                          padding: '1px 5px',
                          borderRadius: 'var(--radius-sm)',
                          background: 'rgba(161, 161, 170, 0.06)',
                          color: 'var(--muted)',
                          border: '1px solid var(--border)',
                        }}
                      >
                        {item.health}
                      </span>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        ))}
      </div>
    </aside>
  )
}

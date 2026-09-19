import { create } from 'zustand'
import {
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
} from '@xyflow/react'
import type {
  AgentDetail,
  GraphDocument,
  GraphEdge,
  GraphNode,
  NodeType,
  ValidationReport,
} from '../types/designer'

const MAX_HISTORY = 50

interface CanvasHistoryState {
  nodes: Node[]
  edges: Edge[]
}

export interface CanvasState {
  // Agent & Draft metadata
  agent: AgentDetail | null
  activeRevisionId: string | null
  draftRevisionId: string | null
  isDirty: boolean
  isSaving: boolean
  isValidating: boolean
  mode: 'design' | 'live' | 'history'
  theme: 'dark' | 'light' | 'system'

  // React Flow state
  nodes: Node[]
  edges: Edge[]
  selectedNodeId: string | null
  validationReport: ValidationReport | null

  // History stack
  undoStack: CanvasHistoryState[]
  redoStack: CanvasHistoryState[]

  // Actions
  setAgent: (agent: AgentDetail) => void
  setMode: (mode: 'design' | 'live' | 'history') => void
  setTheme: (theme: 'dark' | 'light' | 'system') => void
  setSelectedNodeId: (id: string | null) => void
  setValidationReport: (report: ValidationReport | null) => void
  markClean: () => void
  setSaving: (saving: boolean) => void
  setValidating: (validating: boolean) => void

  // Graph mutations
  onNodesChange: (changes: NodeChange[]) => void
  onEdgesChange: (changes: EdgeChange[]) => void
  onConnect: (connection: Connection) => void
  addNode: (type: NodeType, label?: string, config?: Record<string, any>, position?: { x: number; y: number }) => void
  removeNode: (id: string) => void
  updateNodeConfig: (id: string, patch: Record<string, any>) => void
  loadGraph: (graph: GraphDocument) => void
  exportGraph: () => GraphDocument

  // Undo / Redo
  undo: () => void
  redo: () => void
}

function pushHistory(state: CanvasState): Partial<CanvasState> {
  const current: CanvasHistoryState = {
    nodes: JSON.parse(JSON.stringify(state.nodes)),
    edges: JSON.parse(JSON.stringify(state.edges)),
  }
  const undoStack = [...state.undoStack, current].slice(-MAX_HISTORY)
  return {
    undoStack,
    redoStack: [],
    isDirty: true,
  }
}

export const useCanvasStore = create<CanvasState>((set, get) => ({
  agent: null,
  activeRevisionId: null,
  draftRevisionId: null,
  isDirty: false,
  isSaving: false,
  isValidating: false,
  mode: 'design',
  theme: (localStorage.getItem('agent_designer_theme') as any) || 'dark',

  nodes: [],
  edges: [],
  selectedNodeId: null,
  validationReport: null,

  undoStack: [],
  redoStack: [],

  setAgent: (agent) => {
    set({
      agent,
      activeRevisionId: agent.active_revision_id || null,
      draftRevisionId: agent.draft?.revision_id || null,
      isDirty: false,
      undoStack: [],
      redoStack: [],
    })
    if (agent.draft?.graph) {
      get().loadGraph(agent.draft.graph)
    } else {
      // Default initial minimal graph with 1 agent root
      const initialRoot: Node = {
        id: 'agent_root',
        type: 'agent',
        position: { x: 350, y: 200 },
        data: {
          label: agent.name || 'Agent',
          agent_id: agent.agent_id,
          config: {},
        },
      }
      set({ nodes: [initialRoot], edges: [] })
    }
  },

  setMode: (mode) => set({ mode }),

  setTheme: (theme) => {
    localStorage.setItem('agent_designer_theme', theme)
    document.documentElement.setAttribute('data-theme', theme)
    set({ theme })
  },

  setSelectedNodeId: (selectedNodeId) => set({ selectedNodeId }),

  setValidationReport: (validationReport) => set({ validationReport }),

  markClean: () => set({ isDirty: false }),

  setSaving: (isSaving) => set({ isSaving }),

  setValidating: (isValidating) => set({ isValidating }),

  onNodesChange: (changes) => {
    const isMeaningful = changes.some(
      (c) => c.type === 'position' && c.dragging === false || c.type === 'remove'
    )
    const historyUpdate = isMeaningful ? pushHistory(get()) : {}

    set({
      ...historyUpdate,
      nodes: applyNodeChanges(changes, get().nodes),
    })
  },

  onEdgesChange: (changes) => {
    const isMeaningful = changes.some((c) => c.type === 'remove')
    const historyUpdate = isMeaningful ? pushHistory(get()) : {}

    set({
      ...historyUpdate,
      edges: applyEdgeChanges(changes, get().edges),
    })
  },

  onConnect: (connection) => {
    const historyUpdate = pushHistory(get())
    const edge: Edge = {
      ...connection,
      id: `e-${connection.source}-${connection.target}-${Date.now()}`,
      animated: true,
      style: { stroke: 'var(--edge-stroke)', strokeWidth: 2 },
    }
    set({
      ...historyUpdate,
      edges: addEdge(edge, get().edges),
    })
  },

  addNode: (type, label, config = {}, position) => {
    const state = get()
    const historyUpdate = pushHistory(state)
    const id = `${type}_${Date.now()}`
    const nodePosition = position || {
      x: 100 + (state.nodes.length % 5) * 60,
      y: 100 + (state.nodes.length % 5) * 60,
    }

    const newNode: Node = {
      id,
      type,
      position: nodePosition,
      data: {
        label: label || type.toUpperCase(),
        type,
        config,
      },
    }

    set({
      ...historyUpdate,
      nodes: [...state.nodes, newNode],
      selectedNodeId: id,
    })
  },

  removeNode: (id) => {
    const state = get()
    if (id === 'agent_root') return // Root cannot be deleted
    const historyUpdate = pushHistory(state)
    set({
      ...historyUpdate,
      nodes: state.nodes.filter((n) => n.id !== id),
      edges: state.edges.filter((e) => e.source !== id && e.target !== id),
      selectedNodeId: state.selectedNodeId === id ? null : state.selectedNodeId,
    })
  },

  updateNodeConfig: (id, patch) => {
    const state = get()
    const historyUpdate = pushHistory(state)
    set({
      ...historyUpdate,
      nodes: state.nodes.map((node) => {
        if (node.id === id) {
          return {
            ...node,
            data: {
              ...node.data,
              config: {
                ...(node.data.config as Record<string, any>),
                ...patch,
              },
            },
          }
        }
        return node
      }),
    })
  },

  loadGraph: (graph) => {
    const nodes: Node[] = (graph.nodes || []).map((gn: GraphNode) => ({
      id: gn.id,
      type: gn.type,
      position: gn.position || { x: 200, y: 200 },
      data: {
        label: (gn.data?.label as string) || gn.id,
        type: gn.type,
        enabled: gn.data?.enabled ?? true,
        config: gn.data?.config || {},
      },
    }))

    const edges: Edge[] = (graph.edges || []).map((ge) => ({
      id: ge.id || `e-${ge.source}-${ge.target}`,
      source: ge.source,
      target: ge.target,
      sourceHandle: ge.sourceHandle || 'root',
      targetHandle: ge.targetHandle,
      animated: true,
      style: { stroke: 'var(--edge-stroke)', strokeWidth: 2 },
    }))

    set({
      nodes,
      edges,
      isDirty: false,
      undoStack: [],
      redoStack: [],
      selectedNodeId: null,
    })
  },

  exportGraph: () => {
    const state = get()
    const graphNodes: GraphNode[] = state.nodes.map((n) => ({
      id: n.id,
      type: (n.type as NodeType) || 'tool',
      position: { x: Math.round(n.position.x), y: Math.round(n.position.y) },
      data: {
        enabled: (n.data?.enabled as boolean) ?? true,
        label: (n.data?.label as string) || n.id,
        config: (n.data?.config as Record<string, any>) || {},
      },
    }))

    const graphEdges: GraphEdge[] = state.edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle: e.sourceHandle || 'root',
      targetHandle: e.targetHandle || 'model',
    }))

    return {
      schema_version: 1,
      nodes: graphNodes,
      edges: graphEdges,
      viewport: { x: 0, y: 0, zoom: 1 },
    }
  },

  undo: () => {
    const { undoStack, redoStack, nodes, edges } = get()
    if (undoStack.length === 0) return

    const previous = undoStack[undoStack.length - 1]
    const current: CanvasHistoryState = {
      nodes: JSON.parse(JSON.stringify(nodes)),
      edges: JSON.parse(JSON.stringify(edges)),
    }

    set({
      nodes: previous.nodes,
      edges: previous.edges,
      undoStack: undoStack.slice(0, -1),
      redoStack: [...redoStack, current],
      isDirty: true,
    })
  },

  redo: () => {
    const { undoStack, redoStack, nodes, edges } = get()
    if (redoStack.length === 0) return

    const next = redoStack[redoStack.length - 1]
    const current: CanvasHistoryState = {
      nodes: JSON.parse(JSON.stringify(nodes)),
      edges: JSON.parse(JSON.stringify(edges)),
    }

    set({
      nodes: next.nodes,
      edges: next.edges,
      undoStack: [...undoStack, current],
      redoStack: redoStack.slice(0, -1),
      isDirty: true,
    })
  },
}))

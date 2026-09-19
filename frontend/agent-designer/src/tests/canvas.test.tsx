import { describe, it, expect, beforeEach } from 'vitest'
import { useCanvasStore } from '../state/canvasStore'
import type { AgentDetail } from '../types/designer'

const mockAgent: AgentDetail = {
  agent_id: 'agent-test-1',
  slug: 'test-agent',
  name: 'Test Agent',
  description: 'Test Description',
  active_revision_id: 'rev-1',
  active_revision_number: 1,
  row_version: 1,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  revisions_count: 1,
  draft: null,
}

describe('Canvas Store State Management', () => {
  beforeEach(() => {
    useCanvasStore.setState({
      agent: null,
      nodes: [],
      edges: [],
      selectedNodeId: null,
      isDirty: false,
      undoStack: [],
      redoStack: [],
      theme: 'dark',
    })
  })

  it('initializes agent with default root node when no draft exists', () => {
    useCanvasStore.getState().setAgent(mockAgent)

    const state = useCanvasStore.getState()
    expect(state.agent?.agent_id).toBe('agent-test-1')
    expect(state.nodes.length).toBe(1)
    expect(state.nodes[0].id).toBe('agent_root')
    expect(state.isDirty).toBe(false)
  })

  it('adding a node marks draft dirty and records undo state', () => {
    useCanvasStore.getState().setAgent(mockAgent)
    expect(useCanvasStore.getState().isDirty).toBe(false)

    useCanvasStore.getState().addNode('model', 'Claude 3.5 Sonnet', { model_id: 'claude-3-5-sonnet' })

    const state = useCanvasStore.getState()
    expect(state.nodes.length).toBe(2)
    expect(state.isDirty).toBe(true)
    expect(state.undoStack.length).toBe(1)
  })

  it('undo and redo restore and reapply graph modifications', () => {
    useCanvasStore.getState().setAgent(mockAgent)
    useCanvasStore.getState().addNode('skill', 'Web Search', { skill_id: 'web-search' })

    expect(useCanvasStore.getState().nodes.length).toBe(2)

    // Undo
    useCanvasStore.getState().undo()
    expect(useCanvasStore.getState().nodes.length).toBe(1)
    expect(useCanvasStore.getState().redoStack.length).toBe(1)

    // Redo
    useCanvasStore.getState().redo()
    expect(useCanvasStore.getState().nodes.length).toBe(2)
    expect(useCanvasStore.getState().redoStack.length).toBe(0)
  })

  it('removing a non-root node clears node from canvas', () => {
    useCanvasStore.getState().setAgent(mockAgent)
    useCanvasStore.getState().addNode('tool', 'Custom Tool')

    const addedId = useCanvasStore.getState().nodes[1].id
    expect(useCanvasStore.getState().nodes.length).toBe(2)

    useCanvasStore.getState().removeNode(addedId)
    expect(useCanvasStore.getState().nodes.length).toBe(1)
    expect(useCanvasStore.getState().nodes[0].id).toBe('agent_root')
  })

  it('root node cannot be deleted', () => {
    useCanvasStore.getState().setAgent(mockAgent)
    expect(useCanvasStore.getState().nodes.length).toBe(1)

    useCanvasStore.getState().removeNode('agent_root')
    expect(useCanvasStore.getState().nodes.length).toBe(1)
  })

  it('theme switching updates data-theme attribute', () => {
    useCanvasStore.getState().setTheme('light')
    expect(useCanvasStore.getState().theme).toBe('light')
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')

    useCanvasStore.getState().setTheme('dark')
    expect(useCanvasStore.getState().theme).toBe('dark')
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
  })

  it('exportGraph returns a compliant GraphDocument with schema_version 1', () => {
    useCanvasStore.getState().setAgent(mockAgent)
    useCanvasStore.getState().addNode('model', 'Claude 3.5 Sonnet', { model_id: 'claude-3-5-sonnet' })

    const doc = useCanvasStore.getState().exportGraph()
    expect(doc.schema_version).toBe(1)
    expect(doc.nodes.length).toBe(2)
    expect(doc.nodes.some((n) => n.type === 'agent')).toBe(true)
    expect(doc.nodes.some((n) => n.type === 'model')).toBe(true)
    expect(doc.nodes[0].data).toBeDefined()
  })
})

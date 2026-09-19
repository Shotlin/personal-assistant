import { describe, it, expect, beforeEach } from 'vitest'
import { useLiveStore } from '../state/liveStore'
import { useCanvasStore } from '../state/canvasStore'
import type { RunEvent, RunSnapshot } from '../types/designer'

describe('LiveStore State Management', () => {
  beforeEach(() => {
    useLiveStore.getState().reset()
  })

  it('initializes with default empty state', () => {
    const state = useLiveStore.getState()
    expect(state.runId).toBeNull()
    expect(state.status).toBe('unknown')
    expect(state.activeNodes.size).toBe(0)
    expect(state.events.length).toBe(0)
    expect(state.executedTools.length).toBe(0)
  })

  it('setRunId clears prior run state and sets new runId', () => {
    const store = useLiveStore.getState()
    store.processEvent({
      event_id: 1,
      run_id: 'run-1',
      agent_id: 'agent-1',
      sequence_number: 1,
      event_type: 'node.started',
      payload: { node_id: 'node-a' },
      at: new Date().toISOString(),
    })
    expect(useLiveStore.getState().activeNodes.has('node-a')).toBe(true)

    // Switch run
    useLiveStore.getState().setRunId('run-2')
    const nextState = useLiveStore.getState()
    expect(nextState.runId).toBe('run-2')
    expect(nextState.activeNodes.size).toBe(0)
    expect(nextState.events.length).toBe(0)
    expect(nextState.status).toBe('unknown')
  })

  it('processes run.started and transitions status to running', () => {
    const at = '2026-09-19T14:00:00.000Z'
    useLiveStore.getState().processEvent({
      event_id: 10,
      run_id: 'run-1',
      agent_id: 'agent-1',
      sequence_number: 1,
      event_type: 'run.started',
      payload: { started_at: at },
      at,
    })

    const state = useLiveStore.getState()
    expect(state.status).toBe('running')
    expect(state.startedAt).toBe(at)
    expect(state.events.length).toBe(1)
  })

  it('processes node lifecycle (started -> completed)', () => {
    const store = useLiveStore.getState()

    store.processEvent({
      event_id: 1,
      run_id: 'run-1',
      agent_id: 'agent-1',
      sequence_number: 1,
      event_type: 'node.started',
      payload: { node_id: 'model-1' },
      at: '2026-09-19T14:00:01.000Z',
    })
    expect(useLiveStore.getState().activeNodes.has('model-1')).toBe(true)
    expect(useLiveStore.getState().completedNodes.has('model-1')).toBe(false)

    store.processEvent({
      event_id: 2,
      run_id: 'run-1',
      agent_id: 'agent-1',
      sequence_number: 2,
      event_type: 'node.completed',
      payload: { node_id: 'model-1' },
      at: '2026-09-19T14:00:02.000Z',
    })
    expect(useLiveStore.getState().activeNodes.has('model-1')).toBe(false)
    expect(useLiveStore.getState().completedNodes.has('model-1')).toBe(true)
  })

  it('processes tool execution and calculates duration_ms', () => {
    const store = useLiveStore.getState()

    store.processEvent({
      event_id: 1,
      run_id: 'run-1',
      agent_id: 'agent-1',
      sequence_number: 1,
      event_type: 'tool.invoked',
      payload: { tool_name: 'search_web' },
      at: '2026-09-19T14:00:00.000Z',
    })

    let tools = useLiveStore.getState().executedTools
    expect(tools.length).toBe(1)
    expect(tools[0].tool_name).toBe('search_web')
    expect(tools[0].status).toBe('running')

    store.processEvent({
      event_id: 2,
      run_id: 'run-1',
      agent_id: 'agent-1',
      sequence_number: 2,
      event_type: 'tool.completed',
      payload: { tool_name: 'search_web' },
      at: '2026-09-19T14:00:01.500Z',
    })

    tools = useLiveStore.getState().executedTools
    expect(tools[0].status).toBe('completed')
    expect(tools[0].duration_ms).toBe(1500)
  })

  it('processes budget updates from context.budget_update', () => {
    useLiveStore.getState().processEvent({
      event_id: 5,
      run_id: 'run-1',
      agent_id: 'agent-1',
      sequence_number: 5,
      event_type: 'context.budget_update',
      payload: {
        estimated_context_tokens: 4200,
        provider_reported_input_tokens: 3800,
        cumulative_cost_cents: 12.5,
      },
      at: new Date().toISOString(),
    })

    const budget = useLiveStore.getState().budget
    expect(budget.estimated_context_tokens).toBe(4200)
    expect(budget.provider_reported_input_tokens).toBe(3800)
    expect(budget.cumulative_cost_cents).toBe(12.5)
  })

  it('clears active nodes on run.completed or run.failed', () => {
    const store = useLiveStore.getState()

    store.processEvent({
      event_id: 1,
      run_id: 'run-1',
      agent_id: 'agent-1',
      sequence_number: 1,
      event_type: 'node.started',
      payload: { node_id: 'node-x' },
      at: new Date().toISOString(),
    })
    expect(useLiveStore.getState().activeNodes.size).toBe(1)

    store.processEvent({
      event_id: 2,
      run_id: 'run-1',
      agent_id: 'agent-1',
      sequence_number: 2,
      event_type: 'run.failed',
      payload: { error: 'LLM rate limit reached' },
      at: new Date().toISOString(),
    })

    const state = useLiveStore.getState()
    expect(state.status).toBe('failed')
    expect(state.error).toBe('LLM rate limit reached')
    expect(state.activeNodes.size).toBe(0)
  })

  it('deduplicates identical event IDs', () => {
    const store = useLiveStore.getState()
    const event: RunEvent = {
      event_id: 99,
      run_id: 'run-1',
      agent_id: 'agent-1',
      sequence_number: 1,
      event_type: 'node.started',
      payload: { node_id: 'node-y' },
      at: new Date().toISOString(),
    }

    store.processEvent(event)
    store.processEvent(event)

    expect(useLiveStore.getState().events.length).toBe(1)
  })

  it('loads server snapshot correctly', () => {
    const snapshot: RunSnapshot = {
      run_id: 'run-snap-1',
      agent_id: 'agent-1',
      revision_id: 'rev-2',
      status: 'running',
      active_nodes: ['node-1', 'node-2'],
      executed_tools: [
        { tool_name: 'calc', status: 'completed', sequence_number: 1 },
      ],
      budget: { estimated_context_tokens: 1500 },
      error: null,
      event_count: 5,
    }

    useLiveStore.getState().loadSnapshot(snapshot)
    const state = useLiveStore.getState()
    expect(state.status).toBe('running')
    expect(state.activeNodes.has('node-1')).toBe(true)
    expect(state.activeNodes.has('node-2')).toBe(true)
    expect(state.executedTools.length).toBe(1)
    expect(state.pinnedRevisionId).toBe('rev-2')
  })
})

describe('Canvas Mode Switching', () => {
  it('switches between design, live, and history modes', () => {
    expect(useCanvasStore.getState().mode).toBe('design')

    useCanvasStore.getState().setMode('live')
    expect(useCanvasStore.getState().mode).toBe('live')

    useCanvasStore.getState().setMode('history')
    expect(useCanvasStore.getState().mode).toBe('history')

    useCanvasStore.getState().setMode('design')
    expect(useCanvasStore.getState().mode).toBe('design')
  })
})

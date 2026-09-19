import { create } from 'zustand'
import type {
  BudgetSnapshot,
  GraphDocument,
  RunEvent,
  RunSnapshot,
  RunSummary,
  ToolEntry,
} from '../types/designer'
import type { SSEConnectionState } from '../hooks/useEventStream'

const MAX_EVENTS = 500

export interface LiveState {
  // Current run selection and available runs
  runId: string | null
  runList: RunSummary[]
  isLoadingRuns: boolean

  // Execution state
  status: 'unknown' | 'running' | 'completed' | 'failed'
  activeNodes: Set<string>
  completedNodes: Set<string>
  failedNodes: Set<string>
  events: RunEvent[]
  executedTools: ToolEntry[]
  budget: BudgetSnapshot
  error: string | null
  startedAt: string | null

  // Pinned revision graph for the selected run
  pinnedRevisionId: string | null
  pinnedRevisionGraph: GraphDocument | null
  isLoadingRevision: boolean

  // SSE connection state
  sseState: SSEConnectionState
  lastEventAt: number | null

  // Actions
  setRunId: (runId: string | null) => void
  setRunList: (runs: RunSummary[]) => void
  setLoadingRuns: (loading: boolean) => void
  setPinnedRevision: (revisionId: string | null, graph: GraphDocument | null) => void
  setLoadingRevision: (loading: boolean) => void
  setSseState: (sseState: SSEConnectionState, lastEventAt?: number | null) => void
  processEvent: (event: RunEvent) => void
  loadSnapshot: (snapshot: RunSnapshot) => void
  reset: () => void
}

export const useLiveStore = create<LiveState>((set, get) => ({
  runId: null,
  runList: [],
  isLoadingRuns: false,

  status: 'unknown',
  activeNodes: new Set<string>(),
  completedNodes: new Set<string>(),
  failedNodes: new Set<string>(),
  events: [],
  executedTools: [],
  budget: {},
  error: null,
  startedAt: null,

  pinnedRevisionId: null,
  pinnedRevisionGraph: null,
  isLoadingRevision: false,

  sseState: 'closed',
  lastEventAt: null,

  setRunId: (runId) => {
    if (get().runId === runId) return
    set({
      runId,
      status: 'unknown',
      activeNodes: new Set<string>(),
      completedNodes: new Set<string>(),
      failedNodes: new Set<string>(),
      events: [],
      executedTools: [],
      budget: {},
      error: null,
      startedAt: null,
      pinnedRevisionId: null,
      pinnedRevisionGraph: null,
      sseState: 'closed',
      lastEventAt: null,
    })
  },

  setRunList: (runList) => set({ runList }),
  setLoadingRuns: (isLoadingRuns) => set({ isLoadingRuns }),

  setPinnedRevision: (pinnedRevisionId, pinnedRevisionGraph) =>
    set({ pinnedRevisionId, pinnedRevisionGraph, isLoadingRevision: false }),

  setLoadingRevision: (isLoadingRevision) => set({ isLoadingRevision }),

  setSseState: (sseState, lastEventAt) => {
    set((state) => ({
      sseState,
      lastEventAt: lastEventAt !== undefined ? lastEventAt : state.lastEventAt,
    }))
  },

  processEvent: (event: RunEvent) => {
    set((state) => {
      // Avoid duplicate processing if identical event_id already present
      if (state.events.some((e) => e.event_id === event.event_id)) {
        return state
      }

      const newEvents = [...state.events, event]
      if (newEvents.length > MAX_EVENTS) {
        newEvents.splice(0, newEvents.length - MAX_EVENTS)
      }

      let status = state.status
      const activeNodes = new Set(state.activeNodes)
      const completedNodes = new Set(state.completedNodes)
      const failedNodes = new Set(state.failedNodes)
      let executedTools = [...state.executedTools]
      let budget = { ...state.budget }
      let error = state.error
      let startedAt = state.startedAt

      const payload = event.payload || {}

      switch (event.event_type) {
        case 'run.started': {
          status = 'running'
          startedAt = event.at || payload.started_at || new Date().toISOString()
          break
        }

        case 'node.started': {
          const nodeId = String(payload.node_id || '')
          if (nodeId) {
            activeNodes.add(nodeId)
            completedNodes.delete(nodeId)
            failedNodes.delete(nodeId)
          }
          break
        }

        case 'node.completed': {
          const nodeId = String(payload.node_id || '')
          if (nodeId) {
            activeNodes.delete(nodeId)
            completedNodes.add(nodeId)
          }
          break
        }

        case 'node.failed': {
          const nodeId = String(payload.node_id || '')
          if (nodeId) {
            activeNodes.delete(nodeId)
            failedNodes.add(nodeId)
          }
          break
        }

        case 'tool.invoked': {
          executedTools.push({
            tool_name: String(payload.tool_name || 'unknown'),
            status: 'running',
            sequence_number: event.sequence_number,
            started_at: event.at,
          })
          break
        }

        case 'tool.completed': {
          if (executedTools.length > 0) {
            const last = { ...executedTools[executedTools.length - 1] }
            last.status = 'completed'
            last.completed_at = event.at
            if (last.started_at && event.at) {
              const diff = new Date(event.at).getTime() - new Date(last.started_at).getTime()
              if (!isNaN(diff) && diff >= 0) {
                last.duration_ms = diff
              }
            }
            executedTools = [...executedTools.slice(0, -1), last]
          }
          break
        }

        case 'tool.failed': {
          if (executedTools.length > 0) {
            const last = { ...executedTools[executedTools.length - 1] }
            last.status = 'failed'
            last.error = String(payload.error || 'Execution failed')
            last.completed_at = event.at
            if (last.started_at && event.at) {
              const diff = new Date(event.at).getTime() - new Date(last.started_at).getTime()
              if (!isNaN(diff) && diff >= 0) {
                last.duration_ms = diff
              }
            }
            executedTools = [...executedTools.slice(0, -1), last]
          }
          break
        }

        case 'context.budget_update': {
          budget = {
            ...budget,
            ...payload,
          }
          break
        }

        case 'run.completed': {
          status = 'completed'
          activeNodes.clear()
          break
        }

        case 'run.failed': {
          status = 'failed'
          error = String(payload.error || 'Run failed')
          activeNodes.clear()
          break
        }
      }

      return {
        events: newEvents,
        status,
        activeNodes,
        completedNodes,
        failedNodes,
        executedTools,
        budget,
        error,
        startedAt,
        lastEventAt: Date.now(),
      }
    })
  },

  loadSnapshot: (snapshot: RunSnapshot) => {
    set({
      status: snapshot.status || 'unknown',
      activeNodes: new Set<string>(snapshot.active_nodes || []),
      executedTools: snapshot.executed_tools || [],
      budget: snapshot.budget || {},
      error: snapshot.error || null,
      pinnedRevisionId: snapshot.revision_id || null,
    })
  },

  reset: () => {
    set({
      runId: null,
      runList: [],
      isLoadingRuns: false,
      status: 'unknown',
      activeNodes: new Set<string>(),
      completedNodes: new Set<string>(),
      failedNodes: new Set<string>(),
      events: [],
      executedTools: [],
      budget: {},
      error: null,
      startedAt: null,
      pinnedRevisionId: null,
      pinnedRevisionGraph: null,
      isLoadingRevision: false,
      sseState: 'closed',
      lastEventAt: null,
    })
  },
}))

import React, { useEffect, useState, useCallback } from 'react'
import { ReactFlowProvider } from '@xyflow/react'
import { Header } from './components/layout/Header'
import { ModeBar } from './components/layout/ModeBar'
import { ComponentLibrary } from './components/library/ComponentLibrary'
import { CanvasContent } from './components/canvas/Canvas'
import { PropertiesPanel } from './components/inspector/PropertiesPanel'
import { DiagnosticsStrip } from './components/layout/DiagnosticsStrip'
import { CreateAgentDialog } from './components/dialogs/CreateAgentDialog'
import { ActivationDialog } from './components/dialogs/ActivationDialog'
import { RevocationDialog } from './components/dialogs/RevocationDialog'
import { LiveCanvas } from './components/live/LiveCanvas'
import { EventTimeline } from './components/live/EventTimeline'
import { LiveMetricsStrip } from './components/live/LiveMetricsStrip'
import { LoginDialog } from './components/dialogs/LoginDialog'
import { useCanvasStore } from './state/canvasStore'
import { useLiveStore } from './state/liveStore'
import { useEventStream } from './hooks/useEventStream'
import { api } from './api/client'
import type { AgentSummary } from './types/designer'
import './styles/tokens.css'

export const App: React.FC = () => {
  const [agents, setAgents] = useState<AgentSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [createDialogOpen, setCreateDialogOpen] = useState(false)
  const [activateDialogOpen, setActivateDialogOpen] = useState(false)
  const [revokeDialogOpen, setRevokeDialogOpen] = useState(false)
  const [loginDialogOpen, setLoginDialogOpen] = useState(false)

  const mode = useCanvasStore((s) => s.mode)
  const agent = useCanvasStore((s) => s.agent)
  const setAgent = useCanvasStore((s) => s.setAgent)
  const isDirty = useCanvasStore((s) => s.isDirty)
  const markClean = useCanvasStore((s) => s.markClean)
  const setSaving = useCanvasStore((s) => s.setSaving)
  const setValidating = useCanvasStore((s) => s.setValidating)
  const setValidationReport = useCanvasStore((s) => s.setValidationReport)
  const exportGraph = useCanvasStore((s) => s.exportGraph)
  const undo = useCanvasStore((s) => s.undo)
  const redo = useCanvasStore((s) => s.redo)
  const theme = useCanvasStore((s) => s.theme)

  // Live mode state & streaming
  const runId = useLiveStore((s) => s.runId)
  const processEvent = useLiveStore((s) => s.processEvent)
  const setSseState = useLiveStore((s) => s.setSseState)
  const loadSnapshot = useLiveStore((s) => s.loadSnapshot)

  const { connectionState, lastEventAt } = useEventStream({
    runId,
    onEvent: processEvent,
    enabled: mode === 'live',
  })

  useEffect(() => {
    setSseState(connectionState, lastEventAt)
  }, [connectionState, lastEventAt, setSseState])

  // Bootstrap live state snapshot when run selected in Live mode
  useEffect(() => {
    if (mode === 'live' && runId) {
      api
        .getRunSnapshot(runId)
        .then((snapshot) => {
          loadSnapshot(snapshot)
        })
        .catch((err) => {
          console.warn('Failed to load initial snapshot for run', err)
        })
    }
  }, [mode, runId, loadSnapshot])

  // Initialize theme
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
  }, [theme])

  // Warn on tab close if dirty
  useEffect(() => {
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (isDirty) {
        e.preventDefault()
        e.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => window.removeEventListener('beforeunload', handleBeforeUnload)
  }, [isDirty])

  // Initial load: session and agents
  const loadAgents = useCallback(async () => {
    try {
      setLoading(true)
      setError(null)
      const session = await api.getSession().catch(() => null)
      if (!session) {
        setLoginDialogOpen(true)
        setLoading(false)
        return
      }
      setLoginDialogOpen(false)
      const list = await api.listAgents()
      setAgents(list)
      if (list.length > 0) {
        const targetId = list[0].agent_id
        const detail = await api.getAgent(targetId)
        setAgent(detail)
      }
    } catch (err: any) {
      if (err.status === 401 || err.message?.includes('401') || err.message?.includes('Unauthorized')) {
        setLoginDialogOpen(true)
      } else {
        setError(err.message || 'Failed to initialize Agent Designer.')
      }
    } finally {
      setLoading(false)
    }
  }, [setAgent])

  useEffect(() => {
    loadAgents()
  }, [loadAgents])

  // Select another agent
  const handleSelectAgent = async (agentId: string) => {
    if (isDirty) {
      const confirmLeave = window.confirm(
        'You have unsaved changes in the current draft. Switch anyway?'
      )
      if (!confirmLeave) return
    }
    try {
      setLoading(true)
      const detail = await api.getAgent(agentId)
      setAgent(detail)
    } catch (err: any) {
      alert(`Could not load agent: ${err.message}`)
    } finally {
      setLoading(false)
    }
  }

  // Create new agent
  const handleCreateAgent = async (name: string, description: string, useVionTemplate: boolean) => {
    const newAgent = await api.createAgent({
      name,
      description,
      template: useVionTemplate ? 'vion' : undefined,
    })
    setAgents((prev) => [newAgent, ...prev])
    setAgent(newAgent)
  }

  // Save draft
  const handleSave = async () => {
    if (!agent) return
    setSaving(true)
    try {
      const graph = exportGraph()
      await api.saveDraft(agent.agent_id, graph, agent.row_version)
      markClean()
      // Refresh agent details with updated row_version and revision
      const updated = await api.getAgent(agent.agent_id)
      setAgent(updated)
    } catch (err: any) {
      alert(`Failed to save draft: ${err.message}`)
    } finally {
      setSaving(false)
    }
  }

  // Validate graph
  const handleValidate = async () => {
    if (!agent) return
    setValidating(true)
    try {
      const graph = exportGraph()
      const report = await api.validateGraph(agent.agent_id, graph)
      setValidationReport(report)
      if (report.ok) {
        alert('Validation succeeded! Graph structure, schemas, and connections are valid.')
      }
    } catch (err: any) {
      alert(`Validation error: ${err.message}`)
    } finally {
      setValidating(false)
    }
  }

  // Activate revision
  const handleActivateConfirm = async () => {
    if (!agent || !agent.draft?.revision_id) {
      throw new Error('No saved draft revision exists to activate. Please save draft first.')
    }
    await api.activateRevision(
      agent.agent_id,
      agent.draft.revision_id,
      agent.row_version
    )
    const updated = await api.getAgent(agent.agent_id)
    setAgent(updated)
    alert(`Revision v${updated.active_revision_number} is now active and serving!`)
  }

  // Revoke revision
  const handleRevokeConfirm = async () => {
    if (!agent) return
    await api.revokeAgent(agent.agent_id, agent.row_version)
    const updated = await api.getAgent(agent.agent_id)
    setAgent(updated)
    alert('Active revision has been revoked. The serving pointer is now cleared.')
  }

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const isMac = navigator.platform.toUpperCase().indexOf('MAC') >= 0
      const modifier = isMac ? e.metaKey : e.ctrlKey

      if (modifier && e.key.toLowerCase() === 's') {
        e.preventDefault()
        if (isDirty) handleSave()
      } else if (modifier && e.key.toLowerCase() === 'z') {
        e.preventDefault()
        if (e.shiftKey) {
          redo()
        } else {
          undo()
        }
      } else if (modifier && e.key.toLowerCase() === 'y') {
        e.preventDefault()
        redo()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isDirty, agent, exportGraph])

  if (loading && agents.length === 0) {
    return (
      <div
        style={{
          height: '100vh',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'var(--bg)',
          color: 'var(--text)',
          gap: 12,
        }}
      >
        <div style={{ fontWeight: 600, fontSize: 16 }}>Loading Agent Designer...</div>
        <div style={{ fontSize: 12, color: 'var(--muted)' }}>Connecting to Assistant Gateway</div>
      </div>
    )
  }

  if (error && agents.length === 0 && !loginDialogOpen) {
    return (
      <div
        style={{
          height: '100vh',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'var(--bg)',
          color: 'var(--red)',
          gap: 16,
          padding: 24,
          textAlign: 'center',
        }}
      >
        <div style={{ fontWeight: 700, fontSize: 18 }}>Could not load Agent Designer</div>
        <div style={{ fontSize: 13, maxWidth: 480, color: 'var(--muted)' }}>{error}</div>
        <button
          onClick={loadAgents}
          style={{
            padding: '8px 18px',
            background: 'var(--text)',
            color: 'var(--bg)',
            border: 'none',
            borderRadius: 'var(--radius-md)',
            fontWeight: 600,
          }}
        >
          Retry Connection
        </button>
      </div>
    )
  }

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100vh',
        width: '100vw',
        overflow: 'hidden',
        background: 'var(--bg)',
      }}
    >
      <Header
        agents={agents}
        onSelectAgent={handleSelectAgent}
        onCreateAgent={() => setCreateDialogOpen(true)}
      />

      <ModeBar
        onSave={handleSave}
        onValidate={handleValidate}
        onActivate={() => setActivateDialogOpen(true)}
        onRevoke={() => setRevokeDialogOpen(true)}
      />

      <div style={{ display: 'flex', flex: 1, position: 'relative', overflow: 'hidden' }}>
        {mode === 'live' ? (
          <>
            <EventTimeline />
            <div style={{ flex: 1, position: 'relative', height: '100%' }}>
              <ReactFlowProvider>
                <LiveCanvas />
              </ReactFlowProvider>
            </div>
          </>
        ) : mode === 'design' ? (
          <>
            <ComponentLibrary />
            <div style={{ flex: 1, position: 'relative', height: '100%' }}>
              <ReactFlowProvider>
                <CanvasContent />
              </ReactFlowProvider>
            </div>
            <PropertiesPanel />
          </>
        ) : (
          <div
            style={{
              flex: 1,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--muted)',
              gap: 8,
            }}
          >
            <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--text)' }}>
              Revision History
            </div>
            <div style={{ fontSize: 13 }}>
              Full audit trail, semantic diffing, and rollback workflow coming in P11.
            </div>
          </div>
        )}
      </div>

      {mode === 'live' ? <LiveMetricsStrip /> : <DiagnosticsStrip />}

      <CreateAgentDialog
        open={createDialogOpen}
        onClose={() => setCreateDialogOpen(false)}
        onCreate={handleCreateAgent}
      />

      <ActivationDialog
        open={activateDialogOpen}
        onClose={() => setActivateDialogOpen(false)}
        onConfirm={handleActivateConfirm}
      />

      <RevocationDialog
        open={revokeDialogOpen}
        onClose={() => setRevokeDialogOpen(false)}
        onConfirm={handleRevokeConfirm}
      />

      <LoginDialog
        open={loginDialogOpen}
        onSuccess={() => {
          setLoginDialogOpen(false)
          loadAgents()
        }}
      />
    </div>
  )
}

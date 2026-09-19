import React, { useEffect } from 'react'
import { useLiveStore } from '../../state/liveStore'
import { useCanvasStore } from '../../state/canvasStore'
import { api } from '../../api/client'

export const RunSelector: React.FC = () => {
  const agent = useCanvasStore((s) => s.agent)
  const runId = useLiveStore((s) => s.runId)
  const runList = useLiveStore((s) => s.runList)
  const isLoadingRuns = useLiveStore((s) => s.isLoadingRuns)
  const setRunId = useLiveStore((s) => s.setRunId)
  const setRunList = useLiveStore((s) => s.setRunList)
  const setLoadingRuns = useLiveStore((s) => s.setLoadingRuns)

  const loadRuns = async () => {
    if (!agent?.agent_id) return
    setLoadingRuns(true)
    try {
      const runs = await api.listAgentRuns(agent.agent_id)
      setRunList(runs)
      // Auto-select most recent run if none selected, prioritizing 'running'
      if (!runId && runs.length > 0) {
        const runningRun = runs.find((r) => r.status === 'running')
        setRunId(runningRun ? runningRun.run_id : runs[0].run_id)
      }
    } catch (err) {
      console.warn('Failed to load agent runs', err)
    } finally {
      setLoadingRuns(false)
    }
  }

  useEffect(() => {
    loadRuns()
  }, [agent?.agent_id])

  const formatRunLabel = (createdAt: string) => {
    try {
      const d = new Date(createdAt)
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    } catch {
      return ''
    }
  }

  if (runList.length === 0) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--muted)' }}>
        <span>No runs yet</span>
        <button
          onClick={loadRuns}
          disabled={isLoadingRuns}
          style={{
            background: 'var(--surface)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-sm)',
            padding: '2px 8px',
            fontSize: 11,
            color: 'var(--text)',
            cursor: 'pointer',
          }}
        >
          {isLoadingRuns ? '...' : 'Refresh'}
        </button>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <label
        htmlFor="run-selector"
        style={{ fontSize: 12, color: 'var(--muted)', fontWeight: 500 }}
      >
        Run:
      </label>
      <select
        id="run-selector"
        value={runId || ''}
        onChange={(e) => setRunId(e.target.value || null)}
        disabled={isLoadingRuns}
        style={{
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-sm)',
          color: 'var(--text)',
          fontSize: 12,
          padding: '4px 8px',
          outline: 'none',
          fontFamily: 'var(--font-mono)',
          maxWidth: 240,
        }}
      >
        {runList.map((r) => {
          const shortId = r.run_id.slice(0, 8)
          const time = formatRunLabel(r.created_at)
          return (
            <option key={r.run_id} value={r.run_id}>
              {`[${r.status.toUpperCase()}] ${shortId}... (${time})`}
            </option>
          )
        })}
      </select>
      <button
        onClick={loadRuns}
        disabled={isLoadingRuns}
        title="Refresh runs list"
        style={{
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-sm)',
          padding: '4px 8px',
          fontSize: 11,
          color: 'var(--muted)',
          cursor: 'pointer',
        }}
      >
        {isLoadingRuns ? '...' : '↻'}
      </button>
    </div>
  )
}

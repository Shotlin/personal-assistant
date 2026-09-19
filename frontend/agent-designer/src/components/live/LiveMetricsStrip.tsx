import React, { useEffect, useState } from 'react'
import { useLiveStore } from '../../state/liveStore'

export const LiveMetricsStrip: React.FC = () => {
  const status = useLiveStore((s) => s.status)
  const startedAt = useLiveStore((s) => s.startedAt)
  const budget = useLiveStore((s) => s.budget)
  const executedTools = useLiveStore((s) => s.executedTools)
  const events = useLiveStore((s) => s.events)
  const sseState = useLiveStore((s) => s.sseState)
  const lastEventAt = useLiveStore((s) => s.lastEventAt)

  const [elapsed, setElapsed] = useState<string>('00:00.0')

  // Calculate elapsed time from startedAt
  useEffect(() => {
    if (!startedAt) {
      setElapsed('00:00.0')
      return
    }

    const startMs = new Date(startedAt).getTime()
    if (isNaN(startMs)) return

    const updateTimer = () => {
      const now = Date.now()
      const diffMs = Math.max(0, now - startMs)
      const totalSec = Math.floor(diffMs / 1000)
      const m = Math.floor(totalSec / 60)
      const s = totalSec % 60
      const tenths = Math.floor((diffMs % 1000) / 100)
      setElapsed(`${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}.${tenths}`)
    }

    updateTimer()

    // Stop updating if run is finished
    if (status === 'completed' || status === 'failed') {
      return
    }

    const interval = setInterval(updateTimer, 200)
    return () => clearInterval(interval)
  }, [startedAt, status])

  // Count model attempts
  const modelAttempts = events.filter(
    (e) => e.event_type === 'node.started' && (e.payload?.type === 'model' || e.payload?.model)
  ).length

  const formatLastEventTime = (timestamp: number | null) => {
    if (!timestamp) return ''
    const d = new Date(timestamp)
    return d.toTimeString().split(' ')[0]
  }

  return (
    <footer
      style={{
        height: 'var(--diagnostics-height)',
        background: 'var(--panel)',
        borderTop: '1px solid var(--border)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0 16px',
        fontSize: 12,
        color: 'var(--muted)',
        zIndex: 10,
        userSelect: 'none',
      }}
    >
      {/* Left side: Status badge & elapsed time */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: '50%',
              background:
                status === 'running'
                  ? 'var(--blue)'
                  : status === 'completed'
                    ? 'var(--green)'
                    : status === 'failed'
                      ? 'var(--red)'
                      : 'var(--muted)',
              animation: status === 'running' ? 'nodePulse 1.5s infinite ease-in-out' : 'none',
            }}
          />
          <span
            style={{
              fontWeight: 600,
              fontSize: 11,
              textTransform: 'uppercase',
              color:
                status === 'running'
                  ? 'var(--blue)'
                  : status === 'completed'
                    ? 'var(--green)'
                    : status === 'failed'
                      ? 'var(--red)'
                      : 'var(--muted)',
            }}
          >
            {status}
          </span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <span>Elapsed:</span>
          <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text)' }}>
            {elapsed}
          </span>
        </div>

        {modelAttempts > 0 && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <span>Model Invocations:</span>
            <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text)' }}>
              {modelAttempts}
            </span>
          </div>
        )}
      </div>

      {/* Middle: Token budget & Tool calls */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        {budget.estimated_context_tokens !== undefined && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <span>Est. Tokens:</span>
            <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text)' }}>
              {budget.estimated_context_tokens.toLocaleString()}
            </span>
          </div>
        )}

        {(budget.provider_reported_input_tokens !== undefined ||
          budget.provider_reported_output_tokens !== undefined) && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <span>In/Out Tokens:</span>
            <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text)' }}>
              {(budget.provider_reported_input_tokens || 0).toLocaleString()} /{' '}
              {(budget.provider_reported_output_tokens || 0).toLocaleString()}
            </span>
          </div>
        )}

        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <span>Tools:</span>
          <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text)' }}>
            {executedTools.length}
          </span>
        </div>

        {budget.cumulative_cost_cents !== undefined && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <span>Cost:</span>
            <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text)' }}>
              ${(budget.cumulative_cost_cents / 100).toFixed(4)}
            </span>
          </div>
        )}
      </div>

      {/* Right side: SSE connection status indicator */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span
          style={{
            width: 7,
            height: 7,
            borderRadius: '50%',
            background:
              sseState === 'connected'
                ? 'var(--green)'
                : sseState === 'reconnecting' || sseState === 'connecting'
                  ? 'var(--amber)'
                  : 'var(--border)',
            animation: sseState === 'reconnecting' ? 'nodePulse 1.5s infinite ease-in-out' : 'none',
          }}
        />
        <span style={{ fontSize: 11 }}>
          {sseState === 'connected' && 'Live SSE Connected'}
          {sseState === 'connecting' && 'Connecting...'}
          {sseState === 'reconnecting' &&
            `Reconnecting...${lastEventAt ? ` (last event at ${formatLastEventTime(lastEventAt)})` : ''}`}
          {sseState === 'closed' && 'Stream Inactive'}
        </span>
      </div>
    </footer>
  )
}

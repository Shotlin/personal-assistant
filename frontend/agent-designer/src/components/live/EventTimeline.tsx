import React, { useEffect, useRef, useState } from 'react'
import { useLiveStore } from '../../state/liveStore'
import type { RunEvent } from '../../types/designer'

export const EventTimeline: React.FC = () => {
  const events = useLiveStore((s) => s.events)
  const status = useLiveStore((s) => s.status)
  const [autoScroll, setAutoScroll] = useState(true)
  const [expandedEvents, setExpandedEvents] = useState<Record<number, boolean>>({})

  const listEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (autoScroll && listEndRef.current) {
      listEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [events, autoScroll])

  const toggleExpand = (eventId: number) => {
    setExpandedEvents((prev) => ({
      ...prev,
      [eventId]: !prev[eventId],
    }))
  }

  const formatTimestamp = (isoStr: string) => {
    try {
      const d = new Date(isoStr)
      if (isNaN(d.getTime())) return isoStr
      const hh = String(d.getHours()).padStart(2, '0')
      const mm = String(d.getMinutes()).padStart(2, '0')
      const ss = String(d.getSeconds()).padStart(2, '0')
      const ms = String(d.getMilliseconds()).padStart(3, '0')
      return `${hh}:${mm}:${ss}.${ms}`
    } catch {
      return isoStr
    }
  }

  const getEventBadge = (eventType: string) => {
    switch (eventType) {
      case 'run.started':
        return { label: 'RUN START', color: 'var(--blue)', bg: 'rgba(96, 165, 250, 0.15)' }
      case 'run.completed':
        return { label: 'RUN COMPLETE', color: 'var(--green)', bg: 'rgba(74, 222, 128, 0.15)' }
      case 'run.failed':
        return { label: 'RUN FAILED', color: 'var(--red)', bg: 'rgba(248, 113, 113, 0.15)' }
      case 'node.started':
        return { label: 'NODE START', color: 'var(--blue)', bg: 'rgba(96, 165, 250, 0.12)' }
      case 'node.completed':
        return { label: 'NODE DONE', color: 'var(--green)', bg: 'rgba(74, 222, 128, 0.12)' }
      case 'node.failed':
        return { label: 'NODE FAIL', color: 'var(--red)', bg: 'rgba(248, 113, 113, 0.12)' }
      case 'tool.invoked':
        return { label: 'TOOL CALL', color: '#C084FC', bg: 'rgba(192, 132, 252, 0.15)' }
      case 'tool.completed':
        return { label: 'TOOL DONE', color: 'var(--green)', bg: 'rgba(74, 222, 128, 0.12)' }
      case 'tool.failed':
        return { label: 'TOOL FAIL', color: 'var(--red)', bg: 'rgba(248, 113, 113, 0.12)' }
      case 'context.budget_update':
        return { label: 'BUDGET', color: 'var(--muted)', bg: 'rgba(161, 161, 170, 0.15)' }
      default:
        return { label: eventType.toUpperCase(), color: 'var(--muted)', bg: 'var(--surface)' }
    }
  }

  const getTargetName = (event: RunEvent) => {
    const payload = event.payload || {}
    if (payload.tool_name) return payload.tool_name
    if (payload.node_id) return payload.node_id
    if (payload.model) return payload.model
    return null
  }

  return (
    <aside
      style={{
        width: 'var(--library-width)',
        minWidth: 'var(--library-width)',
        height: '100%',
        background: 'var(--panel)',
        borderRight: '1px solid var(--border)',
        display: 'flex',
        flexDirection: 'column',
        zIndex: 5,
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: '12px 14px',
          borderBottom: '1px solid var(--border)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <h2 style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>Timeline</h2>
          <span
            style={{
              fontSize: 11,
              padding: '1px 6px',
              borderRadius: 10,
              background: 'var(--surface)',
              color: 'var(--muted)',
            }}
          >
            {events.length}
          </span>
        </div>
        <button
          onClick={() => setAutoScroll((prev) => !prev)}
          style={{
            fontSize: 11,
            padding: '3px 8px',
            borderRadius: 'var(--radius-sm)',
            border: '1px solid var(--border)',
            background: autoScroll ? 'rgba(96, 165, 250, 0.15)' : 'var(--surface)',
            color: autoScroll ? 'var(--blue)' : 'var(--muted)',
            cursor: 'pointer',
          }}
          title={autoScroll ? 'Auto-scroll is on' : 'Auto-scroll is paused'}
        >
          {autoScroll ? 'Auto-scroll' : 'Paused'}
        </button>
      </div>

      {/* Event List */}
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '8px 10px',
          display: 'flex',
          flexDirection: 'column',
          gap: 6,
        }}
      >
        {events.length === 0 ? (
          <div
            style={{
              padding: '24px 12px',
              textAlign: 'center',
              color: 'var(--muted)',
              fontSize: 12,
            }}
          >
            Waiting for execution events...
          </div>
        ) : (
          events.map((ev) => {
            const badge = getEventBadge(ev.event_type)
            const target = getTargetName(ev)
            const isExpanded = !!expandedEvents[ev.event_id]

            return (
              <div
                key={ev.event_id}
                style={{
                  background: 'var(--surface)',
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--radius-sm)',
                  padding: '8px 10px',
                  fontSize: 12,
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 4,
                }}
              >
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                  }}
                >
                  <span
                    style={{
                      fontSize: 10,
                      fontWeight: 700,
                      padding: '2px 5px',
                      borderRadius: 3,
                      background: badge.bg,
                      color: badge.color,
                      letterSpacing: '0.03em',
                    }}
                  >
                    {badge.label}
                  </span>
                  <span
                    style={{
                      fontSize: 10,
                      fontFamily: 'var(--font-mono)',
                      color: 'var(--muted)',
                    }}
                  >
                    {formatTimestamp(ev.at)}
                  </span>
                </div>

                {target && (
                  <div
                    style={{
                      fontFamily: 'var(--font-mono)',
                      fontSize: 11,
                      color: 'var(--text)',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                    title={String(target)}
                  >
                    {target}
                  </div>
                )}

                {ev.payload?.error && (
                  <div
                    style={{
                      fontSize: 11,
                      color: 'var(--red)',
                      background: 'rgba(248, 113, 113, 0.1)',
                      padding: '4px 6px',
                      borderRadius: 3,
                      lineHeight: 1.3,
                      wordBreak: 'break-word',
                    }}
                  >
                    {String(ev.payload.error)}
                  </div>
                )}

                <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 2 }}>
                  <button
                    onClick={() => toggleExpand(ev.event_id)}
                    style={{
                      background: 'transparent',
                      border: 'none',
                      color: 'var(--muted)',
                      fontSize: 10,
                      cursor: 'pointer',
                      padding: 0,
                    }}
                  >
                    {isExpanded ? 'Hide Payload ▲' : 'View Payload ▼'}
                  </button>
                </div>

                {isExpanded && (
                  <pre
                    style={{
                      fontFamily: 'var(--font-mono)',
                      fontSize: 10,
                      background: 'var(--bg)',
                      border: '1px solid var(--border)',
                      borderRadius: 3,
                      padding: 6,
                      maxHeight: 140,
                      overflow: 'auto',
                      color: 'var(--text)',
                      whiteSpace: 'pre-wrap',
                      wordBreak: 'break-word',
                    }}
                  >
                    {JSON.stringify(ev.payload, null, 2)}
                  </pre>
                )}
              </div>
            )
          })
        )}
        <div ref={listEndRef} />
      </div>

      {/* Footer Run Status indicator */}
      <div
        style={{
          padding: '8px 14px',
          borderTop: '1px solid var(--border)',
          background: 'var(--panel)',
          fontSize: 11,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <span style={{ color: 'var(--muted)' }}>Status</span>
        <span
          style={{
            fontWeight: 600,
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
    </aside>
  )
}

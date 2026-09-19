import { useEffect, useRef, useState, useCallback } from 'react'
import type { RunEvent } from '../types/designer'

export type SSEConnectionState = 'connecting' | 'connected' | 'reconnecting' | 'closed'

interface UseEventStreamOptions {
  runId: string | null
  onEvent?: (event: RunEvent) => void
  enabled?: boolean
}

export function useEventStream({
  runId,
  onEvent,
  enabled = true,
}: UseEventStreamOptions) {
  const [connectionState, setConnectionState] = useState<SSEConnectionState>('closed')
  const [lastEventAt, setLastEventAt] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const eventSourceRef = useRef<EventSource | null>(null)
  const lastEventIdRef = useRef<number | null>(null)
  const reconnectTimeoutRef = useRef<any>(null)
  const backoffRef = useRef<number>(1000)
  const closedExplicitlyRef = useRef<boolean>(false)
  const onEventRef = useRef(onEvent)
  onEventRef.current = onEvent

  const cleanup = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current)
      reconnectTimeoutRef.current = null
    }
    if (eventSourceRef.current) {
      eventSourceRef.current.close()
      eventSourceRef.current = null
    }
  }, [])

  const connect = useCallback(() => {
    if (!runId || !enabled || closedExplicitlyRef.current) {
      setConnectionState('closed')
      return
    }

    cleanup()

    let url = `/designer/api/v1/runs/${runId}/events`
    if (lastEventIdRef.current !== null) {
      url += `?last_event_id=${lastEventIdRef.current}`
    }

    setConnectionState((prev) => (prev === 'connected' ? 'reconnecting' : 'connecting'))

    try {
      const es = new EventSource(url, { withCredentials: true })
      eventSourceRef.current = es

      es.onopen = () => {
        setConnectionState('connected')
        setError(null)
        backoffRef.current = 1000 // Reset backoff on successful connection
      }

      es.onmessage = (e) => {
        if (!e.data) return
        try {
          const parsed = JSON.parse(e.data) as RunEvent
          if (parsed && typeof parsed.event_id === 'number') {
            lastEventIdRef.current = parsed.event_id
          }
          const now = Date.now()
          setLastEventAt(now)
          onEventRef.current?.(parsed)

          if (parsed.event_type === 'run.completed' || parsed.event_type === 'run.failed') {
            // Run has reached terminal state; stream is complete
            closedExplicitlyRef.current = true
            cleanup()
            setConnectionState('closed')
          }
        } catch (err) {
          console.warn('Failed to parse SSE event data', err)
        }
      }

      es.onerror = () => {
        es.close()
        eventSourceRef.current = null

        if (closedExplicitlyRef.current) {
          setConnectionState('closed')
          return
        }

        setConnectionState('reconnecting')
        const delay = backoffRef.current
        backoffRef.current = Math.min(delay * 2, 30000)

        reconnectTimeoutRef.current = setTimeout(() => {
          connect()
        }, delay)
      }
    } catch (err: any) {
      setError(err?.message || 'Failed to open event stream')
      setConnectionState('reconnecting')
      reconnectTimeoutRef.current = setTimeout(connect, backoffRef.current)
    }
  }, [runId, enabled, cleanup])

  useEffect(() => {
    closedExplicitlyRef.current = false
    lastEventIdRef.current = null
    backoffRef.current = 1000
    setError(null)

    if (runId && enabled) {
      connect()
    } else {
      cleanup()
      setConnectionState('closed')
    }

    return () => {
      cleanup()
    }
  }, [runId, enabled, connect, cleanup])

  return {
    connectionState,
    lastEventAt,
    error,
    lastEventId: lastEventIdRef.current,
    reconnect: () => {
      closedExplicitlyRef.current = false
      backoffRef.current = 1000
      connect()
    },
  }
}

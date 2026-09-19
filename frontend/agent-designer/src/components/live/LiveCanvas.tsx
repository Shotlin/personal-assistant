import React, { useEffect, useState, useMemo } from 'react'
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  type Node,
  type Edge,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useLiveStore } from '../../state/liveStore'
import { useCanvasStore } from '../../state/canvasStore'
import { nodeTypes } from '../canvas/nodes'
import { api } from '../../api/client'
import type { GraphNode } from '../../types/designer'

export const LiveCanvas: React.FC = () => {
  const agent = useCanvasStore((s) => s.agent)
  const theme = useCanvasStore((s) => s.theme)

  const runId = useLiveStore((s) => s.runId)
  const runList = useLiveStore((s) => s.runList)
  const isLoadingRuns = useLiveStore((s) => s.isLoadingRuns)
  const activeNodes = useLiveStore((s) => s.activeNodes)
  const completedNodes = useLiveStore((s) => s.completedNodes)
  const failedNodes = useLiveStore((s) => s.failedNodes)
  const pinnedRevisionGraph = useLiveStore((s) => s.pinnedRevisionGraph)
  const isLoadingRevision = useLiveStore((s) => s.isLoadingRevision)
  const setPinnedRevision = useLiveStore((s) => s.setPinnedRevision)
  const setLoadingRevision = useLiveStore((s) => s.setLoadingRevision)

  const currentRun = useMemo(() => {
    return runList.find((r) => r.run_id === runId) || null
  }, [runList, runId])

  const [revisionNumber, setRevisionNumber] = useState<number | null>(null)

  // Fetch pinned revision graph when run or revision_id changes
  useEffect(() => {
    if (!agent || !currentRun?.revision_id) {
      if (agent?.draft?.graph && !pinnedRevisionGraph) {
        // Fallback to draft graph if no revision_id
        setPinnedRevision(agent.draft.revision_id, agent.draft.graph)
        setRevisionNumber(agent.draft.revision_number)
      }
      return
    }

    let isMounted = true
    setLoadingRevision(true)

    api
      .getRevision(agent.agent_id, currentRun.revision_id)
      .then((rev) => {
        if (!isMounted) return
        setPinnedRevision(rev.revision_id, rev.graph_json)
        setRevisionNumber(rev.revision_number)
      })
      .catch((err) => {
        if (!isMounted) return
        console.warn('Failed to load pinned revision graph for run, using fallback', err)
        // Fallback to draft graph if revision endpoint fails or missing
        if (agent.draft?.graph) {
          setPinnedRevision(agent.draft.revision_id, agent.draft.graph)
          setRevisionNumber(agent.draft.revision_number)
        } else {
          setLoadingRevision(false)
        }
      })

    return () => {
      isMounted = false
    }
  }, [agent, currentRun?.revision_id, setPinnedRevision, setLoadingRevision])

  // Transform graph document into ReactFlow nodes & edges with live status styles
  const flowNodes: Node[] = useMemo(() => {
    if (!pinnedRevisionGraph?.nodes) return []

    return pinnedRevisionGraph.nodes.map((gn: GraphNode) => {
      let className = ''
      if (activeNodes.has(gn.id)) {
        className = 'live-node-active'
      } else if (failedNodes.has(gn.id)) {
        className = 'live-node-failed'
      } else if (completedNodes.has(gn.id)) {
        className = 'live-node-completed'
      }

      return {
        id: gn.id,
        type: gn.type,
        position: gn.position || { x: 250, y: 200 },
        draggable: false,
        selectable: false,
        className,
        data: {
          label: (gn.data?.label as string) || gn.id,
          type: gn.type,
          enabled: gn.data?.enabled ?? true,
          config: gn.data?.config || {},
          isActive: activeNodes.has(gn.id),
          isCompleted: completedNodes.has(gn.id),
          isFailed: failedNodes.has(gn.id),
        },
      }
    })
  }, [pinnedRevisionGraph, activeNodes, completedNodes, failedNodes])

  const flowEdges: Edge[] = useMemo(() => {
    if (!pinnedRevisionGraph?.edges) return []

    return pinnedRevisionGraph.edges.map((ge) => {
      const isSourceActive = activeNodes.has(ge.source)
      const isTargetActive = activeNodes.has(ge.target)
      const isHot = isSourceActive || isTargetActive

      return {
        id: ge.id || `e-${ge.source}-${ge.target}`,
        source: ge.source,
        target: ge.target,
        sourceHandle: ge.sourceHandle || 'root',
        targetHandle: ge.targetHandle,
        animated: isHot,
        selectable: false,
        style: {
          stroke: isHot ? 'var(--blue)' : 'var(--edge-stroke)',
          strokeWidth: isHot ? 2.5 : 1.5,
        },
      }
    })
  }, [pinnedRevisionGraph, activeNodes])

  // Empty state: no runs for agent
  if (!isLoadingRuns && runList.length === 0) {
    return (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'var(--bg)',
          color: 'var(--muted)',
          gap: 12,
        }}
      >
        <div style={{ fontSize: 36 }}>⚡</div>
        <h3 style={{ color: 'var(--text)', fontSize: 16 }}>No runs recorded yet</h3>
        <p style={{ fontSize: 13, maxWidth: 360, textAlign: 'center', lineHeight: 1.5 }}>
          Start a conversation with this agent in Open WebUI to see real-time execution flow, node activations, and tool calls.
        </p>
      </div>
    )
  }

  // Loading state
  if (isLoadingRevision && !pinnedRevisionGraph) {
    return (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'var(--bg)',
          color: 'var(--muted)',
          fontSize: 14,
        }}
      >
        Loading execution graph...
      </div>
    )
  }

  const activeRevNum = agent?.active_revision_number
  const isDifferentRevision =
    revisionNumber !== null && activeRevNum !== null && revisionNumber !== activeRevNum

  return (
    <div style={{ width: '100%', height: '100%', position: 'relative' }}>
      {/* Read-Only & Revision Banner */}
      <div
        style={{
          position: 'absolute',
          top: 12,
          left: 16,
          zIndex: 10,
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          background: 'rgba(24, 24, 27, 0.85)',
          backdropFilter: 'blur(8px)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-md)',
          padding: '6px 12px',
          fontSize: 12,
          color: 'var(--text)',
        }}
      >
        <span
          style={{
            display: 'inline-block',
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: 'var(--blue)',
          }}
        />
        <span>
          <strong>Live Mode (Read-Only)</strong>
          {revisionNumber !== null ? ` — Graph pinned to v${revisionNumber}` : ''}
        </span>
        {isDifferentRevision && activeRevNum !== null && (
          <span
            style={{
              padding: '2px 6px',
              borderRadius: 'var(--radius-sm)',
              background: 'rgba(251, 191, 36, 0.15)',
              color: 'var(--amber)',
              fontSize: 11,
              fontWeight: 500,
            }}
          >
            New runs use active v{activeRevNum}
          </span>
        )}
      </div>

      <ReactFlow
        nodes={flowNodes}
        edges={flowEdges}
        nodeTypes={nodeTypes}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        fitView
        minZoom={0.2}
        maxZoom={2.0}
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={16}
          size={1}
          color={theme === 'dark' ? '#27272A' : '#E4E4E7'}
        />
        <Controls
          showInteractive={false}
          style={{
            background: 'var(--panel)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-md)',
            overflow: 'hidden',
          }}
        />
        <MiniMap
          style={{
            background: 'var(--panel)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-md)',
          }}
          nodeColor={(n) => {
            if (activeNodes.has(n.id)) return 'var(--blue)'
            if (failedNodes.has(n.id)) return 'var(--red)'
            if (completedNodes.has(n.id)) return 'var(--green)'
            return 'var(--border)'
          }}
        />
      </ReactFlow>
    </div>
  )
}

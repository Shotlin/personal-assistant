import React, { useCallback } from 'react'
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  useReactFlow,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useCanvasStore } from '../../state/canvasStore'
import { nodeTypes } from './nodes'
import type { NodeType } from '../../types/designer'

export const CanvasContent: React.FC = () => {
  const nodes = useCanvasStore((s) => s.nodes)
  const edges = useCanvasStore((s) => s.edges)
  const onNodesChange = useCanvasStore((s) => s.onNodesChange)
  const onEdgesChange = useCanvasStore((s) => s.onEdgesChange)
  const onConnect = useCanvasStore((s) => s.onConnect)
  const setSelectedNodeId = useCanvasStore((s) => s.setSelectedNodeId)
  const addNode = useCanvasStore((s) => s.addNode)
  const theme = useCanvasStore((s) => s.theme)

  const { screenToFlowPosition } = useReactFlow()

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
  }, [])

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault()

      const type = event.dataTransfer.getData('application/reactflow-type') as NodeType
      const label = event.dataTransfer.getData('application/reactflow-label')
      const configStr = event.dataTransfer.getData('application/reactflow-config')
      let config = {}
      try {
        if (configStr) config = JSON.parse(configStr)
      } catch {
        config = {}
      }

      if (!type) return

      const position = screenToFlowPosition({
        x: event.clientX,
        y: event.clientY,
      })

      addNode(type, label, config, position)
    },
    [screenToFlowPosition, addNode]
  )

  return (
    <div
      style={{ width: '100%', height: '100%', position: 'relative' }}
      onDragOver={onDragOver}
      onDrop={onDrop}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        nodeTypes={nodeTypes}
        onNodeClick={(_, node) => setSelectedNodeId(node.id)}
        onPaneClick={() => setSelectedNodeId(null)}
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
            if (n.type === 'agent') return 'var(--blue)'
            if (n.type === 'model') return 'var(--blue)'
            if (n.type === 'skill' || n.type === 'tool') return 'var(--green)'
            if (n.type === 'memory') return 'var(--amber)'
            return 'var(--muted)'
          }}
        />
      </ReactFlow>
    </div>
  )
}

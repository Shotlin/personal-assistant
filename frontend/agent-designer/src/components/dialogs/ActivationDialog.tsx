import React, { useState, useEffect } from 'react'
import { AlertTriangle, Check, X, ArrowRight, ShieldAlert } from 'lucide-react'
import { useCanvasStore } from '../../state/canvasStore'
import { api } from '../../api/client'
import type { GraphDocument } from '../../types/designer'

interface ActivationDialogProps {
  open: boolean
  onClose: () => void
  onConfirm: () => Promise<void>
}

interface GraphDiff {
  addedNodes: string[]
  removedNodes: string[]
  changedNodes: string[]
  addedEdges: number
  removedEdges: number
  credentialRefs: string[]
}

export const ActivationDialog: React.FC<ActivationDialogProps> = ({
  open,
  onClose,
  onConfirm,
}) => {
  const agent = useCanvasStore((s) => s.agent)
  const isDirty = useCanvasStore((s) => s.isDirty)
  const exportGraph = useCanvasStore((s) => s.exportGraph)

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [diff, setDiff] = useState<GraphDiff | null>(null)
  const [contextEstimate, setContextEstimate] = useState<number | null>(null)
  const [diffLoading, setDiffLoading] = useState(false)

  const activeRevNum = agent?.active_revision_number
  const candidateRevNum = agent?.draft?.revision_number || 1
  const candidateRevisionId = agent?.draft?.revision_id

  useEffect(() => {
    if (!open || !agent) {
      setDiff(null)
      setError('')
      return
    }

    const currentGraph = agent.draft?.graph || exportGraph()

    // Fetch context estimate
    api
      .previewContext(agent.agent_id, currentGraph)
      .then((res) => {
        if (res?.estimated_tokens) {
          setContextEstimate(res.estimated_tokens)
        }
      })
      .catch(() => {
        // Ignore preview failure if not critical
      })

    // Compute diff against active revision
    if (!agent.active_revision_id) {
      // First activation — everything is new
      const credentialRefs: string[] = []
      for (const node of currentGraph.nodes || []) {
        if (node.data?.config?.credential_ref) {
          credentialRefs.push(String(node.data.config.credential_ref))
        }
      }
      setDiff({
        addedNodes: (currentGraph.nodes || []).map((n) => n.data?.label || n.id),
        removedNodes: [],
        changedNodes: [],
        addedEdges: (currentGraph.edges || []).length,
        removedEdges: 0,
        credentialRefs,
      })
      return
    }

    setDiffLoading(true)
    api
      .getRevision(agent.agent_id, agent.active_revision_id)
      .then((activeRev) => {
        const activeGraph: GraphDocument = activeRev.graph_json || { schema_version: 1, nodes: [], edges: [] }
        const activeNodeMap = new Map((activeGraph.nodes || []).map((n) => [n.id, n]))
        const currentNodeMap = new Map((currentGraph.nodes || []).map((n) => [n.id, n]))

        const addedNodes: string[] = []
        const changedNodes: string[] = []
        const credentialRefs: string[] = []

        for (const [id, curr] of currentNodeMap.entries()) {
          if (curr.data?.config?.credential_ref) {
            credentialRefs.push(String(curr.data.config.credential_ref))
          }
          if (!activeNodeMap.has(id)) {
            addedNodes.push(curr.data?.label || id)
          } else {
            const prev = activeNodeMap.get(id)!
            if (
              prev.type !== curr.type ||
              prev.data?.label !== curr.data?.label ||
              JSON.stringify(prev.data?.config || {}) !== JSON.stringify(curr.data?.config || {})
            ) {
              changedNodes.push(curr.data?.label || id)
            }
          }
        }

        const removedNodes: string[] = []
        for (const [id, prev] of activeNodeMap.entries()) {
          if (!currentNodeMap.has(id)) {
            removedNodes.push(prev.data?.label || id)
          }
        }

        const activeEdgeCount = (activeGraph.edges || []).length
        const currentEdgeCount = (currentGraph.edges || []).length

        setDiff({
          addedNodes,
          removedNodes,
          changedNodes,
          addedEdges: Math.max(0, currentEdgeCount - activeEdgeCount),
          removedEdges: Math.max(0, activeEdgeCount - currentEdgeCount),
          credentialRefs,
        })
      })
      .catch((err) => {
        console.warn('Failed to fetch active revision for diff', err)
      })
      .finally(() => {
        setDiffLoading(false)
      })
  }, [open, agent])

  if (!open) return null

  const handleActivate = async () => {
    setLoading(true)
    setError('')
    try {
      await onConfirm()
      onClose()
    } catch (err: any) {
      setError(
        err?.message ||
          `Could not activate v${candidateRevNum}. ${activeRevNum ? `v${activeRevNum} remains active.` : 'No revision active.'}`
      )
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0, 0, 0, 0.65)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
        backdropFilter: 'blur(4px)',
      }}
    >
      <div
        style={{
          background: 'var(--panel)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-lg)',
          width: 520,
          maxWidth: '92vw',
          maxHeight: '90vh',
          display: 'flex',
          flexDirection: 'column',
          boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.5)',
          overflow: 'hidden',
        }}
      >
        {/* Header */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '16px 20px',
            borderBottom: '1px solid var(--border)',
          }}
        >
          <div style={{ fontWeight: 700, fontSize: 16 }}>Activate Agent Revision</div>
          <button
            onClick={onClose}
            style={{ background: 'transparent', border: 'none', color: 'var(--muted)', display: 'flex' }}
          >
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div
          style={{
            padding: '20px',
            display: 'flex',
            flexDirection: 'column',
            gap: 16,
            overflowY: 'auto',
          }}
        >
          {error && (
            <div
              style={{
                padding: '10px 14px',
                background: 'rgba(248, 113, 113, 0.1)',
                border: '1px solid var(--red)',
                borderRadius: 'var(--radius-md)',
                color: 'var(--red)',
                fontSize: 12,
                lineHeight: 1.4,
              }}
            >
              <strong>Activation Failed:</strong> {error}
            </div>
          )}

          {isDirty ? (
            <div
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: 10,
                padding: '12px',
                background: 'rgba(251, 191, 36, 0.1)',
                border: '1px solid var(--amber)',
                borderRadius: 'var(--radius-md)',
                color: 'var(--amber)',
                fontSize: 13,
              }}
            >
              <AlertTriangle size={18} style={{ flexShrink: 0, marginTop: 2 }} />
              <div>
                <strong>Unsaved changes detected.</strong> Please save your draft revision before
                activating.
              </div>
            </div>
          ) : (
            <>
              {/* Revision Transition Visual */}
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: 16,
                  padding: '14px',
                  background: 'var(--surface)',
                  borderRadius: 'var(--radius-md)',
                  border: '1px solid var(--border)',
                }}
              >
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase' }}>
                    Current Active
                  </div>
                  <div style={{ fontSize: 16, fontWeight: 700, marginTop: 2 }}>
                    {activeRevNum ? `v${activeRevNum}` : 'None'}
                  </div>
                </div>
                <ArrowRight size={20} color="var(--muted)" />
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 11, color: 'var(--blue)', textTransform: 'uppercase' }}>
                    Candidate Revision
                  </div>
                  <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--blue)', marginTop: 2 }}>
                    v{candidateRevNum}
                  </div>
                </div>
              </div>

              {/* Effect note */}
              <div
                style={{
                  padding: '8px 12px',
                  background: 'rgba(96, 165, 250, 0.08)',
                  border: '1px solid rgba(96, 165, 250, 0.2)',
                  borderRadius: 'var(--radius-sm)',
                  fontSize: 12,
                  color: 'var(--blue)',
                }}
              >
                ℹ️ <strong>Deployment Scope:</strong> New runs will immediately use v{candidateRevNum}.
                Existing runs retain their version.
              </div>

              {/* Diff summary */}
              {diffLoading ? (
                <div style={{ fontSize: 12, color: 'var(--muted)', textAlign: 'center', padding: 8 }}>
                  Comparing revisions...
                </div>
              ) : diff ? (
                <div
                  style={{
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 8,
                    fontSize: 12,
                    border: '1px solid var(--border)',
                    borderRadius: 'var(--radius-md)',
                    padding: '12px',
                  }}
                >
                  <div style={{ fontWeight: 600, color: 'var(--text)', fontSize: 13 }}>
                    Changes Summary
                  </div>

                  {diff.addedNodes.length > 0 && (
                    <div style={{ color: 'var(--green)' }}>
                      + Added nodes ({diff.addedNodes.length}): {diff.addedNodes.join(', ')}
                    </div>
                  )}

                  {diff.removedNodes.length > 0 && (
                    <div style={{ color: 'var(--red)' }}>
                      - Removed nodes ({diff.removedNodes.length}): {diff.removedNodes.join(', ')}
                    </div>
                  )}

                  {diff.changedNodes.length > 0 && (
                    <div style={{ color: 'var(--amber)' }}>
                      ~ Modified nodes ({diff.changedNodes.length}): {diff.changedNodes.join(', ')}
                    </div>
                  )}

                  {diff.addedNodes.length === 0 &&
                    diff.removedNodes.length === 0 &&
                    diff.changedNodes.length === 0 && (
                      <div style={{ color: 'var(--muted)' }}>
                        No structural node additions or removals compared to active revision.
                      </div>
                    )}

                  {diff.credentialRefs.length > 0 && (
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 6,
                        marginTop: 4,
                        paddingTop: 8,
                        borderTop: '1px solid var(--border)',
                        color: 'var(--muted)',
                      }}
                    >
                      <ShieldAlert size={14} color="var(--amber)" />
                      <span>
                        Credential references utilized: {diff.credentialRefs.join(', ')} (no secrets stored)
                      </span>
                    </div>
                  )}

                  {contextEstimate !== null && (
                    <div
                      style={{
                        marginTop: 4,
                        paddingTop: 8,
                        borderTop: '1px solid var(--border)',
                        color: 'var(--muted)',
                      }}
                    >
                      Estimated context tokens: <strong>{contextEstimate.toLocaleString()}</strong>
                    </div>
                  )}
                </div>
              ) : null}
            </>
          )}

          {/* Footer Buttons */}
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 6 }}>
            <button
              type="button"
              onClick={onClose}
              style={{
                padding: '8px 14px',
                background: 'var(--surface)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-md)',
                color: 'var(--text)',
                fontSize: 13,
                fontWeight: 600,
              }}
            >
              Cancel
            </button>
            <button
              onClick={handleActivate}
              disabled={loading || isDirty || !candidateRevisionId}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                padding: '8px 18px',
                background: 'var(--text)',
                color: 'var(--bg)',
                border: 'none',
                borderRadius: 'var(--radius-md)',
                fontSize: 13,
                fontWeight: 700,
                opacity: loading || isDirty || !candidateRevisionId ? 0.5 : 1,
              }}
            >
              <Check size={14} />
              <span>{loading ? 'Activating...' : `Activate v${candidateRevNum}`}</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

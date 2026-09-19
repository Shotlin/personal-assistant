export type NodeType =
  | 'agent'
  | 'model'
  | 'prompt'
  | 'skill'
  | 'memory'
  | 'context'
  | 'knowledge'
  | 'tool'
  | 'mcp'
  | 'cua'
  | 'terminal'

export type CapabilityStatus = 'EXECUTABLE' | 'CATALOG_ONLY' | 'BLOCKED' | 'UNSUPPORTED'
export type HealthStatus = 'UNKNOWN' | 'ONLINE' | 'DEGRADED' | 'OFFLINE' | 'QUARANTINED'

export interface GraphNodePosition {
  x: number
  y: number
}

export interface NodeData {
  enabled?: boolean
  label?: string
  config: Record<string, any>
  [key: string]: any
}

export interface GraphNode {
  id: string
  type: string
  position?: GraphNodePosition
  data: NodeData
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  sourceHandle?: string
  targetHandle: string
}

export interface Viewport {
  x: number
  y: number
  zoom: number
}

export interface GraphDocument {
  schema_version: number
  nodes: GraphNode[]
  edges: GraphEdge[]
  viewport?: Viewport
}

export interface AgentSummary {
  agent_id: string
  slug: string
  name: string
  description?: string
  active_revision_id?: string | null
  active_revision_number?: number | null
  row_version: number
  created_at: string
  updated_at: string
}

export interface AgentDetail extends AgentSummary {
  revisions_count: number
  draft?: {
    revision_id: string
    revision_number: number
    graph: GraphDocument
    row_version: number
  } | null
}

export interface RevisionSummary {
  revision_id: string
  agent_id: string
  revision_number: number
  semantic_hash: string
  layout_hash: string
  parent_revision_id?: string | null
  created_by: string
  created_at: string
}

export interface ValidationIssue {
  node_id: string | null
  edge_id: string | null
  code: string
  message: string
}

export interface ValidationReport {
  ok: boolean
  issues: ValidationIssue[]
}

export interface CatalogItem {
  id: string
  name: string
  kind: string
  description?: string
  capability: CapabilityStatus
  health: HealthStatus
  default_config?: Record<string, any>
}

export interface CatalogResponse {
  models: CatalogItem[]
  prompts: CatalogItem[]
  skills: CatalogItem[]
  memory_kinds: CatalogItem[]
  tools: CatalogItem[]
  mcp_servers: CatalogItem[]
  context_defaults?: Record<string, any>
}

export interface ActorSession {
  user_id: string
  email?: string
  role: string
  permissions: string[]
  csrf_token: string
}

export interface RunSummary {
  run_id: string
  agent_id: string
  revision_id?: string | null
  status: 'running' | 'completed' | 'failed' | 'unknown'
  created_at: string
  updated_at: string
}

export interface RunEvent {
  event_id: number
  run_id: string
  agent_id: string
  revision_id?: string
  sequence_number: number
  event_type: string
  payload: Record<string, any>
  at: string
}

export interface ToolEntry {
  tool_name: string
  status: 'running' | 'completed' | 'failed'
  sequence_number: number
  error?: string
  started_at?: string
  completed_at?: string
  duration_ms?: number
}

export interface BudgetSnapshot {
  estimated_context_tokens?: number
  provider_reported_input_tokens?: number
  provider_reported_output_tokens?: number
  cumulative_cost_cents?: number
  latency_ms?: number
}

export interface RunSnapshot {
  run_id: string
  agent_id: string
  revision_id?: string
  status: 'running' | 'completed' | 'failed' | 'unknown'
  active_nodes: string[]
  executed_tools: ToolEntry[]
  budget: BudgetSnapshot
  error?: string | null
  event_count: number
  latest_event_id?: number | null
}

export interface RevisionDetail extends RevisionSummary {
  graph_json: GraphDocument
  draft_comment?: string
  is_active?: boolean
}

export interface RevocationResult {
  revoked: boolean
  agent_id: string
  revoked_revision_id: string
  row_version: number
  runtimes_drained: number
  etag: string
}

export interface ActivationResult {
  activated: boolean
  agent_id: string
  active_revision_id: string
  row_version: number
  etag: string
}

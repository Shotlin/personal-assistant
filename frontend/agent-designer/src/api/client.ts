import type {
  ActorSession,
  AgentDetail,
  AgentSummary,
  CatalogResponse,
  GraphDocument,
  RevisionDetail,
  RevisionSummary,
  RunSnapshot,
  RunSummary,
  ValidationReport,
} from '../types/designer'

const API_BASE = '/designer/api/v1'

let cachedCsrfToken = ''

export function setCsrfToken(token: string): void {
  cachedCsrfToken = token
}

export function getCsrfToken(): string {
  if (cachedCsrfToken) return cachedCsrfToken
  // Try reading from cookie
  const match = document.cookie.match(/designer_csrf=([^;]+)/)
  return match ? decodeURIComponent(match[1]) : ''
}

async function apiRequest<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const headers = new Headers(options.headers || {})
  headers.set('Accept', 'application/json')

  const method = (options.method || 'GET').toUpperCase()
  if (['POST', 'PUT', 'DELETE', 'PATCH'].includes(method)) {
    const csrf = getCsrfToken()
    if (csrf) {
      headers.set('x-csrf-token', csrf)
    }
    if (!headers.has('Content-Type') && options.body) {
      headers.set('Content-Type', 'application/json')
    }
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
    credentials: 'include',
  })

  if (!response.ok) {
    let errorData: any
    try {
      errorData = await response.json()
    } catch {
      errorData = { detail: { error: { message: response.statusText } } }
    }
    const err = new Error(
      errorData?.detail?.error?.message ||
      errorData?.message ||
      `Request failed with status ${response.status}`
    ) as any
    err.status = response.status
    err.data = errorData
    throw err
  }

  return response.json() as Promise<T>
}

export const api = {
  async getSession(): Promise<ActorSession> {
    const data = await apiRequest<{ actor: ActorSession; csrf_token: string }>('/session')
    if (data.csrf_token) {
      setCsrfToken(data.csrf_token)
    }
    return { ...data.actor, csrf_token: data.csrf_token }
  },

  async listAgents(): Promise<AgentSummary[]> {
    const data = await apiRequest<{ agents: AgentSummary[] }>('/agents')
    return data.agents || []
  },

  async getAgent(agentId: string): Promise<AgentDetail> {
    const data = await apiRequest<{ agent: AgentDetail }>(`/agents/${agentId}`)
    return data.agent
  },

  async createAgent(payload: { name: string; description?: string; template?: string }): Promise<AgentDetail> {
    const data = await apiRequest<{ agent: AgentDetail }>('/agents', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    return data.agent
  },

  async listRevisions(agentId: string): Promise<RevisionSummary[]> {
    const data = await apiRequest<{ revisions: RevisionSummary[] }>(`/agents/${agentId}/revisions`)
    return data.revisions || []
  },

  async saveDraft(
    agentId: string,
    graph: GraphDocument,
    expectedRowVersion: number
  ): Promise<{ revision_id: string; row_version: number }> {
    return apiRequest<{ revision_id: string; row_version: number }>(
      `/agents/${agentId}/revisions`,
      {
        method: 'POST',
        headers: {
          'If-Match': `"${expectedRowVersion}"`,
        },
        body: JSON.stringify({ graph }),
      }
    )
  },

  async validateGraph(agentId: string, graph: GraphDocument): Promise<ValidationReport> {
    return apiRequest<ValidationReport>(`/agents/${agentId}/validate`, {
      method: 'POST',
      body: JSON.stringify({ graph }),
    })
  },

  async activateRevision(
    agentId: string,
    revisionId: string,
    expectedRowVersion: number
  ): Promise<{ active_revision_id: string; row_version: number }> {
    return apiRequest<{ active_revision_id: string; row_version: number }>(
      `/agents/${agentId}/activate`,
      {
        method: 'POST',
        headers: {
          'If-Match': `"${expectedRowVersion}"`,
        },
        body: JSON.stringify({ revision_id: revisionId }),
      }
    )
  },

  async revokeAgent(
    agentId: string,
    expectedRowVersion: number
  ): Promise<{ active_revision_id: null; row_version: number }> {
    return apiRequest<{ active_revision_id: null; row_version: number }>(
      `/agents/${agentId}/revoke`,
      {
        method: 'POST',
        headers: {
          'If-Match': `"${expectedRowVersion}"`,
        },
        body: JSON.stringify({}),
      }
    )
  },

  async getCatalog(): Promise<CatalogResponse> {
    return apiRequest<CatalogResponse>('/catalog')
  },

  async previewContext(
    agentId: string,
    graph: GraphDocument
  ): Promise<any> {
    return apiRequest<any>(`/agents/${agentId}/context-preview`, {
      method: 'POST',
      body: JSON.stringify({ graph }),
    })
  },

  async listAgentRuns(agentId: string, limit: number = 20): Promise<RunSummary[]> {
    const data = await apiRequest<{ runs: RunSummary[] }>(`/agents/${agentId}/runs?limit=${limit}`)
    return data.runs || []
  },

  async getRunSnapshot(runId: string): Promise<RunSnapshot> {
    return apiRequest<RunSnapshot>(`/runs/${runId}/snapshot`)
  },

  async getRevision(agentId: string, revisionId: string): Promise<RevisionDetail> {
    return apiRequest<RevisionDetail>(`/agents/${agentId}/revisions/${revisionId}`)
  },
}

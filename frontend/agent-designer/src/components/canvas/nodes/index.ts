import { AgentRootNode } from './AgentRootNode'
import { ModelNode } from './ModelNode'
import { PromptNode } from './PromptNode'
import { SkillNode } from './SkillNode'
import { MemoryNode } from './MemoryNode'
import { ContextNode } from './ContextNode'
import { ConnectorNode } from './ConnectorNode'
import { ToolNode } from './ToolNode'

export const nodeTypes = {
  agent: AgentRootNode,
  model: ModelNode,
  prompt: PromptNode,
  skill: SkillNode,
  memory: MemoryNode,
  context: ContextNode,
  knowledge: SkillNode, // Knowledge renders using skill/source card
  tool: ToolNode,
  mcp: ConnectorNode,
  cua: ConnectorNode,
  terminal: ConnectorNode,
}

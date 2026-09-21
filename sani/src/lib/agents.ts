/**
 * Agents Sani can talk to, mirroring the gateway's registered Agent Designer
 * agents. The gateway currently exposes exactly one (`vion` → "Vion", served
 * as model `personal-assistant-v1`), so this roster is the UI's view of that
 * rather than a second source of truth.
 */
export interface AgentInfo {
  id: string;
  name: string;
  blurb: string;
}

export const AGENTS: AgentInfo[] = [{ id: "vion", name: "Vion", blurb: "Personal assistant" }];

export const ACTIVE_AGENT: AgentInfo = AGENTS[0];

export const OTHER_AGENTS: AgentInfo[] = AGENTS.filter((a) => a.id !== ACTIVE_AGENT.id);

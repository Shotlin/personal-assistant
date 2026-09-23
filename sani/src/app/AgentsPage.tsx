import { useSettings } from "./settings/SettingsContext";

const friendly: Record<string, { description: string; capabilities: string[] }> = {
  velo: { description: "Fast computer-control agent. Velo handles fast desktop actions such as opening applications, navigating interfaces, clicking, typing and interacting with your computer.", capabilities: ["Computer control", "Desktop actions", "Quick execution"] },
  deep: { description: "Reasoning and memory agent. Deep Agent handles deeper reasoning, planning, longer conversations, memory and tasks that benefit from Sani skills.", capabilities: ["Reasoning", "Memory", "Skills"] },
};

export default function AgentsPage() {
  const { snapshot, agents, agentsAvailable, selectAgent, error } = useSettings();
  if (!snapshot) return <section className="section-boundary"><p>Loading agents…</p></section>;
  return <section className="agents-page"><header><h1>Agents</h1><p>Choose the agent for your next turn.</p></header>{error && <p className="settings-error">{error}</p>}{!agentsAvailable && <p className="settings-error">Agent runtime is unavailable. Current selection: {snapshot.agent_mode}.</p>}<div className="agent-cards">{agents.map((agent) => { const copy = friendly[agent.id]; const selected = snapshot.agent_mode === agent.id; return <article className="agent-card" key={agent.id}><div><h2>{agent.name}</h2><p>{copy?.description ?? "Available Sani agent."}</p></div><div className="agent-capabilities">{(copy?.capabilities ?? agent.capabilities).map((capability) => <span key={capability}>{capability}</span>)}</div><button disabled={selected} onClick={() => void selectAgent(agent.id)}>{selected ? "Selected" : "Use for next turn"}</button></article>; })}</div></section>;
}

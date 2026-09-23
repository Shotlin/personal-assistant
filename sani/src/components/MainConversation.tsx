import { useState, type FormEvent } from "react";
import { pressEscape, startListening, stopListening, submitText, type ChatMessage, type UiState } from "../lib/tauri";
import { useSettings } from "../app/settings/SettingsContext";

const labels: Record<UiState, string> = {
  idle: "Ready", preparing: "Preparing voice", listening: "Listening",
  finalizing: "Finalizing", working: "Working", error: "Needs attention",
};

export default function MainConversation({ messages, state, partial }: { messages: ChatMessage[]; state: UiState; partial: string }) {
  const { snapshot, agents, agentsAvailable, selectAgent } = useSettings();
  const [draft, setDraft] = useState("");
  const [sendError, setSendError] = useState("");
  const capturing = state === "listening" || state === "preparing" || state === "finalizing";
  const busy = state === "working" || capturing;
  const send = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!draft.trim() || busy) return;
    setSendError("");
    try { await submitText(draft); setDraft(""); }
    catch (reason) { setSendError(reason instanceof Error ? reason.message : String(reason)); }
  };
  const mic = () => {
    setSendError("");
    if (state === "working") void pressEscape();
    else if (capturing) void stopListening();
    else void startListening();
  };
  return <section className="main-conversation"><header className="conversation-header"><div><h1>Home</h1><p>{labels[state]}</p></div><span className={`state-chip ${state}`}>{labels[state]}</span></header><div className="message-list">{messages.length ? messages.map(message => <article className={`main-message ${message.role}`} key={message.id}><div className="message-meta"><strong>{message.role === "user" ? "You" : message.agent_name || "Sani"}</strong><time>{new Date(message.created_at).toLocaleTimeString([], { hour:"numeric", minute:"2-digit" })}</time></div><p>{message.text}</p></article>) : <div className="empty-history">Your conversation history will appear here.</div>}{capturing && <article className="main-message user muted"><div className="message-meta"><strong>Draft</strong><span>{state === "finalizing" ? "Finishing local decode" : "Listening — not sent"}</span></div>{partial && <p>{partial}</p>}<div className="voice-draft-actions"><button disabled={state === "finalizing"} onClick={() => void stopListening()}>Finish &amp; Send</button><button onClick={() => void pressEscape()}>Cancel</button></div></article>}</div><form className="desktop-composer" aria-label="Conversation composer" onSubmit={send}><select aria-label="Next-turn agent" value={snapshot?.agent_mode ?? ""} disabled={!snapshot || !agentsAvailable || busy} onChange={(event) => void selectAgent(event.target.value)}>{snapshot && !agents.some((agent) => agent.id === snapshot.agent_mode) && <option value={snapshot.agent_mode}>{snapshot.agent_mode} (unavailable)</option>}{agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select><textarea aria-label="Ask Sani something" value={draft} disabled={busy} placeholder={capturing ? "Finish or cancel voice capture first" : "Ask Sani something…"} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(); } }} /><button type="button" onClick={mic} aria-label={state === "working" ? "Stop request" : capturing ? "Finish voice capture" : "Start microphone"}>{state === "working" ? "Stop" : capturing ? "Finish" : "Microphone"}</button><button type="submit" disabled={!draft.trim() || busy}>Send</button>{sendError && <p className="composer-error" role="alert">{sendError}</p>}</form></section>;
}

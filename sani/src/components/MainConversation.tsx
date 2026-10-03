import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  pressEscape,
  startListening,
  stopListening,
  submitText,
  missionControl,
  missionGet,
  missionPurge,
  missionApprove,
  onAgentDone,
  type ChatMessage,
  type MissionStatusValue,
  type MissionPendingApproval,
  type UiState,
} from "../lib/tauri";
import { useSettings } from "../app/settings/SettingsContext";
import MissionStatus from "./MissionStatus";

const labels: Record<UiState, string> = {
  idle: "Ready", preparing: "Preparing voice", listening: "Listening",
  finalizing: "Finalizing", working: "Working", error: "Needs attention",
};

interface MissionState {
  missionId: string;
  status: MissionStatusValue | string;
  verified: boolean;
  planVersion: number;
  controlEpoch: number;
  pendingApprovals: MissionPendingApproval[];
  scope: { scope_hash: string };
}

/**
 * The mission status of the newest mission-backed turn, kept truthful
 * (C08/N10): correlation comes from the PERSISTED message row (history and
 * reopen included) and from live agent-done events, never from a guess, and
 * the read happens in effects — not during render. Controls are wired to
 * the real mission.control IPC with the record's plan version and control
 * epoch, so the renderer cannot forge authority; the host validates the CAS.
 */
function useMissionStatus(messages: ChatMessage[]): {
  mission: MissionState | null;
  refresh: (missionId: string) => void;
} {
  const [mission, setMission] = useState<MissionState | null>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const apply = (record: Awaited<ReturnType<typeof missionGet>>) => {
    setMission({
      missionId: record.mission_id,
      status: record.status,
      verified: record.verified,
      planVersion: record.plan_version,
      controlEpoch: record.control_epoch,
      pendingApprovals: record.pending_approvals ?? [],
      scope: { scope_hash: record.scope?.scope_hash ?? "" },
    });
  };

  // Persisted correlation: the newest message's mission, if any.
  const lastMissionId = messages.length
    ? messages[messages.length - 1].mission_id ?? null
    : null;
  useEffect(() => {
    if (!lastMissionId) return;
    let cancelled = false;
    void missionGet(lastMissionId)
      .then((record) => {
        if (!cancelled && alive.current) apply(record);
      })
      .catch(() => {
        if (!cancelled && alive.current) setMission(null);
      });
    return () => {
      cancelled = true;
    };
  }, [lastMissionId]);

  // Live correlation: a mission-backed terminal event refreshes the truth
  // immediately; a different mission's event never overwrites this view.
  useEffect(() => {
    const unlisten = onAgentDone((done) => {
      if (!done.mission_id || !alive.current) return;
      refreshRef.current(done.mission_id);
    });
    return () => {
      void unlisten.then((off) => off()).catch(() => undefined);
    };
  }, []);

  const refreshRef = useRef((missionId: string) => {
    void missionGet(missionId)
      .then((record) => {
        if (alive.current) apply(record);
      })
      .catch(() => undefined);
  });
  refreshRef.current = (missionId: string) => {
    void missionGet(missionId)
      .then((record) => {
        if (alive.current) apply(record);
      })
      .catch(() => undefined);
  };

  return { mission, refresh: (missionId: string) => refreshRef.current(missionId) };
}

export default function MainConversation({ messages, state, partial }: { messages: ChatMessage[]; state: UiState; partial: string }) {
  const { snapshot, agents, agentsAvailable, selectAgent } = useSettings();
  const [draft, setDraft] = useState("");
  const [sendError, setSendError] = useState("");
  const { mission, refresh } = useMissionStatus(messages);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const capturing = state === "listening" || state === "preparing" || state === "finalizing";
  const busy = state === "working" || capturing;

  /** Real host control: pause/resume/cancel with CAS plan/epoch, then
   * refresh so the buttons reflect the NEW durable state. */
  const onControl = (kind: "PAUSE" | "RESUME" | "CANCEL") => {
    if (!mission) return;
    void missionControl(mission.missionId, kind, mission.planVersion, mission.controlEpoch)
      .then(() => refresh(mission.missionId))
      .catch(() => undefined);
  };

  /** D10: revision and priority ride the same host IPC control path; the
   * core validates the CAS, screens the revision, and bumps the plan. */
  const onRevise = (revision: string) => {
    if (!mission) return;
    void missionControl(mission.missionId, "REVISE", mission.planVersion, mission.controlEpoch,
      "owner revision", { revision_request: revision })
      .then(() => refresh(mission.missionId))
      .catch(() => undefined);
  };

  const onApprove = (pending: MissionPendingApproval) => {
    if (!mission) return;
    setSendError("");
    void missionApprove(mission.missionId, mission.scope?.scope_hash ?? "", pending)
      .then(() =>
        missionControl(mission.missionId, "RESUME", pending.plan_version,
          pending.control_epoch))
      .then(() => refresh(mission.missionId))
      .catch((reason) => setSendError(reason instanceof Error ? reason.message : String(reason)));
  };

  const onReject = (pending: MissionPendingApproval) => {
    if (!mission) return;
    void missionControl(mission.missionId, "CANCEL", pending.plan_version,
      pending.control_epoch, "owner rejected the pending action")
      .then(() => refresh(mission.missionId))
      .catch((reason) => setSendError(reason instanceof Error ? reason.message : String(reason)));
  };

  const onPurge = () => {
    if (!mission) return;
    void missionPurge(mission.missionId, "sani-local")
      .then(() => refresh(mission.missionId))
      .catch((reason) => setSendError(reason instanceof Error ? reason.message : String(reason)));
  };

  const onSetPriority = (priority: number) => {
    if (!mission) return;
    void missionControl(mission.missionId, "SET_PRIORITY", mission.planVersion,
      mission.controlEpoch, "owner priority", { priority })
      .then(() => refresh(mission.missionId))
      .catch(() => undefined);
  };

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
  return <section className="main-conversation"><header className="conversation-header"><div><h1>Home</h1><p>{labels[state]}</p></div><span className={`state-chip ${state}`}>{labels[state]}</span></header><MissionStatus status={mission?.status ?? null} verified={mission?.verified ?? false} missionId={mission?.missionId ?? null} onControl={mission ? onControl : undefined} onRevise={mission ? onRevise : undefined} onSetPriority={mission ? onSetPriority : undefined} onPurge={mission ? onPurge : undefined} pendingApprovals={mission?.pendingApprovals ?? []} onApprove={mission ? onApprove : undefined} onReject={mission ? onReject : undefined} /><div className="message-list">{messages.length ? messages.map(message => <article className={`main-message ${message.role}`} key={message.id}><div className="message-meta"><strong>{message.role === "user" ? "You" : message.agent_name || "Sani"}</strong><time>{new Date(message.created_at).toLocaleTimeString([], { hour:"numeric", minute:"2-digit" })}</time></div><p>{message.text}</p></article>) : <div className="empty-history">Your conversation history will appear here.</div>}{capturing && <article className="main-message user muted"><div className="message-meta"><strong>Draft</strong><span>{state === "finalizing" ? "Finishing local decode" : "Listening — not sent"}</span></div>{partial && <p>{partial}</p>}<div className="voice-draft-actions"><button disabled={state === "finalizing"} onClick={() => void stopListening()}>Finish &amp; Send</button><button onClick={() => void pressEscape()}>Cancel</button></div></article>}</div><form className="desktop-composer" aria-label="Conversation composer" onSubmit={send}><select aria-label="Next-turn agent" value={snapshot?.agent_mode ?? ""} disabled={!snapshot || !agentsAvailable || busy} onChange={(event) => void selectAgent(event.target.value)}>{agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select><textarea aria-label="Ask Sani something" value={draft} disabled={busy} placeholder={capturing ? "Finish or cancel voice capture first" : "Ask Sani something…"} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(); } }} /><button type="button" onClick={mic} aria-label={state === "working" ? "Stop request" : capturing ? "Finish voice capture" : "Start microphone"}>{state === "working" ? "Stop" : capturing ? "Finish" : "Microphone"}</button><button type="submit" disabled={!draft.trim() || busy}>Send</button>{sendError && <p className="composer-error" role="alert">{sendError}</p>}</form></section>;
}

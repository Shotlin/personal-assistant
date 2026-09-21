import { useCallback, useEffect, useRef, useState } from "react";
import {
  hidePanel,
  onActivity,
  onAgentChunk,
  onAgentDone,
  onAgentStart,
  onAgentStatus,
  onConversationChanged,
  onFinal,
  getState,
  onHistoryLoaded,
  onMessage,
  onMicError,
  onPartial,
  onState,
  onSttError,
  getMessages,
  listConversations,
  panelReady,
  type ActivityEvent,
  type AgentChunk,
  type ChatMessage,
  type Conversation,
  type UiState,
} from "../lib/tauri";
import Message from "../components/Message";
import ActivityTimeline from "../components/ActivityTimeline";
import HistoryDrawer from "../components/HistoryDrawer";
import SettingsDrawer from "../components/SettingsDrawer";
import "../styles/tokens.css";
import "../styles/panel.css";

const STATE_TEXT: Record<UiState, string> = {
  idle: "Ready",
  preparing: "Preparing voice…",
  listening: "Listening…",
  finalizing: "Finalizing…",
  working: "Working…",
  error: "Something went wrong",
};

/** Terminal outcome of an agent run — never reported as success by accident (FIX-03). */
const OUTCOME_TEXT: Record<string, string> = {
  cancelled: "Stopped — this run was cancelled. Any partial answer is kept above.",
  interrupted: "Interrupted — the stream ended before the answer finished. Partial text is kept.",
  failed: "The agent could not finish this run.",
};

interface LiveRun {
  runId: string;
  text: string;
  status: string;
  activity: ActivityEvent[];
  done: boolean;
  outcome: string;
  error: string;
}

const EMPTY_RUN: LiveRun = {
  runId: "",
  text: "",
  status: "",
  activity: [],
  done: false,
  outcome: "",
  error: "",
};

export default function PanelApp() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [state, setState] = useState<UiState>("idle");
  const [partial, setPartial] = useState("");
  const [finalText, setFinalText] = useState("");
  const [run, setRun] = useState<LiveRun | null>(null);
  const [agentOnline, setAgentOnline] = useState<boolean | null>(null);
  const [drawer, setDrawer] = useState<"none" | "history" | "settings">("none");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [notice, setNotice] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const atBottomRef = useRef(true);

  const reloadConversation = useCallback(async (conversationId?: string) => {
    if (!conversationId) return;
    const msgs = await getMessages(conversationId);
    setMessages(msgs);
    setRun(null);
    setPartial("");
    setFinalText("");
  }, []);

  const refreshConversations = useCallback(async () => {
    setConversations(await listConversations());
  }, []);

  useEffect(() => {
    const unlistens: Array<() => void> = [];
    const reg = async () => {
      unlistens.push(
        await onState((s) => {
          setState(s);
          if (s === "preparing" || s === "listening") setNotice("");
        }),
        await onPartial((t) => {
          setPartial(t);
          setFinalText("");
        }),
        await onFinal((t) => {
          setPartial("");
          setFinalText(t);
        }),
        await onMessage((m) => {
          setMessages((prev) => [
            ...prev,
            { id: m.id, role: m.role as ChatMessage["role"], text: m.text, created_at: m.created_at },
          ]);
          setFinalText("");
        }),
        await onAgentChunk((c: AgentChunk) => {
          setRun((prev) => {
            const base = prev ?? EMPTY_RUN;
            return c.kind === "text"
              ? { ...base, text: base.text + c.delta }
              : { ...base, status: c.delta };
          });
        }),
        await onAgentStart((s) => {
          setRun((prev) => ({ ...(prev ?? EMPTY_RUN), runId: s.run_id }));
        }),
        await onActivity((a) => {
          setRun((prev) => {
            const base = prev ?? { ...EMPTY_RUN, runId: a.run_id };
            return { ...base, activity: [...base.activity, a] };
          });
        }),
        await onAgentDone((d) => {
          setRun((prev) => ({
            ...(prev ?? EMPTY_RUN),
            done: true,
            outcome: d.status,
            error: d.error,
          }));
          setAgentOnline(d.ok ? true : false);
          if (d.run_id) void refreshConversations();
        }),
        await onHistoryLoaded((m) => {
          setMessages(m);
          setRun(null);
        }),
        await onConversationChanged(() => void refreshConversations()),
        await onAgentStatus((online) => setAgentOnline(online)),
        await onMicError((msg) => setNotice(msg)),
        await onSttError((msg) => setNotice(msg)),
      );
      const current = await getState();
      setState(current.state);
      await panelReady();
      await refreshConversations();
    };
    reg();
    return () => unlistens.forEach((u) => u());
  }, [refreshConversations]);

  // Auto-scroll unless the user scrolled up (spec interaction rule).
  useEffect(() => {
    const el = scrollRef.current;
    if (el && atBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages, partial, finalText, run]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    atBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };

  const openDrawer = async (which: "history" | "settings") => {
    if (drawer === which) {
      setDrawer("none");
      return;
    }
    setDrawer(which);
    if (which === "history") await refreshConversations();
  };

  const statusColor =
    state === "error" ? "var(--error)"
    : state === "working" || state === "finalizing" ? "var(--running)"
    : state === "preparing" ? "var(--warning)"
    : state === "listening" ? "var(--success)"
    : "var(--success)";

  return (
    <div className="panel-root">
      <div className="panel">
        {/* Header */}
        <div className="panel-header">
          <div className="panel-id">
            <span className="dot" style={{ background: statusColor }} />
            <div>
              <div className="panel-title">Sani</div>
              <div className="panel-subtitle">{STATE_TEXT[state]}</div>
            </div>
          </div>
          <div className="panel-header-actions">
            {agentOnline === false && <span className="offline-chip">Agent offline</span>}
            <button className="icon-btn" onClick={() => openDrawer("history")} title="History">
              <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.8">
                <circle cx="12" cy="12" r="9" />
                <path d="M12 7v5l3.5 2" strokeLinecap="round" />
              </svg>
            </button>
            <button className="icon-btn" onClick={() => openDrawer("settings")} title="Settings">
              <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.8">
                <circle cx="12" cy="12" r="3.2" />
                <path d="M19.4 13.5a7.6 7.6 0 0 0 0-3l2-1.5-2-3.4-2.3.9a7.7 7.7 0 0 0-2.6-1.5L14 2.5h-4l-.5 2.5a7.7 7.7 0 0 0-2.6 1.5L4.6 5.6l-2 3.4 2 1.5a7.6 7.6 0 0 0 0 3l-2 1.5 2 3.4 2.3-.9a7.7 7.7 0 0 0 2.6 1.5l.5 2.5h4l.5-2.5a7.7 7.7 0 0 0 2.6-1.5l2.3.9 2-3.4-2-1.5Z" strokeLinejoin="round" />
              </svg>
            </button>
            <button className="icon-btn" onClick={() => void hidePanel()} title="Hide panel">
              <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                <path d="M6 12h12" />
              </svg>
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="panel-body" ref={scrollRef} onScroll={onScroll}>
          {notice && (
            <div className="panel-notice" role="alert">
              {notice}
            </div>
          )}

          {messages.length === 0 && !partial && !finalText && !run && (
            <div className="panel-empty">
              <div className="panel-empty-mark">Sani</div>
              <div>Press your shortcut and start speaking.</div>
            </div>
          )}

          {messages.map((m) => (
            <Message key={m.id} role={m.role} text={m.text} createdAt={m.created_at} />
          ))}

          {partial && <Message role="user" text={partial} muted />}

          {finalText && !messages.some((m) => m.text === finalText) && (
            <Message role="user" text={finalText} />
          )}

          {run && (run.text || run.status || run.activity.length > 0 || run.done) && (
            <>
              {run.text && <Message role="assistant" text={run.text} />}
              <ActivityTimeline events={run.activity} status={run.status} working={!run.done} />
              {run.done && run.outcome !== "completed" && (
                <div className={`run-outcome ${run.outcome}`}>
                  {OUTCOME_TEXT[run.outcome] ?? run.error ?? "Run finished."}
                </div>
              )}
            </>
          )}
        </div>

        {drawer === "history" && (
          <HistoryDrawer
            conversations={conversations}
            onClose={() => setDrawer("none")}
            onRefresh={refreshConversations}
            onSelect={reloadConversation}
          />
        )}
        {drawer === "settings" && <SettingsDrawer onClose={() => setDrawer("none")} />}
      </div>
    </div>
  );
}

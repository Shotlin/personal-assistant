import { create } from "zustand";
import type {
  ActivityEvent,
  AgentChunk,
  AgentDone,
  AgentStart,
  ChatMessage,
  UiState,
} from "@/lib/tauri";
import type { ChatItem, ChatPart, StepPart, StepStatus, TextPart } from "./types";

/**
 * Single source of truth for what the chat draws.
 *
 * Every mutation here is a pure fold of one host event or one persisted row,
 * so replaying history and streaming live produce the same shape. Nothing in
 * this file talks to Tauri; `events.ts` wires the host events to these actions.
 */

interface ChatState {
  items: ChatItem[];
  uiState: UiState;
  /** Live speech-to-text partial while the mic is open. */
  partial: string;
  activeConversationId: string;

  setUiState: (state: UiState) => void;
  setPartial: (text: string) => void;
  setActiveConversation: (id: string) => void;
  /** Replace everything with persisted history (+ optional run activity). */
  loadHistory: (messages: ChatMessage[], activity?: ActivityEvent[]) => void;
  addMessage: (message: ChatMessage) => void;
  startAgent: (start: AgentStart) => void;
  applyChunk: (chunk: AgentChunk) => void;
  applyActivity: (event: ActivityEvent) => void;
  finishAgent: (done: AgentDone) => void;
  reset: () => void;
}

const STEP_STATUSES: readonly StepStatus[] = [
  "running",
  "complete",
  "failed",
  "cancelled",
  "unknown",
  "info",
];

function toStepStatus(value: string): StepStatus {
  return (STEP_STATUSES as readonly string[]).includes(value) ? (value as StepStatus) : "info";
}

export function stepFromActivity(event: ActivityEvent): StepPart {
  return {
    type: "step",
    // A structured step keeps its own id so the finished row replaces the live one.
    id: event.step_id ? `${event.run_id}:${event.step_id}` : `${event.run_id}:${event.sequence}`,
    label: event.label,
    status: toStepStatus(event.status),
    tool: event.tool,
    durationMs: event.duration_ms,
    detail: event.detail,
    timestamp: event.timestamp,
  };
}

function itemFromMessage(message: ChatMessage): ChatItem {
  return {
    id: message.id,
    role: message.role,
    createdAt: message.created_at,
    parts: message.text ? [{ type: "text", text: message.text }] : [],
    agentId: message.agent_id ?? null,
    agentName: message.agent_name ?? null,
    runId: message.run_id ?? null,
    missionId: message.mission_id ?? null,
    status: message.role === "assistant" ? "completed" : undefined,
  };
}

function appendText(parts: ChatPart[], delta: string): ChatPart[] {
  const last = parts[parts.length - 1];
  if (last && last.type === "text") {
    const next: TextPart = { type: "text", text: last.text + delta };
    return [...parts.slice(0, -1), next];
  }
  return [...parts, { type: "text", text: delta }];
}

/** Insert or update a step by id, keeping text where it was. */
function upsertStep(parts: ChatPart[], step: StepPart): ChatPart[] {
  const index = parts.findIndex((part) => part.type === "step" && part.id === step.id);
  if (index >= 0) {
    const next = parts.slice();
    next[index] = step;
    return next;
  }
  // Steps read as a rail above the answer text: insert before the first text.
  const firstText = parts.findIndex((part) => part.type === "text");
  if (firstText >= 0) {
    return [...parts.slice(0, firstText), step, ...parts.slice(firstText)];
  }
  return [...parts, step];
}

function mapItem(items: ChatItem[], id: string, fn: (item: ChatItem) => ChatItem): ChatItem[] {
  const index = items.findIndex((item) => item.id === id);
  if (index < 0) return items;
  const next = items.slice();
  next[index] = fn(items[index]);
  return next;
}

function indexByRun(items: ChatItem[], runId: string): number {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item.role === "assistant" && item.runId === runId) return index;
  }
  return -1;
}

/**
 * The host stream's `message_id` is the id of the USER message that started the
 * turn, so the assistant row being streamed needs its own key. Once the turn is
 * done it is re-keyed to the persisted assistant row id.
 */
const turnKey = (messageId: string) => `turn:${messageId}`;

/** Activity that arrives before its turn is announced waits here by run id. */
const pendingActivity = new Map<string, ActivityEvent[]>();

export const useChatStore = create<ChatState>((set) => ({
  items: [],
  uiState: "idle",
  partial: "",
  activeConversationId: "",

  setUiState: (uiState) => set({ uiState }),
  setPartial: (partial) => set({ partial }),
  setActiveConversation: (activeConversationId) => set({ activeConversationId }),

  loadHistory: (messages, activity = []) => {
    const byRun = new Map<string, StepPart[]>();
    for (const event of activity) {
      const list = byRun.get(event.run_id) ?? [];
      list.push(stepFromActivity(event));
      byRun.set(event.run_id, list);
    }
    const items = messages.map((message) => {
      const item = itemFromMessage(message);
      const steps = item.runId ? byRun.get(item.runId) : undefined;
      if (steps?.length) {
        item.parts = [...steps, ...item.parts];
        item.startedAt = steps[0].timestamp;
        item.endedAt = item.createdAt;
      }
      return item;
    });
    pendingActivity.clear();
    set({ items });
  },

  addMessage: (message) =>
    set((state) =>
      state.items.some((item) => item.id === message.id)
        ? state
        : { items: [...state.items, itemFromMessage(message)] },
    ),

  startAgent: (start) =>
    set((state) => {
      const key = turnKey(start.message_id);
      if (state.items.some((item) => item.id === key)) return state;
      const now = Date.now();
      const buffered = pendingActivity.get(start.run_id) ?? [];
      pendingActivity.delete(start.run_id);
      const item: ChatItem = {
        id: key,
        role: "assistant",
        createdAt: now,
        startedAt: now,
        parts: buffered.map(stepFromActivity),
        agentId: start.agent_id,
        agentName: start.agent_name,
        runId: start.run_id,
        status: "streaming",
      };
      return { items: [...state.items, item] };
    }),

  applyChunk: (chunk) =>
    set((state) => ({
      items: mapItem(state.items, turnKey(chunk.message_id), (item) =>
        chunk.kind === "text"
          ? { ...item, parts: appendText(item.parts, chunk.delta), statusLine: undefined }
          : { ...item, statusLine: chunk.delta },
      ),
    })),

  applyActivity: (event) =>
    set((state) => {
      const index = indexByRun(state.items, event.run_id);
      if (index < 0) {
        const list = pendingActivity.get(event.run_id) ?? [];
        list.push(event);
        pendingActivity.set(event.run_id, list);
        return state;
      }
      const next = state.items.slice();
      next[index] = { ...next[index], parts: upsertStep(next[index].parts, stepFromActivity(event)) };
      return { items: next };
    }),

  finishAgent: (done) =>
    set((state) => {
      const now = Date.now();
      const status =
        done.status === "cancelled" || done.status === "failed" || done.status === "mission_pending"
          ? done.status
          : done.ok
            ? "completed"
            : "failed";
      const key = turnKey(done.message_id);
      let items = mapItem(state.items, key, (item) => {
        // The persisted row is authoritative: it replaces whatever was streamed.
        const withoutText = item.parts.filter((part) => part.type !== "text");
        const parts: ChatPart[] = done.text.trim()
          ? [...withoutText, { type: "text", text: done.text }]
          : item.parts;
        return {
          ...item,
          id: done.assistant_message_id || item.id,
          parts: parts.map((part) =>
            part.type === "step" && part.status === "running"
              ? { ...part, status: (status === "completed" ? "complete" : "cancelled") as StepStatus }
              : part,
          ),
          status,
          error: done.error || undefined,
          statusLine: undefined,
          missionId: done.mission_id || item.missionId || null,
          endedAt: now,
        };
      });
      // A turn that produced text but never announced itself (for example a
      // restart that lost the start event) still lands as a row.
      if (
        done.assistant_message_id &&
        done.text.trim() &&
        !items.some((item) => item.id === key || item.id === done.assistant_message_id)
      ) {
        items = [
          ...items,
          {
            id: done.assistant_message_id,
            role: "assistant",
            createdAt: done.created_at,
            parts: [{ type: "text", text: done.text }],
            agentId: done.agent_id || null,
            agentName: done.agent_name || null,
            runId: done.run_id || null,
            missionId: done.mission_id || null,
            status,
            error: done.error || undefined,
          },
        ];
      }
      return { items };
    }),

  reset: () => {
    pendingActivity.clear();
    set({ items: [], partial: "" });
  },
}));

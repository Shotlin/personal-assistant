/**
 * The chat model the main window renders.
 *
 * One assistant turn is a list of typed parts (text, reasoning, step). Host
 * events (`sani://agent-chunk`, `sani://activity`, ...) and persisted history
 * rows are both folded into this shape by `store.ts`, so the UI has a single
 * thing to draw whether a turn is live or reopened from disk.
 */

export type StepStatus = "running" | "complete" | "failed" | "cancelled" | "unknown" | "info";

export interface StepPart {
  type: "step";
  /** Stable within a message (host activity sequence, or a local counter). */
  id: string;
  /** Sentence-first label: "Opened Chrome", "Ran npm test". */
  label: string;
  status: StepStatus;
  /** Optional tool name, used only to choose the leading icon. */
  tool?: string;
  durationMs?: number;
  /** Raw detail, shown only behind the quiet "Technical details" affordance. */
  detail?: string;
  timestamp: number;
  /**
   * How the step reads inside a Claude Code round: `round` is the header (why
   * Sani called it), `prompt` is what Sani asked, `reply` is what came back,
   * `note` is a supervisor remark. Plain tool steps have no kind.
   */
  kind?: "round" | "prompt" | "reply" | "note";
  /** Ties one round's steps together. */
  group?: string;
}

export interface TextPart {
  type: "text";
  text: string;
}

export interface ReasoningPart {
  type: "reasoning";
  text: string;
}

export type ChatPart = TextPart | ReasoningPart | StepPart;

export type TurnStatus = "streaming" | "completed" | "cancelled" | "failed" | "mission_pending";

export interface ChatItem {
  id: string;
  role: "user" | "assistant";
  createdAt: number;
  parts: ChatPart[];
  agentId?: string | null;
  agentName?: string | null;
  runId?: string | null;
  missionId?: string | null;
  /** Assistant turns only. Persisted history rows are always "completed". */
  status?: TurnStatus;
  /** Set when a turn ended without a normal reply. */
  error?: string;
  /** Transient one-line status from the running agent ("Opening Chrome…"). */
  statusLine?: string;
  startedAt?: number;
  endedAt?: number;
}

/** The text of a message, ignoring steps. */
export function itemText(item: ChatItem): string {
  return item.parts
    .filter((part): part is TextPart => part.type === "text")
    .map((part) => part.text)
    .join("");
}

export function itemSteps(item: ChatItem): StepPart[] {
  return item.parts.filter((part): part is StepPart => part.type === "step");
}

import { invoke } from "@tauri-apps/api/core";
import { emit, listen } from "@tauri-apps/api/event";

export type UiState = "idle" | "preparing" | "listening" | "finalizing" | "working" | "error";

/** Which registered agent produced a message; null on user messages and on
 *  history written before attribution existed. */
export interface ChatMessage {
  id: string;
  conversation_id?: string;
  role: "user" | "assistant";
  text: string;
  created_at: number;
  run_id?: string | null;
  agent_id?: string | null;
  agent_name?: string | null;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
}

export interface ActivityEvent {
  sequence: number;
  run_id: string;
  agent_id?: string;
  event_type: string;
  timestamp: number;
  label: string;
  status: string;
  tool?: string;
  duration_ms?: number;
  detail?: string;
}

export interface AgentChunk {
  message_id: string;
  kind: "text" | "status";
  delta: string;
  agent_id?: string;
}

export interface AgentDone {
  message_id: string;
  run_id: string;
  ok: boolean;
  /** "completed" | "cancelled" | "failed" (FIX-03). */
  status: string;
  error: string;
  agent_id: string;
  agent_name: string;
}

export interface AgentStart {
  message_id: string;
  run_id: string;
  agent_id: string;
  agent_name: string;
}

/** An agent descriptor as the Sani Core registry reports it. The frontend may
 *  cache this, but it must never invent who exists. */
export interface AgentDescriptor {
  id: string;
  name: string;
  capabilities: string[];
}

/** One raw sani-core event frame, relayed verbatim from the sidecar. */
export interface CoreEvent {
  type: "event";
  run_id: string;
  agent_id: string;
  kind: string;
  data: Record<string, unknown>;
}

/** macOS microphone authorization state (RC-04). */
export type MicPermission =
  | "not_determined"
  | "restricted"
  | "denied"
  | "granted"
  | "unknown";

export interface SettingsShape {
  hotkey: string;
  mic_device: string;
  launch_at_login: boolean;
  theme: string;
  stt_model: string;
  stt_ready: boolean;
  /** "auto" or a registered agent id. */
  agent_mode: string;
}

// ---------------------------------------------------------------- events

export const onState = (cb: (s: UiState) => void) =>
  listen<UiState>("sani://state", (e) => cb(e.payload));
export const onPartial = (cb: (text: string) => void) =>
  listen<string>("sani://partial", (e) => cb(e.payload));
export const onFinal = (cb: (text: string) => void) =>
  listen<string>("sani://final", (e) => cb(e.payload));
export const onLevel = (cb: (levels: number[]) => void) =>
  listen<number[]>("sani://level", (e) => cb(e.payload));
export const onAgentChunk = (cb: (c: AgentChunk) => void) =>
  listen<AgentChunk>("sani://agent-chunk", (e) => cb(e.payload));
export const onAgentDone = (cb: (d: AgentDone) => void) =>
  listen<AgentDone>("sani://agent-done", (e) => cb(e.payload));
export const onAgentStart = (cb: (s: AgentStart) => void) =>
  listen<AgentStart>("sani://agent-start", (e) => cb(e.payload));
export const onActivity = (cb: (a: ActivityEvent) => void) =>
  listen<ActivityEvent>("sani://activity", (e) => cb(e.payload));
export const onHistoryLoaded = (cb: (m: ChatMessage[]) => void) =>
  listen<ChatMessage[]>("sani://history-loaded", (e) => cb(e.payload));
export const onMessage = (
  cb: (m: { id: string; role: string; text: string; created_at: number }) => void,
) => listen("sani://message", (e) => cb(e.payload as never));
export const onConversationChanged = (cb: (id: string) => void) =>
  listen<string>("sani://conversation-changed", (e) => cb(e.payload));
export const onSttStatus = (cb: (s: unknown) => void) =>
  listen("sani://stt-status", (e) => cb(e.payload));
export const onSttError = (cb: (s: string) => void) =>
  listen<string>("sani://stt-error", (e) => cb(e.payload));
export const onMicError = (cb: (s: string) => void) =>
  listen<string>("sani://mic-error", (e) => cb(e.payload));
export const onMicPermission = (cb: (s: MicPermission) => void) =>
  listen<MicPermission>("sani://mic-permission", (e) => cb(e.payload));
/** Whether the assistant runtime (Sani's own sidecar) is reachable. */
export const onAgentStatus = (cb: (online: boolean) => void) =>
  listen<boolean>("sani://agent-status", (e) => cb(e.payload));
/** Every raw sani-core event frame, for agent-attributed UI. */
export const onCoreEvent = (cb: (e: CoreEvent) => void) =>
  listen<CoreEvent>("sani://core-event", (e) => cb(e.payload));

// -------------------------------------------------------------- commands

export const getState = () => invoke<{ state: UiState; stt_ready: boolean; stt_model: string; partial: string; mic_permission: MicPermission }>("get_state");
export const getSettings = () => invoke<SettingsShape>("get_settings");
export const saveSettings = (patch: {
  hotkey?: string;
  mic_device?: string;
  launch_at_login?: boolean;
  theme?: string;
  agent_mode?: string;
}) => invoke<void>("save_settings_cmd", patch);
export const listMics = () => invoke<string[]>("list_mics");
export const startListening = () => invoke<void>("start_listening_cmd");
export const stopListening = () => invoke<void>("stop_listening_cmd");
export const pressEscape = () => invoke<void>("escape_cmd");
export const listConversations = () => invoke<Conversation[]>("list_conversations");
export const getMessages = (conversationId: string) =>
  invoke<ChatMessage[]>("get_messages", { conversationId });
export const newConversation = () => invoke<string>("new_conversation");
export const selectConversation = (conversationId: string) =>
  invoke<void>("select_conversation", { conversationId });
export const deleteConversation = (conversationId: string) =>
  invoke<void>("delete_conversation", { conversationId });
export const panelReady = () => invoke<void>("panel_ready");

/** The live agent registry. This is the only source of who exists. */
export const coreAgents = () =>
  invoke<{ agents: AgentDescriptor[] }>("core_agents").then((r) => r.agents);
/** The sidecar's own subsystem report, for Diagnostics. */
export const coreStatus = () => invoke<Record<string, unknown>>("core_status");

/** Hide the panel without destroying it (RC-02). Reopen is instant. */
export const hidePanel = () => invoke<void>("hide_panel");
/** Show the panel if hidden, hide it if visible. */
export const togglePanel = () => invoke<void>("toggle_panel");

/** Cross-overlay UI intent: the pill can ask the panel to open a drawer. */
export type UiCommand = "settings" | "history";
export const sendUiCommand = (command: UiCommand) => emit<UiCommand>("sani://ui-command", command);
export const onUiCommand = (cb: (command: UiCommand) => void) =>
  listen<UiCommand>("sani://ui-command", (e) => cb(e.payload));

/** Current macOS microphone authorization state; never triggers a prompt. */
export const micPermissionState = () => invoke<MicPermission>("mic_permission_state");
/** Kick the system prompt when undetermined; poll micPermissionState for the answer. */
export const requestMicPermission = () => invoke<MicPermission>("request_mic_permission");
/** System Settings › Privacy & Security › Microphone. */
export const openMicSettings = () => invoke<void>("open_mic_settings");

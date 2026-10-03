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
  /** C08/N10: persisted mission correlation from the host history store. */
  mission_id?: string | null;
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
  /** Stable id of the step, so a finished row replaces its live "running" one. */
  step_id?: string | null;
  kind?: "round" | "prompt" | "reply" | "note" | null;
  group?: string | null;
}
export interface TimingRecord { run_id: string; stage: string; elapsed_ms: number; status: string; }

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
  /** "completed" | "cancelled" | "failed" | "mission_pending" (FIX-03 + RF-07). */
  status: string;
  error: string;
  agent_id: string;
  agent_name: string;
  /** Persisted terminal assistant row; empty when the run produced no text. */
  assistant_message_id: string;
  text: string;
  created_at: number;
  /** C08/N10: the durable mission this turn belongs to, when mission-backed. */
  mission_id?: string;
  /** The mission status string straight from the core result, when present. */
  mission_status?: string;
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

/** Sidecar-supported voice model together with native cache/selection state. */
export interface VoiceModelDescriptor {
  id: string;
  installed: boolean;
  active: boolean;
}
/** macOS grants are per process: Sani and the CuaDriver helper each need their
 *  own, so the page reports both rather than one merged "ready". */
export interface ComputerControlSnapshot { status: "ready" | "restart_required" | "permission_required" | "driver_missing" | "driver_stopped" | "wrong_mode" | "driver_permission_required" | "policy_locked" | "unavailable"; message: string; accessibility: string; screen_recording: string; /** Whose grants these are, and why the driver shares them. */ permission_authority: string; driver_running: boolean; driver_pid: number | null; driver_endpoint: string; driver_mode: string; /** granted | denied | policy_locked | unanswered | unreachable | unrecognized */ driver_probe: string; /** The driver's own words, whenever the probe could not answer. */ driver_detail: string; /** Live driver sessions; null means the question could not be asked. */ active_sessions: number | null; restart_required: boolean; runtime: string; app_path: string; }

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

export type ApplyStatus = "saved" | "applying" | "ready" | "failed_to_apply";
export type KeyPresence = "absent" | "stored";
export type KeyValidation = "absent" | "connected" | "invalid" | "offline";

/** Native, non-secret settings authority shared by the independent main and
 * overlay WebViews. Neither renderer persists this object itself. */
export interface FullSettingsSnapshot extends SettingsShape {
  version: number;
  reasoning_provider: "openrouter" | string;
  reasoning_model: string;
  openrouter_key: KeyPresence;
  runtime_status: ApplyStatus;
  microphone_permission: MicPermission;
  accessibility_permission: string;
  screen_recording_permission: string;
  storage_path: string;
  technical_retention_days: number;
  claude_code_enabled: boolean;
  claude_code_dirs: string[];
  claude_code_permission: ClaudeCodePermission;
}

// ------------------------------------------------------- overlay layout editor

/** Normalized top-left position plus a logical-point size. The mixed units are
 *  deliberate: position follows a work area that changes shape, while a 520pt
 *  panel stays 520pt on any display. Never physical pixels. */
export interface OverlayFrame {
  x_ratio: number;
  y_ratio: number;
  width: number;
  height: number;
}

export interface OverlayLayout {
  /** Empty means "no preference", which native resolves to the main window's display. */
  display_affinity: string;
  pill: OverlayFrame;
  panel: OverlayFrame;
}

/** Logical points relative to the top-left of a usable work area. */
export interface LogicalRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** One display as native sees it: the whole screen, and the usable work area
 *  inside it. The gap between them is the real menu bar/notch and Dock. */
export interface DisplaySnapshot {
  id: string;
  is_primary: boolean;
  is_main_window_display: boolean;
  scale_factor: number;
  screen_width: number;
  screen_height: number;
  work_x: number;
  work_y: number;
  work_width: number;
  work_height: number;
}

/** The ranges native enforces, so the editor constrains handles to the same numbers. */
export interface OverlayLimits {
  pill_min_width: number;
  pill_max_width: number;
  pill_height: number;
  panel_min_width: number;
  panel_max_width: number;
  panel_min_height: number;
  panel_max_height: number;
}

export interface OverlayEditorState {
  /** null when native has no display snapshot: Preview and Save must be disabled. */
  active_display: string | null;
  displays: DisplaySnapshot[];
  committed: OverlayLayout;
  /** The provisional layout currently applied to the real overlays, if previewing. */
  draft: OverlayLayout | null;
  /** What native actually resolved — authoritative, never the raw draft. */
  pill: LogicalRect;
  panel: LogicalRect;
  adjustments: string[];
  preview_active: boolean;
  limits: OverlayLimits;
}

// ------------------------------------------------------- Claude Code companion

export type ClaudeCodePermission = "read" | "edit" | "run";

export interface ClaudeCodeRate {
  status?: string;
  resets_at?: number | null;
  used_percent?: number | null;
  seen_at?: number;
}

/** What Sani knows about the user's own Claude Code. No secrets, no API key. */
export interface ClaudeCodeLogin {
  state: "idle" | "waiting" | "succeeded" | "failed" | "cancelled";
  /** Anthropic's sign-in page, once Claude Code has printed it. */
  url: string;
  message: string;
}

export interface ClaudeCodeStatus {
  login?: ClaudeCodeLogin;
  enabled: boolean;
  permission: ClaudeCodePermission;
  folders: string[];
  claude: {
    installed: boolean;
    path: string;
    version: string;
    signed_in: boolean;
    auth_method: string;
    detail: string;
  };
  usage: {
    rates: Record<string, ClaudeCodeRate>;
    last_run: {
      conversation?: string;
      session_id?: string;
      context_tokens?: number | null;
      context_window?: number | null;
      cost_usd?: number | null;
      turns?: number;
      at?: number;
    };
    context_percent: number | null;
  };
}

export const claudeCodeStatus = () => invoke<ClaudeCodeStatus>("claude_code_status_cmd");
export const claudeCodeAuth = (action: "login" | "code" | "cancel" | "logout", code?: string) =>
  invoke<ClaudeCodeStatus & { error?: string }>("claude_code_auth_cmd", { action, code });
export const openSignInLink = (url: string) => invoke<void>("open_sign_in_link_cmd", { url });
export const focusMain = () => invoke<void>("focus_main_cmd");
export const applyClaudeCodeSettings = (patch: {
  enabled?: boolean;
  dirs?: string[];
  permission?: ClaudeCodePermission;
}) => invoke<FullSettingsSnapshot>("apply_claude_code_settings", { patch });
/** Save a file the user attached; returns its absolute path under Sani's data folder. */
export const saveAttachment = (name: string, dataBase64: string) =>
  invoke<string>("save_attachment_cmd", { name, dataBase64 });
/** Native folder chooser; null when the user cancels. */
export const pickFolder = () => invoke<string | null>("pick_folder_cmd");

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
  cb: (m: ChatMessage) => void,
) => listen<ChatMessage>("sani://message", (e) => cb(e.payload));
export const onConversationChanged = (cb: (id: string) => void) =>
  listen<string>("sani://conversation-changed", (e) => cb(e.payload));
export const onSttStatus = (cb: (s: unknown) => void) =>
  listen("sani://stt-status", (e) => cb(e.payload));
export const onSttError = (cb: (s: string) => void) =>
  listen<string>("sani://stt-error", (e) => cb(e.payload));
export const onVoiceLimit = (cb: (s: string) => void) =>
  listen<string>("sani://voice-limit", (e) => cb(e.payload));
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

export const getState = () => invoke<{ state: UiState; stt_ready: boolean; stt_model: string; partial: string; mic_permission: MicPermission; active_conversation_id: string }>("get_state");
export const getSettings = () => invoke<SettingsShape>("get_settings");
export const voiceModels = () => invoke<VoiceModelDescriptor[]>("voice_models");
export const installVoiceModel = (model: string) =>
  invoke<VoiceModelDescriptor>("install_voice_model", { model });
export const useVoiceModel = (model: string) =>
  invoke<VoiceModelDescriptor>("use_voice_model", { model });
export const removeVoiceModel = (model: string) =>
  invoke<void>("remove_voice_model", { model });
export const onVoiceModelProgress = (cb: (update: { model: string; progress: number }) => void) =>
  listen<{ model: string; progress: number }>("sani://voice-model-progress", (e) => cb(e.payload));
export const getFullSettings = () => invoke<FullSettingsSnapshot>("get_full_settings");
export const applyAiSettings = (patch: {
  reasoning_provider?: "openrouter";
  reasoning_model?: string;
}) => invoke<FullSettingsSnapshot>("apply_ai_settings", { patch });
export const storeProviderKey = (provider: "openrouter", key: string) =>
  invoke<{ stored: boolean; has_openrouter: boolean; runtime_status: ApplyStatus }>("store_provider_key", { provider, key });
export const validateStoredProviderKey = (provider: "openrouter") =>
  invoke<KeyValidation>("validate_stored_provider_key", { provider });
export const onSettingsChanged = (cb: (snapshot: FullSettingsSnapshot) => void) =>
  listen<FullSettingsSnapshot>("settings://changed", (e) => cb(e.payload));
export const saveSettings = (patch: {
  hotkey?: string;
  mic_device?: string;
  launch_at_login?: boolean;
  theme?: string;
  agent_mode?: string;
}) => invoke<void>("save_settings_cmd", patch);
export const setAgentMode = (agentMode: string) => invoke<void>("set_agent_mode", { agentMode });
export const listMics = () => invoke<string[]>("list_mics");
export const startListening = () => invoke<void>("start_listening_cmd");
export const stopListening = () => invoke<void>("stop_listening_cmd");
export const pressEscape = () => invoke<void>("escape_cmd");
/** Typed and finalized-speech requests share the native admission gate. */
export const submitText = (text: string) => invoke<void>("submit_text_cmd", { text });
export const listConversations = () => invoke<Conversation[]>("list_conversations");
export const getMessages = (conversationId: string) =>
  invoke<ChatMessage[]>("get_messages", { conversationId });
export const getRunActivity = (conversationId: string) =>
  invoke<ActivityEvent[]>("get_run_activity", { conversationId });
export const recentRunTiming = () => invoke<TimingRecord[]>("recent_run_timing");
export const pruneRunActivity = (days: 7 | 14 | 30) => invoke<number>("prune_run_activity", { days });
export const clearRunActivity = () => invoke<number>("clear_run_activity");
export const setTechnicalRetention = (days: 7 | 14 | 30) => invoke<number>("set_technical_retention", { days });
export const newConversation = () => invoke<string>("new_conversation");
export const selectConversation = (conversationId: string) =>
  invoke<void>("select_conversation", { conversationId });
export const deleteConversation = (conversationId: string) =>
  invoke<void>("delete_conversation", { conversationId });
export const panelReady = () => invoke<void>("panel_ready");
export const mainReady = () => invoke<void>("main_ready");

/** The live agent registry. This is the only source of who exists. */
export const coreAgents = () =>
  invoke<{ agents: AgentDescriptor[] }>("core_agents").then((r) => r.agents);
/** The sidecar's own subsystem report, for Diagnostics. */
export const coreStatus = () => invoke<Record<string, unknown>>("core_status");
export const computerControlSnapshot = () => invoke<ComputerControlSnapshot>("computer_control_snapshot");
/** Poke from Sani's driver watchdog: pull a fresh snapshot, nothing more. */
export const onComputerControlChange = (cb: () => void) =>
  listen("sani://computer-control", () => cb());
/** Adds Sani to the macOS list so the toggle exists. Never grants anything. */
export const requestAccessibility = () => invoke<string>("request_accessibility");
/** Raises the Screen Recording prompt. Only the user can complete it. */
export const requestScreenRecording = () => invoke<string>("request_screen_recording");
export const openPermissionSettings = (pane: "accessibility" | "screen_recording") =>
  invoke<void>("open_permission_settings", { pane });
export const restartSani = () => invoke<void>("restart_app");

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
export const micPermissionState = () => invoke<MicPermission>("mic_permission_state");/** Kick the system prompt when undetermined; poll micPermissionState for the answer. */
export const requestMicPermission = () => invoke<MicPermission>("request_mic_permission");
/** System Settings › Privacy & Security › Microphone. */
export const openMicSettings = () => invoke<void>("open_mic_settings");

// --------------------------------------------------- overlay layout commands

/** Display metadata, committed layout, and the frames native resolved. */
export const overlayEditorState = () => invoke<OverlayEditorState>("overlay_editor_state");
/** Apply a draft to the real overlays. Process-local: never writes settings. */
export const previewOverlayLayout = (draft: OverlayLayout) =>
  invoke<OverlayEditorState>("preview_overlay_layout", { draft });
/** Persist a validated draft, apply it, and restore prior overlay visibility. */
export const saveOverlayLayout = (draft: OverlayLayout) =>
  invoke<OverlayEditorState>("save_overlay_layout", { draft });
/** Back to committed placement and the exact visibility captured before preview. */
export const cancelOverlayPreview = () => invoke<OverlayEditorState>("cancel_overlay_preview");
/** The native defaults Reset puts into the draft. Writes nothing, moves nothing. */
export const resetOverlayDraft = () => invoke<OverlayLayout>("reset_overlay_draft");

// ---------------------------------------------------------------- missions
// Jarvis Phase 1 (R08): truthful mission status and owner controls.

export type MissionStatusValue =
  | "PLANNED"
  | "RUNNING"
  | "WAITING_EXTERNAL"
  | "BLOCKED"
  | "NEEDS_APPROVAL"
  | "PAUSED"
  | "VERIFYING"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED"
  | "UNKNOWN";

export interface MissionEventFrame {
  sequence: number;
  event_id: string;
  mission_id: string;
  kind: string;
  safe_payload: Record<string, unknown>;
  occurred_at_ms: number;
}

/** A mission control (pause/resume/cancel/revise/priority) — the host
 * validates Compare-and-Swap expectations; the renderer cannot forge
 * authority. */
export const missionControl = (
  missionId: string,
  kind: "PAUSE" | "RESUME" | "CANCEL" | "REVISE" | "SET_PRIORITY",
  expectedPlanVersion: number,
  expectedControlEpoch: number,
  reason = "",
  extras: { revision_request?: string; priority?: number } = {},
) =>
  invoke<{ mission_id: string; status: MissionStatusValue }>("mission_control_cmd", {
    method: "mission.control",
    params: {
      control_id: crypto.randomUUID(),
      mission_id: missionId,
      expected_plan_version: expectedPlanVersion,
      expected_control_epoch: expectedControlEpoch,
      kind,
      reason,
      ...(extras.revision_request !== undefined ? { revision_request: extras.revision_request } : {}),
      ...(extras.priority !== undefined ? { priority: extras.priority } : {}),
    },
  });

/** D10: the owner approves the EXACT pending action (digest, plan version,
 * epoch). The host mints provenance; the renderer only supplies the
 * decision. */
export const missionApprove = (
  missionId: string, scopeHash: string, pending: MissionPendingApproval,
) =>
  invoke<{ approval_id: string }>("mission_control_cmd", {
    method: "mission.approve",
    params: {
      approval_id: crypto.randomUUID(),
      mission_id: missionId,
      plan_version: pending.plan_version,
      control_epoch: pending.control_epoch,
      action_digest: pending.action_digest,
      scope_hash: scopeHash,
      target_ref: pending.target_ref,
      account_ref: pending.account_ref,
      workspace_ref: pending.workspace_ref,
      effect_class: pending.effect_class,
      issued_at_ms: Date.now(),
      expires_at_ms: Date.now() + 300_000,
    },
  }).then((result) => result as { approval_id: string });

/** D10: the exact per-step approval the owner owes, from durable state. */
export interface MissionPendingApproval {
  step_id: string;
  tool: string;
  action_digest: string;
  plan_version: number;
  control_epoch: number;
  target_ref: string | null;
  account_ref: string | null;
  workspace_ref: string | null;
  effect_class: "READ_ONLY" | "REPEATABLE_LOCAL" | "EXTERNAL_WRITE" | "DESTRUCTIVE";
}

/** Read one mission's durable record (status truth for the UI). */
export const missionGet = (missionId: string) =>
  invoke<{ mission_id: string; status: MissionStatusValue; plan_version: number; control_epoch: number; verified: boolean; pending_approvals: MissionPendingApproval[]; scope: { scope_hash: string; allowed_apps: string[] }; owner_id: string }>(
    "mission_get_cmd",
    { missionId },
  );

/** D12/D16: owner deletion of one mission's derivative records. The core
 * verifies mission/owner identity before anything is touched. */
export const missionPurge = (missionId: string, ownerId: string) =>
  invoke<{ purged: boolean; reason?: string; evidence_files_deleted: number; recommendations_deleted: string[] }>(
    "mission_control_cmd",
    { method: "mission.purge", params: { mission_id: missionId, owner_id: ownerId } },
  );

/** D16: durable owner hold control; expiry workers read this same record. */
export const missionRetentionHold = (
  missionId: string, ownerId: string, held: boolean, reason = "owner retention control",
) => invoke<{ mission_id: string; held: boolean }>("mission_control_cmd", {
  method: "mission.retention_hold",
  params: { mission_id: missionId, owner_id: ownerId, held, reason },
});

/** Replay mission events after a reconnect cursor. */
export const missionEvents = (missionId: string, afterSequence: number) =>
  invoke<{ events: MissionEventFrame[]; cursor: number; chain_ok: boolean }>("mission_events_cmd", {
    missionId,
    afterSequence,
  });

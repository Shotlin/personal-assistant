/**
 * Dev-only stand-in for Tauri's JS bridge, so the REAL main window can run in a
 * plain browser against fixture data (`npm run dev`, then `app.html?preview`).
 *
 * It exists to look at the UI: every state of the chat, settings and consent
 * card can be reached without the Rust host, the sidecar, or any credentials.
 * It is only imported behind `import.meta.env.DEV`, so it never ships.
 *
 * Scenarios (`?preview&scenario=...`): `empty`, `history` (default), `approval`.
 */
type Json = Record<string, unknown>;

const now = Date.now();
const MIN = 60_000;

const conversations = [
  { id: "c1", title: "Open Chrome and search WhatsApp Web", created_at: now - 3 * MIN, updated_at: now - 2 * MIN },
  { id: "c2", title: "Summarise the README for the team", created_at: now - 26 * 3_600_000, updated_at: now - 25 * 3_600_000 },
  { id: "c3", title: "Plan the Sani UI redesign", created_at: now - 4 * 86_400_000, updated_at: now - 4 * 86_400_000 },
];

const historyMessages = [
  { id: "m1", conversation_id: "c1", role: "user", text: "Open Chrome and search WhatsApp Web", created_at: now - 3 * MIN },
  {
    id: "m2",
    conversation_id: "c1",
    role: "assistant",
    run_id: "r1",
    agent_id: "velo",
    agent_name: "Velo",
    created_at: now - 2 * MIN,
    text:
      "Chrome is open on **web.whatsapp.com**.\n\nThe QR code is showing, so you'll need to scan it from your phone before chats load.\n\n- Open WhatsApp on your phone\n- Go to **Linked devices**\n- Tap **Link a device** and scan the code",
  },
];

const historyActivity = [
  { sequence: 1, run_id: "r1", agent_id: "velo", event_type: "agent.progress", timestamp: now - 3 * MIN + 1000, label: "Opened Google Chrome", status: "complete", tool: "chrome", duration_ms: 1400 },
  { sequence: 2, run_id: "r1", agent_id: "velo", event_type: "agent.progress", timestamp: now - 3 * MIN + 3500, label: "Searched for WhatsApp Web", status: "complete", tool: "chrome", duration_ms: 2100 },
  { sequence: 3, run_id: "r1", agent_id: "velo", event_type: "agent.progress", timestamp: now - 3 * MIN + 7000, label: "Clicked the first result", status: "complete", tool: "click", duration_ms: 900 },
  {
    sequence: 4,
    run_id: "r1",
    agent_id: "velo",
    event_type: "agent.progress",
    timestamp: now - 3 * MIN + 9000,
    label: "Checked the page loaded",
    status: "complete",
    duration_ms: 600,
    detail: '{"title":"WhatsApp Web","url":"https://web.whatsapp.com/","verified":true}',
  },
];

const settings = {
  hotkey: "Alt+Space",
  mic_device: "",
  launch_at_login: false,
  theme: "light",
  stt_model: "base-en",
  stt_ready: true,
  agent_mode: "auto",
  version: 1,
  reasoning_provider: "openrouter",
  reasoning_model: "anthropic/claude-sonnet-4.5",
  openrouter_key: "stored",
  runtime_status: "ready",
  microphone_permission: "granted",
  accessibility_permission: "granted",
  screen_recording_permission: "granted",
  storage_path: "/Users/you/Library/Application Support/Sani",
  technical_retention_days: 14,
};

const control = {
  status: "ready",
  message: "Sani can see and control the apps it is allowed to use.",
  accessibility: "granted",
  screen_recording: "granted",
  permission_authority: "Sani.app, the embedded driver shares these grants",
  driver_running: true,
  driver_pid: 4242,
  driver_endpoint: "/Users/you/Library/Application Support/Sani/cua/driver.sock",
  driver_mode: "bounded",
  driver_probe: "granted",
  driver_detail: "",
  active_sessions: 0,
  restart_required: false,
  runtime: "ready",
  app_path: "/Applications/Sani.app",
};

const approvalMission = {
  mission_id: "mis-1",
  status: "NEEDS_APPROVAL",
  plan_version: 2,
  control_epoch: 1,
  verified: false,
  owner_id: "sani-local",
  scope: { scope_hash: "abc", allowed_apps: ["Google Chrome"] },
  pending_approvals: [
    {
      step_id: "s1",
      tool: "Send message in WhatsApp",
      action_digest: "9f2c41d7a0b83e55c1d2e6f7a8b9c0d1e2f3a4b5c6d7e8f90a1b2c3d4e5f6071",
      plan_version: 2,
      control_epoch: 1,
      target_ref: "Priya",
      account_ref: null,
      workspace_ref: null,
      effect_class: "EXTERNAL_WRITE",
    },
  ],
};

export function install(scenario: string): void {
  const w = window as unknown as Json & {
    __TAURI_INTERNALS__?: unknown;
    __TAURI_EVENT_PLUGIN_INTERNALS__?: unknown;
  };
  const callbacks = new Map<number, (e: unknown) => void>();
  const listeners = new Map<string, Map<number, number>>();
  let nextId = 1;
  let uiState = "idle";
  let activeConversation = scenario === "empty" ? "" : "c1";
  const sent: Array<{ id: string; text: string }> = [];

  const emit = (event: string, payload: unknown) => {
    const map = listeners.get(event);
    if (!map) return;
    for (const [eventId, callbackId] of map) {
      callbacks.get(callbackId)?.({ event, id: eventId, payload });
    }
  };
  const setState = (state: string) => {
    uiState = state;
    emit("sani://state", state);
  };

  /** A streamed turn: status line, steps, then tokens, then done. */
  const simulateTurn = (text: string) => {
    const userId = `u${Date.now()}`;
    const runId = `run${Date.now()}`;
    sent.push({ id: userId, text });
    if (!activeConversation) activeConversation = "c-new";
    emit("sani://message", { id: userId, role: "user", text, created_at: Date.now() });
    setState("working");
    let t = 250;
    const at = (ms: number, fn: () => void) => window.setTimeout(fn, (t += ms));
    at(0, () =>
      emit("sani://agent-start", { message_id: userId, run_id: runId, agent_id: "velo", agent_name: "Velo" }),
    );
    const steps = [
      "Looking at what's on screen",
      "Opened Google Chrome",
      "Typed the search into the address bar",
      "Checked the page loaded",
    ];
    steps.forEach((label, index) => {
      at(700, () => {
        emit("sani://agent-chunk", { message_id: userId, kind: "status", delta: `${label}…`, agent_id: "velo" });
        emit("sani://activity", {
          sequence: 100 + index,
          run_id: runId,
          agent_id: "velo",
          event_type: "agent.progress",
          timestamp: Date.now(),
          label,
          status: "info",
        });
      });
    });
    const answer =
      "Done. Chrome is open and the page for **" +
      text.slice(0, 40) +
      "** has loaded.\n\n```bash\nopen -a \"Google Chrome\" https://example.com\n```\n\nTell me what to do next.";
    answer.match(/.{1,6}/gs)?.forEach((chunk) =>
      at(30, () => emit("sani://agent-chunk", { message_id: userId, kind: "text", delta: chunk, agent_id: "velo" })),
    );
    at(300, () => {
      emit("sani://agent-done", {
        message_id: userId,
        run_id: runId,
        ok: true,
        status: "completed",
        error: "",
        agent_id: "velo",
        agent_name: "Velo",
        assistant_message_id: `a${Date.now()}`,
        text: answer,
        created_at: Date.now(),
      });
      setState("idle");
    });
  };

  const handlers: Record<string, (args: Json) => unknown> = {
    get_state: () => ({
      state: uiState,
      stt_ready: true,
      stt_model: "base-en",
      partial: "",
      mic_permission: "granted",
      active_conversation_id: activeConversation,
    }),
    get_full_settings: () => settings,
    get_settings: () => settings,
    list_mics: () => ["MacBook Pro Microphone", "External USB Mic"],
    core_agents: () => ({
      agents: [
        { id: "deep", name: "Deep Agent", capabilities: ["Reasoning", "Memory", "Skills"] },
        { id: "velo", name: "Velo", capabilities: ["Computer control"] },
      ],
    }),
    voice_models: () => [
      { id: "tiny-en", installed: true, active: false },
      { id: "base-en", installed: true, active: true },
      { id: "medium-en", installed: false, active: false },
    ],
    list_conversations: () => (scenario === "empty" ? [] : conversations),
    get_messages: (args) =>
      scenario === "empty" || args.conversationId !== "c1"
        ? []
        : scenario === "approval"
          ? [...historyMessages.slice(0, 1), { ...historyMessages[1], mission_id: "mis-1" }]
          : historyMessages,
    get_run_activity: (args) => (args.conversationId === "c1" && scenario !== "empty" ? historyActivity : []),
    mission_get_cmd: () => approvalMission,
    computer_control_snapshot: () => control,
    recent_run_timing: () => [
      { run_id: "r1", stage: "first_token", elapsed_ms: 640, status: "observed" },
      { run_id: "r1", stage: "run_start", elapsed_ms: 120, status: "observed" },
    ],
    core_status: () => ({ ok: true }),
    main_ready: () => undefined,
    submit_text_cmd: (args) => {
      simulateTurn(String(args.text));
      return undefined;
    },
    escape_cmd: () => {
      setState("idle");
      return undefined;
    },
    start_listening_cmd: () => {
      setState("listening");
      window.setTimeout(() => emit("sani://partial", "open chrome and search for the weather in"), 400);
      return undefined;
    },
    stop_listening_cmd: () => {
      setState("idle");
      return undefined;
    },
    new_conversation: () => {
      activeConversation = "c-new";
      return "c-new";
    },
    select_conversation: (args) => {
      activeConversation = String(args.conversationId);
      return undefined;
    },
    delete_conversation: () => undefined,
    set_agent_mode: (args) => {
      settings.agent_mode = String(args.agentMode);
      emit("settings://changed", settings);
      return undefined;
    },
    save_settings_cmd: () => undefined,
    open_mic_settings: () => undefined,
  };

  w.__TAURI_INTERNALS__ = {
    transformCallback(callback: (e: unknown) => void) {
      const id = nextId++;
      callbacks.set(id, callback);
      return id;
    },
    invoke(command: string, args: Json = {}) {
      if (command === "plugin:event|listen") {
        const event = String(args.event);
        const eventId = nextId++;
        const map = listeners.get(event) ?? new Map<number, number>();
        map.set(eventId, Number(args.handler));
        listeners.set(event, map);
        return Promise.resolve(eventId);
      }
      if (command === "plugin:event|unlisten") {
        listeners.get(String(args.event))?.delete(Number(args.eventId));
        return Promise.resolve();
      }
      if (command === "plugin:event|emit") return Promise.resolve();
      const handler = handlers[command];
      if (!handler) {
        console.warn(`[preview] no fixture for command "${command}"`);
        return Promise.reject(new Error(`preview: no fixture for ${command}`));
      }
      return Promise.resolve(handler(args));
    },
    convertFileSrc: (path: string) => path,
  };
  w.__TAURI_EVENT_PLUGIN_INTERNALS__ = {
    unregisterListener(event: string, eventId: number) {
      listeners.get(event)?.delete(eventId);
    },
  };
  (window as unknown as { __saniPreview: unknown }).__saniPreview = { emit, sent };
}

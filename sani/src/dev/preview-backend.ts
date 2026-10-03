/**
 * Dev-only stand-in for Tauri's JS bridge, so the REAL main window can run in a
 * plain browser against fixture data (`npm run dev`, then `app.html?preview`).
 *
 * It exists to look at the UI: every state of the chat, settings and consent
 * card can be reached without the Rust host, the sidecar, or any credentials.
 * It is only imported behind `import.meta.env.DEV`, so it never ships.
 *
 * Scenarios (`?preview&scenario=...`): `empty`, `history` (default), `approval`,
 * `signedout` (Claude Code installed but not signed in). Typing a message with a coding
 * word (fix, bug, build, test, code, app) plays a Claude Code turn.
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
  claude_code_enabled: true,
  claude_code_dirs: ["/Users/you/Projects/shop-app"],
  claude_code_permission: "edit",
};

let contextPercent = 47;

const claudeStatus = (signedIn: boolean) => ({
  enabled: settings.claude_code_enabled,
  permission: settings.claude_code_permission,
  folders: settings.claude_code_dirs,
  claude: {
    installed: true,
    path: "/Users/you/.local/bin/claude",
    version: "2.1.286",
    signed_in: signedIn,
    auth_method: signedIn ? "claude.ai" : "none",
    detail: signedIn ? "" : "Not signed in. Run `claude auth login` once in Terminal.",
  },
  usage: {
    rates: {
      five_hour: { status: "allowed", resets_at: Date.now() / 1000 + 5400, used_percent: 61 },
      seven_day: { status: "allowed", resets_at: Date.now() / 1000 + 400000, used_percent: 23 },
    },
    last_run: { context_tokens: contextPercent * 2000, context_window: 200000, turns: 6 },
    context_percent: contextPercent,
  },
});

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

  /** A Claude Code turn: real-looking steps that start running, then finish. */
  const simulateCoding = (text: string) => {
    const userId = `u${Date.now()}`;
    const runId = `run${Date.now()}`;
    emit("sani://message", { id: userId, role: "user", text, created_at: Date.now() });
    setState("working");
    let t = 200;
    const at = (ms: number, fn: () => void) => window.setTimeout(fn, (t += ms));
    at(0, () => emit("sani://agent-start", { message_id: userId, run_id: runId, agent_id: "deep", agent_name: "Deep Agent" }));
    const step = (id: string, label: string, status: string, extra: Json = {}) =>
      emit("sani://activity", {
        sequence: 0, run_id: runId, agent_id: "deep", event_type: "agent.step",
        timestamp: Date.now(), label, status, step_id: id, ...extra,
      });
    at(300, () => step("cc:open", "Opened Claude Code in shop-app", "info"));
    at(500, () => step("cc:1", "Read src/cart.ts", "running", { tool: "Read" }));
    at(800, () => step("cc:1", "Read src/cart.ts", "complete", { tool: "Read", duration_ms: 310 }));
    at(300, () => step("cc:2", "Edited src/cart.ts", "running", { tool: "Edit" }));
    at(900, () =>
      step("cc:2", "Edited src/cart.ts", "complete", {
        tool: "Edit", duration_ms: 880,
        detail: "- const total = items.reduce((s, i) => s + i.price, 0)\n+ const total = items.reduce((s, i) => s + i.price * i.qty, 0)",
      }),
    );
    at(300, () => step("cc:3", "Ran `npm test`", "running", { tool: "Bash" }));
    at(1400, () =>
      step("cc:3", "Ran `npm test`", "failed", {
        tool: "Bash", duration_ms: 6400,
        detail: "npm test\n\n→ FAIL src/cart.test.ts\n  expected 30, received 20",
      }),
    );
    at(300, () => step("cc:4", "Edited src/cart.test.ts", "running", { tool: "Edit" }));
    at(700, () => step("cc:4", "Edited src/cart.test.ts", "complete", { tool: "Edit", duration_ms: 540 }));
    at(300, () => step("cc:5", "Ran `npm test`", "running", { tool: "Bash" }));
    at(1200, () => step("cc:5", "Ran `npm test`", "complete", { tool: "Bash", duration_ms: 5200 }));
    const answer =
      "Fixed the cart total: it ignored quantity, so a line with 2 items was counted once.\n\n- Changed `src/cart.ts` to multiply price by quantity\n- Updated the matching test\n\nThe first test run failed, then passed after the test fix. All 14 tests pass.";
    at(300, () => {
      emit("sani://agent-done", {
        message_id: userId, run_id: runId, ok: true, status: "completed", error: "",
        agent_id: "deep", agent_name: "Deep Agent", assistant_message_id: `a${Date.now()}`,
        text: answer, created_at: Date.now(),
      });
      setState("idle");
    });
  };

  /**
   * "Build me a premium SaaS website": Sani writes the request, Claude Code
   * works, the first build fails, Sani sends a narrower follow-up, then answers.
   */
  const simulateWebsite = (text: string) => {
    const userId = `u${Date.now()}`;
    const runId = `run${Date.now()}`;
    emit("sani://message", { id: userId, role: "user", text, created_at: Date.now() });
    setState("working");
    let t = 150;
    const at = (ms: number, fn: () => void) => window.setTimeout(fn, (t += ms));
    const send = (group: string, id: string, label: string, status: string, extra: Json = {}) =>
      emit("sani://activity", {
        sequence: 0, run_id: runId, agent_id: "deep", event_type: "agent.step",
        timestamp: Date.now(), label, status, step_id: id, group, ...extra,
      });
    at(0, () => emit("sani://agent-start", { message_id: userId, run_id: runId, agent_id: "deep", agent_name: "Deep Agent" }));

    // ---------------- Round 1
    const g1 = "r1";
    const prompt1 = [
      'Build a premium marketing website for "Acme Analytics", a B2B SaaS that turns product usage into plain-English insights.',
      "",
      "Stack: Vite, React, TypeScript, Tailwind CSS. No backend.",
      "Sections: hero (headline, subhead, 'Start free trial', product mock), logo strip, three feature blocks, how it works (3 steps), pricing (Starter / Growth / Scale with a monthly/annual toggle), testimonials, FAQ, footer.",
      "Design: restrained and modern. Light background, near-black text, one indigo accent, generous whitespace, an 8px grid, Inter plus one distinctive display face. Responsive at 390, 768 and 1280px. Respect prefers-reduced-motion.",
      "Quality: semantic HTML, visible focus states, AA contrast. Specific copy, no lorem ipsum.",
      "Do not add dependencies beyond tailwindcss, framer-motion and lucide-react.",
      "Finish by running `npm run build` and tell me exactly what happened.",
    ].join("\n");
    at(500, () => send(g1, "r1:round", "Build the first version of the site", "running", { kind: "round", tool: "claude_code" }));
    at(300, () => send(g1, "r1:prompt", "Sani asked Claude Code", "complete", { kind: "prompt", detail: prompt1 }));
    const work = (id: string, label: string, tool: string, ms: number, ok = true, detail = "") => {
      at(350, () => send(g1, id, label, "running", { tool }));
      at(550, () => send(g1, id, label, ok ? "complete" : "failed", { tool, duration_ms: ms, detail }));
    };
    work("a1", "Updated its plan", "TodoWrite", 420);
    work("a2", "Ran `npm create vite@latest acme-site -- --template react-ts`", "Bash", 8200);
    work("a3", "Ran `npm install`", "Bash", 21400);
    work("a4", "Ran `npm install tailwindcss framer-motion lucide-react`", "Bash", 9100);
    work("a5", "Wrote tailwind.config.ts", "Write", 610, true, "export default {\n  content: ['./index.html', './src/**/*.{ts,tsx}'],\n  theme: { extend: { colors: { accent: '#4f46e5' } } },\n}");
    work("a6", "Wrote src/index.css", "Write", 540);
    work("a7", "Wrote src/components/Hero.tsx", "Write", 1900);
    work("a8", "Wrote src/components/Features.tsx", "Write", 1700);
    work("a9", "Wrote src/components/Pricing.tsx", "Write", 2300);
    work("a10", "Wrote src/App.tsx", "Write", 980);
    work("a11", "Ran `npm run build`", "Bash", 7400, false, "npm run build\n\n\u2192 src/App.tsx(7,28): error TS2307: Cannot find module './components/Testimonials' or its corresponding type declarations.");
    at(300, () => { contextPercent = 63; });
    at(200, () =>
      send(g1, "r1:reply", "Claude Code replied", "complete", {
        kind: "reply",
        detail:
          "Built the hero, features, pricing, FAQ and footer.\n\n`npm run build` **fails**: `src/App.tsx` imports `./components/Testimonials`, which I did not create. Nothing else is wrong.",
      }),
    );
    at(100, () => send(g1, "r1:round", "Build the first version of the site", "failed", { kind: "round", tool: "claude_code", duration_ms: 64000 }));

    // ---------------- Round 2: Sani reads the failure and sends a narrow fix
    const g2 = "r2";
    const prompt2 = [
      "The build fails with: src/App.tsx(7,28) TS2307 Cannot find module './components/Testimonials'.",
      "",
      "Create that component: three customer quotes (name, role, company), styled with the existing tokens and the same spacing as Features.tsx. Change nothing else.",
      "Then run `npm run build` and `npm run lint` and report both results.",
    ].join("\n");
    at(900, () => send(g2, "r2:round", "Fix the failing build", "running", { kind: "round", tool: "claude_code" }));
    at(300, () => send(g2, "r2:prompt", "Sani asked Claude Code", "complete", { kind: "prompt", detail: prompt2 }));
    const fix = (id: string, label: string, tool: string, ms: number) => {
      at(350, () => send(g2, id, label, "running", { tool }));
      at(550, () => send(g2, id, label, "complete", { tool, duration_ms: ms }));
    };
    fix("b1", "Read src/App.tsx", "Read", 280);
    fix("b2", "Wrote src/components/Testimonials.tsx", "Write", 1600);
    fix("b3", "Ran `npm run build`", "Bash", 6400);
    fix("b4", "Ran `npm run lint`", "Bash", 3100);
    at(300, () => { contextPercent = 71; });
    at(200, () =>
      send(g2, "r2:reply", "Claude Code replied", "complete", {
        kind: "reply",
        detail: "Added `Testimonials.tsx` with three quotes.\n\n- `npm run build` passes (JS 148 kB, 47 kB gzipped)\n- `npm run lint` is clean",
      }),
    );
    at(100, () => send(g2, "r2:round", "Fix the failing build", "complete", { kind: "round", tool: "claude_code", duration_ms: 21000 }));

    const answer =
      "Your site is ready in **~/Projects/acme-site**.\n\n" +
      "**What's there**\n- Hero with a clear headline and a *Start free trial* button\n- Logo strip, three feature blocks and a three-step *How it works*\n- Pricing with Starter, Growth and Scale and a monthly/annual toggle\n- Testimonials, FAQ and footer\n\n" +
      "**Design:** light and restrained, one indigo accent, Inter with one display face, responsive at phone, tablet and desktop.\n\n" +
      "The first build failed on a missing *Testimonials* component. I asked Claude Code to add just that, and the build and lint now pass.\n\n" +
      "To look at it, run `npm run dev` in that folder. Want me to deploy it, or change the colours or copy?";
    at(900, () => {
      emit("sani://agent-done", {
        message_id: userId, run_id: runId, ok: true, status: "completed", error: "",
        agent_id: "deep", agent_name: "Deep Agent", assistant_message_id: `a${Date.now()}`,
        text: answer, created_at: Date.now(),
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
      const text = String(args.text);
      if (/\b(website|saas|landing)\b/i.test(text)) simulateWebsite(text);
      else if (/\b(code|fix|bug|build|test|app)\b/i.test(text)) simulateCoding(text);
      else simulateTurn(text);
      return undefined;
    },
    claude_code_status_cmd: () => claudeStatus(scenario !== "signedout"),
    apply_claude_code_settings: (args) => {
      const patch = (args.patch ?? {}) as Json;
      if (typeof patch.enabled === "boolean") settings.claude_code_enabled = patch.enabled;
      if (Array.isArray(patch.dirs)) settings.claude_code_dirs = patch.dirs as string[];
      if (typeof patch.permission === "string") settings.claude_code_permission = patch.permission;
      emit("settings://changed", settings);
      return settings;
    },
    pick_folder_cmd: () => "/Users/you/Projects/blog",
    save_attachment_cmd: (args) =>
      `/Users/you/Library/Application Support/Sani/attachments/${"ab".repeat(16)}-${String(args.name)}`,
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

  // `?autorun=code` plays a Claude Code turn by itself, so a screenshot or a
  // shared preview link shows the live steps without anyone typing.
  const autorun = new URLSearchParams(window.location.search).get("autorun");
  if (autorun === "website") {
    window.setTimeout(
      () => simulateWebsite("Build me a premium SaaS website for my product, Acme Analytics"),
      1800,
    );
  }
  if (autorun === "code") {
    window.setTimeout(
      () => simulateCoding("Fix the cart total bug in my shop app and run the tests"),
      1800,
    );
  }
}

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
  claude_code_model: "",
  claude_code_effort: "",
  zcode_cli_enabled: true,
  zcode_mode: "window",
  zcode_effort: "",
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
    name: signedIn ? "Alex Morgan" : "",
    email: signedIn ? "alex@example.com" : "",
    plan: signedIn ? "pro" : "",
    org: "",
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

let zcodeSignedIn = true;
let zcodeLogin = { state: "idle", url: "", message: "" };
let zcodeSelection: { provider?: string; model?: string } = {};
let zcodeControl = new URLSearchParams(window.location.search).get("control") !== "off";
// ?change=switched shows the account-change banner; ?change=signed_out the red state.
let zcodeChange: string | null = new URLSearchParams(window.location.search).get("change");
// Preview of the window read: "idle" = never read, "reading" for a few seconds after a press, then "done".
let zcodeRead: "never" | "reading" | "done" =
  new URLSearchParams(window.location.search).get("zcode") === "never" ? "never" : "done";
const zcodeJob = () => ({
  state: zcodeRead === "reading" ? "reading" : zcodeRead === "done" ? "done" : "idle",
  message: "",
  started_at: null,
  finished_at: null,
  port_closed: zcodeRead === "done" ? true : null,
});
const zcodeWindowRead = () => {
  if (zcodeRead === "never") return { job: zcodeJob() };
  const at = Date.now() / 1000 - 600;
  return {
    contract: { ok: true, version: "3.14.4", version_verified: true, missing: [], as_of: at },
    models: {
      as_of: at,
      current_model: "GLM-5.3-Flash",
      models: [
        { provider: "custom", plan_id: "account:zai-start-plan", plan: "Start Plan", model: "GLM-5.3-Flash", current: true },
        { provider: "custom", plan_id: "account:zai-start-plan", plan: "Start Plan", model: "GLM-5.3", current: false },
      ],
      modes: [
        { id: "plan", label: "Plan mode", current: false },
        { id: "build", label: "Ask before changes", current: false },
        { id: "edit", label: "Edit automatically", current: true },
        { id: "yolo", label: "Full access", current: false },
      ],
      reasoning: [
        { id: "low", label: "Low", current: false },
        { id: "high", label: "High", current: false },
        { id: "max", label: "Max", current: true },
      ],
    },
    sessions: {
      as_of: at,
      count: 6,
      complete: true,
      projects: [
        { path: "/Users/you/Documents/sani_test", name: "sani_test", tasks: [{ id: "sess_1", title: "Create a file hello.py that prints hello", age: "1h" }] },
        { path: "/Users/you/Documents/ShotVault", name: "ShotVault", tasks: [{ id: "sess_2", title: "ShotVault Go Media Storage & Delivery System", age: "20h" }] },
        { path: "/Users/you/Documents/personal-assistant", name: "personal-assistant", tasks: [
          { id: "sess_3", title: "Jarvis Phase 1 Full Implementation", age: "6d" },
          { id: "sess_4", title: "Untitled session", age: "8d" },
          { id: "sess_5", title: "VELO — Short Build Prompt", age: "12d" },
          { id: "sess_6", title: "Phase 1 corrective-completion", age: "5d" },
        ] },
      ],
    },
    last_run: {
      at: Date.now() / 1000 - 300,
      seconds: 28.6,
      ok: true,
      project: "sani_test",
      model: "GLM-5.3",
      plan: "Start Plan",
      session_id: "sess_demo",
      stopped_reason: "",
      cancelled: false,
      error: "",
      steps: [{ label: "Wrote s6-model.txt", status: "complete" }],
      files_changed: ["s6-model.txt"],
      notes: ["Tokens used: 51,300 (51,300 on GLM-5.3 (ZCode Start Plan)), from ZCode's own balance before and after."],
      tokens_used: 51300,
    },
    job: zcodeJob(),
  };
};
const zcodeModels = (...ids: string[]) =>
  ids.map((id) => ({
    id,
    context_window: id === "GLM-5-Turbo" ? 200000 : 1000000,
    max_output: id === "GLM-5-Turbo" ? 64000 : 128000,
  }));
const zcodeStatus = () => ({
  enabled: settings.zcode_cli_enabled,
  permission: settings.claude_code_permission,
  folders: settings.claude_code_dirs,
  backend: "zcode",
  zcode: {
    installed: true,
    path: "/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs",
    version: "0.16.9",
    signed_in: zcodeSignedIn,
    auth_method: zcodeSignedIn ? "z.ai" : "",
    detail: zcodeSignedIn ? "" : "Not signed in. Run `zcode login` once in Terminal.",
  },
  login: zcodeLogin,
  usage: { rates: {}, last_run: {}, context_percent: null },
  selection: zcodeSelection,
  account: zcodeRead === "never" ? null : { name: "Alex Morgan", email: "", as_of: Date.now() / 1000 - 600 },
  balances:
    zcodeRead === "never"
      ? null
      : {
          as_of: Date.now() / 1000 - 600,
          items: [
            { provider: "account:zai-start-plan", plan: "ZCode Trust Build", model: "GLM-5.3-Flash", remaining: 100000000, total: 100000000, percent: 100, reset: "21:30", expires: "Expires Oct 4, 21:30" },
            { provider: "account:zai-start-plan", plan: "ZCode Start Plan", model: "GLM-5.3", remaining: 3000000, total: 3000000, percent: 100, reset: "21:29", expires: "Expires Oct 4, 21:29" },
            { provider: "account:zai-start-plan", plan: "ZCode Start Plan", model: "GLM-5.3-Flash", remaining: 3800000, total: 5000000, percent: 76, reset: "21:29", expires: "Expires Oct 4, 21:29" },
          ],
        },
  cdp: zcodeWindowRead(),
  control: { enabled: zcodeControl, open: false },
  account_change: {
    change:
      zcodeChange === "switched"
        ? { kind: "switched", from: "Alex Morgan", to: "Sam Rivera", plans_added: ["Pro Plan"], plans_removed: ["ZCode Trust Build"], at: Date.now() / 1000 - 40, source: "window" }
        : zcodeChange === "signed_out"
          ? { kind: "signed_out", from: "Alex Morgan", to: "", plans_added: [], plans_removed: [], at: Date.now() / 1000 - 40, source: "window" }
          : null,
    stale: false,
  },
  extra: {
    app_signed_in: zcodeSignedIn,
    zcode_default: { provider: "account:zai-start-plan", model: "GLM-5.3-Flash" },
    catalog: [
      { id: "account:zai-start-plan", name: "Start Plan", family: "zai-family", family_name: "Z.ai", models: zcodeModels("GLM-5.3-Flash", "GLM-5.2", "GLM-5-Turbo") },
      { id: "account:zai-individual-coding-plan", name: "Z.AI Individual Coding Plan", family: "zai-family", family_name: "Z.ai", models: zcodeModels("GLM-5.3", "GLM-5.3-Flash", "GLM-5.2", "GLM-5-Turbo") },
      { id: "account:bigmodel-start-plan", name: "Start Plan", family: "bigmodel-family", family_name: "BigModel (China)", models: zcodeModels("GLM-5.3-Flash", "GLM-5.2") },
    ],
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
  let signedIn = scenario !== "signedout";
  let login: { state: string; url: string; message: string } = { state: "idle", url: "", message: "" };

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
  const simulateWebsiteFix = (text: string) => {
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

  /** Which question, if any, Sani is waiting on the user to answer. */
  let websiteStage: "none" | "brand" = "none";

  /**
   * Turn 1 of "build a website": Sani plans, creates the folder and sends the
   * first request. Claude Code replies with two questions. Sani answers the one
   * it can settle itself and asks the user the one that is theirs to decide.
   */
  const simulateWebsiteScope = (text: string) => {
    const userId = `u${Date.now()}`;
    const runId = `run${Date.now()}`;
    emit("sani://message", { id: userId, role: "user", text, created_at: Date.now() });
    setState("working");
    let t = 150;
    const at = (ms: number, fn: () => void) => window.setTimeout(fn, (t += ms));
    const plain = (id: string, label: string, status: string, extra: Json = {}) =>
      emit("sani://activity", {
        sequence: 0, run_id: runId, agent_id: "deep", event_type: "agent.step",
        timestamp: Date.now(), label, status, step_id: id, ...extra,
      });
    const send = (id: string, label: string, status: string, extra: Json = {}) =>
      plain(id, label, status, { group: "s1", ...extra });
    at(0, () => emit("sani://agent-start", { message_id: userId, run_id: runId, agent_id: "deep", agent_name: "Deep Agent" }));

    at(500, () => plain("p1", "Planned the project: a marketing site for Acme Analytics", "complete", { tool: "plan",
      detail: "Goal: premium B2B SaaS marketing site.\nNeeds: hero, features, pricing, testimonials, FAQ.\nUnknowns to settle before building: framework, brand look." }));
    at(600, () => plain("p2", "Created the folder acme-site", "complete", { tool: "folder", detail: "/Users/you/Projects/acme-site" }));

    const prompt = [
      'You are starting a new project in an empty folder: a premium marketing website for "Acme Analytics", a B2B SaaS that turns product usage into plain-English insights.',
      "",
      "Do NOT write the site yet. First:",
      "1. Look at the folder and confirm it is empty.",
      "2. Propose the file structure and the sections (hero, logo strip, features, how it works, pricing, testimonials, FAQ, footer).",
      "3. List every decision that is genuinely open and would change the build, each with 2 to 3 options and your recommendation. Do not guess on those.",
      "",
      "Keep it short. Do not install anything yet.",
    ].join("\n");
    at(500, () => send("s1:round", "Scope the project", "running", { kind: "round", tool: "claude_code" }));
    at(300, () => send("s1:prompt", "Sani asked Claude Code", "complete", { kind: "prompt", detail: prompt }));
    at(500, () => send("s1a", "Ran `ls -la`", "running", { tool: "Bash" }));
    at(500, () => send("s1a", "Ran `ls -la`", "complete", { tool: "Bash", duration_ms: 310 }));
    at(400, () => send("s1b", "Updated its plan", "running", { tool: "TodoWrite" }));
    at(500, () => send("s1b", "Updated its plan", "complete", { tool: "TodoWrite", duration_ms: 420 }));
    at(600, () =>
      send("s1:reply", "Claude Code replied", "complete", {
        kind: "reply",
        detail: [
          "The folder is empty. The structure and sections are clear. Two decisions change the whole build, so I stopped here:",
          "",
          "**1. Framework**",
          "- A) Next.js: room for app features later, heavier for a brochure site",
          "- B) Astro: fastest static output, built for marketing sites",
          "- C) Vite + React: flexible, more setup",
          "Recommendation: B.",
          "",
          "**2. Brand look**",
          "- A) Indigo, calm and trustworthy",
          "- B) Emerald, fresh and friendly",
          "- C) Charcoal and amber, bold and premium",
          "I do not know your brand, so I will not guess this one.",
        ].join("\n"),
      }),
    );
    at(200, () => send("s1:round", "Scope the project", "complete", { kind: "round", tool: "claude_code", duration_ms: 19000 }));
    at(900, () => plain("d1", "Decided for you: Astro for the framework", "complete", { tool: "decision",
      detail: "Why: this is a marketing site with no app logic, so speed matters most. It also matches Claude Code's own recommendation. Cheap to change later, so not worth interrupting you." }));
    at(500, () => plain("d2", "Needs your choice: the brand look", "info", { tool: "question" }));

    const answer =
      "Claude Code needs two decisions before it builds.\n\n" +
      "**Framework: I chose Astro.** It is the fastest for a marketing site and you do not need app features yet, so it was not worth interrupting you.\n\n" +
      "**The brand look is yours to pick**, because I do not know your brand. Which feels right?\n\n" +
      "[Options]\n- Indigo, calm and trustworthy\n- Emerald, fresh and friendly\n- Charcoal and amber, bold and premium";
    at(900, () => {
      emit("sani://agent-done", {
        message_id: userId, run_id: runId, ok: true, status: "completed", error: "",
        agent_id: "deep", agent_name: "Deep Agent", assistant_message_id: `a${Date.now()}`,
        text: answer, created_at: Date.now(),
      });
      websiteStage = "brand";
      setState("idle");
    });
  };

  /** Turn 2: the user chose a look; Sani sends the full request and builds. */
  const simulateWebsiteBuild = (choice: string) => {
    const userId = `u${Date.now()}`;
    const runId = `run${Date.now()}`;
    emit("sani://message", { id: userId, role: "user", text: choice, created_at: Date.now() });
    setState("working");
    let t = 150;
    const at = (ms: number, fn: () => void) => window.setTimeout(fn, (t += ms));
    const plain = (id: string, label: string, status: string, extra: Json = {}) =>
      emit("sani://activity", {
        sequence: 0, run_id: runId, agent_id: "deep", event_type: "agent.step",
        timestamp: Date.now(), label, status, step_id: id, ...extra,
      });
    const send = (id: string, label: string, status: string, extra: Json = {}) =>
      plain(id, label, status, { group: "b1", ...extra });
    at(0, () => emit("sani://agent-start", { message_id: userId, run_id: runId, agent_id: "deep", agent_name: "Deep Agent" }));
    const look = choice.split(",")[0];
    at(500, () => plain("c1", `Recorded your choice: ${look}`, "complete", { tool: "decision" }));

    const prompt = [
      'Build the "Acme Analytics" marketing site in this folder, continuing the session.',
      "",
      "Decisions made: Astro + TypeScript + Tailwind CSS. Brand look: " + choice + ".",
      "Sections: hero (headline, subhead, 'Start free trial', product mock), logo strip, three feature blocks, how it works (3 steps), pricing (Starter / Growth / Scale, monthly/annual toggle), testimonials, FAQ, footer.",
      "Design: restrained and premium. Generous whitespace, 8px grid, Inter plus one display face, responsive at 390 / 768 / 1280px, respect prefers-reduced-motion.",
      "Quality: semantic HTML, visible focus states, AA contrast, specific copy, no lorem ipsum.",
      "Finish by running `npm run build` and tell me exactly what happened.",
    ].join("\n");
    at(500, () => send("b1:round", `Build the site: Astro, ${look.toLowerCase()}`, "running", { kind: "round", tool: "claude_code", detail: "continuing the same Claude Code session" }));
    at(300, () => send("b1:prompt", "Sani asked Claude Code", "complete", { kind: "prompt", detail: prompt }));
    const work = (id: string, label: string, tool: string, ms: number) => {
      at(350, () => send(id, label, "running", { tool }));
      at(500, () => send(id, label, "complete", { tool, duration_ms: ms }));
    };
    work("w1", "Ran `npm create astro@latest . -- --template minimal --yes`", "Bash", 11800);
    work("w2", "Ran `npx astro add tailwind --yes`", "Bash", 9400);
    work("w3", "Wrote src/styles/tokens.css", "Write", 640);
    work("w4", "Wrote src/components/Hero.astro", "Write", 2100);
    work("w5", "Wrote src/components/Features.astro", "Write", 1900);
    work("w6", "Wrote src/components/Pricing.astro", "Write", 2600);
    work("w7", "Wrote src/components/Testimonials.astro", "Write", 1700);
    work("w8", "Wrote src/pages/index.astro", "Write", 980);
    work("w9", "Ran `npm run build`", "Bash", 6900);
    at(300, () => { contextPercent = 58; });
    at(200, () =>
      send("b1:reply", "Claude Code replied", "complete", {
        kind: "reply",
        detail: "Built the full site with the " + look.toLowerCase() + " look.\n\n- `npm run build` passes\n- All sections on one page, 61 kB of JavaScript",
      }),
    );
    at(100, () => send("b1:round", `Build the site: Astro, ${look.toLowerCase()}`, "complete", { kind: "round", tool: "claude_code", duration_ms: 52000 }));

    const answer =
      "Your site is ready in **~/Projects/acme-site**, built with Astro and the *" + look.toLowerCase() + "* look.\n\n" +
      "- Hero, logo strip, three features, how it works, pricing with a monthly/annual toggle, testimonials, FAQ and footer\n- Responsive at phone, tablet and desktop, with visible focus states\n\n" +
      "The build passes. Run `npm run dev` in that folder to look at it.\n\nWant me to deploy it, or change the copy?";
    at(900, () => {
      emit("sani://agent-done", {
        message_id: userId, run_id: runId, ok: true, status: "completed", error: "",
        agent_id: "deep", agent_name: "Deep Agent", assistant_message_id: `a${Date.now()}`,
        text: answer, created_at: Date.now(),
      });
      websiteStage = "none";
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
      if (websiteStage === "brand") simulateWebsiteBuild(text);
      else if (/\b(website|saas|landing)\b/i.test(text)) simulateWebsiteScope(text);
      else if (/\b(code|fix|bug|build|test|app)\b/i.test(text)) simulateCoding(text);
      else simulateTurn(text);
      return undefined;
    },
    deep_context_cmd: () => ({ known: true, tokens: 18400, window: 128000, percent: 14, estimated: false, model: "z-ai/glm-5.3-flash" }),
    claude_code_status_cmd: () => ({ ...claudeStatus(signedIn), login }),
    zcode_status_cmd: () => zcodeStatus(),
    open_zcode_cmd: () => undefined,
    zcode_auth_cmd: (args) => {
      const action = String(args.action);
      if (action === "login") {
        zcodeLogin = { state: "waiting", url: "https://chat.z.ai/api/oauth/authorize?x=1", message: "Finish signing in in your browser." };
        window.setTimeout(() => {
          zcodeSignedIn = true;
          zcodeLogin = { state: "succeeded", url: "", message: "Signed in." };
        }, 2500);
      } else if (action === "cancel") {
        zcodeLogin = { state: "cancelled", url: "", message: "Sign-in cancelled." };
      } else if (action === "logout") {
        zcodeSignedIn = false;
        zcodeLogin = { state: "idle", url: "", message: "" };
      } else if (action === "select") {
        zcodeSelection = { provider: String(args.provider), model: String(args.model) };
      } else if (action === "control") {
        zcodeControl = args.enabled === true;
      } else if (action === "ack_change") {
        zcodeChange = null;
      } else if (action === "read") {
        zcodeRead = "reading";
        window.setTimeout(() => {
          zcodeRead = "done";
        }, 4000);
      }
      return zcodeStatus();
    },
    claude_code_auth_cmd: (args) => {
      const action = String(args.action);
      if (action === "login") {
        login = {
          state: "waiting",
          url: "https://claude.com/cai/oauth/authorize?code=true&state=demo",
          message: "Finish signing in in your browser.",
        };
        // The browser finishes the sign-in by itself a few seconds later.
        window.setTimeout(() => {
          if (login.state === "waiting") {
            signedIn = true;
            login = { state: "succeeded", url: "", message: "Signed in." };
          }
        }, 5000);
      } else if (action === "code") {
        if (String(args.code) === "DEMO") {
          signedIn = true;
          login = { state: "succeeded", url: "", message: "Signed in." };
        } else {
          login = { ...login, message: "That code didn't work. Check it and try again." };
        }
      } else if (action === "cancel") {
        login = { state: "cancelled", url: "", message: "Sign-in cancelled." };
      } else if (action === "logout") {
        signedIn = false;
        login = { state: "idle", url: "", message: "" };
      }
      return { ...claudeStatus(signedIn), login };
    },
    open_sign_in_link_cmd: () => undefined,
    focus_main_cmd: () => undefined,
    apply_claude_code_settings: (args) => {
      const patch = (args.patch ?? {}) as Json;
      if (typeof patch.enabled === "boolean") settings.claude_code_enabled = patch.enabled;
      if (typeof patch.zcode_enabled === "boolean") settings.zcode_cli_enabled = patch.zcode_enabled;
      if (Array.isArray(patch.dirs)) settings.claude_code_dirs = patch.dirs as string[];
      if (typeof patch.permission === "string") settings.claude_code_permission = patch.permission;
      if (typeof patch.model === "string") settings.claude_code_model = patch.model;
      if (typeof patch.effort === "string") settings.claude_code_effort = patch.effort;
      if (typeof patch.zcode_mode === "string") settings.zcode_mode = patch.zcode_mode;
      if (typeof patch.zcode_effort === "string") settings.zcode_effort = patch.zcode_effort;
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
    start_dictation_cmd: () => {
      setState("listening");
      window.setTimeout(() => emit("sani://partial", "open chrome and"), 400);
      window.setTimeout(() => emit("sani://partial", "open chrome and search for the weather"), 1000);
      return undefined;
    },
    stop_listening_cmd: () => {
      if (uiState === "listening") emit("sani://dictation-final", "open chrome and search for the weather");
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
  if (autorun === "website-fix") {
    window.setTimeout(
      () => simulateWebsiteFix("Build me a premium SaaS website for my product, Acme Analytics"),
      1800,
    );
  }
  if (autorun === "website") {
    window.setTimeout(
      () => simulateWebsiteScope("Build me a premium SaaS website for my product, Acme Analytics"),
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

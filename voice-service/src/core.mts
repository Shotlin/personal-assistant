// The bridge to the Deep Agent: sani-core, spawned as a child process and spoken to over private stdin/stdout (the same framed
// JSON the Sani desktop app uses: 4-byte big-endian length + UTF-8 JSON, max 1 MiB). No localhost server, no new port.
// The child gets the same configuration the desktop app would give it (model, coding agents, data dir from Sani's settings.json),
// EXCEPT computer control: the voice core never drives the screen (CUA off), and coding is capped by the owner's own ceiling.
// Credentials: the Deep Agent's model key is read by the core itself from the repo's .env (cwd = repo root); this file never
// reads, prints or logs it.
import { execFileSync, spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { EventEmitter } from "node:events";
import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { saniDir } from "./sani.mts";

export type CoreEvent = { run_id: string; agent_id: string; kind: string; data: any };
export type RunResult = { status: string; response: string; usage?: any; raw?: any };

/** Environment for the voice core, from Sani's own settings.json. Names only are ever logged. */
export function coreEnv(opts: { coding?: boolean; dataDir?: string } = {}): Record<string, string> {
  const dir = opts.dataDir ?? saniDir();
  let s: any = {};
  try { s = JSON.parse(readFileSync(join(dir, "settings.json"), "utf8")); } catch { /* defaults */ }
  const env: Record<string, string> = {
    MEMORY_BACKEND: "sqlite", SANI_DATA_DIR: dir, APP_ENV: "development", LOG_LEVEL: "WARNING",
    CUA_ENABLED: "false",                                   // the voice core never touches the screen
  };
  if (s.reasoning_provider) env.MODEL_PROVIDER = s.reasoning_provider === "openrouter" ? "openrouter" : String(s.reasoning_provider);
  if (s.reasoning_model) env.MODEL_NAME = String(s.reasoning_model);
  if (opts.coding !== false) {
    // a phone call may ask for edits, never more than "edit" (no shell commands) whatever the desktop app allows
    if (s.claude_code_enabled) env.CLAUDE_CODE_ENABLED = "1";
    if (Array.isArray(s.claude_code_dirs) && s.claude_code_dirs.length) env.CLAUDE_CODE_DIRS = s.claude_code_dirs.join(":");
    env.CLAUDE_CODE_PERMISSION = s.claude_code_permission === "read" ? "read" : "edit";
    if (s.claude_code_model) env.CLAUDE_CODE_MODEL = String(s.claude_code_model);
    if (s.claude_code_effort) env.CLAUDE_CODE_EFFORT = String(s.claude_code_effort);
    if (s.zcode_cli_enabled) { env.ZCODE_CLI_ENABLED = "1"; if (s.zcode_mode) env.ZCODE_MODE = String(s.zcode_mode); if (s.zcode_effort) env.ZCODE_CLI_EFFORT = String(s.zcode_effort); }
  }
  return env;
}

/** The OpenRouter key the Sani desktop app itself uses, from the macOS Keychain (service "sani-openrouter-key", account "app.sani.local": exactly what
 *  the app's Rust host reads when it starts sani-core). Returned only to be placed in the child's environment: never printed, never logged.
 *  The repo `.env` key is only a fallback (it can be a different, exhausted key). VOICE_KEY_SOURCE=env skips the Keychain. */
function appOpenRouterKey(): string | null {
  if (process.env.VOICE_KEY_SOURCE === "env") return null;
  try {
    const out = execFileSync("/usr/bin/security", ["find-generic-password", "-s", "sani-openrouter-key", "-a", "app.sani.local", "-w"], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], timeout: 8000 }).trim();
    return out || null;
  } catch { return null; }
}

const repoRoot = () => resolve(import.meta.dirname, "..", "..");

export class RunHandle extends EventEmitter {
  text = "";            // everything the agent streamed so far
  progress: string[] = []; // "Using <tool>" style notes
  status: "running" | "done" | "blocked" | "failed" | "cancelled" = "running";
  result: RunResult | null = null;
  readonly done: Promise<RunResult>;
  #resolve!: (r: RunResult) => void;
  constructor(readonly runId: string, readonly core: SaniCore) { super(); this.done = new Promise((r) => (this.#resolve = r)); }
  cancel = () => this.core.request("run.cancel", { run_id: this.runId }, 5000).catch(() => {});
  /** @internal */ _event(e: CoreEvent) {
    if (e.kind === "agent.token") this.text += String(e.data?.text ?? "");
    else if (e.kind === "agent.progress") { const m = String(e.data?.message ?? e.data?.step?.label ?? ""); if (m) this.progress.push(m); }
    else if (e.kind === "agent.cancelled") this.status = "cancelled";
    else if (e.kind === "agent.failed") this.status = "failed";
    this.emit("event", e);
  }
  /** @internal */ _finish(r: RunResult) {
    this.result = r;
    if (this.status === "running") this.status = r.status === "done" ? "done" : r.status === "blocked" ? "blocked" : "failed";
    this.#resolve(r); this.emit("done", r);
  }
}

export class SaniCore {
  #child: ChildProcessWithoutNullStreams | null = null;
  #buf = Buffer.alloc(0);
  #pending = new Map<string, { resolve: (v: any) => void; reject: (e: Error) => void; timer?: NodeJS.Timeout }>();
  #runs = new Map<string, RunHandle>();
  #seq = 0;
  exited = false;
  stderrTail = "";
  keySource = "";  // which key the Deep Agent runs on (a description, never the key)

  /** Start the core. Resolves when it answers `agents.list` (so a failure to boot is an error here, not later). */
  static async start(o: { coding?: boolean; dataDir?: string; python?: string; bootMs?: number; command?: string[] } = {}): Promise<SaniCore> {
    const root = repoRoot();
    const python = o.python ?? process.env.SANI_PYTHON ?? join(root, ".venv", "bin", "python");
    if (!o.command && !existsSync(python)) throw new Error(`sani-core python not found at ${python} (run \`uv sync\` in the repo root)`);
    const core = new SaniCore();
    const env = { ...process.env, ...coreEnv(o), PYTHONPATH: join(root, "src"), PYTHONUNBUFFERED: "1" } as Record<string, string>;
    for (const k of ["SARVAM_API_KEY"]) delete env[k]; // the core has no business with the voice key
    if (!o.command) { const k = appOpenRouterKey(); core.keySource = k ? "the Sani app's Keychain key" : "the repo .env key"; if (k) env.OPENROUTER_API_KEY = k; }
    // `command` replaces the real core with a stand-in (tests only)
    core.#child = o.command ? spawn(o.command[0], o.command.slice(1), { cwd: root, env, stdio: ["pipe", "pipe", "pipe"] }) : spawn(python, ["-m", "assistant.core"], { cwd: root, env, stdio: ["pipe", "pipe", "pipe"] });
    core.#child.stdout.on("data", (d: Buffer) => core.#onData(d));
    core.#child.stderr.on("data", (d: Buffer) => { core.stderrTail = (core.stderrTail + d.toString()).slice(-1500); });
    core.#child.on("exit", () => { core.exited = true; for (const [, p] of core.#pending) p.reject(new Error("sani-core exited")); core.#pending.clear(); for (const r of core.#runs.values()) r._finish({ status: "failed", response: "the agent process stopped" }); });
    core.#child.stdin.on("error", () => { /* the exit handler reports it */ });
    try { await core.request("agents.list", {}, o.bootMs ?? 60_000); } catch (e: any) { core.close(); throw new Error(`sani-core did not start: ${e.message}${core.stderrTail ? ` | ${core.stderrTail.slice(-300).replace(/\s+/g, " ")}` : ""}`); }
    return core;
  }

  #onData(d: Buffer) {
    this.#buf = Buffer.concat([this.#buf, d]);
    for (;;) {
      if (this.#buf.length < 4) return;
      const n = this.#buf.readUInt32BE(0);
      if (n > 1024 * 1024) { this.close(); return; }
      if (this.#buf.length < 4 + n) return;
      const body = this.#buf.subarray(4, 4 + n); this.#buf = this.#buf.subarray(4 + n);
      let f: any; try { f = JSON.parse(body.toString("utf8")); } catch { continue; }
      this.#frame(f);
    }
  }
  #frame(f: any) {
    if (f.type === "response") {
      const p = this.#pending.get(f.id); if (!p) return;
      this.#pending.delete(f.id); if (p.timer) clearTimeout(p.timer);
      f.ok ? p.resolve(f.result) : p.reject(new Error(String(f.error || "request failed")));
    } else if (f.type === "event") this.#runs.get(f.run_id)?._event(f as CoreEvent);
  }

  request(method: string, params: Record<string, unknown>, timeoutMs = 30_000): Promise<any> {
    if (!this.#child || this.exited) return Promise.reject(new Error("sani-core is not running"));
    const id = `v${++this.#seq}`;
    return new Promise((resolve, reject) => {
      const timer = timeoutMs > 0 ? setTimeout(() => { this.#pending.delete(id); reject(new Error(`${method} timed out`)); }, timeoutMs) : undefined;
      this.#pending.set(id, { resolve, reject, timer });
      const body = Buffer.from(JSON.stringify({ type: "request", id, method, params }), "utf8");
      if (body.length > 1024 * 1024) { this.#pending.delete(id); if (timer) clearTimeout(timer); return reject(new Error("request too large")); }
      const head = Buffer.alloc(4); head.writeUInt32BE(body.length);
      this.#child!.stdin.write(Buffer.concat([head, body]));
    });
  }

  /** Start a Deep Agent run. The handle streams text/progress; `done` resolves with the final answer (never rejects). */
  startRun(text: string, o: { agent?: string; threadId?: string; runId?: string } = {}): RunHandle {
    const runId = o.runId ?? `voice-${Date.now().toString(36)}-${++this.#seq}`;
    const h = new RunHandle(runId, this);
    this.#runs.set(runId, h);
    this.request("run.start", { agent_id: o.agent ?? "deep", text, run_id: runId, thread_id: o.threadId ?? "" }, 0)
      .then((r) => h._finish({ status: String(r?.status ?? "done"), response: String(r?.response ?? h.text), usage: r?.usage, raw: r }))
      .catch((e: Error) => { h.status = "failed"; h._finish({ status: "failed", response: e.message }); })
      .finally(() => setTimeout(() => this.#runs.delete(runId), 5000));
    return h;
  }

  close() { try { this.#child?.stdin.end(); } catch { /* gone */ } const c = this.#child; setTimeout(() => { try { c?.kill("SIGTERM"); } catch { /* gone */ } }, 1500).unref(); }
}

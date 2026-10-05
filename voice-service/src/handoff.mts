// Jobs: work handed to the Deep Agent from a call. Tracked in memory (live progress for "what is it doing?") and in voice.db (so the
// result survives the call: the next call can say what happened). Phase 4 will ring him when a job finishes.
import type { RunHandle, SaniCore } from "./core.mts";
import type { VoiceMemory } from "./memory.mts";
import type { Item } from "./notes.mts";
import { buildBrief, hasWork, briefForUi } from "./brief.mts";
import type { Requirement } from "./requirement.mts";
import type { SaniHistory } from "./saniHistory.mts";

export type Job = { id: string; goal: string; handle: RunHandle; startedAt: number; finished: boolean; reported?: boolean; sessionId?: string | null };

const short = (s: string, n: number) => s.replace(/\s+/g, " ").trim().slice(0, n);

export class Handoff {
  readonly jobs: Job[] = [];
  constructor(private readonly core: SaniCore, private readonly mem: VoiceMemory | null, public callId: string, private readonly ui: SaniHistory | null = null) {}

  /** Send the brief to the Deep Agent. Returns the job (already running). */
  /** Jobs whose brief is still being prepared (the call ended while the summary was written): the supervisor must not give up before they start. */
  preparing = 0;
  lastJob(): Job | undefined { return this.jobs[this.jobs.length - 1]; }

  submit(goal: string, brief: string, uiText = "", reuseSession?: string | null): Job {
    const id = `${this.callId}-j${this.jobs.length + 1}`;
    // a real session in the Sani app (sidebar); the run uses the session's id as its thread, so a follow-up typed in the app continues the same job
    const sessionId = reuseSession ?? this.ui?.startSession(`Voice: ${short(goal, 60)}`) ?? null;
    const handle = this.core.startRun(brief, { agent: "deep", threadId: sessionId ?? `voice-${id}` });
    const job: Job = { id, goal: short(goal, 300), handle, startedAt: Date.now(), finished: false, sessionId };
    if (sessionId) this.ui!.addMessage(sessionId, "user", uiText || `Sent from a phone call with Shubh:\n\n${short(goal, 600)}`);
    this.jobs.push(job);
    try { this.mem?.addJob({ id, callId: this.callId, runId: handle.runId, goal: job.goal, brief }); } catch { /* the job still runs */ }
    handle.on("event", (e: any) => {
      try { this.mem?.updateJob(id, { progress: this.#progress(job) }); } catch { /* ignore */ }
      if (sessionId && e?.kind === "agent.progress") this.ui!.addActivity(sessionId, handle.runId, e.data?.step ? { step: e.data.step } : { note: String(e.data?.message ?? "") });
    });
    handle.done.then((r) => {
      job.finished = true;
      if (sessionId) this.ui!.addMessage(sessionId, "assistant", r.response || handle.text || (handle.status === "failed" ? "The agent could not finish this." : "(no reply)"), { runId: handle.runId, agent: true });
      try { this.mem?.updateJob(id, { status: handle.status === "done" ? "done" : handle.status, result: short(r.response || handle.text, 1200), progress: "", finished: true }); } catch { /* ignore */ }
    });
    return job;
  }

  /** Tasks already sent (by board id), so a second yes in the same call never sends the same work twice. */
  readonly sent = new Set<string>();
  /** Build the brief from the board and send it. Returns null when there is nothing new to send. */
  sendFromBoard(o: { items: Item[]; quotes: string[]; open?: string; deferred?: string[]; today: string; workspace?: string; remembered?: string; requirement?: Requirement | null; taskText?: string; followUp?: boolean; sessionId?: string | null }): Job | null {
    const items = o.items.filter((i) => !this.sent.has(i.id));
    const deferred = o.deferred ?? [];
    if (!hasWork(items) && !deferred.length && !o.requirement && !o.taskText) return null;
    const brief = buildBrief({ items, quotes: o.quotes, today: o.today, callId: this.callId, question: deferred.join(" | "), open: o.open, workspace: o.workspace, remembered: o.remembered, requirement: o.requirement, followUp: o.followUp });
    const tasks = items.filter((i) => i.kind === "task");
    for (const t of tasks) this.sent.add(t.id);
    const goal = o.requirement?.goal || tasks.map((t) => t.what).join("; ") || deferred.join("; ") || (o.taskText ?? "");
    return this.submit(goal, brief, briefForUi(items, o.quotes, deferred, o.requirement, o.followUp), o.sessionId);
  }

  #progress(j: Job): string {
    const last = j.handle.progress[j.handle.progress.length - 1] ?? "";
    return last || (j.handle.text ? "writing the answer" : "starting");
  }

  /** One plain sentence for Shubh about every job: what it is, how far it is. Empty when nothing was handed over. */
  statusText(): string {
    return this.jobs.map((j) => {
      const secs = Math.round((Date.now() - j.startedAt) / 1000);
      if (!j.finished) return `- "${j.goal}": still working (${secs} s so far, now: ${this.#progress(j)})`;
      const r = j.handle.result;
      return `- "${j.goal}": ${j.handle.status === "done" ? "finished" : j.handle.status}. ${short(r?.response ?? j.handle.text, 400)}`;
    }).join("\n");
  }
  get running(): Job[] { return this.jobs.filter((j) => !j.finished); }
  /** Wait for every job (up to `ms`); returns the jobs still running. */
  async waitAll(ms: number, onTick?: (j: Job[]) => void): Promise<Job[]> {
    const end = Date.now() + ms;
    while (this.running.length && Date.now() < end) { onTick?.(this.running); await Promise.race([Promise.all(this.running.map((j) => j.handle.done)), new Promise((r) => setTimeout(r, 5000))]); }
    return this.running;
  }
  cancelAll() { for (const j of this.running) void j.handle.cancel(); }
}

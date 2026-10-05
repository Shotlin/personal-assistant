// After the first call, this loop keeps Shubh's promises: when the Deep Agent finishes (or gets stuck) it sends a WhatsApp text copy and
// phones the owner with a short spoken report; when he asked "call me back in two minutes", it phones him then.
// Safety: only the owner's number (the caller is given exactly one), at most `maxAttempts` rings per reason with a pause between them,
// one call at a time, a total time limit, and Ctrl-C stops everything. Written against small interfaces so it can be tested with no phone.
import type { Handoff, Job } from "./handoff.mts";
import type { Scheduler, ScheduledCall } from "./schedule.mts";
import { kindOf, textCopy, type Report } from "./report.mts";

export type PlaceCall = (p: { kind: "report"; job: Job; report: Report } | { kind: "callme"; call: ScheduledCall }) => Promise<{ answered: boolean }>;
export type SupervisorDeps = {
  handoff: Handoff | null; scheduler: Scheduler | null;
  placeCall: PlaceCall; notify: (text: string) => Promise<void>; log: (m: string) => void;
  stopped?: () => boolean; now?: () => number; sleep?: (ms: number) => Promise<void>;
  maxMs?: number; retryMs?: number; maxAttempts?: number; tickMs?: number;
};
export type SupervisorResult = { reportCalls: number; callmeCalls: number; textCopies: number; gaveUp: string[]; timedOut: boolean };

export async function supervise(d: SupervisorDeps): Promise<SupervisorResult> {
  const now = d.now ?? Date.now, sleep = d.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const maxMs = d.maxMs ?? 45 * 60_000, retryMs = d.retryMs ?? 120_000, maxAttempts = d.maxAttempts ?? 2, tickMs = d.tickMs ?? 2000;
  const start = now();
  const res: SupervisorResult = { reportCalls: 0, callmeCalls: 0, textCopies: 0, gaveUp: [], timedOut: false };
  const attempts = new Map<string, number>(), nextTry = new Map<string, number>(), texted = new Set<string>();
  const safeNotify = async (t: string) => { try { await d.notify(t); res.textCopies++; } catch (e: any) { d.log(`text copy failed: ${e.message}`); } };

  for (;;) {
    if (d.stopped?.()) break;
    if (now() - start > maxMs) { res.timedOut = true; d.log("supervisor: time limit reached"); d.handoff?.cancelAll(); break; }
    d.scheduler?.expire(now());

    // 1) finished work: text copy first (he can read it), then a spoken report
    for (const job of d.handoff?.jobs ?? []) {
      if (!job.finished || job.reported) continue;
      const report: Report = { kind: kindOf(job.handle.status), goal: job.goal, result: job.handle.result?.response || job.handle.text };
      if (!texted.has(job.id)) { texted.add(job.id); await safeNotify(textCopy(report.kind, job.goal, report.result)); }
      const n = attempts.get(job.id) ?? 0;
      if (n >= maxAttempts) { job.reported = true; res.gaveUp.push(job.id); d.log(`supervisor: ${job.id}: no answer after ${n} calls; he has the text copy`); continue; }
      if (now() < (nextTry.get(job.id) ?? 0)) continue;
      attempts.set(job.id, n + 1);
      d.log(`supervisor: calling him about ${job.id} (${report.kind}), attempt ${n + 1}`);
      const r = await d.placeCall({ kind: "report", job, report });
      res.reportCalls++;
      if (r.answered) job.reported = true; else nextTry.set(job.id, now() + retryMs);
      if (d.stopped?.()) break;
    }

    // 2) calls he asked for ("call me back in two minutes")
    for (const c of d.scheduler?.due(now()) ?? []) {
      const key = `cb${c.id}`, n = attempts.get(key) ?? 0;
      if (n >= maxAttempts) { d.scheduler!.finish(c.id, "failed"); res.gaveUp.push(key); await safeNotify(`📞 I tried to call you back (${new Date(c.dueAt).toLocaleTimeString()}) but you did not pick up.${c.note ? `\n\nAbout: ${c.note}` : ""}\n\n— Shubh`); continue; }
      if (now() < (nextTry.get(key) ?? 0)) continue;
      attempts.set(key, n + 1); d.scheduler!.attempt(c.id);
      d.log(`supervisor: calling him back #${c.id}, attempt ${n + 1}`);
      const r = await d.placeCall({ kind: "callme", call: c });
      res.callmeCalls++;
      if (r.answered) d.scheduler!.finish(c.id, "done"); else nextTry.set(key, now() + retryMs);
      if (d.stopped?.()) break;
    }

    const running = (d.handoff?.running.length ?? 0) + (d.handoff?.preparing ?? 0);
    const unreported = (d.handoff?.jobs ?? []).filter((j) => j.finished && !j.reported).length;
    const pending = d.scheduler?.pending().length ?? 0;
    if (!running && !unreported && !pending) break;
    await sleep(tickMs);
  }
  return res;
}

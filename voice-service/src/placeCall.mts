// The glue between the supervisor and the phone: how a "report" or "call-back" becomes a real call. Shared by `npm run talk` and the dry run.
import { runLiveCall, type LiveShared } from "./liveCall.mts";
import { composeReport, reportOpening } from "./report.mts";
import type { PlaceCall } from "./supervisor.mts";

/** The first line of a call-back that he asked for ("you told me to call you back"). */
export const callmeOpening = (voice: string) => reportOpening(voice, "done").replace("तुम्हारा काम हो गया है।", "तुमने कहा था कि वापस कॉल करूँ।");

export function makePlaceCall(sh: LiveShared, apiKey: string, voice: string, o: { maxMs?: number; ringMs?: number } = {}): PlaceCall {
  return async (p) => {
    if (p.kind === "report") {
      const spoken = await composeReport(apiKey, p.report, voice);
      const lines = [reportOpening(voice, p.report.kind), ...spoken.split(/(?=\[[a-z]+\])/).map((s) => s.trim()).filter(Boolean)];
      const r = await runLiveCall(sh, { kind: "report", lines }, o);
      return { answered: r.answered };
    }
    // a call-back he asked for: if work is running, tell him how far it is (that is what "call me in a minute for an update" means)
    const status = sh.handoff?.statusText() ?? "";
    if (status) {
      const spoken = await composeReport(apiKey, { kind: "progress", goal: sh.handoff!.jobs[sh.handoff!.jobs.length - 1]?.goal ?? "", result: status }, voice);
      const lines = [reportOpening(voice, "progress"), ...spoken.split(/(?=\[[a-z]+\])/).map((s) => s.trim()).filter(Boolean)];
      const r = await runLiveCall(sh, { kind: "callme", lines }, o);
      return { answered: r.answered };
    }
    const lines = [callmeOpening(voice), ...(p.call.note ? [`[warm] ${p.call.note}`] : [])];
    const r = await runLiveCall(sh, { kind: "callme", lines }, o);
    return { answered: r.answered };
  };
}

// The end of a call: summarise it, store it (voice.db) and mirror the digest to the Deep Agent's memory file.
// Never throws: a failure here must not break hanging up. Returns what happened, for the log.
import type { Conversation } from "./conversation.mts";
import { VoiceMemory } from "./memory.mts";
import { fallbackSummary, summarizeCall } from "./summary.mts";
import { voiceCallsMarkdown } from "./context.mts";
import { mirrorToSani } from "./sani.mts";

export type RememberOptions = { apiKey: string; mem: VoiceMemory; conv: Conversation; startedAt: number; endedAt?: number; language?: string; costInr?: number; callId?: string; mirror?: boolean; quick?: boolean };
export async function rememberCall(o: RememberOptions): Promise<string> {
  try {
    const endedAt = o.endedAt ?? Date.now();
    await Promise.race([o.conv.board.updating, new Promise((r) => setTimeout(r, 8000))]); // the last message may still be filed
    const lines = o.conv.transcript();
    const userTurns = lines.filter((l) => l.role === "user").length;
    if (!userTurns) return "memory: nothing was said, nothing stored";
    const items = o.conv.board.items;
    const s = o.quick ? fallbackSummary(items, userTurns) : await summarizeCall(o.apiKey, lines, items);
    const id = o.callId ?? new Date(o.startedAt).toISOString().replace(/[:.]/g, "-");
    o.mem.saveCall({ id, startedAt: o.startedAt, endedAt, durationS: Math.round((endedAt - o.startedAt) / 1000), language: o.language ?? "", summary: s.summary, decisions: s.decisions, topics: s.topics, costInr: o.costInr ?? 0 }, items);
    let msg = `memory: saved call ${id} (${items.length} items). Summary: ${s.summary}`;
    if (o.mirror !== false && process.env.SANI_MEMORY_MIRROR !== "0") { const why = mirrorToSani(voiceCallsMarkdown(o.mem, endedAt)); msg += why ? `\nmemory: Sani copy ${why}` : "\nmemory: Sani copy updated (/memories/voice-calls.md)"; }
    return msg;
  } catch (e: any) { return `memory: could not save the call (${e.message})`; }
}

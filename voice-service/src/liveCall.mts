// One live WhatsApp call, start to finish: listen (Sarvam STT) -> think (Conversation) -> speak (Bulbul), with turn taking, memory, the
// Deep Agent hand-off and recording. Used for the first call (he phones in / Shubh phones him) AND for every callback (Phase 4), so a
// callback is the same Shubh with the same abilities, just with a spoken report as the first thing he says.
import { appendFileSync, mkdirSync, writeFileSync } from "node:fs";
import type { VoipClient } from "baileys-caller";
import { SarvamStt } from "./sarvamStt.mts";
import { Conversation, greetingFor } from "./conversation.mts";
import { Recorder, saveCall } from "./recorder.mts";
import { SteadyFeed } from "./sttFeed.mts";
import { TurnManager } from "./turns.mts";
import { characterName } from "./delivery.mts";
import type { VoiceMemory } from "./memory.mts";
import { buildContext, knownNames, fixHeard } from "./context.mts";
import { rememberCall } from "./callMemory.mts";
import type { SaniCore } from "./core.mts";
import type { Handoff } from "./handoff.mts";
import { makeExpert } from "./expert.mts";
import { workspaceMap } from "./workspace.mts";
import type { Scheduler } from "./schedule.mts";
import { textCopy } from "./report.mts";
import { SendSettler } from "./settle.mts";
import { summarizeRequirement } from "./requirement.mts";

export type LiveShared = {
  client: VoipClient; apiKey: string; number: string; voice: string;
  mem: VoiceMemory | null; core: SaniCore | null; handoff: Handoff | null; scheduler: Scheduler | null;
  notify?: (text: string) => Promise<void>;     // WhatsApp text copy to the owner's own number
  inCall: boolean;                              // one call at a time
  abort?: () => void;                           // ends the call that is running now (Ctrl-C)
};
export type CallPurpose = { kind: "conversation" } | { kind: "report"; lines: string[] } | { kind: "callme"; lines: string[] };
export type CallResult = { answered: boolean; callId: string; seconds: number; reason: string };

const REAL_LANGS = new Set(["hi-IN", "bn-IN", "en-IN"]); // the listener sometimes labels Hindi/Bengali as Gujarati/Marathi: only these are real for this owner

export async function runLiveCall(sh: LiveShared, purpose: CallPurpose, o: { maxMs?: number; ringMs?: number } = {}): Promise<CallResult> {
  if (sh.inCall) throw new Error("a call is already running");
  sh.inCall = true;
  const tag = purpose.kind === "conversation" ? "call" : "callback";
  const maxMs = o.maxMs ?? (purpose.kind === "conversation" ? 180_000 : 150_000);
  const ringMs = o.ringMs ?? 45_000;
  mkdirSync("var", { recursive: true });
  const logFile = `var/last-${tag}.log`; writeFileSync(logFile, "");
  const T0 = Date.now();
  const callId = new Date(T0).toISOString().replace(/[:.]/g, "-");
  const log = (msg: string, quiet = false) => {
    const line = `${((Date.now() - T0) / 1000).toFixed(2).padStart(6)}s  ${msg}`;
    if (!quiet) console.log(line);
    appendFileSync(logFile, line + "\n");
  };
  const VOICE = sh.voice;
  if (sh.handoff) sh.handoff.callId = callId;
  let connected = false, lang: string | undefined = "hi-IN", lastUserSpeechEnd = Date.now(), watching = false, frames = 0, peak = 0;
  const T_REC = Date.now(); const rec = new Recorder(T_REC), recOut = new Recorder(T_REC);
  const background = sh.mem ? buildContext(sh.mem) : "";
  const known = sh.mem ? knownNames(sh.mem) : "";
  log(`${tag}: memory ${background.length} chars`, true);
  console.log(`\n=== ${purpose.kind === "conversation" ? "CALL" : "CALLBACK"} (${new Date().toLocaleTimeString()}) · memory: ${background ? background.length + " chars" : "nothing yet"} ===`);

  let conv!: Conversation; let call: Awaited<ReturnType<VoipClient["call"]>> | null = null;
  const expert = sh.core ? makeExpert(sh.core, { callId, items: () => conv.board.items, log: (m) => log(m) }) : undefined;
  conv = new Conversation({
    apiKey: sh.apiKey,
    voice: { pushAudio: (p) => sh.client.pushAudio(p), clearAudio: () => sh.client.clearAudio(), pendingMs: () => sh.client.pendingAudioMs() },
    speaker: VOICE,
    pace: Number(process.env.SANI_PACE ?? 0.96), flat: process.env.SANI_FLAT === "1",
    onConflict: () => watchConflicts(),
    context: background,
    expert, canSend: !!sh.handoff, jobStatus: () => sh.handoff?.statusText() ?? "",
    scheduleCallback: sh.scheduler ? (r) => { sh.scheduler!.cancelPendingSince(T0); const id = sh.scheduler!.add(r); log(`SCHEDULED call-back #${id} at ${new Date(r.dueAt).toLocaleTimeString()}${r.note ? ` (${r.note})` : ""}`); } : undefined,
    tuning: { temperature: process.env.SANI_TEMPERATURE ? Number(process.env.SANI_TEMPERATURE) : undefined, gain: Number(process.env.SANI_GAIN ?? 1.15) },
  });
  if (sh.mem) conv.board.hints = known;

  function watchConflicts() {
    if (watching) return; watching = true; const t0 = Date.now();
    const tick = setInterval(() => {
      if (Date.now() - t0 > 10_000 || conv.pendingConflicts.length === 0) { clearInterval(tick); watching = false; return; }
      if (!conv.busy && !conv.isSpeaking() && !turns.active && Date.now() - lastUserSpeechEnd > 1500) {
        clearInterval(tick); watching = false;
        const note = conv.takeConflictNote(); log(`BOARD noticed: ${note}`);
        conv.raise(note, lang).then((r) => { if (r) log(`${characterName(VOICE).toUpperCase()} (raised it): ${r}`); }).catch((e) => log(`raise failed: ${e.message}`));
      }
    }, 300);
  }

  /** Build the brief (a summary of the WHOLE call + his own words + the board) and give it to the Deep Agent. A second request in the same call is a
   *  follow-up on the same session (it waits for the first job to finish, so the agent never works on one thread twice at once). */
  async function submitJob() {
    const h = sh.handoff; if (!h) return;
    h.preparing++;
    try {
      const lines = conv.transcript().filter((l) => l.role === "user").map((l) => l.text);
      const prev = h.lastJob();
      if (prev && !prev.finished) { log("agent: the first job is still running; the new request waits for it"); await prev.handle.done; }
      const requirement = await summarizeRequirement(sh.apiKey, lines, conv.board.items);
      // the agent gets THIS request only: his other open items and earlier calls (payments, other apps) are not its business and only confuse it
      const job = h.sendFromBoard({ items: conv.board.items, quotes: lines.map((t) => t.slice(0, 400)).slice(-8), deferred: conv.deferred.splice(0), today: new Date().toString().slice(0, 21),
        workspace: workspaceMap(), requirement, taskText: lines[lines.length - 1], followUp: !!prev, sessionId: prev?.sessionId ?? null });
      log(job ? `AGENT: job ${job.id} sent${requirement ? "" : " (no summary: used his words)"}: ${job.goal.slice(0, 200)}` : "agent: nothing new to send");
      if (job && sh.notify) void sh.notify(textCopy("sent", job.goal));
    } catch (e: any) { log(`agent: could not send: ${e.message}`); }
    finally { h.preparing--; }
  }
  // a request for work is sent a few seconds after Shubh says he is starting (he can add details or say "wait"); at hang-up it goes at once
  const settler = new SendSettler(() => { log("agent: sending the task now"); void submitJob(); }, { quietMs: Number(process.env.VOICE_SEND_QUIET_MS ?? 7000) });

  const turns: TurnManager = new TurnManager({
    isSpeaking: () => conv.isSpeaking(), speakingSince: () => conv.speakingSince,
    log: (m) => log(`  turn: ${m}`, true),
    onInterrupt: (why) => { log(`INTERRUPTED (${why}): ${characterName(VOICE)} stops`); conv.bargeIn(); },
    onTurn: (text, l) => {
      if (l && REAL_LANGS.has(l)) lang = l; else if (l) log(`  (listener said ${l}; keeping ${lang})`, true);
      const heard = fixHeard(text, known);
      log(`YOU  [${lang ?? "?"}]: ${text}${heard !== text ? `   (repaired to: ${heard})` : ""}`);
      conv.respond(heard, lang).then(async (r) => {
        if (r) log(`${characterName(VOICE).toUpperCase()}${conv.lastInterrupted ? " (interrupted)" : ""} (${conv.lastMode}, first sound ${conv.lastLatencyMs} ms): ${r}`);
        if (conv.sendRequested && !conv.lastInterrupted) { settler.cancel(); void submitJob(); }
        else if (conv.sendCancelled) { settler.cancel(); log("agent: he said wait: nothing will be sent until he says so"); }
        else if (conv.taskRequested && !conv.lastInterrupted) { settler.arm(); log("agent: work asked; sending in a few seconds unless he adds or cancels"); }
        else if (settler.pending) settler.touch();
        // he asked how it is going and a job is already finished: he has just heard the result, no need to ring him about it again
        if (conv.lastMode === "recall" && !conv.lastInterrupted) for (const j of sh.handoff?.jobs ?? []) if (j.finished) j.reported = true;
        if (conv.endRequested && !conv.lastInterrupted) {
          while (conv.isSpeaking()) await new Promise((res) => setTimeout(res, 100));
          await new Promise((res) => setTimeout(res, 700));
          log("hanging up (you said you were done)"); call?.end();
        }
      }).catch((e) => log(`turn failed: ${e.message}`));
    },
  });

  const stt = new SarvamStt({ apiKey: sh.apiKey, languageCode: "auto", model: "saaras:v4", streamType: "fast", silenceMs: 500, minSpeechMs: 300 });
  stt.on("speechStart", (i) => { settler.pause(); log(`heard speech start #${i}${conv.isSpeaking() ? ` (while ${characterName(VOICE)} was speaking)` : ""}`, true); turns.speechStart(); });
  stt.on("speechEnd", (i) => { log(`heard speech end   #${i}`, true); lastUserSpeechEnd = Date.now(); turns.speechEnd(); });
  stt.on("partial", (t, _l, i) => { log(`  partial #${i}: ${t}`, true); turns.partial(t); });
  stt.on("final", (t, l, i) => { log(`  final #${i} [${l ?? "?"}]: ${t}`, true); turns.final(t, l); });
  stt.on("error", (e) => log(`stt error: ${e.message}`));
  const feed = new SteadyFeed((p) => stt.push(p));
  const beat = setInterval(() => { log(`heartbeat: ${frames} audio frames from you in the last 5 s, peak ${peak.toFixed(3)}, speaking: ${conv.isSpeaking()}`, true); frames = 0; peak = 0; }, 5000);

  let reason = "not started", timers: NodeJS.Timeout[] = [];
  try {
    await stt.connect();
    sh.client.onSentAudio = (chunk) => { if (connected) recOut.add(chunk); };
    call = await sh.client.call(sh.number, { audioSource: "push", durationMs: maxMs });
    sh.abort = () => call?.end();
    call.on("ringing", () => log("ringing... pick up"));
    call.on("connected", () => {
      connected = true; log("connected"); feed.start();
      if (purpose.kind === "conversation") void conv.say(greetingFor(VOICE), "hi-IN");
      else void conv.sayLines(purpose.lines, "hi-IN").then(() => log(`${characterName(VOICE).toUpperCase()} (report): ${purpose.lines.join(" ").replace(/\[[a-z]+\]\s*/g, "")}`));
    });
    call.on("audio", (pcm) => {
      if (!connected) return;
      frames++; rec.add(pcm);
      for (let i = 0; i < pcm.length; i++) { const a = Math.abs(pcm[i]); if (a > peak) peak = a; }
      feed.add(pcm);
    });
    call.on("ended", (r) => { reason = String(r); log(`call ended: ${r}`); });
    // nobody picks up: stop ringing (WhatsApp would ring for a minute) and report "not answered"
    timers.push(setTimeout(() => { if (!connected) { log(`not answered after ${Math.round(ringMs / 1000)} s: hanging up`); call?.end(); } }, ringMs));
    timers.push(setTimeout(() => { log("time limit reached: hanging up"); call?.end(); }, maxMs + 3000));
    await call.waitForEnd();
  } catch (e: any) { reason = `error: ${e.message}`; log(`call failed: ${e.message}`); }
  finally {
    for (const t of timers) clearTimeout(t);
    settler.flush(); // he hung up with work still waiting: send it now
    clearInterval(beat); feed.stop(); turns.stop(); conv.bargeIn(); stt.close();
    sh.client.onSentAudio = null; sh.abort = undefined;
  }
  const seconds = Math.round((Date.now() - T0) / 1000);
  if (connected) {
    if (purpose.kind === "conversation") rec.save("var/last-call-in.wav");
    saveCall("var", rec, recOut, purpose.kind === "conversation" ? "last-call" : "last-callback");
    await new Promise((r) => setTimeout(r, 1500)); // the last background board update
    if (sh.mem) { const msg = await rememberCall({ apiKey: sh.apiKey, mem: sh.mem, conv, startedAt: T0, language: lang, costInr: conv.costInr(stt.audioSeconds ?? 0).total }); console.log("\n" + msg); appendFileSync(logFile, msg + "\n"); }
    const c = conv.costInr(stt.audioSeconds ?? 0);
    console.log("\nNOTES BOARD at the end of the call:\n" + conv.board.toText());
    const line = `COST of this ${tag}: Rs ${c.total.toFixed(2)}  =  voice Rs ${c.tts.toFixed(2)} (${conv.stats.ttsChars} chars sent, ${conv.stats.cachedChars} cached)  +  listening Rs ${c.stt.toFixed(2)} (${stt.audioSeconds ?? "?"}s)  +  chat Rs ${c.llm.toFixed(2)}`;
    console.log("\n" + line); appendFileSync(logFile, line + "\n");
  }
  sh.inCall = false;
  return { answered: connected, callId, seconds, reason };
}

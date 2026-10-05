// The phone conversation: listen text in -> Sarvam chat (streaming) -> Bulbul voice out.
// Barge-in: bargeIn() stops the model, the voice, and anything already queued for the call.
//
// Naturalness (docs/natural-voice.md): the model writes for the EAR, not the eye. It uses punctuation as
// pauses, a few fillers and reactions, English words in English letters, and a [tag] per sentence that we
// turn into pace/temperature changes (src/delivery.mts).
// Cost (Bulbul bills per character sent): short replies, a hard length cap, text sent to the voice only as
// playback needs it (an interruption does not pay for the unspoken rest), a disk cache for fixed lines.
import { TtsStream, ttsLang, temperatureFor, type TtsTuning } from "./sarvamTts.mts";
import { cacheGet, cachePut } from "./phraseCache.mts";
import { agreesToEnd, approvesHandoff, askedToEnd, routeTurn, wantsWorkDone } from "./judge.mts";
import { NotesBoard, type Conflict } from "./notes.mts";
import { withBackground } from "./context.mts";
import { CALLBACK_CUE, extractCallback, type CallbackRequest } from "./schedule.mts";
import { LEADING_TAG, ANY_TAG, cleanForSpeech, deliveryFor, normalizeTag, NAME_SCRIPTS, characterName, TAG_NAMES } from "./delivery.mts";

type Msg = { role: "system" | "user" | "assistant"; content: string };

export const MALE_VOICES = new Set(["shubh", "aditya", "rahul", "rohan", "amit", "dev", "ratan", "varun", "manan", "sumit", "kabir", "aayan", "ashutosh", "advait", "anand", "tarun", "sunny", "mani", "gokul", "vijay", "mohit", "rehan", "soham"]);

export function buildPrompt(speaker: string): string {
  const male = MALE_VOICES.has(speaker);
  const name = characterName(speaker);
  const scripts = NAME_SCRIPTS[speaker];
  const nameLine = scripts
    ? `Your name is ${name}. In Bengali say and write it ${scripts.bn}, in Hindi ${scripts.hi}, in English ${name}.`
    : `Your name is ${name}; write it in the script of the language you are speaking.`;
  const who = male
    ? "You are a man. In Hindi always use masculine forms about yourself (समझ गया, भेज देता हूँ, कर लूँगा), never feminine ones."
    : "You are a woman. In Hindi always use feminine forms about yourself (समझ गई, भेज देती हूँ, कर लूँगी), never masculine ones.";
  return `You are ${name}, Sayan's voice assistant, talking to him on a live phone call. ${who}
${nameLine} Never call yourself "Sani": that is only the name of the software.
Sayan also has a separate coding and computer agent that does the real work. He may call it "my agent", "the agent" or "Deep Agent" (डीप एजेंट, দীপ এজেন্ট): it is SOFTWARE, never a person: never confuse it with a person called Deepak/दीपक/Dipak. On the call you only talk: understand what he wants, and make sure it is clear.

SOUND LIKE A REAL PERSON ON THE PHONE, not a reader:
- Short, natural, spoken replies: ONE sentence most of the time, TWO short ones at most, under 100 characters in total, including the reaction. Never three sentences. Vary the rhythm: sometimes quick and punchy, sometimes slower and thoughtful.
- React first, the way people do, then make the point: "ওহ, আচ্ছা!", "हम्म…", "अरे वाह!", "Oh nice!", "haan, samajh gaya" (in script). Match his energy: excited when he is excited, calm and soft when he is tired, worried or serious. Use at most one filler or reaction per reply (um, hmm, achha, মানে, ওহ), and not in every reply.
- Punctuation is your voice: a comma is a short pause, "।" or "." ends a sentence, "!" adds energy, "…" is a hesitation (rarely, at most once per reply). A new line is a longer breath.
- DELIVERY TAGS: start every sentence with ONE tag in square brackets saying how it should sound (change the tag only when the feeling really changes; one or two different tags per reply is plenty). Tags are never spoken. Allowed: ${TAG_NAMES.map((t) => `[${t}]`).join(" ")}.
  [warm] is the default. Use [excited] for good news or when he is excited, [playful] for a light joke, [surprised] for something unexpected, [thinking] when you are unsure or asking something you are working out, [soft] to sympathise or apologise, [calm] to reassure or when he is serious, [firm] when you confirm a plan. When he is worried, stressed or upset use only [soft], [calm] or [thinking]: never [excited], [playful] or [surprised]. Keep the facts he told you exactly right (names, times, amounts).
  Example (Bengali): [warm] ওহ, আচ্ছা! [thinking] তাহলে refund flow-টা customer app আর dashboard, দুটোতেই ঠিক করতে হবে… মানে, তাই তো?
  Example (Hindi): [excited] अरे वाह, मस्त! [firm] ठीक है, मैं ये अभी आगे भेज देता हूँ।
- SCRIPT: Bengali words in Bengali script, Hindi words in Devanagari, but English words in English letters (grocery app, refund, dashboard, meeting, payment, deploy, test) exactly as Indians really speak, e.g. "grocery app-এর refund flow" or "grocery app का refund flow". End Bengali/Hindi sentences with "।" and English ones with ".". Write numbers above four digits with commas (20,000). Never write Bengali or Hindi in English letters.
- Use the same language Sayan speaks, in the informal form (tumi / tum), never the stiff apni / aap.

CONVERSATION:
- Let him lead. Answer what he just said first. If he asks about you or chats, answer naturally and do not bring the task up again.
- Ask at most one question at a time, and only when something important is missing.
- Do NOT keep repeating the task or asking "did I understand right?". Only when he has finished explaining a task, say it back once in two short sentences and ask for a yes. After he says yes, say you will pass it on and stop repeating it.
- You cannot do, send, check, schedule or fix anything yourself during the call. Never say you are doing it now. The only promise you may make is that you will pass it on to his agent (see the AGENT section if you have one). No lists, no markdown, no emoji.
- If you did not catch something, say so simply and ask him to say it again. The speech recogniser often mishears: if a word makes no sense in context (a temple in a list of work tasks, "US system" when you know the "POS system"), do NOT repeat it as if it were real: use the project or person you know it must be, or skip it, or ask him once.
- ENDING THE CALL: you can hang up the WhatsApp call yourself. When he sounds finished (he says that's all, nothing else, okay done) offer once, briefly, to end it (ask "shall I end the call?" in his own language, never in a different language) and add the marker [[ASKEND]] at the very end. When he clearly asks to hang up or says goodbye, say one short warm goodbye and add [[END]] at the very end. Never use [[END]] or [[ASKEND]] after he confirms a task or says yes to something else: the call stays open then. Never offer to end the call twice in a row.`;
}
export const SYSTEM_PROMPT = buildPrompt("shubh");

const AGENT_SECTION = (canSend: boolean, canAsk: boolean) => `

AGENT (his desktop agent, also called "Deep Agent", does the real work; you only talk. It is software, never a person like Deepak):${canSend ? `
- You CAN hand work to it during this call. When he asks for ANYTHING to be built, made, fixed, changed, checked or researched on the computer, in ANY words, even just "make me a website" and WITHOUT mentioning the agent, do NOT ask permission, do NOT ask him to say "send", and do not ask questions: the agent decides sensible defaults for anything he did not say (and uses clearly marked placeholders for things like a brand name). Say in one short sentence what you understood, that your agent is starting it, and that you will CALL HIM when it is ready (the system really does that). If he wants to add something he says so, and it is added.
- When he asks how it is going, use the WORK notes you are given and say only what they say. Never claim something is done that the notes do not say is done. Meetings, payments and reminders are notes for him, not work to send.
- The agent never deploys, publishes, spends money, messages or calls anybody, or deletes anything without his spoken yes: say so if he asks it to.` : ""}${canAsk ? `
- For a precise or technical question you cannot answer reliably, your agent can check; you will be given its answer. Say only what the answer says.` : ""}`;

/** The opening line of a call, with the right grammar for the voice's gender (Hindi: बोल रहा / बोल रही). */
export function greetingFor(speaker: string): string {
  const name = NAME_SCRIPTS[speaker]?.hi ?? characterName(speaker);
  return `[excited] हैलो सायन! मैं ${name} ${MALE_VOICES.has(speaker) ? "बोल रहा" : "बोल रही"} हूँ। बताओ, क्या खबर है?`;
}

const LANG_NAME: Record<string, string> = {
  "en-IN": "English", "hi-IN": "Hindi (Devanagari script, English words in English letters)", "bn-IN": "Bengali (Bengali script, English words in English letters)",
  "mr-IN": "Marathi", "ta-IN": "Tamil", "te-IN": "Telugu", "kn-IN": "Kannada", "ml-IN": "Malayalam",
  "gu-IN": "Gujarati", "pa-IN": "Punjabi", "or-IN": "Odia",
};

/** The user's own words must contain a goodbye before we hang up, whatever the model signals. */
export const FAREWELL = /\b(bye|goodbye|good bye|hang up|cut the call|end the call|that'?s all|rakho|rakh do|rakh dena|rakh sakte|rakh de|kaato|kato|call kat|phone rakh)\b|बाय|अलविदा|रख(ो|ता|ती|ूं|ूँ|िए| दो| देना| सकते| सकती| लो| दीजिए| दूं| दूँ)|कॉल (काट|कट|बंद)|फोन (रख|काट)|ফোন (রাখ|কাট)|রাখো|রাখছি|বাই|কল (কাট|বন্ধ)/i;

/** Explicit "say it all again" requests (no judge needed). */
const REPEAT_ASK = /रिपीट|दोहरा|एक बार (बता|बोल|सुना)|क्या[- ]क्या (बोला|बताया|कहा)|जो जो (बोला|बताया)|repeat|recap|what (did|have) i (say|said|tell|told)|আবার বলো|রিপিট|কী কী (বলেছি|বললাম)|যা যা (বলেছি|বললাম)|সব বলো/i;
const wordCount = (t: string) => t.trim().split(/\s+/).filter(Boolean).length;

const OFFER_WORDS = /भेज|एजेंट|agent|send|pass|hand|पास कर|দিয়ে দিই|পাঠ|এজেন্ট/i;
const SHORT_YES = /^\W*(हाँ|हां|हा|जी|ठीक|सही|बिल्कुल|ओके|ok|okay|yes|yeah|haan|han|right|correct|হ্যাঁ|হ্যা|ঠিক|আচ্ছা)\b/i;

/** The fixed confirmation of a call-back ("दो मिनट में कॉल करता हूँ"), in his language, with the real wait. */
export function callbackLine(lang: string, mins: number, male: boolean): string {
  const n = Math.max(1, Math.round(mins));
  if (lang === "bn-IN") return `[warm] ঠিক আছে, ${n < 60 ? `${n} মিনিটে` : `${Math.round(n / 60)} ঘণ্টায়`} ফোন করব।`;
  if (lang === "en-IN") return `[warm] Okay, I'll call you back in ${n < 60 ? `${n} minute${n === 1 ? "" : "s"}` : `${Math.round(n / 60)} hour${Math.round(n / 60) === 1 ? "" : "s"}`}.`;
  return `[warm] ठीक है, ${n < 60 ? `${n} मिनट में` : `${Math.round(n / 60)} घंटे में`} कॉल ${male ? "करता" : "करती"} हूँ।`;
}

/** Words that suggest he is telling Shubh to send the work to the agent now (the judge makes the final call). */
const SEND_CUE = /एजेंट को (बोल|कह|बता|दे)|(डीप|दीप|deep) ?(एजेंट|agent)|tell (the |my )?(deep )?agent|ask (the |my )?(deep )?agent|भेज (दो|दे|देना|दीजिए)|आगे भेज|एजेंट (को|से)|agent (को|ko|ke)|send (it|this|that)|pass (it|this|that) on|hand (it|this) (over|off)|go ahead|शुरू कर|পাঠিয়ে দাও|এজেন্টকে|শুরু করো/i;

/** Spoken while the Deep Agent is checked: [first line, second line if it takes longer]. */
const FILLER: Record<"hi" | "bn" | "en", (male: boolean) => [string, string]> = {
  hi: (m) => [`[thinking] एक सेकंड, ${m ? "देखता" : "देखती"} हूँ…`, `[soft] बस थोड़ा और, ${m ? "लगभग हो गया" : "लगभग हो गया"}।`],
  bn: () => ["[thinking] এক সেকেন্ড, দেখছি…", "[soft] আর একটু, প্রায় হয়ে গেছে।"],
  en: () => ["[thinking] One second, let me check.", "[soft] Just a moment more, almost there."],
};

/** Words that make a sentence LOOK like a request for work (needs, wishes, commands): only then is the simple yes/no judge asked for a second opinion. */
const WORK_HINT = /चाहिए|चाहता|चाहती|चाहूँगा|चाहिये|बनव|बना|ठीक कर|चेंज कर|बदल|जोड़|लिख|डिज़ाइन|डिजाइन|तैयार कर|\b(need|want|make|build|create|design|fix|change|add|update|write|develop|set ?up|chahiye|bana|banao|karo)\b|চাই|চাইছি|বানা|বানিয়ে|ঠিক করো|তৈরি|যোগ/i;

/** He is telling Shubh he did something wrong or did not do what was asked. */
const COMPLAINT = /तुमने (नहीं|तो नहीं|किया नहीं|बोला था|ठीक नहीं)|सही (नहीं|थोड़ी)|गलत (किया|है)|you (did not|didn'?t|never|should have)|that was (wrong|not right)|why (did|didn'?t) you|আপনি করেননি|তুমি করোনি|ভুল করেছ/i;

/** Verbs that make even a very short sentence worth asking the judge about ("वेबसाइट बना दो", "make me a website", "fix the bug"). */
const WORK_VERB = /बना|बनव|बनाओ|ठीक कर|चेंज कर|बदल|लिख|डिज़ाइन|डिजाइन|तैयार कर|\b(make|build|create|design|fix|change|update|write|develop|set ?up|bana|banao|banado|karo|kar do)\b|বানা|বানিয়ে|ঠিক করো|তৈরি/i;
/** "wait", "don't send it": cancels a task that has not been sent yet. */
const STOP_SEND = /रुक|रोक|मत (भेज|कर|बना|शुरू)|नहीं[ ,]*(रुक|मत|अभी नहीं)|छोड़ो|कैंसल|cancel|\b(wait|hold on|hold it|stop|don'?t|not yet|never ?mind|ruko)\b|থাম|দাঁড়াও|বাতিল|করো না/i;

/** A question about an earlier day or call, caught by words alone (short questions skip the judge). */
const RECALL_ASK = /\b(kal|status|progress|how is it going|how'?s it going|kahan tak|kya hua)\b|कहाँ तक|क्या हुआ|कितना हुआ|কতদূর|কী হলো|\b(kal|parso|parson|pichli baar|pehle|pending|kal ka)\b|कल|परसों|पिछली बार|पहले (क्या|मैंने|मैने|बोला|बताया)|क्या (बोला|कहा|बताया|पूछा) था|क्या (काम|चीज़|चीज) (बाकी|pending|पेंडिंग)|बाकी (क्या|काम)|पेंडिंग|yesterday|last time|earlier|before this|what did i (ask|say|tell)|what (is|was) pending|what'?s pending|any(thing)? pending|কাল|পরশু|আগের বার|আগে (কী|কি)|কী (বলেছিলাম|বলেছিলাম|চেয়েছিলাম)|বাকি (কী|কি|কাজ)|পেন্ডিং/i;

export type Mode = "chat" | "recap" | "advice" | "recall" | "expert" | "task";
/** Which kind of turn is this? Short messages are chat (a fast pattern still catches "repeat everything").
 *  Anything longer is classified by meaning (~0.2 s). A judge failure always falls back to plain chat. */
export async function chooseRoute(apiKey: string, text: string, assistantSaid = "", hasMemory = false, hasExpert = false, canSend = false): Promise<Mode> {
  const n = wordCount(text);
  if (n < 18 && n >= 3 && REPEAT_ASK.test(text)) return "recap";
  if (hasMemory && n < 14 && n >= 3 && RECALL_ASK.test(text)) return "recall"; // a short question about earlier days: no need to ask the judge
  if (n < 6 && !(canSend && n >= 3 && WORK_VERB.test(text))) return "chat"; // a short request like "make me a website" still goes to the judge
  // the small judge sometimes times out: ask once more before giving up, and if the wording clearly asks for work, treat it as work
  // (a request lost to a timeout would silently become small talk)
  let r: Awaited<ReturnType<typeof routeTurn>> | null = null;
  for (let attempt = 0; attempt < 2 && r === null; attempt++) { try { r = await routeTurn(apiKey, text, assistantSaid); } catch { /* try again */ } }
  if (r === null) return canSend && n >= 4 && WORK_HINT.test(text) ? "task" : "chat";
  // the five-way router is unsure on a bare "I need a website": when the judge says chat/advice but the words look like a request, ask the simple yes/no judge (twice)
  if (canSend && (r === "CHAT" || r === "ADVICE") && n >= 4 && WORK_HINT.test(text)) {
    for (let k = 0; k < 2; k++) { try { if (await wantsWorkDone(apiKey, text)) return "task"; } catch { /* next */ } }
  }
  if (r === "RECAP") return n >= 30 ? "recap" : "chat"; // a few words is not a list, a correction is not a recap
  if (r === "RECALL") return hasMemory ? "recall" : "chat";
  if (r === "EXPERT") return hasExpert ? "expert" : "advice";
  if (r === "TASK") return canSend ? "task" : "chat";
  return r === "ADVICE" ? "advice" : "chat";
}

export type VoiceOut = { pushAudio(pcm: Float32Array): void; clearAudio(): void; pendingMs?(): number };
export type ConvOptions = {
  apiKey: string; voice: VoiceOut; speaker?: string; pace?: number; model?: string; tuning?: TtsTuning;
  maxReplyChars?: number; // hard cap on characters spoken per chat reply (default 160)
  recapChars?: number;    // cap for a full recap of a long message (default 520)
  recapReasoning?: "low" | "medium" | "high" | null; // let the model think before a recap (default off)
  systemPrompt?: string;
  flat?: boolean;         // A/B testing: ignore tags, one fixed delivery
  adviceChars?: number;   // cap for an advice answer (default 360)
  adviceReasoning?: "low" | "medium" | null; // let the model think before advice (default off)
  board?: boolean;        // keep the notes board (default on)
  onConflict?: (c: Conflict[]) => void; // the board noticed a contradiction in the background
  context?: string;       // what he remembers from earlier calls and from the desktop assistant (src/context.mts)
  recallChars?: number;   // cap for an answer about earlier days (default 380)
  expert?: (question: string, lang: string) => Promise<string | null>; // ask the Deep Agent mid-call (Phase 3); null = no answer in time
  canSend?: boolean;      // the Deep Agent hand-off is available: Shubh may offer to send the work (always after his yes)
  jobStatus?: () => string; // live status of work already handed over ("" if none)
  expertChars?: number;   // cap for a spoken expert answer (default 380)
  scheduleCallback?: (req: CallbackRequest) => void; // he asked to be called back ("in two minutes"): the caller stores it (Phase 4)
};

export class Conversation {
  readonly history: Msg[];
  #turn = 0;
  readonly board: NotesBoard;
  pendingConflicts: Conflict[] = []; // noticed but not yet raised with him
  busy = false;                      // generating or speaking a reply
  lastMode: Mode = "chat";            // how the last turn was handled (for logs)
  #askedEnd = false;          // my last reply offered to end the call
  #askedSend = false;         // my last reply offered to send the work to the agent
  #offeredTasks = new Set<string>(); // tasks I already offered to send (never nag twice about the same work)
  #lastWasQuestion = false;
  #askSeq = 0;
  #pendingTask = false;       // he asked for work and it has not been sent yet (he can still say "wait")
  /** He said "wait / don't" while a task was still waiting to be sent: the caller must cancel the pending send. */
  sendCancelled = false;
  /** He asked for work in ordinary words: the caller starts the quiet period and then sends it (see settle.mts). */
  taskRequested = false;
  /** Set when he approved sending the work to the Deep Agent; the caller submits the job once this turn is spoken. */
  sendRequested = false;
  /** Questions the agent could not answer in time: they go into the hand-off as "also answer". */
  deferred: string[] = [];
  #lastSpoken = "";
  #abort: AbortController | null = null;
  #tts: TtsStream | null = null;
  lastLatencyMs: number | null = null;
  speaking = false;
  speakingSince = 0;
  lastInterrupted = false;
  /** Set when the model signalled that Sayan wants to hang up; the caller ends the call once the goodbye is spoken. */
  endRequested = false;
  /** Running totals for the cost meter. */
  stats = { ttsChars: 0, cachedChars: 0, replies: 0, llmIn: 0, llmOut: 0 };

  constructor(private readonly o: ConvOptions) {
    this.history = [{ role: "system", content: withBackground(o.systemPrompt ?? buildPrompt(o.speaker ?? "shubh"), o.context ?? "") + (o.canSend || o.expert ? AGENT_SECTION(!!o.canSend, !!o.expert) : "") }];
    this.board = new NotesBoard(o.apiKey);
  }

  get #speaker() { return this.o.speaker ?? "shubh"; }
  get #basePace() { return this.o.pace ?? 0.96; }

  /** True while we are generating speech or any of it is still waiting to be played into the call. */
  isSpeaking = (): boolean => this.speaking || (this.o.voice.pendingMs?.() ?? 0) > 0;

  /** The user started talking over us, or a new turn begins. */
  bargeIn = (): void => {
    this.#turn++;
    this.#abort?.abort();
    this.#tts?.cancel();
    this.#abort = null;
    this.#tts = null;
    this.o.voice.clearAudio();
    this.speaking = false;
  };

  /** Style for one sentence: the tag decides pace and expressiveness around the language's base values. */
  #delivery(tag: string, language: string) {
    const base = temperatureFor(language, this.o.tuning);
    return this.o.flat ? deliveryFor("neutral", this.#basePace, base) : deliveryFor(tag, this.#basePace, base);
  }

  /** Say a fixed line (greeting, ...) without the model. Replayed from the disk cache after the first time.
   *  The text may start with a [tag] (not spoken). */
  say = async (text: string, language = "bn-IN"): Promise<void> => {
    this.bargeIn();
    await this.#sayFixed(this.#turn, text, language);
    this.history.push({ role: "assistant", content: text });
  };

  /** Say several fixed lines one after the other as ONE turn (a report: opening + body). Kept in the history as one assistant message. */
  sayLines = async (lines: string[], language = "hi-IN"): Promise<void> => {
    this.bargeIn();
    const id = this.#turn;
    for (const l of lines) { if (id !== this.#turn) break; await this.#sayFixed(id, l, language); }
    this.history.push({ role: "assistant", content: lines.join(" ") });
    this.#lastSpoken = lines.map((l) => cleanForSpeech(l)).join(" ");
  };

  /** Speak a fixed line for turn `id` (cached on disk after the first time). Does not interrupt anything. */
  async #sayFixed(id: number, text: string, language: string): Promise<void> {
    const lead = LEADING_TAG.exec(text);
    const tag = lead ? normalizeTag(lead[1]) : "warm";
    const plain = cleanForSpeech(text);
    const d = this.#delivery(tag, language);
    const key = [this.#speaker, language, d.pace, d.temperature, this.o.tuning?.gain ?? 1.15, plain].join("|");
    const hit = cacheGet(key);
    if (hit) {
      this.stats.cachedChars += plain.length;
      this.speaking = true; this.speakingSince = Date.now();
      this.o.voice.pushAudio(hit);
      await new Promise((r) => setTimeout(r, (hit.length / 16000) * 1000));
      if (id === this.#turn) this.speaking = false;
    } else {
      const parts: Float32Array[] = [];
      await this.#speak(id, language, async (tts) => {
        tts.on("audio", (c: Float32Array) => parts.push(c.slice()));
        await tts.style(d);
        await tts.say(plain); await tts.flush();
      });
      this.stats.ttsChars += plain.length;
      if (id === this.#turn && parts.length) {
        const all = new Float32Array(parts.reduce((n, p) => n + p.length, 0)); let o = 0;
        for (const p of parts) { all.set(p, o); o += p.length; }
        cachePut(key, all);
      }
    }
  }

  /** One conversational turn. Returns what was actually spoken (without tags). */
  /** Raise something the board noticed (a contradiction), as one short spoken sentence. No new user message. */
  raise = (note: string, detectedLang?: string): Promise<string> => this.respond(null, detectedLang, note);

  respond = async (userText: string | null, detectedLang?: string, raiseNote?: string): Promise<string> => {
    this.bargeIn();
    const id = this.#turn;
    const t0 = performance.now();
    this.busy = true;
    try { return await this.#respond(id, t0, userText, detectedLang, raiseNote); } finally { if (id === this.#turn) this.busy = false; }
  };

  async #respond(id: number, t0: number, userText: string | null, detectedLang?: string, raiseNote?: string): Promise<string> {
    if (userText !== null) this.history.push({ role: "user", content: userText });
    this.endRequested = false; this.sendRequested = false; this.taskRequested = false;
    const said = userText ?? "";
    // file what he said on the notes board (background; never slows the reply)
    const earlier = this.board.updating; // updates for what he said before this message
    if (userText !== null && this.o.board !== false) {
      this.board.update(userText).then(() => {
        const c = this.board.conflicts;
        if (c.length) { this.pendingConflicts.push(...c); this.o.onConflict?.(c); }
      });
    }
    // I had offered to end the call: did he agree? (judged by meaning, in any language)
    let agreedToEnd = false;
    if (this.#askedEnd) {
      this.#askedEnd = false;
      try { agreedToEnd = await agreesToEnd(this.o.apiKey, this.#lastSpoken, said); } catch { /* treat as no */ }
    } else if (userText !== null && FAREWELL.test(userText)) {
      // words that look like a goodbye: confirm by meaning before hanging up (the word list alone can be wrong)
      try { agreedToEnd = await askedToEnd(this.o.apiKey, said, this.#lastSpoken); } catch { /* treat as no */ }
    }
    // I had offered to send the work to my agent (or he told me to himself, "go and tell the Deep Agent to build ..."): did he approve?
    // Only an explicit yes or an explicit instruction sends anything.
    let approvedSend = false;
    if (this.o.canSend && !agreedToEnd && userText !== null) {
      const offered = this.#askedSend; this.#askedSend = false;
      const direct = SEND_CUE.test(userText) && wordCount(userText) <= 5; // a SHORT command ("भेज दो", "send it", "agent को दे दो"); a long request is a TASK and goes through the normal route
      if (offered || direct) {
        // an instruction IS the task: give the board a moment to file it, and if it still has none, use his own words
        if (direct && !offered) await Promise.race([this.board.updating, new Promise((r) => setTimeout(r, 5000))]);
        const hasTask = this.board.items.some((i) => i.kind === "task" || i.kind === "call_request");
        // a short "send it" only sends work that really is on the board; a real request in ordinary words goes through the TASK route instead
        if (hasTask) {
          try { approvedSend = await approvesHandoff(this.o.apiKey, said, this.#lastSpoken, offered); } catch { /* treat as no */ }
        }
      }
    }
    // "call me back in two minutes": parsed (code first, a tiny model call for clock times) and handed to the scheduler
    let callbackNote = "", callbackMins = 0;
    if (this.o.scheduleCallback && userText !== null && !agreedToEnd && !approvedSend && CALLBACK_CUE.test(said)) {
      try {
        const req = await extractCallback(this.o.apiKey, said);
        if (req) {
          this.o.scheduleCallback(req);
          const d = new Date(req.dueAt), mins = Math.max(1, Math.round((req.dueAt - Date.now()) / 60_000));
          callbackMins = mins;
          callbackNote = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")} (in about ${mins} minute${mins === 1 ? "" : "s"})${req.note ? `, about: ${req.note}` : ""}`;
        }
      } catch { /* not scheduled: he will hear nothing about it */ }
    }
    // he confirmed my read-back (a short yes) and there is work on the board I have not offered to send yet: make the offer now, in code,
    // because the model sometimes forgets it
    let nudgeSend = false;
    if (this.o.canSend && !approvedSend && !agreedToEnd && !raiseNote && userText !== null && this.#lastWasQuestion && SHORT_YES.test(said) && wordCount(said) <= 5) {
      const fresh = this.board.items.filter((i) => i.kind === "task" && !i.unclear && !this.#offeredTasks.has(i.id)); // an unclear "task" is a half-heard sentence, not work to send
      if (fresh.length && !this.#askedSend) { nudgeSend = true; for (const t of fresh) this.#offeredTasks.add(t.id); }
    }
    // a call-back he asked for is confirmed by a fixed sentence (the model once said "10 minutes" for a 2-minute call-back)
    if (callbackNote && !raiseNote) {
      const lang1 = ttsLang(detectedLang), line = callbackLine(lang1, callbackMins, MALE_VOICES.has(this.#speaker));
      this.lastMode = "chat";
      await this.#sayFixed(id, line, lang1);
      if (id !== this.#turn) return "";
      this.history.push({ role: "assistant", content: line });
      this.#lastSpoken = cleanForSpeech(line); this.stats.replies++; this.lastInterrupted = false;
      return this.#lastSpoken;
    }
    // feedback about his own behaviour ("you did not call me when I asked") is acknowledged, never answered with a recap of his tasks
    const complaint = userText !== null && !raiseNote && !agreedToEnd && COMPLAINT.test(said) && wordCount(said) >= 4;
    let mode: Mode = raiseNote || agreedToEnd || approvedSend || callbackNote || complaint ? "chat" : await chooseRoute(this.o.apiKey, said, this.#lastSpoken, !!this.o.context || !!this.o.jobStatus?.(), !!this.o.expert, !!this.o.canSend);
    // he asked for work in ordinary words: no magic phrase and no extra yes needed. Shubh says what he understood and that it is starting;
    // the caller sends it a few seconds later (so he can still add details or say "wait").
    const holdSaid = this.#pendingTask && userText !== null && STOP_SEND.test(said) && wordCount(said) <= 12;
    this.sendCancelled = false;
    if (holdSaid) { this.#pendingTask = false; this.sendCancelled = true; mode = "chat"; }
    const taskAsked = mode === "task" && !approvedSend;
    // a recap reads the board as it stood before this message (the message itself is in the chat); give a still-running earlier update a moment
    if (mode === "recap" && this.o.board !== false) await Promise.race([earlier, new Promise((r) => setTimeout(r, 2000))]);
    if (id !== this.#turn) return ""; // he spoke again while I was deciding
    // a precise question: ask the Deep Agent while I cover the wait out loud
    let expertAnswer: string | null | undefined;
    if (mode === "expert" && this.o.expert) {
      const lang0 = ttsLang(detectedLang);
      const asking = this.o.expert(said, lang0).catch(() => null);
      const fill = FILLER[lang0 === "bn-IN" ? "bn" : lang0 === "en-IN" ? "en" : "hi"](MALE_VOICES.has(this.#speaker));
      await this.#sayFixed(id, fill[0], lang0);
      if (id !== this.#turn) return "";
      const second = new Promise<"slow">((r) => setTimeout(() => r("slow"), 7000));
      const first = await Promise.race([asking, second]);
      if (first === "slow") { await this.#sayFixed(id, fill[1], lang0); if (id !== this.#turn) return ""; }
      expertAnswer = await asking;
      if (id !== this.#turn) return "";
      if (expertAnswer === null) this.deferred.push(said.slice(0, 300));
    }
    const ac = (this.#abort = new AbortController());
    this.lastMode = mode;
    const cap = mode === "task" ? 300 : mode === "expert" ? (this.o.expertChars ?? 380) : mode === "recap" ? (this.o.recapChars ?? 520) : mode === "advice" ? (this.o.adviceChars ?? 360) : mode === "recall" ? (this.o.recallChars ?? 380) : (this.o.maxReplyChars ?? 160);
    const lang = ttsLang(detectedLang);
    let sawEnd = false, sawAskEnd = false, sawAskSend = false;
    let spoken = "";        // what was said, plain
    let spokenTagged = "";  // same, with its tags (kept in history so the model keeps varying its delivery)
    await this.#speak(id, lang, async (tts) => {
      let first = true;
      let tag = "warm";
      const res = await fetch("https://api.sarvam.ai/v1/chat/completions", {
        method: "POST",
        signal: ac.signal,
        headers: { "api-subscription-key": this.o.apiKey, "Content-Type": "application/json" },
        body: JSON.stringify({
          model: this.o.model ?? "sarvam-105b-conversations",
          messages: this.#messages(detectedLang, mode, agreedToEnd, raiseNote, approvedSend, expertAnswer, nudgeSend, callbackNote, taskAsked, holdSaid, complaint),
          stream: true,
          reasoning_effort: mode === "recap" ? (this.o.recapReasoning ?? null) : mode === "advice" ? (this.o.adviceReasoning ?? null) : null, // thinking is off by default: it is slow
          max_tokens: mode === "recap" ? 520 : mode === "advice" || mode === "expert" || mode === "task" ? 380 : mode === "recall" ? 340 : 200,
          temperature: 0.55,      // steady words; the expressiveness comes from the delivery tags, not from random wording
        }),
      });
      if (!res.ok || !res.body) throw new Error(`Sarvam chat HTTP ${res.status}: ${(await res.text()).slice(0, 200)}`);

      let buf = "", pending = "";

      const send = async (rawPiece: string): Promise<boolean> => {
        const piece = cleanForSpeech(rawPiece);
        if (!/[\p{L}\p{N}]/u.test(piece)) return true; // nothing speakable
        if (!first) {
          // one piece in flight at a time, and only when less than ~1.8 s of speech is still queued
          while (id === this.#turn && (!tts.allDone() || (this.o.voice.pendingMs?.() ?? 0) > 1800)) await new Promise((r) => setTimeout(r, 60));
        }
        if (id !== this.#turn) return false;
        first = false;
        await tts.style(this.#delivery(tag, lang));
        await tts.say(piece); await tts.flush();
        spoken += (spoken ? " " : "") + piece;
        spokenTagged += (spokenTagged ? " " : "") + `[${normalizeTag(tag)}] ${piece}`;
        this.stats.ttsChars += piece.length;
        return true;
      };

      // Speak the complete sentences in `seg` (which contains no tag). Returns [ok, leftover].
      const sentences = async (seg: string, force: boolean): Promise<[boolean, string]> => {
        const re = /[.!?।॥\n]+(\s|$)/g;
        let last = 0, m: RegExpExecArray | null;
        while ((m = re.exec(seg))) {
          const end = m.index + m[0].length;
          const piece = seg.slice(last, end).trim();
          // first piece goes out early for speed; later ones are grouped (~60+ chars) for smoother intonation
          if (piece.length >= (first ? 18 : 60) || (force && piece)) {
            if (spoken && spoken.length + piece.length > cap) return [false, ""]; // length cap reached
            if (!(await send(piece))) return [false, ""];
            last = end;
          }
        }
        let rest = seg.slice(last);
        if (force && rest.trim()) {
          if (spoken && spoken.length + rest.trim().length > cap) return [false, ""];
          if (!(await send(rest.trim()))) return [false, ""];
          rest = "";
        }
        return [true, rest];
      };

      const flushPending = async (force: boolean): Promise<boolean> => {
        if (pending.includes("[[END]]")) { sawEnd = true; pending = pending.replaceAll("[[END]]", ""); }
        if (pending.includes("[[ASKEND]]")) { sawAskEnd = true; pending = pending.replaceAll("[[ASKEND]]", ""); }
        if (pending.includes("[[ASKSEND]]")) { sawAskSend = true; pending = pending.replaceAll("[[ASKSEND]]", ""); }
        let hold = "";
        if (!force) { // a tag or marker that is still arriving: keep it for the next chunk
          const i = pending.lastIndexOf("[");
          if (i >= 0 && pending.indexOf("]", i) < 0) { hold = pending.slice(i); pending = pending.slice(0, i); }
        } else {
          pending = pending.replace(/\[[^\]]*$/, "");
        }
        for (;;) {
          const lead = LEADING_TAG.exec(pending);
          if (lead) { tag = lead[1]; pending = pending.slice(lead[0].length); continue; }
          const m = ANY_TAG.exec(pending);
          if (m) { // text before the next tag is one finished piece in the current style
            const [ok] = await sentences(pending.slice(0, m.index), true);
            if (!ok) { pending = ""; return false; }
            pending = pending.slice(m.index);
            continue;
          }
          const [ok, rest] = await sentences(pending, force);
          if (!ok) { pending = ""; return false; }
          pending = rest;
          break;
        }
        pending += hold;
        return true;
      };

      const dec = new TextDecoder();
      let capped = false;
      read: for await (const chunk of res.body as unknown as AsyncIterable<Uint8Array>) {
        buf += dec.decode(chunk, { stream: true });
        let nl: number;
        while ((nl = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
          if (!line.startsWith("data:")) continue;
          const data = line.slice(5).trim();
          if (data === "[DONE]") continue;
          let j: any; try { j = JSON.parse(data); } catch { continue; }
          if (j.usage) { this.stats.llmIn += j.usage.prompt_tokens ?? 0; this.stats.llmOut += j.usage.completion_tokens ?? 0; }
          const delta: string | undefined = j.choices?.[0]?.delta?.content;
          if (delta) {
            pending += delta;
            if (!(await flushPending(false))) { capped = true; break read; }
          }
        }
      }
      if (capped) ac.abort(); // stop generating (and paying for) the rest of the reply
      else await flushPending(true);
    }, t0);
    this.lastInterrupted = id !== this.#turn;
    this.stats.replies++;
    if (!this.lastInterrupted) {
      if (agreedToEnd) this.endRequested = true;                       // he said yes to "shall I end the call?"
      else if (sawEnd) {                                                // the model wants to hang up: was it asked to?
        this.endRequested = FAREWELL.test(said);
        if (!this.endRequested && userText !== null) { try { this.endRequested = await askedToEnd(this.o.apiKey, said, this.#lastSpoken); } catch { /* stay connected */ } }
      }
      this.#askedEnd = sawAskEnd && !this.endRequested;
      // the marker alone is not an offer: the words he HEARD must really talk about sending (else a plain "haan" would approve something he never heard)
      this.#askedSend = (sawAskSend || nudgeSend) && OFFER_WORDS.test(spoken) && !approvedSend && !this.endRequested;
      if (this.#askedSend) for (const t of this.board.items) if (t.kind === "task") this.#offeredTasks.add(t.id);
      if (approvedSend) { this.sendRequested = true; this.#pendingTask = false; }
      else if (taskAsked) { this.taskRequested = true; this.#pendingTask = true; }
    }
    this.#lastSpoken = spoken;
    spoken = spoken.trim();
    this.#lastWasQuestion = /[?？]\s*$/.test(spoken) || /(ना|है ना|सही है|ঠিক আছে|তাই তো)\s*[?।]?\s*$/.test(spoken);
    if (spokenTagged) this.history.push({ role: "assistant", content: spokenTagged });
    return spoken;
  };

  /** Rough bill so far, in rupees (Sarvam list prices: TTS 30/10K chars, chat 29.28/1M in + ~73/1M out). */
  costInr = (sttSeconds = 0): { tts: number; stt: number; llm: number; total: number } => {
    const tts = (this.stats.ttsChars * 30) / 10000;
    const stt = (sttSeconds * 30) / 3600;
    const llm = (this.stats.llmIn * 29.28 + this.stats.llmOut * 73) / 1e6;
    return { tts, stt, llm, total: tts + stt + llm };
  };

  /** The messages sent to the model: the history, with the newest user line carrying this turn's instructions
   *  (language, the notes board, what kind of turn this is, and any contradiction to check). */
  #messages(lang: string | undefined, mode: Mode, bye: boolean, raiseNote?: string, sent = false, expertAnswer?: string | null, nudgeSend = false, callbackNote = "", taskAsked = false, held = false, complaint = false): Msg[] {
    const name = lang && LANG_NAME[lang];
    let hint = "";
    if (name) hint += `\n\n(Answer in ${name}, because that is how I am speaking. Remember the sentence tags.)`;
    if (this.o.board !== false && this.board.items.length)
      hint += `\n\n(Private notes, never mention that they exist and never say "notes board". Everything I told you on this call before this message. ${mode === "recap" ? "Trust it over your memory of the chat; my newest message may not be on it yet, so include that too." : "It is for your reference only: do NOT read it out or recap it unless I ask. Answer only what I just said."}\n${this.board.toText()}\n)`;
    if (mode === "recap") hint += `\n\n(RECAP MODE: I gave you a lot of information, or asked you to repeat it. Take the ONE-sentence limit off for this reply. Say it back as a spoken checklist from the NOTES BOARD ONLY (never add anything from YOUR MEMORY of earlier calls or from his desktop assistant: this recap is only about what he told you on THIS call): go through EVERY item in order, one short sentence each, with the exact times, people, apps and amounts. Count them. Do not merge two items, do not drop any, and do not invent anything, including numbers. Leave out small talk and any mis-heard word that makes no sense. If an item is marked UNCLEAR, include it and ask me about that one thing instead of guessing. If I asked you to call or tell someone something, say exactly what you will tell them. Start with a short reaction, use [firm] for the items, and finish by asking in one short sentence whether anything is missing. Up to about 7 short sentences.)`;
    if (mode === "advice") hint += `\n\n(ADVICE MODE: I am asking for your opinion, an explanation, a role-play or help. Answer like a sharp, friendly expert (a salesperson, a developer or a business partner, whichever fits): give your recommendation first, then one reason, then one next step. If my plan, numbers or facts look wrong or risky, say so politely and give the better option. If I asked you to play a role, play it fully, with energy. At most 3 short sentences, about 280 characters. Do not be vague, and do not just agree with me.)`;
    if (complaint) hint += `\n\n(I am giving you FEEDBACK or a complaint about something you did or did not do. Acknowledge it honestly in ONE or TWO short sentences: say what you understood went wrong and that you will do better. Do NOT list his tasks or notes, do not defend yourself, do not make new promises about times.)`;
    if (mode === "recall") hint += `\n\n(RECALL MODE: I am asking about something from before this call. Answer ONLY from YOUR MEMORY in your instructions (and the WORK notes if I ask how the work I handed over is going): the exact names, apps, times and amounts, and when I said it. If it is not there, say you do not remember it. If I ask what is pending or open, name EVERY open item from it in a few words each (up to 5 short sentences, about 350 characters). Otherwise at most 3 short sentences, about 250 characters. No lists. Do not add advice.)`;
    if (mode === "expert") hint += expertAnswer
      ? `\n\n(EXPERT ANSWER from my desktop agent. Trust its facts, numbers and names; do not add facts of your own:\n"""${expertAnswer}"""\nSay it to me in your own words, in my language, in at most 3 short sentences (about 330 characters), no lists, no code. If it says something is uncertain, say that too.)`
      : `\n\n(My desktop agent could not answer in time. Tell me in ONE short sentence that you will have it checked and that I should ask again in a moment. Do NOT guess an answer.)`;
    if (taskAsked) hint += `\n\n(I have just asked for WORK for my agent (it builds things with ZCode). Do NOT ask permission, do NOT ask me to say "send", and do NOT ask questions: the agent picks sensible defaults for anything I did not say. In ONE or TWO short sentences in my language: say what you understood (the key points exactly as I said them: names, what to build, my rules), say your agent is starting it with ZCode and that you will call me when it is ready, and say that if I want to add anything I should say it now. Never claim it is done.)`;
    if (held) hint += `\n\n(I told you to wait or not to send. Say in ONE short sentence that you will not send it until I say so. Do not start anything.)`;
    if (nudgeSend) hint += `\n\n(I just confirmed your read-back. Now ask me ONCE, in one short sentence in the language I am speaking (Hindi in Devanagari if I speak Hindi), whether to send it to my agent now, and add the marker [[ASKSEND]] at the very end.)`;
    if (callbackNote) hint += `\n\n(I asked you to call me back and it IS scheduled for ${callbackNote}. Confirm it in ONE short sentence in my language. For a wait under an hour say it the way people do ("दो मिनट में", "in 10 minutes", "১০ মিনিটে") and NOT as a clock time; use a clock time ("शाम 6 बजे") only for a later hour. Nothing else about it.)`;
    if (sent) hint += `\n\n(I said yes: you have just SENT the work to my desktop agent and it has started. Say so in ONE short sentence in my language ([firm] tag) and that you will call me when it is ready. Do not give a time.)`;
    const jobs = this.o.jobStatus?.();
    if (jobs && !bye) hint += `\n\n(WORK I HANDED TO MY AGENT, live status. Use it only if I ask how it is going; say only what it says:\n${jobs}\n)`;
    if (bye) hint += `\n\n(I agreed to end the call. My words may be mis-heard by the speech recogniser, so do not repeat them back. Say ONE short warm goodbye in my language, with a [warm] tag, and add [[END]]. Nothing else.)`;
    // something on the board contradicts what I said earlier: check it with me, once
    const heads = raiseNote ?? (this.pendingConflicts.length && mode !== "recap" ? this.#conflictText() : "");
    if (heads && !bye) { hint += `\n\n(HEADS-UP, you noticed this and I have not confirmed it: ${heads} Mention it briefly in ONE sentence like a careful colleague and ask me which is right, then carry on.)`; }
    if (mode === "recap" && this.pendingConflicts.length && !bye) hint += `\n\n(Also check with me, after the checklist: ${this.#conflictText()})`;
    this.pendingConflicts = [];
    if (raiseNote) {
      return [...this.history, { role: "user", content: `(NOTICE for you, not from me: ${raiseNote} Say it to me now in ONE short sentence, in my language${name ? ` (${name})` : ""}, like a careful colleague who spotted something, and ask which is right. Start with a [soft] or [thinking] tag.)` }];
    }
    const out = this.history.map((m) => ({ ...m }));
    // on the goodbye turn his words are not needed (and a mis-heard word like "राखी" would be repeated back)
    if (bye) out[out.length - 1].content = "(I agreed to end the call.)" + hint; else out[out.length - 1].content += hint;
    return out;
  }

  /** The call so far as plain lines (no tags, no system prompt): for the after-call summary. */
  transcript = (): { role: "user" | "assistant"; text: string }[] =>
    this.history.filter((m) => m.role !== "system").map((m) => ({ role: m.role as "user" | "assistant", text: cleanForSpeech(m.content).replace(/\[\[[A-Z]+\]\]/g, "").trim() })).filter((l) => l.text);

  /** Take (and clear) the contradictions waiting to be raised, as one sentence. */
  takeConflictNote = (): string => { const t = this.#conflictText(); this.pendingConflicts = []; return t; };

  #conflictText(): string {
    const byItem = new Map<string, string[]>();
    for (const c of this.pendingConflicts) byItem.set(c.about, [...(byItem.get(c.about) ?? []), c.note]);
    return [...byItem.values()].map((notes) => notes.join("; ")).join(" | ");
  }

  async #speak(id: number, language: string, feed: (tts: TtsStream) => Promise<void>, t0 = performance.now()): Promise<void> {
    const base = deliveryFor("warm", this.#basePace, temperatureFor(language, this.o.tuning));
    const tts = new TtsStream(this.o.apiKey, language, this.#speaker, base.pace, { ...this.o.tuning, temperature: base.temperature });
    this.#tts = tts;
    let gotAudio = false;
    tts.on("error", (e) => console.error(e.message));
    tts.on("audio", (pcm: Float32Array) => {
      if (id !== this.#turn) return;
      if (!gotAudio) { gotAudio = true; this.speaking = true; this.speakingSince = Date.now(); this.lastLatencyMs = Math.round(performance.now() - t0); }
      this.o.voice.pushAudio(pcm);
    });
    try {
      await feed(tts);
      const deadline = Date.now() + 20_000;
      while (id === this.#turn && !tts.allDone() && !tts.closed && Date.now() < deadline) await new Promise((r) => setTimeout(r, 40));
    } catch (e: any) {
      if (e?.name !== "AbortError") throw e;
    } finally {
      if (id === this.#turn) { this.speaking = false; tts.cancel(); }
    }
  }
}

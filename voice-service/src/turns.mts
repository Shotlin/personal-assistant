// Turn-taking: decides WHEN Sani should answer and WHEN the user is really interrupting.
// Built from Sarvam's voice-agent guidance (raise the wait for speakers who pause mid-sentence;
// require real words before yielding; short acknowledgements like "haan" must not interrupt)
// plus what the real calls showed: the user speaks in fragments with pauses of ~1 s.

// Words that mean "I am not finished": a sentence ending on one of these continues.
const HANGING = new Set([
  // Hindi
  "का", "की", "के", "में", "से", "को", "पर", "और", "तो", "कि", "जो", "ना", "न", "या", "भी", "ही", "एक", "कोई", "बहुत",
  "उनका", "उसका", "उसकी", "उनकी", "मेरा", "मेरी", "मेरे", "तेरा", "तुम्हारा", "वो", "ये", "यह", "वह", "लेकिन", "मगर", "फिर",
  "क्योंकि", "यानी", "मतलब", "अगर", "जब", "तब", "तक", "लिए", "साथ", "बारे", "बाद", "पहले", "ताकि", "जैसे", "वाला", "वाली", "वाले",
  "आजकल", "अभी", "इसमें", "उसमें", "इसका", "इसकी", "हमारा", "हमारी",
  // Bengali
  "আর", "এবং", "কিন্তু", "যে", "তো", "মানে", "তাহলে", "কারণ", "বা", "ও", "এর", "এই", "ওই", "আমার", "তোমার", "জন্য", "সাথে", "থেকে",
  "মধ্যে", "যদি", "তখন", "আবার", "একটা", "কোনো", "ওর", "তার", "তাদের", "আমাদের", "যখন", "সেটা", "এটা",
  // English
  "and", "but", "so", "the", "a", "an", "to", "of", "for", "with", "because", "that", "which", "if", "or", "my", "your", "i", "we",
  "then", "also", "like", "is", "are", "in", "on", "at", "about", "from", "have", "has", "uh", "um",
]);
// Complete answers even when they are one word.
const SHORT_OK = new Set(["हाँ", "हां", "नहीं", "ठीक", "ओके", "ok", "okay", "yes", "no", "haan", "nahi", "हম্", "হ্যাঁ", "না", "ঠিক", "আচ্ছা", "अच्छा", "बस", "सही", "done", "stop"]);
// Acknowledgements that must NOT interrupt Sani while it speaks.
const BACKCHANNEL = new Set([
  "तो", "और", "मतलब", "यानी", "सही", "है", "हैं", "बिल्कुल", "पक्का", "correct", "right", "sure", "ना", "न", "बस", "तब", "फिर", "देखो", "so", "and", "তো", "আর", "মানে", "সঠিক", "ঠিকই", "হ্যাঁ", "হ্যা", "uh", "um", "kh", "उह", "अं", "आं","हाँ", "हां", "हम्म", "हम", "हmm", "ओके", "ok", "okay", "अच्छा", "ठीक", "hmm", "yes", "yeah", "haan", "हुम", "হ্যাঁ", "হুম", "আচ্ছা", "ঠিক", "जी", "जी हाँ", "oh", "ah", "ओह", "ओ"]);
// Commands that interrupt at once, even as a single word.
const STOP_WORDS = ["रुको", "रुक", "ruko", "stop", "wait", "थामो", "থামো", "দাঁড়াও", "एक मिनट", "रुकिए", "hold on"];

const clean = (w: string) => w.replace(/[.,!?।॥…"'“”()\-–—:;]+/g, "").toLowerCase();
const wordsOf = (t: string) => t.split(/\s+/).map(clean).filter(Boolean);

/** How long to wait after the last final transcript before answering (ms). */
export function patienceMs(text: string): number {
  const t = text.trim();
  const w = wordsOf(t);
  if (!w.length) return 1500;
  const last = w[w.length - 1];
  if (/[?？]\s*$/.test(t)) return 600;                       // a question is finished
  if (HANGING.has(last)) return 1800;                        // "…है उनका।" the thought continues
  if (w.length === 1) return SHORT_OK.has(last) ? 600 : 1400;
  if (w.length === 2 && !/[.।!]\s*$/.test(t)) return 1300;   // tiny fragment without an ending
  if (/[.।!]\s*$/.test(t)) return w.length >= 4 ? 650 : 900; // finished sentence
  return 1100;                                               // no ending mark: probably still talking
}

/** Number of words that are more than an acknowledgement. */
export function informativeWords(text: string): number {
  return wordsOf(text).filter((w) => !BACKCHANNEL.has(w)).length;
}
export const isStopCommand = (text: string): boolean => {
  const t = text.toLowerCase();
  return STOP_WORDS.some((s) => t.includes(s));
};

export type TurnOptions = {
  isSpeaking: () => boolean;           // is Sani talking (or has speech queued)?
  speakingSince: () => number;
  onTurn: (text: string, lang?: string) => void;
  onInterrupt: (why: string) => void;
  log?: (msg: string) => void;
  minWordsToInterrupt?: number;        // while Sani speaks, this many real words take the turn (default 2)
  sustainedMs?: number;                // ...or speech lasting this long (default 900)
};

export class TurnManager {
  #buf: string[] = [];
  #lang: string | undefined;
  #timer: NodeJS.Timeout | null = null;
  #barge: NodeJS.Timeout | null = null;
  #userSpeaking = false;
  #interrupted = false;
  constructor(private readonly o: TurnOptions) {}

  /** True while he is speaking or a turn of his is still waiting to be answered. */
  get active(): boolean { return this.#userSpeaking || this.#timer !== null || this.#buf.length > 0; }

  speechStart = (): void => {
    this.#userSpeaking = true;
    if (this.#timer) { clearTimeout(this.#timer); this.#timer = null; this.o.log?.("user is continuing: still waiting"); }
    this.#interrupted = false;
    if (this.o.isSpeaking() && Date.now() - this.o.speakingSince() > 400) {
      // do not yield to a cough, an echo or "haan": wait for real words or sustained speech
      this.#barge = setTimeout(() => this.#interrupt("speech kept going"), this.o.sustainedMs ?? 900);
    }
  };

  partial = (text: string): void => {
    if (!this.o.isSpeaking() || this.#interrupted || !text) return;
    if (isStopCommand(text) || informativeWords(text) >= (this.o.minWordsToInterrupt ?? 2)) this.#interrupt(`words: "${text}"`);
  };

  speechEnd = (): void => {
    this.#userSpeaking = false;
    if (this.#barge) { clearTimeout(this.#barge); this.#barge = null; }
  };

  final = (text: string, lang?: string): void => {
    const t = (text ?? "").trim();
    if (this.#barge) { clearTimeout(this.#barge); this.#barge = null; }
    if (!t) return;
    // a bare acknowledgement while Sani is talking is not a turn
    if (this.o.isSpeaking() && !this.#interrupted && informativeWords(t) === 0 && !isStopCommand(t)) {
      this.o.log?.(`ignored acknowledgement while Sani speaks: "${t}"`);
      return;
    }
    if (this.o.isSpeaking() && !this.#interrupted) this.#interrupt(`final: "${t}"`);
    this.#buf.push(t);
    this.#lang = lang ?? this.#lang;
    this.#arm();
  };

  #arm(): void {
    if (this.#timer) clearTimeout(this.#timer);
    const text = this.#buf.join(" ");
    const wait = patienceMs(text);
    this.o.log?.(`waiting ${wait} ms to see if you continue: "${text.slice(-50)}"`);
    this.#timer = setTimeout(() => this.#flush(), wait);
  }

  #flush(): void {
    this.#timer = null;
    if (this.#userSpeaking) { this.#timer = setTimeout(() => this.#flush(), 400); return; } // still talking: look again shortly
    const text = this.#buf.join(" ").trim();
    this.#buf = [];
    if (text) this.o.onTurn(text, this.#lang);
  }

  #interrupt(why: string): void {
    if (this.#interrupted) return;
    this.#interrupted = true;
    if (this.#barge) { clearTimeout(this.#barge); this.#barge = null; }
    this.o.log?.(`interrupt (${why})`);
    this.o.onInterrupt(why);
  }

  stop = (): void => { if (this.#timer) clearTimeout(this.#timer); if (this.#barge) clearTimeout(this.#barge); };
}

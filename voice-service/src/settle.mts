// "Starting it" without an extra yes, but without rushing either: when he asks for work, Shubh says what he understood and the job is sent a few
// seconds later. If he keeps talking (adds details) the wait restarts, so everything goes in ONE brief; if he says "wait / don't" it is cancelled;
// if he hangs up it is sent at once. Pure timing logic, testable with fast timers.
export class SendSettler {
  #timer: NodeJS.Timeout | null = null;
  #firstAt = 0;
  pending = false;
  constructor(private readonly send: () => void, private readonly o: { quietMs?: number; maxMs?: number } = {}) {}
  get #quiet() { return this.o.quietMs ?? 7000; }
  get #max() { return this.o.maxMs ?? 45_000; }
  #schedule() {
    if (this.#timer) clearTimeout(this.#timer);
    const left = this.#max - (Date.now() - this.#firstAt);
    this.#timer = setTimeout(() => { this.#timer = null; if (this.pending) { this.pending = false; this.send(); } }, Math.max(0, Math.min(this.#quiet, left)));
  }
  /** He asked for work: start (or restart) the quiet period. */
  arm() { if (!this.pending) { this.pending = true; this.#firstAt = Date.now(); } this.#schedule(); }
  /** A turn finished and something is still waiting: another quiet period begins. */
  touch() { if (this.pending) this.#schedule(); }
  /** He started talking: never send while he is mid-sentence. */
  pause() { if (this.#timer) { clearTimeout(this.#timer); this.#timer = null; } }
  cancel() { this.pause(); this.pending = false; }
  /** The call is over (or he said "send"): send now. */
  flush() { if (this.pending) { this.pause(); this.pending = false; this.send(); } }
}

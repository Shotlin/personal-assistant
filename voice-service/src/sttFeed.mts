// Steady 20 ms clock for the listener. WhatsApp may send no audio while you are silent (to save data),
// but Sarvam can only tell that you finished a sentence by hearing silence. So: real frames when we
// have them, zeros when we don't, always 50 frames a second.
export class SteadyFeed {
  #q: Float32Array[] = [];
  #timer: NodeJS.Timeout | null = null;
  realFrames = 0;
  filledFrames = 0;
  skippedFrames = 0;
  #lastReal = 0;
  constructor(private readonly sink: (pcm: Float32Array) => void, private readonly frame = 320, private readonly tailMs = 1500) {}
  start = (): void => {
    if (this.#timer) return;
    this.#timer = setInterval(() => {
      // if frames arrive in a burst, drain a little faster so we never fall behind the call
      const n = this.#q.length > 8 ? 3 : this.#q.length > 3 ? 2 : 1;
      for (let i = 0; i < n; i++) {
        const f = this.#q.shift();
        if (f) { this.realFrames++; this.#lastReal = Date.now(); this.sink(f); }
        else if (i === 0) {
          // silence is only sent right after speech, so Sarvam can tell the sentence ended; idle time is not billed
          if (Date.now() - this.#lastReal < this.tailMs) { this.filledFrames++; this.sink(new Float32Array(this.frame)); }
          else this.skippedFrames++;
        }
      }
    }, 20);
  };
  add = (pcm: Float32Array): void => {
    for (let o = 0; o + this.frame <= pcm.length; o += this.frame) this.#q.push(pcm.slice(o, o + this.frame));
  };
  stop = (): void => { if (this.#timer) clearInterval(this.#timer); this.#timer = null; };
}

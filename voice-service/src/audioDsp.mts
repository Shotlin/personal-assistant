// Audio helpers for the phone path: 24 kHz (Bulbul's native rate) -> 16 kHz (what a WhatsApp call carries),
// plus a gentle level lift with a soft limiter so the voice sounds fuller over the phone.

/** Streaming 24k -> 16k resampler: windowed-sinc, 33 taps, cutoff at the 16 kHz Nyquist (8 kHz). */
export class Resample24to16 {
  static readonly HALF = 16;
  #buf = new Float32Array(Resample24to16.HALF); // leading zeros = silence before the first sample
  #pos = Resample24to16.HALF;                    // fractional read position inside #buf
  push(x: Float32Array): Float32Array {
    const H = Resample24to16.HALF;
    const merged = new Float32Array(this.#buf.length + x.length);
    merged.set(this.#buf, 0); merged.set(x, this.#buf.length);
    const out: number[] = [];
    const fc = 16000 / 24000;
    while (this.#pos + H < merged.length) {
      const c = Math.floor(this.#pos);
      let acc = 0;
      for (let k = c - H + 1; k <= c + H; k++) {
        const d = this.#pos - k;
        const sinc = d === 0 ? 1 : Math.sin(Math.PI * fc * d) / (Math.PI * fc * d);
        const win = 0.5 * (1 + Math.cos((Math.PI * d) / H));
        acc += merged[k] * fc * sinc * win;
      }
      out.push(acc);
      this.#pos += 1.5;
    }
    const drop = Math.max(0, Math.floor(this.#pos) - H);
    this.#buf = merged.slice(drop);
    this.#pos -= drop;
    return Float32Array.from(out);
  }
}

/** Lift the level, then softly limit anything above 0.7 so loud peaks never clip. */
export function lift(pcm: Float32Array, gain: number): Float32Array {
  const T = 0.7;
  for (let i = 0; i < pcm.length; i++) {
    const v = pcm[i] * gain, a = Math.abs(v);
    pcm[i] = a <= T ? v : Math.sign(v) * (T + (1 - T) * Math.tanh((a - T) / (1 - T)));
  }
  return pcm;
}

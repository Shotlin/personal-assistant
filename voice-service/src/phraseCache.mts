// Fixed lines (greeting, "I'll pass it on", ...) are synthesized once and replayed from disk afterwards:
// Bulbul bills per character, so a repeated line costs nothing the second time.
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";

const DIR = "var/tts-cache";
const file = (key: string) => `${DIR}/${createHash("sha1").update(key).digest("hex").slice(0, 20)}.f32`;

export function cacheGet(key: string): Float32Array | null {
  const f = file(key);
  if (!existsSync(f)) return null;
  const b = readFileSync(f);
  return new Float32Array(b.buffer.slice(b.byteOffset, b.byteOffset + b.length));
}
export function cachePut(key: string, pcm: Float32Array): void {
  mkdirSync(DIR, { recursive: true });
  writeFileSync(file(key), Buffer.from(pcm.buffer, pcm.byteOffset, pcm.byteLength));
}

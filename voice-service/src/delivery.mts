// Delivery = how a sentence SOUNDS. Bulbul v3 has no SSML and no emotion tags of its own, so the model
// writes a bracket tag at the start of a sentence ("[excited] ..."), we strip it from the text and
// translate it into the two controls Bulbul does offer, changed per sentence on the same connection:
//   pace        0.5-2.0   (1.0 = native; 0.8-0.9 = relaxed/measured; 1.1+ = brisk/energetic)
//   temperature 0.01-1.0  (low = flat/consistent; 0.7-0.8 = warm, expressive; 0.9-1.0 = highly expressive)
// Source: docs.sarvam.ai/api/api-guides-tutorials/text-to-speech/best-practices (sections 1, 2, 9).

export type Delivery = { pace: number; temperature: number };

/** pace is a multiplier on the base pace; temp is an offset from the language's base temperature. */
const STYLES: Record<string, { pace: number; temp: number }> = {
  neutral:   { pace: 1.00, temp:  0.00 },
  warm:      { pace: 1.00, temp: -0.03 },
  excited:   { pace: 1.12, temp: +0.06 },
  playful:   { pace: 1.08, temp: +0.05 },
  surprised: { pace: 1.08, temp: +0.06 },
  thinking:  { pace: 0.86, temp: -0.12 },
  soft:      { pace: 0.90, temp: -0.18 },
  calm:      { pace: 0.92, temp: -0.22 },
  firm:      { pace: 0.98, temp: -0.30 },
};
const ALIASES: Record<string, string> = {
  energetic: "excited", happy: "excited", cheerful: "excited", enthusiastic: "excited", joy: "excited",
  friendly: "warm", kind: "warm", caring: "warm", natural: "warm", casual: "warm",
  curious: "thinking", hesitant: "thinking", unsure: "thinking", slow: "thinking", pause: "thinking", thoughtful: "thinking",
  sad: "soft", sorry: "soft", gentle: "soft", empathetic: "soft", tired: "soft", apologetic: "soft", sympathetic: "soft",
  serious: "calm", relaxed: "calm", steady: "calm", reassuring: "calm",
  confident: "firm", confirm: "firm", decisive: "firm", sure: "firm",
  amused: "playful", joking: "playful", teasing: "playful",
  shocked: "surprised", wow: "surprised",
};
export const TAG_NAMES = Object.keys(STYLES).filter((k) => k !== "neutral");

export const normalizeTag = (raw: string): string => {
  const t = raw.trim().toLowerCase().replace(/[ _-]+/g, "");
  return STYLES[t] ? t : ALIASES[t] ?? "neutral";
};

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
export function deliveryFor(tag: string, basePace: number, baseTemperature: number): Delivery {
  const s = STYLES[normalizeTag(tag)];
  return { pace: +clamp(basePace * s.pace, 0.5, 2).toFixed(2), temperature: +clamp(baseTemperature + s.temp, 0.01, 1).toFixed(2) };
}

/** A leading "[tag]" (not "[[END]]"). */
export const LEADING_TAG = /^\s*\[(?!\[)([A-Za-z][A-Za-z _-]{1,20})\]\s*/;
export const ANY_TAG = /\[(?!\[)([A-Za-z][A-Za-z _-]{1,20})\]/;

/** Remove everything that should never be read aloud: tags, markdown emphasis, emoji. */
export const cleanForSpeech = (s: string): string =>
  s.replace(/\[(?!\[)[A-Za-z][A-Za-z _-]{1,20}\]/g, "").replace(/[*_`#~]+/g, "").replace(/\p{Extended_Pictographic}/gu, "").replace(/[ \t]{2,}/g, " ").trim();

// Names the character says about itself, per script (Bulbul reads native script best).
export const NAME_SCRIPTS: Record<string, { bn: string; hi: string }> = {
  shubh: { bn: "শুভ", hi: "शुभ" }, pooja: { bn: "পূজা", hi: "पूजा" }, priya: { bn: "প্রিয়া", hi: "प्रिया" },
  rehan: { bn: "রেহান", hi: "रेहान" }, ratan: { bn: "রতন", hi: "रतन" }, ashutosh: { bn: "আশুতোষ", hi: "आशुतोष" },
  ishita: { bn: "ইশিতা", hi: "इशिता" }, roopa: { bn: "রূপা", hi: "रूपा" }, suhani: { bn: "সুহানি", hi: "सुहानी" },
  kabir: { bn: "কবীর", hi: "कबीर" }, rahul: { bn: "রাহুল", hi: "राहुल" }, rohan: { bn: "রোহান", hi: "रोहन" },
  amit: { bn: "অমিত", hi: "अमित" }, aditya: { bn: "আদিত্য", hi: "आदित्य" }, anand: { bn: "আনন্দ", hi: "आनंद" },
  ritu: { bn: "ঋতু", hi: "ऋतु" }, neha: { bn: "নেহা", hi: "नेहा" }, simran: { bn: "সিমরন", hi: "सिमरन" },
  kavya: { bn: "কাব্য", hi: "काव्या" }, mani: { bn: "মণি", hi: "मणि" }, advait: { bn: "অদ্বৈত", hi: "अद्वैत" },
};
export const characterName = (speaker: string) => speaker.charAt(0).toUpperCase() + speaker.slice(1);

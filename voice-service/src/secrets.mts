// Secret screening for everything that is stored or loaded as memory. Same rules as the Python policy in
// src/assistant/memory/policy.py (explicit secret assignment, bearer token, provider key), plus the spoken forms a
// phone call produces ("my OTP is 4821", "password is ..."), because a speech transcript has no "key=value" shape.
const RULES: [RegExp, string][] = [
  [/\b(api[_-]?key|apikey|access[_-]?token|refresh[_-]?token|password|passwd|secret|otp|one[- ]time (?:code|password|url))\b\s*[=:]\s*\S+/gi, "explicit secret assignment"],
  [/\bbearer\s+[A-Za-z0-9._~+/=-]{10,}/gi, "bearer-style token"],
  [/\b(?:sk|pk)[_-][A-Za-z0-9_-]{16,}\b/g, "provider-style API key"],
  [/\b(otp|password|passcode|pin|cvv)\b[^.\n]{0,24}?\b\d{3,}\b/gi, "spoken code or password"],
  [/(ओटीपी|पासवर्ड|पिन|ওটিপি|পাসওয়ার্ড)[^.\n।]{0,24}?\d{3,}/g, "spoken code or password"],
];

/** Why this text must not be stored, or null when it looks clean. */
export function containsSecret(text: string): string | null {
  for (const [re, why] of RULES) { re.lastIndex = 0; if (re.test(text)) return why; }
  return null;
}

/** The text with any secret-looking span replaced, so the rest of a note can still be kept. */
export function screen(text: string): string {
  let out = text;
  for (const [re] of RULES) out = out.replace(re, "[hidden]");
  return out;
}

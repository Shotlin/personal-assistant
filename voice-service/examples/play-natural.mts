// Replays the files made by `npm run natural` (no API calls, free).
import { execFileSync } from "node:child_process";
import { readdirSync } from "node:fs";
const files = readdirSync("var/natural").filter((f) => f.endsWith(".wav")).sort();
const label = (f: string) => f.includes("flat") ? "FLAT (same words, one fixed delivery)" : "EXPRESSIVE (tags change pace + feeling per sentence)";
for (const f of files) { console.log(`  ${f.padEnd(20)} ${label(f)}`); execFileSync("afplay", [`var/natural/${f}`]); await new Promise((r) => setTimeout(r, 700)); }

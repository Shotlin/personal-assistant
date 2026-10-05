// A short map of where things are, handed to the Deep Agent so it never has to ask "where is the file?": the folders it may work in
// (from Sani's own settings) with their top two levels, names only (no contents, no hidden or dependency folders).
import { existsSync, readdirSync, statSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { saniDir } from "./sani.mts";

const SKIP = /^(\.|node_modules$|dist$|build$|__pycache__$|\.git$|venv$|\.venv$|target$|\.next$)/;

export function allowedFolders(dir = saniDir()): string[] {
  try { const s = JSON.parse(readFileSync(join(dir, "settings.json"), "utf8")); return (Array.isArray(s.claude_code_dirs) ? s.claude_code_dirs : []).filter((d: string) => existsSync(d)); } catch { return []; }
}

export function workspaceMap(folders = allowedFolders(), o: { maxLines?: number } = {}): string {
  const lines: string[] = []; const max = o.maxLines ?? 60;
  const walk = (dir: string, depth: number, indent: string) => {
    let names: string[] = []; try { names = readdirSync(dir).filter((n) => !SKIP.test(n)).sort(); } catch { return; }
    for (const n of names) {
      if (lines.length >= max) return;
      const p = join(dir, n); let isDir = false; try { isDir = statSync(p).isDirectory(); } catch { continue; }
      lines.push(`${indent}${n}${isDir ? "/" : ""}`);
      if (isDir && depth < 2) walk(p, depth + 1, indent + "  ");
    }
  };
  for (const f of folders) { lines.push(`${f}/`); walk(f, 1, "  "); if (lines.length >= max) { lines.push("  ..."); break; } }
  return lines.join("\n");
}

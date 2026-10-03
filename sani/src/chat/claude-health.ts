import type { ClaudeCodeStatus } from "@/lib/tauri";

/**
 * One answer to "is Claude Code working for Sani?", shown as a dot:
 * green = ready to use, amber = signed in, one setup step left, red = something needs fixing, grey = off by choice,
 * busy = a sign-in is in progress. The words say what to do about red.
 */
export type HealthTone = "green" | "amber" | "red" | "grey" | "busy";

export interface ClaudeHealth {
  tone: HealthTone;
  title: string;
  detail: string;
}

export function claudeHealth(status: ClaudeCodeStatus | null, loading = false): ClaudeHealth {
  if (!status) {
    return loading
      ? { tone: "grey", title: "Checking…", detail: "" }
      : { tone: "red", title: "Can't check Claude Code", detail: "Sani's engine isn't answering. Restart Sani." };
  }
  const claude = status.claude;
  if (status.login?.state === "waiting") {
    return { tone: "busy", title: "Signing in…", detail: "Finish in your browser." };
  }
  if (!claude.installed) {
    return { tone: "red", title: "Claude Code isn't installed", detail: "Install it, then sign in." };
  }
  if (!claude.signed_in) {
    return { tone: "red", title: "Not signed in", detail: "Sign in to let Sani use Claude Code." };
  }
  if (!status.enabled) {
    return { tone: "grey", title: "Signed in, switched off", detail: "Turn it on to let Sani use it." };
  }
  if (status.folders.length === 0) {
    return { tone: "amber", title: "Signed in — add a project folder", detail: "Sani only works in folders you choose." };
  }
  return { tone: "green", title: "Connected", detail: "Signed in and ready." };
}

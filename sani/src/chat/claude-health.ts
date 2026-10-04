import type { ClaudeCodeStatus, ZCodeStatus } from "@/lib/tauri";

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

/**
 * The same dot for ZCode, but green only when every link in the chain is confirmed:
 * installed, signed in inside the ZCode app, window readable and as expected, switched on, a folder set.
 */
export function zcodeHealth(status: ZCodeStatus | null, loading = false): ClaudeHealth {
  if (!status) {
    return loading
      ? { tone: "grey", title: "Checking…", detail: "" }
      : { tone: "red", title: "Can't check ZCode", detail: "Sani's engine isn't answering. Restart Sani." };
  }
  const cdp = status.cdp;
  if (cdp?.job.state === "reading") {
    return { tone: "busy", title: "Reading ZCode…", detail: "ZCode closes and reopens for a minute." };
  }
  if (!status.zcode.installed) {
    return { tone: "red", title: "ZCode isn't installed", detail: "Install the ZCode app, then sign in." };
  }
  if (status.extra.app_signed_in === false) {
    return { tone: "red", title: "Signed out in ZCode", detail: "Open ZCode and sign in. Sani never types credentials." };
  }
  const change = status.account_change?.change;
  if (change?.kind === "signed_out") {
    return { tone: "red", title: "Signed out in ZCode", detail: "Open ZCode and sign in. Sani refuses runs until then." };
  }
  if (change) {
    return { tone: "amber", title: describeChange(change), detail: "Sani is refreshing what it knows about your plans." };
  }
  if (status.account_change?.stale) {
    return { tone: "amber", title: "Out of date", detail: "ZCode's sign-in changed. Press “Read now” to refresh." };
  }
  if (status.control && !status.control.enabled) {
    return {
      tone: "amber",
      title: "Turn on ZCode control",
      detail: "Sani needs your OK to close and reopen ZCode so it can read and use it.",
    };
  }
  if (!cdp?.contract) {
    return { tone: "amber", title: "Not read yet", detail: "Press “Read now” so Sani can see your account and balances." };
  }
  if (!cdp.contract.ok) {
    return {
      tone: "red",
      title: "ZCode's window looks different",
      detail: "ZCode may have updated or is on another screen. Sani won't click on a guess.",
    };
  }
  if (cdp.job.state === "refused") {
    return { tone: "red", title: "Couldn't read ZCode", detail: cdp.job.message || "Try again." };
  }
  if (!status.enabled) {
    return { tone: "grey", title: "Ready, switched off", detail: "Turn it on to let Sani use ZCode." };
  }
  if (status.folders.length === 0) {
    return { tone: "amber", title: "Add a project folder", detail: "Sani only works in folders you choose." };
  }
  return { tone: "green", title: "Connected", detail: "Signed in inside ZCode and ready." };
}

/** "Account changed: A → B" and its siblings, in plain words. */
export function describeChange(change: NonNullable<NonNullable<ZCodeStatus["account_change"]>["change"]>): string {
  if (change.kind === "switched") return `Account changed: ${change.from || "?"} → ${change.to || "?"}`;
  if (change.kind === "signed_in") return `Signed in to ZCode as ${change.to || "?"}`;
  if (change.kind === "signed_out") return `Signed out of ZCode${change.from ? ` (was ${change.from})` : ""}`;
  const added = change.plans_added.join(", ");
  const removed = change.plans_removed.join(", ");
  return `Plans changed${added ? ` · added ${added}` : ""}${removed ? ` · ended ${removed}` : ""}`;
}

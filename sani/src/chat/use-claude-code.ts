import { useCallback, useEffect } from "react";
import { create } from "zustand";
import {
  claudeCodeAuth,
  claudeCodeStatus,
  focusMain,
  onAgentDone,
  type ClaudeCodeStatus,
} from "@/lib/tauri";

/**
 * One shared, live picture of the user's Claude Code for the whole window, so
 * the sidebar dot, the composer chips and the Settings card can never disagree.
 * It is polled slowly, quickly while a sign-in is in progress, and after every
 * finished turn. A failed read just means "unknown", never an error screen.
 */
interface ClaudeStore {
  status: ClaudeCodeStatus | null;
  loading: boolean;
  error: string;
  apply: (next: ClaudeCodeStatus) => void;
  setError: (error: string) => void;
}

const useStore = create<ClaudeStore>((set, get) => ({
  status: null,
  loading: true,
  error: "",
  apply: (next) => {
    // A browser sign-in that just finished brings Sani back to the front.
    const before = get().status?.login?.state;
    if (before === "waiting" && next.login?.state === "succeeded") {
      void focusMain().catch(() => undefined);
    }
    set({ status: next, loading: false });
  },
  setError: (error) => set({ error }),
}));

async function refreshNow(): Promise<void> {
  try {
    useStore.getState().apply(await claudeCodeStatus());
  } catch {
    useStore.setState({ status: null, loading: false });
  }
}

// A single poller, alive while at least one component is using the hook.
let users = 0;
let timer: number | undefined;
let stopTurnListener: (() => void) | undefined;
let slowMs = 30_000;

function schedule(): void {
  window.clearTimeout(timer);
  const waiting = useStore.getState().status?.login?.state === "waiting";
  timer = window.setTimeout(async () => {
    await refreshNow();
    if (users > 0) schedule();
  }, waiting ? 1500 : slowMs);
}

function attach(pollMs: number): void {
  users += 1;
  slowMs = Math.min(slowMs, pollMs);
  if (users > 1) return;
  void refreshNow().then(schedule);
  void onAgentDone(() => void refreshNow()).then((off) => {
    if (users > 0) stopTurnListener = off;
    else off();
  });
}

function detach(): void {
  users -= 1;
  if (users > 0) return;
  window.clearTimeout(timer);
  stopTurnListener?.();
  stopTurnListener = undefined;
}

export function useClaudeCode(pollMs = 30_000) {
  const status = useStore((state) => state.status);
  const loading = useStore((state) => state.loading);
  const error = useStore((state) => state.error);

  useEffect(() => {
    attach(pollMs);
    return detach;
  }, [pollMs]);

  const act = useCallback(async (action: "login" | "code" | "cancel" | "logout", code?: string) => {
    useStore.getState().setError("");
    try {
      const next = await claudeCodeAuth(action, code);
      if (next.error) useStore.getState().setError(next.error);
      else useStore.getState().apply(next);
      schedule();
    } catch (reason) {
      useStore.getState().setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, []);

  return { status, loading, error, refresh: refreshNow, act };
}

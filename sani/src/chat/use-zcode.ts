import { useCallback, useEffect } from "react";
import { create } from "zustand";
import { focusMain, openSignInLink, zcodeAuth, zcodeStatus, type ClaudeCodeStatus, type ZCodeStatus } from "@/lib/tauri";

/**
 * A live picture of the user's ZCode, shared by the whole window. Same idea as
 * `use-claude-code`: polled slowly, quickly while a sign-in is in progress. A
 * failed read just means "unknown". The status is also exposed in the Claude Code
 * shape (`asClaude`) so the same health dot logic can be reused.
 */
interface Store {
  status: ZCodeStatus | null;
  loading: boolean;
  error: string;
  apply: (next: ZCodeStatus) => void;
  setError: (error: string) => void;
}

const useStore = create<Store>((set, get) => ({
  status: null,
  loading: true,
  error: "",
  apply: (next) => {
    const before = get().status?.login?.state;
    if (before === "waiting" && next.login?.state === "succeeded") {
      void focusMain().catch(() => undefined);
    }
    // ZCode starts its sign-in without opening a browser, so Sani opens the link once it appears.
    if (next.login?.state === "waiting" && next.login.url && get().status?.login?.url !== next.login.url) {
      void openSignInLink(next.login.url).catch(() => undefined);
    }
    set({ status: next, loading: false });
  },
  setError: (error) => set({ error }),
}));

async function refreshNow(): Promise<void> {
  try {
    useStore.getState().apply(await zcodeStatus());
  } catch {
    useStore.setState({ status: null, loading: false });
  }
}

let users = 0;
let timer: number | undefined;

function schedule(): void {
  window.clearTimeout(timer);
  const current = useStore.getState().status;
  const waiting = current?.login?.state === "waiting";
  const reading = current?.cdp?.job.state === "reading";
  timer = window.setTimeout(async () => {
    await refreshNow();
    if (users > 0) schedule();
  }, waiting ? 1500 : reading ? 3000 : 30_000);
}

/** The ZCode status in the shape `claudeHealth` understands. */
export function asClaude(status: ZCodeStatus | null): ClaudeCodeStatus | null {
  if (!status) return null;
  return { ...status, claude: status.zcode, usage: { rates: {}, last_run: {}, context_percent: null } };
}

export function useZCode() {
  const status = useStore((state) => state.status);
  const loading = useStore((state) => state.loading);
  const error = useStore((state) => state.error);

  useEffect(() => {
    users += 1;
    if (users === 1) void refreshNow().then(schedule);
    return () => {
      users -= 1;
      if (users === 0) window.clearTimeout(timer);
    };
  }, []);

  const act = useCallback(
    async (
      action: "login" | "cancel" | "logout" | "select" | "read" | "control" | "ack_change",
      choice?: { provider?: string; model?: string; enabled?: boolean },
    ) => {
      useStore.getState().setError("");
      try {
        const next = await zcodeAuth(action, choice);
        if (next.error) useStore.getState().setError(next.error);
        else useStore.getState().apply(next);
        schedule();
      } catch (reason) {
        useStore.getState().setError(reason instanceof Error ? reason.message : String(reason));
      }
    },
    [],
  );

  return { status, loading, error, refresh: refreshNow, act };
}

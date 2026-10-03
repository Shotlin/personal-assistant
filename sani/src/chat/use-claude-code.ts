import { useCallback, useEffect, useState } from "react";
import { claudeCodeStatus, onAgentDone, type ClaudeCodeStatus } from "@/lib/tauri";

/**
 * Live facts about the user's Claude Code: installed, signed in, folders and
 * the usage numbers it last reported. Refreshed on a slow timer and after every
 * finished turn; a failure just means "unknown", never an error screen.
 */
export function useClaudeCode(pollMs = 30_000) {
  const [status, setStatus] = useState<ClaudeCodeStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(() => {
    setLoading(true);
    return claudeCodeStatus()
      .then(setStatus)
      .catch(() => setStatus(null))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), pollMs);
    const off = onAgentDone(() => void refresh());
    return () => {
      window.clearInterval(timer);
      void off.then((fn) => fn()).catch(() => undefined);
    };
  }, [refresh, pollMs]);

  return { status, loading, refresh };
}

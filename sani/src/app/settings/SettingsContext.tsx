import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  applyAiSettings,
  getFullSettings,
  listMics,
  coreAgents,
  onSettingsChanged,
  saveSettings,
  setAgentMode,
  storeProviderKey,
  type FullSettingsSnapshot,
} from "../../lib/tauri";

interface SettingsContextValue {
  snapshot: FullSettingsSnapshot | null;
  microphones: string[];
  agents: import("../../lib/tauri").AgentDescriptor[];
  agentsAvailable: boolean;
  error: string;
  refresh: () => Promise<void>;
  saveGeneral: (patch: Parameters<typeof saveSettings>[0]) => Promise<void>;
  saveAi: (patch: Parameters<typeof applyAiSettings>[0]) => Promise<void>;
  saveProviderKey: (provider: "openrouter" | "typesafe", candidate: string) => Promise<void>;
  selectAgent: (agentId: string) => Promise<void>;
}

const SettingsContext = createContext<SettingsContextValue | null>(null);

/**
 * The implementation is deliberately mounted once per renderer. Tauri main
 * and overlay windows are different WebViews, so this Context cannot and does
 * not pretend to share React memory across them; native snapshot/events do.
 */
export function SettingsProvider({ children }: { children: ReactNode }) {
  const [snapshot, setSnapshot] = useState<FullSettingsSnapshot | null>(null);
  const [microphones, setMicrophones] = useState<string[]>([]);
  const [agents, setAgents] = useState<import("../../lib/tauri").AgentDescriptor[]>([]);
  const [agentsAvailable, setAgentsAvailable] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    const [next, mics, roster] = await Promise.all([getFullSettings(), listMics(), coreAgents().catch(() => null)]);
    setSnapshot(next);
    setMicrophones(mics);
    setAgents(roster ?? []);
    setAgentsAvailable(roster !== null);
  }, []);

  useEffect(() => {
    void refresh().catch((reason) => setError(String(reason)));
    let unlisten: (() => void) | undefined;
    void onSettingsChanged((next) => setSnapshot(next)).then((cleanup) => { unlisten = cleanup; });
    return () => unlisten?.();
  }, [refresh]);

  const mutate = useCallback(async (operation: () => Promise<unknown>) => {
    setError("");
    try {
      await operation();
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      await refresh().catch(() => undefined);
      throw reason;
    }
  }, [refresh]);

  const value = useMemo<SettingsContextValue>(() => ({
    snapshot,
    microphones,
    agents,
    agentsAvailable,
    error,
    refresh,
    saveGeneral: (patch) => mutate(() => saveSettings(patch)),
    saveAi: (patch) => mutate(() => applyAiSettings(patch)),
    // Candidate values exist only during this invocation; the provider never
    // retains them in state or exposes them in the snapshot.
    saveProviderKey: (provider, candidate) => mutate(() => storeProviderKey(provider, candidate)),
    selectAgent: (agentId) => mutate(() => setAgentMode(agentId)),
  }), [snapshot, microphones, agents, agentsAvailable, error, refresh, mutate]);

  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>;
}

export function useSettings() {
  const context = useContext(SettingsContext);
  if (!context) throw new Error("useSettings must be used inside SettingsProvider");
  return context;
}

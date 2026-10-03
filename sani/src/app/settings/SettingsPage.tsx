import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { ChoiceMenu } from "@/components/ui/choice-menu";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { SettingsGroup, SettingsRow } from "@/components/settings-rows";
import { cn } from "@/lib/utils";
import {
  clearRunActivity,
  computerControlSnapshot,
  onComputerControlChange,
  setTechnicalRetention,
  type ComputerControlSnapshot,
} from "@/lib/tauri";
import ComputerControlStatus from "../ComputerControlStatus";
import DiagnosticsPage from "../DiagnosticsPage";
import OverlayLayoutEditor from "../OverlayLayoutEditor";
import { useSettings } from "./SettingsContext";

export const SETTINGS_CATEGORIES = [
  "General",
  "Models",
  "Voice",
  "Computer control",
  "Appearance",
  "Storage",
  "Diagnostics",
] as const;
export type SettingsCategory = (typeof SETTINGS_CATEGORIES)[number];

const STATUS_LABEL: Record<string, string> = {
  saved: "Saved",
  applying: "Applying…",
  ready: "Ready",
  failed_to_apply: "Couldn't apply",
};

function sentence(value: string): string {
  const text = value.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function voiceLabel(id: string): string {
  const prefix = id.split("-")[0];
  return prefix.charAt(0).toUpperCase() + prefix.slice(1);
}

function useControlSnapshot() {
  const [control, setControl] = useState<ComputerControlSnapshot | null>(null);
  const refresh = () => void computerControlSnapshot().then(setControl).catch(() => setControl(null));
  useEffect(() => {
    let live = true;
    let stop: (() => void) | undefined;
    refresh();
    void onComputerControlChange(refresh).then((unlisten) => {
      if (live) stop = unlisten;
      else unlisten();
    });
    return () => {
      live = false;
      stop?.();
    };
  }, []);
  return { control, refresh };
}

export default function SettingsPage({ initialCategory = "General" }: { initialCategory?: SettingsCategory }) {
  const {
    snapshot,
    microphones,
    agents,
    agentsAvailable,
    voiceModels,
    voiceProgress,
    error,
    saveGeneral,
    saveAi,
    saveProviderKey,
    selectAgent,
    installVoice,
    useVoice,
    removeVoice,
  } = useSettings();
  const [category, setCategory] = useState<SettingsCategory>(initialCategory);
  const [keyCandidate, setKeyCandidate] = useState("");
  const [voiceBusy, setVoiceBusy] = useState("");
  const [removeTarget, setRemoveTarget] = useState("");
  const [clearOpen, setClearOpen] = useState(false);
  const [storageNotice, setStorageNotice] = useState("");
  const { control, refresh: refreshControl } = useControlSnapshot();

  useEffect(() => setCategory(initialCategory), [initialCategory]);

  if (!snapshot) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground" role="status">
        Loading settings…
      </div>
    );
  }

  const runVoice = async (model: string, action: () => Promise<void>) => {
    setVoiceBusy(model);
    try {
      await action();
    } finally {
      setVoiceBusy("");
    }
  };

  const agentChoices = [{ value: "auto", label: "Auto" }, ...agents.map((agent) => ({ value: agent.id, label: agent.name }))];

  return (
    <div className="flex h-full min-h-0">
      <nav aria-label="Settings categories" className="w-48 shrink-0 overflow-y-auto border-r border-border px-2.5 py-6">
        <h1 className="px-2.5 pb-3 text-xl font-semibold tracking-tight">Settings</h1>
        {SETTINGS_CATEGORIES.map((item) => (
          <button
            key={item}
            type="button"
            aria-current={category === item ? "page" : undefined}
            onClick={() => setCategory(item)}
            className={cn(
              "flex h-8 w-full items-center rounded-lg px-2.5 text-left text-sm transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
              category === item
                ? "bg-sidebar-accent text-foreground"
                : "text-muted-foreground hover:bg-sidebar-accent/70 hover:text-foreground",
            )}
          >
            {item}
          </button>
        ))}
      </nav>

      <div className="min-w-0 flex-1 overflow-y-auto">
        <div className={cn("mx-auto w-full px-8 py-6", category === "Appearance" ? "max-w-[960px]" : "max-w-[680px]")}>
          <div className="mb-5 flex items-center justify-between gap-3">
            <h2 className="text-lg font-semibold tracking-tight">{category}</h2>
            <span className="text-xs text-muted-foreground" role="status">
              {STATUS_LABEL[snapshot.runtime_status] ?? snapshot.runtime_status.replaceAll("_", " ")}
            </span>
          </div>
          {error ? (
            <p className="mb-4 rounded-lg border border-destructive/30 bg-red-a2 px-3 py-2 text-sm text-destructive" role="alert">
              {error}
            </p>
          ) : null}

          {category === "General" ? (
            <>
              <SettingsGroup>
                <SettingsRow label="Agent for the next turn" state={agentsAvailable ? undefined : "Runtime unavailable"}>
                  <ChoiceMenu
                    label="Agent for the next turn"
                    value={snapshot.agent_mode}
                    choices={agentChoices}
                    disabled={!agentsAvailable}
                    onChange={(value) => void selectAgent(value)}
                  />
                </SettingsRow>
                <SettingsRow label="Launch Sani at login">
                  <Switch
                    aria-label="Launch Sani at login"
                    checked={snapshot.launch_at_login}
                    onCheckedChange={(checked) => void saveGeneral({ launch_at_login: checked })}
                  />
                </SettingsRow>
                <SettingsRow label="Global shortcut">
                  <Input
                    aria-label="Global shortcut"
                    className="w-44"
                    defaultValue={snapshot.hotkey}
                    onBlur={(event) => {
                      if (event.target.value !== snapshot.hotkey) void saveGeneral({ hotkey: event.target.value });
                    }}
                  />
                </SettingsRow>
              </SettingsGroup>
            </>
          ) : null}

          {category === "Models" ? (
            <SettingsGroup>
              <SettingsRow label="Reasoning provider" state="OpenRouter" />
              <SettingsRow label="Reasoning model">
                <Input
                  aria-label="Reasoning model ID"
                  className="w-72 font-mono text-xs"
                  defaultValue={snapshot.reasoning_model}
                  onBlur={(event) => {
                    if (event.target.value !== snapshot.reasoning_model) void saveAi({ reasoning_model: event.target.value });
                  }}
                />
              </SettingsRow>
              <SettingsRow
                label="OpenRouter key"
                state={snapshot.openrouter_key === "stored" ? "Stored in Keychain" : "Not set"}
                stack
              >
                <form
                  className="flex w-full gap-2"
                  onSubmit={(event) => {
                    event.preventDefault();
                    if (!keyCandidate.trim()) return;
                    void saveProviderKey("openrouter", keyCandidate).then(() => setKeyCandidate(""));
                  }}
                >
                  <Input
                    type="password"
                    aria-label="OpenRouter key"
                    autoComplete="off"
                    value={keyCandidate}
                    placeholder={snapshot.openrouter_key === "stored" ? "Paste a key to replace it" : "Paste your key"}
                    onChange={(event) => setKeyCandidate(event.target.value)}
                  />
                  <Button type="submit" variant="outline" disabled={!keyCandidate.trim()}>
                    Save key
                  </Button>
                </form>
              </SettingsRow>
            </SettingsGroup>
          ) : null}

          {category === "Voice" ? (
            <>
              <SettingsGroup title="Speech model">
                {voiceModels.length === 0 ? (
                  <SettingsRow label="Voice engine unavailable" state="Install or repair the local voice component" />
                ) : (
                  voiceModels.map((model) => {
                    const busy = voiceBusy === model.id;
                    const progress = voiceProgress[model.id];
                    return (
                      <SettingsRow
                        key={model.id}
                        label={voiceLabel(model.id)}
                        state={
                          progress !== undefined && !model.installed ? (
                            <span className="flex items-center gap-2">
                              <span className="h-1 w-24 overflow-hidden rounded-full bg-slate-4">
                                <span className="block h-full bg-foreground" style={{ width: `${Math.round(progress * 100)}%` }} />
                              </span>
                              {Math.round(progress * 100)}%
                            </span>
                          ) : model.active ? (
                            "Active"
                          ) : model.installed ? (
                            "Installed"
                          ) : (
                            "Not installed"
                          )
                        }
                      >
                        {!model.installed ? (
                          <Button size="sm" variant="outline" disabled={busy} onClick={() => void runVoice(model.id, () => installVoice(model.id))}>
                            {busy ? "Installing…" : "Install"}
                          </Button>
                        ) : null}
                        {model.installed && !model.active ? (
                          <>
                            <Button size="sm" variant="outline" disabled={busy} onClick={() => void runVoice(model.id, () => useVoice(model.id))}>
                              {busy ? "Switching…" : "Use"}
                            </Button>
                            <Button size="sm" variant="ghost" disabled={busy} onClick={() => setRemoveTarget(model.id)}>
                              Remove
                            </Button>
                          </>
                        ) : null}
                      </SettingsRow>
                    );
                  })
                )}
              </SettingsGroup>
              <SettingsGroup title="Microphone">
                <SettingsRow label="Input device">
                  <ChoiceMenu
                    label="Input device"
                    value={snapshot.mic_device}
                    choices={[{ value: "", label: "System default" }, ...microphones.map((mic) => ({ value: mic, label: mic }))]}
                    onChange={(value) => void saveGeneral({ mic_device: value })}
                  />
                </SettingsRow>
                <SettingsRow label="Permission" state={sentence(snapshot.microphone_permission)}>
                  <Button size="sm" variant="ghost" onClick={() => void invoke("open_mic_settings")}>
                    Open settings
                  </Button>
                </SettingsRow>
              </SettingsGroup>
            </>
          ) : null}

          {category === "Computer control" ? <ComputerControlStatus snapshot={control} onRefresh={refreshControl} compact /> : null}

          {category === "Appearance" ? <OverlayLayoutEditor /> : null}

          {category === "Storage" ? (
            <SettingsGroup>
              <SettingsRow label="Local Sani data" state={snapshot.storage_path} />
              <SettingsRow label="Keep technical details">
                <ChoiceMenu
                  label="Keep technical details"
                  value={String(snapshot.technical_retention_days)}
                  choices={[
                    { value: "7", label: "7 days" },
                    { value: "14", label: "14 days" },
                    { value: "30", label: "30 days" },
                  ]}
                  onChange={(value) =>
                    void setTechnicalRetention(Number(value) as 7 | 14 | 30).then((count) =>
                      setStorageNotice(`Saved. Removed ${count} expired technical records.`),
                    )
                  }
                />
              </SettingsRow>
              <SettingsRow label="Technical execution details" state={storageNotice || undefined}>
                <Button size="sm" variant="outline" onClick={() => setClearOpen(true)}>
                  Clear
                </Button>
              </SettingsRow>
            </SettingsGroup>
          ) : null}

          {category === "Diagnostics" ? <DiagnosticsPage /> : null}
        </div>
      </div>

      <AlertDialog open={removeTarget !== ""} onOpenChange={(open) => !open && setRemoveTarget("")}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove {voiceLabel(removeTarget || "model")} voice model?</AlertDialogTitle>
            <AlertDialogDescription>It's removed from Sani's local voice cache. You can install it again later.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep model</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                const model = removeTarget;
                setRemoveTarget("");
                if (model) void runVoice(model, () => removeVoice(model));
              }}
            >
              Remove model
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={clearOpen} onOpenChange={setClearOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Clear technical details?</AlertDialogTitle>
            <AlertDialogDescription>Conversations and messages stay. Only execution details are removed.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep details</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setClearOpen(false);
                void clearRunActivity().then((count) =>
                  setStorageNotice(`Cleared ${count} technical records. Conversations were not changed.`),
                );
              }}
            >
              Clear details
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

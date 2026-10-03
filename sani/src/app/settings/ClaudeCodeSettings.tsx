import { useState } from "react";
import { Check, Copy, FolderPlus, Lock, RefreshCw, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { ChoiceMenu } from "@/components/ui/choice-menu";
import { SettingsGroup, SettingsRow } from "@/components/settings-rows";
import { useClaudeCode } from "@/chat/use-claude-code";
import { pickFolder, type ClaudeCodePermission } from "@/lib/tauri";
import { useSettings } from "./SettingsContext";

const LOGIN_COMMAND = "claude auth login";

const PERMISSIONS: Array<{ value: ClaudeCodePermission; label: string }> = [
  { value: "read", label: "Look only" },
  { value: "edit", label: "Edit files" },
  { value: "run", label: "Edit files and run commands" },
];

function CopyCommand({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex w-full items-center gap-2">
      <code className="min-w-0 flex-1 truncate rounded-md border border-border bg-slate-2 px-2.5 py-1.5 font-mono text-xs select-text">
        {command}
      </code>
      <Button
        size="sm"
        variant="outline"
        onClick={() => {
          void navigator.clipboard
            ?.writeText(command)
            .then(() => {
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1500);
            })
            .catch(() => undefined);
        }}
      >
        {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
        {copied ? "Copied" : "Copy"}
      </Button>
    </div>
  );
}

/** Folder names read better as "name · parent". */
function folderParts(path: string): { name: string; parent: string } {
  const parts = path.split("/").filter(Boolean);
  return { name: parts[parts.length - 1] ?? path, parent: "/" + parts.slice(0, -1).join("/") };
}

export default function ClaudeCodeSettings() {
  const { snapshot, saveClaudeCode, error } = useSettings();
  const { status, loading, refresh } = useClaudeCode(15_000);
  const [pickError, setPickError] = useState("");
  if (!snapshot) return null;

  const claude = status?.claude;
  const ready = Boolean(claude?.installed && claude.signed_in);
  const statusText = !status
    ? "Checking…"
    : !claude?.installed
      ? "Not installed"
      : claude.signed_in
        ? `Signed in · version ${claude.version || "unknown"}`
        : "Not signed in";

  const addFolder = async () => {
    setPickError("");
    try {
      const chosen = await pickFolder();
      if (!chosen) return;
      await saveClaudeCode({ dirs: [...snapshot.claude_code_dirs, chosen] });
    } catch (reason) {
      setPickError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  return (
    <>
      <SettingsGroup>
        <SettingsRow label="Your Claude Code" state={statusText}>
          <Button
            size="sm"
            variant="ghost"
            className="text-muted-foreground"
            disabled={loading}
            onClick={() => void refresh()}
          >
            <RefreshCw className="size-3.5" aria-hidden="true" />
            Check
          </Button>
        </SettingsRow>
        {claude && !claude.installed ? (
          <SettingsRow label="Install Claude Code" state="Then sign in once" stack>
            <CopyCommand command="curl -fsSL https://claude.ai/install.sh | bash" />
          </SettingsRow>
        ) : null}
        {claude?.installed && !claude.signed_in ? (
          <SettingsRow label="Sign in once in Terminal" state="It opens your browser" stack>
            <CopyCommand command={LOGIN_COMMAND} />
          </SettingsRow>
        ) : null}
        <SettingsRow label="Let Sani use Claude Code" state={ready ? undefined : "Needs sign-in first"}>
          <Switch
            aria-label="Let Sani use Claude Code"
            checked={snapshot.claude_code_enabled}
            onCheckedChange={(checked) => void saveClaudeCode({ enabled: checked })}
          />
        </SettingsRow>
        <SettingsRow label="What a run may do">
          <ChoiceMenu
            label="What a run may do"
            value={snapshot.claude_code_permission}
            choices={PERMISSIONS}
            onChange={(value) => void saveClaudeCode({ permission: value as ClaudeCodePermission })}
          />
        </SettingsRow>
      </SettingsGroup>

      <SettingsGroup title="Project folders">
        {snapshot.claude_code_dirs.length === 0 ? (
          <SettingsRow label="No folders yet" state="Sani only works inside folders you add" />
        ) : (
          snapshot.claude_code_dirs.map((dir) => {
            const { name, parent } = folderParts(dir);
            return (
              <SettingsRow key={dir} label={name} state={parent}>
                <Button
                  size="icon-sm"
                  variant="ghost"
                  aria-label={`Remove ${name}`}
                  className="text-muted-foreground"
                  onClick={() =>
                    void saveClaudeCode({ dirs: snapshot.claude_code_dirs.filter((item) => item !== dir) })
                  }
                >
                  <X className="size-4" />
                </Button>
              </SettingsRow>
            );
          })
        )}
        <SettingsRow label="Add a project folder">
          <Button size="sm" variant="outline" onClick={() => void addFolder()}>
            <FolderPlus className="size-3.5" aria-hidden="true" />
            Choose folder
          </Button>
        </SettingsRow>
      </SettingsGroup>

      {pickError || error ? (
        <p className="mb-4 text-sm text-destructive" role="alert">
          {pickError || error}
        </p>
      ) : null}
      <p className="flex items-start gap-2 text-xs text-muted-foreground">
        <Lock className="mt-0.5 size-3 shrink-0" aria-hidden="true" />
        <span>
          Sani runs the Claude Code already on this Mac, with your own sign-in. No API key is used or stored.
          Runs cannot leave the folders above, and “Edit files and run commands” lets Claude Code run commands as you
          inside them.
        </span>
      </p>
    </>
  );
}

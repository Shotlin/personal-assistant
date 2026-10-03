import { useState } from "react";
import { Check, Copy, ExternalLink, FolderPlus, Lock, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { ChoiceMenu } from "@/components/ui/choice-menu";
import { SettingsGroup, SettingsRow } from "@/components/settings-rows";
import { Input } from "@/components/ui/input";
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
import { StatusDot } from "@/components/status-dot";
import { claudeHealth } from "@/chat/claude-health";
import { useClaudeCode } from "@/chat/use-claude-code";
import { openSignInLink, pickFolder, type ClaudeCodePermission } from "@/lib/tauri";
import { useSettings } from "./SettingsContext";

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

function ClaudeAccount({ code }: { code: ReturnType<typeof useClaudeCode> }) {
  const { status, loading, error, act } = code;
  const [pasted, setPasted] = useState("");
  const [confirmOut, setConfirmOut] = useState(false);
  const health = claudeHealth(status, loading);
  const claude = status?.claude;
  const login = status?.login;
  const waiting = login?.state === "waiting";
  const signedIn = Boolean(claude?.installed && claude.signed_in);

  return (
    <div className="mb-6 rounded-xl border border-border bg-card px-4 py-3.5">
      <div className="flex items-center gap-3">
        <StatusDot tone={health.tone} />
        <div className="min-w-0 flex-1">
          <div className="font-medium text-foreground">{health.title}</div>
          <div className="truncate text-xs text-muted-foreground">
            {signedIn && health.tone !== "busy"
              ? `Claude Code ${claude?.version ?? ""} · ${claude?.auth_method === "claude.ai" ? "your Claude account" : claude?.auth_method}`
              : health.detail}
          </div>
        </div>
        {claude?.installed && !signedIn && !waiting ? (
          <Button size="sm" onClick={() => void act("login")}>
            Sign in
          </Button>
        ) : null}
        {waiting ? (
          <Button size="sm" variant="ghost" className="text-muted-foreground" onClick={() => void act("cancel")}>
            Cancel
          </Button>
        ) : null}
        {signedIn && !waiting ? (
          <Button size="sm" variant="outline" onClick={() => setConfirmOut(true)}>
            Sign out
          </Button>
        ) : null}
      </div>

      {claude && !claude.installed ? (
        <div className="mt-3 border-t border-border pt-3">
          <div className="mb-2 text-xs text-muted-foreground">Install Claude Code, then come back and sign in.</div>
          <CopyCommand command="curl -fsSL https://claude.ai/install.sh | bash" />
        </div>
      ) : null}

      {waiting ? (
        <div className="mt-3 flex flex-col gap-3 border-t border-border pt-3">
          <p className="text-sm text-foreground">Your browser opened the Claude sign-in page. Finish there and Sani continues by itself.</p>
          <div className="flex flex-wrap items-center gap-2">
            {login?.url ? (
              <Button size="sm" variant="outline" onClick={() => void openSignInLink(login.url).catch(() => undefined)}>
                <ExternalLink className="size-3.5" aria-hidden="true" />
                Open the sign-in page again
              </Button>
            ) : null}
          </div>
          <form
            className="flex flex-col gap-1.5"
            onSubmit={(event) => {
              event.preventDefault();
              if (pasted.trim()) void act("code", pasted.trim()).then(() => setPasted(""));
            }}
          >
            <label htmlFor="claude-sign-in-code" className="text-xs text-muted-foreground">
              Browser showing a code instead? Paste it here.
            </label>
            <div className="flex gap-2">
              <Input
                id="claude-sign-in-code"
                autoComplete="off"
                spellCheck={false}
                value={pasted}
                onChange={(event) => setPasted(event.target.value)}
                placeholder="Paste the code"
                className="font-mono text-xs"
              />
              <Button type="submit" variant="outline" disabled={!pasted.trim()}>
                Continue
              </Button>
            </div>
          </form>
          {login?.message ? <p className="text-xs text-muted-foreground">{login.message}</p> : null}
        </div>
      ) : null}

      {login?.state === "failed" || error ? (
        <p className="mt-3 text-sm text-destructive" role="alert">
          {error || login?.message}
        </p>
      ) : null}

      <AlertDialog open={confirmOut} onOpenChange={setConfirmOut}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Sign out of Claude Code?</AlertDialogTitle>
            <AlertDialogDescription>
              This signs out the Claude Code on this Mac that Sani uses. You can sign in again any time.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Stay signed in</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setConfirmOut(false);
                void act("logout");
              }}
            >
              Sign out
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

export default function ClaudeCodeSettings() {
  const { snapshot, saveClaudeCode, error } = useSettings();
  const code = useClaudeCode(15_000);
  const { status } = code;
  const [pickError, setPickError] = useState("");
  if (!snapshot) return null;

  const claude = status?.claude;
  const ready = Boolean(claude?.installed && claude.signed_in);

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
      <ClaudeAccount code={code} />
      <SettingsGroup>
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

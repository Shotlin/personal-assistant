import { useEffect, useState } from "react";
import { Check, Copy, ExternalLink, Lock } from "lucide-react";
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
import { openSignInLink, type ClaudeCodeStatus } from "@/lib/tauri";
import { ProjectFolders, RunLimitRow } from "./ProjectFolders";
import { useSettings } from "./SettingsContext";

const MODELS: Array<{ value: string; label: string }> = [
  { value: "", label: "Claude Code's default" },
  { value: "haiku", label: "Haiku — fastest, lightest" },
  { value: "sonnet", label: "Sonnet — balanced" },
  { value: "opus", label: "Opus — strongest" },
  { value: "fable", label: "Fable" },
];

const EFFORTS: Array<{ value: string; label: string }> = [
  { value: "", label: "Default" },
  { value: "low", label: "Low — quickest, fewest tokens" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
  { value: "xhigh", label: "Extra high" },
  { value: "max", label: "Max — most thinking" },
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

const LIMITS: Array<{ keys: string[]; label: string }> = [
  { keys: ["five_hour"], label: "5-hour session" },
  { keys: ["seven_day", "seven_day_opus"], label: "Weekly" },
];

function resetLabel(epochSeconds: number | null | undefined, weekly: boolean): string {
  if (!epochSeconds) return "";
  const date = new Date(epochSeconds * 1000);
  if (Number.isNaN(date.getTime())) return "";
  const time = date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return weekly ? `${date.toLocaleDateString([], { weekday: "short" })} ${time}` : time;
}

/** How much of the 5-hour session and weekly limit is left, as Claude Code last reported it. */
let autoChecked = false;

function UsageLimits({
  status,
  onCheck,
}: {
  status: ClaudeCodeStatus | null;
  onCheck: () => Promise<void>;
}) {
  const [checking, setChecking] = useState(false);
  const check = async () => {
    setChecking(true);
    try {
      await onCheck();
    } finally {
      setChecking(false);
    }
  };
  const empty = !status || Object.keys(status.usage.rates).length === 0;
  // With nothing known yet, ask once per app session so the bars show real figures.
  useEffect(() => {
    if (empty && status && !autoChecked) {
      autoChecked = true;
      void check();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [empty, Boolean(status)]);
  const rates = status?.usage.rates ?? {};
  const now = Date.now() / 1000;
  const rows = LIMITS.map(({ keys, label }) => {
    const rate = keys.map((key) => rates[key]).find((entry) => entry && entry.used_percent != null);
    return { label, weekly: keys[0] !== "five_hour", rate };
  });
  const any = rows.some((row) => row.rate);
  return (
    <div className="mt-3 space-y-2.5 border-t border-border pt-3" aria-label="Claude usage limits">
      {rows.map(({ label, weekly, rate }) => {
        const over = rate?.resets_at != null && rate.resets_at < now;
        const used = rate && !over ? Math.min(100, Math.round(rate.used_percent ?? 0)) : null;
        const reset = resetLabel(rate?.resets_at, weekly);
        return (
          <div key={label}>
            <div className="flex items-baseline justify-between text-xs">
              <span className="font-medium text-foreground">{label}</span>
              <span className="text-muted-foreground tabular-nums">
                {used !== null
                  ? `${100 - used}% left · ${used}% used${reset ? ` · resets ${reset}` : ""}`
                  : over
                    ? "Window has reset"
                    : "—"}
              </span>
            </div>
            <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-secondary">
              <div
                className={`h-full rounded-full ${used !== null && used >= 90 ? "bg-amber-9" : "bg-blue-10"}`}
                style={{ width: `${used ?? 0}%` }}
              />
            </div>
          </div>
        );
      })}
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">
          {checking
            ? "Asking Claude Code…"
            : any
              ? "As Claude Code last reported."
              : "Not reported yet."}
        </p>
        <Button size="xs" variant="ghost" disabled={checking} onClick={() => void check()}>
          {checking ? "Checking…" : "Check now"}
        </Button>
      </div>
      <p className="text-[11px] text-muted-foreground/80">
        Checking sends one tiny request on your own Claude plan.
      </p>
    </div>
  );
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
              ? `Claude Code ${claude?.version ?? ""}`
              : health.detail}
          </div>
          {signedIn && health.tone !== "busy" && (claude?.email || claude?.name) ? (
            <div className="mt-1.5 flex min-w-0 items-center gap-2 text-sm text-foreground">
              <span className="min-w-0 truncate">
                {claude.name ? <span className="font-medium">{claude.name}</span> : null}
                {claude.name && claude.email ? <span className="text-muted-foreground"> · </span> : null}
                {claude.email ? <span className="text-muted-foreground">{claude.email}</span> : null}
              </span>
              {claude.plan ? (
                <span className="shrink-0 rounded-full bg-secondary px-2 py-0.5 text-xs font-medium capitalize text-foreground">
                  {claude.plan}
                </span>
              ) : null}
            </div>
          ) : null}
          {signedIn && health.tone !== "busy" ? <UsageLimits status={status} onCheck={() => act("usage")} /> : null}
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
  const { snapshot, saveClaudeCode } = useSettings();
  const code = useClaudeCode(15_000);
  const { status } = code;
  if (!snapshot) return null;

  const claude = status?.claude;
  const ready = Boolean(claude?.installed && claude.signed_in);

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
        <RunLimitRow />
        <SettingsRow
          label="Model for coding"
          state={
            snapshot.claude_code_model
              ? undefined
              : code.status?.usage.last_run.model
                ? `Last run used ${code.status.usage.last_run.model}`
                : "Whatever Claude Code picks"
          }
        >
          <ChoiceMenu
            label="Model for coding"
            value={snapshot.claude_code_model}
            choices={MODELS}
            onChange={(value) => void saveClaudeCode({ model: value })}
          />
        </SettingsRow>
        <SettingsRow label="How hard it thinks" state="Lower uses fewer tokens on your plan">
          <ChoiceMenu
            label="How hard it thinks"
            value={snapshot.claude_code_effort}
            choices={EFFORTS}
            onChange={(value) => void saveClaudeCode({ effort: value })}
          />
        </SettingsRow>
      </SettingsGroup>

      <ProjectFolders />
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
